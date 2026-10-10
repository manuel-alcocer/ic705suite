"""The bundled catalog: current radios, antennas, power supplies and the
repeaters of the URE list.

Every JSON file under ``hamrlog/data/preseed`` is read, whatever its name,
so adding a catalog is dropping a file there. The files are written in
Spanish, like the equipment they describe: each one is an object
``{"tipo": "emisoras" | "antenas" | "fuentes" | "repetidores",
"elementos": [...]}``, or a bare list when the file is named after its kind
(``emisoras.json``).

Entries are loaded as ``preset`` rows: the services refuse to change or
delete them, and they exist so that building an equipment set, or tuning a
repeater, does not start with typing everything in by hand.

Loading runs on every start and is idempotent: it only adds what is missing,
so a newer catalog adds its new entries and an item the operator registered
under the same name keeps theirs. Equipment is matched by name regardless of
case; a repeater by callsign and output frequency, since one callsign often
names several repeaters.
"""

from __future__ import annotations

import json
import logging
import os
from importlib import resources
from typing import Any

from sqlalchemy import Engine, func, insert, select

from ..core import bands, modes, repeaters
from .models import (
    Antenna,
    PowerSupply,
    Repeater,
    Station,
    StationType,
    station_type_links,
)

logger = logging.getLogger(__name__)

#: Set to "0" to start without the catalog (the tests do).
PRESEED_ENV = "HAMRLOG_PRESEED"


def brand_of(entry: dict[str, Any]) -> str:
    """The entry's brand, or the first word of its name."""
    return str(entry.get("brand") or "").strip() or str(entry["name"]).split()[0]


#: Kinds a catalog file may hold, by the Spanish name the files use, and the
#: key every entry of that kind must have.
KINDS = {"emisoras": "name", "antenas": "name", "fuentes": "name", "repetidores": "callsign"}


def load_catalogs(folder: Any = None) -> dict[str, list[dict[str, Any]]]:
    """Entries of every catalog file, grouped by kind.

    A file that cannot be read or does not say its kind is skipped with a
    warning rather than stopping the application.
    """
    found: dict[str, list[dict[str, Any]]] = {kind: [] for kind in KINDS}
    try:
        if folder is None:
            folder = resources.files("ic705suite.apps.log").joinpath("data", "preseed")
        files = sorted(
            (entry for entry in folder.iterdir() if entry.name.endswith(".json")),
            key=lambda entry: entry.name,
        )
    except (FileNotFoundError, NotADirectoryError, OSError):
        return found
    for source in files:
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            logger.warning("preseed: could not read %s", source.name)
            continue
        if isinstance(data, dict):
            kind = str(data.get("tipo", "")).lower()
            entries = data.get("elementos", [])
        else:
            kind = source.name.removesuffix(".json").lower()
            entries = data
        if kind not in found or not isinstance(entries, list):
            logger.warning("preseed: %s does not say what it holds", source.name)
            continue
        found[kind].extend(
            entry for entry in entries if isinstance(entry, dict) and entry.get(KINDS[kind])
        )
    return found


def apply_preseed(engine: Engine, folder: Any = None) -> int:
    """Add the catalog entries the database does not have yet.

    Args:
        folder: Where the catalog files are; the bundled ones by default.

    Returns:
        How many rows were added.
    """
    if os.environ.get(PRESEED_ENV, "1") == "0":
        return 0
    catalogs = load_catalogs(folder)
    added = 0
    with engine.begin() as connection:

        def known(model: Any) -> set[str]:
            return set(connection.execute(select(func.lower(model.name))).scalars())

        type_ids = {
            name.lower(): type_id
            for type_id, name in connection.execute(select(StationType.id, StationType.name))
        }
        present = known(Station)
        for entry in catalogs["emisoras"]:
            name = str(entry["name"]).strip()
            if name.lower() in present:
                continue
            power = entry.get("power_w")
            station_id = connection.execute(
                insert(Station).values(
                    name=name,
                    brand=brand_of(entry),
                    rig=str(entry.get("rig") or ""),
                    power_w=int(power) if power else None,
                    notes=str(entry.get("notes") or ""),
                    preset=True,
                )
            ).inserted_primary_key[0]
            for type_name in entry.get("types") or []:
                type_id = type_ids.get(str(type_name).lower())
                if type_id is not None:
                    connection.execute(
                        insert(station_type_links).values(
                            station_id=station_id, type_id=type_id
                        )
                    )
            present.add(name.lower())
            added += 1

        present = known(Antenna)
        for entry in catalogs["antenas"]:
            name = str(entry["name"]).strip()
            if name.lower() in present:
                continue
            valid = [band.name for raw in entry.get("bands") or [] if (band := bands.get(raw))]
            connection.execute(
                insert(Antenna).values(
                    name=name,
                    brand=brand_of(entry),
                    bands=valid,
                    notes=str(entry.get("notes") or ""),
                    preset=True,
                )
            )
            present.add(name.lower())
            added += 1

        present = known(PowerSupply)
        for entry in catalogs["fuentes"]:
            name = str(entry["name"]).strip()
            if name.lower() in present:
                continue
            connection.execute(
                insert(PowerSupply).values(
                    name=name,
                    brand=brand_of(entry),
                    voltage_v=entry.get("voltage_v"),
                    current_a=entry.get("current_a"),
                    notes=str(entry.get("notes") or ""),
                    preset=True,
                )
            )
            present.add(name.lower())
            added += 1

        added += _add_repeaters(connection, catalogs["repetidores"])
    if added:
        logger.info("preseed: added %d catalog items", added)
    return added


def _add_repeaters(connection: Any, entries: list[dict[str, Any]]) -> int:
    """Insert the repeaters of the list not stored yet; how many were added.

    Each entry gives ``callsign``, ``output_hz`` and ``shift_hz``, and may
    give ``mode``, ``ctcss``, ``ure``, ``channel``, ``gridsquare``, ``qth``,
    ``name`` (who runs it), ``notes`` and ``digital``. One without an output
    frequency is skipped.
    """
    present = {
        (call.upper(), output)
        for call, output in connection.execute(select(Repeater.callsign, Repeater.output_hz))
    }
    added = 0
    for entry in entries:
        call = str(entry["callsign"]).strip().upper()
        try:
            output_hz = int(entry.get("output_hz") or 0)
            shift_hz = int(entry.get("shift_hz") or 0)
        except (TypeError, ValueError):
            output_hz = 0
        if not output_hz or (call, output_hz) in present:
            continue
        band = bands.from_frequency(output_hz)
        mode = modes.get(str(entry.get("mode") or "FM"))
        digital = entry.get("digital")
        connection.execute(
            insert(Repeater).values(
                callsign=call,
                name=str(entry.get("name") or "").strip(),
                ure_number=str(entry.get("ure") or "").strip(),
                channel=str(entry.get("channel") or "").strip(),
                band=band.name if band else "",
                output_hz=output_hz,
                input_hz=repeaters.input_frequency(output_hz, shift_hz),
                shift_hz=shift_hz,
                mode=mode.name if mode else "FM",
                ctcss_tx=repeaters.normalize_tone(str(entry.get("ctcss") or "")),
                qth=str(entry.get("qth") or "").strip(),
                gridsquare=str(entry.get("gridsquare") or "").strip().upper(),
                digital_data=dict(digital) if isinstance(digital, dict) else {},
                notes=str(entry.get("notes") or "").strip(),
                preset=True,
            )
        )
        present.add((call, output_hz))
        added += 1
    return added
