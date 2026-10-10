"""Plain data objects handed to the UI.

Returning detached ORM instances to a Textual widget invites lazy-load errors,
so read paths return these instead. They are also the natural shape for the
future REST API serialisers.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from ..i18n import _


@dataclass(frozen=True, slots=True)
class QsoRow:
    """One row of the history panel."""

    id: int
    call: str
    name: str
    qso_utc: dt.datetime
    band: str
    freq_hz: int | None
    freq_tx_hz: int | None
    mode: str
    rst_sent: str
    rst_rcvd: str
    qth: str
    gridsquare: str
    country: str
    comment: str
    entry_mode: str
    operator_callsign: str
    station_name: str
    repeater_call: str = ""
    antenna_name: str = ""
    digital_data: dict[str, Any] = field(default_factory=dict)
    #: Equipment set assigned to the QSO, empty when none.
    equipment_name: str = ""
    #: True when the set cannot work the QSO's frequency or band.
    equipment_mismatch: bool = False
    #: Name the address book has for the station, empty when none.
    book_name: str = ""
    #: True when the QSO's name differs from the address book's («drift»).
    name_drift: bool = False

    @property
    def is_manual(self) -> bool:
        return self.entry_mode == "MANUAL"

    @property
    def via_repeater(self) -> bool:
        return bool(self.repeater_call)


@dataclass(frozen=True, slots=True)
class ContactRow:
    """One row of the address book view."""

    id: int
    callsign: str
    first_name: str
    last_name: str
    dmr_id: int | None
    city: str
    state: str
    country: str
    gridsquare: str
    email: str
    notes: str
    source: str
    is_favorite: bool
    #: How many QSOs the log holds with this station, filled on demand.
    qso_count: int = 0

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)

    @property
    def summary(self) -> str:
        """One line for the entry panel: 'Manuel · Sevilla · DMR 2147001'."""
        parts = [self.full_name, self.city or self.state]
        if self.dmr_id:
            parts.append(f"DMR {self.dmr_id}")
        return " · ".join(part for part in parts if part)


@dataclass(frozen=True, slots=True)
class PartRow:
    """A radio, antenna or supply as the QSO card lists it."""

    code: str
    name: str
    #: Power and ranges, bands, or voltage and current.
    details: str = ""


@dataclass(frozen=True, slots=True)
class QsoCard:
    """Everything known about a logged QSO, for the card V opens."""

    row: QsoRow
    #: The station worked, as the address book has it.
    contact: ContactRow | None = None
    operator_name: str = ""
    operator_gridsquare: str = ""
    operator_qth: str = ""
    power_w: int | None = None
    #: Radio and antenna of the QSO when it has no setup (older QSOs).
    station: PartRow | None = None
    antenna: PartRow | None = None
    #: The repeater: «ED7ZAL · out 145.600 · in 145.000 · tone 88.5».
    repeater: str = ""
    setup_name: str = ""
    setup_notes: str = ""
    radios: tuple[PartRow, ...] = ()
    antennas: tuple[PartRow, ...] = ()
    supplies: tuple[PartRow, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Outcome of importing an address book file."""

    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    format_name: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return _(
            "{created} new, {updated} updated, {skipped} skipped of {total} records."
        ).format(
            created=self.created, updated=self.updated, skipped=self.skipped, total=self.total
        )


@dataclass(frozen=True, slots=True)
class LogStats:
    """Counters shown in the footer and exported to Prometheus."""

    total: int
    today: int
    unique_calls: int
    countries: int
    by_band: dict[str, int] = field(default_factory=dict)
    by_mode: dict[str, int] = field(default_factory=dict)
