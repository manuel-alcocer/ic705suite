"""D-STAR's history, D-PRS reports and the name cache, in the suite's database (tables
dstar_history, dstar_dprs, dstar_names). QDStar's SQLite schema and queries, over
SQLAlchemy: the same methods, called from the core through threads (they block).
"""

from __future__ import annotations

import json
import time

import sqlalchemy as sa

from ic705suite import db

metadata = sa.MetaData()
history = sa.Table(
    "dstar_history", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("started", sa.Float, nullable=False, index=True),
    sa.Column("ended", sa.Float),
    sa.Column("direction", sa.String(2), nullable=False),     # RX or TX
    sa.Column("callsign", sa.String(16), nullable=False, index=True),
    sa.Column("suffix", sa.String(8)),
    sa.Column("name", sa.String(120)),
    sa.Column("location", sa.String(160)),
    sa.Column("reflector", sa.String(16)),                    # the TO in use, e.g. /XLX214D
    sa.Column("message", sa.String(40)),
    sa.Column("rpt1", sa.String(16)),
    sa.Column("rpt2", sa.String(16)),
    sa.Column("lat", sa.Float),
    sa.Column("lon", sa.Float),
)
dprs = sa.Table(
    "dstar_dprs", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("received", sa.Float, nullable=False, index=True),
    sa.Column("name", sa.String(16), nullable=False),         # station, object or item
    sa.Column("kind", sa.String(10), nullable=False),         # position, object, item, weather
    sa.Column("symbol", sa.String(4)),
    sa.Column("lat", sa.Float, nullable=False),
    sa.Column("lon", sa.Float, nullable=False),
    sa.Column("via", sa.String(16)),                          # whose over carried it
    sa.Column("reflector", sa.String(16)),
    sa.Column("weather", sa.Text),                            # JSON
)
names = sa.Table(
    "dstar_names", metadata,
    sa.Column("callsign", sa.String(16), primary_key=True),
    sa.Column("name", sa.String(120)),
    sa.Column("location", sa.String(160)),
    sa.Column("fetched", sa.Float, nullable=False),
)


def _rows(result) -> list[dict]:
    return [dict(r) for r in result.mappings()]


class Storage:
    def __init__(self, url: str | None) -> None:
        self.engine = db.make_engine(url)
        metadata.create_all(self.engine)

    # ---- history ------------------------------------------------------------------------

    def start_entry(self, direction: str, callsign: str, suffix: str = "",
                    reflector: str = "", rpt1: str = "", rpt2: str = "",
                    started: float | None = None) -> int:
        with self.engine.begin() as conn:
            done = conn.execute(history.insert().values(
                started=started or time.time(), direction=direction, callsign=callsign,
                suffix=suffix, reflector=reflector, rpt1=rpt1, rpt2=rpt2))
            return done.inserted_primary_key[0]

    def update_entry(self, entry_id: int, **fields) -> None:
        if fields:
            with self.engine.begin() as conn:
                conn.execute(history.update().where(history.c.id == entry_id).values(**fields))

    def fill_recent(self, callsign: str, field: str, value: str, window: float = 900) -> int:
        """Set a field on this station's recent entries that still lack it (a name or a
        message arriving after the over)."""
        if not value:
            return 0
        column = history.c[field]
        with self.engine.begin() as conn:
            done = conn.execute(history.update().where(
                history.c.callsign == callsign, history.c.started >= time.time() - window,
                sa.or_(column.is_(None), column == "")).values({field: value}))
            return done.rowcount

    def entry(self, entry_id: int) -> dict | None:
        with self.engine.connect() as conn:
            rows = _rows(conn.execute(sa.select(history).where(history.c.id == entry_id)))
        return rows[0] if rows else None

    def recent(self, limit: int = 500) -> list[dict]:
        with self.engine.connect() as conn:
            return _rows(conn.execute(sa.select(history).order_by(
                history.c.started.desc()).limit(limit)))

    def last_heard(self, reflector: str, limit: int = 4) -> list[dict]:
        with self.engine.connect() as conn:
            return _rows(conn.execute(sa.select(history).where(
                history.c.reflector == reflector).order_by(
                history.c.started.desc()).limit(limit)))

    def last_position(self, callsign: str, max_age: float = 6 * 3600) -> tuple | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(history.c.lat, history.c.lon).where(
                history.c.callsign == callsign, history.c.lat.is_not(None),
                history.c.started >= time.time() - max_age).order_by(
                history.c.started.desc()).limit(1)).first()
        return (row.lat, row.lon) if row else None

    def clear_history(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(history.delete())

    # ---- D-PRS --------------------------------------------------------------------------

    def add_dprs(self, pos, via: str, reflector: str) -> bool:
        """A received D-PRS report, unless the same one arrived in the last 10 minutes."""
        with self.engine.begin() as conn:
            dup = conn.execute(sa.select(dprs.c.id).where(
                dprs.c.name == pos.callsign, dprs.c.kind == pos.kind, dprs.c.lat == pos.lat,
                dprs.c.lon == pos.lon, dprs.c.received >= time.time() - 600)).first()
            if dup:
                return False
            conn.execute(dprs.insert().values(
                received=time.time(), name=pos.callsign, kind=pos.kind, symbol=pos.symbol,
                lat=pos.lat, lon=pos.lon, via=via, reflector=reflector,
                weather=json.dumps(pos.weather) if pos.weather else None))
            return True

    def recent_dprs(self, limit: int = 300) -> list[dict]:
        with self.engine.connect() as conn:
            return _rows(conn.execute(sa.select(dprs).order_by(
                dprs.c.received.desc()).limit(limit)))

    def latest_weather(self, max_age: float = 24 * 3600) -> list[dict]:
        """The latest weather report of each station heard in the last max_age seconds."""
        since = time.time() - max_age
        latest = sa.select(dprs.c.name, sa.func.max(dprs.c.received).label("latest")).where(
            dprs.c.kind == "weather", dprs.c.received >= since).group_by(dprs.c.name).subquery()
        with self.engine.connect() as conn:
            return _rows(conn.execute(sa.select(dprs).join(latest, sa.and_(
                dprs.c.name == latest.c.name, dprs.c.received == latest.c.latest)).where(
                dprs.c.kind == "weather")))

    # ---- names --------------------------------------------------------------------------

    def cached_name(self, callsign: str, max_age: float = 30 * 86400) -> dict | None:
        with self.engine.connect() as conn:
            rows = _rows(conn.execute(sa.select(names).where(names.c.callsign == callsign)))
        if rows and time.time() - rows[0]["fetched"] < max_age:
            return rows[0]
        return None

    def store_name(self, callsign: str, name: str, location: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(names.delete().where(names.c.callsign == callsign))
            conn.execute(names.insert().values(callsign=callsign, name=name,
                                               location=location, fetched=time.time()))
