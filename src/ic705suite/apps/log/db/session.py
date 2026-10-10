"""Engine and session management.

A single module-level engine is created on startup. SQLite is the default, but
``HAMRLOG_DATABASE_URL`` switches the whole application to PostgreSQL or any
other SQLAlchemy backend without touching the rest of the code, which is what
a future web frontend or API would need.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, make_url, select
from sqlalchemy.orm import Session, sessionmaker

from .. import appconfig
from ..paths import database_path, ensure_dirs
from .codes import assign_missing_codes
from .migrations import add_missing_columns, upgrade_data
from .models import CURRENT_SCHEMA_VERSION, Base, SchemaVersion
from .preseed import apply_preseed

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def default_database_url() -> str:
    """Database URL from the environment, the settings (F9), or the local file."""
    url = os.environ.get("HAMRLOG_DATABASE_URL")
    if url:
        return url
    config = appconfig.load()
    if config.database_url:
        if config.database_path is not None:
            config.database_path.parent.mkdir(parents=True, exist_ok=True)
        return config.database_url
    ensure_dirs()
    # as_posix() keeps the URL valid on Windows, where paths use backslashes.
    return f"sqlite:///{database_path().as_posix()}"


def init_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Create the engine, the schema and the session factory.

    Safe to call more than once; later calls with the same URL reuse the
    existing engine.
    """
    global _engine, _session_factory

    target = url or default_database_url()
    # Compared as URLs, not as text: SQLAlchemy escapes the colon of a
    # Windows drive ("C%3A"), so the text of the same URL never matched and
    # every call built a new engine.
    if _engine is not None and _engine.url == make_url(target):
        return _engine

    connect_args = {}
    if target.startswith("sqlite"):
        # The TUI touches the database from worker threads during exports.
        connect_args["check_same_thread"] = False

    _engine = create_engine(target, echo=echo, future=True, connect_args=connect_args)

    if target.startswith("sqlite"):
        _enable_sqlite_pragmas(_engine)

    Base.metadata.create_all(_engine)
    # Existing databases predate any column added since they were created.
    add_missing_columns(_engine)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    upgrade_data(_engine, _stored_schema_version())
    _stamp_schema_version()
    apply_preseed(_engine)
    assign_missing_codes(_engine)
    return _engine


def _enable_sqlite_pragmas(engine: Engine) -> None:
    """Turn on foreign keys and WAL so concurrent readers do not block."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def _stored_schema_version() -> int | None:
    """Revision the database was left at, None when it was just created."""
    with session_scope() as session:
        row = session.scalars(select(SchemaVersion).limit(1)).first()
        return row.version if row is not None else None


def _stamp_schema_version() -> None:
    """Record the schema revision so future upgrades know where to start."""
    with session_scope() as session:
        row = session.scalars(select(SchemaVersion).limit(1)).first()
        if row is None:
            session.add(SchemaVersion(version=CURRENT_SCHEMA_VERSION))
        elif row.version != CURRENT_SCHEMA_VERSION:
            row.version = CURRENT_SCHEMA_VERSION


def session_factory() -> sessionmaker[Session]:
    """Return the configured session factory, initialising the engine if needed."""
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context: commits on success, rolls back on error."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose() -> None:
    """Close pooled connections. Used by tests and on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
