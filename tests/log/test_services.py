"""Service layer: logging rules, duplicates, profiles and statistics."""

from __future__ import annotations

import datetime as dt

import pytest

from ic705suite.apps.log.core.entry import parse
from ic705suite.apps.log.core.services import (
    AntennaService,
    OperatorService,
    ProfileService,
    QsoService,
    ServiceError,
    SettingsService,
    StationService,
    StationTypeService,
)
from ic705suite.apps.log.core.state import SessionState


def log_line(line: str, state: SessionState, **kwargs):
    parsed = parse(line, field_order=state.field_order, mode_name=state.mode)
    return QsoService.log(parsed.fields, state, digital=parsed.digital, **kwargs)


def test_logging_uses_the_session_defaults(state):
    row = log_line("ea4abc,juan,59,57", state)
    assert row.call == "EA4ABC"
    assert row.band == "40m"
    assert row.freq_hz == 7_130_000
    assert row.mode == "SSB"
    assert row.country == "Spain"
    assert row.entry_mode == "AUTO"
    assert row.station_name == "HF-Casa"


def test_logging_without_an_operator_is_rejected():
    state = SessionState()
    state.set_band("40m")
    with pytest.raises(ServiceError, match="operador"):
        QsoService.log({"call": "EA4ABC"}, state)


def test_manual_timestamp_marks_the_contact_as_manual(state):
    row = log_line("ea3mno,laia", state, qso_utc=dt.datetime(2026, 1, 15, 12, 30))
    assert row.entry_mode == "MANUAL"
    assert row.qso_utc == dt.datetime(2026, 1, 15, 12, 30)


def test_automatic_contact_allows_everything_but_the_timestamp(state):
    row = log_line("ea4abc,juan", state)
    assert "qso_utc" not in QsoService.editable_fields(row.id)

    updated = QsoService.update(
        row.id, {"call": "ea4abc/p", "name": "Otro", "qth": "X", "freq_hz": 14_250_000}
    )
    assert (updated.call, updated.name, updated.qth) == ("EA4ABC/P", "Otro", "X")
    assert updated.band == "20m"
    assert updated.qso_utc == row.qso_utc

    with pytest.raises(ServiceError, match="automático"):
        QsoService.update(row.id, {"qso_utc": dt.datetime(2020, 1, 1)})


def test_manual_contact_allows_every_field(state):
    row = log_line("ea3mno,laia", state, qso_utc=dt.datetime(2026, 1, 15, 12, 30))
    updated = QsoService.update(
        row.id,
        {
            "name": "Laia M.",
            "qth": "Barcelona",
            "freq_hz": 14_250_000,
            "qso_utc": dt.datetime(2026, 1, 15, 13, 0),
        },
    )
    assert updated.name == "Laia M."
    assert updated.qth == "Barcelona"
    # Changing the frequency must move the band with it.
    assert updated.band == "20m"
    assert updated.qso_utc == dt.datetime(2026, 1, 15, 13, 0)


def test_changing_the_callsign_recomputes_the_country(state):
    row = log_line("ea4abc", state, qso_utc=dt.datetime(2026, 1, 15, 12, 30))
    assert row.country == "Spain"
    assert QsoService.update(row.id, {"call": "DL2JKL"}).country == "Germany"


def test_duplicates_are_scoped_to_band_and_mode(state):
    log_line("ea4abc,juan", state)
    assert len(QsoService.find_duplicates("EA4ABC", "40m", "SSB")) == 1
    assert QsoService.find_duplicates("EA4ABC", "20m", "SSB") == []
    assert QsoService.find_duplicates("EA4ABC", "40m", "CW") == []
    # Portable operation is the same station.
    assert len(QsoService.find_duplicates("EA4ABC/P", "40m", "SSB")) == 1


def test_another_day_is_another_qso(state):
    """The everyday rule: yesterday's QSO is not a duplicate today; the whole log is
    still asked for a contest."""
    yesterday = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=1)
    log_line("ea4abc,juan", state, qso_utc=yesterday)
    assert QsoService.find_duplicates("EA4ABC", "40m", "SSB", since_day=True) == []
    assert len(QsoService.find_duplicates("EA4ABC", "40m", "SSB")) == 1
    log_line("ea4abc,juan", state)
    assert len(QsoService.find_duplicates("EA4ABC", "40m", "SSB", since_day=True)) == 1


def test_a_profile_keeps_its_duplicate_rule(state):
    profile = ProfileService.save(None, name="Contest", dupe_rule="log")
    assert profile.dupe_rule == "log"
    ProfileService.apply_to_state(profile.id, state)
    assert state.dupe_rule == "log"
    with pytest.raises(ServiceError):
        ProfileService.save(profile.id, name="Contest", dupe_rule="sometimes")


def test_history_is_returned_oldest_first(state):
    for index, call in enumerate(["ea1aaa", "ea2bbb", "ea3ccc"]):
        log_line(call, state, qso_utc=dt.datetime(2026, 1, 15, 12, index))
    assert [row.call for row in QsoService.recent()] == ["EA1AAA", "EA2BBB", "EA3CCC"]


def test_history_by_band_and_mode(state):
    log_line("ea1aaa", state)                       # 40m SSB
    state.set_band("20m")
    log_line("ea2bbb", state)
    state.set_mode("CW")
    log_line("ea3ccc", state)
    assert [row.call for row in QsoService.recent(band="20m")] == ["EA2BBB", "EA3CCC"]
    assert [row.call for row in QsoService.recent(mode="SSB")] == ["EA1AAA", "EA2BBB"]
    assert [row.call for row in QsoService.recent(band="20m", mode="CW")] == ["EA3CCC"]
    assert QsoService.logged_bands() == ["40m", "20m"]
    assert QsoService.logged_modes() == ["CW", "SSB"]


def test_search_matches_several_fields(state):
    log_line("ea4abc,juan,59,57,Madrid,por la tarde", state)
    assert len(QsoService.search("juan")) == 1
    assert len(QsoService.search("madrid")) == 1
    assert len(QsoService.search("tarde")) == 1
    assert len(QsoService.search("nada de eso")) == 0


def test_delete_removes_the_contact(state):
    row = log_line("ea4abc", state)
    QsoService.delete(row.id)
    assert QsoService.get(row.id) is None
    with pytest.raises(ServiceError):
        QsoService.delete(row.id)


def test_stats_counts_by_band_and_mode(state):
    log_line("ea1aaa", state)
    state.set_band("20m")
    log_line("ea2bbb", state)
    state.set_mode("CW")
    log_line("dl2jkl", state)

    stats = QsoService.stats()
    assert stats.total == 3
    assert stats.today == 3
    assert stats.unique_calls == 3
    assert stats.countries == 2
    assert stats.by_band == {"40m": 1, "20m": 2}
    assert stats.by_mode == {"SSB": 2, "CW": 1}


def test_profiles_round_trip_the_session_state(state):
    state.set_mode("DMR")
    state.digital_data = {"talkgroup": "21466", "network": "Brandmeister"}
    ProfileService.save_from_state("DMR-Hotspot", state)

    restored = SessionState()
    ProfileService.apply_to_state(ProfileService.list_all()[0].id, restored)
    assert restored.profile_name == "DMR-Hotspot"
    assert restored.band == state.band
    assert restored.freq_hz == state.freq_hz
    assert restored.mode == "DMR"
    assert restored.digital_data == {"talkgroup": "21466", "network": "Brandmeister"}
    # A configuration is shared between stations, so alone it keeps the
    # station untouched; loading it from under a station selects that one.
    assert restored.station_id is None
    ProfileService.apply_to_state(
        ProfileService.list_all()[0].id, restored, station_id=state.station_id
    )
    assert restored.station_id == state.station_id


def test_saving_a_profile_twice_needs_overwrite(state):
    ProfileService.save_from_state("perfil", state)
    with pytest.raises(ServiceError, match="Ya existe"):
        ProfileService.save_from_state("perfil", state)
    state.set_band("20m")
    updated = ProfileService.save_from_state("perfil", state, overwrite=True)
    assert updated.band == "20m"


def test_only_one_profile_is_the_default(state):
    first = ProfileService.save_from_state("uno", state)
    second = ProfileService.save_from_state("dos", state)
    ProfileService.set_default(first.id)
    ProfileService.set_default(second.id)
    assert ProfileService.get_default().id == second.id


def test_deleting_a_station_keeps_its_contacts(state):
    row = log_line("ea4abc", state)
    StationService.delete(state.station_id)
    assert QsoService.get(row.id) is not None
    assert QsoService.get(row.id).station_name == ""


def test_session_state_survives_a_restart(state):
    state.set_mode("C4FM")
    state.digital_data = {"room": "ESPANA"}
    SettingsService.save_state(state)

    restored = SettingsService.load_state()
    assert restored.mode == "C4FM"
    assert restored.digital_data == {"room": "ESPANA"}
    assert restored.operator_id == state.operator_id
    assert restored.field_order == state.field_order


def test_changing_mode_drops_stale_digital_values(state):
    state.set_mode("DMR")
    state.digital_data = {"talkgroup": "214"}
    state.set_mode("SSB")
    assert state.digital_data == {}


# --------------------------------------------------------------------------- #
# Station types and configurations per station
# --------------------------------------------------------------------------- #

def test_new_databases_start_with_the_usual_station_types():
    names = [station_type.name for station_type in StationTypeService.list_all()]
    assert names == ["HF", "CB", "VHF", "UHF"]


def test_station_types_need_a_valid_range():
    with pytest.raises(ServiceError, match="menor que la máxima"):
        StationTypeService.create("SHF", 10_000_000_000, 3_000_000_000)
    with pytest.raises(ServiceError, match="Ya existe"):
        StationTypeService.create("hf", 1_000_000, 2_000_000)
    created = StationTypeService.create("6m", 50_000_000, 54_000_000)
    assert StationTypeService.resolve("HF, 6M") == [
        StationTypeService.list_all()[0].id, created.id
    ]
    with pytest.raises(ServiceError, match="No existe el tipo"):
        StationTypeService.resolve("HF, SHF")


def test_a_station_only_takes_configurations_it_can_tune(state):
    hf_vhf = StationService.create(
        "IC-705 Sevilla", rig="IC-705", type_ids=StationTypeService.resolve("HF, VHF")
    )
    cb = StationService.create("CB base", type_ids=StationTypeService.resolve("CB"))

    forty = ProfileService.save_from_state("40m SSB", state)
    ProfileService.assign(forty.id, hf_vhf.id)
    with pytest.raises(ServiceError, match="queda fuera"):
        ProfileService.assign(forty.id, cb.id)

    assert [p.name for p in ProfileService.for_station(hf_vhf.id)] == ["40m SSB"]
    assert ProfileService.for_station(cb.id) == []

    # Saving under a station refuses before storing anything.
    with pytest.raises(ServiceError, match="queda fuera"):
        ProfileService.save_from_state("otra", state, station_id=cb.id)
    assert [p.name for p in ProfileService.list_all()] == ["40m SSB"]


def test_configurations_are_reused_between_stations(state):
    sevilla = StationService.create("IC-705 Sevilla")
    nueva = StationService.create("IC-705 Nueva")
    shared = ProfileService.save_from_state("20m", state, station_id=sevilla.id)
    ProfileService.assign(shared.id, nueva.id)
    assert {s.name for s in ProfileService.get(shared.id).stations} == {
        "IC-705 Sevilla", "IC-705 Nueva"
    }

    # Unassigning, and deleting a station, leave the configuration alone.
    ProfileService.unassign(shared.id, nueva.id)
    assert ProfileService.for_station(nueva.id) == []
    StationService.delete(sevilla.id)
    assert ProfileService.get(shared.id) is not None
    assert [p.name for p in ProfileService.unassigned()] == ["20m"]


def test_deleting_a_type_frees_its_stations():
    vhf = StationTypeService.list_all()[2]
    station = StationService.create("FT-65", type_ids=[vhf.id])
    StationTypeService.delete(vhf.id)
    assert StationService.get(station.id).types == []


def test_upgrading_a_v3_database_assigns_profiles_to_their_station(tmp_path):
    """Before schema 4 a profile carried one station; it becomes an assignment."""
    import sqlite3

    from ic705suite.apps.log.db import session as db_session

    url = f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}"
    db_session.dispose()
    db_session.init_engine(url)
    station = StationService.create("HF-Casa")
    loose = ProfileService.save_from_state("suelta", SessionState())
    tied = ProfileService.save_from_state("atada", SessionState())
    db_session.dispose()

    # Turn it back into what version 3 left behind.
    with sqlite3.connect(tmp_path / "old.sqlite3") as connection:
        connection.execute("DELETE FROM station_profile_links")
        connection.execute("DELETE FROM station_types")
        connection.execute("UPDATE profiles SET station_id = ? WHERE id = ?", (station.id, tied.id))
        connection.execute("UPDATE schema_version SET version = 3")

    db_session.init_engine(url)
    assert [p.name for p in ProfileService.for_station(station.id)] == ["atada"]
    assert [p.name for p in ProfileService.unassigned()] == [loose.name]
    assert len(StationTypeService.list_all()) == 4


# --------------------------------------------------------------------------- #
# Antennas
# --------------------------------------------------------------------------- #

def test_antenna_bands_are_amateur_bands_in_plan_order():
    assert AntennaService.resolve_bands("70CM, 2m, 2m") == ["2m", "70cm"]
    with pytest.raises(ServiceError, match="no es una banda"):
        AntennaService.resolve_bands("2m, VHF")


def test_an_antenna_narrows_the_configurations_of_its_station(state):
    rig = StationService.create("ICOM IC-705")
    x300 = AntennaService.create("Diamond X300N", ["2m", "70cm"])
    anything = AntennaService.create("Sin bandas")
    forty = ProfileService.save_from_state("40m SSB", state, station_id=rig.id)
    state.set_band("2m")
    ProfileService.save_from_state("2m FM", state, station_id=rig.id)

    def names(antenna_id):
        return [p.name for p in ProfileService.for_station(rig.id, antenna_id)]

    assert names(x300.id) == ["2m FM"]
    assert names(anything.id) == ["2m FM", "40m SSB"]
    assert names(None) == ["2m FM", "40m SSB"]

    # Saving under an antenna that does not work on the band is refused.
    state.set_band("40m")
    with pytest.raises(ServiceError, match="no trabaja en 40m"):
        ProfileService.save_from_state(
            "otra", state, station_id=rig.id, antenna_id=x300.id
        )
    assert ProfileService.get(forty.id).band == "40m"


def test_antennas_are_shared_and_deleting_one_keeps_the_log(state):
    other = StationService.create("Kenwood TM-241E")
    dipolo = StationService.get(state.station_id).antennas[0]
    StationService.assign_antenna(other.id, dipolo.id)
    assert [a.name for a in StationService.get(other.id).antennas] == ["Dipolo"]

    row = log_line("ea4abc", state)
    assert row.antenna_name == "Dipolo"
    AntennaService.delete(dipolo.id)
    assert StationService.get(other.id).antennas == []
    assert QsoService.recent()[-1].antenna_name == ""


def test_upgrading_a_v4_database_moves_antennas_to_their_own_table(tmp_path):
    """Before schema 5 the antenna was a text column of the station."""
    import sqlite3

    from ic705suite.apps.log.db import session as db_session

    url = f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}"
    db_session.dispose()
    db_session.init_engine(url)
    sg = StationService.create("AT-878UV · SG7900")
    tm = StationService.create("TM-241E · SG7900")
    bare = StationService.create("IC-705")
    operator = OperatorService.create("EA7WM")
    state = SessionState(operator_id=operator.id, station_id=sg.id)
    state.set_band("2m")
    log_line("ea4abc", state)
    db_session.dispose()

    with sqlite3.connect(tmp_path / "old.sqlite3") as connection:
        connection.execute("DELETE FROM station_antenna_links")
        connection.execute("DELETE FROM antennas")
        connection.execute("UPDATE qsos SET antenna_id = NULL")
        connection.execute(
            "UPDATE stations SET antenna = 'Diamond SG7900' WHERE id IN (?, ?)", (sg.id, tm.id)
        )
        connection.execute("UPDATE schema_version SET version = 4")

    db_session.init_engine(url)
    assert [a.name for a in AntennaService.list_all()] == ["Diamond SG7900"]
    assert [a.name for a in StationService.get(sg.id).antennas] == ["Diamond SG7900"]
    assert [a.name for a in StationService.get(tm.id).antennas] == ["Diamond SG7900"]
    assert StationService.get(bare.id).antennas == []
    assert QsoService.recent()[-1].antenna_name == "Diamond SG7900"


def test_the_engine_is_reused_when_its_url_text_is_escaped(tmp_path):
    """A colon in the path (every Windows drive, "C:") is escaped in the URL.

    Comparing the text made each call build a new engine, which on Windows
    re-ran the catalog preseed against the real files.
    """
    import sys

    from ic705suite.apps.log.db import session as db_session

    # Windows paths carry the drive already; elsewhere a folder named like
    # one gets the same escaping.
    folder = tmp_path
    if sys.platform != "win32":
        folder = tmp_path / "C:"
        folder.mkdir()
    url = f"sqlite:///{(folder / 'test.sqlite3').as_posix()}"
    first = db_session.init_engine(url)
    assert db_session.init_engine(url) is first
