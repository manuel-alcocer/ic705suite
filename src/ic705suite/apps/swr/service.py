"""Antenna tuning: SWR and noise sweeps on the shared station (formerly swr-remote).

* SWR sweep (transmits): RTTY carrier at low power, about 0.4 s per point, PTT watchdog,
  only inside one amateur band; restores frequency, mode, data mode and power afterwards.
  Needs transmission enabled ([rig] tx_enabled or --tx).
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

from ic705suite import db
from ic705suite.apps.swr.analysis import COIL_SIGN, DEFAULT_KHZ_PER_UNIT, analyze
from ic705suite.config import Config
from ic705suite.i18n import N_
from ic705suite.rig.bands import band_for, find_band, format_frequency
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


class SweepError(Exception):
    pass


@dataclass
class Sweep:
    kind: str = ""             # "swr" or "noise"
    running: bool = False
    band: str = ""
    target: int = 0
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
        """Where the antenna was left on each band: the latest saved position per band."""
        out: dict[str, dict] = {}
        for entry in self.entries(antenna_id=antenna_id):
            if entry.get("kind") == "final":
                out[entry["band"]] = entry
        return out

    def _import(self, path: Path) -> None:
        with self.engine.begin() as conn:
            if conn.execute(sa.select(sa.func.count()).select_from(history_table)).scalar():
                return
            try:
                entries = json.loads(path.read_text())
            except (OSError, ValueError):
                return
            for entry in entries:
                self._insert(conn, entry, None)
        log.info("imported %d antenna sweeps from %s", len(entries), path)

    @staticmethod
    def _insert(conn, entry: dict, antenna_id: int | None) -> None:
        conn.execute(history_table.insert().values(
            ts=entry.get("ts", ""), band=entry.get("band", ""), kind=entry.get("kind", ""),
            data=json.dumps(entry), antenna_id=antenna_id))

    def add(self, entry: dict, antenna_id: int | None = None) -> None:
        antenna_id = antenna_id if antenna_id is not None else self.antenna()["id"]
        with self.engine.begin() as conn:
            self._insert(conn, entry, antenna_id)

    def entries(self, band: str = "", limit: int | None = None,
                antenna_id: int | None = None) -> list[dict]:
        antenna_id = antenna_id if antenna_id is not None else self.antenna()["id"]
        query = sa.select(history_table.c.data).order_by(history_table.c.id) \
            .where(history_table.c.antenna_id == antenna_id)
        if band:
            query = query.where(history_table.c.band == band)
        with self.engine.connect() as conn:
            rows = [json.loads(r[0]) for r in conn.execute(query)]
        return rows[-limit:] if limit else rows

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
        self._web = None
        self._version = 0
        self._sent = -1

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
            # Reachable from the phone on the local network, as swr-remote was.
            self._web = await serve(self, "0.0.0.0", self.port)
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
            if band is None or band_for(stop) is not band:
                raise SweepError("SWR sweep must stay inside one amateur band")
        target = int(req.get("target") or (start + stop) // 2)
        power = max(1, min(self.max_power, int(req.get("power") or self.max_power)))
        self.sweep = Sweep(kind=kind, running=True, band=req.get("band") or (band.name if band else ""),
                           target=target, total=len(freqs))
        self._version += 1
        self._task = asyncio.create_task(self._run_sweep(freqs, power))

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
        if not result.confident:
            return
        self.history.add({"ts": _now(), "band": sweep.band, "kind": sweep.kind,
                          "target": sweep.target, "resonance": result.resonance,
                          "best": result.best_value, "at_edge": result.at_edge})

    def save_final(self, coil_text: str, band: str = "", note: str = "") -> dict:
        """Record where the coil (or adjustment) ended up on a band, for the active antenna:
        with the last SWR sweep's results when that sweep was on this band, else alone."""
        coil = float(str(coil_text).strip().replace(",", "."))
        sweep = self.sweep
        if sweep.running:
            raise SweepError("wait for the sweep to finish")
        band = band or sweep.band
        if not band:
            raise SweepError("which band?")
        entry = {"ts": _now(), "band": band, "kind": "final", "coil": coil}
        if note.strip():
            entry["note"] = note.strip()
        a = sweep.analysis
        if a and sweep.kind == "swr" and sweep.band == band and not sweep.error:
            entry.update({"target": sweep.target, "resonance": a["resonance"],
                          "best": a["best_value"], "at_edge": a["at_edge"], "tuned": a["tuned"],
                          "swr_at_target": a.get("swr_at_target")})
        self.history.add(entry)
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
                "allow_tx": self.allow_tx, "max_power": self.max_power, "port": self.port,
                "frequency": st.frequency, "mode": st.mode,
                "sweep": {"kind": s.kind, "running": s.running, "band": s.band, "target": s.target,
                          "total": s.total, "points": s.points,
                          "analysis": s.analysis, "warning": s.warning, "error": s.error,
                          "finished_at": s.finished_at}}

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
            out.append({"name": name, "start": band.start, "end": end, "target": target,
                        "step": step})
        return out

    async def call(self, method: str, params: dict) -> dict:
        """The "swr.*" calls of the core protocol (the TUI uses them; the web page uses HTTP)."""
        try:
            if method == "state":
                return self.state()
            if method == "bands":
                return {"bands": self.bands()}
            if method == "history":
                return {"history": self.history.entries(params.get("band", ""), limit=20)}
            if method == "sweep":
                self.start_sweep(params)
                return self.state()
            if method == "stop":
                self.stop_sweep()
                return self.state()
            if method == "final":
                return {"entry": self.save_final(params.get("coil", ""), params.get("band", ""),
                                                 params.get("note", ""))}
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
