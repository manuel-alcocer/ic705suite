"""High-level IC-705 control over CI-V (works on top of any Transport)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from ic705suite.apps.cw import civ, morse
from ic705suite.apps.cw.transport import Transport

log = logging.getLogger(__name__)

MODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY", 0x05: "FM",
         0x07: "CW-R", 0x08: "RTTY-R", 0x17: "DV"}
MODE_CW = 0x03
BREAK_IN_SEMI = 0x01

# 0x14 0x09 maps 0..255 to 300..900 Hz; 0x14 0x0C maps 0..255 to 6..48 WPM.
PITCH_MIN, PITCH_MAX = 300, 900
WPM_MIN, WPM_MAX = 6, 48


class CivError(Exception):
    pass


class TxDisabledError(CivError):
    pass


# Commands that make the radio transmit: 0x17 (send CW) and 0x1C (PTT / tuner).
TX_COMMANDS = (0x17, 0x1C)
STOP_CW = b"\x17\xff"


@dataclass
class RadioState:
    frequency: int | None = None
    mode: str | None = None
    filter: int | None = None
    smeter: int = 0            # raw 0..255 (120 = S9, 241 = S9+60 dB)
    pitch: int = 600
    key_wpm: int = 20
    power: int | None = None   # percent


def smeter_text(raw: int) -> str:
    if raw <= 120:
        return f"S{round(raw / 120 * 9)}"
    return f"S9+{round((raw - 120) / 121 * 60)}"


def filter_width_to_index(hz: int) -> int:
    hz = max(50, min(3600, hz))
    if hz <= 500:
        return round(hz / 50) - 1
    return 10 + round((hz - 600) / 100)


def filter_index_to_width(index: int) -> int:
    return (index + 1) * 50 if index < 10 else 600 + (index - 10) * 100


class Radio:
    def __init__(self, transport: Transport, civ_address: int = civ.IC705_ADDR) -> None:
        self.transport = transport
        self.address = civ_address
        # Receive-only unless explicitly enabled: no command able to key the transmitter is sent.
        self.tx_enabled = False
        self.state = RadioState()
        self.on_state: Callable[[], None] | None = None
        self._parser = civ.CivParser()
        self._lock = asyncio.Lock()
        self._pending: tuple[bytes | None, asyncio.Future[civ.CivFrame]] | None = None
        transport.on_civ = self._on_civ

    # ---- low level ------------------------------------------------------------------

    def _on_civ(self, data: bytes) -> None:
        for frame in self._parser.feed(data):
            if frame.src != self.address or frame.dst not in (civ.CONTROLLER_ADDR, civ.BROADCAST_ADDR):
                continue  # echo of our own command or traffic for another controller
            self._update_state(frame)
            if self._pending is not None:
                prefix, future = self._pending
                if future.done():
                    continue
                if frame.cmd in (civ.OK, civ.NG) or (prefix is not None and frame.body.startswith(prefix)):
                    future.set_result(frame)

    def _update_state(self, frame: civ.CivFrame) -> None:
        body = frame.body
        changed = True
        if body[0] in (0x00, 0x03) and len(body) >= 6:
            self.state.frequency = civ.decode_frequency(body[1:6])
        elif body[0] in (0x01, 0x04) and len(body) >= 2:
            self.state.mode = MODES.get(body[1], f"0x{body[1]:02X}")
            if len(body) >= 3:
                self.state.filter = body[2]
        elif body[:2] == b"\x15\x02" and len(body) >= 4:
            self.state.smeter = civ.decode_level(body[2:4])
        else:
            changed = False
        if changed and self.on_state:
            self.on_state()

    async def transact(self, body: bytes, reply_prefix: bytes | None = None,
                       timeout: float = 1.0, retries: int = 2) -> civ.CivFrame:
        """Send a command and wait for its answer.

        reply_prefix=None waits for OK/NG (set commands); otherwise waits for a frame whose
        body starts with reply_prefix (read commands).
        """
        if not self.tx_enabled and body[:1] and body[0] in TX_COMMANDS and body != STOP_CW:
            raise TxDisabledError("transmit disabled (receive-only mode; start with --tx to enable)")
        async with self._lock:
            last_error: Exception | None = None
            for _ in range(retries + 1):
                future: asyncio.Future[civ.CivFrame] = asyncio.get_running_loop().create_future()
                self._pending = (reply_prefix, future)
                try:
                    self.transport.send_civ(civ.build_frame(self.address, body))
                    frame = await asyncio.wait_for(future, timeout)
                except TimeoutError as exc:
                    last_error = exc
                    continue
                finally:
                    self._pending = None
                if frame.cmd == civ.NG:
                    raise CivError(f"radio rejected command {body.hex(' ')}")
                return frame
            raise CivError(f"no answer to command {body.hex(' ')}") from last_error

    # ---- commands ---------------------------------------------------------------------

    async def read_frequency(self) -> int:
        frame = await self.transact(b"\x03", b"\x03")
        return civ.decode_frequency(frame.body[1:6])

    async def set_frequency(self, hz: int) -> None:
        await self.transact(b"\x05" + civ.encode_frequency(hz))
        self.state.frequency = hz
        if self.on_state:
            self.on_state()

    async def read_mode(self) -> str:
        frame = await self.transact(b"\x04", b"\x04")
        return MODES.get(frame.body[1], f"0x{frame.body[1]:02X}")

    async def set_mode_cw(self, filter_number: int | None = None) -> None:
        body = bytes([0x06, MODE_CW]) + (bytes([filter_number]) if filter_number else b"")
        await self.transact(body)
        self.state.mode = "CW"

    async def read_smeter(self) -> int:
        frame = await self.transact(b"\x15\x02", b"\x15\x02")
        return civ.decode_level(frame.body[2:4])

    async def read_pitch(self) -> int:
        frame = await self.transact(b"\x14\x09", b"\x14\x09")
        raw = civ.decode_level(frame.body[2:4])
        self.state.pitch = round((PITCH_MIN + raw * (PITCH_MAX - PITCH_MIN) / 255) / 5) * 5
        return self.state.pitch

    async def set_pitch(self, hz: int) -> None:
        hz = max(PITCH_MIN, min(PITCH_MAX, hz))
        raw = round((hz - PITCH_MIN) * 255 / (PITCH_MAX - PITCH_MIN))
        await self.transact(b"\x14\x09" + civ.encode_level(raw))
        self.state.pitch = hz

    async def read_key_speed(self) -> int:
        frame = await self.transact(b"\x14\x0c", b"\x14\x0c")
        raw = civ.decode_level(frame.body[2:4])
        self.state.key_wpm = round(WPM_MIN + raw * (WPM_MAX - WPM_MIN) / 255)
        return self.state.key_wpm

    async def set_key_speed(self, wpm: int) -> None:
        wpm = max(WPM_MIN, min(WPM_MAX, wpm))
        raw = round((wpm - WPM_MIN) * 255 / (WPM_MAX - WPM_MIN))
        await self.transact(b"\x14\x0c" + civ.encode_level(raw))
        self.state.key_wpm = wpm

    async def set_power(self, percent: int) -> None:
        percent = max(0, min(100, percent))
        await self.transact(b"\x14\x0a" + civ.encode_level(round(percent * 255 / 100)))
        self.state.power = percent

    async def read_power(self) -> int:
        frame = await self.transact(b"\x14\x0a", b"\x14\x0a")
        self.state.power = round(civ.decode_level(frame.body[2:4]) * 100 / 255)
        return self.state.power

    async def read_filter_width(self) -> int:
        frame = await self.transact(b"\x1a\x03", b"\x1a\x03")
        return filter_index_to_width(civ.decode_bcd_byte(frame.body[2]))

    async def set_filter_width(self, hz: int) -> None:
        await self.transact(b"\x1a\x03" + bytes([civ.encode_bcd_byte(filter_width_to_index(hz))]))

    async def set_break_in(self, mode: int = BREAK_IN_SEMI) -> None:
        await self.transact(b"\x16\x47" + bytes([mode]))

    async def send_cw_chunk(self, text: str) -> None:
        """Queue up to 30 characters in the radio's keyer (radio must be in CW mode)."""
        await self.transact(b"\x17" + text.encode("ascii"))

    async def stop_cw(self) -> None:
        await self.transact(b"\x17\xff")

    async def send_cw(self, text: str, on_chunk: Callable[[str], None] | None = None) -> None:
        """Send normalized text, pacing 30-char chunks so the keyer buffer never overflows."""
        loop = asyncio.get_running_loop()
        finish = loop.time()
        for chunk in morse.chunk_for_icom(text):
            # Queue the next chunk shortly before the previous one ends.
            await asyncio.sleep(max(0.0, finish - loop.time() - 0.4))
            await self.send_cw_chunk(chunk)
            if on_chunk:
                on_chunk(chunk)
            finish = max(finish, loop.time()) + morse.text_duration(chunk, self.state.key_wpm)
        await asyncio.sleep(max(0.0, finish - loop.time()))
