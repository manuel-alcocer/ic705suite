"""Simulated IC-705: answers CI-V like the radio and generates CW receive audio.

Lets you try the program (and run the tests) without a radio.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field

import numpy as np

from ic705suite import morse
from ic705suite.rig.icom import civ
from ic705suite.rig.icom.rig import filter_index_to_width
from ic705suite.synth import keying_envelope
from ic705suite.rig.transport import Transport


SCOPE_POINTS = 475
# Settings answered generically: command prefix -> current value bytes.
DEFAULT_SETTINGS: dict[bytes, bytes] = {
    **{bytes([0x16, sub]): bytes([value]) for sub, value in (
        (0x02, 1), (0x12, 2), (0x22, 0), (0x40, 0), (0x41, 0), (0x42, 0), (0x43, 0), (0x44, 0),
        (0x45, 0), (0x46, 0),
        (0x47, 0), (0x48, 0), (0x4F, 0), (0x50, 0), (0x56, 0), (0x57, 0), (0x58, 1))},
    b"\x1c\x01": b"\x00",
    b"\x11": b"\x00", b"\x10": b"\x01", b"\x0f": b"\x00",
    b"\x21\x00": b"\x00\x00\x00", b"\x21\x01": b"\x00", b"\x21\x02": b"\x00",
    b"\x27\x10": b"\x00", b"\x27\x11": b"\x00",
    b"\x27\x14\x00": b"\x00", b"\x27\x15\x00": civ.encode_frequency(25_000),
    b"\x27\x16\x00": b"\x01", b"\x27\x17\x00": b"\x00", b"\x27\x19\x00": b"\x00\x00\x00",
    b"\x27\x1a\x00": b"\x01", b"\x27\x1d\x00": b"\x00",
    # Menu items (1A 05): DATA OFF MOD and DATA1 MOD inputs (0 MIC, 1 USB, 2 MIC+USB, 3 WLAN).
    b"\x1a\x05\x01\x18": b"\x00", b"\x1a\x05\x01\x19": b"\x01",
    # The duplex offset (100 Hz units, little-endian BCD) and the repeater tones.
    b"\x0d": b"\x00\x00\x00", b"\x1b\x00": b"\x00\x08\x85", b"\x1b\x01": b"\x00\x08\x85",
}


def _setting_prefix(body: bytes) -> bytes | None:
    for length in (4, 3, 2, 1):
        if body[:length] in DEFAULT_SETTINGS:
            return body[:length]
    return None


@dataclass
class SimStation:
    freq: int
    text: str | None          # None = steady carrier (birdie)
    wpm: float = 20.0
    amplitude: float = 0.2
    pause: float = 2.5
    envelope: np.ndarray = field(default=None, repr=False)  # type: ignore[assignment]

    def build(self, sample_rate: int) -> None:
        if self.text is None:
            self.envelope = np.ones(sample_rate, np.float32)
        else:
            env = keying_envelope(self.text, self.wpm, sample_rate)
            self.envelope = np.concatenate([env, np.zeros(int(self.pause * sample_rate), np.float32)])


DEFAULT_STATIONS = [
    SimStation(7_012_000, "CQ CQ CQ DE EA4SIM EA4SIM K", 18, 0.25),
    SimStation(7_012_450, "EA4SIM DE DL9SIM DL9SIM K", 24, 0.12),
    SimStation(7_013_100, "TEST OK1SIM OK1SIM TEST", 28, 0.08),
    SimStation(7_018_000, None, amplitude=0.15),
    SimStation(7_023_400, "TEST DL5XYZ TEST", 26, 0.12),
    SimStation(7_028_350, "R UR 5NN 5NN BK", 22, 0.08),
    SimStation(10_116_500, "CQ DE G4SIM G4SIM K", 14, 0.15),
    SimStation(14_031_200, "CQ DX DE K1SIM K1SIM K", 24, 0.2),
    SimStation(14_052_700, "QRL? CQ CQ DE F5SIM F5SIM K", 16, 0.1),
]


class SimTransport(Transport):
    name = "sim"
    BLOCK_S = 0.02

    def __init__(self, sample_rate: int = 8000, stations: list[SimStation] | None = None,
                 noise_rms: float = 0.03, sideband_sign: int = 1, frequency: int = 7_030_000,
                 seed: int | None = None) -> None:
        super().__init__(sample_rate)
        self.stations = list(DEFAULT_STATIONS if stations is None else stations)
        for st in self.stations:
            st.build(sample_rate)
        self.noise_rms = noise_rms
        self.sideband_sign = sideband_sign
        self.frequency = frequency
        self.mode = 0x03
        self.filter = 1
        self.filter_index = 9  # 500 Hz
        self.levels = {0x09: 128, 0x0C: 85, 0x0A: 128}  # pitch ~600 Hz, ~20 wpm, 50 % power
        self.smeter = 0
        self.data_mode = (0, 0)
        self.memories: dict[bytes, bytes] = {}   # group + channel (BCD) -> its contents
        self.ptt = False
        self.tx_audio_bytes = 0
        self.tx_peak = 0.0                   # of full scale: the audio sent lately (the ALC)
        self.antenna_resonance = 7_100_000   # simulated antenna: SWR 1.1 here, rising away
        # Every other setting (levels, switches, scope) is kept by its command prefix.
        self.settings: dict[bytes, bytes] = dict(DEFAULT_SETTINGS)
        self.scope_frames = 0
        self._scope_task: asyncio.Task | None = None
        self.sent_text: list[str] = []
        self._tx_until = 0.0
        self._t = 0
        self._rng = np.random.default_rng(seed)
        self._task: asyncio.Task | None = None

    @property
    def pitch(self) -> float:
        return 300 + self.levels[0x09] * 600 / 255

    @property
    def key_wpm(self) -> float:
        return 6 + self.levels[0x0C] * 42 / 255

    @property
    def transmitting(self) -> bool:
        return time.monotonic() < self._tx_until

    #: Transmit audio arrives here (a test or a lab passes it on to another station).
    can_send_audio = True
    on_tx_audio = None

    def send_audio(self, pcm: bytes) -> None:
        self.tx_audio_bytes += len(pcm)
        if pcm:
            self.tx_peak = float(np.abs(np.frombuffer(pcm, "<i2")).max()) / 32767
        if self.on_tx_audio is not None:
            self.on_tx_audio(pcm, self.ptt)

    async def connect(self) -> None:
        self.civ_address = civ.IC705_ADDR
        self.radio_name = "IC-705 (simulator)"
        self._task = asyncio.create_task(self._audio_loop())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._scope_task:
            self._scope_task.cancel()
            self._scope_task = None

    # ---- CI-V -----------------------------------------------------------------------------

    def send_civ(self, frame: bytes) -> None:
        loop = asyncio.get_running_loop()
        loop.call_soon(self._emit_civ, frame)  # the real bus echoes our own frames
        for f in civ.CivParser().feed(frame):
            reply = self._handle(f.body)
            if reply is not None:
                loop.call_later(0.005, self._emit_civ,
                                civ.build_frame(civ.CONTROLLER_ADDR, reply, src=civ.IC705_ADDR))

    def _handle(self, body: bytes) -> bytes | None:
        ok, ng = bytes([civ.OK]), bytes([civ.NG])
        cmd, data = body[0], body[1:]
        if cmd == 0x03:
            return b"\x03" + civ.encode_frequency(self.frequency)
        if cmd == 0x05 and len(data) == 5:
            self.frequency = civ.decode_frequency(data)
            return ok
        if cmd == 0x04:
            return bytes([0x04, self.mode, self.filter])
        if cmd == 0x06 and data:
            self.mode = data[0]
            if len(data) > 1:
                self.filter = data[1]
            return ok
        if body[:2] == b"\x15\x02":
            return b"\x15\x02" + civ.encode_level(self.smeter)
        if cmd == 0x14 and data:
            if len(data) == 1:
                return body + civ.encode_level(self.levels.get(data[0], 128))
            self.levels[data[0]] = civ.decode_level(data[1:3])
            return ok
        if cmd == 0x07:
            return ok
        if body[:2] == b"\x1a\x00" and len(data) >= 5:      # memory channels
            where = bytes(data[1:5])
            if len(data) == 5:
                return b"\x1a\x00" + where + self.memories.get(where, b"\xff")
            if data[5:] == b"\xff":
                self.memories.pop(where, None)
            else:
                self.memories[where] = bytes(data[5:])
            return ok
        prefix = _setting_prefix(body)
        if prefix is not None and prefix in self.settings:
            value = body[len(prefix):]
            if not value:
                return prefix + self.settings[prefix]
            self.settings[prefix] = bytes(value)
            if prefix in (b"\x27\x10", b"\x27\x11"):
                self._scope_switch()
            return ok
        if body[:2] == b"\x15\x13":   # ALC: the audio over 0.6 of full scale drives it
            alc = max(0.0, self.tx_peak - 0.6) / 0.4 * 180 if self.ptt else 0
            return body[:2] + civ.encode_level(min(255, int(alc)))
        if body[:2] == b"\x15\x14":
            return body[:2] + civ.encode_level(0)
        if body[:2] == b"\x15\x15":
            return body[:2] + civ.encode_level(102)        # about 6.8 V: two Li-ion cells
        if body[:2] == b"\x15\x16":
            return body[:2] + civ.encode_level(18 if not self.ptt else 120)
        if body[:2] == b"\x1a\x03":
            if len(data) == 1:
                return body + bytes([civ.encode_bcd_byte(self.filter_index)])
            self.filter_index = civ.decode_bcd_byte(data[1])
            return ok
        if body[:2] == b"\x1a\x06":
            if len(data) == 1:
                return body + bytes(self.data_mode)
            self.data_mode = (data[1], data[2] if len(data) > 2 else 0)
            return ok
        if body[:2] == b"\x1c\x00":
            if len(data) == 1:
                return body + bytes([int(self.ptt)])
            if data[1] == 1 and not self.ptt:
                self.tx_peak = 0.0               # each transmission drives its own ALC
            self.ptt = data[1] == 1
            return ok
        if body[:2] == b"\x15\x12":   # SWR meter: a parabola around the resonance
            if not self.ptt:
                return b"\x15\x12" + civ.encode_level(0)
            off = abs(self.frequency - self.antenna_resonance) / 100_000
            return b"\x15\x12" + civ.encode_level(min(255, int(5 + 40 * off * off)))
        if body[:2] == b"\x15\x11":   # RF power meter
            raw = round(self.levels[0x0A] * 213 / 255) if self.ptt else 0
            return b"\x15\x11" + civ.encode_level(raw)
        if cmd == 0x17:
            if data == b"\xff":
                self._tx_until = 0.0
                return ok
            if self.mode not in (0x03, 0x07) or not data or len(data) > 30:
                return ng
            text = data.decode("ascii")
            self.sent_text.append(text)
            start = max(time.monotonic(), self._tx_until)
            self._tx_until = start + morse.text_duration(text, self.key_wpm)
            return ok
        return ng

    # ---- band scope ---------------------------------------------------------------------

    def _scope_switch(self) -> None:
        on = self.settings[b"\x27\x10"] == b"\x01" and self.settings[b"\x27\x11"] == b"\x01"
        if on and self._scope_task is None:
            self._scope_task = asyncio.get_running_loop().create_task(self._scope_loop())
        elif not on and self._scope_task is not None:
            self._scope_task.cancel()
            self._scope_task = None

    async def _scope_loop(self) -> None:
        while True:
            speed = self.settings[b"\x27\x1a\x00"][0]
            await asyncio.sleep((0.08, 0.15, 0.3)[min(2, speed)])
            if self.settings[b"\x27\x17\x00"] == b"\x01":
                continue                                    # hold
            self._emit_civ(civ.build_frame(civ.CONTROLLER_ADDR, self.scope_frame(),
                                           src=civ.IC705_ADDR))
            self.scope_frames += 1

    def scope_frame(self, points: int = SCOPE_POINTS) -> bytes:
        """One sweep as the IC-705 sends it over the network: 27 00, main 00, sequence
        01 of 01, mode, two frequencies, out-of-range flag and the 475 amplitudes."""
        mode = self.settings[b"\x27\x14\x00"][0]
        if mode in (0, 2):
            half = civ.decode_frequency(self.settings[b"\x27\x15\x00"])
            start, end, f1, f2 = self.frequency - half, self.frequency + half, self.frequency, half
        else:
            from ic705suite.rig.bands import band_for

            band = band_for(self.frequency)
            start, end = (band.start, band.end) if band else (self.frequency - 25_000,
                                                              self.frequency + 25_000)
            f1, f2 = start, end
        ref = civ.decode_level(self.settings[b"\x27\x19\x00"][:2]) / 100
        if self.settings[b"\x27\x19\x00"][2:3] == b"\x01":
            ref = -ref
        hz = start + np.arange(points) * (end - start) / (points - 1)
        floor = 30 + 3 * ref
        levels = floor + 8 * self._rng.random(points)
        bin_hz = (end - start) / points
        t = self._t
        for st in self.stations:
            if not start <= st.freq <= end:
                continue
            keyed = 1.0 if st.envelope is None else float(st.envelope[t % len(st.envelope)])
            if keyed < 0.5:
                continue
            peak = floor + 40 + 160 * st.amplitude
            spread = np.exp(-0.5 * ((hz - st.freq) / max(60.0, bin_hz)) ** 2)
            levels = np.maximum(levels, peak * spread)
        data = bytes(int(v) for v in np.clip(levels, 0, 160))
        return (b"\x27\x00\x00\x01\x01" + bytes([mode]) + civ.encode_frequency(f1)
                + civ.encode_frequency(f2) + b"\x00" + data)

    # ---- audio ------------------------------------------------------------------------------

    async def _audio_loop(self) -> None:
        block = int(self.sample_rate * self.BLOCK_S)
        next_time = time.monotonic()
        while True:
            self._emit_audio(self._generate(block))
            next_time += self.BLOCK_S
            await asyncio.sleep(max(0.0, next_time - time.monotonic()))

    def _generate(self, n: int) -> np.ndarray:
        fs = self.sample_rate
        t = self._t + np.arange(n)
        x = self._rng.standard_normal(n).astype(np.float32) * self.noise_rms
        width = filter_index_to_width(self.filter_index)
        pitch = self.pitch
        strongest = 0.0
        for st in self.stations:
            tone = pitch + self.sideband_sign * (st.freq - self.frequency)
            beyond = abs(tone - pitch) - width / 2
            gain = 1.0 if beyond <= 0 else 10 ** (-min(80.0, beyond / 5) / 20)  # 4 dB / 20 Hz skirt
            if gain < 1e-3 or not 50 < tone < fs / 2 - 50:
                continue
            env = st.envelope[t % len(st.envelope)]
            x += st.amplitude * gain * env * np.sin(2 * np.pi * tone * t / fs).astype(np.float32)
            strongest = max(strongest, st.amplitude * gain * float(env.max()))
        if self.transmitting:
            x *= 0.05  # receiver muted during transmission
        self._t += n
        level = max(strongest, self.noise_rms)
        self.smeter = int(max(0, min(241, 120 + 2.22 * 20 * math.log10(level / 0.3))))
        return x
