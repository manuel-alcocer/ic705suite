"""The FT8 QSOs as ADIF, the format LoTW, eQSL, QRZ and every logbook read: one record per
QSO completed, appended to ft8_log.adi (as WSJT-X keeps wsjtx_log.adi)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ic705suite import __version__
from ic705suite.rig import bands

ADIF_VERSION = "3.1.4"


def record(*, call: str, grid: str, sent: str, received: str, began: float, ended: float,
           dial: int, audio: int, mycall: str, mygrid: str, power_w: float | None) -> dict:
    """The fields of one QSO, ADIF's names."""
    on = dt.datetime.fromtimestamp(began, dt.UTC)
    off = dt.datetime.fromtimestamp(ended, dt.UTC)
    frequency = dial + audio
    band = bands.band_for(dial)
    fields = {
        "CALL": call, "GRIDSQUARE": grid, "MODE": "FT8", "RST_SENT": sent,
        "RST_RCVD": received, "QSO_DATE": f"{on:%Y%m%d}", "TIME_ON": f"{on:%H%M%S}",
        "QSO_DATE_OFF": f"{off:%Y%m%d}", "TIME_OFF": f"{off:%H%M%S}",
        "BAND": band.name.upper() if band else "", "FREQ": f"{frequency / 1e6:.6f}",
        "STATION_CALLSIGN": mycall, "MY_GRIDSQUARE": mygrid,
        "TX_PWR": f"{power_w:g}" if power_w is not None else "",
        "COMMENT": "FT8 ic705suite",
    }
    return {name: value for name, value in fields.items() if value}


def text(fields: dict) -> str:
    """One ADI record: <NAME:length>value ... <EOR>."""
    return " ".join(f"<{name}:{len(str(value))}>{value}" for name, value in fields.items()) \
        + " <EOR>\n"


def header() -> str:
    return (f"ic705suite FT8 log\n<ADIF_VER:{len(ADIF_VERSION)}>{ADIF_VERSION} "
            f"<PROGRAMID:10>ic705suite <PROGRAMVERSION:{len(__version__)}>{__version__} "
            "<EOH>\n")


def append(path: Path, fields: dict) -> None:
    """Add a QSO to the log file (made with its header the first time)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8") as log:
        if new:
            log.write(header())
        log.write(text(fields))
