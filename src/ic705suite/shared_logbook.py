"""One logbook for every client: hamrlog's database on the core's machine (PostgreSQL),
named in the core's [logbook] section. The core opens it there, and tells each client that
connects how to reach it (in the welcome, behind the core's token): at the address the
client used for the core. A client then opens it instead of its own file.

    [logbook]
    database = "postgresql+psycopg://ic705suite@localhost/hamrlog"
    password_file = "~/.config/ic705suite/logbook-db-password"
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)

VARIABLE = "HAMRLOG_DATABASE_URL"       # what hamrlog opens (apps.log.db.session)


def _password(config) -> str:
    if not config.logbook_password_file:
        return ""
    try:
        return Path(config.logbook_password_file).expanduser().read_text().strip()
    except OSError as exc:
        log.warning("logbook: cannot read the password file: %s", exc)
        return ""


def core_url(config) -> str | None:
    """The logbook as the core opens it (its own machine), None with no [logbook]."""
    if not config.logbook_database:
        return None
    url = make_url(config.logbook_database)
    password = _password(config)
    if password:
        url = url.set(password=password)
    return url.render_as_string(hide_password=False)


def for_clients(config) -> dict | None:
    """What a client needs to open it, all but the host (the core's, as the client sees
    it): for the welcome."""
    if not config.logbook_database:
        return None
    url = make_url(config.logbook_database)
    if url.get_backend_name() == "sqlite":
        return None                        # a file of the core's machine: not to be shared
    return {"driver": url.drivername, "user": url.username or "",
            "password": _password(config) or url.password or "", "port": url.port,
            "database": url.database or ""}


def client_url(info: dict, host: str) -> str:
    from sqlalchemy.engine import URL

    return URL.create(info["driver"], username=info.get("user") or None,
                      password=info.get("password") or None, host=host,
                      port=info.get("port"), database=info.get("database") or None
                      ).render_as_string(hide_password=False)


def use(url: str) -> None:
    """hamrlog opens `url` from now on (the engine already open, if another, is closed)."""
    if os.environ.get(VARIABLE) == url:
        return
    os.environ[VARIABLE] = url
    from ic705suite.apps.log.db import session

    session.dispose()
    log.info("logbook: %s", make_url(url).render_as_string(hide_password=True))


def ask_core(address: str, token: str, timeout: float = 8.0) -> bool:
    """For the logbook opened on its own (ic705suite log): the core's logbook, asked once
    with a connection of its own; False (this machine's own then) when the core does not
    answer or has none."""
    import asyncio

    from ic705suite.station.links import RemoteLink

    async def ask() -> bool:
        link = RemoteLink(address, token, client_name="ic705suite log")
        try:
            await asyncio.wait_for(link.connect(), timeout)
            return bool((link.welcome or {}).get("logbook"))
        finally:
            await link.close()

    try:
        return asyncio.run(ask())
    except Exception as exc:  # noqa: BLE001 - the logbook of this machine then
        log.warning("logbook: the core did not answer (%s): this machine's", exc)
        return False
