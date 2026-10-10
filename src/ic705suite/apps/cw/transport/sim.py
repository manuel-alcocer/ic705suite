"""Simulated IC-705: answers CI-V like the radio and generates CW receive audio.

Lets you try the program (and run the tests) without a radio.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field

import numpy as np

from ic705suite.apps.cw import civ, morse
from ic705suite.apps.cw.radio import filter_index_to_width
from ic705suite.apps.cw.synth import keying_envelope
from ic705suite.apps.cw.transport import Transport


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

    async def connect(self) -> None:
        self.civ_address = civ.IC705_ADDR
        self.radio_name = "IC-705 (simulator)"
        self._task = asyncio.create_task(self._audio_loop())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

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
        if cmd == 0x14 and data and data[0] in self.levels:
            if len(data) == 1:
                return body + civ.encode_level(self.levels[data[0]])
            self.levels[data[0]] = civ.decode_level(data[1:3])
            return ok
        if body[:2] == b"\x1a\x03":
            if len(data) == 1:
                return body + bytes([civ.encode_bcd_byte(self.filter_index)])
            self.filter_index = civ.decode_bcd_byte(data[1])
            return ok
        if body[:2] == b"\x16\x47":
            return ok
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
