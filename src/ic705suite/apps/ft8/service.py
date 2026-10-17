"""FT8 in the core: the radio's audio cut into the 15-second cycles of the UTC clock, each
decoded by jt9 while the next one is heard; and the QSO, sent in our turn.

Events (server -> client):
  ft8        {on, jt9, band, dial, cycle, decoding, last, error, tx: {...}}  when it changes
  ft8.cycle  {start, pass, seconds, depth, dial, decodes: [{time, snr, dt, freq, message,
             doubtful, kind, sender, to, grid, report, end, cq_to}]}  what a pass found in a
             cycle: "early" (in a QSO, near the other station, at second 13), "quick", then
             "deep" when the machine has time; a cycle seen before gets the new decodes
  ft8.tx     {start, message, freq}  a message sent
  ft8.sent   {start, message, alc, watts}  how it went out: the highest ALC (0 none, 1 the
             top of the radio's ALC zone) and RF power the radio showed while sending
  ft8.qso    {qso: {ADIF fields}, path}  a QSO completed, added to ft8_log.adi
  (each decode also has, where known: country, continent, lat, lon, approx (from the
  country, not a grid), km and az from our locator; with the shared logbook b4, b4_any,
  dxcc and grid_state: new, new_band, worked or confirmed, see worked.py)
  ft8.wf     {time, low, step, levels}  a waterfall row (apps.ft8.waterfall), four a second
             while a screen asks for them (ft8.waterfall)
  info       a QSO completed, given up, or a transmission refused (and why)
Calls ("ft8.*"): state, start, stop, history {cycles}, band {band}, cq {freq}, reply
  {decode, start}, worked (the grids and entities worked and confirmed, and our place),
  dupes {on} (allow a second QSO with a station on a band it was worked on: off, the
  default, leaves it unanswered, by hand, in the auto mode and when it answers our CQ),
  halt, tone {on, seconds} (a steady tone on our frequency, for the ALC,
  at most TONE_LONGEST s; halt stops it too), txfreq {freq}, waterfall {seconds} (rows for
  that long: asked again to keep them), level {level} (the transmit audio, 0.05-1 of full
  scale: lower it when the ALC moves), log (the QSOs completed and ft8_log.adi)
Decoding ([ft8] depth 0, the default): as WSJT-X, the deepest pass that is done in about
3 s first, then a deeper one if it fits before the next cycle, from what each depth took
here (a Raspberry Pi 4: depth 1 in 3 s, 2 in 9 s, 3 in 23 s). In a QSO a narrow pass near
the other station at second 13 (under a second, even on the Pi) decides what goes next.
Transmitting: our turn is every other cycle, the other parity from the station we answer;
the message starts 0.5 s into the cycle, as audio over the radio's own link, keyed by CI-V;
only with tx_enabled and the radio in a data mode (its DATA1 MOD input set to WLAN).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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

from ic705suite import geo
from ic705suite.apps.ft8 import adif, encode, jt9, waterfall
from ic705suite.apps.ft8.auto import CQS, STATIONS, TRIES_EACH, Auto
from ic705suite.apps.ft8.messages import base, parse
from ic705suite.apps.ft8.sequence import TRIES, Qso
from ic705suite.i18n import N_
from ic705suite.paths import data_dir
from ic705suite.rig import bands
from ic705suite.rig.base import RigError
from ic705suite.rig.transport import TransportError

log = logging.getLogger(__name__)

CYCLE = jt9.CYCLE
KEEP_CYCLES = 40           # decoded cycles kept for a screen opened later
SHORTEST = 0.8             # of a cycle: less audio than this (a gap, a reconnection) is skipped
QUICK = 3.5                # seconds the first pass may take
DEEP = 13.5                # seconds the second pass may take: done before the next cycle ends
EARLY = 13.2               # second of the cycle when, in a QSO, the narrow pass runs
NEAR = 250                 # Hz each side of the other station for that pass
CQ_EARLY = 11.8 / 13.2     # calling CQ, that pass comes sooner (second 11.8, as WSJT-X's
                           # first) over the whole passband: a caller may be anywhere
EARLY_BUDGET = 1.8         # seconds the narrow pass may take to be ready for our turn
EARLY_WAIT = 1.0           # our turn waits this long (at most) for the early pass
START = 0.5                # seconds into our cycle when the message starts
LATE = 2.0                 # starting later than this would not be decoded well: next turn
LEAD = 0.1                 # audio sent this much ahead (the radio keeps a small buffer)
PIECE = 0.02               # seconds of audio per piece sent
TAIL = 0.3                 # after the last piece, before unkeying
LEVEL_RANGE = (0.05, 1.0)  # the transmit audio, of full scale ([ft8] level; the radio's
                           # LAN MOD level scales it again)
# The ALC meter as the radio's (1 = the top of its ALC zone). Icom wants it within the zone
# in data modes: there the radio gives the power set. Past it the audio is too strong; at 0
# the radio may give less than set (not enough audio).
ALC_HIGH = 1.0             # over this: past the zone, lower the level
ALC_IDLE = 0.05            # under this the ALC does not move at all
SHORT_OF_POWER = 0.8       # of the power set: less, with the ALC still, says raise the level
CUT_POWER = 0.5            # of the power set: less with the ALC acting, the radio cuts it
LOW_SETTING_W = 2.0        # the radio set to less than this: said (it may have been left so)
LONGEST_KEY = 16.0         # the PTT watchdog while a message goes out
CQ_TURNS = 20              # CQs with nobody answering (5 minutes): stop
WORKED_EVERY = 60.0        # seconds between readings of the worked index (the logbook)
TONE_SECONDS = 10.0        # the tune tone, unless asked otherwise
TONE_LONGEST = 15.0        # and never longer
DATA1_MOD = b"\x1a\x05\x01\x19"
MOD_WLAN = 0x03

# FT8 dial frequencies (USB), as WSJT-X has them for IARU Region 1.
DIALS = {"160m": 1_840_000, "80m": 3_573_000, "60m": 5_357_000, "40m": 7_074_000,
         "30m": 10_136_000, "20m": 14_074_000, "17m": 18_100_000, "15m": 21_074_000,
         "12m": 24_915_000, "10m": 28_074_000, "6m": 50_313_000, "2m": 144_174_000,
         "70cm": 432_174_000}


class Ft8Error(Exception):
    def __init__(self, msgid: str, **args: object) -> None:
        super().__init__(msgid.format(**args) if args else msgid)
        self.msgid, self.args_ = msgid, args


def clamp_level(level: float) -> float:
    return round(max(LEVEL_RANGE[0], min(LEVEL_RANGE[1], float(level))), 2)


async def sleep_until(moment: float) -> None:
    """Until the clock reads `moment` (asyncio's timers may wake a few ms early)."""
    while (wait := moment - time.time()) > 0:
        await asyncio.sleep(max(wait, 0.001))


def cycle_start(now: float) -> float:
    return math.floor(now / CYCLE) * CYCLE


def parity(start: float) -> int:
    """0 for the even cycles (:00, :30), 1 for the odd ones (:15, :45)."""
    return int(round(start / CYCLE)) % 2


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
        self.coder: str | None = None
        self._chunks: list[np.ndarray] = []
        self._wf_until = 0.0                      # rows sent until then (monotonic)
        self._wf_audio: deque[np.ndarray] = deque()
        self._wf_fresh = False                    # audio came since the last row
        self._wf_task: asyncio.Task | None = None
        self._start: float | None = None       # the cycle being heard (None: a partial one)
        self._task: asyncio.Task | None = None
        self._work: Path | None = None
        self._pending: set[asyncio.Task] = set()
        self._changed = True
        self.timings: dict[int, float] = {}       # seconds each depth took here, lately
        self.early_timings: dict[int, float] = {}  # the narrow early pass's, per depth
        self._early_task: asyncio.Task | None = None
        self._deep_busy = False
        self._known: dict[float, set[str]] = {}   # messages already sent out, per cycle
        # Transmitting
        self.qso = Qso(config.callsign.upper(), config.locator.upper()[:4])
        self.tx_on = False
        self.tx_parity = 0
        self.tx_freq = config.ft8_tx_freq
        self.tx_level = clamp_level(config.ft8_level)
        self.ignore_calls: set[str] = set()      # never worked by the auto mode
        self.ignore_countries: set[str] = set()  # DXCC entities, as cty.dat names them
        self.allow_dupes = False                  # a station worked on this band again
        self._load_settings()                     # as the operator left them last time
        self.last_sent: dict = {}                 # the last message's ALC and power peaks
        self.their_freq = 0                       # the other station's audio frequency
        self.transmitting = False
        self.cq_turns = 0
        self._sending: asyncio.Task | None = None
        self._mod_set = False
        self._tone: asyncio.Task | None = None
        self.qso_began: float | None = None      # our first message to the other station
        self.qso_watts = 0.0                     # the RF power the radio gave in this QSO
        self._late_until = 0.0                   # a give-up whose answer may still come
        self._short_said: float | None = None    # the level "short of power" was said for
        self._cut_said = False                   # "the radio cuts its power" said
        self._setting_said: int | None = None    # the low power setting said
        # Who is where, and what has been worked (the shared logbook: worked.py).
        self.worked = None
        self._worked_task: asyncio.Task | None = None
        self._grids: dict[str, str] = {}         # call -> the grid it last sent
        self.logged: list[dict] = []
        self._worked_now: set[tuple[str, str]] = set()   # (call, band) logged since it started
        self.auto = Auto()                       # the super-automatic mode (auto.py)
        self._drop_them = False                  # the station called works another: next
        # Nobody connected: it goes on decoding, and transmits for this long at most
        # ([ft8] unattended minutes, 0 without a limit; a client changes it until a restart).
        self.unattended = max(0, int(config.ft8_unattended))
        self._alone_since: float | None = None   # when the last client left

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
        self.coder = encode.find_ft8code(self.config.ft8_jt9)
        self.error = ""
        self._work = Path(tempfile.mkdtemp(prefix="ic705suite-ft8-"))
        self._chunks, self._start = [], None
        self.station.add_audio_listener(self._audio)
        self.on = True
        self._task = asyncio.create_task(self._run())
        self._wf_task = asyncio.create_task(self._waterfall())
        if self.config.logbook_database and self._worked_task is None:
            self._worked_task = asyncio.create_task(self._keep_worked())
        self._touch()

    async def stop(self) -> None:
        await self.halt()
        if not self.on:
            return
        self.on = False
        self.station.remove_audio_listener(self._audio)
        for task in (self._task, self._wf_task, self._worked_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 - it ended badly before: stop anyway
                    log.warning("ft8: a task had stopped: %s", exc)
        self._task = self._wf_task = self._worked_task = None
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

    def _background(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    # ---- the cycles ---------------------------------------------------------------------------

    def _audio(self, samples: np.ndarray) -> None:
        if self._start is not None:
            self._chunks.append(samples.copy())
        if time.monotonic() < self._wf_until:
            self._wf_audio.append(samples.copy())
            self._wf_fresh = True

    async def _waterfall(self) -> None:
        """A spectrum row every quarter of a second, while a screen watches."""
        while True:
            await asyncio.sleep(waterfall.INTERVAL)
            if time.monotonic() >= self._wf_until:
                # Nobody watches: what was kept goes, and so does "fresh" (else, watched
                # again, an empty buffer was taken for new audio and this task died).
                self._wf_audio.clear()
                self._wf_fresh = False
                continue
            if not self._wf_fresh or not self._wf_audio:
                continue                                   # no new audio: no new row
            self._wf_fresh = False
            try:
                rate = self.station.transport.sample_rate
                keep = waterfall.window_size(rate)
                samples = np.concatenate(self._wf_audio)[-keep:]
                self._wf_audio.clear()
                self._wf_audio.append(samples)             # the next row overlaps it
                levels = waterfall.encode(waterfall.row(samples, rate))
            except Exception as exc:  # noqa: BLE001 - one row lost, never the waterfall
                log.warning("ft8: a waterfall row: %s", exc)
                self._wf_audio.clear()
                continue
            self.hub.emit({"type": "ft8.wf", "time": time.time(), "low": waterfall.LOW,
                           "step": waterfall.STEP, "levels": levels})

    async def _run(self) -> None:
        last = 0.0
        while True:
            now = time.time()
            # Never the same boundary twice (a timer may wake a little early: Windows).
            boundary = max(cycle_start(now) + CYCLE, last + CYCLE)
            last = boundary
            early_at = boundary - CYCLE + EARLY * (CQ_EARLY if self.qso.step == "cq" else 1)
            if self._listening_for_them() and now < early_at:
                await sleep_until(early_at)
                if self._start is not None and self._listening_for_them():
                    self._early_task = self._background(
                        self._early(self._start, list(self._chunks)))
            await sleep_until(boundary)
            heard, start = self._chunks, self._start
            self._chunks, self._start = [], boundary
            if start is not None and heard:
                self._background(self._decode(start, heard))
            if self.tx_on and parity(boundary) == self.tx_parity:
                self._sending = self._background(self._transmit(boundary))
            self._touch()

    def _listening_for_them(self) -> bool:
        """In a QSO, and this cycle is the other station's turn."""
        if not self.tx_on or self.qso.step in ("idle", "done"):
            return False
        return parity(cycle_start(time.time())) != self.tx_parity

    def depths(self) -> tuple[int, int | None]:
        """The first pass's depth and the second's (None: no second pass)."""
        if self.config.ft8_depth:
            return self.config.ft8_depth, None
        quick = max((d for d in (1, 2, 3) if self.timings.get(d, QUICK if d == 1 else 99)
                     <= QUICK), default=1)
        deep = next((d for d in (3, 2) if d > quick and self.timings.get(d, 0) <= DEEP), None)
        return quick, deep

    def _samples(self, chunks: list[np.ndarray], whole: bool) -> np.ndarray | None:
        rate = self.station.transport.sample_rate
        if not chunks:
            return None
        samples = np.concatenate(chunks)
        if whole and len(samples) < SHORTEST * CYCLE * rate:
            return None
        if len(samples) < CYCLE * rate:          # the early pass: the rest is still to come
            samples = np.concatenate([samples, np.zeros(CYCLE * rate - len(samples),
                                                        np.float32)])
        return samples[:CYCLE * rate]

    async def _early(self, start: float, chunks: list[np.ndarray]) -> None:
        """At second 13 of their turn: what the other station said, in time to answer."""
        samples = self._samples(chunks, whole=False)
        if samples is None:
            return
        if self.qso.step == "cq":
            # A caller may answer anywhere: the whole passband, as fast as it goes.
            band, depth = None, 1
        else:
            centre = self.their_freq or self.tx_freq
            band = (max(100, centre - NEAR), min(3500, centre + NEAR))
            depth = self._early_depth()
        folder = self._folder(start, "early")
        wav = folder / jt9.cycle_name(start)
        try:
            await asyncio.to_thread(jt9.write_wav, wav, samples,
                                    self.station.transport.sample_rate)
            await self._pass(start, wav, depth, "early", band=band, limit=QUICK * 2)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            log.warning("ft8: early pass: %s", exc)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _early_depth(self) -> int:
        """The deepest narrow pass ready in time for our turn here: what it took lately,
        else a guess from the whole passband's (a fifth of the band: about a quarter of the
        time). A Raspberry Pi takes 1; a desktop, 3."""
        for depth in (3, 2):
            took = self.early_timings.get(depth)
            if took is None:
                took = self.timings.get(depth, 99.0) * 0.3
            if took <= EARLY_BUDGET:
                return depth
        return 1

    async def _decode(self, start: float, chunks: list[np.ndarray]) -> None:
        samples = self._samples(chunks, whole=True)
        if samples is None:
            log.info("ft8: cycle %s too short, skipped", start)
            return
        folder = self._folder(start, "full")
        wav = folder / jt9.cycle_name(start)
        quick, deep = self.depths()
        self.decoding = True
        self._touch()
        try:
            await asyncio.to_thread(jt9.write_wav, wav, samples,
                                    self.station.transport.sample_rate)
            await self._pass(start, wav, quick, "quick", limit=QUICK * 8)
            if deep is not None and not self._deep_busy:
                self._deep_busy = True
                try:
                    await self._pass(start, wav, deep, "deep", limit=DEEP + 5)
                finally:
                    self._deep_busy = False
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.error = str(exc)
            log.warning("ft8: %s", exc)
        finally:
            self.decoding = False
            shutil.rmtree(folder, ignore_errors=True)
            self._touch()

    def _folder(self, start: float, name: str) -> Path:
        folder = self._work / f"{int(start)}-{name}"      # jt9 leaves its files where it runs
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    async def _pass(self, start: float, wav: Path, depth: int, kind: str,
                    band: tuple[int, int] | None = None, limit: float = 30.0) -> list[dict]:
        """One jt9 run; sends what is new in this cycle, and lets the QSO hear it."""
        began = time.monotonic()
        try:
            decodes = await asyncio.to_thread(jt9.decode, self.program, wav, depth, limit, band)
        except subprocess.TimeoutExpired:
            if kind == "early" and band is not None:
                self.early_timings[depth] = limit
            elif band is None and kind != "early":
                self.timings[depth] = limit     # too slow here: not chosen again
            log.info("ft8: depth %d took over %.0f s here", depth, limit)
            return []
        took = round(time.monotonic() - began, 1)
        if kind == "early" and band is not None:
            before = self.early_timings.get(depth)
            self.early_timings[depth] = took if before is None else \
                round(0.7 * before + 0.3 * took, 1)
        elif band is None and kind != "early":
            # Smoothed: one slow cycle (the machine busy) does not change the choice.
            before = self.timings.get(depth)
            self.timings[depth] = took if before is None else round(0.7 * before + 0.3 * took, 1)
        known = self._known.setdefault(start, set())
        for old in [s for s in self._known if s < start - 4 * CYCLE]:
            del self._known[old]
        entries = [self._entry(d) for d in decodes if d.message not in known]
        known.update(e["message"] for e in entries)
        cycle = {"start": start, "pass": kind, "seconds": took, "depth": depth,
                 "dial": self.station.rig.state.frequency, "decodes": entries}
        seen = next((c for c in self.history if c["start"] == start), None)
        if seen is not None:
            seen["decodes"] = seen["decodes"] + entries
        else:
            self.history.append({**cycle, "decodes": list(entries)})
        if kind != "early":
            self.last = {"start": start, "count": len(known), "seconds": took, "depth": depth}
        self.hub.emit({"type": "ft8.cycle", **cycle})
        self._hear(start, entries)
        if self._drop_them:
            self._drop_them = False
            await self._stop_tx()                 # the next one comes from this cycle
        if kind != "early":
            await self._auto_turn(start)
        self._touch()
        return entries

    def _entry(self, decode: jt9.Decode) -> dict:
        message = parse(decode.message)
        entry = {**asdict(decode), **asdict(message)}
        self._place(entry)
        return entry

    def _place(self, entry: dict) -> None:
        """Where the sender is (its grid, the one it sent last, or its country's middle),
        how far and which way from us, and what the logbook says of it."""
        sender = (entry.get("sender") or "").strip("<>")
        if not sender or sender == "...":
            return
        grid = entry.get("grid") or ""
        if grid and geo.is_locator(grid):
            self._grids[sender] = grid
            if len(self._grids) > 20000:
                self._grids.pop(next(iter(self._grids)))
        else:
            grid = self._grids.get(sender, "")
        entity = geo.entity_for(sender)
        if entity is not None:
            entry["country"], entry["continent"] = entity.name, entity.continent
        where = geo.locator_to_latlon(grid) if grid else None
        if where is None and entity is not None:
            where, entry["approx"] = (entity.lat, entity.lon), True
        if where is not None:
            entry["lat"], entry["lon"] = round(where[0], 3), round(where[1], 3)
            mine = geo.locator_to_latlon(self.config.locator)
            if mine is not None:
                entry["km"] = round(geo.distance_km(mine, where))
                entry["az"] = round(geo.bearing_deg(mine, where))
        if self.worked is not None:
            dial = self.station.rig.state.frequency
            band = bands.band_for(dial) if dial else None
            entry.update(self.worked.status(sender, band.name if band else "", grid))

    async def _keep_worked(self) -> None:
        """The worked index, read from the logbook every minute (other clients log too)."""
        while True:
            await self._read_worked()
            await asyncio.sleep(WORKED_EVERY)

    async def _read_worked(self) -> None:
        from ic705suite.worked import from_logbook

        try:
            self.worked = await asyncio.to_thread(from_logbook, self.config.callsign)
        except Exception as exc:  # noqa: BLE001 - the marks wait for the next time
            log.warning("ft8: the worked index: %s", exc)

    # ---- the QSO ------------------------------------------------------------------------------

    def _hear(self, start: float, entries: list[dict]) -> None:
        """Decodes of the other station's turn move the QSO on."""
        if not self.tx_on:
            self._late_end(start, entries)
            return
        if parity(start) == self.tx_parity:
            return
        for entry in entries:
            if self.qso.them and base(entry.get("sender") or "") == base(self.qso.them):
                # Where they are now, whatever they say (a CQ too): the narrow pass at
                # second 13 looks there.
                self.their_freq = entry["freq"]
                if self._busy(entry):
                    self._drop_them = True
                    self.hub.info(N_("FT8 auto: {call} is working {other}; on to the next"),
                                  call=self.qso.them, other=entry.get("to") or "?")
                    self.auto.missed_one(self.qso.them, time.time())
                    break
            if self.qso.step == "cq" and (self.auto.on and self.ignored(entry)
                                          or self.dupe(entry)):
                continue                          # not answered: ignored, or worked before
            if self.qso.heard(entry):
                self.cq_turns = 0
                self._touch()
        if self.qso.step == "done":
            self._finished()

    def _busy(self, entry: dict) -> bool:
        """In the auto mode, the station we call (it was calling CQ) answers another: it
        will not hear us now."""
        to = entry.get("to") or ""
        return self.auto.on and self.qso.step == "tx1" and entry.get("kind") == "call" \
            and bool(to) and not to.startswith("<") and base(to) != base(self.qso.mycall)

    def ignored(self, entry: dict) -> bool:
        """A station, or its country, the operator does not want the auto mode to work."""
        sender = base(entry.get("sender") or "")
        return bool(sender) and (sender in self.ignore_calls
                                 or (entry.get("country") or "") in self.ignore_countries)

    def dupe(self, entry: dict) -> bool:
        """A station worked on this band before, with dupes not allowed: the logbook's mark,
        or a QSO logged here that the worked index has not read yet (or with no logbook)."""
        if self.allow_dupes:
            return False
        sender = base(entry.get("sender") or "")
        if not sender or sender.startswith("<"):
            return False
        dial = self.station.rig.state.frequency
        band = bands.band_for(dial) if dial else None
        return bool(entry.get("b4")) or \
            (sender, (band.name if band else "").lower()) in self._worked_now

    def set_dupes(self, on: bool) -> dict:
        self.allow_dupes = bool(on)
        self._save_settings()
        self._touch()
        return {"allow": self.allow_dupes}

    def set_ignore(self, call: str = "", country: str = "", on: bool = True) -> dict:
        for name, table in ((base(call), self.ignore_calls), (country, self.ignore_countries)):
            if name:
                if on:
                    table.add(name)
                else:
                    table.discard(name)
        self._save_settings()
        self._touch()
        return self.ignore_state()

    def ignore_state(self) -> dict:
        return {"calls": sorted(self.ignore_calls), "countries": sorted(self.ignore_countries)}

    def _late_end(self, start: float, entries: list[dict]) -> None:
        """Their RR73 (or RRR, 73) decoded just after we gave up on them (it comes in the
        cycle after our last try, sometimes after our turn began): the reports went both
        ways, so the QSO counts and is logged."""
        qso = self.qso
        if not self._late_until or start > self._late_until or not qso.them:
            return
        for entry in entries:
            if base(entry.get("sender") or "") == base(qso.them) and qso._for_me(entry) \
                    and entry.get("end") in ("RRR", "RR73", "73") and qso.complete:
                self._late_until = 0.0
                self._log()
                self.hub.info(N_("FT8 QSO with {call} complete (reports {sent} and {got}): "
                                 "their {end} came just after the last try"),
                              call=qso.them, sent=qso.sent_report, got=qso.got_report,
                              end=entry["end"])
                self._touch()
                return

    async def call_cq(self, freq: int | None = None) -> str:
        self._check_tx()
        if freq:
            self.tx_freq = int(freq)
            self._save_settings()
        message = self.qso.call_cq()
        self.their_freq, self.cq_turns, self.qso_began = 0, 0, None
        self.qso_watts, self._late_until = 0.0, 0.0
        # The next cycle is ours.
        self.tx_parity = parity(cycle_start(time.time()) + CYCLE)
        self.tx_on = True
        self._touch()
        return message

    async def reply(self, decode: dict, start: float, tries: int = TRIES) -> str:
        """Answer a station heard in the cycle that began at `start` (called `tries` times
        while it does not answer)."""
        self._check_tx()
        if not decode.get("sender") or decode.get("kind") not in ("cq", "call"):
            raise Ft8Error(N_("Nobody to answer in that message"))
        if self.dupe(decode):
            raise Ft8Error(N_("{call} was already worked on this band (allow dupes to work "
                              "it again)"), call=decode["sender"])
        message = self.qso.answer(decode)
        self.qso.first_tries = tries
        self.their_freq = int(decode.get("freq") or 0)
        self.qso_began = None
        self.qso_watts, self._late_until = 0.0, 0.0
        self.tx_parity = 1 - parity(start)
        self.tx_on = True
        self._touch()
        return message

    @property
    def sending(self) -> bool:
        """Anything that transmits under way: a QSO, CQ, the tone, the super-automatic."""
        return self.tx_on or self.transmitting or self.toning or self.auto.on

    def watch_clients(self, clients: int, now: float | None = None) -> None:
        """The core's clients, every moment: with nobody connected for `unattended`
        minutes, transmitting stops (decoding goes on)."""
        now = time.monotonic() if now is None else now
        if clients > 0:
            self._alone_since = None
            return
        if self._alone_since is None:
            self._alone_since = now
            return
        if self.unattended and self.sending and \
                now - self._alone_since >= self.unattended * 60:
            self._alone_since = now               # said once per stretch
            self.hub.info(N_("FT8: nobody connected for {minutes} min: transmission stopped, "
                             "still decoding"), minutes=self.unattended)
            self._background(self.halt())

    async def halt(self) -> None:
        """Stop transmitting now (and the QSO, or the tone, and the super-automatic mode)."""
        if self.auto.on:
            self.auto.stop()
            self.hub.info(N_("FT8: super-automatic mode off"))
        await self._stop_tx()

    async def _stop_tx(self) -> None:
        """Stop transmitting now; the super-automatic mode goes on."""
        await self._stop_tone()
        self.tx_on = False
        self.qso.stop()
        sending, self._sending = self._sending, None
        if sending is not None and not sending.done():
            sending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sending
        if self.transmitting:
            await self._unkey()
        self._touch()

    def _check_tx(self) -> None:
        if not self.on:
            raise Ft8Error(N_("FT8 is stopped: S starts it"))
        if not self.config.tx_enabled:
            raise Ft8Error(N_("Transmission is off ([rig] tx_enabled, --tx)"))
        if self.toning:
            raise Ft8Error(N_("The tone is on: T stops it"))
        if not self.config.callsign:
            raise Ft8Error(N_("No callsign: [station] callsign"))
        if self.coder is None:
            raise Ft8Error(N_("WSJT-X's ft8code is not installed here (it comes with jt9)"))
        self.qso.mycall = self.config.callsign.upper()
        self.qso.mygrid = self.config.locator.upper()[:4]

    @property
    def log_path(self) -> Path:
        return data_dir() / "ft8" / "ft8_log.adi"

    @property
    def settings_path(self) -> Path:
        return data_dir() / "ft8" / "settings.json"

    def _load_settings(self) -> None:
        """The transmit level and frequency set last time (the level that keeps the ALC at
        0 is found once): over [ft8] level and tx_freq, which are the first ones."""
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
            self.tx_level = clamp_level(saved.get("level", self.tx_level))
            self.tx_freq = max(200, min(3000, int(saved.get("tx_freq", self.tx_freq))))
            self.ignore_calls = {str(c).upper() for c in saved.get("ignore_calls", [])}
            self.ignore_countries = {str(c) for c in saved.get("ignore_countries", [])}
            self.allow_dupes = bool(saved.get("allow_dupes", False))
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save_settings(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(
                {"level": self.tx_level, "tx_freq": self.tx_freq,
                 "ignore_calls": sorted(self.ignore_calls),
                 "ignore_countries": sorted(self.ignore_countries),
                 "allow_dupes": self.allow_dupes}), encoding="utf-8")
        except OSError as exc:
            log.warning("ft8: cannot save %s: %s", self.settings_path, exc)

    def _finished(self) -> None:
        them = self.qso.them
        if self.auto.on:
            if self.qso.complete:
                self.auto.worked_one()
            elif them:
                self.auto.missed_one(them, time.time())
        if self.qso.complete:
            self._log()
            self.hub.info(N_("FT8 QSO with {call} complete (reports {sent} and {got})"),
                          call=them, sent=self.qso.sent_report, got=self.qso.got_report)
        self.tx_on = False
        self.qso.stop()
        self.qso_began = None
        self._touch()

    def _log(self) -> None:
        """The QSO into ft8_log.adi, and to the clients (each one's logbook)."""
        qso, rig = self.qso, self.station.rig
        # The power measured while sending (the radio may give less than it is set to: a
        # high SWR); the setting when the meter said nothing.
        power = rig.state.power
        watts = self.qso_watts or (round(power * self.station.model.max_power_w / 100, 1)
                                   if power is not None else None)
        # Their grid: the one the QSO's messages carried, else the last one they sent at all
        # (a CQ before, a call to another): a station answering with its report sends none.
        grid = qso.their_grid or self._grids.get(qso.them.strip("<>"), "")
        fields = adif.record(call=qso.them, grid=grid, sent=qso.sent_report,
                             received=qso.got_report, began=self.qso_began or time.time(),
                             ended=time.time(), dial=rig.state.frequency or 0,
                             audio=self.tx_freq, mycall=qso.mycall,
                             mygrid=self.config.locator.upper() or qso.mygrid,  # all of it
                             power_w=watts)
        try:
            adif.append(self.log_path, fields)
        except OSError as exc:
            self.hub.error(N_("FT8: cannot write {path}: {error}"), path=str(self.log_path),
                           error=str(exc))
        self.logged.append(fields)
        self._worked_now.add((base(qso.them), (fields.get("BAND") or "").lower()))
        # With the shared logbook ([logbook]) the core logs it, once for every client.
        here = bool(self.config.logbook_database)
        self.hub.emit({"type": "ft8.qso", "qso": fields, "path": str(self.log_path),
                       "logbook": here})
        if here:
            self._background(self._into_logbook(fields))

    async def _into_logbook(self, fields: dict) -> None:
        from ic705suite.apps.ft8.logbook import log_qso

        try:
            row, why = await asyncio.to_thread(log_qso, fields, self.config.ft8_setup,
                                               self.station.model.name)
        except Exception as exc:  # noqa: BLE001 - said, and the ADIF has it anyway
            row, why = None, str(exc)
        if row is not None:
            await self._read_worked()               # marked worked from now on
            self.hub.info(N_("{call} logged in the logbook (FT8 {band}, sent {sent}, "
                             "received {rcvd})"), call=fields["CALL"],
                          band=fields.get("BAND", ""), sent=fields.get("RST_SENT", ""),
                          rcvd=fields.get("RST_RCVD", ""))
        elif why != "already logged":
            self.hub.error(N_("Not in the logbook: {error} (it is in ft8_log.adi)"),
                           error=why)

    # ---- the super-automatic mode ---------------------------------------------------------------

    async def set_auto(self, on: bool) -> dict:
        """Turn the super-automatic mode on (it begins with the next cycle heard) or off
        (a QSO under way ends as usual; calling CQ, or a station not answering yet, stops)."""
        if on:
            if not self.config.ft8_auto:
                raise Ft8Error(N_("The super-automatic mode is not available on this station"))
            self._check_tx()
            # What would stop its first call (the radio not in USB-D...) is said now, not
            # a cycle or two later with the mode turned off.
            problem = await self._cannot_key()
            if problem is not None:
                msgid, args = problem
                raise Ft8Error(msgid, **args)
            self.auto.start()
            self.hub.info(N_("FT8: super-automatic mode on (H or A stops it)"))
        elif self.auto.on:
            self.auto.stop()
            self.hub.info(N_("FT8: super-automatic mode off"))
            if self.tx_on and self.qso.step in ("cq", "tx1"):
                await self._stop_tx()
        self._touch()
        return self.auto_state()

    def auto_state(self) -> dict:
        return {"on": self.auto.on, "phase": self.auto.phase, "missed": self.auto.missed,
                "stations": STATIONS, "cqs": CQS, "allowed": self.config.ft8_auto}

    async def _auto_turn(self, start: float) -> None:
        """A cycle decoded: with nothing under way, the next station, or CQ."""
        auto = self.auto
        if not auto.on or self.tx_on or self.transmitting or self.toning:
            return
        if self._late_until and start <= self._late_until:
            return                          # the last one's RR73 may still come
        cycle = next((c for c in self.history if c["start"] == start), None)
        decodes = cycle["decodes"] if cycle is not None else []
        pick = None if auto.wants_cq() else auto.pick(
            [d for d in decodes if not self.ignored(d) and not self.dupe(d)],
            self.config.callsign, time.time())
        if pick is not None:
            auto.quiet = 0
            await self._auto_go(self.reply(pick, start, tries=TRIES_EACH),
                                *self._auto_why(pick))
            return
        auto.quiet += 1
        if auto.wants_cq():
            auto.phase, auto.missed, auto.quiet = "cq", 0, 0
            await self._auto_go(self.call_cq(), N_("FT8 auto: calling CQ ({count} times)"),
                                {"count": CQS})

    async def _auto_go(self, step, msgid: str, args: dict) -> None:
        try:
            await step
        except Ft8Error as exc:
            self.auto.stop()
            self.hub.error(N_("FT8: super-automatic mode off: {error}"), error=str(exc))
            return
        self.hub.info(msgid, **args)

    @staticmethod
    def _auto_why(d: dict) -> tuple[str, dict]:
        """What is said of the station chosen, and why it was."""
        args = {"call": d["sender"], "country": d.get("country") or "?",
                "count": d.get("dxcc_count", 0)}
        if d.get("kind") == "call":
            return N_("FT8 auto: answering {call}, who calls us"), args
        if d.get("dxcc") == "new":
            return N_("FT8 auto: calling {call}, a new DXCC ({country})"), args
        if d.get("dxcc") == "new_band":
            return N_("FT8 auto: calling {call}, {country} new on this band"), args
        return N_("FT8 auto: calling {call} ({country}, {count} QSOs)"), args

    # ---- sending ------------------------------------------------------------------------------

    async def _transmit(self, start: float) -> None:
        qso = self.qso
        early = self._early_task
        if early is not None and not early.done():
            # What the other station (or a caller to our CQ) said may come any moment: a
            # little wait, and our message answers it in this turn instead of the next.
            with contextlib.suppress(Exception):     # late or failed: the turn goes on
                await asyncio.wait_for(asyncio.shield(early), max(
                    0.0, start + START + min(EARLY_WAIT, LATE / 2) - time.time()))
        if qso.step == "cq":
            self.cq_turns += 1
            if self.auto.on and self.cq_turns > CQS:
                self.hub.info(N_("FT8 auto: nobody answered {count} CQs; answering again"),
                              count=CQS)
                self.auto.cq_done()
                await self._stop_tx()
                return
            if self.cq_turns > CQ_TURNS:
                self.hub.info(N_("FT8: nobody answered {count} CQs; calling stopped"),
                              count=CQ_TURNS)
                await self.halt()
                return
        if qso.gave_up:
            self.hub.info(N_("FT8: no answer from {call}; stopped"), call=qso.them or "?")
            began = self.qso_began
            if self.auto.on:
                self.auto.missed_one(qso.them, time.time())
            await self._stop_tx()
            self.qso_began = began               # for a late end (_late_end)
            self._late_until = start + CYCLE     # their turns before and after ours
            return
        message = qso.message
        if not message:
            return
        begin = start + START
        lateness = time.time() - begin
        if lateness > LATE:
            log.info("ft8: too late for this turn (%.1f s)", lateness)
            return
        problem = await self._cannot_key()
        if problem is not None:
            msgid, args = problem
            self.hub.info(msgid, **args)
            await self.halt()
            return
        await self._read_power()
        rate = self.station.transport.sample_rate
        try:
            sound = await asyncio.to_thread(encode.audio, self.coder, message, self.tx_freq)
        except encode.EncodeError as exc:
            self.hub.error(N_("FT8 cannot send {message}: {error}"), message=message,
                           error=str(exc))
            await self.halt()
            return
        sound = jt9.resample(sound, encode.RATE, rate) * self.tx_level
        pcm = (np.clip(sound, -1, 1) * 32767).astype("<i2").tobytes()
        piece = int(rate * PIECE) * 2
        meters = self.station.rig.state.meters
        alc = watts = swr = 0.0
        try:
            await asyncio.sleep(max(0.0, begin - LEAD - 0.2 - time.time()))
            # Marked first: halted while the radio is being keyed, it is still released.
            self.transmitting = True
            await self.station.rig.ptt(True, longest=LONGEST_KEY)
            for meter in ("alc", "po"):          # the last message's: read anew for this one
                meters.pop(meter, None)
            if qso.them and self.qso_began is None:
                self.qso_began = time.time()          # TIME_ON: our first word to them
            self._touch()
            self.hub.emit({"type": "ft8.tx", "start": start, "message": message,
                           "freq": self.tx_freq})
            began = max(begin, time.time())
            for index, offset in enumerate(range(0, len(pcm), piece)):
                await asyncio.sleep(max(0.0, began + index * PIECE - LEAD - time.time()))
                self.station.transport.send_audio(pcm[offset:offset + piece])
                if index % 25 == 0:      # the station reads the meters while transmitting
                    alc = max(alc, float(meters.get("alc") or 0))
                    watts = max(watts, float(meters.get("po") or 0))
                    swr = max(swr, float(meters.get("swr") or 0))
            await asyncio.sleep(max(0.0, began + len(pcm) / 2 / rate - time.time()) + TAIL)
            alc = max(alc, float(meters.get("alc") or 0))
            watts = max(watts, float(meters.get("po") or 0))
            swr = max(swr, float(meters.get("swr") or 0))
        except (RigError, TransportError) as exc:
            self.hub.error(N_("FT8 transmission failed: {error}"), error=str(exc))
            await self.halt()
            return
        finally:
            if self.transmitting:
                await self._unkey()
        self._sent(start, message, alc, watts, swr)
        qso.sent(message)
        self._touch()
        if qso.step == "done":
            self._finished()

    # ---- the tune tone -------------------------------------------------------------------------

    @property
    def toning(self) -> bool:
        return self._tone is not None and not self._tone.done()

    async def tone(self, on: bool, seconds: float = TONE_SECONDS) -> None:
        """A steady tone on our transmit frequency and level, as WSJT-X's Tune: to set the
        level with the ALC moving, with nobody to answer."""
        if not on:
            await self._stop_tone()
            return
        if self.toning:
            return
        if self.tx_on or self.transmitting:
            raise Ft8Error(N_("Transmitting a message: H halts it first"))
        problem = await self._cannot_key()
        if problem is not None:
            msgid, args = problem
            raise Ft8Error(msgid, **args)
        self._tone = asyncio.create_task(self._send_tone(min(TONE_LONGEST, float(seconds))))
        self._touch()

    async def _stop_tone(self) -> None:
        tone, self._tone = self._tone, None
        if tone is not None and not tone.done():
            tone.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tone
        self._touch()

    async def _send_tone(self, seconds: float) -> None:
        rate = self.station.transport.sample_rate
        piece = int(rate * PIECE)
        step = 2 * np.pi * self.tx_freq / rate
        meters = self.station.rig.state.meters
        alc = watts = swr = 0.0
        began = time.time()
        try:
            self.transmitting = True
            await self.station.rig.ptt(True, longest=seconds + 2)
            for meter in ("alc", "po"):
                meters.pop(meter, None)
            self._touch()
            phase, index = 0.0, 0
            while time.time() - began < seconds:
                await asyncio.sleep(max(0.0, began + index * PIECE - LEAD - time.time()))
                angles = phase + step * np.arange(piece)
                phase = float(angles[-1] + step) % (2 * np.pi)
                sound = np.sin(angles) * self.tx_level
                self.station.transport.send_audio(
                    (np.clip(sound, -1, 1) * 32767).astype("<i2").tobytes())
                if index % 25 == 0:
                    alc = max(alc, float(meters.get("alc") or 0))
                    watts = max(watts, float(meters.get("po") or 0))
                    swr = max(swr, float(meters.get("swr") or 0))
                index += 1
            await asyncio.sleep(TAIL)
        except (RigError, TransportError) as exc:
            self.hub.error(N_("FT8 transmission failed: {error}"), error=str(exc))
        finally:
            if self.transmitting:
                await self._unkey()
            alc = max(alc, float(meters.get("alc") or 0))
            watts = max(watts, float(meters.get("po") or 0))
            swr = max(swr, float(meters.get("swr") or 0))
            if time.time() - began > 1:
                self._sent(began, N_("tone"), alc, watts, swr)
            self._touch()

    def _sent(self, start: float, message: str, alc: float, watts: float,
              swr: float = 0.0) -> None:
        if self.tx_on:                            # a QSO's message (not the tone)
            self.qso_watts = max(self.qso_watts, round(watts, 1))
        self.last_sent = {"start": start, "message": message, "alc": round(alc, 2),
                          "watts": round(watts, 1), "swr": round(swr, 2),
                          "level": self.tx_level}
        self.hub.emit({"type": "ft8.sent", **self.last_sent})
        if alc > ALC_HIGH:
            self.hub.info(N_("FT8: the ALC went past its zone ({alc}) while sending: lower the "
                             "TX level (now {level} %) until it stays within it"),
                          alc=f"{alc:.2f}", level=round(self.tx_level * 100))
            return
        power = self.station.rig.state.power
        wanted = power * self.station.model.max_power_w / 100 if power else 0
        if alc < ALC_IDLE and watts and wanted and watts < SHORT_OF_POWER * wanted \
                and self._short_said != self.tx_level:
            self._short_said = self.tx_level     # once for each level
            self.hub.info(N_("FT8: {watts} W of the {wanted} W set, with the ALC still: the TX "
                             "level can go up (now {level} %) while the ALC stays within its "
                             "zone"), watts=f"{watts:.1f}", wanted=f"{wanted:g}",
                          level=round(self.tx_level * 100))
        elif alc >= ALC_IDLE and watts and wanted and watts < CUT_POWER * wanted:
            # Audio enough (the ALC acts) and still little power: the radio cuts it, as it
            # does with a high SWR. Said once, until the power comes back.
            if not self._cut_said:
                self._cut_said = True
                self.hub.info(N_("FT8: only {watts} W of the {wanted} W set, with the ALC "
                                 "acting: the radio is cutting its power, most often for a "
                                 "high SWR (check the antenna: SWR {swr})"),
                              watts=f"{watts:.1f}", wanted=f"{wanted:g}",
                              swr=self.last_sent.get("swr") or "?")
        elif watts and wanted and watts >= SHORT_OF_POWER * wanted:
            self._cut_said = False

    async def _read_power(self) -> None:
        """The power set, read again before each message: it may have been changed on the
        radio itself (read only when connecting, it said 100 % while 0.5 W were set)."""
        rig = self.station.rig
        try:
            percent = await rig.read_power()
        except (RigError, TransportError):
            return
        wanted = percent * self.station.model.max_power_w / 100
        if wanted < LOW_SETTING_W:
            if self._setting_said != percent:
                self._setting_said = percent
                self.hub.info(N_("FT8: the radio is set to {wanted} W ({percent} %): , . or the "
                                 "radio raise it"), wanted=f"{wanted:.1f}", percent=percent)
        else:
            self._setting_said = None

    async def _unkey(self) -> None:
        with contextlib.suppress(RigError, TransportError):
            await self.station.rig.unkey()
        self.transmitting = False
        self._touch()

    async def _cannot_key(self) -> tuple[str, dict] | None:
        if not self.config.tx_enabled:
            return N_("FT8 cannot transmit: transmission is off ([rig] tx_enabled, --tx)"), {}
        if not self.station.connected:
            return N_("FT8 cannot transmit: the radio is not connected"), {}
        if not self.station.transport.can_send_audio:
            return N_("FT8 cannot transmit: this connection cannot send the radio audio"), {}
        if self.station.keyed_by == "voice":
            return N_("FT8 cannot transmit: a microphone is talking"), {}
        rig = self.station.rig
        try:
            data_mode, _filter = await rig.read_data_mode()
            if not data_mode:
                return N_("FT8 cannot transmit: the radio is not in a data mode (USB-D); "
                          "←→ tunes it for FT8"), {}
            if not self._mod_set:
                await rig.transact(DATA1_MOD + bytes([MOD_WLAN]))
                self._mod_set = True
        except (RigError, TransportError) as exc:
            return N_("FT8 cannot transmit: the radio does not answer ({error})"), \
                {"error": str(exc)}
        return None

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
        qso = self.qso
        return {"on": self.on, "jt9": self.program or "", "error": self.error,
                "dial": dial, "band": band.name if band else "",
                "ft8_dial": bool(dial and dial in DIALS.values()),
                "cycle": self._start, "decoding": self.decoding, "last": self.last,
                "depths": list(self.depths()),
                "tx": {"on": self.tx_on, "transmitting": self.transmitting,
                       "message": qso.message if self.tx_on else "", "step": qso.step,
                       "them": qso.them, "freq": self.tx_freq, "parity": self.tx_parity,
                       "tries": qso.tries, "mycall": qso.mycall, "grid": qso.mygrid,
                       "rx": self.their_freq if self.tx_on else 0,
                       "level": self.tx_level, "last": self.last_sent,
                       "tone": self.toning},
                "auto": self.auto_state(), "ignore": self.ignore_state(),
                "dupes": self.allow_dupes, "unattended": self.unattended}

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
            elif method == "cq":
                return {"message": await self.call_cq(params.get("freq"))}
            elif method == "reply":
                return {"message": await self.reply(dict(params["decode"]),
                                                    float(params["start"]))}
            elif method == "halt":
                await self.halt()
            elif method == "ignore":
                return self.set_ignore(str(params.get("call") or ""),
                                       str(params.get("country") or ""),
                                       bool(params.get("on", True)))
            elif method == "dupes":
                return self.set_dupes(bool(params.get("on", not self.allow_dupes)))
            elif method == "auto":
                return await self.set_auto(bool(params.get("on", not self.auto.on)))
            elif method == "tone":
                await self.tone(bool(params.get("on", True)),
                                float(params.get("seconds", TONE_SECONDS)))
            elif method == "log":
                try:
                    text = self.log_path.read_text(encoding="utf-8")
                except OSError:
                    text = ""
                return {"qsos": self.logged, "path": str(self.log_path), "adif": text}
            elif method == "txfreq":
                self.tx_freq = max(200, min(3000, int(params["freq"])))
                self._save_settings()
                self._touch()
                return {"freq": self.tx_freq}
            elif method == "worked":
                mine = geo.locator_to_latlon(self.config.locator)
                return {**(self.worked.summary() if self.worked is not None else
                           {"grids": {}, "entities": {}, "qsos": 0}),
                        "locator": self.config.locator.upper(),
                        "lat": mine[0] if mine else None, "lon": mine[1] if mine else None,
                        "logbook": self.worked is not None}
            elif method == "waterfall":
                self._wf_until = time.monotonic() + min(60.0, float(params.get("seconds", 15)))
                return {"low": waterfall.LOW, "high": waterfall.HIGH, "step": waterfall.STEP}
            elif method == "unattended":
                self.unattended = max(0, min(24 * 60, int(params["minutes"])))
                self._touch()
                return {"minutes": self.unattended}
            elif method == "level":
                self.tx_level = clamp_level(params["level"])
                self._save_settings()
                self._touch()
                return {"level": self.tx_level, "percent": round(self.tx_level * 100)}
            else:
                return {"error": f"unknown method ft8.{method}"}
        except Ft8Error as exc:
            return {"error": str(exc), "msgid": exc.msgid, "args": {
                k: str(v) for k, v in exc.args_.items()}}
        except (KeyError, ValueError) as exc:
            return {"error": f"bad request: {exc}"}
        return {"ok": True}
