"""Antenna tuning: SWR and noise sweeps on the shared station (formerly swr-remote).

* SWR sweep (transmits): RTTY carrier at low power, about 0.4 s per point, PTT watchdog,
  only inside one amateur band; restores frequency, mode, data mode and power afterwards.
  Needs transmission enabled ([rig] tx_enabled or --tx) *and* the remote control switched
  on by hand in the antenna application (``set_remote``): it is off whenever the suite
  starts, and only the antenna application turns it on, never the phone page. While it is
  off the phone page only watches (and can always stop a sweep).
* Noise sweep (receive only): the S-meter noise peak marks the resonance.

Results say which way to move the coil and, with the per-band history kept in the suite
database (table ``swr_history``), roughly how much.

Several antennas can be kept (table ``swr_antennas``): each has its own history and saved
settings (where its coil or adjustment ended on each band) and its own way of responding to
the adjustment. The sweeps, the advice and the phone page work on the active one.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa

from ic705suite import db, paths
from ic705suite.apps.swr.analysis import COIL_SIGN, DEFAULT_KHZ_PER_UNIT, analyze
from ic705suite.config import Config
from ic705suite.i18n import N_
from ic705suite.rig.bands import band_for, find_band, format_frequency, parse_frequency
from ic705suite.rig.base import FEATURE_SWR_METER, RigError
from ic705suite.rig.transport import TransportError
from ic705suite.station.station import Station

log = logging.getLogger(__name__)

MAX_POINTS = 61
MIN_STEP = 1_000
NOISE_OFFSETS = (-1_500, 0, 1_500)
NO_POWER_W = 0.3
# Above this raw SWR reading the meter is saturated (about 3:1 on the IC-705).
SWR_SATURATED = 3.0

# Bands the antenna is tuned on, with an FT8 target and the default SWR sweep step.
TUNE_BANDS = [("40m", 7_074_000, 10_000), ("30m", 10_136_000, 5_000), ("20m", 14_074_000, 25_000),
              ("17m", 18_100_000, 10_000), ("15m", 21_074_000, 25_000), ("12m", 24_915_000, 10_000),
              ("10m", 28_074_000, 50_000), ("6m", 50_313_000, 50_000)]
TEN_METRE_SWEEP_END = 29_000_000  # the whole 10 m band is too wide for one useful sweep

# Photos of the antenna kept with the sweeps (the phone's camera, a file): their formats by
# their first bytes, and the largest accepted.
PHOTO_TYPES = {b"\xff\xd8\xff": ("jpg", "image/jpeg"), b"\x89PNG": ("png", "image/png"),
               b"RIFF": ("webp", "image/webp"), b"GIF8": ("gif", "image/gif")}
MAX_PHOTO = 16 * 1024 * 1024
PHOTO_CHUNK = 32 * 1024          # per call over the link (its messages are at most 64 kB)


def photo_type(data: bytes) -> tuple[str, str] | None:
    """(extension, media type) of an image, by its first bytes; None if it is not one."""
    for magic, kind in PHOTO_TYPES.items():
        if data.startswith(magic) and (magic != b"RIFF" or data[8:12] == b"WEBP"):
            return kind
    return None


def summary(entry: dict) -> dict:
    """An entry of the history without its points and analysis: for the lists."""
    return {k: v for k, v in entry.items() if k not in ("points", "analysis")}

# History of the standalone swr-remote, imported once into the database.
LEGACY_HISTORY = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") \
    / "swr-remote" / "history.json"

# The antenna swr-remote was written for: its history becomes this antenna's.
# (Its notes are the operator's to write, in their language.)
LEGACY_ANTENNA = {"name": "Komunica HF-PRO-2-PLUS-T", "coil_sign": COIL_SIGN, "notes": "",
                  "slopes": DEFAULT_KHZ_PER_UNIT}

metadata = sa.MetaData()
history_table = sa.Table(
    "swr_history", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("ts", sa.String(32), nullable=False),
    sa.Column("band", sa.String(10), nullable=False, index=True),
    sa.Column("kind", sa.String(10), nullable=False),
    sa.Column("data", sa.Text, nullable=False),   # the whole entry, JSON
    sa.Column("antenna_id", sa.Integer, index=True),
)
antennas_table = sa.Table(
    "swr_antennas", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(80), nullable=False, unique=True),
    sa.Column("notes", sa.Text, nullable=False, default=""),
    # -1: a higher number lowers the frequency; +1 raises it; 0 not known (learnt)
    sa.Column("coil_sign", sa.Integer, nullable=False, default=0),
    sa.Column("slopes", sa.Text, nullable=False, default="{}"),   # JSON {band: kHz per unit}
    sa.Column("active", sa.Boolean, nullable=False, default=False),
)


# The operator's own targets (a name, a band, a frequency), besides the bands' default ones:
# "SSB at home" 7.150 on 40 m. Each keeps its own saved position of the adjustment.
presets_table = sa.Table(
    "swr_presets", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(80), nullable=False, unique=True),
    sa.Column("band", sa.String(10), nullable=False),
    sa.Column("target", sa.Integer, nullable=False),
)


class SweepError(Exception):
    pass


def setting_key(band: str, preset: int | None = None) -> str:
    """Where a saved position belongs: a band, or a preset ("preset:3")."""
    return f"preset:{preset}" if preset else band


@dataclass
class Sweep:
    kind: str = ""             # "swr" or "noise"
    running: bool = False
    band: str = ""
    target: int = 0
    preset: int | None = None  # the preset swept, if one was
    entry_id: int | None = None  # its entry in the history, once finished
    total: int = 0
    points: list[dict] = field(default_factory=list)
    analysis: dict | None = None
    warning: str = ""
    error: str = ""
    finished_at: float = 0.0


class HistoryStore:
    """Antennas and their sweeps. Every reading and writing method works on one antenna:
    the active one unless another is named."""

    def __init__(self, url: str | None, legacy: Path | None = LEGACY_HISTORY) -> None:
        self.engine = db.make_engine(url)
        metadata.create_all(self.engine)
        self._upgrade()
        if legacy is not None and legacy.exists():
            self._import(legacy)
        self._adopt_orphans()

    def _upgrade(self) -> None:
        """Databases from before the antennas: add the history's antenna column."""
        columns = {c["name"] for c in sa.inspect(self.engine).get_columns("swr_history")}
        if "antenna_id" not in columns:
            with self.engine.begin() as conn:
                conn.execute(sa.text("ALTER TABLE swr_history ADD COLUMN antenna_id INTEGER"))

    def _adopt_orphans(self) -> None:
        """Sweeps without an antenna (from before there were several) go to the antenna they
        were made with; and there is always one antenna, the active one."""
        with self.engine.begin() as conn:
            orphans = conn.execute(sa.select(sa.func.count()).select_from(history_table)
                                   .where(history_table.c.antenna_id.is_(None))).scalar()
            count = conn.execute(sa.select(sa.func.count()).select_from(antennas_table)).scalar()
            if orphans:
                existing = conn.execute(sa.select(antennas_table.c.id).where(
                    antennas_table.c.name == LEGACY_ANTENNA["name"])).scalar()
                antenna_id = existing or self._create(conn, active=not count, **LEGACY_ANTENNA)
                conn.execute(history_table.update().where(history_table.c.antenna_id.is_(None))
                             .values(antenna_id=antenna_id))
                log.info("%d antenna sweeps assigned to %s", orphans, LEGACY_ANTENNA["name"])
            elif not count:
                self._create(conn, name="Antenna 1", notes="", coil_sign=0, slopes={},
                             active=True)
            if not conn.execute(sa.select(antennas_table.c.id)
                                .where(antennas_table.c.active)).first():
                first = conn.execute(sa.select(sa.func.min(antennas_table.c.id))).scalar()
                conn.execute(antennas_table.update().where(antennas_table.c.id == first)
                             .values(active=True))

    @staticmethod
    def _create(conn, name: str, notes: str, coil_sign: int, slopes: dict,
                active: bool = False) -> int:
        result = conn.execute(antennas_table.insert().values(
            name=name, notes=notes, coil_sign=coil_sign, slopes=json.dumps(slopes),
            active=active))
        return result.inserted_primary_key[0]

    # ---- antennas -------------------------------------------------------------------------

    @staticmethod
    def _antenna(row) -> dict:
        return {"id": row.id, "name": row.name, "notes": row.notes,
                "coil_sign": row.coil_sign, "slopes": json.loads(row.slopes or "{}"),
                "active": bool(row.active)}

    def antennas(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(antennas_table).order_by(antennas_table.c.name))
            return [self._antenna(r) for r in rows]

    def antenna(self, antenna_id: int | None = None) -> dict:
        """The antenna `antenna_id`, or the active one."""
        query = sa.select(antennas_table)
        query = query.where(antennas_table.c.id == antenna_id) if antenna_id is not None \
            else query.where(antennas_table.c.active)
        with self.engine.connect() as conn:
            row = conn.execute(query).first()
        if row is None:
            raise ValueError(f"no antenna {antenna_id}")
        return self._antenna(row)

    def save_antenna(self, name: str, notes: str = "", coil_sign: int = 0,
                     antenna_id: int | None = None) -> dict:
        """A new antenna (the active one from now on), or changes to one."""
        name = name.strip()
        if not name:
            raise ValueError("the antenna needs a name")
        if coil_sign not in (-1, 0, 1):
            raise ValueError("coil_sign must be -1, 0 or 1")
        with self.engine.begin() as conn:
            clash = conn.execute(sa.select(antennas_table.c.id).where(
                antennas_table.c.name == name)).scalar()
            if clash is not None and clash != antenna_id:
                raise ValueError(f"there is already an antenna called {name}")
            if antenna_id is None:
                conn.execute(antennas_table.update().values(active=False))
                antenna_id = self._create(conn, name, notes.strip(), coil_sign, {}, active=True)
            else:
                conn.execute(antennas_table.update().where(antennas_table.c.id == antenna_id)
                             .values(name=name, notes=notes.strip(), coil_sign=coil_sign))
        return self.antenna(antenna_id)

    def select_antenna(self, antenna_id: int) -> dict:
        antenna = self.antenna(antenna_id)
        with self.engine.begin() as conn:
            conn.execute(antennas_table.update().values(
                active=antennas_table.c.id == antenna_id))
        return {**antenna, "active": True}

    def delete_antenna(self, antenna_id: int) -> None:
        """An antenna and its history; the last antenna cannot go."""
        antenna = self.antenna(antenna_id)
        with self.engine.begin() as conn:
            if conn.execute(sa.select(sa.func.count()).select_from(antennas_table)).scalar() <= 1:
                raise ValueError("the last antenna cannot be deleted")
            conn.execute(history_table.delete().where(history_table.c.antenna_id == antenna_id))
            conn.execute(antennas_table.delete().where(antennas_table.c.id == antenna_id))
            if antenna["active"]:
                first = conn.execute(sa.select(sa.func.min(antennas_table.c.id))).scalar()
                conn.execute(antennas_table.update().where(antennas_table.c.id == first)
                             .values(active=True))

    def settings(self, antenna_id: int | None = None) -> dict[str, dict]:
        """Where the antenna was left on each band and preset: the latest saved position of
        each, by setting_key()."""
        out: dict[str, dict] = {}
        for entry in self.entries(antenna_id=antenna_id):
            # A saved position, or a sweep given the position it was made at.
            if entry.get("kind") == "final" or entry.get("coil") is not None:
                out[setting_key(entry["band"], entry.get("preset"))] = entry
        return out

    # ---- presets ----

    def presets(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(presets_table).order_by(presets_table.c.id))
            return [{"id": r.id, "name": r.name, "band": r.band, "target": r.target}
                    for r in rows]

    def save_preset(self, name: str, band: str, target: int,
                    preset_id: int | None = None) -> dict:
        name = name.strip()
        if not name:
            raise SweepError("the preset needs a name")
        with self.engine.begin() as conn:
            clash = conn.execute(sa.select(presets_table.c.id)
                                 .where(presets_table.c.name == name)).scalar()
            if clash is not None and clash != preset_id:
                raise SweepError(f"there is already a preset called {name}")
            values = {"name": name, "band": band, "target": int(target)}
            if preset_id is None:
                preset_id = conn.execute(presets_table.insert().values(**values)) \
                    .inserted_primary_key[0]
            else:
                conn.execute(presets_table.update().where(presets_table.c.id == preset_id)
                             .values(**values))
        return {"id": preset_id, **values}

    def delete_preset(self, preset_id: int) -> None:
        """The preset goes; its sweeps stay in its band's history."""
        with self.engine.begin() as conn:
            conn.execute(presets_table.delete().where(presets_table.c.id == preset_id))

    def _import(self, path: Path) -> None:
        with self.engine.begin() as conn:
            if conn.execute(sa.select(sa.func.count()).select_from(history_table)).scalar():
                return
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            for entry in entries:
                self._insert(conn, entry, None)
        log.info("imported %d antenna sweeps from %s", len(entries), path)

    @staticmethod
    def _insert(conn, entry: dict, antenna_id: int | None) -> int:
        return conn.execute(history_table.insert().values(
            ts=entry.get("ts", ""), band=entry.get("band", ""), kind=entry.get("kind", ""),
            data=json.dumps(entry), antenna_id=antenna_id)).inserted_primary_key[0]

    def add(self, entry: dict, antenna_id: int | None = None) -> int:
        antenna_id = antenna_id if antenna_id is not None else self.antenna()["id"]
        with self.engine.begin() as conn:
            return self._insert(conn, entry, antenna_id)

    def entries(self, band: str = "", limit: int | None = None,
                antenna_id: int | None = None) -> list[dict]:
        antenna_id = antenna_id if antenna_id is not None else self.antenna()["id"]
        query = sa.select(history_table.c.id, history_table.c.data) \
            .order_by(history_table.c.id).where(history_table.c.antenna_id == antenna_id)
        if band:
            query = query.where(history_table.c.band == band)
        with self.engine.connect() as conn:
            rows = [{**json.loads(r.data), "id": r.id} for r in conn.execute(query)]
        return rows[-limit:] if limit else rows

    # ---- one entry of the history: its details, position, note, photo ----

    def entry(self, entry_id: int) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(history_table.c.id, history_table.c.data)
                               .where(history_table.c.id == entry_id)).first()
        if row is None:
            raise SweepError("that entry of the history no longer exists")
        return {**json.loads(row.data), "id": row.id}

    def update_entry(self, entry_id: int, **changes) -> dict:
        """Change fields of an entry (coil, note, photo); None removes one."""
        entry = self.entry(entry_id)
        for key, value in changes.items():
            if value is None or value == "":
                entry.pop(key, None)
            else:
                entry[key] = value
        data = {k: v for k, v in entry.items() if k != "id"}
        with self.engine.begin() as conn:
            conn.execute(history_table.update().where(history_table.c.id == entry_id)
                         .values(data=json.dumps(data)))
        return entry

    def delete_entry(self, entry_id: int) -> dict:
        entry = self.entry(entry_id)
        with self.engine.begin() as conn:
            conn.execute(history_table.delete().where(history_table.c.id == entry_id))
        return entry

    def close(self) -> None:
        self.engine.dispose()


class SwrService:
    def __init__(self, station: Station, config: Config, ui) -> None:
        self.station = station
        self.config = config
        self.ui = ui
        self.max_power = max(1, min(100, config.swr_max_power))
        self.port = config.swr_port if config.swr_web else None
        self.sweep = Sweep()
        self._task: asyncio.Task | None = None
        self._history: HistoryStore | None = None
        self.photos_dir = paths.data_dir() / "swr" / "photos"
        self._uploads: dict[int, bytearray] = {}      # photos arriving in pieces
        self._web = None
        self._version = 0
        self._sent = -1
        self.remote = False        # off at every start: switched on by hand (antenna app)

    @property
    def allow_tx(self) -> bool:
        return self.config.tx_enabled

    @property
    def history(self) -> HistoryStore:
        if self._history is None:
            self._history = HistoryStore(self.config.database_url or None, LEGACY_HISTORY)
        return self._history

    # ---- lifecycle ----------------------------------------------------------------------

    async def start(self) -> None:
        if self.port is None or not self.station.model.has(FEATURE_SWR_METER):
            return
        from ic705suite.apps.swr.web import serve

        try:
            # Reachable from the phone on the local network by default, as swr-remote was.
            self._web = await serve(self, self.config.swr_host, self.port)
        except OSError as exc:
            self.ui.error(N_("antenna page not available on port {port}: {error}"),
                          port=self.port, error=str(exc))
            self.port = None

    async def stop(self) -> None:
        task = self._task
        self.stop_sweep()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, 15)  # lets the sweep unkey and restore the radio
        if self._web is not None:
            self._web.close()
            with contextlib.suppress(Exception):
                await self._web.wait_closed()
        if self._history is not None:
            self._history.close()

    def changed(self) -> bool:
        """True once per change, or continuously while a sweep runs (the "swr" event)."""
        if self.sweep.running or self._version != self._sent:
            self._sent = self._version
            return True
        return False

    # ---- sweeps --------------------------------------------------------------------------

    def start_sweep(self, req: dict) -> None:
        if self._task and not self._task.done():
            raise SweepError("a sweep is already running")
        if not self.station.connected:
            raise SweepError("the radio is not connected")
        kind = req.get("kind")
        if kind not in ("swr", "noise"):
            raise SweepError("kind must be 'swr' or 'noise'")
        start, stop = int(req["start"]), int(req["stop"])
        step = max(MIN_STEP, int(req.get("step") or MIN_STEP))
        if stop < start:
            start, stop = stop, start
        freqs = list(range(start, stop + 1, step))
        if freqs[-1] != stop:
            freqs.append(stop)
        if len(freqs) > MAX_POINTS:
            raise SweepError(f"too many points ({len(freqs)}); use a larger step (max {MAX_POINTS})")
        band = band_for(start)
        if kind == "swr":
            if not self.allow_tx:
                raise SweepError("transmission disabled: SWR sweeps need --tx "
                                 "(or [rig] tx_enabled); the noise sweep never transmits")
            if not self.remote:
                raise SweepError("remote control is off: switch it on in the antenna "
                                 "application (R) before an SWR sweep")
            if band is None or band_for(stop) is not band:
                raise SweepError("SWR sweep must stay inside one amateur band")
        target = int(req.get("target") or (start + stop) // 2)
        power = max(1, min(self.max_power, int(req.get("power") or self.max_power)))
        preset = int(req["preset"]) if req.get("preset") else None
        self.sweep = Sweep(kind=kind, running=True, band=req.get("band") or (band.name if band else ""),
                           target=target, preset=preset, total=len(freqs))
        self._version += 1
        self._task = asyncio.create_task(self._run_sweep(freqs, power))

    def set_remote(self, on: bool) -> None:
        """The antenna application's remote control switch (never the phone page's)."""
        if self.remote != bool(on):
            self.remote = bool(on)
            log.info("antenna remote control %s", "on" if on else "off")
            self.ui.info(N_("antenna: remote control on") if on
                         else N_("antenna: remote control off"))
            self._version += 1

    def require_remote(self) -> None:
        """The phone page may act only while the remote control is on."""
        if not self.remote:
            raise SweepError("remote control is off: switch it on in the antenna "
                             "application of the suite (R)")

    def stop_sweep(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run_sweep(self, freqs: list[int], power: int) -> None:
        sweep, rig = self.sweep, self.station.rig
        saved = None
        # The sweep owns the radio: no polling, and the CW screens see no dial changes.
        self.station.paused.add("swr")
        self.ui.info(N_("antenna: SWR sweep {start}-{end} MHz started") if sweep.kind == "swr"
                     else N_("antenna: noise sweep {start}-{end} MHz started"),
                     start=format_frequency(freqs[0]), end=format_frequency(freqs[-1]))
        try:
            saved = await rig.save_settings()
            if sweep.kind == "swr":
                if hasattr(rig, "set_data_mode"):
                    await rig.set_data_mode(0)
                await rig.set_mode("RTTY")
                await rig.set_power(power)
                await asyncio.sleep(0.2)
            no_power = 0
            for hz in freqs:
                if sweep.kind == "swr":
                    point = await self._measure_swr(hz)
                    if point["watts"] < NO_POWER_W:
                        no_power += 1
                else:
                    point = await self._measure_noise(hz)
                sweep.points.append(point)
            if sweep.kind == "swr" and no_power > len(freqs) // 2:
                sweep.warning = "no_power"
            self._finish(sweep)
        except asyncio.CancelledError:
            sweep.error = sweep.error or "stopped"
        except (SweepError, RigError, TransportError) as exc:
            sweep.error = str(exc)
            log.error("sweep failed: %s", exc)
        finally:
            if getattr(rig, "keyed_at", None) is not None:
                await rig.unkey()
            if saved is not None and self.station.connected:
                await rig.restore_settings(saved)
            self.station.paused.discard("swr")
            sweep.running = False
            sweep.finished_at = time.time()
            self._record(sweep)
            self._version += 1
            if sweep.analysis and sweep.analysis["confident"] and not sweep.error:
                self.ui.info(N_("antenna: resonance at {freq} MHz"),
                             freq=format_frequency(sweep.analysis["resonance"]))

    async def _measure_swr(self, hz: int) -> dict:
        rig = self.station.rig
        await rig.set_frequency(hz)
        await asyncio.sleep(0.08)
        await rig.ptt(True)
        try:
            # The SWR meter jumps from one read to the next: take the median of four.
            await asyncio.sleep(0.2)
            readings = []
            for _ in range(4):
                readings.append(await rig.read_swr())
                await asyncio.sleep(0.08)
            watts = await rig.read_rf_watts()
        finally:
            await rig.unkey()
        await asyncio.sleep(0.15)
        readings.sort()
        value = round((readings[1] + readings[2]) / 2, 2)
        return {"f": hz, "v": value, "sat": value >= SWR_SATURATED, "watts": watts}

    async def _measure_noise(self, hz: int) -> dict:
        """Noise floor near hz: the quietest of three nearby spots, so a carrier on one of
        them (broadcast, CW, digital) does not look like an antenna resonance."""
        rig = self.station.rig
        floors = []
        for offset in NOISE_OFFSETS:
            await rig.set_frequency(hz + offset)
            await asyncio.sleep(0.25)
            readings = []
            for _ in range(3):
                readings.append(await rig.read_smeter())
                await asyncio.sleep(0.05)
            floors.append(sorted(readings)[1])
        await rig.set_frequency(hz)
        return {"f": hz, "v": float(min(floors))}

    def _finish(self, sweep: Sweep) -> None:
        freqs = [p["f"] for p in sweep.points]
        values = [p["v"] for p in sweep.points]
        antenna = self.history.antenna()
        result = analyze(sweep.kind, freqs, values, sweep.target, sweep.band,
                         self.history.entries(sweep.band), antenna["coil_sign"],
                         antenna["slopes"])
        if result is None:
            return
        sweep.analysis = result.to_dict()

    def _record(self, sweep: Sweep) -> None:
        """Every finished sweep goes into the history whole (points, analysis, what went
        wrong), so it can be looked at again; a photo and the position may be added later.
        The advice only learns from the saved positions, so a doubtful sweep does no harm."""
        if len(sweep.points) < 2:
            return
        a = sweep.analysis or {}
        entry = {"ts": _now(), "band": sweep.band, "kind": sweep.kind, "target": sweep.target,
                 "points": sweep.points, "analysis": sweep.analysis,
                 "confident": bool(a.get("confident"))}
        if a.get("confident"):
            entry.update(resonance=a["resonance"], best=a["best_value"], at_edge=a["at_edge"])
        if sweep.preset:
            entry["preset"] = sweep.preset
        if sweep.warning:
            entry["warning"] = sweep.warning
        if sweep.error:
            entry["error"] = sweep.error
        try:
            sweep.entry_id = self.history.add(entry)
        except Exception:  # noqa: BLE001 - the sweep itself is not lost for that
            log.exception("cannot record the sweep")

    def save_final(self, coil_text: str, band: str = "", note: str = "",
                   preset: int | None = None) -> dict:
        """Record where the coil (or adjustment) ended up on a band, or for a preset, for the
        active antenna: with the last SWR sweep's results when that sweep was for the same
        band and preset, else alone."""
        coil = float(str(coil_text).strip().replace(",", "."))
        sweep = self.sweep
        if sweep.running:
            raise SweepError("wait for the sweep to finish")
        band = band or sweep.band
        if not band:
            raise SweepError("which band?")
        entry = {"ts": _now(), "band": band, "kind": "final", "coil": coil}
        if preset:
            entry["preset"] = int(preset)
        if note.strip():
            entry["note"] = note.strip()
        a = sweep.analysis
        if a and sweep.kind == "swr" and sweep.band == band and sweep.preset == (
                int(preset) if preset else None) and not sweep.error:
            entry.update({"target": sweep.target, "resonance": a["resonance"],
                          "best": a["best_value"], "at_edge": a["at_edge"], "tuned": a["tuned"],
                          "swr_at_target": a.get("swr_at_target"),
                          "points": sweep.points, "analysis": a})
            if sweep.entry_id is not None:
                # The sweep in the history gets the position it was made at, as well.
                with contextlib.suppress(SweepError):
                    self.history.update_entry(sweep.entry_id, coil=coil)
        entry["id"] = self.history.add(entry)
        self._version += 1
        return entry

    # ---- API ------------------------------------------------------------------------------

    def state(self) -> dict:
        st, s = self.station.state, self.sweep
        if self.station.connected:
            status = f"connected to {self.station.rig.name}"
        else:
            status = "radio not connected"
        antenna = self.history.antenna()
        return {"connected": self.station.connected, "status": status,
                "antenna": {"id": antenna["id"], "name": antenna["name"]},
                "allow_tx": self.allow_tx, "remote": self.remote, "max_power": self.max_power, "port": self.port,
                "frequency": st.frequency, "mode": st.mode,
                "sweep": {"kind": s.kind, "running": s.running, "band": s.band, "target": s.target,
                          "preset": s.preset, "entry_id": s.entry_id,
                          "total": s.total, "points": s.points,
                          "analysis": s.analysis, "warning": s.warning, "error": s.error,
                          "finished_at": s.finished_at}}

    # ---- photos ----

    def set_photo(self, entry_id: int, data: bytes) -> dict:
        """Keep `data` (an image) as the entry's photo, replacing the one it had."""
        if len(data) > MAX_PHOTO:
            raise SweepError("the photo is too large (16 MB at most)")
        kind = photo_type(data)
        if kind is None:
            raise SweepError("that is not a photo (JPEG, PNG, WebP or GIF)")
        entry = self.history.entry(entry_id)
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        name = f"{entry_id}-{int(time.time())}.{kind[0]}"
        (self.photos_dir / name).write_bytes(data)
        self._remove_photo(entry.get("photo"))
        self._version += 1
        return self.history.update_entry(entry_id, photo=name)

    def photo(self, entry_id: int) -> tuple[bytes, str] | None:
        """(the image, its media type), or None when the entry has none."""
        name = self.history.entry(entry_id).get("photo")
        path = self.photos_dir / name if name else None
        if path is None or not path.is_file():
            return None
        data = path.read_bytes()
        return data, (photo_type(data) or ("", "application/octet-stream"))[1]

    def delete_photo(self, entry_id: int) -> dict:
        entry = self.history.entry(entry_id)
        self._remove_photo(entry.get("photo"))
        self._version += 1
        return self.history.update_entry(entry_id, photo=None)

    def _remove_photo(self, name: str | None) -> None:
        if name and "/" not in name and "\\" not in name:
            with contextlib.suppress(OSError):
                (self.photos_dir / name).unlink()

    def update_entry(self, entry_id: int, params: dict) -> dict:
        """The position (coil) and the note of an entry; an empty one removes it."""
        changes: dict = {}
        if "coil" in params:
            text = str(params["coil"] if params["coil"] is not None else "").strip()
            changes["coil"] = float(text.replace(",", ".")) if text else None
        if "note" in params:
            changes["note"] = str(params["note"] or "").strip() or None
        self._version += 1
        return self.history.update_entry(entry_id, **changes)

    def delete_entry(self, entry_id: int) -> None:
        entry = self.history.delete_entry(entry_id)
        self._remove_photo(entry.get("photo"))
        self._version += 1

    def antennas(self) -> dict:
        """Every antenna with its saved settings (band -> latest saved position)."""
        return {"antennas": [{**a, "settings": self.history.settings(a["id"])}
                             for a in self.history.antennas()]}

    def _antenna_changed(self) -> None:
        if not self.sweep.running:
            self.sweep = Sweep()           # the last sweep was another antenna's
        self._version += 1

    @staticmethod
    def bands() -> list[dict]:
        out = []
        for name, target, step in TUNE_BANDS:
            band = find_band(name)
            end = min(band.end, TEN_METRE_SWEEP_END) if name == "10m" else band.end
            out.append({"name": name, "band": name, "start": band.start, "end": end,
                        "target": target, "step": step})
        return out

    def presets(self) -> list[dict]:
        """The presets as rows like the bands': their band's edges and step, their own name
        and target, and "preset" (their id)."""
        by_name = {b["name"]: b for b in self.bands()}
        return [{**by_name[p["band"]], "name": p["name"], "target": p["target"],
                 "preset": p["id"]}
                for p in self.history.presets() if p["band"] in by_name]

    def save_preset(self, params: dict) -> dict:
        band = next((b for b in self.bands() if b["name"] == params.get("band")), None)
        if band is None:
            raise SweepError("choose one of the antenna's bands")
        target = params.get("target")
        if isinstance(target, str):
            target = parse_frequency(target)
        whole = find_band(band["name"])
        if not target or not whole.start <= int(target) <= whole.end:
            raise SweepError(f"the target must be inside {band['name']} "
                             f"({whole.start / 1e6:.3f}-{whole.end / 1e6:.3f} MHz)")
        preset_id = params.get("id")
        self.history.save_preset(str(params.get("name", "")), band["name"], int(target),
                                 int(preset_id) if preset_id is not None else None)
        self._version += 1
        return {"presets": self.presets()}

    async def call(self, method: str, params: dict) -> dict:
        """The "swr.*" calls of the core protocol (the TUI uses them; the web page uses HTTP)."""
        try:
            if method == "state":
                return self.state()
            if method == "bands":
                return {"bands": self.bands()}
            if method == "history":
                entries = self.history.entries(params.get("band", ""),
                                               limit=params.get("limit"))
                return {"history": [summary(e) for e in reversed(entries)]}
            if method == "entry":
                return {"entry": self.history.entry(int(params["id"]))}
            if method == "entry.update":
                return {"entry": self.update_entry(int(params["id"]), params)}
            if method == "entry.delete":
                self.delete_entry(int(params["id"]))
                return {"deleted": int(params["id"])}
            if method == "photo.put":
                # In pieces: base64 "data" at "offset"; "done" with the last one.
                import base64

                entry_id = int(params["id"])
                buffer = self._uploads.setdefault(entry_id, bytearray())
                if int(params.get("offset", 0)) != len(buffer):
                    self._uploads.pop(entry_id, None)
                    raise SweepError("photo upload out of step: start again")
                buffer += base64.b64decode(params.get("data", ""))
                if len(buffer) > MAX_PHOTO:
                    self._uploads.pop(entry_id, None)
                    raise SweepError("the photo is too large (16 MB at most)")
                if not params.get("done"):
                    return {"received": len(buffer)}
                return {"entry": self.set_photo(entry_id, bytes(self._uploads.pop(entry_id)))}
            if method == "photo.get":
                import base64

                found = self.photo(int(params["id"]))
                if found is None:
                    return {"error": "no photo"}
                data, media = found
                offset = int(params.get("offset", 0))
                piece = data[offset:offset + PHOTO_CHUNK]
                return {"data": base64.b64encode(piece).decode(), "size": len(data),
                        "type": media}
            if method == "photo.delete":
                return {"entry": self.delete_photo(int(params["id"]))}
            if method == "sweep":
                self.start_sweep(params)
                return self.state()
            if method == "stop":
                self.stop_sweep()
                return self.state()
            if method == "remote":
                self.set_remote(bool(params.get("on")))
                return self.state()
            if method == "final":
                return {"entry": self.save_final(params.get("coil", ""), params.get("band", ""),
                                                 params.get("note", ""), params.get("preset"))}
            if method == "presets":
                return {"presets": self.presets()}
            if method == "preset.save":
                return self.save_preset(params)
            if method == "preset.delete":
                self.history.delete_preset(int(params["id"]))
                self._version += 1
                return {"presets": self.presets()}
            if method == "antennas":
                return self.antennas()
            if method == "antenna.select":
                self.history.select_antenna(int(params["id"]))
                self._antenna_changed()
                return self.antennas()
            if method == "antenna.save":
                antenna_id = params.get("id")
                self.history.save_antenna(params.get("name", ""), params.get("notes", ""),
                                          int(params.get("coil_sign", 0)),
                                          int(antenna_id) if antenna_id is not None else None)
                self._antenna_changed()
                return self.antennas()
            if method == "antenna.delete":
                self.history.delete_antenna(int(params["id"]))
                self._antenna_changed()
                return self.antennas()
        except (SweepError, KeyError, ValueError) as exc:
            return {"error": str(exc)}
        return {"error": f"unknown method swr.{method}"}


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
