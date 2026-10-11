"""AGWPE: the TCP interface of Direwolf (and SoundModem, LinBPQ...) that the core uses to
drive the radio's AX.25: connected sessions, unproto frames and the monitor.

Every frame is a 36 byte header followed by ``data_len`` bytes:

    port(1) reserved(3) kind(1) reserved(1) pid(1) reserved(1)
    call_from(10) call_to(10) data_len(4, little endian) user(4)

AGWPE numbers radio ports from 0; the suite numbers them from 1, as the nodes do, and the
translation happens here. In the frames of a session Direwolf always puts the other station
in ``call_from`` and ours in ``call_to``, whoever called whom.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

HEADER = struct.Struct("<B3xcxBx10s10sI4x")

#: Protocol id of plain text (no layer 3).
PID_TEXT = 0xF0

#: Monitored frame kinds: UI, I, supervisory, unnumbered and our own transmissions.
MONITOR_KINDS = frozenset("UISTt")

#: First line of a monitored frame, e.g.
#: `` 1:Fm EA7WM-1 To APRS Via WIDE1-1 <UI pid=F0 Len=20 >[18:42:10]``
MONITOR_LINE = re.compile(
    r"^\s*(?P<port>\d+):Fm (?P<src>\S+) To (?P<dest>\S+)"
    r"(?: Via (?P<via>\S+))?\s*<(?P<ftype>[^ >]+)"
)

#: A callsign with an optional SSID, as AX.25 takes it.
CALLSIGN = re.compile(r"^[A-Z0-9]{1,6}(-([0-9]|1[0-5]))?$")


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
            encode_call(self.call_from), encode_call(self.call_to), len(self.data),
        ) + self.data


def encode_call(call: str) -> bytes:
    return call.upper().encode("ascii", "replace")[:9].ljust(10, b"\0")


def decode_call(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace").strip().upper()


def decode_header(raw: bytes) -> tuple[Frame, int]:
    """Frame (without data yet) and the number of data bytes that follow."""
    port, kind, pid, call_from, call_to, length = HEADER.unpack(raw)
    frame = Frame(port, kind.decode("ascii", "replace"), decode_call(call_from),
                  decode_call(call_to), pid=pid)
    return frame, length


def decode_text(data: bytes) -> str:
    """Packet text arrives in whatever the other station uses: UTF-8 first, else Latin-1."""
    data = data.rstrip(b"\0")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def digis(via: list[str]) -> bytes:
    """The digipeater list of the 'v' and 'V' frames."""
    return bytes([len(via)]) + b"".join(encode_call(digi) for digi in via)


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


def parse_ports(text: str) -> list[str]:
    """``"2;Port1 VHF 1200;Port2 UHF 9600;"`` -> descriptions, numbered from 1.

    Direwolf always lists six ports and marks the unused ones as invalid.
    """
    parts = [part.strip(" \0") for part in text.split(";")]
    return [f"{index}: {part}" for index, part in enumerate(parts[1:], start=1)
            if part and "INVALID" not in part.upper()]


class AgwClient:
    """One TCP connection to an AGWPE server; the frames it sends arrive at `on_frame`."""

    def __init__(self, host: str, port: int, on_frame: Callable[[Frame], None]) -> None:
        self.host = host
        self.port = port
        self.on_frame = on_frame
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.rx_bytes = 0
        self.tx_bytes = 0

    @property
    def is_open(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def open(self, timeout: float = 10) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout)

    async def pump(self) -> None:
        """Read frames until the connection ends (raises the reason, if any)."""
        assert self._reader is not None
        while True:
            raw = await self._reader.readexactly(HEADER.size)
            frame, length = decode_header(raw)
            frame.data = await self._reader.readexactly(length) if length else b""
            self.rx_bytes += HEADER.size + length
            self.on_frame(frame)

    def send(self, frame: Frame) -> None:
        if not self.is_open:
            raise ConnectionError("the TNC is not connected")
        data = frame.encode()
        self._writer.write(data)
        self.tx_bytes += len(data)

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        if writer is None:
            return
        writer.close()
        with contextlib.suppress(OSError, ConnectionError):
            await writer.wait_closed()
