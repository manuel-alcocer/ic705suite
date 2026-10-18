"""The gateway's outgoing links to a reflector, QDStar's (ports of ircDDBGateway's
CDPlusHandler, CDCSHandler and CDExtraHandler and their packet codecs) on the core's loop:
DPlus (REF, after authenticating the call sign with the trust server), DCS (DCS and XLX)
and DExtra (XRF). Each link is one UDP socket; its state goes linking -> linked (or
refused), and back to linking when the reflector stops answering.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from collections.abc import Callable

from ic705suite.apps.dstar.protocol import (
    DCS_PORT,
    DEXTRA_PORT,
    DPLUS_PORT,
    END_PATTERN_BYTES,
    NULL_AMBE_DATA_BYTES,
    Header,
    ccitt_crc,
    pad,
)

log = logging.getLogger(__name__)

AUTH_HOST = "auth.dstargateway.org"
AUTH_PORT = 20001
LINK_RETRY = 1.0            # s: the first retry of a link request, then doubling up to 60
DPLUS_POLL = 1.0            # s
DPLUS_TIMEOUT = 30.0        # s without a packet: relink
HEADER_REPEATS = 5
DCS_HTML = ("<table border=\"0\" width=\"95%\"><tr><td width=\"4%\"><img border=\"0\" "
            "src=dongle.jpg></td><td width=\"96%\"><font size=\"2\"><b>DONGLE</b> ic705suite"
            "</font></td></tr></table>")


def protocol_for(name: str) -> str:
    """How a reflector is linked: 'REF001' -> DPlus, 'XRF988' -> DExtra, DCS / XLX -> DCS."""
    return {"REF": "DPlus", "XRF": "DExtra"}.get(name[:3].upper(), "DCS")


def split_reflector(reflector: str) -> tuple[str, str]:
    """'REF001 C' -> ('REF001', 'C')."""
    reflector = pad(reflector)
    return reflector[:7].strip(), reflector[7]


# ---- DPlus authentication (blocking: a thread) ------------------------------------------

def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise OSError("connection closed")
        data += chunk
    return data


def authenticate(login: str, host: str = AUTH_HOST) -> dict[str, str]:
    """The call sign registered with the DPlus trust server (REF reflectors only take links
    from call signs that did lately); the REF reflectors' addresses, {'REF001': ip}."""
    packet = bytearray(b" " * 56)
    packet[0:4] = b"\x38\xc0\x01\x00"
    call = login.upper().encode()[:8]
    packet[4:4 + len(call)] = call
    packet[12:20] = b"DV019999"
    packet[28:32] = b"W7IB"
    packet[32] = ord("2")
    packet[40:47] = b"DHS0257"
    hosts = {}
    with socket.create_connection((host, AUTH_PORT), timeout=15) as sock:
        sock.sendall(bytes(packet))
        while True:
            try:
                head = _recv_exact(sock, 2)
            except OSError:
                break
            length = (head[1] & 0x0F) * 256 + head[0]
            body = head + _recv_exact(sock, length - 2)
            if (body[1] & 0xC0) != 0xC0 or body[2] != 0x01:
                break
            for i in range(8, length - 25, 26):
                address = body[i:i + 16].split(b"\0")[0].decode(errors="replace").strip()
                name = body[i + 16:i + 24].decode(errors="replace").strip()
                if address and name and body[i + 25] & 0x80 and name.startswith("REF"):
                    hosts[name[:6]] = address
    return hosts


# ---- the links --------------------------------------------------------------------------

class _Datagrams(asyncio.DatagramProtocol):
    def __init__(self, link: Link) -> None:
        self.link = link

    def datagram_received(self, data: bytes, addr) -> None:
        if addr[0] == self.link.address:
            self.link.last_rx = time.monotonic()
            self.link.handle(data)

    def error_received(self, exc: Exception) -> None:
        log.debug("dstar %s: %s", self.link.NAME, exc)


class Link:
    """The state machine every protocol shares: a link request sent again until answered,
    keepalives, and relinking when the reflector goes quiet. Calls on_state(state),
    on_header(stream id, Header) and on_data(stream id, seq, frame12, end)."""

    NAME = "?"
    PORT = 0
    POLL = 5.0              # s
    TIMEOUT = 60.0          # s without a packet: relink

    def __init__(self, reflector: str, address: str, login: str, terminal_call: str, *,
                 on_state: Callable[[str], None],
                 on_header: Callable[[int, Header], None],
                 on_data: Callable[[int, int, bytes, bool], None]) -> None:
        self.reflector = pad(reflector)                             # 'DCS214 D'
        self.address = address
        self.login = login.upper().strip()
        self.repeater = pad(pad(self.login)[:7] + pad(terminal_call)[7])   # 'EA7KLX B'
        self.on_state, self.on_header, self.on_data = on_state, on_header, on_data
        self.state = "idle"
        self.last_rx = 0.0
        self.tries = 0
        self.transport: asyncio.DatagramTransport | None = None
        self._timers: dict[str, asyncio.TimerHandle] = {}

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _Datagrams(self), local_addr=("0.0.0.0", 0))

    def link(self) -> None:
        self.tries = 0
        self.last_rx = time.monotonic()
        self._set_state("linking")
        self._send(self.link_packet())
        self._later("try", LINK_RETRY, self._retry)
        self._later("poll", self.POLL, self._poll)

    def unlink(self) -> None:
        self._cancel()
        if self.state in ("linking", "linked"):
            for _ in range(2):
                self._send(self.unlink_packet())
        self._set_state("unlinked")

    def close(self) -> None:
        self.unlink()
        if self.transport:
            self.transport.close()
            self.transport = None

    # ---- internals ----------------------------------------------------------------------

    def _later(self, name: str, delay: float, callback: Callable[[], None]) -> None:
        old = self._timers.pop(name, None)
        if old:
            old.cancel()
        self._timers[name] = asyncio.get_running_loop().call_later(delay, callback)

    def _cancel(self, *names: str) -> None:
        for name in names or list(self._timers):
            handle = self._timers.pop(name, None)
            if handle:
                handle.cancel()

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.on_state(state)

    def _send(self, data: bytes) -> None:
        if self.transport:
            self.transport.sendto(data, (self.address, self.PORT))

    def _retry(self) -> None:
        if self.state != "linking":
            return
        self._send(self.link_packet())
        self.tries += 1
        self._later("try", min(60, 2 ** min(self.tries, 6)), self._retry)

    def _poll(self) -> None:
        if self.state == "linked":
            self._send(self.poll_packet())
        if self.state in ("linking", "linked") and \
                time.monotonic() - self.last_rx > self.TIMEOUT:
            log.info("dstar %s: %s does not answer, relinking", self.NAME,
                     self.reflector.strip())
            self.link()
            return
        if self.state in ("linking", "linked"):
            self._later("poll", self.POLL, self._poll)

    def _answered(self, ok: bool, reply: str) -> None:
        self._cancel("try")
        if ok:
            log.info("dstar %s: linked to %s", self.NAME, self.reflector.strip())
            self._set_state("linked")
        else:
            log.info("dstar %s: %s refuses the link (%s)", self.NAME, self.reflector.strip(),
                     reply)
            self._cancel()
            self._set_state("refused")

    def _closed(self) -> None:
        log.info("dstar %s: %s closed the link", self.NAME, self.reflector.strip())
        self._cancel()
        self._set_state("unlinked")

    # ---- per protocol -------------------------------------------------------------------

    def link_packet(self) -> bytes:
        raise NotImplementedError

    def unlink_packet(self) -> bytes:
        raise NotImplementedError

    def poll_packet(self) -> bytes:
        raise NotImplementedError

    def send_header(self, stream_id: int, header: Header) -> None:
        raise NotImplementedError

    def send_data(self, stream_id: int, seq: int, frame: bytes, end: bool = False) -> None:
        raise NotImplementedError

    def handle(self, data: bytes) -> None:
        raise NotImplementedError


class DPlusLink(Link):
    """A REF module, linked as a dongle."""

    NAME = "DPlus"
    PORT = DPLUS_PORT
    POLL = DPLUS_POLL
    TIMEOUT = DPLUS_TIMEOUT

    def link_packet(self) -> bytes:
        return b"\x05\x00\x18\x00\x01"

    def unlink_packet(self) -> bytes:
        return b"\x05\x00\x18\x00\x00"

    def poll_packet(self) -> bytes:
        return b"\x03\x60\x00"

    def send_header(self, stream_id: int, header: Header) -> None:
        if self.state != "linked":
            return
        rpt1 = pad(self.login)[:7] + self.repeater[7]
        h = header.with_(flag1=0, flag2=0, flag3=0, rpt1=rpt1, rpt2=self.reflector)
        body = bytes(3) + h.callsigns()
        packet = (b"\x3a\x80DSVT\x10\x00\x00\x00\x20\x00\x00\x00"
                  + struct.pack("<H", stream_id) + b"\x80" + body + ccitt_crc(body))
        for _ in range(HEADER_REPEATS):
            self._send(packet)

    def send_data(self, stream_id: int, seq: int, frame: bytes, end: bool = False) -> None:
        if self.state != "linked":
            return
        head = b"DSVT\x20\x00\x00\x00\x20\x00\x00\x00" + struct.pack("<H", stream_id)
        if end:
            self._send(b"\x20\x80" + head + bytes([seq | 0x40]) + NULL_AMBE_DATA_BYTES
                       + END_PATTERN_BYTES[:6])
        else:
            self._send(b"\x1d\x80" + head + bytes([seq]) + bytes(frame))

    def handle(self, data: bytes) -> None:
        if len(data) >= 6 and data[2:6] == b"DSVT":
            if data[0] == 0x3A and data[1] == 0x80 and len(data) >= 58:
                stream_id = struct.unpack_from("<H", data, 14)[0]
                self.on_header(stream_id, Header.from_radio(data[17:58]))
            elif data[0] in (0x1D, 0x20) and data[1] == 0x80 and len(data) >= 29:
                stream_id = struct.unpack_from("<H", data, 14)[0]
                seq = data[16]
                self.on_data(stream_id, seq & 0x1F, data[17:29], bool(seq & 0x40))
            return
        if len(data) == 5 and self.state == "linking" and data[4] == 0x01:
            # LINK1 echoed: who we are (LINK2)
            packet = bytearray(28)
            packet[0:4] = b"\x1c\xc0\x04\x00"
            login = self.login.encode()[:16]
            packet[4:4 + len(login)] = login
            packet[20:28] = b"DV019999"
            self._send(bytes(packet))
        elif len(data) == 5 and data[4] == 0x00:
            self._closed()
        elif len(data) == 8:
            reply = data[4:8].decode(errors="replace")
            if reply != "OKRW":
                self._send(self.unlink_packet())
            self._answered(reply == "OKRW", reply)


class DCSLink(Link):
    """A DCS or XLX module."""

    NAME = "DCS"
    PORT = DCS_PORT
    POLL = 5.0

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rpt_seq = 0
        self.tx_header: Header | None = None
        self.rx_id: int | None = None

    def link_packet(self) -> bytes:
        packet = bytearray(519)
        packet[0:8] = (self.repeater[:7] + " ").encode()
        packet[8] = ord(self.repeater[7])
        packet[9] = ord(self.reflector[7])
        packet[11:19] = (self.reflector[:7] + " ").encode()
        html = DCS_HTML.encode()[:500]
        packet[19:19 + len(html)] = html
        return bytes(packet)

    def unlink_packet(self) -> bytes:
        packet = bytearray(19)
        packet[0:8] = (self.repeater[:7] + " ").encode()
        packet[8] = ord(self.repeater[7])
        packet[9] = 0x20
        packet[11:19] = (self.reflector[:7] + " ").encode()
        return bytes(packet)

    def poll_packet(self) -> bytes:
        return self.repeater.encode() + b"\x00" + self.reflector.encode()

    def send_header(self, stream_id: int, header: Header) -> None:
        # DCS has no header packet: every voice packet carries it
        self.tx_header = header.with_(flag1=0, flag2=0, flag3=0, rpt1=self.repeater,
                                      rpt2=self.reflector, your=pad("CQCQCQ"))
        self.rpt_seq = 0

    def send_data(self, stream_id: int, seq: int, frame: bytes, end: bool = False) -> None:
        if self.state != "linked" or self.tx_header is None:
            return
        packet = bytearray(100)
        packet[0:4] = b"0001"
        packet[7:43] = self.tx_header.callsigns()
        struct.pack_into("<H", packet, 43, stream_id)
        packet[45] = seq | (0x40 if end else 0)
        # The end as ircDDBGateway sends it: the end pattern, then 0x55 x3 as slow data
        packet[46:58] = (END_PATTERN_BYTES[:6] + b"\x00\x00\x00\x55\x55\x55") if end \
            else bytes(frame)
        packet[58:61] = struct.pack("<I", self.rpt_seq)[:3]
        packet[61:64] = b"\x01\x00\x21"
        self.rpt_seq += 1
        self._send(bytes(packet))
        if end:
            self.tx_header = None

    def handle(self, data: bytes) -> None:
        n = len(data)
        if n >= 100 and data[0:4] == b"0001":
            stream_id = struct.unpack_from("<H", data, 43)[0]
            if stream_id != self.rx_id:
                self.rx_id = stream_id
                calls = [data[7 + 8 * i:15 + 8 * i].decode("ascii", "replace") for i in range(4)]
                self.on_header(stream_id, Header(0, 0, 0, *calls,
                                                 my2=data[39:43].decode("ascii", "replace")))
            seq = data[45]
            end = bool(seq & 0x40)
            self.on_data(stream_id, seq & 0x1F, data[46:58], end)
            if end:
                self.rx_id = None
        elif n == 14:
            if self.state == "linking":
                reply = data[10:13]
                self._answered(reply == b"ACK", reply.decode(errors="replace"))
        elif n == 22:
            self._send(self.poll_packet())          # the reflector's poll, answered
        elif n == 19 and self.state == "linked":
            self._closed()


class DExtraLink(Link):
    """An XRF (or XLX) module."""

    NAME = "DExtra"
    PORT = DEXTRA_PORT
    POLL = 10.0

    def link_packet(self, module: str | None = None) -> bytes:
        packet = bytearray(11)
        packet[0:8] = (self.repeater[:7] + " ").encode()
        packet[8] = ord(self.repeater[7])
        packet[9] = ord(module if module is not None else self.reflector[7])
        return bytes(packet)

    def unlink_packet(self) -> bytes:
        return self.link_packet(module=" ")

    def poll_packet(self) -> bytes:
        return (self.repeater[:7] + " ").encode() + b"\x00"

    def send_header(self, stream_id: int, header: Header) -> None:
        if self.state != "linked":
            return
        h = header.with_(rpt1=self.reflector[:7] + "G", rpt2=self.reflector,
                         your=pad("CQCQCQ"))
        body = bytes(3) + h.callsigns()
        packet = (b"DSVT\x10\x00\x00\x00\x20\x00\x00\x00" + struct.pack("<H", stream_id)
                  + b"\x80" + body + ccitt_crc(body))
        for _ in range(HEADER_REPEATS):
            self._send(packet)

    def send_data(self, stream_id: int, seq: int, frame: bytes, end: bool = False) -> None:
        if self.state != "linked":
            return
        payload = (NULL_AMBE_DATA_BYTES + END_PATTERN_BYTES[:3]) if end else bytes(frame)
        self._send(b"DSVT\x20\x00\x00\x00\x20\x00\x00\x00" + struct.pack("<H", stream_id)
                   + bytes([seq | (0x40 if end else 0)]) + payload)

    def handle(self, data: bytes) -> None:
        n = len(data)
        if n >= 56 and data[0:4] == b"DSVT" and data[4] == 0x10:
            stream_id = struct.unpack_from("<H", data, 12)[0]
            self.on_header(stream_id, Header.from_radio(data[15:56]))
        elif n >= 27 and data[0:4] == b"DSVT" and data[4] == 0x20:
            stream_id = struct.unpack_from("<H", data, 12)[0]
            seq = data[14]
            self.on_data(stream_id, seq & 0x1F, data[15:27], bool(seq & 0x40))
        elif n == 14 and self.state == "linking":
            reply = data[10:13]
            self._answered(reply == b"ACK", reply.decode(errors="replace"))
        elif n == 11 and data[9] == 0x20 and self.state == "linked":
            self._closed()


LINKS = {"DPlus": DPlusLink, "DCS": DCSLink, "DExtra": DExtraLink}
