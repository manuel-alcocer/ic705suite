"""The suite around the applications: rig models, configuration, station, dashboard."""

import asyncio

import pytest

from ic705suite.config import Config, load_config, migrate_legacy
from ic705suite.rig import get_model, open_rig
from ic705suite.rig.base import FEATURE_DSTAR, TxDisabledError
from ic705suite.rig.icom.sim import SimTransport
from ic705suite.station.links import LocalLink


def sim_config(tmp_path, **kwargs) -> Config:
    return Config(connection="sim", swr_web=False,
                  database_url=f"sqlite:///{tmp_path / 'suite.db'}", **kwargs)


def test_models_and_factory():
    model = get_model("IC-705")
    assert model.id == "ic705" and model.has(FEATURE_DSTAR) and model.covers(7_030_000)
    assert not model.covers(300_000_000)
    with pytest.raises(ValueError, match="unknown rig model"):
        get_model("ft-817")
    rig = open_rig(Config(connection="sim"))
    assert isinstance(rig.transport, SimTransport) and rig.model is model
    with pytest.raises(ValueError, match="no radio address"):
        open_rig(Config(connection="lan"))


def test_cwic705_settings_are_imported(tmp_path):
    legacy = tmp_path / "cwic705"
    legacy.mkdir()
    (legacy / "config.toml").write_text(
        '[radio]\nhost = "10.0.0.5"\nusername = "op"\n\n[keyer]\ntx_enabled = false\n'
        '[skimmer]\nscp_path = "~/.config/cwic705/MASTER.SCP"\n')
    (legacy / "MASTER.SCP").write_text("EA1AAA\n")
    new = tmp_path / "suite" / "config.toml"
    assert migrate_legacy(new, legacy) == new
    assert migrate_legacy(new, legacy) is None          # only once
    config = load_config(new)
    assert (config.rig_model, config.connection, config.host) == ("ic705", "lan", "10.0.0.5")
    assert config.scp_path.as_posix().endswith(".config/ic705suite/MASTER.SCP")
    assert (new.parent / "MASTER.SCP").exists()


def test_rig_commands_through_the_core(tmp_path):
    async def run():
        link = LocalLink(None, sim_config(tmp_path))
        events = []
        link.attach(events.append)
        await link.connect()
        try:
            await link.command("/f 14.060", app="rig")
            await link.command("/mode USB", app="rig")
            await link.command("/mode XYZ", app="rig")
            rig = await link.call("rig")
            assert rig["frequency"] == 14_060_000 and rig["mode"] == "USB"
            assert rig["band"] == "20m" and rig["model"] == "ic705" and not rig["tx_enabled"]
            assert any(e.get("msgid") == "{model} has no {mode} mode" for e in events)
            # Receive only: the rig itself refuses to key the transmitter.
            with pytest.raises(TxDisabledError):
                await link.core.station.rig.ptt(True)
            await asyncio.sleep(0.6)
            assert link.latest["rig"]["connected"]
            # A client attached later gets the welcome, the messages and the latest rig state.
            replay = []
            link.attach(replay.append)
            kinds = [e["type"] for e in replay]
            assert kinds[0] == "welcome" and "rig" in kinds and "info" in kinds
        finally:
            await link.close()

    asyncio.run(run())


def test_dashboard_lists_the_apps_and_opens_one(tmp_path):
    from ic705suite.dashboard.app import DashboardApp
    from ic705suite.launcher import start_link

    async def run():
        link = LocalLink(None, sim_config(tmp_path))
        start_link(link)
        app = DashboardApp(link, link.core.config)
        try:
            async with app.run_test(size=(120, 36)) as pilot:
                for _ in range(40):
                    await pilot.pause(0.1)
                    if link.connected and app.rig.get("connected"):
                        break
                ids = [a["id"] for a in app.apps]
                assert ids == ["view", "cw", "dstar", "swr", "log", "packet", "ft8"]
                await pilot.press("f4")              # D-STAR: not ready yet, stays here
                await pilot.pause(0.1)
                assert app.is_running and "coming soon" in app.messages[-1][1]
                await pilot.press("f3")              # CW
                await pilot.pause(0.1)
            assert app.return_value == "cw"
        finally:
            await link.close()

    asyncio.run(run())


def test_dashboard_without_a_radio_still_opens_the_logbook(tmp_path):
    from ic705suite.dashboard.app import DashboardApp

    async def run():
        app = DashboardApp(None, Config(), problem="no radio address")
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("f2")                  # rig view needs the radio
            await pilot.pause(0.1)
            assert app.is_running
            await pilot.press("f6")                  # logbook works without it
            await pilot.pause(0.1)
        assert app.return_value == "log"

    asyncio.run(run())


def test_rig_view_tunes_with_the_keyboard(tmp_path):
    from ic705suite.apps.view.app import ViewApp

    async def run():
        link = LocalLink(None, sim_config(tmp_path))
        await link.connect()
        app = ViewApp(link)
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause(0.6)
                start = app.rig["frequency"]
                await pilot.press("pageup")
                await pilot.pause(0.6)
                assert app.rig["frequency"] == (start // 100 + 1) * 100
                await pilot.press("f2", "pagedown")  # step 1 kHz, down to the previous kHz
                await pilot.pause(0.6)
                assert app.rig["frequency"] % 1000 == 0 and app.rig["frequency"] < start + 100
                await pilot.press("escape")
            assert app.return_value == 0 and link.connected   # the link outlives the app
        finally:
            await link.close()

    asyncio.run(run())


def test_dashboard_with_the_cursor_keys(tmp_path):
    from ic705suite.dashboard.app import DashboardApp

    async def run():
        config = sim_config(tmp_path)
        DashboardApp.chosen = None
        app = DashboardApp(None, config)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.press("down", "down", "down", "down")     # View -> ... -> Logbook
            await pilot.press("up", "down", "down", "enter")      # Antenna, Logbook, Packet
            await pilot.pause(0.1)
            # Packet runs on the core, and with no radio set up there is none: it stays.
            assert app.is_running and "needs the radio" in app.messages[-1][1]
            await pilot.press("up", "enter")                      # the Logbook
            await pilot.pause(0.1)
        assert app.return_value == "log"
        # The View needs the radio too and stays; the Logbook opens with Space.
        app = DashboardApp(None, config)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.press(*["up"] * 5, "enter")
            await pilot.pause(0.1)
            assert app.is_running and "needs the radio" in app.messages[-1][1]
            await pilot.press(*["down"] * 4, "space")
            await pilot.pause(0.1)
        assert app.return_value == "log"

    asyncio.run(run())


def test_dashboard_settings_and_scope(tmp_path):
    from ic705suite.dashboard.app import DashboardApp
    from ic705suite.launcher import start_link
    from ic705suite.ui.controlpanel import ControlPanel
    from ic705suite.ui.waterfall import Waterfall

    def typed(text):
        return ["full_stop" if c == "." else c for c in text]

    async def until(pilot, condition, seconds=5.0):
        """Wait for the radio's answer (slower when the machine is busy)."""
        for _ in range(int(seconds / 0.05)):
            if condition():
                return True
            await pilot.pause(0.05)
        return condition()

    async def run():
        link = LocalLink(None, sim_config(tmp_path))
        start_link(link)
        DashboardApp.chosen = None
        app = DashboardApp(link, link.core.config)
        station = link.core.station
        try:
            async with app.run_test(size=(140, 44)) as pilot:
                assert await until(pilot, lambda: app.rig.get("connected")
                                   and app.query_one(Waterfall)._raw)   # the mini scope streams
                panel = app.query_one("#controls", ControlPanel)
                await pilot.press("tab", "tab")               # applications, quick log, settings
                assert app.focused is panel and panel.selected == "frequency"
                await pilot.press("enter", *typed("7.010"), "enter")
                assert await until(pilot, lambda: station.state.frequency == 7_010_000)
                assert not panel.editing
                await pilot.press("enter", "right", "right", "enter")   # +200 Hz
                assert await until(pilot, lambda: station.state.frequency == 7_010_200)
                await pilot.press("right")                    # Receive: AF
                af = station.state.controls["af"]
                await pilot.press("space", "up", "escape")    # changed, then put back
                await pilot.pause(0.3)
                assert await until(pilot, lambda: station.state.controls["af"] == af)
                assert panel.selected == "af"
                await pilot.press("pageup")                   # tune from anywhere
                assert await until(pilot, lambda: station.state.frequency == 7_010_300)
                await pilot.press("escape")                   # back to the applications
                assert app.focused is app.query_one("#apps")
                await pilot.press("shift+tab")
                assert app.focused is panel
                await pilot.press("tab", "f3")
                await pilot.pause(0.1)
            assert app.return_value == "cw"
        finally:
            await link.close()

    asyncio.run(run())


def test_dashboard_quick_log(tmp_path):
    """A line into hamrlog's log, with the radio's frequency and mode; today's list."""
    from ic705suite.apps.log.core.services import OperatorService, QsoService, SettingsService
    from ic705suite.apps.log.db.session import init_engine
    from ic705suite.dashboard.app import DashboardApp
    from ic705suite.dashboard.quicklog import QuickLog
    from ic705suite.launcher import start_link
    from textual.widgets import DataTable, Input

    init_engine(None)                      # the test's own logbook (tests/conftest.py)
    operator = OperatorService.create("EA7KLX", "Manuel")
    state = SettingsService.load_state()
    state.operator_id = operator.id
    SettingsService.save_state(state)

    def typed(text):
        names = {" ": "space", ",": "comma", "?": "question_mark"}
        return [names.get(c, c) for c in text]

    async def run():
        link = LocalLink(None, sim_config(tmp_path))
        start_link(link)
        DashboardApp.chosen = None
        app = DashboardApp(link, link.core.config)
        try:
            async with app.run_test(size=(150, 44)) as pilot:
                for _ in range(60):
                    await pilot.pause(0.05)
                    if app.rig.get("connected"):
                        break
                await pilot.press("tab")                          # applications -> quick log
                field = app.query_one("#qso-input", Input)
                assert app.focused is field
                await pilot.press(*typed("ea7xyz, pepe, 599, 579, sevilla"), "enter")
                for _ in range(60):
                    await pilot.pause(0.05)
                    if app.query_one("#qso-today", DataTable).row_count:
                        break
                assert field.value == ""
                row = QsoService.recent(limit=5)[-1]
                assert (row.call, row.name, row.rst_sent, row.rst_rcvd) == \
                    ("EA7XYZ", "Pepe", "599", "579")
                assert (row.freq_hz, row.band, row.mode) == (7_030_000, "40m", "CW")
                assert app.query_one("#qso-today", DataTable).row_count == 1
                await pilot.press(*typed("x"), "enter")          # not a callsign
                await pilot.pause(0.3)
                assert field.value == "x" and len(QsoService.recent(limit=5)) == 1
                await pilot.press("escape", "escape")             # clear, then back
                assert field.value == "" and app.focused is app.query_one("#apps")
                assert QuickLog in {type(w) for w in app.query("*")}
        finally:
            await link.close()

    asyncio.run(run())


def test_simulator_uses_the_demo_logbook(monkeypatch):
    import os

    from ic705suite.cli import demo_logbook

    real = os.environ["HAMRLOG_DATABASE_URL"]
    with demo_logbook(Config(connection="lan")):
        assert os.environ["HAMRLOG_DATABASE_URL"] == real
    with demo_logbook(Config(connection="sim")) as folder:
        url = os.environ["HAMRLOG_DATABASE_URL"]
        assert url != real and folder.as_posix() in url and os.path.exists(url.split("///")[1])
    assert os.environ["HAMRLOG_DATABASE_URL"] == real and not folder.exists()


def test_with_the_radio_on_a_server_it_connects_there(tmp_path, monkeypatch):
    """[rig] connection = "server": ic705suite opens a remote link to [client] connect."""
    from ic705suite import cli
    from ic705suite.station.links import RemoteLink

    path = tmp_path / "config.toml"
    path.write_text('[rig]\nconnection = "server"\n[station]\ncallsign = "EA7KLX"\n'
                    '[client]\nconnect = "pi.local:7373"\ntoken = "t"\n', encoding="utf-8")
    seen = {}

    async def fake_suite(link, config, scp=None, start_app=None, problem=""):
        seen["link"] = link
        return 0

    monkeypatch.setattr("ic705suite.launcher.run_suite", fake_suite)
    assert cli.main(["--config", str(path)]) == 0
    assert isinstance(seen["link"], RemoteLink) and seen["link"].uri == "ws://pi.local:7373"
