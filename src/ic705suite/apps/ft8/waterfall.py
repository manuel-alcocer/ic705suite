"""The band's audio as a waterfall: one spectrum row every quarter of a second, 0-3200 Hz in
5 Hz columns, each column's level in dB over the row's noise floor (0.25 dB steps: a byte),
so a client draws it with its own colours, gain and zero.
"""

from __future__ import annotations

import base64
import math

import numpy as np

LOW, HIGH, STEP = 0, 3200, 5          # Hz: what a row covers, and each column
INTERVAL = 0.25                       # seconds between rows
FLOOR = 10                            # dB under the noise floor that a 0 stands for
SCALE = 4                             # steps per dB


def columns() -> int:
    return (HIGH - LOW) // STEP


def window_size(rate: int) -> int:
    """Samples per spectrum: a power of two fine enough for the columns."""
    return 2 ** math.ceil(math.log2(rate / STEP))


def row(samples: np.ndarray, rate: int) -> bytes:
    """The columns' levels of the last samples, a byte each."""
    size = window_size(rate)
    if len(samples) < size:
        samples = np.concatenate([np.zeros(size - len(samples), np.float32), samples])
    chunk = samples[-size:] * np.hanning(size)
    power = np.abs(np.fft.rfft(chunk)) ** 2
    freqs = np.fft.rfftfreq(size, 1 / rate)
    edges = np.arange(LOW, HIGH + STEP, STEP)
    index = np.clip(np.searchsorted(freqs, edges), 0, len(power) - 1)
    sums = np.add.reduceat(power[:index[-1]], index[:-1])
    counts = np.maximum(np.diff(index), 1)
    db = 10 * np.log10(sums / counts + 1e-12)
    floor = float(np.median(db))
    return np.clip((db - floor + FLOOR) * SCALE, 0, 255).astype(np.uint8).tobytes()


def encode(levels: bytes) -> str:
    return base64.b64encode(levels).decode("ascii")


def decode(text: str) -> bytes:
    return base64.b64decode(text)


def db_over_floor(level: int) -> float:
    return level / SCALE - FLOOR
