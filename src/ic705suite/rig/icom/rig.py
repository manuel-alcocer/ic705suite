"""Icom radios over CI-V (works on top of any Transport: network, USB, simulator)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ic705suite import morse
from ic705suite.rig.base import Meter, Rig, RigError, RigModel, ScopeFrame, TxDisabledError
from ic705suite.rig.controls import Control
from ic705suite.rig.icom.controls import SPECIAL_CONTROLS
from ic705suite.rig.icom import civ
from ic705suite.rig.transport import Transport, TransportError

log = logging.getLogger(__name__)

MODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY", 0x05: "FM",
         0x06: "WFM", 0x07: "CW-R", 0x08: "RTTY-R", 0x17: "DV"}
MODE_CODES = {name: code for code, name in MODES.items()}
MODE_CW = 0x03
MODE_RTTY = 0x04
FILTER_1 = 0x01
BREAK_IN_SEMI = 0x01

# 0x14 0x09 maps 0..255 to 300..900 Hz; 0x14 0x0C maps 0..255 to 6..48 WPM.
PITCH_MIN, PITCH_MAX = 300, 900
WPM_MIN, WPM_MAX = 6, 48

STOP_CW = b"\x17\xff"
PTT_OFF = b"\x1c\x00\x00"


def keys_transmitter(body: bytes) -> bool:
    """Whether a command can make the radio transmit, or arms it to transmit on its own
    (IC-705 CI-V reference: send CW, PTT on, tuner TUNE, voice TX memory, VOX, break-in).
    Reads (no data) and the commands that stop a transmission are never blocked."""
    if body[:1] == b"\x17":
        return body != STOP_CW
    data = body[2:3]
    if body[:2] == b"\x1c\x00":
        return data == b"\x01"
    if body[:2] == b"\x1c\x01":
        return data == b"\x02"            # TUNE sends a carrier
    if body[:2] in (b"\x28\x00", b"\x16\x46", b"\x16\x47"):
        return bool(data) and data != b"\x00"
    return False

# Meter calibration points from the IC-705 CI-V reference.
SWR_POINTS = [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0)]   # 0x15 0x12
SWR_SATURATED_RAW = 120
PO_POINTS = [(0, 0.0), (143, 5.0), (213, 10.0)]              # 0x15 0x11, on a 10 W radio

# Never leave the transmitter keyed longer than this, whatever happens to the caller.
MAX_KEY_SECONDS = 2.0

# Name used by the first, CW-only version.
CivError = RigError


def filter_width_to_index(hz: int) -> int:
    hz = max(50, min(3600, hz))
    if hz <= 500:
        return round(hz / 50) - 1
    return 10 + round((hz - 600) / 100)


def filter_index_to_width(index: int) -> int:
    return (index + 1) * 50 if index < 10 else 600 + (index - 10) * 100


def _interpolate(points: list[tuple[float, float]], x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = points[-2], points[-1]
    return y1 + (y1 - y0) * (x - x1) / (x1 - x0)


def swr_from_raw(raw: float) -> float:
    return round(_interpolate(SWR_POINTS, raw), 2)


def watts_from_raw(raw: float, max_power_w: float = 10.0) -> float:
    return round(_interpolate(PO_POINTS, raw) * max_power_w / 10.0, 1)


@dataclass
class IcomSettings:
    frequency: int
    mode: int
    filter: int
    data_mode: int
    data_filter: int
    power: int | None


class IcomRig(Rig):
    def __init__(self, model: RigModel, transport: Transport, civ_address: int | None = None) -> None:
        super().__init__(model, transport)
        self.address = civ_address or model.params.get("civ_address", 0xA4)
        self._fixed_address = civ_address is not None
        self.keyed_at: float | None = None
        self._watchdog: asyncio.Task | None = None
        self._parser = civ.CivParser()
        self._lock = asyncio.Lock()
        self._pending: tuple[bytes | None, asyncio.Future[civ.CivFrame]] | None = None
        self._controls = {c.control.id: c for c in model.params.get("controls", ())}
        self._meters = {m.meter.id: m for m in model.params.get("meters", ())}
        self._vfo: str | None = None          # the radio cannot be asked which VFO is in use
        self._scope_header: tuple[int, int, bool, bool] | None = None
        self._scope_data = bytearray()
        transport.on_civ = self._on_civ

    async def connect(self) -> None:
        await super().connect()
        if not self._fixed_address and self.transport.civ_address is not None:
            self.address = self.transport.civ_address

    async def close(self) -> None:
        if self.keyed_at is not None:
            await self.unkey()
        await super().close()

    # ---- low level ------------------------------------------------------------------

    def _on_civ(self, data: bytes) -> None:
        for frame in self._parser.feed(data):
            if frame.src != self.address or frame.dst not in (civ.CONTROLLER_ADDR, civ.BROADCAST_ADDR):
                continue  # echo of our own command or traffic for another controller
            if frame.body[:2] == b"\x27\x00":
                self._scope_frame(frame.body[2:])
                continue
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
        if changed:
            self._changed()

    async def transact(self, body: bytes, reply_prefix: bytes | None = None,
                       timeout: float = 1.0, retries: int = 2) -> civ.CivFrame:
        """Send a command and wait for its answer.

        reply_prefix=None waits for OK/NG (set commands); otherwise waits for a frame whose
        body starts with reply_prefix (read commands).
        """
        if not self.tx_enabled and keys_transmitter(body):
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
                    raise RigError(f"radio rejected command {body.hex(' ')}")
                return frame
            raise RigError(f"no answer to command {body.hex(' ')}") from last_error

    async def raw(self, data: bytes) -> bytes:
        frame = await self.transact(data, data[:1])
        return frame.body

    async def _read_level(self, prefix: bytes) -> int:
        frame = await self.transact(prefix, prefix)
        return civ.decode_level(frame.body[2:4])

    # ---- band scope -----------------------------------------------------------------------

    async def scope_output(self, on: bool) -> None:
        # Both the scope and its data output must be on for 27 00 frames to stream.
        if on:
            await self.transact(b"\x27\x10\x01")
        await self.transact(b"\x27\x11" + (b"\x01" if on else b"\x00"))

    def _scope_frame(self, data: bytes) -> None:
        """27 00 frames: over the network a whole sweep in one frame (sequence 1 of 1);
        over USB a header frame and then the points in up to ten more."""
        if len(data) < 3:
            return
        seq, total = civ.decode_bcd_byte(data[1]), civ.decode_bcd_byte(data[2])
        if seq == 1:
            if len(data) < 15:
                return
            mode = data[3]
            f1, f2 = civ.decode_frequency(data[4:9]), civ.decode_frequency(data[9:14])
            centred = mode in (0, 2)
            start, end = (f1 - f2, f1 + f2) if centred else (f1, f2)
            out_of_range = len(data) > 14 and data[14] == 1
            self._scope_header = (start, end, centred, out_of_range)
            self._scope_data = bytearray(data[15:])
            if total == 1 or out_of_range:
                self._emit_scope()
            return
        if self._scope_header is None:
            return
        self._scope_data += data[3:]
        if seq == total:
            self._emit_scope()

    def _emit_scope(self) -> None:
        start, end, centred, out_of_range = self._scope_header
        top = self.model.params.get("scope_amplitude", 160)
        points = self.model.params.get("scope_points", 475)
        if out_of_range or not self._scope_data:
            amplitudes = [0.0] * points
        else:
            amplitudes = [min(1.0, b / top) for b in self._scope_data]
        self._scope_header, self._scope_data = None, bytearray()
        if self.on_scope:
            self.on_scope(ScopeFrame(start, end, amplitudes, centred, out_of_range))

    # ---- front panel ----------------------------------------------------------------------

    def controls(self) -> list[Control]:
        return [c.control for c in self._controls.values()] + list(SPECIAL_CONTROLS)

    def meters(self) -> list[Meter]:
        return [m.meter for m in self._meters.values()]

    async def read_meter(self, meter_id: str) -> float:
        meter = self._meters.get(meter_id)
        if meter is None:
            raise RigError(f"{self.model.name}: no meter {meter_id}")
        return meter.value(await self._read_level(meter.command))

    async def read_control(self, control_id: str):
        if control_id == "filter":
            return str((await self.read_mode_raw())[1])
        if control_id == "filter_width":
            return await self.read_filter_width()
        if control_id == "data_mode":
            return bool((await self.read_data_mode())[0])
        if control_id == "vfo":
            return self._vfo
        spec = self._controls.get(control_id)
        if spec is None:
            raise RigError(f"{self.model.name}: no control {control_id}")
        frame = await self.transact(spec.command, spec.command)
        data = frame.body[len(spec.command):]
        if not data:
            raise RigError(f"{self.model.name}: empty answer for {control_id}")
        return spec.decode(data)

    async def set_control(self, control_id: str, value) -> None:
        if control_id == "filter":
            mode, _ = await self.read_mode_raw()
            await self.set_mode_raw(mode, int(value))
            return
        if control_id == "filter_width":
            await self.set_filter_width(int(value))
            return
        if control_id == "data_mode":
            _, filt = await self.read_mode_raw()
            await self.set_data_mode(1 if value else 0, filt)
            return
        if control_id == "vfo":
            await self.transact(b"\x07" + (b"\x01" if value == "B" else b"\x00"))
            self._vfo = "B" if value == "B" else "A"
            return
        spec = self._controls.get(control_id)
        if spec is None:
            raise RigError(f"{self.model.name}: no control {control_id}")
        armed = spec.arms_tx
        if not self.tx_enabled and armed is not None and \
                (value in armed if isinstance(armed, tuple) else value == armed):
            raise TxDisabledError(f"{control_id} would let the radio transmit "
                                  "(receive-only mode; start with --tx to enable)")
        await self.transact(spec.command + spec.encode(value))

    # ---- frequency and mode -------------------------------------------------------------

    async def read_frequency(self) -> int:
        frame = await self.transact(b"\x03", b"\x03")
        return civ.decode_frequency(frame.body[1:6])

    async def set_frequency(self, hz: int) -> None:
        await self.transact(b"\x05" + civ.encode_frequency(hz))
        self.state.frequency = hz
        self._changed()

    async def read_mode(self) -> str:
        code, _ = await self.read_mode_raw()
        return MODES.get(code, f"0x{code:02X}")

    async def read_mode_raw(self) -> tuple[int, int]:
        frame = await self.transact(b"\x04", b"\x04")
        return frame.body[1], frame.body[2] if len(frame.body) > 2 else FILTER_1

    async def set_mode(self, mode: str, filter_number: int | None = None) -> None:
        if mode.upper() not in MODE_CODES:
            raise RigError(f"unknown mode {mode}")
        await self.set_mode_raw(MODE_CODES[mode.upper()], filter_number)
        self.state.mode = mode.upper()

    async def set_mode_raw(self, code: int, filter_number: int | None = None) -> None:
        await self.transact(bytes([0x06, code]) + (bytes([filter_number]) if filter_number else b""))

    async def read_data_mode(self) -> tuple[int, int]:
        frame = await self.transact(b"\x1a\x06", b"\x1a\x06")
        body = frame.body
        return (body[2] if len(body) > 2 else 0), (body[3] if len(body) > 3 else 0)

    async def set_data_mode(self, on: int, filter_number: int = 0) -> None:
        await self.transact(bytes([0x1A, 0x06, on, filter_number if on else 0]))

    async def read_smeter(self) -> int:
        return await self._read_level(b"\x15\x02")

    async def read_filter_width(self) -> int:
        frame = await self.transact(b"\x1a\x03", b"\x1a\x03")
        index = civ.decode_bcd_byte(frame.body[2])
        if self.state.mode == "AM":
            return (index + 1) * 200          # AM: 200 Hz to 10 kHz in 200 Hz steps
        return filter_index_to_width(index)

    async def set_filter_width(self, hz: int) -> None:
        if self.state.mode == "AM":
            index = max(0, min(49, round(hz / 200) - 1))
        else:
            index = filter_width_to_index(hz)
        await self.transact(b"\x1a\x03" + bytes([civ.encode_bcd_byte(index)]))

    async def set_power(self, percent: int) -> None:
        percent = max(0, min(100, percent))
        await self.transact(b"\x14\x0a" + civ.encode_level(round(percent * 255 / 100)))
        self.state.power = percent

    async def read_power(self) -> int:
        self.state.power = round(await self._read_level(b"\x14\x0a") * 100 / 255)
        return self.state.power

    # ---- CW ------------------------------------------------------------------------------

    async def read_pitch(self) -> int:
        raw = await self._read_level(b"\x14\x09")
        self.state.pitch = round((PITCH_MIN + raw * (PITCH_MAX - PITCH_MIN) / 255) / 5) * 5
        return self.state.pitch

    async def set_pitch(self, hz: int) -> None:
        hz = max(PITCH_MIN, min(PITCH_MAX, hz))
        raw = round((hz - PITCH_MIN) * 255 / (PITCH_MAX - PITCH_MIN))
        await self.transact(b"\x14\x09" + civ.encode_level(raw))
        self.state.pitch = hz

    async def read_key_speed(self) -> int:
        raw = await self._read_level(b"\x14\x0c")
        self.state.key_wpm = round(WPM_MIN + raw * (WPM_MAX - WPM_MIN) / 255)
        return self.state.key_wpm

    async def set_key_speed(self, wpm: int) -> None:
        wpm = max(WPM_MIN, min(WPM_MAX, wpm))
        raw = round((wpm - WPM_MIN) * 255 / (WPM_MAX - WPM_MIN))
        await self.transact(b"\x14\x0c" + civ.encode_level(raw))
        self.state.key_wpm = wpm

    async def set_break_in(self, semi: bool = True) -> None:
        await self.transact(b"\x16\x47" + bytes([BREAK_IN_SEMI if semi else 0]))

    async def send_cw_chunk(self, text: str) -> None:
        """Queue up to 30 characters in the radio's keyer (radio must be in CW mode)."""
        await self.transact(b"\x17" + text.encode("ascii"))

    async def stop_cw(self) -> None:
        await self.transact(STOP_CW)

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

    # ---- PTT and meters (antenna tuning) ----------------------------------------------------

    async def ptt(self, on: bool) -> None:
        await self.transact(b"\x1c\x00" + (b"\x01" if on else b"\x00"))
        self.keyed_at = time.monotonic() if on else None
        self.state.transmitting = on
        if on and (self._watchdog is None or self._watchdog.done()):
            self._watchdog = asyncio.create_task(self._watchdog_loop())

    async def unkey(self) -> None:
        """Release PTT, insisting: this must not fail silently."""
        for _ in range(4):
            try:
                await self.ptt(False)
                return
            except (RigError, TransportError) as exc:
                log.error("PTT off failed: %s", exc)
                await asyncio.sleep(0.1)
        log.critical("could not release PTT")

    async def _watchdog_loop(self) -> None:
        while self.keyed_at is not None:
            if time.monotonic() - self.keyed_at > MAX_KEY_SECONDS:
                log.error("watchdog: transmitter keyed too long, releasing PTT")
                await self.unkey()
                return
            await asyncio.sleep(0.2)

    async def read_swr_raw(self) -> int:
        return await self._read_level(b"\x15\x12")

    async def read_po_raw(self) -> int:
        return await self._read_level(b"\x15\x11")

    async def read_swr(self) -> float:
        return swr_from_raw(await self.read_swr_raw())

    async def read_rf_watts(self) -> float:
        return watts_from_raw(await self.read_po_raw(), self.model.max_power_w)

    async def save_settings(self) -> IcomSettings:
        frequency = await self.read_frequency()
        mode, filt = await self.read_mode_raw()
        data_mode, data_filter = 0, 0
        with contextlib.suppress(RigError):
            data_mode, data_filter = await self.read_data_mode()
        power = None
        with contextlib.suppress(RigError):
            power = await self.read_power()
        return IcomSettings(frequency, mode, filt, data_mode, data_filter, power)

    async def restore_settings(self, saved: object) -> None:
        assert isinstance(saved, IcomSettings)
        steps = [self.set_frequency(saved.frequency),
                 self.set_mode_raw(saved.mode, saved.filter),
                 self.set_data_mode(saved.data_mode, saved.data_filter)]
        if saved.power is not None:
            steps.append(self.set_power(saved.power))
        for step in steps:
            with contextlib.suppress(RigError, TransportError):
                await step
        self.state.mode = MODES.get(saved.mode, self.state.mode)
