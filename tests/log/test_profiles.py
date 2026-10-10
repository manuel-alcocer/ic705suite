"""Profiles (F4): what every new QSO inherits, the ten Ctrl+digit keys and
the default one activated on start."""

from __future__ import annotations

import pytest

from ic705suite.apps.log.core.services import (
    EquipmentService,
    OperatorService,
    ProfileService,
    QsoService,
    RepeaterService,
    ServiceError,
    SettingsService,
    StationService,
    StationTypeService,
)
from ic705suite.apps.log.core.state import SessionState
from ic705suite.apps.log.db.models import Qso
from ic705suite.apps.log.db.session import session_scope
from ic705suite.apps.log.tui.app import HamrlogApp
from ic705suite.apps.log.tui.profiles import ProfileKind, format_digital, parse_digital
from ic705suite.apps.log.tui.screens.base import ConfirmScreen
from ic705suite.apps.log.tui.widgets.entry import EntryPanel
from ic705suite.apps.log.tui.widgets.history import HistoryPanel
from ic705suite.apps.log.tui.widgets.items import InventoryView, ItemTable
from ic705suite.apps.log.tui.widgets.statusline import StatusLine


@pytest.fixture
def setup(station):
    """A setup with the HF radio of the shared fixture and its antenna."""
    return EquipmentService.create(
        name="Casa",
        station_ids=[station.id],
        antenna_ids=[station.antennas[0].id],
        supply_ids=[],
        notes="",
    )


def stored(qso_id: int) -> Qso:
    """The QSO as stored, with the columns a log row does not show."""
    with session_scope() as session:
        return session.get(Qso, qso_id)


def feedback(app) -> str:
    return app.query_one("#entry").message


async def open_profiles(pilot) -> None:
    await pilot.press("f4")
    await pilot.pause()
    await pilot.pause()


async def fill(pilot, app, values: dict[str, str]) -> None:
    for box in app.query_one(EntryPanel).fields:
        box.value = values.get(box.field_name, "")
    await pilot.press("enter")
    await pilot.pause()


# ----------------------------------------------------------------- services --
def test_a_profile_takes_its_band_from_the_frequency(operator, setup):
    profile = ProfileService.save(
        None, name="40m casa", slot=1, operator_id=operator.id, equipment_id=setup.id,
        freq_hz=7_100_000, mode="SSB", power_w=50,
    )
    assert (profile.band, profile.slot, profile.equipment.name) == ("40m", 1, "Casa")
    assert ProfileService.get_by_slot(1).id == profile.id


def test_a_key_moves_to_the_profile_that_takes_it():
    first = ProfileService.save(None, name="uno", slot=3)
    second = ProfileService.save(None, name="dos", slot=3)
    assert ProfileService.get(first.id).slot is None
    assert ProfileService.get_by_slot(3).id == second.id
    # Saving a profile again keeps its own key.
    ProfileService.save(second.id, name="dos", slot=3)
    assert ProfileService.get_by_slot(3).id == second.id


def test_profile_names_are_unique_and_keys_go_from_0_to_9():
    ProfileService.save(None, name="uno")
    with pytest.raises(ServiceError, match="Ya existe el perfil"):
        ProfileService.save(None, name="uno")
    with pytest.raises(ServiceError, match="del 0 al 9"):
        ProfileService.save(None, name="otro", slot=10)
    with pytest.raises(ServiceError, match="no puede estar vacío"):
        ProfileService.save(None, name="  ")


def test_main_profiles_are_listed_first_by_their_key():
    ProfileService.save(None, name="a sin tecla")
    ProfileService.save(None, name="z", slot=0)
    ProfileService.save(None, name="b", slot=5)
    assert [p.name for p in ProfileService.list_all()] == ["z", "b", "a sin tecla"]


def test_activating_a_profile_sets_what_new_qsos_inherit(operator, setup):
    other = OperatorService.create("EA7XYZ")
    profile = ProfileService.save(
        None, name="Casa", operator_id=other.id, equipment_id=setup.id,
        freq_hz=7_150_000, mode="CW", power_w=50,
    )
    state = SessionState(operator_id=operator.id)
    ProfileService.apply_to_state(profile.id, state)
    assert state.profile_id == profile.id and state.profile_name == "Casa"
    assert (state.operator_id, state.band, state.freq_hz, state.mode, state.power_w) == (
        other.id, "40m", 7_150_000, "CW", 50
    )
    assert state.equipment_id == setup.id
    assert state.station_id == setup.stations[0].id

    row = QsoService.log({"call": "EA1AAA"}, state)
    assert (row.operator_callsign, row.freq_hz, row.mode) == ("EA7XYZ", 7_150_000, "CW")
    assert row.equipment_name == "Casa" and not row.equipment_mismatch
    assert stored(row.id).power_w == 50


def test_a_qso_takes_the_radio_of_the_setup_that_suits_its_frequency(operator):
    types = {t.name: t.id for t in StationTypeService.list_all()}
    hf = StationService.create("HF", rig="IC-7300", type_ids=[types["HF"]])
    vhf = StationService.create("VHF", rig="FT-2980", type_ids=[types["VHF"]])
    both = EquipmentService.create(
        name="Todo", station_ids=[hf.id, vhf.id], antenna_ids=[], supply_ids=[], notes=""
    )
    profile = ProfileService.save(None, name="2m", equipment_id=both.id, freq_hz=145_500_000)
    state = SessionState(operator_id=operator.id)
    ProfileService.apply_to_state(profile.id, state)
    row = QsoService.log({"call": "EA1AAA"}, state)
    assert stored(row.id).station_id == vhf.id


def test_without_a_power_the_qso_keeps_the_one_typed(state):
    state.power_w = 5
    assert stored(QsoService.log({"call": "EA1AAA"}, state).id).power_w == 5
    typed = QsoService.log({"call": "EA1AAB", "power_w": 100}, state)
    assert stored(typed.id).power_w == 100


def test_deleting_a_setup_leaves_its_profiles_without_it(setup):
    profile = ProfileService.save(None, name="Casa", equipment_id=setup.id)
    EquipmentService.delete(setup.id)
    assert ProfileService.get(profile.id).equipment_id is None


def test_the_default_can_be_cleared():
    profile = ProfileService.save(None, name="uno")
    ProfileService.set_default(profile.id)
    ProfileService.clear_default()
    assert ProfileService.get_default() is None


# --------------------------------------------------------- the entry boxes --
def test_digital_values_accept_short_and_long_names():
    assert parse_digital("TG=214, cc=1", "DMR") == {"talkgroup": "214", "color_code": "1"}
    assert parse_digital("talkgroup=91; Repetidor=ED7ZAE", "DMR") == {
        "talkgroup": "91", "repeater": "ED7ZAE"
    }
    assert parse_digital("DG-ID=10, room=12345", "C4FM") == {"dg_id": "10", "room": "12345"}
    assert format_digital({"talkgroup": "214", "color_code": "1"}) == "TG=214, CC=1"


def test_digital_values_the_mode_does_not_use_are_refused():
    with pytest.raises(ServiceError, match="SSB no usa «TG»"):
        parse_digital("TG=214", "SSB")
    with pytest.raises(ServiceError, match="NOMBRE=VALOR"):
        parse_digital("TG 214", "DMR")


def test_the_form_creates_the_operator_and_follows_the_repeater():
    RepeaterService.create("ED7ZAE", output_hz=438_200_000, shift_hz=-7_600_000, mode="DMR",
                           digital_data={"talkgroup": "214"})
    kind = ProfileKind()
    name = kind.save(None, {"name": "Rptr", "operator": "ea7new", "repeater": "ed7zae"})
    profile = next(p for p in ProfileService.list_all() if p.name == name)
    assert profile.operator.callsign == "EA7NEW"
    assert (profile.freq_hz, profile.band, profile.mode) == (438_200_000, "70cm", "DMR")
    assert profile.digital_data == {"talkgroup": "214"}
    assert kind.values(profile.id)["repeater"] == "ED7ZAE"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"name": "x", "slot": "12"}, "del 0 al 9"),
        ({"name": "x", "equipment": "Nada"}, "No existe el equipo «Nada»"),
        ({"name": "x", "repeater": "ED0XXX"}, "ED0XXX"),
        ({"name": "x", "freq_hz": "abc"}, "Frecuencia no reconocida"),
        ({"name": "x", "mode": "ZZZ"}, "Modo desconocido"),
        ({"name": "x", "power_w": "mucha"}, "número entero de vatios"),
    ],
)
def test_the_form_explains_what_it_cannot_read(values, message):
    with pytest.raises(ServiceError, match=message):
        ProfileKind().save(None, values)


# ---------------------------------------------------------------- interface --
async def test_f4_lists_the_profiles_in_place_of_the_log(operator):
    ProfileService.save(None, name="40m casa", slot=1, freq_hz=7_100_000, mode="SSB")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_profiles(pilot)
        assert app.query_one(InventoryView).display
        assert not app.query_one(HistoryPanel).display
        assert app.query_one("#log-frame").border_title == "Perfiles"
        assert len(app.screen_stack) == 1
        assert app.query_one(ItemTable).item_count == 1
        assert list(app.query_one(EntryPanel).values()) == [
            "slot", "name", "operator", "equipment",
            "freq_hz", "mode", "power_w", "repeater", "digital",
        ]


async def test_a_profile_is_created_and_activated_with_enter(operator, setup):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_profiles(pilot)
        await fill(pilot, app, {
            "slot": "2", "name": "Casa", "equipment": "casa", "freq_hz": "14.200",
            "mode": "usb", "power_w": "100 W",
        })
        assert "dado de alta" in feedback(app)
        profile = ProfileService.get_by_slot(2)
        assert (profile.mode, profile.band, profile.power_w) == ("USB", "20m", 100)

        await pilot.press("up")
        await pilot.press("enter")
        await pilot.pause()
        assert app.state.profile_id == profile.id
        assert app.state.freq_hz == 14_200_000
        assert "activo" in feedback(app)
        assert app.query_one(StatusLine).profile == "Casa"
        assert app.query_one(ItemTable).selected_item().cells[0].startswith("▶")

        # Back in the log, a callsign is logged with the profile.
        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()
        await fill(pilot, app, {"call": "EA1AAA"})
    row = QsoService.recent()[-1]
    assert (row.freq_hz, row.mode, row.equipment_name) == (14_200_000, "USB", "Casa")
    assert stored(row.id).power_w == 100


async def test_ctrl_and_a_digit_activate_a_main_profile_from_the_log(operator):
    ProfileService.save(None, name="DMR", slot=3, freq_hz=438_200_000, mode="DMR",
                        digital_data={"talkgroup": "214"})
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+3")
        await pilot.pause()
        assert (app.state.profile_name, app.state.mode) == ("DMR", "DMR")
        assert app.state.digital_data == {"talkgroup": "214"}

        await pilot.press("ctrl+7")
        await pilot.pause()
        assert "Ctrl+7" in feedback(app)
        assert app.state.profile_name == "DMR"

        # The command does the same, by key or by name.
        app.state.leave_profile()
        app.query_one(EntryPanel).set_first_value("/perfil 3")
        await pilot.press("enter")
        await pilot.pause()
        assert app.state.profile_name == "DMR"


async def test_changing_the_band_by_hand_leaves_the_profile(operator):
    ProfileService.save(None, name="DMR", slot=3, freq_hz=438_200_000, mode="DMR")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("ctrl+3")
        await pilot.pause()
        app.query_one(EntryPanel).set_first_value("/banda 20m")
        await pilot.press("enter")
        await pilot.pause()
        assert app.state.profile_id is None
        assert app.query_one(StatusLine).profile == ""


async def test_the_star_marks_the_profile_activated_on_start(operator):
    profile = ProfileService.save(None, name="Portable", freq_hz=145_500_000, mode="FM")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_profiles(pilot)
        await pilot.press("up")
        await pilot.press("asterisk")
        await pilot.pause()
        assert ProfileService.get_default().id == profile.id
        assert "★" in app.query_one(ItemTable).selected_item().cells[0]
        await pilot.press("asterisk")
        await pilot.pause()
        assert ProfileService.get_default() is None
        await pilot.press("asterisk")
        await pilot.pause()

    # Whatever the last session left, the default profile is what starts.
    SettingsService.save_state(SessionState(operator_id=operator.id, band="40m",
                                            freq_hz=7_100_000))
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        assert (app.state.profile_id, app.state.freq_hz) == (profile.id, 145_500_000)


async def test_editing_the_active_profile_updates_the_session(operator):
    profile = ProfileService.save(None, name="HF", slot=1, freq_hz=7_100_000, mode="SSB")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("ctrl+1")
        await open_profiles(pilot)
        await pilot.press("up")
        await pilot.press("e")
        await pilot.pause()
        panel = app.query_one(EntryPanel)
        assert panel.values()["freq_hz"] == "7.100 MHz"
        await fill(pilot, app, {**panel.values(), "freq_hz": "7.150"})
        assert "modificado" in feedback(app)
        assert app.state.freq_hz == 7_150_000
        assert app.state.profile_id == profile.id


async def test_deleting_the_active_profile_keeps_its_values(operator):
    ProfileService.save(None, name="HF", slot=1, freq_hz=7_100_000, mode="CW")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("ctrl+1")
        await open_profiles(pilot)
        await pilot.press("up")
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        app.screen.dismiss(True)
        await pilot.pause()
        assert ProfileService.list_all() == []
        assert app.state.profile_id is None
        assert (app.state.freq_hz, app.state.mode) == (7_100_000, "CW")


async def test_enter_on_a_logged_qso_does_nothing(state):
    QsoService.log({"call": "EA1AAA"}, state)
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("up")
        await pilot.press("enter")
        await pilot.press("asterisk")
        await pilot.pause()
        assert len(QsoService.recent()) == 1
        assert app.query_one(HistoryPanel).selected_qso_id() is not None


def test_profiles_of_an_older_database_get_the_new_columns(tmp_path):
    """Before schema 10 a profile had no setup, power or key."""
    import sqlite3

    from ic705suite.apps.log.db import session as db_session

    url = f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}"
    db_session.dispose()
    db_session.init_engine(url)
    ProfileService.save(None, name="Antiguo", freq_hz=7_100_000, mode="SSB")
    db_session.dispose()

    with sqlite3.connect(tmp_path / "old.sqlite3") as connection:
        # SQLite cannot drop a column with a foreign key: rebuild the table.
        kept = (
            "id, name, operator_id, station_id, repeater_id, band, freq_hz, mode, "
            "digital_data, field_order, separator, is_default, created_at, updated_at"
        )
        connection.execute(f"CREATE TABLE old_profiles AS SELECT {kept} FROM profiles")
        connection.execute("DROP TABLE profiles")
        connection.execute("ALTER TABLE old_profiles RENAME TO profiles")
        connection.execute("UPDATE schema_version SET version = 9")

    db_session.init_engine(url)
    old = ProfileService.list_all()[0]
    assert (old.name, old.slot, old.power_w, old.equipment_id) == ("Antiguo", None, None, None)
    ProfileService.save(old.id, name="Antiguo", slot=0, freq_hz=7_100_000, power_w=10)
    assert ProfileService.get_by_slot(0).power_w == 10
