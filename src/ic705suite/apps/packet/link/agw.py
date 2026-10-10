"""AGWPE client.

AGWPE is a small TCP protocol for driving an AX.25 stack remotely. LinBPQ
offers it (``AGWPORT=8000`` in bpq32.cfg), and so do Direwolf and SoundModem,
so the same client keeps working whatever ends up driving the radio.

Every frame is a 36 byte header followed by ``data_len`` bytes:

    port(1) reserved(3) kind(1) reserved(1) pid(1) reserved(1)
    call_from(10) call_to(10) data_len(4, little endian) user(4)

AGWPE numbers radio ports from 0; the interface numbers them from 1, as the
node does, and the translation happens here.
"""

from __future__ import annotations

import asyncio
import re
import struct
from dataclasses import dataclass

from ..i18n import N_, _
from .base import Link, LinkEvent, decode_text

HEADER = struct.Struct("<B3xcxBx10s10sI4x")

#: Protocol id of plain text (no layer 3).
PID_TEXT = 0xF0

#: Monitored frame kinds: UI, I, supervisory and our own transmissions.
MONITOR_KINDS = frozenset("UISTt")

#: First line of a monitored frame, e.g.
#: `` 1:Fm EA7WM-1 To APRS Via WIDE1-1 <UI pid=F0 Len=20 >[18:42:10]``
MONITOR_LINE = re.compile(
    r"^\s*(?P<port>\d+):Fm (?P<src>\S+) To (?P<dest>\S+)"
    r"(?: Via (?P<via>\S+))?\s*<(?P<ftype>[^ >]+)"
)


@dataclass(slots=True)
class Frame:
    port: int
    kind: str
    call_from: str = ""
    call_to: str = ""
    data: bytes = b""
    pid: int = 0

    def encode(self) -> bytes:
        return HEADER.pack(
            self.port, self.kind.encode("ascii"), self.pid,
            _encode_call(self.call_from), _encode_call(self.call_to), len(self.data),
        ) + self.data


def _encode_call(call: str) -> bytes:
    return call.upper().encode("ascii", "replace")[:9].ljust(10, b"\0")


def _decode_call(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace").strip().upper()


def decode_header(raw: bytes) -> tuple[Frame, int]:
    """Frame (without data yet) and the number of data bytes that follow."""
    port, kind, pid, call_from, call_to, length = HEADER.unpack(raw)
    frame = Frame(port, kind.decode("ascii", "replace"), _decode_call(call_from),
                  _decode_call(call_to), pid=pid)
    return frame, length


@dataclass(slots=True)
class Heard:
    port: int
    src: str
    dest: str
    via: str
    ftype: str


def parse_monitor(text: str) -> Heard | None:
    """Who sent a monitored frame, from its first line."""
    match = MONITOR_LINE.match(text)
    if not match:
        return None
    return Heard(int(match["port"]), match["src"].rstrip("*").upper(),
                 match["dest"].upper(), match["via"] or "", match["ftype"])


class AgwLink(Link):
    kind = "AGWPE"
    supports_monitor = True

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.monitoring = False
        self.radio_ports: list[str] = []

    @property
    def mycall(self) -> str:
        return self.config.mycall.upper()

    def _send(self, frame: Frame) -> None:
        self._write(frame.encode())

    async def _on_open(self) -> None:
        link = self.config.link
        if link.user:
            # LinBPQ asks remote AGWPE clients to log in with a node user.
            self.sink(LinkEvent("info", _("Logging in as {user}").format(user=link.user)))
            data = link.user.encode()[:255].ljust(255, b"\0")
            data += link.password.encode()[:255].ljust(255, b"\0")
            self._send(Frame(0, "P", data=data))
        self._send(Frame(0, "R"))
        self._send(Frame(0, "G"))
        if self.mycall:
            self._send(Frame(0, "X", call_from=self.mycall))
        if link.monitor:
            self.set_monitor(True)

    async def _pump(self, reader: asyncio.StreamReader) -> None:
        while True:
            raw = await reader.readexactly(HEADER.size)
            frame, length = decode_header(raw)
            frame.data = await reader.readexactly(length) if length else b""
            self.rx_bytes += HEADER.size + length
            self._dispatch(frame)

    def _remote(self, frame: Frame) -> str:
        """The other end of a session frame, whichever field the server put it in."""
        if frame.call_from and frame.call_from != self.mycall:
            return frame.call_from
        return frame.call_to

    def _dispatch(self, frame: Frame) -> None:
        kind = frame.kind
        if kind == "D":
            self.sink(LinkEvent("rx", decode_text(frame.data), call=self._remote(frame)))
        elif kind == "C":
            self.sink(LinkEvent("session_up", decode_text(frame.data).strip(),
                                call=self._remote(frame), extra={"port": frame.port + 1}))
        elif kind == "d":
            self.sink(LinkEvent("session_down", decode_text(frame.data).strip(),
                                call=self._remote(frame)))
        elif kind in MONITOR_KINDS:
            self.sink(LinkEvent("monitor", decode_text(frame.data), own=kind in "Tt"))
        elif kind == "X":
            ok = bool(frame.data) and frame.data[0] == 1
            message = (N_("Callsign {call} registered") if ok
                       else N_("The node refused to register {call}"))
            self.sink(LinkEvent("info" if ok else "error", _(message).format(call=self.mycall)))
        elif kind == "G":
            self.radio_ports = _parse_ports(decode_text(frame.data))
            if self.radio_ports:
                ports = " · ".join(self.radio_ports)
                self.sink(LinkEvent("info", _("Radio ports: {ports}").format(ports=ports)))
        elif kind == "R" and len(frame.data) >= 8:
            major, minor = struct.unpack("<II", frame.data[:8])
            version = f"{major}.{minor}"
            self.sink(LinkEvent("info", _("AGWPE server version {version}").format(
                version=version)))

    # ------------------------------------------------------------ actions --
    def send_line(self, text: str, call: str = "", radio_port: int = 1) -> None:
        data = (text + "\r").encode("utf-8", "replace")
        self._send(Frame(radio_port - 1, "D", self.mycall, call, data, PID_TEXT))

    def connect(self, call: str, via: list[str], radio_port: int) -> None:
        port = radio_port - 1
        if via:
            data = bytes([len(via)]) + b"".join(_encode_call(digi) for digi in via)
            self._send(Frame(port, "v", self.mycall, call, data, PID_TEXT))
        else:
            self._send(Frame(port, "C", self.mycall, call, pid=PID_TEXT))

    def disconnect(self, call: str, radio_port: int = 1) -> None:
        self._send(Frame(radio_port - 1, "d", self.mycall, call))

    def unproto(self, dest: str, text: str, radio_port: int, via: list[str]) -> None:
        port = radio_port - 1
        payload = (text + "\r").encode("utf-8", "replace")
        if via:
            data = bytes([len(via)]) + b"".join(_encode_call(digi) for digi in via) + payload
            self._send(Frame(port, "V", self.mycall, dest, data, PID_TEXT))
        else:
            self._send(Frame(port, "M", self.mycall, dest, payload, PID_TEXT))

    def set_monitor(self, enabled: bool) -> None:
        # 'm' toggles, so only send it when the state really changes.
        if enabled != self.monitoring:
            self._send(Frame(0, "m"))
            self.monitoring = enabled


def _parse_ports(text: str) -> list[str]:
    """``"2;Port1 VHF 1200;Port2 UHF 9600;"`` -> descriptions, numbered from 1.

    Direwolf always lists six ports and marks the unused ones as invalid.
    """
    parts = [part.strip(" \0") for part in text.split(";")]
    return [
        f"{index}: {part}" for index, part in enumerate(parts[1:], start=1)
        if part and "INVALID" not in part.upper()
    ]

