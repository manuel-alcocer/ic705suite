"""The IC-705's Terminal Mode / Access Point link over USB ("External Gateway USB"), QDStar's
icom_terminal (a port of G4KLX's CIcomController, with its fixes: the 0xFF idle bytes are
drained in bulk, and the port is opened again when the radio comes back) on the core's
loop: the serial port is read as the loop finds it readable, nothing ever waits on it.

Serial: 38400 baud, RTS/CTS. Frames are length-prefixed; byte 1 is the type:
  radio -> us: 0x03 pong, 0x10 header, 0x12 data (0x40 in byte 3 = end),
               0x21 header ack, 0x23 data ack
  us -> radio: 0x20 header, 0x22 data; one frame in flight, sent again every 50 ms

A stream the radio stops confirming is dropped, never retried for ever: the IC-705 keeps
refusing it and stays busy (its PTT dead until Terminal Mode is left and entered again).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ic705suite.apps.dstar.protocol import (
    DATA_SYNC_BYTES,
    END_PATTERN_BYTES,
    FRAMES_PER_SUPERFRAME,
    VOICE_FRAME_LENGTH,
    Header,
)

log = logging.getLogger(__name__)

VALID_LENGTHS = (0x03, 0x04, 0x10, 0x2C)
VALID_TYPES = (0x03, 0x10, 0x12, 0x21, 0x23)

POLL = b"\xff\xff\xff"
PING = b"\x02\x02\xff"
FAST_POLLS = 18         # the first polls, every POLL_FAST
POLL_FAST = 0.1         # s
PING_EVERY = 1.0        # s
RETRY = 0.05            # s: the frame in flight, sent again
GIVE_UP = 2.0           # s: a frame the radio does not confirm in this time is dropped
NAK_GIVE_UP = 0.3       # s: a stream whose frames the radio refuses this long is abandoned
LOST = 5.0              # s without a byte from the radio: it is gone
REOPEN = 2.0            # s: the port, tried again
BY_ID = Path("/dev/serial/by-id")


def find_data_port(by_id: Path = BY_ID) -> str:
    """The IC-705's D-STAR data port: of its two USB serial ports, interface 02 (00 is
    CI-V); "" when it is not plugged in."""
    try:
        names = sorted(p.name for p in by_id.iterdir())
    except OSError:
        return ""
    found = [n for n in names if "IC-705" in n and n.endswith("-if02")]
    return str(by_id / found[0]) if found else ""


def split_frames(buffer: bytearray) -> list[bytes]:
    """The whole frames at the start of the buffer, taken out of it (the idle 0xFF bytes and
    any garbage dropped; a frame not complete yet stays)."""
    frames = []
    while buffer:
        i = 0
        while i < len(buffer) and buffer[i] == 0xFF:
            i += 1
        if i:
            del buffer[:i]
            continue
        length = buffer[0]
        if length not in VALID_LENGTHS:
            del buffer[0]
            continue
        if len(buffer) < 2:
            break
        if buffer[1] not in VALID_TYPES:
            del buffer[0]
            continue
        if len(buffer) < length:
            break
        frames.append(bytes(buffer[:length]))
        del buffer[:length]
    return frames


def _open_serial(path: str) -> int:
    """The port raw at 38400 8N1 with RTS/CTS, non-blocking (POSIX)."""
    import termios
    import tty

    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[2] |= termios.CRTSCTS | termios.CLOCAL | termios.CREAD
        attrs[4] = attrs[5] = termios.B38400
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
    except OSError:
        os.close(fd)
        raise
    return fd


class IcomTerminal:
    """The radio over USB. Calls on_connected(bool), on_header(Header) and
    on_data(frame12, end) from the loop."""

    def __init__(self, port: str = "", *, on_connected: Callable[[bool], None],
                 on_header: Callable[[Header], None],
                 on_data: Callable[[bytes, bool], None]) -> None:
        self.port = port                 # "": found by its name under /dev/serial/by-id
        self.on_connected = on_connected
        self.on_header = on_header
        self.on_data = on_data
        self.path = ""
        self.fd: int | None = None
        self.rx = bytearray()
        self.tx_queue: collections.deque = collections.deque()   # (frame, kind, seq)
        self.in_flight: tuple | None = None
        self.in_flight_since = 0.0
        self.last_nak = ""
        self.tx_counter = 0
        self.pkt_counter = 0
        self.connected = False
        self.last_rx = 0.0
        self.poll_count = 0
        self.last_error = ""
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._running = False

    # ---- life ---------------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self._open()

    def close(self) -> None:
        self._running = False
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()
        self._close_port()
        self._set_connected(False)

    def _later(self, name: str, delay: float, callback: Callable[[], None]) -> None:
        old = self._timers.pop(name, None)
        if old:
            old.cancel()
        self._timers[name] = asyncio.get_running_loop().call_later(delay, callback)

    def _stop(self, name: str) -> None:
        handle = self._timers.pop(name, None)
        if handle:
            handle.cancel()

    def _open(self) -> None:
        if not self._running:
            return
        path = self.port or find_data_port()
        error = ""
        if not path:
            error = "the radio's USB data port was not found"
        elif sys.platform == "win32":
            error = "the USB gateway needs Linux or macOS"
        else:
            try:
                self.fd = _open_serial(path)
            except OSError as exc:
                error = f"cannot open {path}: {exc}"
        if error:
            if error != self.last_error:        # tried every 2 s: each problem logged once
                log.info("dstar USB: %s", error)
                self.last_error = error
            self._later("reopen", REOPEN, self._open)
            return
        self.path, self.last_error = path, ""
        log.info("dstar USB: port %s open", path)
        asyncio.get_running_loop().add_reader(self.fd, self._read)
        self._reset_link()
        self._later("lost", 1.0, self._check_lost)

    def _close_port(self) -> None:
        if self.fd is not None:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                asyncio.get_running_loop().remove_reader(self.fd)
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                os.close(self.fd)
            self.fd = None

    def _port_failed(self, error: OSError) -> None:
        log.info("dstar USB: port error (%s), trying again", error)
        self._close_port()
        self._set_connected(False)
        self._stop("poll")
        self._stop("retry")
        self._stop("lost")
        self._later("reopen", REOPEN, self._open)

    def _write(self, data: bytes) -> None:
        if self.fd is None:
            return
        try:
            os.write(self.fd, data)
        except BlockingIOError:
            pass                            # the frame in flight goes again in 50 ms
        except OSError as exc:
            self._port_failed(exc)

    # ---- the link ---------------------------------------------------------------------

    def _reset_link(self) -> None:
        self.rx.clear()
        self.tx_queue.clear()
        self.in_flight = None
        self._stop("retry")
        self.poll_count = 0
        self.last_rx = time.monotonic()
        self._later("poll", POLL_FAST, self._poll)

    def _set_connected(self, ok: bool) -> None:
        if ok != self.connected:
            self.connected = ok
            log.info("dstar USB: %s", "radio connected (Terminal Mode)" if ok
                     else "no answer from the radio")
            self.on_connected(ok)

    def _poll(self) -> None:
        if self.fd is None:
            return
        fast = self.poll_count < FAST_POLLS
        self._write(POLL if fast else PING)
        self.poll_count += 1
        self._later("poll", POLL_FAST if fast else PING_EVERY, self._poll)

    def _check_lost(self) -> None:
        if self.fd is None:
            return
        if time.monotonic() - self.last_rx > LOST:
            self._set_connected(False)
            self._reset_link()
        self._later("lost", 1.0, self._check_lost)

    # ---- transmitting -----------------------------------------------------------------

    def write_header(self, header: Header) -> None:
        frame = bytes([0x29, 0x20]) + header.to_radio(with_crc=False)[:39] + b"\xff"
        self.tx_counter = self.pkt_counter = 0
        self.tx_queue.append((frame, "header", 0))
        self._pump()

    def write_data(self, frame12: bytes, end: bool = False) -> None:
        if end:
            data = bytes([0x10, 0x22, self.tx_counter, self.pkt_counter | 0x40]) \
                + END_PATTERN_BYTES + b"\xff"
            self.tx_queue.append((data, "data", self.tx_counter))
        else:
            seq = self.tx_counter
            data = bytes([0x10, 0x22, seq, self.pkt_counter]) + bytes(frame12) + b"\xff"
            self.tx_counter = (self.tx_counter + 1) & 0xFF
            self.pkt_counter += 1
            if frame12[VOICE_FRAME_LENGTH:VOICE_FRAME_LENGTH + 3] == DATA_SYNC_BYTES or \
                    self.pkt_counter == FRAMES_PER_SUPERFRAME:
                self.pkt_counter = 0
            self.tx_queue.append((data, "data", seq))
        self._pump()

    def _pump(self) -> None:
        if self.in_flight is None and self.tx_queue and self.fd is not None:
            self.in_flight = self.tx_queue.popleft()
            self.in_flight_since = time.monotonic()
            self._write(self.in_flight[0])
            self._later("retry", RETRY, self._retry)

    def _retry(self) -> None:
        if not self.in_flight or self.fd is None:
            return
        waited = time.monotonic() - self.in_flight_since
        if self.last_nak and self.in_flight[1] == "data" and waited > NAK_GIVE_UP:
            # The radio refuses the stream: all of it dropped (up to the next header)
            dropped = 1
            while self.tx_queue and self.tx_queue[0][1] == "data":
                self.tx_queue.popleft()
                dropped += 1
            log.info("dstar USB: the radio refuses the stream (answer %s), %d frames dropped",
                     self.last_nak, dropped)
            self._acked()
            return
        if waited > GIVE_UP:
            _frame, kind, seq = self.in_flight
            log.info("dstar USB: the radio does not confirm the %s frame %d (answer %s), "
                     "dropped", kind, seq, self.last_nak or "-")
            self._acked()
            return
        self._write(self.in_flight[0])
        self._later("retry", RETRY, self._retry)

    def _acked(self) -> None:
        self.in_flight = None
        self.last_nak = ""
        self._stop("retry")
        self._pump()

    # ---- receiving --------------------------------------------------------------------

    def _read(self) -> None:
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return
        except OSError as exc:
            self._port_failed(exc)
            return
        if not data:                        # readable and empty: the radio was unplugged
            self._port_failed(OSError("the port closed"))
            return
        self.rx.extend(data)
        for frame in split_frames(self.rx):
            self.handle(frame)

    def handle(self, frame: bytes) -> None:
        kind = frame[1]
        self.last_rx = time.monotonic()
        if kind == 0x03:
            self._set_connected(True)
        elif kind == 0x10 and len(frame) >= 2 + 41:
            self._set_connected(True)
            self.on_header(Header.from_radio(frame[2:43]))
        elif kind == 0x12 and len(frame) >= 16:
            self.on_data(frame[4:16], bool(frame[3] & 0x40))
        elif kind == 0x21:
            if frame[2] == 0x00 and self.in_flight and self.in_flight[1] == "header":
                self._acked()
            elif frame[2] != 0x00:
                self.last_nak = frame.hex(" ")
        elif kind == 0x23 and len(frame) >= 4:
            if frame[3] == 0x00 and self.in_flight and self.in_flight[1] == "data" and \
                    self.in_flight[2] == frame[2]:
                self._acked()
            elif frame[3] != 0x00:
                self.last_nak = frame.hex(" ")

