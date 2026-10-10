"""Lightweight schema migrations.

``create_all`` creates missing tables but never alters existing ones, so a
database written by an older version would be missing the newer columns. This
module adds them in place.

The scope is deliberately narrow: adding columns and tables, which is all the
schema has needed so far and what every supported backend does safely. A
change that renames or drops a column would need a real migration tool.
"""

from __future__ import annotations

import logging
from importlib import resources

from sqlalchemy import Engine, insert, inspect, select, text
from sqlalchemy.schema import Column

from .models import (
    Antenna,
    Base,
    Contact,
    Equipment,
    Operator,
    Profile,
    Qso,
    Repeater,
    Station,
    StationType,
    equipment_antenna_links,
    equipment_station_links,
    station_antenna_links,
    station_profile_links,
)

logger = logging.getLogger(__name__)


def _literal_default(column: Column) -> str | None:
    """SQL literal to backfill a NOT NULL column being added.

    SQLite refuses ``ADD COLUMN ... NOT NULL`` without a default, and existing
    rows need a value anyway.
    """
    if column.nullable:
        return None

    python_type: type | None
    try:
        python_type = column.type.python_type
    except NotImplementedError:  # pragma: no cover - exotic types
        python_type = None

    if python_type is str:
        return "''"
    if python_type is bool:
        return "0"
    if python_type in (int, float):
        return "0"
    if python_type is dict or python_type is list:
        return "'{}'" if python_type is dict else "'[]'"
    return None


def add_missing_columns(engine: Engine) -> list[str]:
    """Add columns present in the models but missing in the database.

    Returns:
        The ``table.column`` names that were added, for logging and tests.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            # create_all handles brand new tables.
            continue

        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue

            type_sql = column.type.compile(engine.dialect)
            clause = f"{column.name} {type_sql}"
            default = _literal_default(column)
            if default is not None:
                clause += f" NOT NULL DEFAULT {default}"

            statement = f"ALTER TABLE {table.name} ADD COLUMN {clause}"
            with engine.begin() as connection:
                connection.execute(text(statement))
            added.append(f"{table.name}.{column.name}")
            logger.info("schema upgrade: added %s.%s", table.name, column.name)

    return added


#: Types every new or upgraded database starts with. Amateur HF includes 160 m,
#: hence 1.8 MHz rather than the textbook 3 MHz.
DEFAULT_STATION_TYPES: tuple[tuple[str, int, int], ...] = (
    ("HF", 1_800_000, 30_000_000),
    ("CB", 26_965_000, 27_405_000),
    ("VHF", 30_000_000, 300_000_000),
    ("UHF", 300_000_000, 3_000_000_000),
)


def upgrade_data(engine: Engine, previous_version: int | None) -> None:
    """Fill in the data a schema revision expects.

    Args:
        previous_version: Revision the database was at before this start,
            None for a database created just now.
    """
    if previous_version is None or previous_version < 4:
        _seed_station_types(engine)
        _assign_profiles_to_their_station(engine)
    if previous_version is None or previous_version < 5:
        _split_antennas_from_stations(engine)
    if previous_version is not None and previous_version < 6:
        _make_an_equipment_set_per_station(engine)
    if previous_version is not None and previous_version < 7:
        _english_country_names(engine)
    if previous_version is not None and previous_version < 11:
        _let_repeaters_share_a_callsign(engine)
    if previous_version is not None and previous_version < 13:
        _normalise_callsigns_and_names(engine)


#: The columns the models normalise on write, per model.
_NORMALISED_COLUMNS = (
    (Operator, ("callsign", "name", "qth")),
    (Qso, ("call", "repeater_call", "name", "qth")),
    (Contact, ("callsign", "first_name", "last_name", "city")),
    (Repeater, ("callsign",)),
)


def _normalise_callsigns_and_names(engine: Engine) -> None:
    """Apply to stored rows what the models now do on write.

    Assigning each value back runs it through the model's validators; the
    rows they leave unchanged are not written.
    """
    from sqlalchemy.orm import Session

    changed = 0
    with Session(engine) as session, session.begin():
        for model, columns in _NORMALISED_COLUMNS:
            for row in session.scalars(select(model)):
                before = [getattr(row, column) for column in columns]
                for column, value in zip(columns, before, strict=True):
                    setattr(row, column, value)
                if [getattr(row, column) for column in columns] != before:
                    changed += 1
    if changed:
        logger.info("schema upgrade: normalised callsigns and names in %d rows", changed)


def _let_repeaters_share_a_callsign(engine: Engine) -> None:
    """Turn the unique index on the repeater callsign into a plain one.

    Clubs run several repeaters under one callsign, on different bands or
    modes, and the URE list holds them all. The index keeps its name, so a
    database created now and an upgraded one end up the same.
    """
    index = "ix_repeaters_callsign"
    with engine.begin() as connection:
        connection.execute(text(f"DROP INDEX IF EXISTS {index}"))
        connection.execute(text(f"CREATE INDEX {index} ON repeaters (callsign)"))
    logger.info("schema upgrade: repeaters may share a callsign")


def _english_country_names(engine: Engine) -> None:
    """Rename the Spanish country names older versions stored to English.

    The Spanish catalog is the dictionary: its msgstr is what used to be
    stored, its msgid is what is stored now. Names it does not know, such as
    the ones an imported ADIF file brought, are left alone.
    """
    from .. import i18n

    try:
        source = resources.files("ic705suite.apps.log").joinpath("locales", "es", "countries.po")
        spanish_to_english = {
            spanish: english
            for english, spanish in i18n.parse_po(source.read_text(encoding="utf-8")).items()
            if spanish != english
        }
    except (FileNotFoundError, OSError):
        return
    changed = 0
    with engine.begin() as connection:
        for table in (Qso.__table__, Contact.__table__):
            for spanish, english in spanish_to_english.items():
                result = connection.execute(
                    table.update().where(table.c.country == spanish).values(country=english)
                )
                changed += result.rowcount or 0
    if changed:
        logger.info("schema upgrade: renamed %d country names to English", changed)


def _make_an_equipment_set_per_station(engine: Engine) -> None:
    """Each existing radio becomes a set of its own, with its antennas.

    Before schema 6 the radio was the «equipo». Giving each one a set with
    the same name keeps that meaning without asking the operator to rebuild
    them by hand.
    """
    with engine.begin() as connection:
        stations = connection.execute(select(Station.id, Station.name)).all()
        for station_id, name in stations:
            if connection.execute(
                select(Equipment.id).where(Equipment.name == name)
            ).first() is not None:
                continue
            equipment_id = connection.execute(
                insert(Equipment).values(name=name, notes="")
            ).inserted_primary_key[0]
            connection.execute(
                insert(equipment_station_links).values(
                    equipment_id=equipment_id, station_id=station_id
                )
            )
            antenna_ids = connection.execute(
                select(station_antenna_links.c.antenna_id).where(
                    station_antenna_links.c.station_id == station_id
                )
            ).scalars().all()
            for antenna_id in antenna_ids:
                connection.execute(
                    insert(equipment_antenna_links).values(
                        equipment_id=equipment_id, antenna_id=antenna_id
                    )
                )
    if stations:
        logger.info("schema upgrade: made %d equipment sets from the radios", len(stations))


def _seed_station_types(engine: Engine) -> None:
    """Create the default types, once. Deleting them later is respected."""
    with engine.begin() as connection:
        if connection.execute(select(StationType.id).limit(1)).first() is not None:
            return
        connection.execute(
            insert(StationType),
            [
                {"name": name, "min_hz": low, "max_hz": high}
                for name, low, high in DEFAULT_STATION_TYPES
            ],
        )
    logger.info("schema upgrade: added the default station types")


def _assign_profiles_to_their_station(engine: Engine) -> None:
    """A profile used to carry one station; it becomes an assignment."""
    with engine.begin() as connection:
        rows = connection.execute(
            select(Profile.id, Profile.station_id).where(Profile.station_id.is_not(None))
        ).all()
        for profile_id, station_id in rows:
            connection.execute(
                insert(station_profile_links).values(
                    station_id=station_id, profile_id=profile_id
                )
            )
            connection.execute(
                Profile.__table__.update()
                .where(Profile.id == profile_id)
                .values(station_id=None)
            )
    if rows:
        logger.info("schema upgrade: assigned %d profiles to their station", len(rows))


def _split_antennas_from_stations(engine: Engine) -> None:
    """The antenna text of each station becomes an antenna assigned to it.

    Stations that named the same antenna share it. Their past QSOs take that
    antenna, so MY_ANTENNA keeps coming out in the exports. The bands are
    unknown and left empty, which accepts every configuration.
    """
    with engine.begin() as connection:
        rows = connection.execute(
            select(Station.id, Station.antenna).where(Station.antenna != "")
        ).all()
        antenna_ids: dict[str, int] = {}
        for station_id, text_value in rows:
            name = text_value.strip()
            key = name.lower()
            if key not in antenna_ids:
                existing = connection.execute(
                    select(Antenna.id).where(Antenna.name == name)
                ).first()
                if existing is None:
                    result = connection.execute(
                        insert(Antenna).values(name=name, bands=[], notes="")
                    )
                    antenna_ids[key] = result.inserted_primary_key[0]
                else:
                    antenna_ids[key] = existing[0]
            antenna_id = antenna_ids[key]
            connection.execute(
                insert(station_antenna_links).values(
                    station_id=station_id, antenna_id=antenna_id
                )
            )
            connection.execute(
                Qso.__table__.update()
                .where(Qso.station_id == station_id, Qso.antenna_id.is_(None))
                .values(antenna_id=antenna_id)
            )
            connection.execute(
                Station.__table__.update().where(Station.id == station_id).values(antenna="")
            )
    if rows:
        logger.info("schema upgrade: moved %d station antennas to their own table", len(rows))
