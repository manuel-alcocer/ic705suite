"""Equipment sets, power supplies, the read-only catalog and the F2 view."""

from __future__ import annotations

import json

import pytest

from ic705suite.apps.log.core.services import (
    AntennaService,
    EquipmentService,
    PowerSupplyService,
    ServiceError,
    StationService,
)
from ic705suite.apps.log.db import session as db_session
from ic705suite.apps.log.db.preseed import apply_preseed
from ic705suite.apps.log.tui.app import HamrlogApp
from ic705suite.apps.log.tui.screens.base import ConfirmScreen
from ic705suite.apps.log.tui.widgets.entry import EntryPanel
from ic705suite.apps.log.tui.widgets.history import HistoryPanel
from ic705suite.apps.log.tui.widgets.items import InventoryView, ItemTable


# ----------------------------------------------------------------- services --
def test_a_set_needs_at_least_one_radio():
    with pytest.raises(ServiceError, match="al menos una emisora"):
        EquipmentService.create("Vacío", [])


def test_a_set_groups_radios_antennas_and_supplies():
    radio = StationService.create("IC-705", brand="Icom")
    antenna = AntennaService.create("X-300N", ["2m", "70cm"], brand="Diamond")
    supply = PowerSupplyService.create("DM-330MV", 13.8, 30, brand="Alinco")
    created = EquipmentService.create("Portátil", [radio.id], [antenna.id], [supply.id])

    found = EquipmentService.get(created.id)
    assert [s.name for s in found.stations] == ["IC-705"]
    assert [a.name for a in found.antennas] == ["X-300N"]
    assert [p.name for p in found.supplies] == ["DM-330MV"]

    # Deleting a part only takes it out of the set.
    AntennaService.delete(antenna.id)
    PowerSupplyService.delete(supply.id)
    found = EquipmentService.get(created.id)
    assert found.antennas == [] and found.supplies == []


def test_the_only_radio_of_a_set_cannot_be_deleted():
    radio = StationService.create("IC-705")
    other = StationService.create("FT-818")
    EquipmentService.create("Portátil", [radio.id])
    with pytest.raises(ServiceError, match="única emisora"):
        StationService.delete(radio.id)

    EquipmentService.create("Doble", [radio.id, other.id])
    StationService.delete(other.id)
    assert [s.name for s in EquipmentService.list_all()[0].stations] == ["IC-705"]


def test_supplies_validate_their_values():
    with pytest.raises(ServiceError, match="tensión"):
        PowerSupplyService.create("Rara", -1, 10)
    with pytest.raises(ServiceError, match="Ya existe"):
        PowerSupplyService.create("Batería", 12.8, 20)
        PowerSupplyService.create("batería")


# ------------------------------------------------------------------ catalog --
def write(folder, name, data) -> None:
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMRLOG_PRESEED", "1")
    folder = tmp_path / "preseed"
    folder.mkdir()
    write(folder, "radios_2026.json", {
        "tipo": "emisoras",
        "elementos": [{
            "name": "Icom IC-7300", "brand": "Icom", "rig": "IC-7300",
            "power_w": 100, "types": ["HF"], "notes": "Base HF",
        }],
    })
    write(folder, "antenas.json", [
        {"name": "Diamond X-300N", "brand": "Diamond", "bands": ["2m", "70cm", "11m"]},
    ])
    write(folder, "fuentes-extra.json", {
        "tipo": "fuentes",
        "elementos": [{"name": "Manson SPS-9600", "voltage_v": 13.8, "current_a": 60}],
    })
    write(folder, "otra-cosa.json", {"tipo": "cables", "elementos": []})
    (folder / "rota.json").write_text("{", encoding="utf-8")
    return folder


def test_every_catalog_file_is_loaded_once(catalog):
    engine = db_session.init_engine()
    assert apply_preseed(engine, catalog) == 3
    assert apply_preseed(engine, catalog) == 0

    radio = StationService.list_all()[0]
    assert (radio.name, radio.brand, radio.power_w, radio.preset) == (
        "Icom IC-7300", "Icom", 100, True
    )
    assert radio.type_names == "HF"
    antenna = AntennaService.list_all()[0]
    # Bands outside the plan are dropped.
    assert antenna.bands == ["2m", "70cm"]
    supply = PowerSupplyService.list_all()[0]
    # Without a brand, the first word of the name.
    assert (supply.brand, supply.current_a) == ("Manson", 60)


def test_the_operators_own_item_wins_over_the_catalog(catalog):
    StationService.create("icom ic-7300", rig="mío")
    engine = db_session.init_engine()
    apply_preseed(engine, catalog)
    radios = StationService.list_all()
    assert [(r.name, r.preset) for r in radios] == [("icom ic-7300", False)]


def test_catalog_items_are_read_only_but_usable_in_sets(catalog):
    apply_preseed(db_session.init_engine(), catalog)
    radio = StationService.list_all()[0]
    antenna = AntennaService.list_all()[0]
    supply = PowerSupplyService.list_all()[0]

    with pytest.raises(ServiceError, match="catálogo"):
        StationService.update(radio.id, notes="cambio")
    with pytest.raises(ServiceError, match="catálogo"):
        StationService.delete(radio.id)
    with pytest.raises(ServiceError, match="catálogo"):
        AntennaService.update(antenna.id, "Otra", [])
    with pytest.raises(ServiceError, match="catálogo"):
        AntennaService.delete(antenna.id)
    with pytest.raises(ServiceError, match="catálogo"):
        PowerSupplyService.update(supply.id, "Otra")
    with pytest.raises(ServiceError, match="catálogo"):
        PowerSupplyService.delete(supply.id)

    created = EquipmentService.create("Casa", [radio.id], [antenna.id], [supply.id])
    assert EquipmentService.get(created.id).stations[0].name == "Icom IC-7300"


def test_upgrading_a_v5_database_makes_a_set_per_radio(tmp_path):
    import sqlite3

    url = f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}"
    db_session.dispose()
    db_session.init_engine(url)
    radio = StationService.create("HF-Casa", rig="IC-7300")
    antenna = AntennaService.create("Dipolo", ["40m"])
    StationService.assign_antenna(radio.id, antenna.id)
    db_session.dispose()

    with sqlite3.connect(tmp_path / "old.sqlite3") as connection:
        connection.execute("UPDATE schema_version SET version = 5")

    db_session.init_engine(url)
    (equipment,) = EquipmentService.list_all()
    assert equipment.name == "HF-Casa"
    assert [s.name for s in equipment.stations] == ["HF-Casa"]
    assert [a.name for a in equipment.antennas] == ["Dipolo"]


# ---------------------------------------------------------------- interface --
async def open_tab(pilot, app, steps: int = 0) -> None:
    await pilot.press("f2")
    await pilot.pause()
    await pilot.pause()
    for _ in range(steps):
        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.pause()


async def submit(pilot, app, values: dict[str, str]) -> None:
    app.query_one(EntryPanel).set_values(values)
    await pilot.press("enter")
    await pilot.pause()


def feedback(app) -> str:
    return app.query_one("#entry").message


async def test_f2_turns_the_log_into_the_equipment_tabs(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.query_one(EntryPanel).set_values({"call": "ea4"})
        await open_tab(pilot, app)

        assert len(app.screen_stack) == 1
        assert app.query_one(InventoryView).display
        assert not app.query_one(HistoryPanel).display
        assert app.query_one("#log-frame").border_title == "Inventario"
        tabs = str(app.query_one("#inventory-tabs").render())
        for title in ("Equipos", "Emisoras", "Antenas", "Fuentes"):
            assert title in tabs
        assert list(app.query_one(EntryPanel).values()) == [
            "name", "stations", "antennas", "supplies", "notes"
        ]

        # F2 has no use inside the view; F1 is the log, with the half-typed
        # QSO intact.
        await pilot.press("f2")
        await pilot.pause()
        assert app.query_one(InventoryView).display
        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(HistoryPanel).display
        assert app.query_one(EntryPanel).values()["call"] == "ea4"


async def test_items_are_written_in_the_entry_line(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_tab(pilot, app, steps=1)
        assert "Emisoras" in str(app.query_one("#inventory-tabs").render())
        await submit(pilot, app, {"brand": "Icom", "rig": "IC-705", "power_w": "10",
                                  "types": "hf, vhf, uhf"})
        radio = StationService.list_all()[0]
        assert (radio.name, radio.power_w, radio.type_names) == (
            "Icom IC-705", 10, "HF, VHF, UHF"
        )

        await pilot.press("f1")
        await pilot.pause()
        await open_tab(pilot, app, steps=1)  # reopens on Emisoras: one more is Antenas
        assert "Antenas" in str(app.query_one("#inventory-tabs").render())
        await submit(pilot, app, {"brand": "Diamond", "name": "X-300N", "bands": "70cm, 2m"})
        await pilot.press("shift+pagedown")
        await pilot.pause()
        await pilot.pause()
        await submit(pilot, app, {"name": "Batería", "voltage_v": "12,8", "current_a": "20"})
        assert PowerSupplyService.list_all()[0].voltage_v == 12.8

        await pilot.press("ctrl+n")  # wraps round to Equipos
        await pilot.pause()
        await pilot.pause()
        await submit(pilot, app, {"name": "Portátil", "antennas": "x-300n"})
        assert "al menos una emisora" in feedback(app)
        await submit(pilot, app, {"name": "Portátil", "stations": "icom ic-705",
                                  "antennas": "x-300n", "supplies": "Batería"})
        (equipment,) = EquipmentService.list_all()
        assert [a.name for a in equipment.antennas] == ["X-300N"]
        assert app.query_one(ItemTable).item_count == 1


async def test_e_edits_and_d_deletes_an_item(operator):
    StationService.create("Icom IC-705", rig="IC-705", brand="Icom")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_tab(pilot, app, steps=1)
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        panel = app.query_one(EntryPanel)
        assert panel.editing and panel.values()["rig"] == "IC-705"
        app.query_one("#entry-power_w").value = "10"
        await pilot.press("enter")
        await pilot.pause()
        assert StationService.list_all()[0].power_w == 10
        assert not panel.editing and panel.browsing

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("s")
        await pilot.pause()
        assert StationService.list_all() == []


async def test_catalog_items_cannot_be_edited_from_the_view(operator, catalog):
    apply_preseed(db_session.init_engine(), catalog)
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_tab(pilot, app, steps=1)
        await pilot.press("up")
        await pilot.pause()
        for key in ("e", "d"):
            await pilot.press(key)
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert not app.query_one(EntryPanel).editing
            assert "catálogo" in feedback(app)


async def test_lists_filter_by_brand(operator):
    StationService.create("Icom IC-705", brand="Icom")
    StationService.create("Icom IC-7300", brand="Icom")
    StationService.create("Yaesu FT-991A", brand="Yaesu")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_tab(pilot, app, steps=1)
        table = app.query_one(ItemTable)
        assert table.item_count == 3

        app.query_one(EntryPanel).set_first_value("/marca yae")
        await pilot.press("enter")
        await pilot.pause()
        assert table.item_count == 1
        assert "Yaesu" in str(app.query_one("#inventory-tabs").render())

        app.query_one(EntryPanel).set_first_value("/marca")
        await pilot.press("enter")
        await pilot.pause()
        assert table.item_count == 3

        await pilot.press("alt+down")  # first brand: Icom
        await pilot.pause()
        assert table.item_count == 2
        await pilot.press("alt+down", "alt+down")  # Yaesu, then all again
        await pilot.pause()
        assert table.item_count == 3


async def test_function_keys_belong_to_the_view(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_tab(pilot, app)
        tabs = app.query_one("#inventory-tabs")

        def active() -> str:
            # The active tab is the one drawn with a background.
            text = tabs.render()
            return next(
                text.plain[span.start:span.end].strip()
                for span in text.spans
                if "on" in str(span.style)
            )

        assert active() == "Equipos"
        for key, expected in (("ctrl+n", "Emisoras"), ("shift+pagedown", "Antenas"),
                              ("ctrl+n", "Fuentes"), ("ctrl+n", "Equipos"),
                              ("shift+pageup", "Fuentes")):
            await pilot.press(key)
            await pilot.pause()
            await pilot.pause()
            assert active() == expected, key

        # Its own key, and keys no view uses, do nothing and never type into
        # the line.
        for key in ("f2", "f7", "f12"):
            await pilot.press(key)
            await pilot.pause()
        assert app.query_one(InventoryView).display
        assert active() == "Fuentes"
        assert not any(app.query_one(EntryPanel).values().values())

        # The keys of the other views open them from here, and back.
        for key, title in (("f3", "Agenda"), ("f4", "Perfiles"), ("f5", "Repetidores"),
                           ("f2", "Inventario")):
            await pilot.press(key)
            await pilot.pause()
            await pilot.pause()
            assert app.query_one("#log-frame").border_title == title, key
        # The inventory comes back on the tab it was left on.
        assert active() == "Fuentes"

        # Escape never leaves the view; F1 does.
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one(InventoryView).display
        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(HistoryPanel).display
        assert app.query_one("#log-frame").border_title == "Registro"

        # In the log, F1 stays put and the page keys walk the history.
        await pilot.press("f1")
        await pilot.pause()
        assert app.query_one(HistoryPanel).display


# -------------------------------------------------------------------- codes --
def test_radios_antennas_and_supplies_get_their_own_unique_code():
    first = StationService.create("IC-705")
    second = StationService.create("FT-818")
    antenna = AntennaService.create("Dipolo")
    supply = PowerSupplyService.create("Batería")
    assert (first.code, second.code, antenna.code, supply.code) == (
        "E0001", "E0002", "A0001", "S0001"
    )
    # Deleting one does not let a later radio take a code still in use.
    StationService.delete(first.id)
    assert StationService.create("FT-991A").code == "E0003"


def test_the_code_is_unique_in_the_database():
    import sqlalchemy

    StationService.create("IC-705")
    StationService.create("FT-818")
    engine = db_session.init_engine()
    with pytest.raises(sqlalchemy.exc.IntegrityError), engine.begin() as connection:
        connection.execute(sqlalchemy.text("UPDATE stations SET code = 'E0001'"))


def test_rows_without_a_code_get_one_on_start(tmp_path):
    import sqlite3

    url = f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}"
    db_session.dispose()
    db_session.init_engine(url)
    StationService.create("IC-705")
    StationService.create("FT-818")
    AntennaService.create("Dipolo")
    db_session.dispose()

    with sqlite3.connect(tmp_path / "old.sqlite3") as connection:
        connection.execute("DROP INDEX ix_stations_code")
        connection.execute("UPDATE stations SET code = NULL")
        connection.execute("UPDATE antennas SET code = NULL")
        connection.execute("UPDATE schema_version SET version = 7")

    db_session.init_engine(url)
    assert [(s.name, s.code) for s in StationService.list_all()] == [
        ("FT-818", "E0002"), ("IC-705", "E0001")
    ]
    assert AntennaService.list_all()[0].code == "A0001"


def test_catalog_items_get_codes_too(catalog):
    apply_preseed(db_session.init_engine(), catalog)
    from ic705suite.apps.log.db.codes import assign_missing_codes

    assign_missing_codes(db_session.init_engine())
    assert StationService.list_all()[0].code == "E0001"
    assert PowerSupplyService.list_all()[0].code == "S0001"


async def test_sets_take_codes_and_the_code_is_shown(operator):
    StationService.create("Icom IC-705", brand="Icom")
    AntennaService.create("X-300N", brand="Diamond")
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_tab(pilot, app)
        await submit(pilot, app, {"name": "Portátil", "stations": "e0001", "antennas": "A0001"})
        (equipment,) = EquipmentService.list_all()
        assert [s.name for s in equipment.stations] == ["Icom IC-705"]
        assert [a.name for a in equipment.antennas] == ["X-300N"]

        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.pause()
        table = app.query_one(ItemTable)
        assert str(table.get_row_at(0)[0]) == "E0001"
        await submit(pilot, app, {"brand": "Yaesu", "rig": "FT-818"})
        assert "E0002" in feedback(app)


def test_the_bundled_catalog_is_well_formed():
    """The shipped files: every kind present, names unique, values usable."""
    from ic705suite.apps.log.core import bands
    from ic705suite.apps.log.db.migrations import DEFAULT_STATION_TYPES
    from ic705suite.apps.log.db.preseed import load_catalogs

    catalogs = load_catalogs()
    assert all(catalogs[kind] for kind in ("emisoras", "antenas", "fuentes"))
    type_names = {name for name, _low, _high in DEFAULT_STATION_TYPES}
    for kind in ("emisoras", "antenas", "fuentes"):
        entries = catalogs[kind]
        names = [entry["name"].lower() for entry in entries]
        assert len(names) == len(set(names)), kind
        for entry in entries:
            assert entry.get("brand"), entry["name"]
    for radio in catalogs["emisoras"]:
        assert set(radio["types"]) <= type_names, radio["name"]
    for antenna in catalogs["antenas"]:
        assert all(bands.get(name) for name in antenna["bands"]), antenna["name"]


async def test_page_keys_page_through_the_list_of_each_view(operator):
    from ic705suite.apps.log.core.services import QsoService
    from ic705suite.apps.log.core.state import SessionState

    state = SessionState(operator_id=operator.id)
    state.set_band("40m")
    for number in range(40):
        QsoService.log({"call": f"EA4A{chr(65 + number % 26)}{number}"}, state)
    for number in range(40):
        StationService.create(f"Radio {number:02d}")

    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        bottom = history.cursor_row
        # The action bar takes a row of the list once a row is selected.
        rows = history.size.height - 2
        await pilot.press("pageup")
        await pilot.pause()
        page = bottom - history.cursor_row
        assert page > 1
        assert page == rows
        await pilot.press("pagedown")
        await pilot.pause()
        assert history.cursor_row == bottom

        await open_tab(pilot, app, steps=1)
        table = app.query_one(ItemTable)
        tabs_before = str(app.query_one("#inventory-tabs").render())
        insert_row = table.cursor_row
        await pilot.press("pageup")
        await pilot.pause()
        assert insert_row - table.cursor_row == table.size.height - 1
        # Paging moves the list, never the tab.
        assert str(app.query_one("#inventory-tabs").render()) == tabs_before
        assert app.query_one(EntryPanel).browsing


# ------------------------------------------------- setups assigned to QSOs --
@pytest.fixture
def setups():
    """A 2 m/70 cm handheld kit and an HF kit, with antennas that suit them."""
    from ic705suite.apps.log.core.services import StationTypeService

    handheld = StationService.create(
        "Anytone AT-D878UV", type_ids=StationTypeService.resolve("VHF, UHF")
    )
    hf_rig = StationService.create(
        "Icom IC-705", type_ids=StationTypeService.resolve("HF, VHF, UHF")
    )
    whip = AntennaService.create("Nagoya NA-771", ["2m", "70cm"])
    vertical = AntennaService.create("Komunica HF-PRO2", ["40m", "20m"])
    portable = EquipmentService.create("Walkie", [handheld.id], [whip.id])
    hf = EquipmentService.create("HF", [hf_rig.id], [whip.id, vertical.id])
    return {"walkie": portable, "hf": hf, "handheld": handheld, "hf_rig": hf_rig,
            "whip": whip, "vertical": vertical}


def log_on(operator, band: str, call: str = "EA4ABC"):
    from ic705suite.apps.log.core.services import QsoService
    from ic705suite.apps.log.core.state import SessionState

    state = SessionState(operator_id=operator.id)
    state.set_band(band)
    return QsoService.log({"call": call}, state)


def test_a_setup_assigned_to_a_qso_brings_its_radio_and_antenna(operator, setups):
    from ic705suite.apps.log.core.services import QsoService

    row = log_on(operator, "20m")
    updated = QsoService.update(row.id, {"equipment_id": setups["hf"].id})
    assert updated.equipment_name == "HF"
    assert not updated.equipment_mismatch
    assert updated.station_name == "Icom IC-705"
    # The antenna that works the band, not just the first one of the set.
    assert updated.antenna_name == "Komunica HF-PRO2"


def test_a_setup_that_cannot_work_the_frequency_is_kept_but_flagged(operator, setups):
    from ic705suite.apps.log.core.services import QsoService

    row = log_on(operator, "40m")
    updated = QsoService.update(row.id, {"equipment_id": setups["walkie"].id})
    assert updated.equipment_name == "Walkie"
    assert updated.equipment_mismatch
    assert updated.station_name == "Anytone AT-D878UV"

    # Moving the QSO to a band the set works clears the flag.
    updated = QsoService.update(row.id, {"freq_hz": 145_500_000})
    assert not updated.equipment_mismatch


def test_deleting_a_setup_leaves_its_qsos_alone(operator, setups):
    from ic705suite.apps.log.core.services import QsoService

    row = log_on(operator, "2m")
    QsoService.update(row.id, {"equipment_id": setups["walkie"].id})
    EquipmentService.delete(setups["walkie"].id)
    after = QsoService.get(row.id)
    assert after.equipment_name == ""
    assert after.station_name == "Anytone AT-D878UV"


async def edit_first_qso(pilot, app):
    await pilot.press("up")
    await pilot.pause()
    await pilot.press("e")
    await pilot.pause()


async def test_a_setup_is_chosen_while_editing_a_qso_in_the_log(operator, setups):
    log_on(operator, "40m")
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await edit_first_qso(pilot, app)
        box = app.query_one("#entry-equipment")
        assert box.display and box.value == ""

        box.value = "walkie"
        await pilot.press("enter")
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        # Saved anyway, but flagged E in the INFO column.
        assert str(history.get_cell_at((history.cursor_row, 0))) == "E"
        assert "no corresponde" in feedback(app)
        assert "no corresponde" in str(app.query_one("#detail").render())

        # Saving leaves the cursor on the QSO: E edits it again.
        assert app.query_one(EntryPanel).browsing
        await pilot.press("e")
        await pilot.pause()
        assert app.query_one("#entry-equipment").value == "Walkie"
        app.query_one("#entry-equipment").value = "HF"
        await pilot.press("enter")
        await pilot.pause()
        assert str(history.get_cell_at((history.cursor_row, 0))) == ""

        await pilot.press("e")
        await pilot.pause()
        app.query_one("#entry-equipment").value = "No existe"
        await pilot.press("enter")
        await pilot.pause()
        assert "No existe el equipo" in feedback(app)
        assert app.query_one(EntryPanel).editing


async def test_the_log_has_a_qth_column(operator):
    from ic705suite.apps.log.core.services import QsoService
    from ic705suite.apps.log.core.state import SessionState

    state = SessionState(operator_id=operator.id)
    state.set_band("40m")
    QsoService.log({"call": "EA4ABC", "qth": "Madrid", "comment": "nota"}, state)
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(HistoryPanel)
        assert str(history.get_cell(str(QsoService.recent()[0].id), "QTH")) == "Madrid"


async def test_a_rule_separates_the_tabs_from_the_list(operator):
    app = HamrlogApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await open_tab(pilot, app)
        tabs = app.query_one("#inventory-tabs")
        table = app.query_one(ItemTable)
        lines = [
            "".join(segment.text for segment in strip)
            for strip in app.screen._compositor.render_strips()
        ]
        rule = lines[tabs.region.y + 1]
        assert "─" in rule and "Equipos" not in rule
        # The list starts right under the rule.
        assert table.region.y == tabs.region.y + 2
