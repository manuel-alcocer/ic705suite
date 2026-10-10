"""Address book: file formats, import, export and autofill."""

from __future__ import annotations

import json

import pytest

from ic705suite.apps.log.core import contacts as contact_files
from ic705suite.apps.log.core import transfer
from ic705suite.apps.log.core.entry import parse
from ic705suite.apps.log.core.services import ContactService, QsoService, ServiceError

RADIOID_CSV = """RADIO_ID,CALLSIGN,FIRST_NAME,LAST_NAME,CITY,STATE,COUNTRY,REMARKS
2147001,EA7WM,Manuel,Alcocer,Sevilla,Sevilla,Spain,
2147122,EA7ABC,Jose Luis,Garcia,Cordoba,Cordoba,Spain,DMR+FM
2620001,DL2JKL,Hans,Mueller,Berlin,,Germany,
"""

BRANDMEISTER_CSV = """Radio ID;Callsign;Name;City;State;Country;Remarks
2147455;EA7XYZ;Ana Ruiz;Huelva;Huelva;Spain;
"""

ANYTONE_CSV = """No.,Radio ID,Callsign,Name,City,State,Country,Remarks,Call Type,Call Alert
1,2147999,EA7ZZZ,Luis Marin,Jaen,Jaen,Spain,,Private Call,None
"""

API_JSON = json.dumps(
    {
        "count": 1,
        "results": [
            {
                "id": 2147333,
                "callsign": "EA7JSN",
                "fname": "Rocio",
                "surname": "Diaz",
                "city": "Almeria",
                "state": "Almeria",
                "country": "Spain",
                "remarks": "",
            }
        ],
    }
)


def write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------- formats ----

@pytest.mark.parametrize(
    ("name", "text", "expected_calls"),
    [
        ("radioid.csv", RADIOID_CSV, {"EA7WM", "EA7ABC", "DL2JKL"}),
        ("bm.csv", BRANDMEISTER_CSV, {"EA7XYZ"}),
        ("anytone.csv", ANYTONE_CSV, {"EA7ZZZ"}),
        ("api.json", API_JSON, {"EA7JSN"}),
    ],
)
def test_reader_detects_every_common_format(tmp_path, name, text, expected_calls):
    report = contact_files.read_file(write(tmp_path, name, text))
    assert {record.callsign for record in report.records} == expected_calls
    assert report.format_name


def test_reader_handles_a_file_without_headers(tmp_path):
    """RadioID exports sometimes arrive with the header row stripped."""
    path = write(tmp_path, "raw.csv", "2147555,EA7NOH,Sin,Cabecera,Sevilla,Sevilla,Spain,\n")
    report = contact_files.read_file(path)
    assert len(report.records) == 1
    assert report.records[0].callsign == "EA7NOH"
    assert report.records[0].dmr_id == 2147555


def test_reader_splits_a_single_name_column(tmp_path):
    """BrandMeister puts the whole name in one column; RadioID splits it."""
    report = contact_files.read_file(write(tmp_path, "bm.csv", BRANDMEISTER_CSV))
    record = report.records[0]
    assert record.first_name == "Ana"
    assert record.last_name == "Ruiz"


def test_reader_reports_an_unrecognisable_file(tmp_path):
    path = write(tmp_path, "nope.csv", "alfa,beta,gamma\n1,2,3\n")
    report = contact_files.read_file(path)
    assert not report.records
    assert report.warnings


def test_reader_tolerates_non_utf8_bytes(tmp_path):
    path = tmp_path / "latin.csv"
    path.write_bytes(
        "RADIO_ID,CALLSIGN,FIRST_NAME,CITY,COUNTRY\n2147001,EA7WM,José,Córdoba,Spain\n".encode(
            "latin-1"
        )
    )
    report = contact_files.read_file(path)
    assert report.records[0].first_name == "José"


# -------------------------------------------------------------- import ----

def test_import_creates_entries(tmp_path):
    summary = transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    assert summary.created == 3
    assert summary.skipped == 0
    assert ContactService.count() == 3


def test_reimporting_updates_instead_of_duplicating(tmp_path):
    path = write(tmp_path, "r.csv", RADIOID_CSV)
    transfer.import_contacts(path)
    second = transfer.import_contacts(path)
    assert second.created == 0
    assert second.updated == 3
    assert ContactService.count() == 3


def test_import_does_not_overwrite_what_the_operator_typed(tmp_path):
    """A downloaded list must not replace a name entered by hand."""
    ContactService.create("EA7WM", first_name="Manolo", city="Dos Hermanas")
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))

    entry = ContactService.lookup("EA7WM")
    assert entry.first_name == "Manolo"
    assert entry.city == "Dos Hermanas"
    # Blanks are still filled in from the file.
    assert entry.dmr_id == 2147001
    assert entry.last_name == "Alcocer"


def test_import_can_filter_by_country(tmp_path):
    summary = transfer.import_contacts(
        write(tmp_path, "r.csv", RADIOID_CSV), country_filter="Germany"
    )
    assert summary.created == 1
    assert summary.skipped == 2
    assert ContactService.lookup("DL2JKL") is not None
    assert ContactService.lookup("EA7WM") is None


def test_import_matches_an_existing_entry_by_dmr_id(tmp_path):
    """The same person may appear under a slightly different callsign."""
    ContactService.create("EA7WM", dmr_id=2147001)
    summary = transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    assert summary.updated == 1
    assert summary.created == 2


def test_import_of_a_large_list_is_batched(tmp_path):
    """A national list is tens of thousands of rows and must not be slow."""
    lines = ["RADIO_ID,CALLSIGN,FIRST_NAME,LAST_NAME,CITY,STATE,COUNTRY,REMARKS"]
    for index in range(5_000):
        letters = (index % 26, (index // 26) % 26, (index // 676) % 26)
        suffix = "".join(chr(65 + value) for value in letters)
        lines.append(f"{2160000 + index},EA{index % 10}{suffix},N{index},A{index},C,P,Spain,")
    summary = transfer.import_contacts(write(tmp_path, "big.csv", "\n".join(lines)))
    assert summary.created > 4_900
    assert ContactService.count() == summary.created


# -------------------------------------------------------------- lookup ----

def test_lookup_ignores_portable_indicators(tmp_path):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    assert ContactService.lookup("F/EA7WM/P").callsign == "EA7WM"
    assert ContactService.lookup("ea7wm").first_name == "Manuel"


def test_lookup_returns_none_for_a_stranger():
    assert ContactService.lookup("EA9ZZZ") is None


def test_search_covers_name_city_and_dmr_id(tmp_path):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    assert [c.callsign for c in ContactService.search("manuel")] == ["EA7WM"]
    assert [c.callsign for c in ContactService.search("cordoba")] == ["EA7ABC"]
    assert [c.callsign for c in ContactService.search("2620001")] == ["DL2JKL"]


def test_search_counts_qsos_with_each_station(tmp_path, state):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    for _ in range(3):
        QsoService.log(parse("ea7wm", mode_name="SSB").fields, state)

    found = {row.callsign: row.qso_count for row in ContactService.search()}
    assert found["EA7WM"] == 3
    assert found["DL2JKL"] == 0


def test_duplicate_callsign_is_rejected():
    ContactService.create("EA7WM")
    with pytest.raises(ServiceError, match="ya está en la agenda"):
        ContactService.create("ea7wm")


def test_duplicate_dmr_id_is_rejected():
    ContactService.create("EA7WM", dmr_id=2147001)
    with pytest.raises(ServiceError, match="2147001"):
        ContactService.create("EA7ABC", dmr_id=2147001)


# ------------------------------------------------------------ autofill ----

def test_logging_fills_blanks_from_the_address_book(state, tmp_path):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    row = QsoService.log(parse("ea7wm", mode_name="SSB").fields, state)
    assert row.name == "Manuel"
    assert row.qth == "Sevilla"


def test_what_the_operator_types_beats_the_address_book(state, tmp_path):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    row = QsoService.log(parse("ea7wm,Manolo", mode_name="SSB").fields, state)
    assert row.name == "Manolo"


def test_autofill_can_be_switched_off(state, tmp_path):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    state.autofill_from_book = False
    row = QsoService.log(parse("ea7wm", mode_name="SSB").fields, state)
    assert row.name == ""


# -------------------------------------------------------------- export ----

@pytest.mark.parametrize("export_format", ["hamrlog", "radioid", "anytone"])
def test_export_writes_every_format(tmp_path, export_format):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    path, count = transfer.export_contacts(
        tmp_path / f"out-{export_format}.csv", export_format=export_format
    )
    assert count == 3
    text = path.read_text(encoding="utf-8-sig")
    assert "EA7WM" in text


def test_anytone_export_has_the_columns_the_cps_expects(tmp_path):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    path, _ = transfer.export_contacts(tmp_path / "at.csv", export_format="anytone")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == (
        "No.,Radio ID,Callsign,Name,City,State,Country,Remarks,Call Type,Call Alert"
    )
    first = lines[1].split(",")
    assert first[0] == "1"
    assert first[3] == "Hans Mueller"
    assert first[-2:] == ["Private Call", "None"]


def test_anytone_export_skips_contacts_without_a_dmr_id(tmp_path):
    """The radio cannot use an entry with no ID, so it is left out."""
    ContactService.create("EA7NOID", first_name="Sin", last_name="ID")
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    path, count = transfer.export_contacts(tmp_path / "at.csv", export_format="anytone")
    assert count == 3
    assert "EA7NOID" not in path.read_text(encoding="utf-8")


def test_export_then_import_round_trips(tmp_path):
    transfer.import_contacts(write(tmp_path, "r.csv", RADIOID_CSV))
    path, _ = transfer.export_contacts(tmp_path / "out.csv", export_format="hamrlog")
    ContactService.clear()
    assert ContactService.count() == 0

    summary = transfer.import_contacts(path)
    assert summary.created == 3
    entry = ContactService.lookup("EA7WM")
    assert entry.first_name == "Manuel"
    assert entry.dmr_id == 2147001


# ------------------------------------------------- automatic bookkeeping ----

def test_working_a_new_station_adds_it_to_the_book(state):
    """The book grows with the log: a callsign worked is a callsign known."""
    assert ContactService.count() == 0
    QsoService.log(parse("ea4abc,Jose  Manuel,59,57,Madrid", mode_name="SSB").fields, state)

    entry = ContactService.lookup("EA4ABC")
    assert entry is not None
    # The name heard on air is a name, whole: never split into a surname.
    assert entry.first_name == "Jose Manuel"
    assert entry.last_name == ""
    assert entry.city == "Madrid"
    assert entry.country == "Spain"
    assert entry.source == "log"


def test_a_second_qso_does_not_duplicate_the_entry(state):
    QsoService.log(parse("ea4abc,Juan", mode_name="SSB").fields, state)
    QsoService.log(parse("ea4abc,Juan", mode_name="SSB").fields, state)
    assert ContactService.count() == 1


def test_an_existing_entry_is_never_overwritten(state):
    """What the operator typed into the book outlives a single QSO."""
    ContactService.create("EA4ABC", first_name="Juanito", city="Alcalá")
    QsoService.log(parse("ea4abc,Juan Garcia,59,57,Madrid", mode_name="SSB").fields, state)

    entry = ContactService.lookup("EA4ABC")
    assert entry.first_name == "Juanito"
    assert entry.city == "Alcalá"
    assert entry.source == "manual"


def test_portable_operation_files_under_the_home_callsign(state):
    """F/EA4ABC/P is the same person, not a second contact."""
    QsoService.log(parse("ea4abc,Juan", mode_name="SSB").fields, state)
    QsoService.log(parse("f/ea4abc/p,Juan", mode_name="SSB").fields, state)
    assert ContactService.count() == 1
    assert ContactService.lookup("EA4ABC").callsign == "EA4ABC"


def test_a_callsign_without_a_name_still_gets_an_entry(state):
    QsoService.log(parse("dl2jkl", mode_name="SSB").fields, state)
    entry = ContactService.lookup("DL2JKL")
    assert entry is not None
    assert entry.first_name == ""
    assert entry.country == "Germany"


def test_automatic_bookkeeping_can_be_switched_off(state):
    state.add_to_book = False
    QsoService.log(parse("ea4abc,Juan", mode_name="SSB").fields, state)
    assert ContactService.count() == 0


def test_manually_dated_qsos_also_feed_the_book(state):
    import datetime as dt

    QsoService.log(
        parse("ea3mno,Laia", mode_name="SSB").fields,
        state,
        qso_utc=dt.datetime(2026, 1, 15, 12, 30),
    )
    assert ContactService.lookup("EA3MNO") is not None


def test_importing_adif_does_not_flood_the_book(tmp_path, state):
    """A file of thousands of QSOs must not become thousands of contacts."""
    from ic705suite.apps.log.core.services import OperatorService

    QsoService.log(parse("ea4abc,Juan", mode_name="SSB").fields, state)
    QsoService.log(parse("dl2jkl,Hans", mode_name="SSB").fields, state)
    before = ContactService.count()

    path, _ = transfer.export_adif(tmp_path / "log.adi")
    other = OperatorService.create("EA7XX", "Otro")
    transfer.import_adif(path, operator_id=other.id, respect_file_operator=False)

    assert ContactService.count() == before


def test_logging_fills_what_the_book_entry_lacks_and_nothing_else(state):
    """Name and QTH heard on air complete an entry that has none."""
    from ic705suite.apps.log.core.services import ContactService, QsoService

    state.autofill_from_book = False
    first = QsoService.log({"call": "EA7LFZ"}, state)
    entry = ContactService.lookup("EA7LFZ")
    assert (entry.first_name, entry.city) == ("", "")

    QsoService.log({"call": "EA7LFZ", "name": "Luis Miguel", "qth": "Cádiz"}, state)
    entry = ContactService.lookup("EA7LFZ")
    assert (entry.first_name, entry.last_name, entry.city) == ("Luis Miguel", "", "Cádiz")
    # The earlier QSO is left as it was logged.
    earlier = QsoService.get(first.id)
    assert (earlier.name, earlier.qth) == ("", "")

    # What the book already has is not replaced by a later QSO.
    QsoService.log({"call": "EA7LFZ/P", "name": "Otro", "qth": "Jerez"}, state)
    entry = ContactService.lookup("EA7LFZ")
    assert (entry.first_name, entry.city) == ("Luis Miguel", "Cádiz")


def test_an_entry_with_a_name_still_takes_a_missing_qth(state):
    from ic705suite.apps.log.core.services import ContactService, QsoService

    ContactService.create("EA7LFZ", first_name="Luis", source="import")
    state.autofill_from_book = False
    QsoService.log({"call": "EA7LFZ", "name": "Luisito", "qth": "Cádiz"}, state)
    entry = ContactService.lookup("EA7LFZ")
    assert (entry.first_name, entry.city) == ("Luis", "Cádiz")
