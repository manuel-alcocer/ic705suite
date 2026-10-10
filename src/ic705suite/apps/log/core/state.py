"""Working configuration of a radio session.

This is what the status bar shows and what every new QSO inherits. It is held
in memory while the application runs and persisted between runs so the
operator finds the same setup on the next start.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import bands, modes, units
from .entry import DEFAULT_FIELD_ORDER, DEFAULT_VALIDATION


@dataclass(slots=True)
class SessionState:
    """Defaults applied to every logged contact.

    Attributes:
        operator_id: Local operator logging the contacts.
        station_id: Rig in use.
        antenna_id: Antenna connected to it, one of the station's.
        equipment_id: Equipment set («setup») in use; it decides the station
            and antenna of each QSO from the frequency.
        repeater_id: Repeater being worked through, None for simplex.
        repeater_call: Its callsign, kept here to avoid a query per contact.
        band: ADIF band name, e.g. "40m".
        freq_hz: Frequency as tuned: the repeater output when using one.
        freq_tx_hz: Actual transmit frequency when it differs (repeater input).
        mode: Mode name as shown in the UI, e.g. "C4FM".
        power_w: Transmit power, None when not stated.
        digital_data: Mode specific values (talkgroup, reflector, room...).
        field_order: Positional mapping of the fast entry line.
        separator: Token separator of the fast entry line.
        callsign_validation: "strict", "warn" or "off".
        freq_unit: Unit frequencies are shown in and assumed when typed.
        decimal_separator: Decimal separator for frequencies.
        thousands_separator: Thousands separator, empty to hide grouping.
        autofill_from_book: Fill missing name and QTH from the address book.
        add_to_book: Add a station to the address book the first time it is
            worked, so the book grows with the log.
        history_order: "asc" shows the oldest QSO first and grows downwards,
            "desc" puts the newest at the top.
        profile_id: The active profile, None when none is or when the
            operator has changed what it set.
        profile_name: Its name, for the status line.
    """

    operator_id: int | None = None
    station_id: int | None = None
    antenna_id: int | None = None
    equipment_id: int | None = None
    repeater_id: int | None = None
    repeater_call: str = ""
    band: str = ""
    freq_hz: int | None = None
    freq_tx_hz: int | None = None
    mode: str = modes.DEFAULT_MODE
    power_w: int | None = None
    digital_data: dict[str, str] = field(default_factory=dict)
    field_order: tuple[str, ...] = DEFAULT_FIELD_ORDER
    separator: str = ","
    callsign_validation: str = DEFAULT_VALIDATION
    freq_unit: str = units.UNIT_MHZ
    decimal_separator: str = "."
    thousands_separator: str = ""
    autofill_from_book: bool = True
    add_to_book: bool = True
    history_order: str = "asc"
    profile_id: int | None = None
    profile_name: str = ""

    @property
    def via_repeater(self) -> bool:
        return self.repeater_id is not None

    @property
    def frequency_format(self) -> units.FrequencyFormat:
        """How frequencies should be shown and read.

        Falls back to the default when the stored settings are unusable, so a
        hand-edited value can never stop the application from starting.
        """
        try:
            return units.FrequencyFormat(
                self.freq_unit, self.decimal_separator, self.thousands_separator
            )
        except units.UnitError:
            return units.FrequencyFormat()

    def apply_frequency_format(self) -> None:
        """Make this state's format the one the whole application uses."""
        units.set_active(self.frequency_format)

    def leave_profile(self) -> None:
        """The operator changed by hand what the active profile set.

        Its values stay in use, but they are no longer that profile.
        """
        self.profile_id = None
        self.profile_name = ""

    def set_band(self, band_name: str, *, move_frequency: bool = True) -> None:
        """Select a band and, unless told otherwise, jump to its default frequency.

        Choosing a band by hand means leaving the repeater: its frequencies no
        longer describe where the operator is working.
        """
        band = bands.get(band_name)
        if band is None:
            return
        self.clear_repeater()
        self.band = band.name
        if move_frequency or self.freq_hz is None or not band.contains(self.freq_hz):
            self.freq_hz = band.default_hz

    def set_frequency(self, freq_hz: int) -> None:
        """Set the frequency and keep the band consistent with it."""
        self.clear_repeater()
        self.freq_hz = freq_hz
        band = bands.from_frequency(freq_hz)
        if band is not None:
            self.band = band.name

    def clear_repeater(self) -> None:
        """Go back to simplex, leaving the frequency and mode as they are."""
        self.repeater_id = None
        self.repeater_call = ""
        self.freq_tx_hz = None

    def set_repeater(
        self,
        repeater_id: int,
        callsign: str,
        *,
        output_hz: int | None,
        input_hz: int | None,
        band: str,
        mode: str,
        digital_data: dict[str, str] | None = None,
    ) -> None:
        """Work through a repeater, adopting everything it defines.

        The repeater is a complete working setup: after selecting one the
        operator can log contacts without touching anything else.
        """
        if band:
            self.band = band
        elif output_hz:
            found = bands.from_frequency(output_hz)
            if found:
                self.band = found.name
        if output_hz:
            self.freq_hz = output_hz
        if mode:
            self.set_mode(mode)
        if digital_data:
            self.digital_data = dict(digital_data)
        # Assigned last: set_mode and set_band both clear the repeater.
        self.freq_tx_hz = input_hz
        self.repeater_id = repeater_id
        self.repeater_call = callsign

    def set_mode(self, mode_name: str) -> None:
        """Select a mode, dropping digital values that no longer apply."""
        mode = modes.get(mode_name)
        if mode is None:
            return
        self.mode = mode.name
        valid_keys = {key for key, _label in mode.digital_fields}
        self.digital_data = {k: v for k, v in self.digital_data.items() if k in valid_keys}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["field_order"] = list(self.field_order)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        """Rebuild from persisted data, ignoring unknown or stale keys."""
        known = {f for f in cls.__slots__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in (data or {}).items() if k in known}
        order = payload.get("field_order")
        if order:
            payload["field_order"] = tuple(order)
        else:
            payload.pop("field_order", None)
        return cls(**payload)
