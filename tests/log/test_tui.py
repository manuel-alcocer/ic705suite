"""End-to-end interface tests driven through Textual's pilot."""

from __future__ import annotations

from ic705suite.apps.log.core.services import QsoService
from ic705suite.apps.log.tui.app import HamrlogApp
from ic705suite.apps.log.tui.screens.base import ConfirmScreen, FormScreen
from ic705suite.apps.log.tui.widgets.detail import DetailPanel
from ic705suite.apps.log.tui.widgets.entry import BrowseBar, EntryPanel
from ic705suite.apps.log.tui.widgets.history import HistoryPanel


async def type_line(pilot, app, line: str) -> None:
    """Fill the entry form from a comma-separated shorthand and submit.

    The panel has one box per field now; the tests keep the compact notation
    and spread it over the boxes in the profile's order.
    """
    panel = app.query_one(EntryPanel)
    if line.startswith("/"):
        panel.set_first_value(line)
    else:
        parts = [part.strip() for part in line.split(",")]
        panel.set_values(
            {
                name: parts[index] if index < len(parts) else ""
                for index, name in enumerate(app.state.field_order)
            }
        )
    await pilot.press("enter")
    await pilot.pause()


def entry_value(app, field: str = "call") -> str:
    """Contents of one box of the entry form."""
    return app.query_one(EntryPanel).values().get(field, "")


async def wait_for(pilot, app, selector: str, tries: int = 60):
    """Wait until a widget exists, then return it.

    A screen is current as soon as it is pushed, but its children are mounted
    on a later refresh. A single pause is enough on a fast machine and not on
    a slow one, which made these tests flaky on the Windows runners.
    """
    for _ in range(tries):
        found = app.screen.query(selector)
        if found:
            return found.first()
        await pilot.pause()
    raise AssertionError(f"{selector} no apareció en {app.screen}")


async def test_logging_from_the_entry_line(operator, station):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea4abc,juan,59,57")
        await type_line(pilot, app, "dl2jkl,hans")

        assert app.query_one(HistoryPanel).qso_count == 2
        rows = QsoService.recent()
        assert [row.call for row in rows] == ["EA4ABC", "DL2JKL"]
        assert rows[0].band == app.state.band
        assert rows[0].entry_mode == "AUTO"


async def test_entry_line_is_cleared_after_logging(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea4abc,juan")
        assert entry_value(app) == ""


async def test_duplicate_warning_appears_while_typing(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea4abc,juan")
        app.query_one(EntryPanel).set_first_value("ea4abc")
        await pilot.pause()
        feedback = app.query_one("#entry").message
        assert "DUPLICADO" in feedback


async def test_yesterdays_qso_is_known_not_a_duplicate(operator):
    """Worked yesterday on the same band and mode: only known; with a contest profile's
    rule (the whole log) it is a duplicate; with "off" never."""
    import datetime as dt

    from ic705suite.apps.log.core.entry import parse

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        state = app.state
        parsed = parse("ea4abc,juan", field_order=state.field_order, mode_name=state.mode)
        QsoService.log(parsed.fields, state, qso_utc=dt.datetime.now(dt.timezone.utc)
                       .replace(tzinfo=None) - dt.timedelta(days=1))
        panel = app.query_one(EntryPanel)
        panel.set_first_value("ea4abc")
        await pilot.pause()
        feedback = app.query_one("#entry").message
        assert "DUPLICADO" not in feedback and "Conocido" in feedback
        for rule, dupe in (("log", True), ("off", False)):
            state.dupe_rule = rule
            panel.set_first_value("")
            await pilot.pause()
            panel.set_first_value("ea4abc")
            await pilot.pause()
            assert ("DUPLICADO" in app.query_one("#entry").message) is dupe


async def test_band_command_changes_the_session(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "/banda 20m")
        assert app.state.band == "20m"
        assert app.state.freq_hz == 14_250_000

        await type_line(pilot, app, "/frec 14.074")
        assert app.state.freq_hz == 14_074_000
        assert app.state.band == "20m"

        await type_line(pilot, app, "/modo cw")
        assert app.state.mode == "CW"


async def test_unknown_command_reports_an_error(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "/noexiste")
        assert "desconocido" in app.query_one("#entry").message


async def test_new_contacts_inherit_the_changed_configuration(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "/banda 20m")
        await type_line(pilot, app, "/modo cw")
        await type_line(pilot, app, "ea4abc")

        row = QsoService.recent()[-1]
        assert row.band == "20m"
        assert row.mode == "CW"
        # CW pre-fills 599 rather than the SSB 59.
        assert row.rst_sent == "599"


async def test_digital_mode_asks_for_its_extra_fields(operator):
    """``/modo DMR`` applies the mode and asks for what it needs."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "/modo DMR")

        assert app.state.mode == "DMR"
        (await wait_for(pilot, app, "#field-talkgroup")).value = "21466"
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.state.digital_data["talkgroup"] == "21466"
        await type_line(pilot, app, "ea5zz,pepe")
        assert QsoService.recent()[-1].digital_data["talkgroup"] == "21466"


async def test_line_recall_walks_previous_entries(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa,uno")
        await type_line(pilot, app, "ea2bbb,dos")

        entry = app.query_one(EntryPanel)
        entry.focus_input()
        await pilot.press("ctrl+up")
        await pilot.pause()
        assert entry.values()["call"] == "ea2bbb"
        assert entry.values()["name"] == "dos"
        await pilot.press("ctrl+up")
        await pilot.pause()
        assert entry.values()["call"] == "ea1aaa"
        await pilot.press("ctrl+down")
        await pilot.pause()
        assert entry.values()["call"] == "ea2bbb"


async def test_first_run_asks_for_a_callsign(tmp_path):
    """With no operator in the database the application must not just fail."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        assert app.state.operator_id is None
        (await wait_for(pilot, app, "#field-callsign")).value = "EA7WM"
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.state.operator_id is not None
        await type_line(pilot, app, "ea4abc")
        assert QsoService.stats().total == 1


async def test_ctrl_d_deletes_the_last_contact_from_the_entry_line(operator):
    """The common case: the callsign was just mistyped and Enter already hit."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa,uno")
        await type_line(pilot, app, "ea2bbb,dos")

        await pilot.press("ctrl+d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("s")
        await pilot.pause()

        assert [row.call for row in QsoService.recent()] == ["EA1AAA"]
        assert app.query_one(HistoryPanel).qso_count == 1


async def test_delete_key_on_the_history_removes_the_highlighted_contact(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        for call in ("ea1aaa", "ea2bbb", "ea3ccc"):
            await type_line(pilot, app, call)

        # Walk up to the oldest QSO with the arrow keys from the entry line.
        for _ in range(3):
            await pilot.press("up")
            await pilot.pause()

        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("s")
        await pilot.pause()

        assert [row.call for row in QsoService.recent()] == ["EA2BBB", "EA3CCC"]


async def test_cancelling_the_confirmation_keeps_the_contact(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        await pilot.press("ctrl+d")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert QsoService.stats().total == 1


async def test_delete_command_is_available(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        await type_line(pilot, app, "/borrar")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("s")
        await pilot.pause()
        assert QsoService.stats().total == 0


async def test_deleting_with_an_empty_log_is_harmless(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        assert "borrar" in app.query_one("#entry").message


async def test_main_history_shows_date_and_time(operator):
    from ic705suite.apps.log import i18n
    from ic705suite.apps.log.i18n import _
    from ic705suite.apps.log.tui.widgets.history import COLUMNS

    assert [_(label) for label, _width in COLUMNS[:2]] == ["INFO", "FECHA HORA"]
    assert "BAND" not in [label for label, _width in COLUMNS]
    assert "NOTES" not in [label for label, _width in COLUMNS]

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        history = app.query_one(HistoryPanel)
        stamp = QsoService.recent()[0].qso_utc
        # Spanish: day first, no seconds. Nothing to flag: INFO is empty.
        assert str(history.get_cell_at((0, 1))) == stamp.strftime("%d/%m/%y %H:%M")
        assert str(history.get_cell_at((0, 0))) == ""
    try:
        i18n.set_language("en")
        assert _(COLUMNS[1][0]) == "DATE TIME"
        app = HamrlogApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            history = app.query_one(HistoryPanel)
            # English: year first.
            assert str(history.get_cell_at((0, 1))) == stamp.strftime("%y/%m/%d %H:%M")
    finally:
        i18n.set_language("es")


async def test_repeater_and_direct_commands(operator):
    from ic705suite.apps.log.core.services import RepeaterService

    RepeaterService.create("ED7ZAE", name="Sevilla", output_hz=145_600_000, ctcss_tx="88.5")

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "/repetidor ED7ZAE")

        assert app.state.via_repeater
        assert app.state.repeater_call == "ED7ZAE"
        assert app.state.freq_hz == 145_600_000
        assert app.state.freq_tx_hz == 145_000_000
        assert app.state.band == "2m"

        await type_line(pilot, app, "ea4abc,juan")
        row = QsoService.recent()[-1]
        assert row.repeater_call == "ED7ZAE"
        assert row.freq_tx_hz == 145_000_000

        await type_line(pilot, app, "/directo")
        assert not app.state.via_repeater
        await type_line(pilot, app, "ea5bbb")
        assert QsoService.recent()[-1].repeater_call == ""


async def test_repeater_command_takes_a_callsign(operator):
    from ic705suite.apps.log.core.services import RepeaterService

    RepeaterService.create("ED7ZAF", output_hz=438_750_000, mode="DMR",
                           digital_data={"talkgroup": "214"})

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "/repetidor ED7ZAF")
        assert app.state.repeater_call == "ED7ZAF"
        assert app.state.mode == "DMR"
        assert app.state.digital_data["talkgroup"] == "214"

        await type_line(pilot, app, "/repetidor NOEXISTE")
        assert "No hay ningún repetidor" in app.query_one("#entry").message


async def test_changing_band_leaves_the_repeater_in_the_interface(operator):
    from ic705suite.apps.log.core.services import RepeaterService

    RepeaterService.create("ED7ZAE", output_hz=145_600_000)

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "/repetidor ED7ZAE")
        assert app.state.via_repeater
        await type_line(pilot, app, "/banda 20m")
        assert not app.state.via_repeater
        assert app.state.band == "20m"


async def test_typing_a_known_callsign_shows_who_it_is(operator):
    from ic705suite.apps.log.core.services import ContactService

    ContactService.create(
        "EA7WM", first_name="Manuel", city="Sevilla", dmr_id=2147001
    )

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        app.query_one(EntryPanel).set_first_value("ea7wm")
        await pilot.pause()
        feedback = app.query_one("#entry").message
        assert "Manuel" in feedback
        assert "Sevilla" in feedback
        assert "2147001" in feedback


async def test_logging_fills_the_name_from_the_address_book(operator):
    from ic705suite.apps.log.core.services import ContactService

    ContactService.create("EA7WM", first_name="Manuel", city="Sevilla")

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea7wm")
        row = QsoService.recent()[-1]
        assert row.name == "Manuel"
        assert row.qth == "Sevilla"


async def test_malformed_callsign_is_refused_in_the_interface(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "qwerty,juan")
        assert QsoService.stats().total == 0
        assert "número" in app.query_one("#entry").message

        # The override marker gets it in anyway.
        await type_line(pilot, app, "bv100!,chen")
        assert QsoService.stats().total == 1
        assert QsoService.recent()[-1].call == "BV100"


async def test_bad_callsign_is_flagged_while_typing(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        app.query_one(EntryPanel).set_first_value("ea77")
        await pilot.pause()
        assert "indicativo" in app.query_one("#entry").message.lower()


async def test_commands_never_open_a_screen(operator):
    """The main view is all there is: a bare command answers on the feedback line."""
    app = HamrlogApp()
    async with app.run_test(size=(140, 34)) as pilot:
        for command, expected in (
            ("/ayuda", "Comandos: /banda"),
            ("/banda", "Uso: /banda"),
            ("/frec", "Uso: /frec"),
            ("/modo", "Uso: /modo"),
            ("/perfil", "Uso: /perfil"),
            ("/repetidor", "Uso: /repetidor"),
            ("/config", "Comando desconocido"),
            ("/contactos", "Comando desconocido"),
        ):
            await type_line(pilot, app, command)
            assert len(app.screen_stack) == 1, command
            assert expected in app.query_one("#entry").message, command


async def test_no_shortcut_opens_a_menu(operator, station):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        assert not app.query("#menubar")
        for key in ("alt+r", "alt+b", "alt+f", "alt+m", "alt+c", "alt+e", "alt+p",
                    "alt+o", "alt+t", "ctrl+f1", "f12"):
            await pilot.press(key)
            await pilot.pause()
            assert len(app.screen_stack) == 1, key


async def test_footer_shows_how_to_quit(operator):
    from ic705suite.apps.log.i18n import _
    from ic705suite.apps.log.tui.widgets.footer import HELP_HINT, StatsFooter

    assert _(HELP_HINT) == (
        "F1 Registro · F2 Inventario · F3 Agenda · F4 Perfiles · F5 Repetidores · "
        "F8 Herramientas · F9 Configuración · Ctrl+Q Salir"
    )
    app = HamrlogApp()
    async with app.run_test(size=(200, 30)) as pilot:
        await pilot.pause()
        assert _(HELP_HINT) in str(app.query_one(StatsFooter).render())
    # Narrower: the keys of the views stay, the way out is in the help.
    app = HamrlogApp()
    async with app.run_test(size=(175, 30)) as pilot:
        await pilot.pause()
        footer = str(app.query_one(StatsFooter).render())
        assert "F5 Repetidores · F8 Herramientas · F9 Configuración" in footer
        assert "Ctrl+Q" not in footer
    # Narrower still: every key keeps a short name of where it leads, and
    # the counters close up and lose the date to make room for it.
    app = HamrlogApp()
    async with app.run_test(size=(108, 30)) as pilot:
        await pilot.pause()
        footer = str(app.query_one(StatsFooter).render())
        assert "F1 Reg F2 Inv F3 Agen F4 Perf F5 Rep F8 Herr F9 Conf" in footer
        assert "UTC" in footer


async def test_history_ends_with_the_insert_row(operator):
    from ic705suite.apps.log.tui.widgets.history import INSERT_ROW_KEY

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        history = app.query_one(HistoryPanel)
        # Present even with an empty log: it is where you write.
        assert history.row_count == 1
        assert history.on_insert_row

        await type_line(pilot, app, "ea1aaa")
        assert history.qso_count == 1
        assert history.row_count == 2
        # The cursor returns to the insert row after logging.
        assert history.on_insert_row
        key = history.coordinate_to_cell_key(history.cursor_coordinate).row_key
        assert key.value == INSERT_ROW_KEY


async def test_arrows_browse_the_history_without_moving_focus(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        for call in ("ea1aaa", "ea2bbb", "ea3ccc"):
            await type_line(pilot, app, call)

        history = app.query_one(HistoryPanel)
        assert history.on_insert_row

        await pilot.press("up")
        await pilot.pause()
        assert not history.on_insert_row
        assert history.selected_row().call == "EA3CCC"
        # The keyboard goes to the action bar, never to the history table.
        assert isinstance(app.focused, BrowseBar)
        assert not isinstance(app.focused, HistoryPanel)

        await pilot.press("up")
        await pilot.pause()
        assert history.selected_row().call == "EA2BBB"

        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        assert history.on_insert_row


async def test_history_panel_is_not_reachable_with_tab(operator):
    """The main screen has one focusable thing: the entry line."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        assert app.focused.id == "entry-call"

        await pilot.press("tab")
        await pilot.pause()
        assert not isinstance(app.focused, HistoryPanel)
        assert not app.query_one(HistoryPanel).can_focus


async def test_typing_always_logs_a_new_qso_wherever_the_cursor_is(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        await pilot.press("up")
        await pilot.pause()
        assert not app.query_one(HistoryPanel).on_insert_row

        # Typing jumps back to the insert row rather than editing.
        await type_line(pilot, app, "ea2bbb")
        assert QsoService.stats().total == 2
        assert app.query_one(HistoryPanel).on_insert_row


async def test_delete_removes_the_browsed_qso_but_still_edits_text(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        panel = app.query_one(EntryPanel)

        # In the form, Delete belongs to the text box.
        assert not panel.browsing
        box = panel.fields[0]
        box.focus()
        box.value = "abc"
        box.cursor_position = 0
        await pilot.press("delete")
        await pilot.pause()
        assert box.value == "bc"
        box.value = ""
        await pilot.pause()

        # Browsing a QSO, Delete removes it.
        await pilot.press("up")
        await pilot.pause()
        assert panel.browsing
        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("s")
        await pilot.pause()
        assert QsoService.stats().total == 0


async def test_history_direction_is_configurable(operator):
    from ic705suite.apps.log.tui.widgets.history import (
        INSERT_ROW_KEY,
        ORDER_NEWEST_FIRST,
        ORDER_OLDEST_FIRST,
    )

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        for call in ("ea1aaa", "ea2bbb"):
            await type_line(pilot, app, call)

        history = app.query_one(HistoryPanel)

        # Oldest first: the insert row is at the bottom.
        assert app.state.history_order == ORDER_OLDEST_FIRST
        assert history.get_row_at(0)[2].plain == "EA1AAA"
        assert history.coordinate_to_cell_key((2, 0)).row_key.value == INSERT_ROW_KEY

        app.state.history_order = ORDER_NEWEST_FIRST
        app._reload_history()
        await pilot.pause()

        # Newest first: the insert row moves to the top, where QSOs appear.
        assert history.coordinate_to_cell_key((0, 0)).row_key.value == INSERT_ROW_KEY
        assert history.get_row_at(1)[2].plain == "EA2BBB"
        assert history.on_insert_row


async def test_entry_form_becomes_an_action_bar_over_a_qso(operator):

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa,uno")
        panel = app.query_one(EntryPanel)

        # On the insert row it is a form of text boxes.
        assert not panel.browsing
        assert panel.query_one("#entry-fields").display
        for character in "ea2":
            await pilot.press(character)
        await pilot.pause()
        assert panel.values()["call"] == "ea2"

        # Over a QSO the action bar takes the last row of the main window;
        # the form stays in view, dimmed, with the draft in it.
        entry_height = panel.region.height
        await pilot.press("up")
        await pilot.pause()
        assert panel.browsing
        bar = app.query_one("#entry-browse", BrowseBar)
        assert bar.display
        assert isinstance(app.focused, BrowseBar)
        frame = app.query_one("#log-frame")
        assert bar in frame.walk_children()
        assert bar.region.bottom == frame.region.bottom - 1
        assert panel.query_one("#entry-fields").display
        assert all(box.disabled for box in panel.fields)
        assert panel.values()["call"] == "ea2"
        assert panel.region.height == entry_height

        # The half-typed QSO comes back untouched.
        await pilot.press("down")
        await pilot.pause()
        assert not panel.browsing
        assert panel.values()["call"] == "ea2"


async def test_letters_do_not_type_while_browsing(operator):
    """D, E and R are ordinary letters in a callsign; no silent typing."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        await pilot.press("up")
        await pilot.pause()

        panel = app.query_one(EntryPanel)
        await pilot.press("x")
        await pilot.pause()
        assert panel.values()["call"] == ""
        assert "D suprimir" in app.query_one("#entry").message


async def test_r_repeats_a_qso_into_the_entry_line(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea4abc,juan,59,57,Madrid,por la tarde")

        await pilot.press("up")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        panel = app.query_one(EntryPanel)
        history = app.query_one(HistoryPanel)
        assert panel.values() == {
            "call": "EA4ABC",
            "name": "Juan",
            "rst_sent": "59",
            "rst_rcvd": "57",
            "qth": "Madrid",
            "comment": "por la tarde",
        }
        # Back on the insert row, editable, ready for Enter.
        assert history.on_insert_row
        assert not panel.browsing

        panel.query_one("#entry-rst_sent").value = "55"
        panel.query_one("#entry-rst_rcvd").value = "59"
        await pilot.press("enter")
        await pilot.pause()

        assert QsoService.stats().total == 2
        row = QsoService.recent()[-1]
        assert row.call == "EA4ABC"
        assert row.rst_sent == "55"
        assert row.rst_rcvd == "59"
        assert row.qth == "Madrid"


async def test_repeat_uses_the_current_band_and_mode(operator):
    """Repeating means working the same station again now, not re-filing it."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea4abc,juan")
        await type_line(pilot, app, "/banda 20m")
        await type_line(pilot, app, "/modo cw")

        await pilot.press("up")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        row = QsoService.recent()[-1]
        assert row.band == "20m"
        assert row.mode == "CW"


async def test_d_deletes_the_browsed_qso(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        await type_line(pilot, app, "ea2bbb")

        await pilot.press("up")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("s")
        await pilot.pause()
        assert QsoService.stats().total == 1


async def test_e_edits_the_browsed_qso_in_the_entry_line(operator):
    """No dialog: the form comes back holding that QSO, and Enter rewrites it."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa,ana")
        await type_line(pilot, app, "ea2bbb,pepe")
        original = QsoService.recent()[0]

        await pilot.press("up", "up")
        await pilot.pause()
        await pilot.press("E")
        await pilot.pause()

        panel = app.query_one(EntryPanel)
        assert len(app.screen_stack) == 1
        assert panel.editing
        assert panel.values()["call"] == "EA1AAA"
        # The band is worked out by the application, so it has no box.
        assert "band" not in panel.values()
        assert not app.query("#entry-band")

        # The arrows stay put while the form holds this row.
        await pilot.press("down")
        await pilot.pause()
        assert app.query_one(HistoryPanel).selected_qso_id() == original.id

        app.query_one("#entry-call").value = "ea1aab"
        app.query_one("#entry-name").value = "anabel"
        app.query_one("#entry-comment").value = "corregido, con coma"
        app.query_one("#entry-freq_hz").value = "14.250"
        app.query_one("#entry-mode").value = "cw"
        await pilot.press("enter")
        await pilot.pause()

        row = QsoService.get(original.id)
        assert (row.call, row.name, row.comment) == ("EA1AAB", "Anabel", "corregido, con coma")
        assert (row.band, row.mode) == ("20m", "CW")
        assert row.freq_hz == 14_250_000
        assert row.qso_utc == original.qso_utc
        assert QsoService.stats().total == 2

        # Back on the action bar, same row, and the table shows the change.
        assert not panel.editing and panel.browsing
        history = app.query_one(HistoryPanel)
        assert history.selected_qso_id() == original.id
        assert history.selected_row().call == "EA1AAB"
        assert "EA1AAB" in str(history.get_cell(str(original.id), "CALLSIGN"))


async def test_escape_abandons_an_edit(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa,ana")
        app.query_one("#entry-call").value = "ea9"

        await pilot.press("up")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        app.query_one("#entry-name").value = "otra"
        await pilot.press("escape")
        await pilot.pause()

        panel = app.query_one(EntryPanel)
        assert not panel.editing and panel.browsing
        assert QsoService.recent()[0].name == "Ana"

        # The half-typed new QSO is still there on the way back.
        await pilot.press("down")
        await pilot.pause()
        assert entry_value(app) == "ea9"
        assert "freq_hz" not in panel.values()


async def test_an_edit_refuses_what_it_cannot_read(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa,ana")
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        app.query_one("#entry-freq_hz").value = "muchos"
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(EntryPanel).editing
        assert "Frecuencia no reconocida" in app.query_one("#entry").message
        assert QsoService.recent()[0].freq_hz == 7_130_000


async def test_an_edited_frequency_outside_every_band_clears_the_band(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa,ana")
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        app.query_one("#entry-freq_hz").value = "5.000"
        await pilot.press("enter")
        await pilot.pause()
        row = QsoService.recent()[0]
        assert (row.freq_hz, row.band) == (5_000_000, "")
        assert "fuera de las bandas" in app.query_one("#entry").message


async def test_a_name_heard_on_air_fills_a_nameless_book_entry(operator):
    from ic705suite.apps.log.core.services import ContactService

    ContactService.create("EA5ZZZ", source="import")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea5zzz,pepe")
        await type_line(pilot, app, "ea6yyy,marta")
        await type_line(pilot, app, "ea6yyy,otra")

    assert ContactService.lookup("EA5ZZZ").first_name == "Pepe"
    assert ContactService.lookup("EA6YYY").first_name == "Marta"


async def test_escape_returns_from_browsing_to_writing(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        await pilot.press("up")
        await pilot.pause()
        assert app.query_one(EntryPanel).browsing

        await pilot.press("escape")
        await pilot.pause()
        assert not app.query_one(EntryPanel).browsing
        assert app.query_one(HistoryPanel).on_insert_row


async def test_logging_says_when_the_station_is_new_to_the_book(operator):
    from ic705suite.apps.log.core.services import ContactService

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea4abc,juan")
        feedback = app.query_one("#entry").message
        assert "nuevo en la agenda" in feedback
        assert ContactService.lookup("EA4ABC") is not None

        # Second time around it is already known, so no notice.
        await type_line(pilot, app, "ea4abc,juan")
        assert "nuevo en la agenda" not in app.query_one("#entry").message


async def test_tab_walks_the_entry_fields(operator):
    """Each field has its own box; Tab and Shift+Tab move between them."""
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        panel = app.query_one(EntryPanel)
        names = [box.field_name for box in panel.fields]
        assert names == list(app.state.field_order)

        assert app.focused.id == "entry-call"
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused.id == "entry-name"
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused.id == "entry-rst_sent"

        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused.id == "entry-name"
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused.id == "entry-call"


async def test_typing_field_by_field_logs_the_qso(operator):
    """The whole point: type, Tab, type, Enter."""
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        for character in "ea4abc":
            await pilot.press(character)
        await pilot.press("tab")
        for character in "juan":
            await pilot.press(character)
        await pilot.press("tab")
        await pilot.press("5", "9")
        await pilot.press("tab")
        await pilot.press("5", "7")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        row = QsoService.recent()[-1]
        assert row.call == "EA4ABC"
        assert row.name == "Juan"
        assert row.rst_sent == "59"
        assert row.rst_rcvd == "57"
        # The form is cleared and ready for the next one.
        assert app.query_one(EntryPanel).values()["call"] == ""
        assert app.focused.id == "entry-call"


async def test_enter_logs_from_any_field(operator):
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        panel = app.query_one(EntryPanel)
        panel.set_values({"call": "ea4abc", "name": "Juan", "qth": "Madrid"})
        panel.query_one("#entry-qth").focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        assert QsoService.stats().total == 1
        assert QsoService.recent()[-1].qth == "Madrid"


async def test_a_comma_is_no_longer_a_separator(operator):
    """Commas are ordinary text now; in a callsign that makes it invalid."""
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        panel = app.query_one(EntryPanel)
        panel.set_first_value("ea4abc,juan")
        await pilot.press("enter")
        await pilot.pause()

        assert QsoService.stats().total == 0
        assert "no permitidos" in app.query_one("#entry").message

        # A comma inside a free-text field is kept verbatim.
        panel.set_values({"call": "ea4abc", "comment": "primero, y luego otro"})
        await pilot.press("enter")
        await pilot.pause()
        assert QsoService.recent()[-1].comment == "primero, y luego otro"


async def test_changing_the_field_order_rebuilds_the_boxes(operator):
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        panel = app.query_one(EntryPanel)
        app.state.field_order = ("call", "rst_sent", "rst_rcvd", "comment")
        app._refresh_status()
        await panel.ready()
        await pilot.pause()

        assert [box.field_name for box in panel.fields] == [
            "call",
            "rst_sent",
            "rst_rcvd",
            "comment",
        ]


async def test_each_window_has_its_own_frame(operator):
    """Top line, main window, detail window, entry window, status line."""
    from textual.containers import Vertical

    app = HamrlogApp()
    async with app.run_test(size=(126, 26)) as pilot:
        await pilot.pause()
        frame = app.query_one("#log-frame", Vertical)
        detail = app.query_one(DetailPanel)
        entry = app.query_one(EntryPanel)
        assert frame.border_title == "Registro"
        assert detail.border_title == "Detalle"
        assert entry.border_title == "Entrada"
        assert app.query_one(HistoryPanel) in frame.walk_children()
        assert detail not in frame.walk_children()

        order = [app.query_one("#statusline"), frame, detail, entry, app.query_one("#stats")]
        tops = [widget.region.y for widget in order]
        assert tops == sorted(tops)
        for window in (frame, detail, entry):
            assert window.styles.border_top[0] == "round", window.id
            assert window.styles.border_bottom[0] == "round", window.id
            assert window.styles.border_left[0] == "round", window.id
            assert window.styles.border_right[0] == "round", window.id


async def test_detail_shows_what_a_new_qso_would_inherit(operator, station):
    app = HamrlogApp()
    async with app.run_test(size=(126, 26)) as pilot:
        app.state.station_id = station.id
        app._refresh_status()
        await pilot.pause()

        text = str(app.query_one(DetailPanel).render())
        assert "NUEVO CONTACTO" in text
        assert operator.callsign in text
        assert app.state.band in text
        assert station.rig in text


async def test_detail_expands_the_selected_qso(operator, station):
    app = HamrlogApp()
    async with app.run_test(size=(126, 26)) as pilot:
        app.state.station_id = station.id
        app._refresh_status()
        await pilot.pause()
        await type_line(
            pilot, app, "ea4abc,Juan Garcia,59,57,Madrid,primer contacto"
        )

        detail = app.query_one(DetailPanel)
        assert "NUEVO CONTACTO" in str(detail.render())

        await pilot.press("up")
        await pilot.pause()
        text = str(detail.render())

        assert "EA4ABC" in text
        assert "Juan Garcia" in text
        assert "Madrid" in text
        assert "AUTO" in text
        # Things the table has no room for.
        assert operator.callsign in text
        assert station.name in text
        assert "primer contacto" in text

        # Coming back to the insert row restores the session summary.
        await pilot.press("down")
        await pilot.pause()
        assert "NUEVO CONTACTO" in str(detail.render())


async def test_detail_shows_both_frequencies_over_a_repeater(operator):
    from ic705suite.apps.log.core.services import RepeaterService

    RepeaterService.create("ED7ZAE", output_hz=145_600_000, ctcss_tx="88.5")

    app = HamrlogApp()
    async with app.run_test(size=(126, 26)) as pilot:
        await type_line(pilot, app, "/repetidor ED7ZAE")
        await type_line(pilot, app, "ea5zz,Pepe")

        await pilot.press("up")
        await pilot.pause()
        text = str(app.query_one(DetailPanel).render())
        assert "ED7ZAE" in text
        assert "145.600" in text
        assert "TX 145.000" in text


async def test_detail_marks_a_manual_qso(operator):
    import datetime as dt

    from ic705suite.apps.log.core.services import QsoService as Service

    app = HamrlogApp()
    async with app.run_test(size=(126, 26)) as pilot:
        Service.log(
            {"call": "EA3MNO", "name": "Laia"},
            app.state,
            qso_utc=dt.datetime(2026, 1, 15, 12, 30),
        )
        app._reload_history()
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()
        text = str(app.query_one(DetailPanel).render())
        assert "MANUAL" in text
        assert "2026-01-15 12:30:00" in text


async def test_frequency_format_applies_across_the_interface(operator):
    from ic705suite.apps.log.core import units
    from ic705suite.apps.log.tui.widgets.statusline import StatusLine

    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await type_line(pilot, app, "/banda 40m")
        await type_line(pilot, app, "ea4abc,Juan")

        # Default: megahertz, dot, no grouping.
        assert "7.130 MHz" in str(app.query_one(StatusLine).render())
        assert "7.130 MHz" in str(app.query_one(DetailPanel).render())

        # Switching the format redraws everything.
        app.state.freq_unit = units.UNIT_HZ
        app.state.decimal_separator = ","
        app.state.thousands_separator = "."
        app._refresh_status()
        app._reload_history()
        await pilot.pause()

        assert "7.130.000 Hz" in str(app.query_one(StatusLine).render())
        assert "7.130.000 Hz" in str(app.query_one(DetailPanel).render())


async def test_frequency_command_reads_the_configured_unit(operator):
    from ic705suite.apps.log.core import units

    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        # Megahertz by default, so a bare number is megahertz.
        await type_line(pilot, app, "/frec 14.250")
        assert app.state.freq_hz == 14_250_000

        # A unit written by hand wins.
        await type_line(pilot, app, "/frec 7130 K")
        assert app.state.freq_hz == 7_130_000

        app.state.freq_unit = units.UNIT_KHZ
        app.state.apply_frequency_format()
        await type_line(pilot, app, "/frec 21300")
        assert app.state.freq_hz == 21_300_000


async def test_frequency_format_survives_a_restart(operator):
    from ic705suite.apps.log.core.services import SettingsService

    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        app.state.freq_unit = "kHz"
        app.state.thousands_separator = " "
        SettingsService.save_state(app.state)
        await pilot.pause()

    restored = SettingsService.load_state()
    assert restored.freq_unit == "kHz"
    assert restored.frequency_format.format(7_130_000) == "7 130 kHz"


async def test_dialogs_are_drawn_in_the_log_area(operator):
    """No floating boxes: a dialog covers exactly the log, the rest stays in view."""
    app = HamrlogApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")

        for command, screen_type in (("/deshacer", ConfirmScreen), ("/modo DMR", FormScreen)):
            await type_line(pilot, app, command)
            await pilot.pause()
            assert isinstance(app.screen, screen_type), command
            await pilot.pause()
            frame = app.query_one("#log-frame").region
            assert app.screen.query_one(".modal").region == frame, command
            await pilot.press("escape")
            await pilot.pause()


def test_the_application_runs_without_the_mouse(monkeypatch):
    """Keyboard only: the terminal keeps the mouse for selecting text."""
    from ic705suite.apps.log import cli

    calls = {}
    monkeypatch.setattr(HamrlogApp, "run", lambda self, **kwargs: calls.update(kwargs))
    cli.main([])
    assert calls == {"mouse": False}


async def test_no_help_line_under_the_entry(operator):
    """Neither the log, the other views, editing nor a dialog show key help."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()

        def screen_text() -> str:
            return app.export_screenshot().replace("&#160;", " ")

        assert not app.query("#entry-hint")
        assert "Shift+Tab" not in screen_text()

        await type_line(pilot, app, "ea1aaa")
        await type_line(pilot, app, "/deshacer")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert "S sí" not in screen_text()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()
        await pilot.press("E")
        await pilot.pause()
        assert app.query_one(EntryPanel).editing
        assert "Esc" not in screen_text()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("f2")
        await pilot.pause()
        assert "Alt+" not in screen_text()


async def test_messages_go_on_the_entry_frame(operator):
    """No row of its own: a message sits on the entry's bottom edge."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        entry = app.query_one(EntryPanel)
        height = entry.region.height
        assert not entry.border_subtitle

        await type_line(pilot, app, "/ayuda")
        await pilot.pause()
        assert "/band" in entry.message
        assert "/band" in str(entry.border_subtitle)
        assert entry.region.height == height

        entry.feedback("")
        await pilot.pause()
        assert entry.message == ""
        assert not entry.border_subtitle


async def test_open_panel_follows_the_main_window(operator):
    """When the entry window grows, the main window and its panel shrink."""
    app = HamrlogApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await type_line(pilot, app, "ea1aaa")
        await type_line(pilot, app, "/deshacer")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)

        app.query_one(EntryPanel).styles.height = 8
        await pilot.pause()
        await pilot.pause()
        frame = app.query_one("#log-frame").region
        assert app.screen.query_one(".modal").region == frame


async def test_a_shared_log_shows_what_others_log(operator, station):
    """The log in a database shared with others (the core logs FT8, another client logs):
    their QSOs appear by themselves within seconds, and at once with Ctrl+R."""
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await type_line(pilot, app, "ea4abc,juan,59,57")
        panel = app.query_one(HistoryPanel)
        assert panel.qso_count == 1
        state = app.state
        QsoService.log({"call": "F5RRS", "mode": "FT8", "band": "20m"}, state)   # elsewhere
        app._watch_log()                                   # what the timer does
        await pilot.pause()
        assert panel.qso_count == 2
        QsoService.log({"call": "MW3FLI", "mode": "FT8", "band": "20m"}, state)
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert panel.qso_count == 3
        app._watch_log()                                   # nothing new: no reload
        assert panel.qso_count == 3
