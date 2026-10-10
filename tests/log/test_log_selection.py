"""Selecting QSOs in the log and editing several at once."""

from __future__ import annotations

from ic705suite.apps.log.core.services import (
    AntennaService,
    EquipmentService,
    QsoService,
    StationService,
    StationTypeService,
)
from ic705suite.apps.log.core.state import SessionState
from ic705suite.apps.log.tui.app import HamrlogApp
from ic705suite.apps.log.tui.widgets.entry import EntryPanel
from ic705suite.apps.log.tui.widgets.history import HistoryPanel


def log_three(operator) -> list[int]:
    state = SessionState(operator_id=operator.id)
    state.set_band("40m")
    state.set_mode("SSB")
    for call in ("EA1AAA", "EA2BBB", "EA3CCC"):
        QsoService.log({"call": call, "rst_sent": "57", "rst_rcvd": "58"}, state)
    return [row.id for row in QsoService.recent()]


def info(app, qso_id: int) -> str:
    return str(app.query_one(HistoryPanel).get_cell(str(qso_id), "INFO"))


def feedback(app) -> str:
    return app.query_one("#entry").message


async def test_space_marks_and_unmarks_the_browsed_qso(operator):
    ids = log_three(operator)
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert history.marked == {ids[-1]}
        assert info(app, ids[-1]) == "S"
        assert "1 QSO seleccionado" in feedback(app)
        # Space never types while browsing.
        assert not any(app.query_one(EntryPanel).pending_values.values())

        await pilot.press("space")
        await pilot.pause()
        assert history.marked == set()
        assert info(app, ids[-1]) == ""


async def test_ctrl_a_selects_and_unselects_everything(operator):
    ids = log_three(operator)
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        await pilot.press("up", "space")  # one marked: Ctrl+A completes the set
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert history.marked == set(ids)
        assert all(info(app, qso_id) == "S" for qso_id in ids)
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert history.marked == set()


async def test_editing_several_qsos_offers_only_frequency_mode_and_setup(operator):
    ids = log_three(operator)
    radio = StationService.create("IC-705", type_ids=StationTypeService.resolve("HF"))
    antenna = AntennaService.create("Dipolo", ["20m"])
    EquipmentService.create("HF casa", [radio.id], [antenna.id])
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        panel = app.query_one(EntryPanel)
        assert panel.bulk
        assert list(panel.values()) == ["freq_hz", "mode", "equipment"]
        # Shared values come filled in; the call box is out of reach.
        assert panel.values()["mode"] == "SSB"
        assert app.query_one("#entry-call").disabled

        app.query_one("#entry-freq_hz").value = "14.200"
        app.query_one("#entry-mode").value = "cw"
        app.query_one("#entry-equipment").value = "hf casa"
        await pilot.press("enter")
        await pilot.pause()

        for qso_id in ids:
            row = QsoService.get(qso_id)
            assert (row.freq_hz, row.band, row.mode) == (14_200_000, "20m", "CW")
            assert row.equipment_name == "HF casa"
            assert not row.equipment_mismatch
            # Everything else stays as it was.
            assert (row.rst_sent, row.rst_rcvd) == ("57", "58")
        assert "3 QSO modificados" in feedback(app)
        assert not panel.editing
        # Only browsing dims the form now; back on the insert row it is whole.
        panel.set_browsing(False)
        await pilot.pause()
        assert not app.query_one("#entry-call").disabled


async def test_an_empty_box_leaves_each_qso_as_it_was(operator):
    ids = log_three(operator)
    QsoService.update(ids[0], {"mode": "FM"})
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a", "up", "e")
        await pilot.pause()
        panel = app.query_one(EntryPanel)
        # The modes differ, so the box starts empty.
        assert panel.values()["mode"] == ""
        app.query_one("#entry-freq_hz").value = "7.100"
        await pilot.press("enter")
        await pilot.pause()
        modes = [QsoService.get(qso_id).mode for qso_id in ids]
        assert modes == ["FM", "SSB", "SSB"]
        assert all(QsoService.get(qso_id).freq_hz == 7_100_000 for qso_id in ids)


async def test_one_selected_qso_is_edited_as_usual(operator):
    ids = log_three(operator)
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await pilot.press("up", "space", "e")
        await pilot.pause()
        panel = app.query_one(EntryPanel)
        assert panel.editing and not panel.bulk
        assert panel.values()["call"] == QsoService.get(ids[-1]).call


async def test_the_list_shows_the_setup_and_the_detail_the_reports(operator):
    ids = log_three(operator)
    radio = StationService.create("IC-705")
    EquipmentService.create("Portátil", [radio.id])
    QsoService.update(ids[-1], {"equipment_id": EquipmentService.list_all()[0].id})
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        assert "SETUP" in history.columns and "RST" not in history.columns
        assert str(history.get_cell(str(ids[-1]), "SETUP")) == "Portátil"
        await pilot.press("up")
        await pilot.pause()
        assert "57/58" in str(app.query_one("#detail").render())


async def test_a_selected_qso_with_an_error_reads_se(operator):
    ids = log_three(operator)
    radio = StationService.create("TM-241E", type_ids=StationTypeService.resolve("VHF"))
    EquipmentService.create("2 m", [radio.id])
    QsoService.update(ids[-1], {"equipment_id": EquipmentService.list_all()[0].id})
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        assert info(app, ids[-1]) == "E"
        await pilot.press("up", "space")
        await pilot.pause()
        assert info(app, ids[-1]) == "SE"


async def test_a_name_that_differs_from_the_book_is_flagged_d(operator):
    from ic705suite.apps.log.core.services import ContactService

    ContactService.create("EA7LFZ", first_name="Luis", last_name="Gómez")
    state = SessionState(operator_id=operator.id)
    state.set_band("40m")
    same = QsoService.log({"call": "EA7LFZ", "name": "luis"}, state)
    full = QsoService.log({"call": "EA7LFZ", "name": "Luis Gómez"}, state)
    other = QsoService.log({"call": "EA7LFZ/P", "name": "Pepe"}, state)
    unnamed = QsoService.log({"call": "EA7LFZ"}, state)
    stranger = QsoService.log({"call": "EA1ZZZ", "name": "Ana"}, state)
    assert [QsoService.get(r.id).name_drift for r in (same, full, other, unnamed)] == [
        False, False, True, False
    ]
    # Not in the book (or added by this very QSO): nothing to differ from.
    assert not QsoService.get(stranger.id).name_drift
    assert QsoService.get(other.id).book_name == "Luis Gómez"

    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        assert info(app, other.id) == "d"
        assert info(app, same.id) == ""
        history = app.query_one(HistoryPanel)
        while history.selected_qso_id() != other.id:
            await pilot.press("up")
            await pilot.pause()
        assert "en la agenda es «Luis Gómez»" in str(app.query_one("#detail").render())


async def test_logging_a_different_name_says_so(operator):
    from ic705suite.apps.log.core.services import ContactService

    ContactService.create("EA7LFZ", first_name="Luis")
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        app.query_one(EntryPanel).set_values({"call": "ea7lfz", "name": "Pepe"})
        await pilot.press("enter")
        await pilot.pause()
        assert "en la agenda es «Luis»" in feedback(app)
        row = QsoService.recent()[-1]
        assert info(app, row.id) == "d"
        # The book keeps its own name.
        assert ContactService.lookup("EA7LFZ").first_name == "Luis"


async def test_fixing_the_contact_clears_the_d_on_return_to_the_log(operator):
    """The log rows are rebuilt when coming back from another view."""
    from ic705suite.apps.log.core.services import ContactService

    ContactService.create("EA7LFZ", first_name="Jose")
    state = SessionState(operator_id=operator.id)
    state.set_band("40m")
    row = QsoService.log({"call": "EA7LFZ", "name": "Jose Manuel"}, state)
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        assert info(app, row.id) == "d"
        await pilot.press("f3")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("up", "e")
        await pilot.pause()
        app.query_one("#entry-last_name").value = "Manuel"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()
        assert info(app, row.id) == ""
        assert app.query_one(HistoryPanel).on_insert_row
