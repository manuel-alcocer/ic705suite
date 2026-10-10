"""Repeaters (F5): the URE list as a read-only catalog, shared callsigns and
the view that tunes them."""

from __future__ import annotations

import json
import re

import pytest

from ic705suite.apps.log.core import bands, modes
from ic705suite.apps.log.core.services import ProfileService, RepeaterService, ServiceError
from ic705suite.apps.log.db import session as db_session
from ic705suite.apps.log.db.preseed import apply_preseed, load_catalogs
from ic705suite.apps.log.tui.app import HamrlogApp
from ic705suite.apps.log.tui.profiles import ProfileKind
from ic705suite.apps.log.tui.widgets.entry import EntryPanel
from ic705suite.apps.log.tui.widgets.items import InventoryView, ItemTable


@pytest.fixture
def ure_list(tmp_path, monkeypatch):
    """A small URE list: one callsign naming three repeaters, and one alone."""
    monkeypatch.setenv("HAMRLOG_PRESEED", "1")
    folder = tmp_path / "preseed"
    folder.mkdir()
    entries = [
        {"callsign": "ED1YAB", "output_hz": 145_725_000, "shift_hz": -600_000,
         "mode": "FM", "ctcss": "77.0", "channel": "RV58", "ure": "R5",
         "gridsquare": "IN82PO", "name": "Radio Club Rioja"},
        {"callsign": "ED4ZAH", "output_hz": 438_350_000, "shift_hz": -7_600_000,
         "mode": "DSTAR", "channel": "RU668", "gridsquare": "IN70WR",
         "name": "URE Sierra de Guadarrama"},
        {"callsign": "ED4ZAH", "output_hz": 438_325_000, "shift_hz": -7_600_000,
         "mode": "DMR", "channel": "RU666", "gridsquare": "IN70WR",
         "name": "URE Sierra de Guadarrama", "digital": {"network": "Brandmeister"}},
        {"callsign": "ED4ZAH", "output_hz": 438_375_000, "shift_hz": -7_600_000,
         "mode": "C4FM", "channel": "RU670", "gridsquare": "IN70WR",
         "name": "URE Sierra de Guadarrama"},
        {"callsign": "SIN-SALIDA"},
    ]
    (folder / "repetidores.json").write_text(
        json.dumps({"tipo": "repetidores", "elementos": entries}), encoding="utf-8"
    )
    apply_preseed(db_session.init_engine(), folder)
    return folder


def feedback(app) -> str:
    return app.query_one("#entry").message


async def open_repeaters(pilot) -> None:
    await pilot.press("f5")
    await pilot.pause()
    await pilot.pause()


async def submit(pilot, app, values: dict[str, str]) -> None:
    app.query_one(EntryPanel).set_values(values)
    await pilot.press("enter")
    await pilot.pause()


async def command(pilot, app, text: str) -> None:
    app.query_one(EntryPanel).set_values({app.query_one(EntryPanel).fields[0].field_name: text})
    await pilot.press("enter")
    await pilot.pause()


def rows(app) -> list[tuple[str, ...]]:
    table = app.query_one(ItemTable)
    return [tuple(str(cell) for cell in table.get_row_at(i)) for i in range(table.item_count)]


# ------------------------------------------------------------------ catalog --
def test_the_bundled_ure_list_is_well_formed():
    """The shipped file: every repeater tunable, none twice, URE numbers sane."""
    repeaters = load_catalogs()["repetidores"]
    assert len(repeaters) > 250
    seen = set()
    for entry in repeaters:
        key = (entry["callsign"], entry["output_hz"])
        assert key not in seen, key
        seen.add(key)
        assert bands.from_frequency(entry["output_hz"]), key
        assert modes.get(entry["mode"]), key
        assert entry["ure"] == "" or re.fullmatch(r"R\d{1,3}", entry["ure"]), key
        assert entry["channel"] == "" or re.fullmatch(r"R[HFVUS]\d+", entry["channel"]), key
        assert entry["ctcss"] == "" or re.fullmatch(r"\d{2,3}\.\d", entry["ctcss"]), key
    rioja = next(e for e in repeaters if e["callsign"] == "ED1YAB")
    assert (rioja["output_hz"], rioja["shift_hz"], rioja["ure"], rioja["ctcss"]) == (
        145_725_000, -600_000, "R5", "77.0"
    )


def test_the_ure_list_is_loaded_once_and_read_only(ure_list):
    assert apply_preseed(db_session.init_engine(), ure_list) == 0
    # The entry without an output frequency is left out.
    assert len(RepeaterService.search()) == 4

    rioja = RepeaterService.resolve("ED1YAB")
    assert (rioja.ure_number, rioja.channel, rioja.ctcss_tx, rioja.preset) == (
        "R5", "RV58", "77.0", True
    )
    assert (rioja.band, rioja.input_hz) == ("2m", 145_125_000)
    with pytest.raises(ServiceError, match="lista de la URE"):
        RepeaterService.update(rioja.id, name="otro")
    with pytest.raises(ServiceError, match="lista de la URE"):
        RepeaterService.delete(rioja.id)


def test_a_repeater_the_operator_added_is_kept_over_the_list(tmp_path, monkeypatch):
    RepeaterService.create("ED1YAB", output_hz=145_725_000, notes="mío")
    monkeypatch.setenv("HAMRLOG_PRESEED", "1")
    folder = tmp_path / "preseed"
    folder.mkdir()
    (folder / "repetidores.json").write_text(
        json.dumps([{"callsign": "ED1YAB", "output_hz": 145_725_000, "shift_hz": -600_000}]),
        encoding="utf-8",
    )
    assert apply_preseed(db_session.init_engine(), folder) == 0
    (mine,) = RepeaterService.search()
    assert (mine.notes, mine.preset) == ("mío", False)


def test_upgrading_lets_repeaters_share_a_callsign(tmp_path):
    """Before schema 11 the callsign was unique; the URE list needs it shared."""
    import sqlite3

    url = f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}"
    db_session.dispose()
    db_session.init_engine(url)
    db_session.dispose()
    with sqlite3.connect(tmp_path / "old.sqlite3") as connection:
        connection.execute("DROP INDEX ix_repeaters_callsign")
        connection.execute("CREATE UNIQUE INDEX ix_repeaters_callsign ON repeaters (callsign)")
        connection.execute("UPDATE schema_version SET version = 10")

    db_session.init_engine(url)
    RepeaterService.create("ED4ZAH", output_hz=438_325_000, mode="DMR")
    RepeaterService.create("ED4ZAH", output_hz=438_375_000, mode="C4FM")
    assert len(RepeaterService.with_callsign("ED4ZAH")) == 2


# ----------------------------------------------------------------- services --
def test_one_callsign_may_name_several_repeaters_but_not_twice_one_output():
    RepeaterService.create("ED4ZAH", output_hz=438_325_000, mode="DMR")
    RepeaterService.create("ED4ZAH", output_hz=438_375_000, mode="C4FM")
    with pytest.raises(ServiceError, match="ya existe"):
        RepeaterService.create("ed4zah", output_hz=438_325_000)


def test_resolve_picks_by_frequency_mode_or_band(ure_list):
    with pytest.raises(ServiceError, match="son 3 repetidores"):
        RepeaterService.resolve("ED4ZAH")
    assert RepeaterService.resolve("ED4ZAH DMR").output_hz == 438_325_000
    assert RepeaterService.resolve("ed4zah 438.375").mode == "C4FM"
    assert RepeaterService.resolve("ED1YAB 2m").callsign == "ED1YAB"
    with pytest.raises(ServiceError, match="Ningún repetidor"):
        RepeaterService.resolve("ED4ZAH FM")
    with pytest.raises(ServiceError, match="No hay ningún repetidor"):
        RepeaterService.resolve("ED9XXX")


def test_labels_add_the_output_only_when_the_callsign_is_shared(ure_list):
    assert RepeaterService.label(RepeaterService.resolve("ED1YAB")) == "ED1YAB"
    dmr = RepeaterService.resolve("ED4ZAH DMR")
    assert RepeaterService.label(dmr) == "ED4ZAH 438.325"
    # What a label says, resolve reads back.
    for label in RepeaterService.labels():
        assert RepeaterService.resolve(label)


def test_search_matches_every_word_somewhere(ure_list):
    def calls(text: str) -> list[str]:
        return [f"{r.callsign} {r.mode}" for r in RepeaterService.search(text)]

    assert calls("R5") == ["ED1YAB FM"]
    assert calls("dmr guadarrama") == ["ED4ZAH DMR"]
    # Same callsign: by output frequency.
    assert calls("IN70") == ["ED4ZAH DMR", "ED4ZAH DSTAR", "ED4ZAH C4FM"]
    assert calls("rioja 70cm") == []
    assert RepeaterService.count("ED4ZAH") == 3


def test_the_input_sets_the_shift():
    repeater = RepeaterService.create("ED7ZZZ", output_hz=145_600_000, input_hz=145_000_000)
    assert repeater.shift_hz == -600_000
    updated = RepeaterService.update(repeater.id, input_hz=145_600_000)
    assert (updated.shift_hz, updated.input_hz) == (0, 145_600_000)


def test_a_profile_keeps_a_repeater_whose_callsign_is_shared(ure_list):
    kind = ProfileKind()
    kind.save(None, {"name": "DMR sierra", "repeater": "ED4ZAH DMR"})
    (profile,) = ProfileService.list_all()
    assert profile.repeater.output_hz == 438_325_000
    # Its digital values come with it.
    assert profile.digital_data == {"network": "Brandmeister"}
    values = kind.values(profile.id)
    assert values["repeater"] == "ED4ZAH 438.325"
    kind.save(profile.id, values)
    assert ProfileService.get(profile.id).repeater.mode == "DMR"


# ---------------------------------------------------------------- interface --
async def test_f5_lists_the_repeaters(operator, ure_list):
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await open_repeaters(pilot)
        assert app.query_one(InventoryView).display
        assert app.query_one("#log-frame").border_title == "Repetidores"
        assert "4 repetidores" in str(app.query_one("#inventory-tabs").render())
        assert list(app.query_one(EntryPanel).values()) == [
            "call", "output", "input", "tone", "mode", "ure_number", "channel",
            "owner", "qth", "gridsquare", "digital", "notes",
        ]
        first = rows(app)[0]
        assert first[:7] == ("R5", "ED1YAB", "145.725", "145.125", "77.0", "FM", "RV58")


async def test_a_repeater_is_written_in_the_entry_line(operator):
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await open_repeaters(pilot)
        await submit(pilot, app, {"call": "ed7zzz", "output": "145.600", "tone": "88",
                                  "ure_number": "r0", "owner": "Mi club"})
        (repeater,) = RepeaterService.search()
        assert (repeater.callsign, repeater.input_hz, repeater.ctcss_tx) == (
            "ED7ZZZ", 145_000_000, "88.5"
        )
        assert (repeater.ure_number, repeater.name) == ("R0", "Mi club")

        # The input may be a shift.
        await submit(pilot, app, {"call": "ED7YYY", "output": "438.700", "input": "-7.6 MHz"})
        assert RepeaterService.resolve("ED7YYY").input_hz == 431_100_000
        await submit(pilot, app, {"call": "ED7XXX", "output": "nada"})
        assert "Salida" in feedback(app)


async def test_enter_on_a_repeater_tunes_it(operator, ure_list):
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await open_repeaters(pilot)
        await command(pilot, app, "/buscar R5")
        assert [row[1] for row in rows(app)] == ["ED1YAB"]
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.state.repeater_call == "ED1YAB"
        assert (app.state.freq_hz, app.state.freq_tx_hz) == (145_725_000, 145_125_000)
        assert "ED1YAB" in feedback(app)


async def test_the_ure_list_cannot_be_edited_from_the_view(operator, ure_list):
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await open_repeaters(pilot)
        await pilot.press("up")
        await pilot.pause()
        for key in ("e", "d"):
            await pilot.press(key)
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert not app.query_one(EntryPanel).editing
            assert "lista de la URE" in feedback(app)


async def test_the_repeater_command_asks_which_one_when_shared(operator, ure_list):
    app = HamrlogApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await command(pilot, app, "/repetidor ED4ZAH")
        assert "son 3 repetidores" in feedback(app)
        assert app.state.repeater_id is None
        await command(pilot, app, "/repetidor ED4ZAH C4FM")
        assert app.state.repeater_call == "ED4ZAH"
        assert app.state.freq_hz == 438_375_000


def screen_text(app) -> str:
    return "\n".join(
        "".join(segment.text for segment in strip)
        for strip in app.screen._compositor.render_strips()
    )


async def test_the_insert_row_is_shown_below_a_long_list(operator):
    """With more rows than fit, the list opens scrolled down to <New repeater>."""
    for index in range(60):
        RepeaterService.create(f"ED1Z{index:02d}", output_hz=145_000_000 + index * 12_500)
    app = HamrlogApp()
    async with app.run_test(size=(140, 34)) as pilot:
        await open_repeaters(pilot)
        assert app.query_one(ItemTable).on_insert_row
        assert "<Nuevo repetidor>" in screen_text(app)
        # Also after a search narrows it and another widens it again.
        await command(pilot, app, "/buscar ED1Z5")
        await command(pilot, app, "/buscar")
        await pilot.pause()
        assert "<Nuevo repetidor>" in screen_text(app)
