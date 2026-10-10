"""Serialisation shapes shared by the REST API and any future web frontend.

Plain dictionaries rather than pydantic models, so importing this module never
requires the optional API dependencies.
"""

from __future__ import annotations

from typing import Any

from ..core import bands, modes
from ..core.dto import LogStats, QsoRow


def qso_to_dict(row: QsoRow) -> dict[str, Any]:
    """JSON-ready representation of a contact."""
    mode = modes.get(row.mode)
    return {
        "id": row.id,
        "call": row.call,
        "name": row.name,
        "qso_utc": row.qso_utc.isoformat() + "Z",
        "band": row.band,
        "frequency_hz": row.freq_hz,
        "frequency_mhz": bands.frequency_mhz(row.freq_hz) or None,
        "mode": row.mode,
        "adif_mode": mode.adif_mode if mode else row.mode,
        "adif_submode": mode.adif_submode if mode else "",
        "rst_sent": row.rst_sent,
        "rst_rcvd": row.rst_rcvd,
        "qth": row.qth,
        "gridsquare": row.gridsquare,
        "country": row.country,
        "comment": row.comment,
        "entry_mode": row.entry_mode,
        "operator": row.operator_callsign,
        "station": row.station_name,
        "digital": row.digital_data,
    }


def stats_to_dict(stats: LogStats) -> dict[str, Any]:
    """JSON-ready representation of the log counters."""
    return {
        "total": stats.total,
        "today": stats.today,
        "unique_calls": stats.unique_calls,
        "countries": stats.countries,
        "by_band": stats.by_band,
        "by_mode": stats.by_mode,
    }
