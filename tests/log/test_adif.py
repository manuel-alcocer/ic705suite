"""ADIF export, import and round-tripping."""

from __future__ import annotations

import datetime as dt

from ic705suite.apps.log.adif import parse_adif, qso_to_adif
from ic705suite.apps.log.core import transfer
from ic705suite.apps.log.core.entry import parse
from ic705suite.apps.log.core.services import OperatorService, QsoService
from ic705suite.apps.log.core.state import SessionState


def log_line(line: str, state: SessionState, **kwargs):
    parsed = parse(line, field_order=state.field_order, mode_name=state.mode)
    return QsoService.log(parsed.fields, state, digital=parsed.digital, **kwargs)


def test_record_carries_the_standard_fields(state):
    row = log_line("ea4abc,juan,59,57,Madrid,una nota", state)
    record = qso_to_adif(row)
    for fragment in (
        "<CALL:6>EA4ABC",
        "<BAND:3>40m",
        "<FREQ:8>7.130000",
        "<MODE:3>SSB",
        "<RST_SENT:2>59",
        "<RST_RCVD:2>57",
        "<NAME:4>Juan",
        "<QTH:6>Madrid",
        "<EOR>",
    ):
        assert fragment in record, fragment


def test_digital_voice_uses_the_adif_submode(state):
    """DMR is not an ADIF mode: it is DIGITALVOICE with SUBMODE=DMR."""
    state.set_mode("DMR")
    state.digital_data = {"talkgroup": "21466", "network": "Brandmeister"}
    row = log_line("ea5zz,pepe", state)
    record = qso_to_adif(row)
    assert "<MODE:12>DIGITALVOICE" in record
    assert "<SUBMODE:3>DMR" in record
    # Values ADIF has no field for travel as application fields.
    assert "<APP_HAMRLOG_TALKGROUP:5>21466" in record
    assert "<APP_HAMRLOG_NETWORK:12>Brandmeister" in record


def test_declared_lengths_match_the_values(state):
    """A wrong length breaks every other ADIF reader."""
    row = log_line("ea4abc,juan,59,57,Sevilla,con acentos áéíóú", state)
    for field in qso_to_adif(row).split("<")[1:]:
        if field.startswith("EOR"):
            continue
        header, _, value = field.partition(">")
        name, _, declared = header.partition(":")
        declared = declared.split(":")[0]
        assert len(value) == int(declared), name


def test_export_then_import_preserves_the_log(tmp_path, state):
    log_line("ea4abc,juan,59,57,Madrid,una nota", state)
    state.set_mode("CW")
    log_line("dl2jkl,hans,599,579", state)
    state.set_mode("DMR")
    state.digital_data = {"talkgroup": "214"}
    log_line("ea5zz,pepe", state)

    path, count = transfer.export_adif(tmp_path / "log.adi")
    assert count == 3

    other = OperatorService.create("EA7XX", "Otro operador")
    report = transfer.import_adif(path, operator_id=other.id, respect_file_operator=False)
    assert report.imported == 3
    assert report.skipped_invalid == 0

    imported = QsoService.search(operator_id=other.id)
    by_call = {row.call: row for row in imported}
    assert by_call["EA4ABC"].qth == "Madrid"
    assert by_call["EA4ABC"].rst_rcvd == "57"
    assert by_call["DL2JKL"].mode == "CW"
    assert by_call["EA5ZZ"].mode == "DMR"
    assert by_call["EA5ZZ"].digital_data["talkgroup"] == "214"


def test_imported_contacts_are_editable(tmp_path, state):
    """Imported QSOs were not timed by this clock, so they must stay manual."""
    log_line("ea4abc,juan", state)
    path, _ = transfer.export_adif(tmp_path / "log.adi")
    other = OperatorService.create("EA7XX", "Otro")
    transfer.import_adif(path, operator_id=other.id, respect_file_operator=False)

    imported = QsoService.search(operator_id=other.id)[0]
    assert imported.entry_mode == "MANUAL"
    assert "name" in QsoService.editable_fields(imported.id)


def test_reimporting_the_same_file_skips_duplicates(tmp_path, state):
    log_line("ea4abc,juan", state)
    path, _ = transfer.export_adif(tmp_path / "log.adi")
    other = OperatorService.create("EA7XX", "Otro")

    first = transfer.import_adif(path, operator_id=other.id, respect_file_operator=False)
    second = transfer.import_adif(path, operator_id=other.id, respect_file_operator=False)
    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped_duplicate == 1


def test_parses_a_file_written_by_another_program():
    """Mixed case tags, no header and unknown fields must not break the import."""
    text = (
        "<call:6>EA4ABC<qso_date:8>20260115<time_on:4>1230<band:3>20m"
        "<mode:3>SSB<rst_sent:2>59<rst_rcvd:2>59<my_own_field:3>abc<eor>"
    )
    records = parse_adif(text)
    assert len(records) == 1
    record = records[0]
    assert record.fields["call"] == "EA4ABC"
    assert record.fields["band"] == "20m"
    assert record.qso_utc == dt.datetime(2026, 1, 15, 12, 30)
    # Unknown fields survive so a re-export loses nothing.
    assert record.extra["MY_OWN_FIELD"] == "abc"


def test_record_without_a_date_is_reported_as_invalid(tmp_path, state):
    path = tmp_path / "broken.adi"
    path.write_text("<CALL:6>EA4ABC<BAND:3>20m<EOR>\n", encoding="utf-8")
    report = transfer.import_adif(path, operator_id=state.operator_id)
    assert report.imported == 0
    assert report.skipped_invalid == 1


def test_csv_export_has_a_header_and_one_row_per_contact(tmp_path, state):
    log_line("ea4abc,juan,59,57", state)
    log_line("dl2jkl,hans", state)
    path, count = transfer.export_csv(tmp_path / "log.csv")
    lines = path.read_text(encoding="utf-8-sig").strip().splitlines()
    assert count == 2
    assert len(lines) == 3
    assert lines[0].startswith("fecha_utc;hora_utc;indicativo")
    assert "EA4ABC" in lines[1] or "EA4ABC" in lines[2]
