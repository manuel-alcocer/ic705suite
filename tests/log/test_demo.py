"""Demo mode: hamrlog --demo opens invented data and keeps nothing."""

from __future__ import annotations

import os

import pytest

from ic705suite.apps.log import cli, demo, i18n
from ic705suite.apps.log.core.services import (
    AntennaService,
    ContactService,
    PowerSupplyService,
    QsoService,
    SettingsService,
    StationService,
)
from ic705suite.apps.log.core.state import SessionState
from ic705suite.apps.log.db import session as db_session
from ic705suite.apps.log.db.preseed import apply_preseed


def open_database(path) -> None:
    db_session.dispose()
    db_session.init_engine(f"sqlite:///{path.as_posix()}")


def snapshot() -> list[tuple]:
    return [
        (row.qso_utc, row.call, row.name, row.freq_hz, row.mode, row.equipment_name,
         row.repeater_call, row.entry_mode)
        for row in QsoService.recent(1000)
    ]


def test_the_seed_is_invented_and_its_setups_come_from_the_catalog(monkeypatch):
    seed = demo.load_seed()
    assert "made up" in seed["comment"]
    assert len(seed["qsos"]) >= 100 and len(seed["contacts"]) >= 30
    # The catalog is what the setups are made of.
    monkeypatch.setenv("HAMRLOG_PRESEED", "1")
    apply_preseed(db_session.init_engine())
    radios = {station.name for station in StationService.list_all()}
    antennas = {antenna.name for antenna in AntennaService.list_all()}
    supplies = {supply.name for supply in PowerSupplyService.list_all()}
    for setup in seed["setups"]:
        assert set(setup["radios"]) <= radios, setup["name"]
        assert set(setup["antennas"]) <= antennas, setup["name"]
        assert set(setup["supplies"]) <= supplies, setup["name"]


def test_the_template_holds_the_seed_and_is_always_the_same(tmp_path):
    seed = demo.load_seed()
    first = demo.build_template()
    open_database(first)
    built = snapshot()
    assert len(built) == len(seed["qsos"])
    # Logged at given times, but as the radio would have: not «manual».
    assert {row[-1] for row in built} == {"AUTO"}
    assert ContactService.count() >= len(seed["contacts"])
    state = SettingsService.load_state()
    assert state.profile_name == seed["active_profile"]
    assert state.operator_id is not None

    # Built again, the same data.
    db_session.dispose()
    second = tmp_path / "again.sqlite3"
    demo.build_database(f"sqlite:///{second.as_posix()}")
    open_database(second)
    assert snapshot() == built


def test_the_template_is_built_once_and_again_when_the_seed_changes(monkeypatch):
    path = demo.ensure_template()
    built_at = path.stat().st_mtime_ns
    assert demo.ensure_template().stat().st_mtime_ns == built_at
    monkeypatch.setattr(demo, "fingerprint", lambda: "another seed")
    assert not demo.template_is_current()
    demo.ensure_template()
    assert demo.template_is_current()


def test_nothing_done_in_a_demo_outlives_it(monkeypatch):
    template = demo.ensure_template()
    home = os.environ["HAMRLOG_HOME"]
    with demo.session("es") as folder:
        assert os.environ["HAMRLOG_HOME"] == str(folder)
        assert os.environ["HAMRLOG_LANG"] == "es"
        db_session.dispose()
        db_session.init_engine()
        before = len(QsoService.recent(1000))
        state = SessionState(operator_id=SettingsService.load_state().operator_id)
        state.set_band("20m")
        QsoService.log({"call": "EA4XYZ"}, state)
        assert len(QsoService.recent(1000)) == before + 1
    assert not folder.exists()
    assert os.environ["HAMRLOG_HOME"] == home
    assert "HAMRLOG_LANG" not in os.environ or os.environ["HAMRLOG_LANG"] != "es"

    # The next demo starts from the same data.
    open_database(template)
    assert len(QsoService.recent(1000)) == before
    with demo.session() as folder:
        assert os.environ["HAMRLOG_LANG"] == "en"


@pytest.mark.parametrize(
    ("argv", "language"),
    [(["--demo"], "en"), (["--demo", "--lang=es"], "es"), (["-d", "-l", "es"], "es")],
)
def test_the_command_line_opens_the_demo_in_a_language(monkeypatch, argv, language):
    seen = {}

    def run(app, **kwargs):
        db_session.init_engine()
        seen["demo"] = app.demo
        seen["language"] = i18n.language()
        seen["qsos"] = len(QsoService.recent(1000))
        seen["kwargs"] = kwargs

    monkeypatch.setattr("ic705suite.apps.log.tui.app.HamrlogApp.run", run)
    try:
        assert cli.main(argv) == 0
    finally:
        i18n.set_language("es")
    assert seen == {
        "demo": True,
        "language": language,
        "qsos": len(demo.load_seed()["qsos"]),
        "kwargs": {"mouse": False},
    }


def test_demo_does_not_mix_with_other_commands():
    with pytest.raises(SystemExit):
        cli.main(["--demo", "export"])
    with pytest.raises(SystemExit):
        cli.main(["--demo", "--database", "sqlite:///x.db"])


def test_lang_alone_sets_the_language_of_a_normal_run(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "ic705suite.apps.log.tui.app.HamrlogApp.run", lambda app, **kwargs: seen.update(demo=app.demo)
    )
    monkeypatch.delenv("HAMRLOG_LANG", raising=False)
    try:
        cli.main(["-l", "en"])
        assert i18n.language() == "en"
        assert os.environ["HAMRLOG_LANG"] == "en"
    finally:
        os.environ.pop("HAMRLOG_LANG", None)
        i18n.set_language("es")
    assert seen == {"demo": False}


def test_build_demo_prints_where_the_template_is(capsys):
    assert cli.main(["build-demo"]) == 0
    assert str(demo.template_path()) in capsys.readouterr().out


async def test_the_status_line_says_it_is_a_demo(operator):
    from ic705suite.apps.log.tui.app import HamrlogApp

    app = HamrlogApp(demo=True)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        assert str(app.query_one("#statusline").render()).startswith(" DEMO ")
