"""The card of a QSO: V while browsing the log, floating over it."""

from __future__ import annotations

from ic705suite.apps.log.core.services import (
    AntennaService,
    ContactService,
    EquipmentService,
    PowerSupplyService,
    QsoService,
    StationService,
    StationTypeService,
)
from ic705suite.apps.log.core.state import SessionState
from ic705suite.apps.log.tui.app import HamrlogApp
from ic705suite.apps.log.tui.screens.card import QsoCardScreen, render_card


def log_with_setup(operator) -> int:
    radio = StationService.create(
        "TM-241E", type_ids=StationTypeService.resolve("VHF"), power_w=50
    )
    antenna = AntennaService.create("Vertical X50", ["2m", "70cm"])
    supply = PowerSupplyService.create("Fuente 30 A", voltage_v=13.8, current_a=30)
    setup = EquipmentService.create("TM-241E casa", [radio.id], [antenna.id], [supply.id])
    ContactService.create("EA7LFZ", first_name="Jose Manuel", city="Sevilla", dmr_id=2147123)
    state = SessionState(operator_id=operator.id, equipment_id=setup.id)
    state.set_band("2m")
    state.set_mode("FM")
    row = QsoService.log(
        {"call": "EA7LFZ", "name": "Jose Manuel", "qth": "Triana", "comment": "buena señal"},
        state,
    )
    return row.id


def test_the_card_holds_the_station_worked_ours_and_the_setup(operator):
    qso_id = log_with_setup(operator)
    card = QsoService.card(qso_id)
    assert card.setup_name == "TM-241E casa"
    assert [part.name for part in card.radios] == ["TM-241E"]
    assert card.radios[0].details == "50 W · VHF"
    assert card.antennas[0].details == "2m, 70cm"
    assert card.supplies[0].details == "13.8 V · 30 A"
    assert card.contact.dmr_id == 2147123

    text = render_card(card).plain
    for expected in (
        "CONTACTO", "EA7LFZ", "Jose Manuel", "Triana", "2147123",
        "MI ESTACIÓN", "EA7WM", "EQUIPO «TM-241E casa»",
        "Emisoras", "TM-241E", "Antenas", "Vertical X50", "Fuentes", "13.8 V",
        "buena señal",
    ):
        assert expected in text, expected


def test_a_qso_without_a_setup_says_so(operator):
    state = SessionState(operator_id=operator.id)
    state.set_band("40m")
    row = QsoService.log({"call": "EA4ABC"}, state)
    text = render_card(QsoService.card(row.id)).plain
    assert "Este QSO no tiene equipo." in text
    assert QsoService.card(10_000) is None


async def test_v_opens_the_card_in_the_middle_of_the_log(operator):
    log_with_setup(operator)
    app = HamrlogApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, QsoCardScreen)

        # Smaller than the log, inside it and centred: the log shows around it.
        frame = app.screen_stack[0].query_one("#log-frame").region
        card = app.screen.query_one(".modal").region
        assert card.width < frame.width and card.height < frame.height
        assert frame.contains_region(card)
        assert abs((card.x - frame.x) - (frame.right - card.right)) <= 1
        assert abs((card.y - frame.y) - (frame.bottom - card.bottom)) <= 1
        assert app.screen.query_one(".modal").border_title == "EA7LFZ"

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        # Back on the same QSO, browsing.
        assert app.query_one("#entry").browsing


async def test_v_in_another_view_explains_itself(operator):
    StationService.create("IC-705")
    app = HamrlogApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert "ficha" in app.query_one("#entry").message
