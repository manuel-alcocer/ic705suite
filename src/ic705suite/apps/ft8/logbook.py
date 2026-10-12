"""An FT8 QSO completed, into hamrlog's log on the machine whose screen shows it (the core
may be on another one, with its own ft8_log.adi): with its time, frequency, band, the
reports and the grid, once (two screens open log it once).

It goes with the setup (hamrlog's equipment set) it was made with: the one named in [ft8]
setup, else the first whose radio for that band is the one FT8 uses (its antenna for the
band with it), not whatever setup the logbook had last (a 2 m FM one, say).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re

SAME_QSO = dt.timedelta(minutes=2)


def when(fields: dict) -> dt.datetime:
    return dt.datetime.strptime(fields["QSO_DATE"] + fields["TIME_ON"], "%Y%m%d%H%M%S")


def choose_setup(setups: list, name: str, radio: str, freq_hz: int | None,
                 band: str | None) -> object | None:
    """The setup named `name`; else the first (oldest) whose radio for the band is `radio`
    (its model, as in "IC-705"), one with an antenna for the band before one without."""
    if name:
        return next((s for s in setups if s.name.strip().lower() == name.strip().lower()),
                    None)
    model = re.sub(r"\(.*?\)", "", radio).replace(" ", "").upper()   # not "(simulator)"
    if not model:
        return None
    suited = []
    for setup in sorted(setups, key=lambda s: s.id):
        station, antenna = setup.parts_for(freq_hz, band)
        if station is None or model not in station.name.replace(" ", "").upper():
            continue
        if freq_hz and not station.covers(freq_hz):
            continue
        suited.append((antenna is None or not antenna.covers_band(band), setup.id, setup))
    return min(suited, key=lambda t: t[:2])[2] if suited else None


def setup_id(name: str, radio: str, freq_hz: int | None, band: str | None) -> int | None:
    """choose_setup() among the logbook's setups (read with their radios and antennas)."""
    from sqlalchemy import select

    from ic705suite.apps.log.core.services import _EQUIPMENT_PARTS
    from ic705suite.apps.log.db.models import Equipment
    from ic705suite.apps.log.db.session import session_scope

    with session_scope() as session:
        setups = list(session.scalars(select(Equipment).options(*_EQUIPMENT_PARTS)))
        chosen = choose_setup(setups, name, radio, freq_hz, band)
        return chosen.id if chosen is not None else None


def log_qso(fields: dict, setup: str = "", radio: str = "") -> tuple[object | None, str]:
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
    chosen = setup_id(setup, radio, freq_hz, band.name if band is not None else None)
    if chosen is not None:
        state = dataclasses.replace(state, equipment_id=chosen)
    elif setup:
        return None, f"no setup called {setup!r} in the logbook ([ft8] setup)"
    try:
        return QsoService.log(entry, state, qso_utc=began), ""
    except ServiceError as exc:
        return None, str(exc)
