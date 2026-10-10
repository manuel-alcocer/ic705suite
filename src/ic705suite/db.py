"""The suite's database: any SQLAlchemy URL from ``[database] url``.

PostgreSQL is the recommended engine (``postgresql+psycopg://user:password@host/ic705suite``,
``pip install ic705suite[postgres]``); with no URL configured the suite uses a SQLite file so
it works out of the box. Every application keeps its tables in this one database, each with
its own prefix (``cw_``, ``swr_``...).
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from ic705suite import paths


def default_url() -> str:
    return f"sqlite:///{paths.data_dir() / 'ic705suite.db'}"


def database_url(url: str | None) -> str:
    """Config value -> SQLAlchemy URL; "~" is expanded in SQLite paths."""
    url = url or default_url()
    if url.startswith("sqlite:///~"):
        url = "sqlite:///" + str(Path(url[len("sqlite:///"):]).expanduser())
    return url


def make_engine(url: str | None) -> sa.Engine:
    url = database_url(url)
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(url, future=True)
    if engine.dialect.name == "sqlite":
        sa.event.listen(engine, "connect",
                        lambda conn, _rec: conn.execute("PRAGMA foreign_keys = ON"))
    return engine


def describe(url: str | None) -> str:
    """The URL without its password, for the screen and the log."""
    try:
        return sa.engine.make_url(database_url(url)).render_as_string(hide_password=True)
    except sa.exc.ArgumentError:
        return "?"
