"""An FT8 QSO completed, into hamrlog's log on the machine whose screen shows it (the core
may be on another one, with its own ft8_log.adi): with its time, frequency, band, the
reports and the grid, once (two screens open log it once)."""

from __future__ import annotations

import datetime as dt

SAME_QSO = dt.timedelta(minutes=2)


def when(fields: dict) -> dt.datetime:
    return dt.datetime.strptime(fields["QSO_DATE"] + fields["TIME_ON"], "%Y%m%d%H%M%S")


def log_qso(fields: dict) -> tuple[object | None, str]:
    """(the contact logged, "") or (None, why not: already there, or the error)."""
    from ic705suite.dashboard.quicklog import _logbook

    _entry, log_bands, QsoService, ServiceError, SettingsService = _logbook()
    state = SettingsService.load_state()
    began = when(fields)
    for row in QsoService.recent(limit=200, operator_id=state.operator_id):
        if row.call == fields["CALL"] and (row.mode or "").upper() == "FT8" \
                and abs(row.qso_utc - began) < SAME_QSO:
            return None, "already logged"
    freq_hz = round(float(fields["FREQ"]) * 1e6) if fields.get("FREQ") else None
    band = log_bands.from_frequency(freq_hz) if freq_hz else None
    entry = {"call": fields["CALL"], "mode": "FT8",
             "rst_sent": fields.get("RST_SENT", ""), "rst_rcvd": fields.get("RST_RCVD", ""),
             "gridsquare": fields.get("GRIDSQUARE", ""), "comment": fields.get("COMMENT", "")}
    if freq_hz:
        entry["freq_hz"] = freq_hz
    if band is not None:
        entry["band"] = band.name
    if fields.get("TX_PWR"):
        entry["power_w"] = round(float(fields["TX_PWR"]))
    try:
        return QsoService.log(entry, state, qso_utc=began), ""
    except ServiceError as exc:
        return None, str(exc)
