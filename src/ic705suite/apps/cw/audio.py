"""Ring buffer of recent receive audio that coroutines can wait on."""

from __future__ import annotations

import asyncio

import numpy as np


class AudioRing:
    def __init__(self, sample_rate: int, seconds: float = 4.0) -> None:
        self.sample_rate = sample_rate
        self._buf = np.zeros(int(sample_rate * seconds), np.float32)
        self.total = 0  # samples received since start
        self._waiters: list[tuple[int, asyncio.Future[None]]] = []

    def push(self, samples: np.ndarray) -> None:
        n = len(samples)
        if n >= len(self._buf):
            self._buf[:] = samples[-len(self._buf):]
        elif n:
            self._buf = np.roll(self._buf, -n)
            self._buf[-n:] = samples
        self.total += n
        still_waiting = []
        for target, future in self._waiters:
            if future.done():
                continue
            if self.total >= target:
                future.set_result(None)
            else:
                still_waiting.append((target, future))
        self._waiters = still_waiting

    def latest(self, count: int) -> np.ndarray:
        count = min(count, len(self._buf), self.total)
        return self._buf[len(self._buf) - count:].copy()

    async def collect(self, seconds: float) -> np.ndarray:
        """Wait until `seconds` of fresh audio have arrived and return them."""
        count = int(seconds * self.sample_rate)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((self.total + count, future))
        await future
        return self.latest(count)
