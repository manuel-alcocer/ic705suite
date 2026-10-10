"""Demo mode: ``hamrlog --demo`` opens an invented log that never changes.

The data lives in the repository (``data/demo/demo.json``): an operator,
setups built from the catalog, profiles, an address book and a couple of
months of QSOs, every callsign and name made up. From it a template database
is built once, at install time or on the first ``--demo``, and kept in the
data directory.

Each demo run works on a copy of the template in a temporary folder, which
also takes the settings file, and the folder is deleted on exit. Whatever is
logged, edited or configured in the demo is gone the next time, and the
next demo starts from exactly the same data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from sqlalchemy import update

from . import __version__
from .core.services import (
    AntennaService,
    ContactService,
    EquipmentService,
    OperatorService,
    PowerSupplyService,
    ProfileService,
    QsoService,
    RepeaterService,
    SettingsService,
    StationService,
)
from .core.state import SessionState
from .db import session as db_session
from .db.models import EntryMode, Qso
from .db.preseed import PRESEED_ENV
from .paths import data_dir

#: Language of the demo unless another is asked for.
DEFAULT_LANGUAGE = "en"

TEMPLATE_NAME = "hamrlog-demo.sqlite3"
#: Next to the template: what it was built from, to rebuild it when that changes.
STAMP_NAME = "hamrlog-demo.stamp"


def seed_bytes() -> bytes:
    return resources.files("ic705suite.apps.log").joinpath("data", "demo", "demo.json").read_bytes()


def load_seed() -> dict[str, Any]:
    return json.loads(seed_bytes().decode("utf-8"))


def fingerprint() -> str:
    """Changes with the seed and with the version, whose schema may differ."""
    digest = hashlib.sha256(seed_bytes())
    digest.update(__version__.encode())
    return digest.hexdigest()


def template_dir() -> Path:
    return data_dir() / "demo"


def template_path() -> Path:
    return template_dir() / TEMPLATE_NAME


def template_is_current() -> bool:
    stamp = template_dir() / STAMP_NAME
    try:
        return template_path().is_file() and stamp.read_text().strip() == fingerprint()
    except OSError:
        return False


def build_template() -> Path:
    """Build the template database from the seed, replacing any older one."""
    folder = template_dir()
    folder.mkdir(parents=True, exist_ok=True)
    building = folder / f"{TEMPLATE_NAME}.building"
    building.unlink(missing_ok=True)
    build_database(f"sqlite:///{building.as_posix()}")
    os.replace(building, template_path())
    (folder / STAMP_NAME).write_text(fingerprint())
    return template_path()


def ensure_template() -> Path:
    """The template, built first if it is missing or out of date."""
    return template_path() if template_is_current() else build_template()


def build_database(url: str, seed: dict[str, Any] | None = None) -> None:
    """Fill an empty database with the demo data.

    The catalog (radios, antennas, supplies, the URE repeaters) is loaded
    whatever ``HAMRLOG_PRESEED`` says: the demo setups are made from it.
    """
    seed = seed or load_seed()
    previous = os.environ.get(PRESEED_ENV)
    os.environ[PRESEED_ENV] = "1"
    try:
        db_session.dispose()
        db_session.init_engine(url)
        _fill(seed)
    finally:
        db_session.dispose()
        if previous is None:
            os.environ.pop(PRESEED_ENV, None)
        else:
            os.environ[PRESEED_ENV] = previous


def _fill(seed: dict[str, Any]) -> None:
    me = seed["operator"]
    operator = OperatorService.create(me["callsign"], me["name"], me["gridsquare"], me["qth"])

    radios = {station.name: station.id for station in StationService.list_all()}
    antennas = {antenna.name: antenna.id for antenna in AntennaService.list_all()}
    supplies = {supply.name: supply.id for supply in PowerSupplyService.list_all()}
    setups = {}
    for entry in seed["setups"]:
        setup = EquipmentService.create(
            entry["name"],
            [radios[name] for name in entry["radios"]],
            [antennas[name] for name in entry["antennas"]],
            [supplies[name] for name in entry["supplies"]],
            notes=entry.get("notes", ""),
        )
        setups[entry["name"]] = setup.id

    for entry in seed["contacts"]:
        values = {key: value for key, value in entry.items() if key != "call"}
        ContactService.create(entry["call"], source="demo", **values)

    profiles = {}
    for entry in seed["profiles"]:
        repeater = (
            RepeaterService.resolve(entry["repeater"]) if entry.get("repeater") else None
        )
        profile = ProfileService.save(
            None,
            name=entry["name"],
            slot=entry.get("slot"),
            operator_id=operator.id,
            equipment_id=setups[entry["setup"]],
            repeater_id=repeater.id if repeater else None,
            freq_hz=entry.get("freq_hz") or (repeater.output_hz if repeater else None),
            mode=entry["mode"],
            power_w=entry.get("power_w"),
            digital_data=entry.get("digital"),
        )
        profiles[entry["name"]] = profile.id

    for entry in seed["qsos"]:
        state = SessionState(
            operator_id=operator.id,
            equipment_id=setups[entry["setup"]],
            power_w=entry.get("power_w"),
        )
        if entry.get("repeater"):
            RepeaterService.apply_to_state(RepeaterService.resolve(entry["repeater"]).id, state)
        else:
            state.set_frequency(entry["freq_hz"])
        state.set_mode(entry["mode"])
        if entry.get("digital"):
            state.digital_data = dict(entry["digital"])
        fields = {
            key: entry[key]
            for key in ("call", "name", "qth", "gridsquare", "rst_sent", "rst_rcvd", "comment")
            if entry.get(key)
        }
        QsoService.log(fields, state, qso_utc=dt.datetime.fromisoformat(entry["utc"]))

    # Given a time, a QSO is marked as typed in by hand; these were not.
    with db_session.session_scope() as session:
        session.execute(update(Qso).values(entry_mode=EntryMode.AUTO))

    state = SessionState(operator_id=operator.id)
    ProfileService.apply_to_state(profiles[seed["active_profile"]], state)
    SettingsService.save_state(state)


@contextmanager
def session(language: str | None = None) -> Iterator[Path]:
    """Run inside a throwaway copy of the demo: nothing outlives the block.

    The database, the settings file and the exports all go to a temporary
    folder, through the same environment variables a user could set, so the
    rest of the application needs to know nothing about the demo.
    """
    template = ensure_template()
    folder = Path(tempfile.mkdtemp(prefix="hamrlog-demo-"))
    database = folder / TEMPLATE_NAME
    shutil.copyfile(template, database)
    overrides = {
        "HAMRLOG_HOME": str(folder),
        "HAMRLOG_DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "HAMRLOG_LANG": language or DEFAULT_LANGUAGE,
    }
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        yield folder
    finally:
        # Windows will not delete a database file that is still open.
        db_session.dispose()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(folder, ignore_errors=True)
