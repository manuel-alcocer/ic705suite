"""Synthetic CW audio, used by the simulator and the tests."""

from __future__ import annotations

import numpy as np

from ic705suite import morse


def keying_envelope(text: str, wpm: float, sample_rate: int, rise: float = 0.005,
                    jitter: float = 0.0, rng: np.random.Generator | None = None) -> np.ndarray:
    """0..1 keying envelope for `text` with raised-cosine edges.

    jitter > 0 imitates a human fist: every element and gap is stretched by a random
    log-normal factor with that standard deviation (0.15-0.25 is a typical hand key).
    """
    dit = morse.dit_seconds(wpm) * sample_rate
    segments = morse.elements(text)
    if not segments:
        return np.zeros(0, dtype=np.float32)
    rng = rng or np.random.default_rng()
    scale = np.exp(rng.normal(0, jitter, len(segments))) if jitter else np.ones(len(segments))
    env = np.concatenate([np.full(max(1, int(round(units * dit * k))), 1.0 if down else 0.0)
                          for (down, units), k in zip(segments, scale)])
    n = max(1, int(rise * sample_rate))
    kernel = np.hanning(2 * n + 1)
    env = np.convolve(env, kernel / kernel.sum(), mode="same")
    return env.astype(np.float32)


def cw_signal(text: str, wpm: float, freq: float, sample_rate: int, amplitude: float = 0.5,
              lead: float = 0.3, tail: float = 0.6) -> np.ndarray:
    env = keying_envelope(text, wpm, sample_rate)
    env = np.concatenate([np.zeros(int(lead * sample_rate), np.float32), env,
                          np.zeros(int(tail * sample_rate), np.float32)])
    t = np.arange(len(env)) / sample_rate
    return (amplitude * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def qsb(n: int, sample_rate: int, depth: float, period: float = 8.0,
        rng: np.random.Generator | None = None) -> np.ndarray:
    """Slow fading gain (1 - depth .. 1) with a random phase."""
    rng = rng or np.random.default_rng()
    t = np.arange(n) / sample_rate
    return (1 - depth * (0.5 + 0.5 * np.sin(2 * np.pi * t / period + rng.uniform(0, 6.3))))\
        .astype(np.float32)


def qrn(n: int, sample_rate: int, rate: float, amplitude: float,
        rng: np.random.Generator | None = None) -> np.ndarray:
    """Static crashes: short broadband bursts, `rate` per second on average."""
    rng = rng or np.random.default_rng()
    out = np.zeros(n, np.float32)
    for start in rng.integers(0, max(1, n), rng.poisson(rate * n / sample_rate)):
        length = int(sample_rate * rng.uniform(0.003, 0.03))
        burst = rng.standard_normal(length) * amplitude * np.hanning(length)
        out[start:start + length] += burst[:max(0, n - start)]
    return out


def noise(n: int, rms: float, rng: np.random.Generator | None = None) -> np.ndarray:
    rng = rng or np.random.default_rng()
    return (rng.standard_normal(n) * rms).astype(np.float32)
