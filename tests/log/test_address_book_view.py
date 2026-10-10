"""The address book view (F3): list, search, and the two rows of boxes."""

from __future__ import annotations

import pytest

from ic705suite.apps.log.core.services import ContactService, QsoService, ServiceError
from ic705suite.apps.log.core.state import SessionState
from ic705suite.apps.log.tui.address_book import LIMIT
from ic705suite.apps.log.tui.app import HamrlogApp
from ic705suite.apps.log.tui.screens.base import ConfirmScreen
from ic705suite.apps.log.tui.widgets.entry import EntryPanel
from ic705suite.apps.log.tui.widgets.history import HistoryPanel
from ic705suite.apps.log.tui.widgets.items import InventoryView, ItemTable


async def open_book(pilot) -> None:
    await pilot.press("f3")
    await pilot.pause()
    await pilot.pause()


def feedback(app) -> str:
    return app.query_one("#entry").message


def header(app) -> str:
    return str(app.query_one("#inventory-tabs").render())


async def command(pilot, app, text: str) -> None:
    app.query_one(EntryPanel).set_first_value(text)
    await pilot.press("enter")
    await pilot.pause()


# ----------------------------------------------------------------- services --
def test_editing_a_contact_keeps_callsigns_and_dmr_ids_unique():
    first = ContactService.create("EA7WM", dmr_id=2147001)
    ContactService.create("EA4ABC", dmr_id=2141234)
    with pytest.raises(ServiceError, match="ya está en la agenda"):
        ContactService.update(first.id, callsign="EA4ABC/P")
    with pytest.raises(ServiceError, match="ya es de"):
        ContactService.update(first.id, dmr_id=2141234)
    with pytest.raises(ServiceError, match="vacío"):
        ContactService.update(first.id, callsign="  ")
    updated = ContactService.update(first.id, callsign="EA7WM/P", gridsquare="im76")
    assert (updated.callsign, updated.gridsquare) == ("EA7WM/P", "IM76")


def test_the_search_count_matches_the_search():
    for number in range(5):
        ContactService.create(f"EA7AA{number}", city="Sevilla")
    ContactService.create("EA4ZZZ", city="Madrid", state="Madrid")
    assert ContactService.count() == 6
    assert ContactService.count("sevilla") == 5
    assert ContactService.count("madrid") == 1
    assert len(ContactService.search("madrid")) == 1


# ---------------------------------------------------------------- interface --
async def test_f3_shows_the_address_book_in_place_of_the_log(operator):
    ContactService.create("EA7WM", first_name="Manuel", city="Sevilla")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_book(pilot)
        assert app.query_one(InventoryView).display
        assert not app.query_one(HistoryPanel).display
        assert app.query_one("#log-frame").border_title == "Agenda"
        assert len(app.screen_stack) == 1
        # No tabs: a single list, with its search and how many there are.
        assert "BUSCAR" in header(app) and "1 contactos" in header(app)
        assert app.query_one(ItemTable).item_count == 1

        panel = app.query_one(EntryPanel)
        # Two rows of boxes, both part of the form.
        assert list(panel.values()) == [
            "call", "first_name", "last_name", "dmr_id", "gridsquare",
            "city", "state", "country", "email", "notes",
        ]
        assert app.query_one("#entry-second").display

        # F3 does nothing here, F2 opens the inventory and F3 comes back;
        # F1 goes back to the log.
        await pilot.press("f3")
        await pilot.pause()
        assert app.query_one("#log-frame").border_title == "Agenda"
        await pilot.press("f2")
        await pilot.pause()
        await pilot.pause()
        assert app.query_one("#log-frame").border_title == "Inventario"
        await pilot.press("f3")
        await pilot.pause()
        await pilot.pause()
        assert app.query_one("#log-frame").border_title == "Agenda"
        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(HistoryPanel).display
        assert not app.query_one("#entry-second").display


async def test_a_contact_is_written_in_both_rows(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_book(pilot)
        app.query_one(EntryPanel).set_values({
            "call": "ea4abc", "first_name": "Juan", "last_name": "Pérez",
            "dmr_id": "2141234", "gridsquare": "in80", "city": "Madrid",
            "state": "Madrid", "country": "españa", "notes": "QSL directa",
        })
        await pilot.press("enter")
        await pilot.pause()
        contact = ContactService.lookup("EA4ABC")
        assert (contact.full_name, contact.dmr_id, contact.gridsquare) == (
            "Juan Pérez", 2141234, "IN80"
        )
        # Typed in Spanish, stored in English like the log.
        assert contact.country == "Spain"
        assert "EA4ABC" in feedback(app)
        table = app.query_one(ItemTable)
        assert table.item_count == 1
        assert str(table.get_row_at(0)[5]) == "España"

        app.query_one(EntryPanel).set_values({"call": "ea9", "dmr_id": "abc"})
        await pilot.press("enter")
        await pilot.pause()
        assert "número" in feedback(app)


async def test_e_edits_and_d_deletes_a_contact(operator):
    ContactService.create("EA7WM", first_name="Manuel")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_book(pilot)
        await pilot.press("up", "e")
        await pilot.pause()
        panel = app.query_one(EntryPanel)
        assert panel.editing and panel.values()["call"] == "EA7WM"
        assert app.query_one("#entry-second").display
        app.query_one("#entry-city").value = "Sevilla"
        await pilot.press("enter")
        await pilot.pause()
        assert ContactService.lookup("EA7WM").city == "Sevilla"
        assert panel.browsing and not panel.editing
        # Back on the bar, the form stays in view but out of reach.
        assert app.query_one("#entry-city").disabled

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("s")
        await pilot.pause()
        assert ContactService.count() == 0


async def test_search_narrows_a_long_book(operator):
    for number in range(LIMIT + 20):
        ContactService.create(f"EA1{number:04d}", city="Lugo", source="import")
    ContactService.create("EA7WM", first_name="Manuel", city="Sevilla")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_book(pilot)
        table = app.query_one(ItemTable)
        # Never more than LIMIT rows at once, and the header says so.
        assert table.item_count == LIMIT
        assert f"{LIMIT} de {LIMIT + 21}" in header(app)

        await command(pilot, app, "/buscar sevilla")
        assert table.item_count == 1
        assert "sevilla" in header(app)
        assert "1 encontrados" in feedback(app)

        await command(pilot, app, "/buscar")
        assert table.item_count == LIMIT


async def test_the_detail_tells_how_many_qsos_there_are(operator):
    ContactService.create("EA4ABC", first_name="Juan", dmr_id=2141234)
    state = SessionState(operator_id=operator.id)
    state.set_band("40m")
    QsoService.log({"call": "EA4ABC"}, state)
    QsoService.log({"call": "EA4ABC/P"}, state)
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_book(pilot)
        await pilot.press("up")
        await pilot.pause()
        detail = str(app.query_one("#detail").render())
        assert "DMR 2141234" in detail
        assert "2 QSO en el registro" in detail


async def test_search_only_works_in_the_address_book(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        await command(pilot, app, "/buscar icom")
        assert "agenda" in feedback(app)
