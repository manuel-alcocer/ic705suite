"""A logbook (hamrlog's database) copied whole into another database, typically from the
SQLite file of one machine into the PostgreSQL of the core, so every client shares it:

    ic705suite --copy-logbook sqlite:////path/hamrlog.sqlite3 postgresql+psycopg://...

The tables are made in the target as hamrlog makes them, with nothing seeded; the rows go
table by table in the order of their references, the ids kept; then PostgreSQL's counters
are moved past them. The target must be empty (no QSOs): nothing there is overwritten.
"""

from __future__ import annotations

import sqlalchemy as sa

BATCH = 500


class CopyError(Exception):
    pass


def copy_logbook(source_url: str, target_url: str) -> dict[str, int]:
    """{table: rows copied}; CopyError when the target already has a logbook."""
    from ic705suite.apps.log.db import session as log_session
    from ic705suite.apps.log.db.models import Base

    log_session.dispose()
    log_session.init_engine(source_url)            # the source brought up to date first
    log_session.dispose()
    source = sa.create_engine(source_url)
    target = sa.create_engine(target_url)
    try:
        Base.metadata.create_all(target)
        with target.connect() as conn:
            for table in Base.metadata.sorted_tables:
                if conn.execute(sa.select(sa.func.count()).select_from(table)).scalar():
                    raise CopyError(f"the target already has rows in {table.name}: "
                                    "it must be an empty logbook")
        copied: dict[str, int] = {}
        with source.connect() as src, target.begin() as dst:
            for table in Base.metadata.sorted_tables:
                rows = [dict(r._mapping) for r in src.execute(sa.select(table))]
                for start in range(0, len(rows), BATCH):
                    dst.execute(table.insert(), rows[start:start + BATCH])
                copied[table.name] = len(rows)
            if target.dialect.name == "postgresql":
                for table in Base.metadata.sorted_tables:
                    if "id" in table.c and isinstance(table.c.id.type, sa.Integer):
                        dst.execute(sa.text(
                            "SELECT setval(pg_get_serial_sequence(:t, 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"),
                            {"t": table.name})
        with target.connect() as conn:
            for table in Base.metadata.sorted_tables:
                have = conn.execute(sa.select(sa.func.count()).select_from(table)).scalar()
                if have != copied[table.name]:
                    raise CopyError(f"{table.name}: {have} rows in the target, "
                                    f"{copied[table.name]} copied")
        return copied
    finally:
        source.dispose()
        target.dispose()
