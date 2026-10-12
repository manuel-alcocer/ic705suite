"""One logbook for every client: the core's [logbook], told to the clients in the welcome."""

import asyncio

from ic705suite import shared_logbook
from ic705suite.config import Config
from ic705suite.station.links import RemoteLink


def core_config(tmp_path, database: str) -> Config:
    secret = tmp_path / "logbook-db-password"
    secret.write_text("s3cret\n")
    return Config(connection="sim", swr_web=False, callsign="EA7KLX",
                  database_url=f"sqlite:///{tmp_path / 'suite.db'}",
                  logbook_database=database, logbook_password_file=str(secret))


def test_the_urls(tmp_path):
    config = core_config(tmp_path, "postgresql+psycopg://ic705suite@localhost/hamrlog")
    assert shared_logbook.core_url(config) == \
        "postgresql+psycopg://ic705suite:s3cret@localhost/hamrlog"
    info = shared_logbook.for_clients(config)
    assert info == {"driver": "postgresql+psycopg", "user": "ic705suite", "password": "s3cret",
                    "port": None, "database": "hamrlog"}
    assert shared_logbook.client_url(info, "hotspot.example.net") == \
        "postgresql+psycopg://ic705suite:s3cret@hotspot.example.net/hamrlog"
    assert shared_logbook.for_clients(Config()) is None
    assert shared_logbook.for_clients(core_config(tmp_path, "sqlite:///x.db")) is None


def test_a_client_opens_the_cores_logbook(tmp_path, monkeypatch):
    from tests.cw.test_client_server import start_server

    monkeypatch.setenv(shared_logbook.VARIABLE, "sqlite:///client-own.sqlite3")
    config = core_config(tmp_path, "postgresql+psycopg://ic705suite@localhost/hamrlog")

    async def scenario():
        from ic705suite.rig.icom.sim import SimTransport

        core, stop, task, port = await start_server(config, SimTransport(stations=[], seed=7))
        assert shared_logbook.os.environ[shared_logbook.VARIABLE].endswith("@localhost/hamrlog")
        link = RemoteLink(f"127.0.0.1:{port}", "secret")
        try:
            await link.connect()
            assert link.welcome["logbook"]["database"] == "hamrlog"
            assert shared_logbook.os.environ[shared_logbook.VARIABLE] == \
                "postgresql+psycopg://ic705suite:s3cret@127.0.0.1/hamrlog"   # the core's host
        finally:
            await link.close()
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_ft8_logs_from_the_core(tmp_path, monkeypatch):
    """With [logbook] the core logs the QSO; the clients are told so (logbook: true)."""
    from ic705suite.apps.ft8 import service as service_module
    from ic705suite.apps.log.core.services import OperatorService, QsoService, SettingsService
    from ic705suite.apps.log.db import session
    from ic705suite.station.links import LocalLink

    monkeypatch.setattr(service_module, "data_dir", lambda: tmp_path)
    shared = f"sqlite:///{tmp_path / 'shared.sqlite3'}"
    monkeypatch.setenv(shared_logbook.VARIABLE, shared)
    session.dispose()
    session.init_engine(shared)
    operator = OperatorService.create("EA7KLX", "Manuel")
    state = SettingsService.load_state()
    state.operator_id = operator.id
    SettingsService.save_state(state)
    config = core_config(tmp_path, shared)

    async def scenario():
        link = LocalLink(None, config)
        await link.connect()
        events: list[dict] = []
        link.attach(lambda e: e.get("type") in ("ft8.qso", "info", "error") and events.append(e))
        ft8 = link.core.ft8
        try:
            qso = ft8.qso
            qso.them, qso.their_grid, qso.sent_report, qso.got_report = \
                "F5RRS", "JN36", "-16", "-08"
            ft8._log()
            for _ in range(100):
                if any("logged in the logbook" in e.get("text", "") for e in events):
                    break
                await asyncio.sleep(0.05)
            assert next(e for e in events if e["type"] == "ft8.qso")["logbook"] is True
            assert any("F5RRS logged in the logbook" in e.get("text", "") for e in events)
        finally:
            await link.close()

    asyncio.run(scenario())
    session.init_engine(shared)
    assert [q.call for q in QsoService.recent()] == ["F5RRS"]
    session.dispose()
