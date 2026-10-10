"""Tools (F8), settings (F9) and the line between the days of the log."""

from __future__ import annotations

import datetime as dt
import os
import re

import pytest

from ic705suite.apps.log import appconfig, i18n
from ic705suite.apps.log.core import qcodes
from ic705suite.apps.log.core.services import ProfileService, QsoService
from ic705suite.apps.log.core.state import SessionState
from ic705suite.apps.log.db import session as db_session
from ic705suite.apps.log.tui.app import HamrlogApp
from ic705suite.apps.log.tui.profiles import ProfileKind
from ic705suite.apps.log.tui.screens.base import ConfirmScreen
from ic705suite.apps.log.tui.settings import DAY_SEPARATOR, LANGUAGE, TIMEZONE, UPDATES
from ic705suite.apps.log.tui.widgets.detail import DetailPanel
from ic705suite.apps.log.tui.widgets.entry import BrowseBar, EntryField, EntryPanel
from ic705suite.apps.log.tui.widgets.footer import StatsFooter
from ic705suite.apps.log.tui.widgets.history import DAY_SEPARATOR_CHAR, HistoryPanel
from ic705suite.apps.log.tui.widgets.items import ItemTable


def feedback(app) -> str:
    return app.query_one("#entry").message


def log_on_two_days(operator) -> None:
    state = SessionState(operator_id=operator.id)
    state.set_band("40m")
    for call, when in (
        ("EA1AAA", dt.datetime(2026, 10, 6, 18, 0)),
        ("EA2BBB", dt.datetime(2026, 10, 6, 19, 0)),
        ("EA3CCC", dt.datetime(2026, 10, 7, 9, 0)),
    ):
        QsoService.log({"call": call}, state, qso_utc=when)


async def open_view(pilot, key: str) -> None:
    await pilot.press(key)
    await pilot.pause()
    await pilot.pause()


# ----------------------------------------------------------------- tools ----

def test_q_codes_are_found_by_letters_or_meaning():
    assert [code.code for code in qcodes.search("qrz")] == ["QRZ"]
    # In the language in use: Spanish in the tests.
    assert "QRO" in [code.code for code in qcodes.search("aumente")]
    assert len(qcodes.search("")) == len(qcodes.QCODES)


async def test_f8_shows_the_q_code_and_the_box_filters_it(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_view(pilot, "f8")
        assert app.query_one("#log-frame").border_title == "Herramientas"
        tabs = str(app.query_one("#inventory-tabs").render())
        assert "Código Q" in tabs
        table = app.query_one(ItemTable)
        assert table.item_count == len(qcodes.QCODES)
        # Read-only: no insert row, the cursor starts on the first code.
        assert table.row_count == len(qcodes.QCODES)
        assert table.selected_item().name == "QRA"

        panel = app.query_one(EntryPanel)
        box = app.query_one("#entry-filter", EntryField)
        assert app.focused is box
        for character in "qsy":
            await pilot.press(character)
        await pilot.pause()
        assert [item.name for item in table._items.values()] == ["QSY"]
        assert "1 de" in str(app.query_one("#inventory-tabs").render())
        assert "QSY" in str(app.query_one(DetailPanel).render())

        # The arrows move over the list while the box keeps the keyboard.
        for _ in range(3):
            await pilot.press("backspace")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert table.selected_item().name == "QRB"
        assert not panel.browsing
        assert app.focused is box

        # Enter does not try to add a code.
        await pilot.press("enter")
        await pilot.pause()
        assert table.item_count == len(qcodes.QCODES)


# -------------------------------------------------------------- settings ----

async def change_setting(pilot, app, row_id: int, value: str) -> None:
    table = app.query_one(ItemTable)
    table.move_cursor(row=table.get_row_index(str(row_id)))
    table.move_selection(0)
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    box = app.query_one("#entry-value", EntryField)
    box.value = value
    await pilot.press("enter")
    await pilot.pause()


async def test_f9_lists_the_settings_and_saves_them(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_view(pilot, "f9")
        assert app.query_one("#log-frame").border_title == "Configuración"
        table = app.query_one(ItemTable)
        assert [item.name for item in table._items.values()] == [
            "Base de datos", "Idioma", "Zona horaria", "Separador de días", "Actualizaciones",
        ]
        # Settings are only changed: the bar is up from the start.
        assert app.query_one(EntryPanel).browsing
        assert isinstance(app.focused, BrowseBar)

        await change_setting(pilot, app, LANGUAGE, "english")
        assert appconfig.load().language == "en"
        assert "próxima vez" in feedback(app)

        await change_setting(pilot, app, LANGUAGE, "klingon")
        assert "Idioma desconocido" in feedback(app)
        assert appconfig.load().language == "en"
        await pilot.press("escape")
        await pilot.pause()

        await change_setting(pilot, app, TIMEZONE, "Mars/Olympus")
        assert "Zona horaria desconocida" in feedback(app)
        await pilot.press("escape")
        await pilot.pause()
        await change_setting(pilot, app, TIMEZONE, "Atlantic/Canary")
        assert appconfig.load().timezone == "Atlantic/Canary"
        # At once: the status line shows the local time next to UTC.
        footer = app.query_one(StatsFooter)
        assert footer.timezone == "Atlantic/Canary"
        assert "WE" in str(footer.render())  # WET or WEST, depending on the date

        # D does not delete a setting.
        await pilot.press("d")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        assert "no se borra" in feedback(app)


def day_rows(history) -> list[int]:
    return [index for index in range(history.row_count) if history.is_day_row(index)]


def screen_lines(app) -> list[str]:
    svg = app.export_screenshot().replace("&#160;", " ")
    return re.findall(r"<text[^>]*>(.*?)</text>", svg)


async def test_a_dashed_line_separates_the_days(operator):
    log_on_two_days(operator)
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        # Oldest first: 6 Oct, 6 Oct, the line, 7 Oct, then the insert row
        # (which is not a day of its own: no line before it).
        assert day_rows(history) == [2]
        assert history.qso_count == 3
        # Drawn dashed, in dark grey, across the whole width.
        strip = history._render_line(
            history.header_height + 2, 0, history.size.width, history.rich_style
        )
        assert strip.text == DAY_SEPARATOR_CHAR * history.size.width
        assert all(segment.style.color.name == "grey30" for segment in strip)
        assert any(DAY_SEPARATOR_CHAR * 20 in line for line in screen_lines(app))

        # The arrows step over it, both ways.
        assert history.on_insert_row
        await pilot.press("up")
        await pilot.pause()
        assert history.selected_row().call == "EA3CCC"
        await pilot.press("up")
        await pilot.pause()
        assert history.selected_row().call == "EA2BBB"
        await pilot.press("down")
        await pilot.pause()
        assert history.selected_row().call == "EA3CCC"


async def test_the_cursor_never_rests_on_the_line(operator):
    log_on_two_days(operator)
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        history.move_cursor(row=3)
        await pilot.pause()
        # A jump landing on the line goes past it, the way it was going.
        for delta, expected in ((-1, "EA2BBB"), (1, "EA3CCC")):
            history.move_selection(delta)
            await pilot.pause()
            assert history.selected_row().call == expected
        history.move_selection(-100)
        await pilot.pause()
        history.move_selection(100)
        await pilot.pause()
        assert history.on_insert_row


async def test_the_day_separator_can_be_switched_off(operator):
    log_on_two_days(operator)
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        assert day_rows(history) == [2]
        await open_view(pilot, "f9")
        await change_setting(pilot, app, DAY_SEPARATOR, "no")
        assert appconfig.load().day_separator is False
        assert not history.day_separator
        assert day_rows(history) == []


async def test_the_day_separator_follows_the_order_of_the_log(operator):
    log_on_two_days(operator)
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        history.set_order("desc")
        # Newest first: the insert row, 7 Oct, the line, 6 Oct, 6 Oct.
        assert day_rows(history) == [2]
        assert history.get_row_at(1)[2].plain.endswith("EA3CCC")


async def test_the_setting_off_is_applied_on_start(operator):
    log_on_two_days(operator)
    appconfig.save(appconfig.AppConfig(day_separator=False))
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        assert day_rows(app.query_one(HistoryPanel)) == []


# --------------------------------------------------------- config file ----

def test_the_config_file_survives_a_broken_or_strange_file():
    path = appconfig.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert appconfig.load() == appconfig.AppConfig()
    path.write_text('{"language": "es", "day_separator": "maybe", "other": 1}', encoding="utf-8")
    assert appconfig.load() == appconfig.AppConfig(language="es")


@pytest.mark.skipif(bool(os.environ.get("IC705SUITE_TEST_POSTGRES")),
                    reason="checks the SQLite URL the tests use by default")
def test_the_database_comes_from_the_settings_unless_the_environment_says(
    tmp_path, monkeypatch
):
    target = tmp_path / "elsewhere" / "radio.sqlite3"
    appconfig.save(appconfig.AppConfig(database=str(target)))
    assert db_session.default_database_url().startswith("sqlite:///")  # environment
    monkeypatch.delenv("HAMRLOG_DATABASE_URL")
    assert db_session.default_database_url() == f"sqlite:///{target.as_posix()}"
    assert target.parent.is_dir()

    appconfig.save(appconfig.AppConfig(database="postgresql://radio@localhost/log"))
    assert db_session.default_database_url() == "postgresql://radio@localhost/log"


def test_the_language_comes_from_the_settings_after_the_override(monkeypatch):
    for name in ("HAMRLOG_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    monkeypatch.setattr(i18n, "_windows_ui_language", lambda: "")
    appconfig.save(appconfig.AppConfig(language="en"))
    assert i18n.detect_language() == "en"
    monkeypatch.setenv("HAMRLOG_LANG", "es")
    assert i18n.detect_language() == "es"


# -------------------------------------------------------------- profiles ----

async def test_the_power_of_a_profile_is_aligned_right(operator):
    ProfileService.save(
        None, name="Fonía", operator_id=operator.id, freq_hz=145_500_000, mode="FM", power_w=5
    )
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_view(pilot, "f4")
        table = app.query_one(ItemTable)
        assert "PWR" in ProfileKind.right_aligned
        assert table.columns["PWR"].label.justify == "right"
        row = table.get_row_at(0)
        column = list(table.columns).index("PWR")
        assert row[column].justify == "right"


async def test_the_update_check_can_be_switched_off_in_f9(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await open_view(pilot, "f9")
        await change_setting(pilot, app, UPDATES, "no")
        assert appconfig.load().check_updates is False
        await change_setting(pilot, app, UPDATES, "sí")
        assert appconfig.load().check_updates is True
