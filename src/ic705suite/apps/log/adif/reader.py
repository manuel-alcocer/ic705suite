"""ADIF import.

The parser is deliberately permissive: real world ADI files come from dozens
of programs, mix line endings, and sometimes declare lengths that disagree
with the data. Anything unrecognised is preserved in ``AdifRecord.extra`` so
importing and re-exporting never loses information.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import bands, callsign, modes

# <NAME:5:S>Victor  ->  name, length, optional type
_TAG_RE = re.compile(r"<([A-Za-z0-9_]+)(?::(\d+))?(?::([A-Za-z]))?>", re.IGNORECASE)

APP_FIELD_PREFIX = "APP_HAMRLOG_"

#: ADIF field -> Qso attribute. Fields absent here land in ``extra``.
_FIELD_MAP: dict[str, str] = {
    "CALL": "call",
    "NAME": "name",
    "BAND": "band",
    "RST_SENT": "rst_sent",
    "RST_RCVD": "rst_rcvd",
    "QTH": "qth",
    "GRIDSQUARE": "gridsquare",
    "COUNTRY": "country",
    "COMMENT": "comment",
    "NOTES": "comment",
}


@dataclass(slots=True)
class AdifRecord:
    """One parsed ADI record, normalised to hamrlog's field names."""

    fields: dict[str, Any] = field(default_factory=dict)
    digital: dict[str, str] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)
    qso_utc: dt.datetime | None = None
    #: Values an ADI file may carry but that map to local entities by name.
    operator_callsign: str = ""
    station_name: str = ""
    repeater_call: str = ""

    @property
    def call(self) -> str:
        return str(self.fields.get("call", ""))


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Split ADI text into (field name, value) pairs, including EOH/EOR markers."""
    pairs: list[tuple[str, str]] = []
    position = 0
    while True:
        match = _TAG_RE.search(text, position)
        if match is None:
            break
        name = match.group(1).upper()
        declared = match.group(2)
        start = match.end()

        if declared is None:
            # Control tags (<EOH>, <EOR>) carry no data.
            pairs.append((name, ""))
            position = start
            continue

        length = int(declared)
        value = text[start : start + length]
        # Tolerate files whose declared length overruns the next tag.
        next_tag = text.find("<", start)
        if next_tag != -1 and start + length > next_tag:
            value = text[start:next_tag]
        pairs.append((name, value))
        position = start + len(value)
    return pairs


def _parse_mhz(text: str) -> int | None:
    """Parse an ADIF frequency in megahertz into integer hertz."""
    text = text.strip()
    if not text:
        return None
    try:
        return int(round(float(text) * bands.MHZ))
    except ValueError:
        return None


def _parse_timestamp(date_text: str, time_text: str) -> dt.datetime | None:
    """Combine QSO_DATE and TIME_ON into a naive UTC datetime."""
    date_text = (date_text or "").strip()
    if len(date_text) != 8 or not date_text.isdigit():
        return None
    time_text = (time_text or "0000").strip().ljust(6, "0")[:6]
    if not time_text.isdigit():
        time_text = "000000"
    try:
        return dt.datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _resolve_mode(adif_mode: str, adif_submode: str, app_mode: str) -> str:
    """Map an ADIF MODE/SUBMODE pair back to a hamrlog mode name."""
    if app_mode and modes.get(app_mode):
        return modes.get(app_mode).name  # type: ignore[union-attr]
    if adif_submode:
        direct = modes.get(adif_submode)
        if direct:
            return direct.name
    for mode in modes.MODES:
        if mode.adif_mode == adif_mode.upper() and mode.adif_submode == adif_submode.upper():
            return mode.name
    direct = modes.get(adif_mode)
    return direct.name if direct else adif_mode.upper()


def parse_adif(text: str) -> list[AdifRecord]:
    """Parse ADI text into records, skipping the header."""
    records: list[AdifRecord] = []
    current: dict[str, str] = {}
    in_header = "<EOH>" in text.upper()

    for name, value in _tokenize(text):
        if name == "EOH":
            in_header = False
            current = {}
            continue
        if in_header:
            continue
        if name == "EOR":
            if current:
                record = _build_record(current)
                if record.call:
                    records.append(record)
            current = {}
            continue
        current[name] = value

    if current:
        record = _build_record(current)
        if record.call:
            records.append(record)
    return records


def _build_record(raw: dict[str, str]) -> AdifRecord:
    """Turn a raw ADI field dictionary into an AdifRecord."""
    record = AdifRecord()
    consumed: set[str] = set()

    for adif_name, attribute in _FIELD_MAP.items():
        if adif_name in raw and raw[adif_name].strip():
            record.fields[attribute] = raw[adif_name].strip()
            consumed.add(adif_name)

    if "call" in record.fields:
        record.fields["call"] = callsign.normalize(str(record.fields["call"]))

    record.qso_utc = _parse_timestamp(raw.get("QSO_DATE", ""), raw.get("TIME_ON", ""))
    consumed.update({"QSO_DATE", "TIME_ON", "TIME_OFF", "QSO_DATE_OFF"})

    # FREQ is the transmit frequency, FREQ_RX the receive one on a split
    # QSO. hamrlog stores the tuned frequency in freq_hz, which is the receive
    # side when the two differ.
    tx_hz = _parse_mhz(raw.get("FREQ", ""))
    rx_hz = _parse_mhz(raw.get("FREQ_RX", ""))
    if rx_hz and tx_hz and rx_hz != tx_hz:
        record.fields["freq_hz"] = rx_hz
        record.fields["freq_tx_hz"] = tx_hz
    elif tx_hz:
        record.fields["freq_hz"] = tx_hz
    elif rx_hz:
        record.fields["freq_hz"] = rx_hz
    consumed.update({"FREQ", "FREQ_RX"})

    record.repeater_call = raw.get(f"{APP_FIELD_PREFIX}REPEATER", "").strip().upper()
    consumed.add("PROP_MODE")

    mode_name = _resolve_mode(
        raw.get("MODE", ""), raw.get("SUBMODE", ""), raw.get(f"{APP_FIELD_PREFIX}MODE", "")
    )
    if mode_name:
        record.fields["mode"] = mode_name
    consumed.update({"MODE", "SUBMODE"})

    if not record.fields.get("band") and record.fields.get("freq_hz"):
        band = bands.from_frequency(int(record.fields["freq_hz"]))
        if band:
            record.fields["band"] = band.name
    if record.fields.get("band"):
        band = bands.get(str(record.fields["band"]))
        record.fields["band"] = band.name if band else str(record.fields["band"]).lower()

    power = raw.get("TX_PWR", "").strip()
    if power:
        try:
            record.fields["power_w"] = int(float(power))
        except ValueError:
            pass
    consumed.add("TX_PWR")

    record.operator_callsign = (
        raw.get("OPERATOR", "").strip() or raw.get("STATION_CALLSIGN", "").strip()
    )
    consumed.update({"OPERATOR", "STATION_CALLSIGN"})

    if not record.fields.get("country") and record.call:
        detected = callsign.country_for(record.call)
        if detected:
            record.fields["country"] = detected

    # Application fields: hamrlog's own go back to their columns, the rest stay.
    for name, value in raw.items():
        if name in consumed or not value.strip():
            continue
        if name.startswith(APP_FIELD_PREFIX):
            key = name[len(APP_FIELD_PREFIX) :].lower()
            if key == "station":
                record.station_name = value.strip()
            elif key == "repeater":
                continue
            elif key in ("entry_mode", "mode", "qso_count"):
                continue
            else:
                record.digital[key] = value.strip()
        else:
            record.extra[name] = value.strip()

    return record


def read_adif_file(path: str | Path) -> list[AdifRecord]:
    """Read and parse an ADI file, tolerating non-UTF-8 bytes."""
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return parse_adif(text)
