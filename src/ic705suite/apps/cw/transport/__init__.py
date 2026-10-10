"""Transports move CI-V bytes and receive audio between this program and the radio."""

from __future__ import annotations

import abc
from collections.abc import Callable

import numpy as np

CivCallback = Callable[[bytes], None]
AudioCallback = Callable[[np.ndarray], None]  # float32 mono samples in [-1, 1]
EventCallback = Callable[[str], None]


class TransportError(Exception):
    pass


class Transport(abc.ABC):
    """Base class. Subclasses call the callbacks from the asyncio event loop thread."""

    name = "transport"

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self.on_civ: CivCallback | None = None
        self.on_audio: AudioCallback | None = None
        # Called when the link drops after connect() succeeded (message explains why).
        self.on_disconnect: EventCallback | None = None
        # Radio CI-V address discovered during connect, if the transport can know it.
        self.civ_address: int | None = None
        self.radio_name: str = ""

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @abc.abstractmethod
    def send_civ(self, frame: bytes) -> None: ...

    def _emit_civ(self, data: bytes) -> None:
        if self.on_civ:
            self.on_civ(data)

    def _emit_audio(self, samples: np.ndarray) -> None:
        if self.on_audio:
            self.on_audio(samples)

    def _emit_disconnect(self, reason: str) -> None:
        if self.on_disconnect:
            self.on_disconnect(reason)
