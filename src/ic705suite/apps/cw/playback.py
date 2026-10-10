"""Play a recorded WAV as if it were the radio's receive audio.

Recordings made with ``--record`` get a sidecar ``<name>.json`` holding the dial frequency
over time, so a replayed skimmer shows the right frequencies for every moment.
"""

from __future__ import annotations

import asyncio
import json
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

BLOCK_S = 0.02


def load_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Mono float32 samples in [-1, 1] and the sample rate of a 16-bit WAV or a FLAC file."""
    if str(path).lower().endswith(".flac"):
        import soundfile

        data, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
        return data.mean(axis=1), rate
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV files are supported")
        rate, channels = wav.getframerate(), wav.getnchannels()
        data = np.frombuffer(wav.readframes(wav.getnframes()), "<i2").astype(np.float32) / 32768
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def resample(samples: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target or len(samples) == 0:
        return samples
    n = int(len(samples) * target / rate)
    return np.interp(np.arange(n) * rate / target, np.arange(len(samples)), samples).astype(np.float32)


def sidecar_path(wav: str | Path) -> Path:
    return Path(wav).with_suffix(".json")


def load_sidecar(wav: str | Path) -> list[tuple[float, int]]:
    """[(seconds, dial_hz), ...]: from a FLAC's metadata or the .json next to a WAV."""
    if str(wav).lower().endswith(".flac"):
        try:
            from mutagen.flac import FLAC

            tags = FLAC(str(wav)).tags or {}
            if "cwic705_frequency_timeline" in tags:
                return [(float(t), int(f)) for t, f in
                        json.loads(tags["cwic705_frequency_timeline"][0])]
            if "cwic705_frequency" in tags:
                return [(0.0, int(tags["cwic705_frequency"][0]))]
        except (OSError, ValueError, KeyError):
            pass
        return []
    path = sidecar_path(wav)
    try:
        data = json.loads(path.read_text())
        return [(float(t), int(f)) for t, f in data.get("frequency", [])]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


class SidecarWriter:
    """Collects the dial frequency during a recording and writes the sidecar on close."""

    def __init__(self, wav: str | Path, sample_rate: int) -> None:
        self.path = sidecar_path(wav)
        self.sample_rate = sample_rate
        self.frequency: list[tuple[float, int]] = []

    def note(self, samples_written: int, frequency: int | None) -> None:
        if frequency is not None and (not self.frequency or self.frequency[-1][1] != frequency):
            self.frequency.append((round(samples_written / self.sample_rate, 2), frequency))

    def close(self) -> None:
        self.path.write_text(json.dumps({"sample_rate": self.sample_rate,
                                         "frequency": self.frequency}, indent=1))


class WavPlayback:
    """Feeds a WAV file to `on_audio` in real time (or faster), block by block."""

    def __init__(self, path: str | Path, target_rate: int, on_audio: Callable[[np.ndarray], None],
                 speed: float = 1.0, frequency: int | None = None) -> None:
        self.path = Path(path)
        samples, rate = load_wav(self.path)
        self.samples = resample(samples, rate, target_rate)
        self.rate = target_rate
        self.on_audio = on_audio
        self.speed = speed
        self.position = 0  # samples already played
        self.timeline = load_sidecar(self.path)
        if frequency is not None:
            self.timeline = [(0.0, frequency)]

    @property
    def duration(self) -> float:
        return len(self.samples) / self.rate

    @property
    def elapsed(self) -> float:
        return self.position / self.rate

    @property
    def frequency(self) -> int | None:
        """Dial frequency of the recording at the current position, if known."""
        current = None
        for t, freq in self.timeline:
            if t > self.elapsed:
                break
            current = freq
        return current

    async def run(self) -> None:
        block = int(self.rate * BLOCK_S)
        next_time = time.monotonic()
        while self.position < len(self.samples):
            chunk = self.samples[self.position:self.position + block]
            self.position += len(chunk)
            self.on_audio(chunk)
            next_time += BLOCK_S / self.speed
            await asyncio.sleep(max(0.0, next_time - time.monotonic()))
