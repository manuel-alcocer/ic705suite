"""APRS-IS, send only: our own D-PRS beacon after each over, and (if wanted) the position of
the station on air, gated with our login. QDStar's client on asyncio: it connects when
there is something to send, logs in, waits for the server to verify it, sends, and closes
after 15 minutes idle. Our own beacon goes at most every 30 s, the same one every 5 min.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from ic705suite import __version__

log = logging.getLogger(__name__)

DEFAULT_SERVER = "rotate.aprs2.net:14580"
TOCALL = "API705"              # what the IC-705 itself puts in its D-PRS packets
LOGIN_SSID = "10"              # conventional SSID of internet gateways
LOGIN_TIMEOUT = 15.0
IDLE_CLOSE = 15 * 60.0
MIN_INTERVAL = 30.0            # s between two beacons of our own station
REPEAT_INTERVAL = 5 * 60.0     # s before an unchanged beacon goes again


def passcode(callsign: str) -> int:
    """APRS-IS passcode of a call sign (the public algorithm every igate uses)."""
    call = callsign.upper().split("-")[0].strip()
    value = 0x73E2
    for i in range(0, len(call), 2):
        value ^= ord(call[i]) << 8
        if i + 1 < len(call):
            value ^= ord(call[i + 1])
    return value & 0x7FFF


def _latitude(lat: float) -> str:
    deg = int(abs(lat))
    minutes = round((abs(lat) - deg) * 60, 2)
    if minutes >= 60:
        deg, minutes = deg + 1, 0.0
    return f"{deg:02d}{minutes:05.2f}{'N' if lat >= 0 else 'S'}"


def _longitude(lon: float) -> str:
    deg = int(abs(lon))
    minutes = round((abs(lon) - deg) * 60, 2)
    if minutes >= 60:
        deg, minutes = deg + 1, 0.0
    return f"{deg:03d}{minutes:05.2f}{'E' if lon >= 0 else 'W'}"


def position_body(lat: float, lon: float, symbol: str = "/[", altitude: float | None = None,
                  comment: str = "") -> str:
    """Uncompressed position report without timestamp: '!DDMM.mmN/DDDMM.mmW[...'."""
    table, code = symbol[:2] if len(symbol) >= 2 else "/["
    body = f"!{_latitude(lat)}{table}{_longitude(lon)}{code}"
    if altitude is not None:
        feet = max(-99999, min(999999, round(altitude / 0.3048)))
        body += f"/A={feet:06d}" if feet >= 0 else f"/A=-{-feet:05d}"
    comment = "".join(c for c in comment if 32 <= ord(c) < 127).strip()
    return body + comment


def packet(source: str, body: str, path: str) -> str:
    return f"{source.upper()}>{TOCALL},{path}:{body}"


class AprsIs:
    def __init__(self, say=None) -> None:
        self.callsign = ""
        self.server = DEFAULT_SERVER
        self.say = say or (lambda text: log.info("%s", text))
        self.queue: list[str] = []
        self.last_own = ("", 0.0)
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._last_sent = 0.0

    @property
    def login(self) -> str:
        return f"{self.callsign}-{LOGIN_SSID}"

    def configure(self, callsign: str, server: str) -> None:
        callsign = callsign.upper().split("-")[0].strip()
        server = server.strip() or DEFAULT_SERVER
        if (callsign, server) != (self.callsign, self.server):
            self.close()
        self.callsign, self.server = callsign, server

    def send_own(self, source: str, body: str) -> bool:
        last_body, last_time = self.last_own
        age = time.monotonic() - last_time
        if age < MIN_INTERVAL or (body == last_body and age < REPEAT_INTERVAL):
            return False
        self.last_own = (body, time.monotonic())
        self.send(packet(source, body, "TCPIP*"))
        return True

    def send_heard(self, source: str, body: str) -> None:
        self.send(packet(source, body, f"DSTAR*,qAR,{self.login}"))

    def send(self, line: str) -> None:
        if not self.callsign:
            return
        self.queue.append(line)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._session())

    def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    async def _session(self) -> None:
        host, _, port = self.server.rpartition(":")
        if not host or not port.isdigit():
            host, port = self.server, "14580"
        try:
            self.say(f"APRS-IS: {host}:{port}")
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)),
                                                    LOGIN_TIMEOUT)
            self._writer = writer
            writer.write(f"user {self.login} pass {passcode(self.callsign)} vers ic705suite "
                         f"{__version__}\r\n".encode("ascii", "replace"))
            await writer.drain()
            deadline = time.monotonic() + LOGIN_TIMEOUT
            while True:
                line = await asyncio.wait_for(reader.readline(),
                                              max(0.1, deadline - time.monotonic()))
                if not line:
                    raise ConnectionError("closed by the server")
                text = line.decode("ascii", "replace").strip()
                if text.startswith("# logresp"):
                    if " verified" not in text:
                        self.say(f"APRS-IS: {self.login} not verified: {text}")
                        self.queue.clear()
                        return
                    break
            self._last_sent = time.monotonic()
            while time.monotonic() - self._last_sent < IDLE_CLOSE:
                while self.queue:
                    line = self.queue.pop(0)
                    writer.write((line + "\r\n").encode("ascii", "replace"))
                    await writer.drain()
                    self.say(f"APRS-IS <- {line}")
                    self._last_sent = time.monotonic()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(reader.readline(), 1.0)   # keepalives, ignored
        except (TimeoutError, OSError, ConnectionError) as exc:
            self.say(f"APRS-IS: {exc}")
            self.queue.clear()
        finally:
            if self._writer is not None:
                self._writer.close()
                self._writer = None
