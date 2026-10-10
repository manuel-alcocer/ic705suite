"""Icom network protocol client (WiFi/LAN), as spoken by the IC-705 remote server.

The protocol is not publicly documented by Icom. Packet layouts follow the
reverse-engineered implementations in wfview and kappanhang.

Three UDP streams are used, each with its own handshake, pings and idle keep-alives:
  * control (default port 50001): login, token management, stream negotiation
  * CI-V    (port announced by the radio, usually 50002): CI-V frames
  * audio   (port announced by the radio, usually 50003): receive audio (16-bit LPCM)

All multi-byte header fields are little-endian; several payload fields are
big-endian, as noted where they are packed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ic705suite.rig.transport import Transport, TransportError

__all__ = ["LanTransport", "RadioNotAnswering", "encode_credential"]

log = logging.getLogger(__name__)

CONTROL_PORT = 50001

# Header 'type' values of control packets.
PKT_IDLE = 0x00
PKT_RETRANSMIT = 0x01
PKT_ARE_YOU_THERE = 0x03
PKT_I_AM_HERE = 0x04
PKT_DISCONNECT = 0x05
PKT_ARE_YOU_READY = 0x06  # the radio answers "I am ready" with the same type
PKT_PING = 0x07

# Packet sizes identify the packet kind.
CONTROL_SIZE = 0x10
PING_SIZE = 0x15
OPENCLOSE_SIZE = 0x16
CIV_HEADER_SIZE = 0x15
AUDIO_HEADER_SIZE = 0x18
TOKEN_SIZE = 0x40
STATUS_SIZE = 0x50
LOGIN_RESPONSE_SIZE = 0x60
LOGIN_SIZE = 0x80
CONNINFO_SIZE = 0x90
CAP_HEADER_SIZE = 0x42
RADIO_CAP_SIZE = 0x66

TOKEN_REMOVE = 0x01
TOKEN_CONFIRM = 0x02
TOKEN_RENEW = 0x05
STREAM_REQUEST = 0x03

CODEC_LPCM16_MONO = 0x04
TX_BUFFER_MS = 150
CIV_DATA_MARKER = 0xC1

PING_INTERVAL = 0.5
IDLE_INTERVAL = 0.1
TOKEN_RENEW_INTERVAL = 60.0
LINK_TIMEOUT = 6.0
HANDSHAKE_TIMEOUT = 6.0
REPLY_TIMEOUT = 5.0

ERR_BAD_CREDENTIALS = b"\xff\xff\xff\xfe"
ERR_CONNECTION_FAILED = b"\xff\xff\xff\xff"

# Substitution table used by Icom to obfuscate the username and password.
_PASSCODE_TABLE = bytes(32) + bytes([
    0x47, 0x5D, 0x4C, 0x42, 0x66, 0x20, 0x23, 0x46, 0x4E, 0x57, 0x45, 0x3D, 0x67, 0x76, 0x60, 0x41,
    0x62, 0x39, 0x59, 0x2D, 0x68, 0x7E, 0x7C, 0x65, 0x7D, 0x49, 0x29, 0x72, 0x73, 0x78, 0x21, 0x6E,
    0x5A, 0x5E, 0x4A, 0x3E, 0x71, 0x2C, 0x2A, 0x54, 0x3C, 0x3A, 0x63, 0x4F, 0x43, 0x75, 0x27, 0x79,
    0x5B, 0x35, 0x70, 0x48, 0x6B, 0x56, 0x6F, 0x34, 0x32, 0x6C, 0x30, 0x61, 0x6D, 0x7B, 0x2F, 0x4B,
    0x64, 0x38, 0x2B, 0x2E, 0x50, 0x40, 0x3F, 0x55, 0x33, 0x37, 0x25, 0x77, 0x24, 0x26, 0x74, 0x6A,
    0x28, 0x53, 0x4D, 0x69, 0x22, 0x5C, 0x44, 0x31, 0x36, 0x58, 0x3B, 0x7A, 0x51, 0x5F, 0x52,
]) + b"\x00"


class RadioNotAnswering(TransportError):
    """The radio is unreachable or still holds a previous session (it expires in ~1 min)."""


def encode_credential(text: str) -> bytes:
    """Obfuscate a username/password the way the radio expects (16 bytes, zero padded)."""
    out = bytearray(16)
    for i, ch in enumerate(text.encode("ascii", errors="replace")[:16]):
        p = ch + i
        if p > 126:
            p = 32 + p % 127
        out[i] = _PASSCODE_TABLE[p]
    return bytes(out)


def make_header(length: int, ptype: int, seq: int, sent_id: bytes, rcvd_id: bytes) -> bytearray:
    p = bytearray(length)
    struct.pack_into("<IHH", p, 0, length, ptype, seq & 0xFFFF)
    p[8:12] = sent_id
    p[12:16] = rcvd_id
    return p


def make_local_id(remote_ip: str, local_port: int) -> bytes:
    """Session id derived from the local address, as wfview does."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((remote_ip, CONTROL_PORT))
        local_ip = socket.inet_aton(probe.getsockname()[0])
    except OSError:
        local_ip = os.urandom(4)
    finally:
        probe.close()
    value = (local_ip[2] << 24) | (local_ip[3] << 16) | (local_port & 0xFFFF)
    return struct.pack("<I", value)


class _Stream(asyncio.DatagramProtocol):
    """One UDP stream with the common handshake, keep-alive and retransmission logic."""

    def __init__(self, name: str, host_ip: str, remote_port: int,
                 on_lost: Callable[[str], None]) -> None:
        self.name = name
        self.host_ip = host_ip
        self.remote_port = remote_port
        self.local_port = 0
        self._on_lost = on_lost
        self._udp: asyncio.DatagramTransport | None = None
        self.my_id = b"\x00" * 4
        self.remote_id = b"\x00" * 4
        self._send_seq = 1
        self._ping_seq = 0
        self._sent: OrderedDict[int, bytes] = OrderedDict()
        self._recent_rx: deque[int] = deque(maxlen=128)
        self._recent_rx_set: set[int] = set()
        self._last_send = 0.0
        self.last_rx = 0.0
        self._start = time.monotonic()
        self._here = asyncio.Event()
        self._ready = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._closed = False
        self.established = False

    # ---- socket lifecycle -------------------------------------------------

    async def bind(self) -> None:
        loop = asyncio.get_running_loop()
        self._udp, _ = await loop.create_datagram_endpoint(
            lambda: self, local_addr=("0.0.0.0", 0), family=socket.AF_INET)
        self.local_port = self._udp.get_extra_info("sockname")[1]
        self.my_id = make_local_id(self.host_ip, self.local_port)

    async def handshake(self) -> None:
        """Are-you-there / I-am-here, then are-you-ready / I-am-ready."""
        deadline = time.monotonic() + HANDSHAKE_TIMEOUT
        for event, ptype, seq in ((self._here, PKT_ARE_YOU_THERE, 0),
                                  (self._ready, PKT_ARE_YOU_READY, 1)):
            while not event.is_set():
                if time.monotonic() > deadline:
                    raise RadioNotAnswering(f"{self.name}: no answer from radio "
                                            f"({self.host_ip}:{self.remote_port})")
                self._send(make_header(CONTROL_SIZE, ptype, seq, self.my_id, self.remote_id))
                try:
                    await asyncio.wait_for(event.wait(), 0.5)
                except TimeoutError:
                    pass
        self.established = True
        self.last_rx = time.monotonic()
        self._tasks += [asyncio.create_task(self._ping_loop()),
                        asyncio.create_task(self._idle_loop()),
                        asyncio.create_task(self._watchdog_loop())]
        log.info("%s stream established (local port %d -> %s:%d)",
                 self.name, self.local_port, self.host_ip, self.remote_port)

    async def close(self) -> None:
        if self._closed:
            return
        for task in self._tasks:
            task.cancel()
        if self._udp is not None and self._here.is_set():
            # Without this the radio keeps the session open and ignores new connections
            # until it times out. Sent twice: it is not acknowledged and WiFi drops packets.
            disconnect = make_header(CONTROL_SIZE, PKT_DISCONNECT, 0, self.my_id, self.remote_id)
            self._send(disconnect)
            await asyncio.sleep(0.02)
            self._send(disconnect)
        self._closed = True
        if self._udp is not None:
            self._udp.close()

    def connection_lost(self, exc: Exception | None) -> None:
        if not self._closed:
            self._lost(f"socket closed: {exc}")

    def error_received(self, exc: Exception) -> None:
        log.warning("%s socket error: %s", self.name, exc)

    def _lost(self, reason: str) -> None:
        if self._closed:
            return
        log.error("%s stream lost: %s", self.name, reason)
        self._on_lost(f"{self.name}: {reason}")

    # ---- sending -----------------------------------------------------------

    def _send(self, packet: bytes | bytearray) -> None:
        if self._udp is None or self._closed:
            return
        self._udp.sendto(bytes(packet), (self.host_ip, self.remote_port))
        self._last_send = time.monotonic()
        if log.isEnabledFor(logging.DEBUG - 5):
            log.log(logging.DEBUG - 5, "%s >> %s", self.name, bytes(packet).hex())

    def send_tracked(self, packet: bytearray) -> None:
        """Send a packet with the next sequence number and keep it for retransmission."""
        seq = self._send_seq
        struct.pack_into("<H", packet, 6, seq)
        self._send_seq = (seq + 1) & 0xFFFF
        self._sent[seq] = bytes(packet)
        while len(self._sent) > 512:
            self._sent.popitem(last=False)
        self._send(packet)

    def _retransmit(self, seq: int) -> None:
        packet = self._sent.get(seq)
        if packet is None:
            # Unknown sequence: answer with an idle packet carrying that sequence.
            self._send(make_header(CONTROL_SIZE, PKT_IDLE, seq, self.my_id, self.remote_id))
        else:
            self._send(packet)

    # ---- background loops -------------------------------------------------

    async def _ping_loop(self) -> None:
        while True:
            p = make_header(PING_SIZE, PKT_PING, self._ping_seq, self.my_id, self.remote_id)
            p[0x10] = 0x00
            struct.pack_into("<I", p, 0x11, int((time.monotonic() - self._start) * 1000) & 0xFFFFFFFF)
            self._send(p)
            await asyncio.sleep(PING_INTERVAL)

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(IDLE_INTERVAL)
            if time.monotonic() - self._last_send >= IDLE_INTERVAL:
                self.send_tracked(make_header(CONTROL_SIZE, PKT_IDLE, 0, self.my_id, self.remote_id))

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if time.monotonic() - self.last_rx > LINK_TIMEOUT:
                self._lost("radio stopped answering")
                return

    # ---- receiving ---------------------------------------------------------

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if addr[0] != self.host_ip or len(data) < CONTROL_SIZE:
            return
        self.last_rx = time.monotonic()
        if log.isEnabledFor(logging.DEBUG - 5):
            log.log(logging.DEBUG - 5, "%s << %s", self.name, data.hex())
        try:
            self._dispatch(data)
        except Exception:
            log.exception("%s: error handling packet %s", self.name, data.hex())

    def _dispatch(self, data: bytes) -> None:
        _length, ptype, seq = struct.unpack_from("<IHH", data, 0)
        if len(data) == CONTROL_SIZE:
            if ptype == PKT_I_AM_HERE:
                self.remote_id = data[8:12]
                self._here.set()
            elif ptype == PKT_ARE_YOU_READY:
                self._ready.set()
            elif ptype == PKT_RETRANSMIT:
                self._retransmit(seq)
            elif ptype == PKT_DISCONNECT:
                self._lost("radio closed the connection")
            return
        if ptype == PKT_PING and len(data) == PING_SIZE:
            self._handle_ping(data, seq)
            return
        if ptype == PKT_RETRANSMIT:
            for offset in range(0x10, len(data) - 1, 2):
                self._retransmit(struct.unpack_from("<H", data, offset)[0])
            return
        if seq in self._recent_rx_set:
            return  # duplicate (retransmitted) packet
        if len(self._recent_rx) == self._recent_rx.maxlen:
            self._recent_rx_set.discard(self._recent_rx[0])
        self._recent_rx.append(seq)
        self._recent_rx_set.add(seq)
        self.handle_packet(data)

    def _handle_ping(self, data: bytes, seq: int) -> None:
        if data[0x10] == 0x00:
            reply = make_header(PING_SIZE, PKT_PING, seq, self.my_id, self.remote_id)
            reply[0x10] = 0x01
            reply[0x11:0x15] = data[0x11:0x15]
            self._send(reply)
        elif seq == self._ping_seq:
            self._ping_seq = (self._ping_seq + 1) & 0xFFFF

    def handle_packet(self, data: bytes) -> None:
        """Stream-specific payload packets."""


@dataclass
class RadioCapability:
    guid: bytes        # 16 bytes identifying the radio (MAC address based on the IC-705)
    name_raw: bytes    # 32 bytes, radio name as sent
    civ_address: int
    rx_sample_rates: int
    tx_sample_rates: int

    @property
    def name(self) -> str:
        return self.name_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


class ControlStream(_Stream):
    def __init__(self, host_ip: str, port: int, on_lost: Callable[[str], None]) -> None:
        super().__init__("control", host_ip, port, on_lost)
        self._auth_seq = 0
        self._tok_request = os.urandom(2)
        self._token = b"\x00" * 4
        loop = asyncio.get_running_loop()
        self._login_reply: asyncio.Future[bytes] = loop.create_future()
        self._caps_reply: asyncio.Future[list[RadioCapability]] = loop.create_future()
        self._status_reply: asyncio.Future[bytes] | None = None
        self._token_removed = asyncio.Event()
        self.streams_open = False

    def _token_fields(self, p: bytearray, request_type: int) -> None:
        struct.pack_into(">H", p, 0x12, len(p) - 0x10)  # payload size, big-endian
        p[0x14] = 0x01
        p[0x15] = request_type
        struct.pack_into(">H", p, 0x16, self._auth_seq)  # inner sequence, big-endian
        self._auth_seq = (self._auth_seq + 1) & 0xFFFF
        p[0x1A:0x1C] = self._tok_request
        p[0x1C:0x20] = self._token

    async def login(self, username: str, password: str, computer_name: str) -> str:
        p = make_header(LOGIN_SIZE, 0, 0, self.my_id, self.remote_id)
        self._token_fields(p, 0x00)
        p[0x40:0x50] = encode_credential(username)
        p[0x50:0x60] = encode_credential(password)
        p[0x60:0x70] = computer_name.encode("ascii", errors="replace")[:15].ljust(16, b"\x00")
        self.send_tracked(p)
        try:
            reply = await asyncio.wait_for(self._login_reply, REPLY_TIMEOUT)
        except TimeoutError:
            raise TransportError("no login answer from the radio") from None
        if reply[0x30:0x34] == ERR_BAD_CREDENTIALS:
            raise TransportError("login rejected: invalid username or password "
                                 "(see WLAN Set > Remote Settings > Network User1 on the radio)")
        self._token = reply[0x1C:0x20]
        connection = reply[0x40:0x50].split(b"\x00", 1)[0].decode("ascii", errors="replace")
        self.send_token(TOKEN_CONFIRM)
        self._tasks.append(asyncio.create_task(self._token_renew_loop()))
        return connection

    def send_token(self, request_type: int) -> None:
        p = make_header(TOKEN_SIZE, 0, 0, self.my_id, self.remote_id)
        self._token_fields(p, request_type)
        self.send_tracked(p)

    async def _token_renew_loop(self) -> None:
        while True:
            await asyncio.sleep(TOKEN_RENEW_INTERVAL)
            self.send_token(TOKEN_RENEW)

    async def wait_capabilities(self) -> list[RadioCapability]:
        try:
            return await asyncio.wait_for(self._caps_reply, REPLY_TIMEOUT)
        except TimeoutError:
            raise TransportError("the radio did not send its capabilities") from None

    async def request_streams(self, radio: RadioCapability, username: str, sample_rate: int,
                              civ_port: int, audio_port: int) -> tuple[int, int]:
        """Ask the radio to open CI-V and audio streams. Returns the radio-side ports."""
        p = make_header(CONNINFO_SIZE, 0, 0, self.my_id, self.remote_id)
        self._token_fields(p, STREAM_REQUEST)
        p[0x20:0x30] = radio.guid
        p[0x40:0x60] = radio.name_raw
        p[0x60:0x70] = encode_credential(username)
        p[0x70] = 0x01  # rx audio enabled
        p[0x71] = 0x01  # tx audio enabled (unused, CW is keyed by the radio itself)
        p[0x72] = CODEC_LPCM16_MONO
        p[0x73] = CODEC_LPCM16_MONO
        struct.pack_into(">IIIII", p, 0x74, sample_rate, sample_rate, civ_port, audio_port,
                         TX_BUFFER_MS)
        p[0x88] = 0x01
        self._status_reply = asyncio.get_running_loop().create_future()
        self.send_tracked(p)
        try:
            status = await asyncio.wait_for(self._status_reply, REPLY_TIMEOUT)
        except TimeoutError:
            raise TransportError("the radio did not answer the stream request") from None
        if status[0x30:0x34] == ERR_CONNECTION_FAILED:
            raise TransportError("the radio refused the stream (busy with another client? "
                                 "wait a minute or power-cycle the radio)")
        if status[0x40] == 0x01:
            raise TransportError("the radio reported the stream as disconnected")
        civ_remote = struct.unpack_from(">H", status, 0x42)[0]
        audio_remote = struct.unpack_from(">H", status, 0x46)[0]
        self.streams_open = True
        return civ_remote, audio_remote

    async def close(self) -> None:
        if self.established and not self._closed:
            self.send_token(TOKEN_REMOVE)
            try:
                await asyncio.wait_for(self._token_removed.wait(), 1.0)
            except TimeoutError:
                log.warning("the radio did not acknowledge the logout")
        await super().close()

    def handle_packet(self, data: bytes) -> None:
        size = len(data)
        if size == LOGIN_RESPONSE_SIZE:
            if not self._login_reply.done():
                self._login_reply.set_result(data)
        elif size == TOKEN_SIZE:
            request_type, response = data[0x15], data[0x30:0x34]
            if request_type == TOKEN_REMOVE:
                self._token_removed.set()
            if request_type == TOKEN_RENEW and response == ERR_CONNECTION_FAILED:
                log.warning("token renewal rejected by the radio")
        elif size == STATUS_SIZE:
            if self._status_reply is not None and not self._status_reply.done():
                self._status_reply.set_result(data)
            elif self.streams_open and data[0x40] == 0x01:
                self._lost("radio closed the streams")
        elif size == CONNINFO_SIZE:
            log.debug("radio connection info: %s", data.hex())
        elif size >= CAP_HEADER_SIZE + RADIO_CAP_SIZE and \
                (size - CAP_HEADER_SIZE) % RADIO_CAP_SIZE == 0:
            caps = self._parse_capabilities(data)
            if caps and not self._caps_reply.done():
                self._caps_reply.set_result(caps)

    @staticmethod
    def _parse_capabilities(data: bytes) -> list[RadioCapability]:
        count = (len(data) - CAP_HEADER_SIZE) // RADIO_CAP_SIZE
        caps = []
        for i in range(count):
            r = data[CAP_HEADER_SIZE + i * RADIO_CAP_SIZE:CAP_HEADER_SIZE + (i + 1) * RADIO_CAP_SIZE]
            caps.append(RadioCapability(
                guid=bytes(r[0x00:0x10]),
                name_raw=bytes(r[0x10:0x30]),
                civ_address=r[0x52],
                rx_sample_rates=struct.unpack_from(">H", r, 0x53)[0],
                tx_sample_rates=struct.unpack_from(">H", r, 0x55)[0],
            ))
        return caps


class CivStream(_Stream):
    REOPEN_AFTER = 2.0

    def __init__(self, host_ip: str, on_lost: Callable[[str], None],
                 on_data: Callable[[bytes], None]) -> None:
        super().__init__("civ", host_ip, 0, on_lost)
        self._on_data = on_data
        self._data_seq = 0
        self._last_data = 0.0

    def _next_data_seq(self) -> int:
        seq = self._data_seq
        self._data_seq = (seq + 1) & 0xFFFF
        return seq

    def open_civ(self, close: bool = False) -> None:
        p = make_header(OPENCLOSE_SIZE, 0, 0, self.my_id, self.remote_id)
        struct.pack_into("<H", p, 0x10, 0x01C0)
        struct.pack_into(">H", p, 0x13, self._next_data_seq())
        p[0x15] = 0x00 if close else 0x04
        self.send_tracked(p)
        if not close:
            self._last_data = time.monotonic()

    async def start(self) -> None:
        await self.handshake()
        self.open_civ()
        self._tasks.append(asyncio.create_task(self._reopen_loop()))

    async def _reopen_loop(self) -> None:
        # The radio stops forwarding CI-V if it thinks the port is closed; reopen when silent.
        while True:
            await asyncio.sleep(1.0)
            if time.monotonic() - self._last_data > self.REOPEN_AFTER:
                self.open_civ()

    def send_civ(self, frame: bytes) -> None:
        p = make_header(CIV_HEADER_SIZE + len(frame), 0, 0, self.my_id, self.remote_id)
        p[0x10] = CIV_DATA_MARKER
        struct.pack_into("<H", p, 0x11, len(frame))
        struct.pack_into(">H", p, 0x13, self._next_data_seq())
        p[CIV_HEADER_SIZE:] = frame
        self.send_tracked(p)

    async def close(self) -> None:
        if self.established and not self._closed:
            self.open_civ(close=True)
        await super().close()

    def handle_packet(self, data: bytes) -> None:
        if len(data) > CIV_HEADER_SIZE and data[0x10] == CIV_DATA_MARKER:
            length = struct.unpack_from("<H", data, 0x11)[0]
            self._last_data = time.monotonic()
            self._on_data(data[CIV_HEADER_SIZE:CIV_HEADER_SIZE + length])


class AudioStream(_Stream):
    def __init__(self, host_ip: str, on_lost: Callable[[str], None],
                 on_audio: Callable[[np.ndarray], None]) -> None:
        super().__init__("audio", host_ip, 0, on_lost)
        self._on_audio = on_audio

    def handle_packet(self, data: bytes) -> None:
        if len(data) <= AUDIO_HEADER_SIZE:
            return
        length = struct.unpack_from(">H", data, 0x16)[0]
        available = len(data) - AUDIO_HEADER_SIZE
        if not 0 < length <= available:
            length = available
        length -= length % 2
        samples = np.frombuffer(data, dtype="<i2", count=length // 2, offset=AUDIO_HEADER_SIZE)
        self._on_audio(samples.astype(np.float32) / 32768.0)


class LanTransport(Transport):
    """Connects to the radio's built-in network server over WiFi."""

    name = "wifi"

    def __init__(self, host: str, username: str, password: str, sample_rate: int = 8000,
                 control_port: int = CONTROL_PORT, computer_name: str = "ic705suite") -> None:
        super().__init__(sample_rate)
        if sample_rate not in (8000, 16000, 24000, 48000):
            raise ValueError("sample rate must be 8000, 16000, 24000 or 48000")
        self.host = host
        self.username = username
        self.password = password
        self.control_port = control_port
        self.computer_name = computer_name
        self._control: ControlStream | None = None
        self._civ: CivStream | None = None
        self._audio: AudioStream | None = None
        self._lost_reported = False

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(self.host, self.control_port,
                                           family=socket.AF_INET, type=socket.SOCK_DGRAM)
        except socket.gaierror as exc:
            raise TransportError(f"cannot resolve {self.host}: {exc}") from None
        host_ip = infos[0][4][0]
        self._lost_reported = False
        try:
            self._control = ControlStream(host_ip, self.control_port, self._lost)
            await self._control.bind()
            await self._control.handshake()
            connection = await self._control.login(self.username, self.password, self.computer_name)
            caps = await self._control.wait_capabilities()
            radio = caps[0]
            self.civ_address = radio.civ_address
            self.radio_name = radio.name
            log.info("logged in (%s), radio %s, CI-V address 0x%02X",
                     connection, radio.name, radio.civ_address)

            self._civ = CivStream(host_ip, self._lost, self._emit_civ)
            self._audio = AudioStream(host_ip, self._lost, self._emit_audio)
            await self._civ.bind()
            await self._audio.bind()
            civ_port, audio_port = await self._control.request_streams(
                radio, self.username, self.sample_rate, self._civ.local_port, self._audio.local_port)
            self._civ.remote_port = civ_port
            self._audio.remote_port = audio_port
            await self._civ.start()
            await self._audio.handshake()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        for stream in (self._audio, self._civ, self._control):
            if stream is not None:
                await stream.close()
        self._audio = self._civ = self._control = None

    def send_civ(self, frame: bytes) -> None:
        if self._civ is None:
            raise TransportError("not connected")
        self._civ.send_civ(frame)

    def _lost(self, reason: str) -> None:
        if not self._lost_reported:
            self._lost_reported = True
            self._emit_disconnect(reason)
