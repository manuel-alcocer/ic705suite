"""Address book file formats.

DMR user lists circulate in several shapes — RadioID.net, BrandMeister, the
CSV a radio's programming software expects — and they change over time. Rather
than hard-coding each one, the reader normalises the column headers and maps
them onto canonical fields, so a file with familiar columns in an unfamiliar
order still imports.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..i18n import N_, _
from .callsign import CALLSIGN_RE

#: Canonical field -> header spellings seen in the wild, lower-cased and with
#: punctuation stripped. Order matters only for readability.
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "dmr_id": (
        "radioid", "radio id", "radio_id", "dmrid", "dmr id", "dmr_id", "id",
        "subscriber id", "subscriberid",
    ),
    "callsign": ("callsign", "call sign", "call", "indicativo", "cs"),
    "first_name": ("firstname", "first name", "first_name", "fname", "name", "nombre"),
    "last_name": (
        "lastname", "last name", "last_name", "lname", "surname", "apellidos", "apellido",
    ),
    "city": ("city", "ciudad", "qth", "town", "localidad"),
    "state": ("state", "provincia", "province", "region", "región", "estado"),
    "country": ("country", "pais", "país", "nation"),
    "notes": ("remarks", "remark", "notes", "note", "notas", "comentario", "comment"),
    "email": ("email", "e-mail", "correo"),
    "gridsquare": ("gridsquare", "grid", "locator", "loc"),
}

#: Columns a radio's programming software adds that carry no contact data.
IGNORED_HEADERS: frozenset[str] = frozenset(
    {"no", "no.", "number", "index", "calltype", "call type", "callalert", "call alert"}
)

#: Positional layout of RadioID.net exports, used when a file has no header.
RADIOID_POSITIONAL: tuple[str, ...] = (
    "dmr_id", "callsign", "first_name", "last_name", "city", "state", "country", "notes",
)


@dataclass(slots=True)
class ContactRecord:
    """One address book entry read from a file."""

    callsign: str = ""
    dmr_id: int | None = None
    first_name: str = ""
    last_name: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    gridsquare: str = ""
    email: str = ""
    notes: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)


@dataclass(slots=True)
class ReadReport:
    """Outcome of reading a contact file."""

    records: list[ContactRecord] = field(default_factory=list)
    #: Human readable description of the detected layout, shown to the user.
    format_name: str = ""
    total_rows: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def _normalize_header(text: str) -> str:
    """Lower-case a header and strip the punctuation formats disagree on."""
    cleaned = text.strip().lower().lstrip("﻿")
    for character in ("_", "-", "."):
        cleaned = cleaned.replace(character, " ")
    return " ".join(cleaned.split())


def _map_headers(headers: Iterable[str]) -> dict[int, str]:
    """Map column positions to canonical field names.

    Returns:
        Position -> field name, leaving out columns that match nothing.
    """
    lookup: dict[str, str] = {}
    for field_name, spellings in HEADER_ALIASES.items():
        for spelling in spellings:
            lookup.setdefault(_normalize_header(spelling), field_name)

    mapping: dict[int, str] = {}
    used: set[str] = set()
    for position, raw in enumerate(headers):
        normalized = _normalize_header(raw)
        if not normalized or normalized in IGNORED_HEADERS:
            continue
        field_name = lookup.get(normalized)
        # "name" maps to first_name, but only if first_name is still free:
        # a file with both "Name" and "First Name" should not collide.
        if field_name is None or field_name in used:
            continue
        mapping[position] = field_name
        used.add(field_name)
    return mapping


def _looks_like_header(row: list[str]) -> bool:
    """True when the first row names columns instead of holding data."""
    return bool(_map_headers(row)) and not (row and row[0].strip().isdigit())


def _looks_like_radioid_row(row: list[str]) -> bool:
    """True when a header-less row really is a RadioID style record.

    Checks the two columns that carry identity: a plausible DMR ID and a
    callsign-shaped second field. Without this an ordinary CSV would be read
    as contacts and fill the address book with rubbish.
    """
    if len(row) < 2:
        return False
    has_id = _parse_dmr_id(row[0]) is not None
    has_call = bool(CALLSIGN_RE.match(row[1].strip().upper()))
    return has_id and has_call


def _parse_dmr_id(text: str) -> int | None:
    """Parse a DMR ID, ignoring separators and obviously invalid values."""
    digits = "".join(character for character in str(text) if character.isdigit())
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    # DMR IDs are 7 digits for users; hotspots add an eighth.
    return value if 1_000 <= value <= 99_999_999 else None


def _build_record(values: dict[str, str]) -> ContactRecord | None:
    """Turn canonical field values into a record, or None when unusable."""
    callsign = values.get("callsign", "").strip().upper()
    dmr_id = _parse_dmr_id(values.get("dmr_id", ""))
    if not callsign and dmr_id is None:
        return None

    first = values.get("first_name", "").strip()
    last = values.get("last_name", "").strip()
    # Some exports put the whole name in one column.
    if first and not last and " " in first and len(first.split()) > 1:
        head, _sep, tail = first.partition(" ")
        first, last = head, tail.strip()

    return ContactRecord(
        callsign=callsign,
        dmr_id=dmr_id,
        first_name=first,
        last_name=last,
        city=values.get("city", "").strip(),
        state=values.get("state", "").strip(),
        country=values.get("country", "").strip(),
        gridsquare=values.get("gridsquare", "").strip().upper(),
        email=values.get("email", "").strip(),
        notes=values.get("notes", "").strip(),
    )


def _sniff_delimiter(sample: str) -> str:
    """Guess the column separator, defaulting to a comma."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Fall back to whichever candidate appears most on the first line.
        first_line = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {candidate: first_line.count(candidate) for candidate in ",;\t|"}
        best = max(counts, key=lambda key: counts[key])
        return best if counts[best] else ","


def read_csv(text: str) -> ReadReport:
    """Read a delimited contact list, detecting separator and columns."""
    report = ReadReport()
    sample = text[:8192]
    delimiter = _sniff_delimiter(sample)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    rows = iter(reader)
    try:
        first = next(rows)
    except StopIteration:
        report.warnings.append(_("The file is empty."))
        return report

    if _looks_like_header(first):
        mapping = _map_headers(first)
        report.format_name = _("CSV with header ({delimiter} as separator)").format(
            delimiter=repr(delimiter)
        )
    elif _looks_like_radioid_row(first):
        mapping = dict(enumerate(RADIOID_POSITIONAL))
        report.format_name = _("CSV without header, RadioID.net order")
        rows = iter([first, *rows])
    else:
        # Without a recognisable header, the only safe fallback is the
        # RadioID column order. Assuming it for an unrelated CSV would fill
        # the address book with nonsense, so refuse instead.
        report.warnings.append(
            _(
                "Unknown format: there are no known headers and the first row does "
                "not look like RadioID.net (DMR ID, callsign, name...). Check that "
                "the file is a contact list."
            )
        )
        return report

    if not mapping:
        report.warnings.append(
            _(
                "No column was recognised. At least a callsign (CALLSIGN) or "
                "a DMR ID (RADIO_ID) is expected."
            )
        )
        return report

    for row in rows:
        if not any(cell.strip() for cell in row):
            continue
        report.total_rows += 1
        values = {
            name: row[position]
            for position, name in mapping.items()
            if position < len(row)
        }
        record = _build_record(values)
        if record is None:
            report.skipped += 1
            continue
        report.records.append(record)

    return report


#: JSON key -> canonical field, covering the RadioID and BrandMeister APIs.
_JSON_KEYS: dict[str, str] = {
    "id": "dmr_id", "radio_id": "dmr_id", "dmr_id": "dmr_id", "radioid": "dmr_id",
    "callsign": "callsign", "call": "callsign",
    "fname": "first_name", "first_name": "first_name", "name": "first_name",
    "surname": "last_name", "last_name": "last_name",
    "city": "city", "state": "state", "country": "country",
    "remarks": "notes", "notes": "notes",
}


def read_json(text: str) -> ReadReport:
    """Read the JSON the RadioID and BrandMeister APIs return.

    Accepts a bare list of objects or an object wrapping one under any key,
    which is how both services have shaped their responses over time.
    """
    report = ReadReport(format_name="JSON")
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        report.warnings.append(_("Invalid JSON: {error}").format(error=exc))
        return report

    entries: list[Any] | None = None
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                entries = value
                break
    if entries is None:
        report.warnings.append(_("No contact list was found in the JSON."))
        return report

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        report.total_rows += 1
        values = {
            _JSON_KEYS[key.lower()]: str(value)
            for key, value in entry.items()
            if key.lower() in _JSON_KEYS and value is not None
        }
        record = _build_record(values)
        if record is None:
            report.skipped += 1
            continue
        report.records.append(record)

    return report


def read_file(path: str | Path) -> ReadReport:
    """Read a contact list, choosing the parser from the content.

    The extension is only a hint: files named ``.csv`` that hold JSON, and
    files with no extension at all, both turn up in practice.
    """
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")

    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return read_json(text)
    return read_csv(text)


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #

#: Export layouts -> description, in English; translate it with _() where shown.
EXPORT_FORMATS: dict[str, str] = {
    "hamrlog": N_("hamrlog CSV, with Spanish headers"),
    "radioid": N_("RadioID.net / BrandMeister CSV"),
    "anytone": N_("CSV for the Anytone CPS (D878UV and compatibles)"),
}


def _rows_for(export_format: str, records: Iterable[ContactRecord]) -> Iterator[list[Any]]:
    """Yield the header row followed by one row per record."""
    if export_format == "radioid":
        yield [
            "RADIO_ID", "CALLSIGN", "FIRST_NAME", "LAST_NAME", "CITY", "STATE",
            "COUNTRY", "REMARKS",
        ]
        for record in records:
            yield [
                record.dmr_id or "", record.callsign, record.first_name,
                record.last_name, record.city, record.state, record.country,
                record.notes,
            ]
    elif export_format == "anytone":
        # The CPS needs a sequential number and the call type columns; the
        # radio ignores contacts without an ID, so those are left out.
        yield [
            "No.", "Radio ID", "Callsign", "Name", "City", "State", "Country",
            "Remarks", "Call Type", "Call Alert",
        ]
        number = 0
        for record in records:
            if record.dmr_id is None:
                continue
            number += 1
            yield [
                number, record.dmr_id, record.callsign, record.full_name,
                record.city, record.state, record.country, record.notes,
                "Private Call", "None",
            ]
    else:
        yield [
            "indicativo", "nombre", "apellidos", "dmr_id", "ciudad", "provincia",
            "pais", "locator", "email", "notas",
        ]
        for record in records:
            yield [
                record.callsign, record.first_name, record.last_name,
                record.dmr_id or "", record.city, record.state, record.country,
                record.gridsquare, record.email, record.notes,
            ]


def write_csv(
    path: str | Path, records: Iterable[ContactRecord], *, export_format: str = "hamrlog"
) -> int:
    """Write contacts in one of the supported layouts.

    Returns:
        Number of contacts written, which may be fewer than supplied when the
        format requires a field the record lacks.
    """
    if export_format not in EXPORT_FORMATS:
        raise ValueError(f"Unknown export format: {export_format}")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # The CPS of most radios expects plain commas and CRLF line endings.
    delimiter = ";" if export_format == "hamrlog" else ","
    encoding = "utf-8-sig" if export_format == "hamrlog" else "utf-8"

    written = 0
    with target.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\r\n")
        for index, row in enumerate(_rows_for(export_format, records)):
            writer.writerow(row)
            if index:
                written += 1
    return written
