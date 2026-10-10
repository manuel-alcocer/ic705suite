"""Import and export of the log (ADIF and CSV).

Kept apart from ``services`` because it is I/O bound and the TUI runs it in a
worker thread, while the rest of the services are called from the event loop.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select

from ..adif import read_adif_file, write_adif_file
from ..adif.reader import AdifRecord
from ..db.models import Contact, EntryMode, Qso
from ..db.session import session_scope
from ..i18n import _
from ..paths import export_dir
from . import bands, callsign
from . import contacts as contact_files
from .dto import ContactRow, ImportSummary, QsoRow
from .services import (
    ContactService,
    OperatorService,
    QsoService,
    ServiceError,
    StationService,
)

CSV_COLUMNS = [
    "fecha_utc", "hora_utc", "indicativo", "nombre", "banda", "frecuencia_hz", "modo",
    "rst_enviado", "rst_recibido", "qth", "locator", "pais", "comentario",
    "operador", "equipo", "tipo_entrada",
]


@dataclass(slots=True)
class ImportReport:
    """Outcome of an ADIF import."""

    total: int = 0
    imported: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    @property
    def summary(self) -> str:
        return _(
            "{imported} imported, {duplicates} duplicates, {invalid} invalid "
            "of {total} records."
        ).format(
            imported=self.imported,
            duplicates=self.skipped_duplicate,
            invalid=self.skipped_invalid,
            total=self.total,
        )


def _source_label(format_name: str) -> str:
    """Short tag recorded on imported entries, from the detected format."""
    lowered = format_name.lower()
    if "radioid" in lowered:
        return "radioid"
    if "json" in lowered:
        return "json"
    return "importado"


def default_export_path(extension: str) -> Path:
    """Timestamped file inside the user's export directory."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return export_dir() / f"hamrlog-{stamp}.{extension}"


def _station_lookup() -> dict[str, dict[str, str]]:
    """Station name -> rig details, used to fill MY_RIG and TX_PWR.

    The antenna is not here: it belongs to each QSO, not to the station.
    """
    lookup: dict[str, dict[str, str]] = {}
    for station in StationService.list_all():
        lookup[station.name] = {
            "rig": station.rig,
            "power_w": str(station.power_w) if station.power_w else "",
        }
    return lookup


def export_adif(
    path: str | Path | None = None, rows: list[QsoRow] | None = None
) -> tuple[Path, int]:
    """Export contacts to ADI.

    Args:
        path: Destination; defaults to a timestamped file in the export dir.
        rows: Contacts to export; defaults to the whole log.

    Returns:
        The path written and the number of records.
    """
    target = Path(path) if path else default_export_path("adi")
    data = rows if rows is not None else QsoService.search(limit=1_000_000)
    count = write_adif_file(target, data, stations=_station_lookup())
    return target, count


def export_csv(
    path: str | Path | None = None, rows: list[QsoRow] | None = None
) -> tuple[Path, int]:
    """Export contacts to CSV with Spanish column headers."""
    target = Path(path) if path else default_export_path("csv")
    data = rows if rows is not None else QsoService.search(limit=1_000_000)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(CSV_COLUMNS)
        for row in data:
            writer.writerow(
                [
                    row.qso_utc.strftime("%Y-%m-%d"),
                    row.qso_utc.strftime("%H:%M:%S"),
                    row.call,
                    row.name,
                    row.band,
                    row.freq_hz or "",
                    row.mode,
                    row.rst_sent,
                    row.rst_rcvd,
                    row.qth,
                    row.gridsquare,
                    row.country,
                    row.comment,
                    row.operator_callsign,
                    row.station_name,
                    row.entry_mode,
                ]
            )
    return target, len(data)


def import_adif(
    path: str | Path,
    *,
    operator_id: int,
    skip_duplicates: bool = True,
    respect_file_operator: bool = True,
) -> ImportReport:
    """Import an ADI file into the log.

    Imported contacts keep their original timestamp, so they are stored as
    MANUAL entries: they were not timed by this application's clock and must
    stay fully editable.

    Args:
        path: ADI file to read.
        operator_id: Operator the contacts are assigned to when the file does
            not name a known one.
        skip_duplicates: Skip a record when the same callsign, band, mode and
            minute already exist.
        respect_file_operator: When true, a record whose OPERATOR field names a
            known local operator is assigned to them. When false, every record
            goes to ``operator_id``, which is what you want when merging
            somebody else's log into your own.
    """
    records = read_adif_file(path)
    report = ImportReport(total=len(records))

    fallback = OperatorService.get(operator_id)
    if fallback is None:
        raise ServiceError(_("Target operator not found."))

    operator_ids = {op.callsign: op.id for op in OperatorService.list_all(include_inactive=True)}
    station_ids = {st.name: st.id for st in StationService.list_all()}

    for record in records:
        if not record.call or record.qso_utc is None:
            report.skipped_invalid += 1
            continue

        owner_id = operator_id
        if respect_file_operator and record.operator_callsign:
            owner_id = operator_ids.get(
                callsign.normalize(record.operator_callsign), operator_id
            )
        station_id = station_ids.get(record.station_name) if record.station_name else None

        if skip_duplicates and _exists(record, owner_id):
            report.skipped_duplicate += 1
            continue

        try:
            _insert(record, owner_id, station_id)
            report.imported += 1
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the import
            report.skipped_invalid += 1
            report.errors.append(f"{record.call}: {exc}")

    return report


def _exists(record: AdifRecord, operator_id: int) -> bool:
    """True when an identical contact is already logged, to the minute."""
    assert record.qso_utc is not None
    minute_start = record.qso_utc.replace(second=0, microsecond=0)
    minute_end = minute_start + dt.timedelta(minutes=1)
    with session_scope() as session:
        query = (
            session.query(Qso.id)
            .filter(Qso.operator_id == operator_id)
            .filter(Qso.base_call == callsign.base_call(record.call))
            .filter(Qso.qso_utc >= minute_start, Qso.qso_utc < minute_end)
        )
        band = record.fields.get("band")
        if band:
            query = query.filter(Qso.band == band)
        mode = record.fields.get("mode")
        if mode:
            query = query.filter(Qso.mode == mode)
        return session.query(query.exists()).scalar() or False


def _insert(record: AdifRecord, operator_id: int, station_id: int | None) -> None:
    """Persist one imported record."""
    assert record.qso_utc is not None
    fields = record.fields
    freq_hz = fields.get("freq_hz")
    band = str(fields.get("band") or "")
    if not band and freq_hz:
        found = bands.from_frequency(int(freq_hz))
        band = found.name if found else ""

    with session_scope() as session:
        session.add(
            Qso(
                operator_id=operator_id,
                station_id=station_id,
                call=callsign.normalize(str(fields.get("call", ""))),
                base_call=callsign.base_call(str(fields.get("call", ""))),
                name=str(fields.get("name", "")),
                qso_utc=record.qso_utc,
                band=band,
                freq_hz=int(freq_hz) if freq_hz else None,
                freq_tx_hz=int(fields["freq_tx_hz"]) if fields.get("freq_tx_hz") else None,
                mode=str(fields.get("mode", "")),
                repeater_call=record.repeater_call,
                rst_sent=str(fields.get("rst_sent", "")),
                rst_rcvd=str(fields.get("rst_rcvd", "")),
                qth=str(fields.get("qth", "")),
                gridsquare=str(fields.get("gridsquare", "")),
                country=str(fields.get("country", "")),
                comment=str(fields.get("comment", "")),
                power_w=fields.get("power_w"),
                # Imported contacts were not timed by this clock.
                entry_mode=EntryMode.MANUAL,
                digital_data=dict(record.digital),
                extra=dict(record.extra),
            )
        )


# --------------------------------------------------------------------------- #
# Address book
# --------------------------------------------------------------------------- #

def import_contacts(
    path: str | Path,
    *,
    update_existing: bool = True,
    source: str | None = None,
    country_filter: str = "",
) -> ImportSummary:
    """Import an address book file, detecting its format.

    A national DMR user list runs to tens of thousands of rows, so existing
    entries are fetched once and the writes go out in batches rather than one
    statement per contact.

    Args:
        path: File to read: CSV or JSON, from RadioID, BrandMeister, a radio's
            programming software or hamrlog itself.
        update_existing: Fill in blanks on entries already in the book. Values
            already stored are never overwritten with worse ones.
        source: Label recorded on each entry; defaults to the detected format.
        country_filter: Keep only rows whose country contains this text, for
            trimming a worldwide list down to one country.

    Returns:
        A summary of what was created, updated and skipped.
    """
    report = contact_files.read_file(path)
    if not report.records:
        return ImportSummary(
            total=report.total_rows,
            skipped=report.total_rows,
            format_name=report.format_name,
            warnings=tuple(report.warnings),
        )

    label = source or _source_label(report.format_name)
    needle = country_filter.strip().lower()

    created = updated = skipped = 0
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    with session_scope() as session:
        # One pass over the existing book instead of a query per record.
        by_base: dict[str, int] = {}
        by_dmr: dict[int, int] = {}
        for contact_id, base, dmr in session.execute(
            select(Contact.id, Contact.base_call, Contact.dmr_id)
        ).all():
            if base:
                by_base[base] = contact_id
            if dmr is not None:
                by_dmr[dmr] = contact_id

        seen: set[int] = set()
        for record in report.records:
            if needle and needle not in record.country.lower():
                skipped += 1
                continue

            call = callsign.normalize(record.callsign)
            base = callsign.base_call(call) if call else ""
            if not base and record.dmr_id is None:
                skipped += 1
                continue

            existing_id = by_dmr.get(record.dmr_id) if record.dmr_id else None
            if existing_id is None and base:
                existing_id = by_base.get(base)

            if existing_id is not None:
                if not update_existing or existing_id in seen:
                    skipped += 1
                    continue
                seen.add(existing_id)
                updates.append({"id": existing_id, **_contact_values(record, base, label)})
                updated += 1
                continue

            # Guard against a file that repeats the same station.
            if base and base in by_base:
                skipped += 1
                continue
            if record.dmr_id is not None and record.dmr_id in by_dmr:
                skipped += 1
                continue

            values = _contact_values(record, base, label)
            values["callsign"] = call or f"DMR{record.dmr_id}"
            inserts.append(values)
            created += 1
            if base:
                by_base[base] = -1
            if record.dmr_id is not None:
                by_dmr[record.dmr_id] = -1

        for start in range(0, len(inserts), ContactService.BATCH_SIZE):
            session.execute(
                insert(Contact), inserts[start : start + ContactService.BATCH_SIZE]
            )
        if updates:
            _apply_contact_updates(session, updates)

    return ImportSummary(
        total=report.total_rows,
        created=created,
        updated=updated,
        skipped=skipped,
        format_name=report.format_name,
        warnings=tuple(report.warnings),
    )


def _contact_values(
    record: contact_files.ContactRecord, base: str, source: str
) -> dict[str, Any]:
    """Column values for one imported record."""
    return {
        "base_call": base,
        "dmr_id": record.dmr_id,
        "first_name": record.first_name,
        "last_name": record.last_name,
        "city": record.city,
        "state": record.state,
        "country": record.country or callsign.country_for(record.callsign),
        "gridsquare": record.gridsquare,
        "email": record.email,
        "notes": record.notes,
        "source": source,
    }


def _apply_contact_updates(session: Any, updates: list[dict[str, Any]]) -> None:
    """Fill blanks on existing entries without discarding what is stored.

    An import must not wipe a name the operator typed by hand just because
    the downloaded list has that field empty.
    """
    existing = {
        contact.id: contact
        for contact in session.scalars(
            select(Contact).where(Contact.id.in_([row["id"] for row in updates]))
        )
    }
    for row in updates:
        contact = existing.get(row["id"])
        if contact is None:
            continue
        for key, value in row.items():
            if key == "id":
                continue
            if key == "source":
                contact.source = value
                continue
            if value not in (None, "") and not getattr(contact, key, ""):
                setattr(contact, key, value)


def export_contacts(
    path: str | Path | None = None,
    *,
    export_format: str = "hamrlog",
    rows: list[ContactRow] | None = None,
) -> tuple[Path, int]:
    """Export the address book.

    Args:
        path: Destination; defaults to a timestamped file in the export dir.
        export_format: One of ``contact_files.EXPORT_FORMATS``. The "anytone"
            layout is what the CPS of a D878UV and compatible radios expects.
        rows: Contacts to export; defaults to the whole book.

    Returns:
        The path written and the number of entries.
    """
    target = Path(path) if path else default_export_path(
        "csv" if export_format == "hamrlog" else f"{export_format}.csv"
    )
    data = rows if rows is not None else ContactService.search(limit=1_000_000, with_counts=False)
    records = [
        contact_files.ContactRecord(
            callsign=row.callsign,
            dmr_id=row.dmr_id,
            first_name=row.first_name,
            last_name=row.last_name,
            city=row.city,
            state=row.state,
            country=row.country,
            gridsquare=row.gridsquare,
            email=row.email,
            notes=row.notes,
        )
        for row in data
    ]
    written = contact_files.write_csv(target, records, export_format=export_format)
    return target, written
