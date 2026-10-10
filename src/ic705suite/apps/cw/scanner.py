"""Automatic search for CW signals across a band's CW segment."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from ic705suite.apps.cw.audio import AudioRing
from ic705suite.rig.bands import Band
from ic705suite.apps.cw.decoder import detect_cw
from ic705suite.rig.base import Rig, RigError

log = logging.getLogger(__name__)

STEP_HZ = 800          # RF step between listening points
SCAN_FILTER_HZ = 1200  # IF filter width while searching
SETTLE_S = 0.35        # wait after retuning (radio + network audio latency)
DWELL_S = 1.2          # audio analysed at each step


class Scanner:
    def __init__(self, radio: Rig, audio: AudioRing,
                 on_progress: Callable[[int, float], None] | None = None) -> None:
        self.radio = radio
        self.audio = audio
        self.on_progress = on_progress
        # +1 when a higher RF frequency gives a higher audio tone (USB-side CW); learned on the fly.
        self.sideband_sign = 1
        self.dwell = DWELL_S

    async def search(self, band: Band, start: int, direction: int = 1) -> int | None:
        """Step through the band's CW segment from `start`; return the centred frequency of
        the first keyed signal found, or None after one full pass."""
        pitch = self.radio.state.pitch
        half = STEP_HZ / 2 + 50
        original_width = await self._try(self.radio.read_filter_width())
        await self._try(self.radio.set_filter_width(SCAN_FILTER_HZ))
        try:
            freq = start
            steps = (band.cw_end - band.cw_start) // STEP_HZ + 1
            for _ in range(steps):
                if freq > band.cw_end:
                    freq = band.cw_start
                elif freq < band.cw_start:
                    freq = band.cw_end
                await self.radio.set_frequency(freq)
                await asyncio.sleep(SETTLE_S)
                samples = await self.audio.collect(self.dwell)
                found, tone, snr, keying = detect_cw(samples, self.audio.sample_rate,
                                                     pitch - half, pitch + half)
                log.debug("scan %d Hz: found=%s tone=%s snr=%.1f keying=%.1f",
                          freq, found, tone, snr, keying)
                if self.on_progress:
                    self.on_progress(freq, snr)
                if found and tone is not None:
                    return await self.centre(freq, tone, pitch)
                freq += direction * STEP_HZ
            return None
        finally:
            if original_width:
                await self._try(self.radio.set_filter_width(original_width))

    async def centre(self, freq: int, tone: float, pitch: int) -> int:
        """Retune so the signal's tone lands on the CW pitch; learn the sideband if wrong."""
        offset = tone - pitch
        target = round((freq + self.sideband_sign * offset) / 10) * 10
        if abs(offset) < 40:
            return freq
        await self.radio.set_frequency(target)
        await asyncio.sleep(SETTLE_S)
        samples = await self.audio.collect(0.8)
        found, new_tone, _, _ = detect_cw(samples, self.audio.sample_rate, pitch - 450, pitch + 450)
        if found and new_tone is not None and abs(new_tone - pitch) > max(60.0, abs(offset) / 2):
            self.sideband_sign = -self.sideband_sign
            target = round((freq + self.sideband_sign * offset) / 10) * 10
            log.info("scanner: switching sideband sign to %+d", self.sideband_sign)
            await self.radio.set_frequency(target)
        return target

    @staticmethod
    async def _try(coro):
        try:
            return await coro
        except RigError as exc:
            log.warning("scanner: %s", exc)
            return None
