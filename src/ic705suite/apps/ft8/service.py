"""FT8 in the core: the radio's audio cut into the 15-second cycles of the UTC clock, each
decoded by jt9 while the next one is heard.

Events (server -> client):
  ft8        {on, jt9, band, dial, cycle, decoding, last, error}  when it changes
  ft8.cycle  {start, seconds, depth, deep, decodes: [{time, snr, dt, freq, message,
             doubtful, kind, sender, to, grid, report, end, cq_to}]}  each cycle decoded: a
             quick pass first; when the machine has time, a deeper one ("deep") adds the
             weaker signals it found
Decoding ([ft8] depth 0, the default): as WSJT-X, the deepest pass that is done in about
3 s first, then a deeper one if it fits before the next cycle, from what each depth took
here (a Raspberry Pi 4: depth 1 in 3 s, 2 in 9 s, 3 in 23 s).
Calls ("ft8.*"): state, start, stop, history {cycles}, band {band}
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import shutil
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ic705suite.apps.ft8 import jt9
from ic705suite.apps.ft8.messages import parse
from ic705suite.i18n import N_
from ic705suite.rig import bands
from ic705suite.rig.base import RigError
from ic705suite.rig.transport import TransportError

log = logging.getLogger(__name__)

CYCLE = jt9.CYCLE
KEEP_CYCLES = 40           # decoded cycles kept for a screen opened later
SHORTEST = 0.8             # of a cycle: less audio than this (a gap, a reconnection) is skipped
QUICK = 3.5                # seconds the first pass may take: answers go in the next cycle
DEEP = 13.5                # seconds the second pass may take: done before the next cycle ends

# FT8 dial frequencies (USB), as WSJT-X has them for IARU Region 1.
DIALS = {"160m": 1_840_000, "80m": 3_573_000, "60m": 5_357_000, "40m": 7_074_000,
         "30m": 10_136_000, "20m": 14_074_000, "17m": 18_100_000, "15m": 21_074_000,
         "12m": 24_915_000, "10m": 28_074_000, "6m": 50_313_000, "2m": 144_174_000,
         "70cm": 432_174_000}


class Ft8Error(Exception):
    def __init__(self, msgid: str, **args: object) -> None:
        super().__init__(msgid.format(**args) if args else msgid)
        self.msgid, self.args_ = msgid, args


def cycle_start(now: float) -> float:
    return math.floor(now / CYCLE) * CYCLE


class Ft8Service:
    def __init__(self, station, config, hub) -> None:
        self.station = station
        self.config = config
        self.hub = hub
        self.on = False
        self.error = ""
        self.history: deque[dict] = deque(maxlen=KEEP_CYCLES)
        self.decoding = False
        self.last: dict = {}
        self.program: str | None = None
        self._chunks: list[np.ndarray] = []
        self._start: float | None = None       # the cycle being heard (None: a partial one)
        self._task: asyncio.Task | None = None
        self._work: Path | None = None
        self._pending: set[asyncio.Task] = set()
        self._changed = True
        self.timings: dict[int, float] = {}       # seconds each depth took here, lately
        self._deep_busy = False

    # ---- life ---------------------------------------------------------------------------------

    async def start(self) -> None:
        if self.on:
            return
        self.program = jt9.find_jt9(self.config.ft8_jt9)
        if self.program is None:
            self.error = N_("WSJT-X's jt9 is not installed here: install WSJT-X (Debian, "
                            "Ubuntu, Raspberry Pi OS: apt install wsjtx) or set [ft8] jt9")
            self._touch()
            raise Ft8Error(self.error)
        self.error = ""
        self._work = Path(tempfile.mkdtemp(prefix="ic705suite-ft8-"))
        self._chunks, self._start = [], None
        self.station.add_audio_listener(self._audio)
        self.on = True
        self._task = asyncio.create_task(self._run())
        self._touch()

    async def stop(self) -> None:
        if not self.on:
            return
        self.on = False
        self.station.remove_audio_listener(self._audio)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for task in list(self._pending):
            task.cancel()
        if self._work is not None:
            shutil.rmtree(self._work, ignore_errors=True)
            self._work = None
        self._touch()

    def changed(self) -> bool:
        changed, self._changed = self._changed, False
        return changed

    def _touch(self) -> None:
        self._changed = True

    # ---- the cycles ---------------------------------------------------------------------------

    def _audio(self, samples: np.ndarray) -> None:
        if self._start is not None:
            self._chunks.append(samples.copy())

    async def _run(self) -> None:
        while True:
            boundary = cycle_start(time.time()) + CYCLE
            await asyncio.sleep(max(0.0, boundary - time.time()))
            heard, start = self._chunks, self._start
            self._chunks, self._start = [], boundary
            if start is not None and heard:
                task = asyncio.create_task(self._decode(start, heard))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
            self._touch()

    def depths(self) -> tuple[int, int | None]:
        """The first pass's depth and the second's (None: no second pass)."""
        if self.config.ft8_depth:
            return self.config.ft8_depth, None
        quick = max((d for d in (1, 2, 3) if self.timings.get(d, QUICK if d == 1 else 99)
                     <= QUICK), default=1)
        deep = next((d for d in (3, 2) if d > quick and self.timings.get(d, 0) <= DEEP), None)
        return quick, deep

    async def _decode(self, start: float, chunks: list[np.ndarray]) -> None:
        rate = self.station.transport.sample_rate
        samples = np.concatenate(chunks)
        if len(samples) < SHORTEST * CYCLE * rate:
            log.info("ft8: cycle %s too short (%.1f s), skipped", start, len(samples) / rate)
            return
        samples = samples[:CYCLE * rate]
        folder = self._work / str(int(start))       # jt9 leaves its files where it runs
        folder.mkdir(exist_ok=True)
        wav = folder / jt9.cycle_name(start)
        quick, deep = self.depths()
        self.decoding = True
        self._touch()
        try:
            await asyncio.to_thread(jt9.write_wav, wav, samples, rate)
            first = await self._pass(start, wav, quick, set())
            if deep is not None and not self._deep_busy:
                self._deep_busy = True
                try:
                    await self._pass(start, wav, deep, {d["message"] for d in first})
                finally:
                    self._deep_busy = False
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.error = str(exc)
            log.warning("ft8: %s", exc)
        finally:
            self.decoding = False
            shutil.rmtree(folder, ignore_errors=True)
            self._touch()

    async def _pass(self, start: float, wav: Path, depth: int, known: set[str]) -> list[dict]:
        """One jt9 run; sends what is new (the second pass: what the first missed)."""
        began = time.monotonic()
        limit = QUICK * 8 if not known else DEEP + 5
        try:
            decodes = await asyncio.to_thread(jt9.decode, self.program, wav, depth, limit)
        except subprocess.TimeoutExpired:
            self.timings[depth] = limit         # too slow here: not chosen again
            log.info("ft8: depth %d took over %.0f s here", depth, limit)
            return []
        took = round(time.monotonic() - began, 1)
        # Smoothed: one slow cycle (the machine busy) does not change the choice.
        before = self.timings.get(depth)
        self.timings[depth] = took if before is None else round(0.7 * before + 0.3 * took, 1)
        entries = [self._entry(d) for d in decodes if d.message not in known]
        cycle = {"start": start, "seconds": took, "depth": depth, "deep": bool(known),
                 "dial": self.station.rig.state.frequency, "decodes": entries}
        if known:
            merged = next((c for c in self.history if c["start"] == start), None)
            if merged is not None:
                merged["decodes"] = merged["decodes"] + entries
        else:
            self.history.append(dict(cycle))
        count = len(entries) + (len(known) if known else 0)
        self.last = {"start": start, "count": count, "seconds": took, "depth": depth}
        self.hub.emit({"type": "ft8.cycle", **cycle})
        self._touch()
        return entries

    @staticmethod
    def _entry(decode: jt9.Decode) -> dict:
        message = parse(decode.message)
        return {**asdict(decode), **asdict(message)}

    # ---- the band -----------------------------------------------------------------------------

    async def tune(self, band_name: str) -> int:
        band = bands.find_band(band_name)
        if band is None or band.name not in DIALS:
            raise Ft8Error(N_("No FT8 frequency for {band}"), band=band_name)
        if not self.station.connected:
            raise Ft8Error(N_("The radio is not connected"))
        rig = self.station.rig
        try:
            await rig.set_frequency(DIALS[band.name])
            await rig.set_mode("USB")
            await rig.set_data_mode(1, 1)
        except (RigError, TransportError) as exc:
            raise Ft8Error(N_("The radio did not take it: {error}"), error=str(exc)) from None
        self._touch()
        return DIALS[band.name]

    def state(self) -> dict:
        dial = self.station.rig.state.frequency
        band = bands.band_for(dial) if dial else None
        return {"on": self.on, "jt9": self.program or "", "error": self.error,
                "dial": dial, "band": band.name if band else "",
                "ft8_dial": bool(dial and dial in DIALS.values()),
                "cycle": self._start, "decoding": self.decoding, "last": self.last,
                "depths": list(self.depths())}

    async def call(self, method: str, params: dict) -> dict:
        try:
            if method == "state":
                return self.state()
            if method == "start":
                await self.start()
            elif method == "stop":
                await self.stop()
            elif method == "history":
                cycles = int(params.get("cycles", KEEP_CYCLES))
                return {"cycles": list(self.history)[-cycles:]}
            elif method == "band":
                return {"dial": await self.tune(str(params.get("band", "")))}
            else:
                return {"error": f"unknown method ft8.{method}"}
        except Ft8Error as exc:
            return {"error": str(exc), "msgid": exc.msgid, "args": {
                k: str(v) for k, v in exc.args_.items()}}
        return {"ok": True}
