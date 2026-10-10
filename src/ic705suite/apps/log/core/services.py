"""Application services.

Every write and every query goes through this module. The TUI holds no SQL and
no ORM objects, so the same services can back a REST API or a web frontend
without changes.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from ..db.codes import next_code
from ..db.models import (
    Antenna,
    Contact,
    EntryMode,
    Equipment,
    Operator,
    PowerSupply,
    Profile,
    Qso,
    Repeater,
    Setting,
    Station,
    StationType,
    equipment_antenna_links,
    equipment_station_links,
    equipment_supply_links,
    station_antenna_links,
    station_profile_links,
    station_type_links,
)
from ..db.session import session_scope
from ..i18n import _
from . import bands, modes, repeaters
from . import callsign as callsign_module
from .dto import ContactRow, LogStats, PartRow, QsoCard, QsoRow
from .state import SessionState

#: Fields a manually dated QSO allows editing.
MANUAL_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {
        "call", "name", "qso_utc", "band", "freq_hz", "freq_tx_hz", "mode", "rst_sent",
        "rst_rcvd", "qth", "gridsquare", "country", "comment", "power_w", "station_id",
        "operator_id", "digital_data", "repeater_id", "repeater_call", "equipment_id",
    }
)

#: Fields an automatically timestamped QSO allows editing: everything but the
#: timestamp, which the program set and is the evidence of when it happened.
AUTO_EDITABLE_FIELDS: frozenset[str] = MANUAL_EDITABLE_FIELDS - {"qso_utc"}


class ServiceError(Exception):
    """Raised when a request is rejected by a business rule."""


# --------------------------------------------------------------------------- #
# Operators
# --------------------------------------------------------------------------- #

class OperatorService:
    """Local operators. No authentication: a shack is a trusted environment."""

    @staticmethod
    def list_all(include_inactive: bool = False) -> list[Operator]:
        with session_scope() as session:
            stmt = select(Operator).order_by(Operator.callsign)
            if not include_inactive:
                stmt = stmt.where(Operator.is_active.is_(True))
            return list(session.scalars(stmt))

    @staticmethod
    def get(operator_id: int) -> Operator | None:
        with session_scope() as session:
            return session.get(Operator, operator_id)

    @staticmethod
    def get_by_callsign(call: str) -> Operator | None:
        with session_scope() as session:
            normalized = callsign_module.normalize(call)
            return session.scalars(
                select(Operator).where(Operator.callsign == normalized)
            ).first()

    @staticmethod
    def create(call: str, name: str = "", gridsquare: str = "", qth: str = "") -> Operator:
        normalized = callsign_module.normalize(call)
        if not normalized:
            raise ServiceError(_("The operator callsign cannot be empty."))
        with session_scope() as session:
            existing = session.scalars(
                select(Operator).where(Operator.callsign == normalized)
            ).first()
            if existing is not None:
                raise ServiceError(
                    _("Operator {call} already exists.").format(call=normalized)
                )
            operator = Operator(
                callsign=normalized,
                name=name.strip(),
                gridsquare=gridsquare.strip().upper(),
                qth=qth.strip(),
            )
            session.add(operator)
            session.flush()
            return operator

    @staticmethod
    def update(operator_id: int, **changes: Any) -> Operator:
        with session_scope() as session:
            operator = session.get(Operator, operator_id)
            if operator is None:
                raise ServiceError(_("Operator not found."))
            for key, value in changes.items():
                if hasattr(operator, key):
                    setattr(operator, key, value)
            session.flush()
            return operator

    @staticmethod
    def deactivate(operator_id: int) -> None:
        """Operators are never deleted: their QSOs must keep a valid owner."""
        with session_scope() as session:
            operator = session.get(Operator, operator_id)
            if operator is None:
                raise ServiceError(_("Operator not found."))
            operator.is_active = False


# --------------------------------------------------------------------------- #
# Stations
# --------------------------------------------------------------------------- #

class StationService:
    """Radios («emisoras»), each with the antennas it can use."""

    @staticmethod
    def _query():  # type: ignore[no-untyped-def]
        # Loaded eagerly: the objects leave the session before anybody reads
        # their types or configurations.
        return select(Station).options(
            selectinload(Station.types),
            selectinload(Station.profiles),
            selectinload(Station.antennas),
        )

    @staticmethod
    def list_all() -> list[Station]:
        with session_scope() as session:
            return list(session.scalars(StationService._query().order_by(Station.name)))

    @staticmethod
    def get(station_id: int) -> Station | None:
        with session_scope() as session:
            return session.scalars(
                StationService._query().where(Station.id == station_id)
            ).first()

    @staticmethod
    def create(
        name: str,
        rig: str = "",
        power_w: int | None = None,
        notes: str = "",
        type_ids: list[int] | None = None,
        brand: str = "",
    ) -> Station:
        clean = name.strip()
        if not clean:
            raise ServiceError(_("The radio name cannot be empty."))
        with session_scope() as session:
            if _station_named(session, clean) is not None:
                raise ServiceError(
                    _("There is already a radio called «{name}».").format(name=clean)
                )
            station = Station(
                name=clean,
                brand=brand.strip(),
                rig=rig.strip(),
                power_w=power_w,
                notes=notes.strip(),
            )
            station.types = _load_types(session, type_ids or [])
            station.code = next_code(session, Station)
            session.add(station)
            session.flush()
            return station

    @staticmethod
    def update(station_id: int, *, type_ids: list[int] | None = None, **changes: Any) -> Station:
        """Change a station; ``type_ids`` replaces its types when given."""
        with session_scope() as session:
            station = session.get(Station, station_id)
            if station is None:
                raise ServiceError(_("Radio not found."))
            _refuse_preset(station, _("«{name}» is a catalog radio"))
            if "name" in changes:
                changes["name"] = str(changes["name"]).strip()
                if not changes["name"]:
                    raise ServiceError(_("The radio name cannot be empty."))
                other = _station_named(session, changes["name"])
                if other is not None and other.id != station_id:
                    raise ServiceError(
                        _("There is already a radio called «{name}».").format(
                            name=changes["name"]
                        )
                    )
            for key, value in changes.items():
                if hasattr(station, key):
                    setattr(station, key, value)
            if type_ids is not None:
                station.types = _load_types(session, type_ids)
            session.flush()
            return station

    @staticmethod
    def delete(station_id: int) -> None:
        with session_scope() as session:
            station = session.get(Station, station_id)
            if station is None:
                raise ServiceError(_("Radio not found."))
            _refuse_preset(station, _("«{name}» is a catalog radio"))
            # A set must keep at least one radio.
            for equipment in session.scalars(
                select(Equipment).options(selectinload(Equipment.stations))
            ):
                if [s.id for s in equipment.stations] == [station_id]:
                    raise ServiceError(
                        _(
                            "«{radio}» is the only radio of the set «{equipment}»: "
                            "add another or delete the set first."
                        ).format(radio=station.name, equipment=equipment.name)
                    )
            session.execute(
                equipment_station_links.delete().where(
                    equipment_station_links.c.station_id == station_id
                )
            )
            # Detach the station from past QSOs instead of losing the contacts.
            # Its type and configuration links go with it; the configurations
            # themselves stay, they may belong to other stations.
            session.query(Qso).filter(Qso.station_id == station_id).update({"station_id": None})
            session.query(Profile).filter(Profile.station_id == station_id).update(
                {"station_id": None}
            )
            session.delete(station)

    @staticmethod
    def assign_antenna(station_id: int, antenna_id: int) -> None:
        """Make an antenna available under a station."""
        with session_scope() as session:
            station = session.get(Station, station_id)
            antenna = session.get(Antenna, antenna_id)
            if station is None or antenna is None:
                raise ServiceError(_("Radio or antenna not found."))
            if antenna not in station.antennas:
                station.antennas.append(antenna)

    @staticmethod
    def unassign_antenna(station_id: int, antenna_id: int) -> None:
        """Stop offering an antenna under a station; it is not deleted."""
        with session_scope() as session:
            session.execute(
                station_antenna_links.delete().where(
                    station_antenna_links.c.station_id == station_id,
                    station_antenna_links.c.antenna_id == antenna_id,
                )
            )


def _callsign_and_output(repeater: Repeater) -> str:
    """«ED4ZAH 438.325»: a repeater whose callsign names others too."""
    return f"{repeater.callsign} {bands.format_frequency(repeater.output_hz, with_unit=False)}"


def _refuse_preset_repeater(repeater: Repeater) -> None:
    """Repeaters of the URE list are reference data, like the equipment catalog."""
    if repeater.preset:
        raise ServiceError(
            _("{call} comes from the URE list: it cannot be changed or deleted.").format(
                call=repeater.callsign
            )
        )


def _refuse_preset(item: Any, what: str) -> None:
    """Catalog items are shared reference data: never changed or deleted.

    Args:
        item: The radio, antenna or power supply about to change.
        what: Translated start of the message, naming the item with a
            ``{name}`` placeholder, e.g. "«{name}» is a catalog antenna".
    """
    if getattr(item, "preset", False):
        raise ServiceError(
            _("{what}: it cannot be changed or deleted. Use it when building a set.").format(
                what=what.format(name=item.name)
            )
        )


def _station_named(session: Any, name: str) -> Station | None:
    return session.scalars(
        select(Station).where(func.lower(Station.name) == name.lower())
    ).first()


def _load_types(session: Any, type_ids: list[int]) -> list[StationType]:
    if not type_ids:
        return []
    found = list(session.scalars(select(StationType).where(StationType.id.in_(type_ids))))
    if len(found) != len(set(type_ids)):
        raise ServiceError(_("One of the equipment types no longer exists."))
    return found


class StationTypeService:
    """Station types (HF, VHF, UHF, CB...) and the frequencies they cover."""

    @staticmethod
    def list_all() -> list[StationType]:
        with session_scope() as session:
            return list(
                session.scalars(
                    select(StationType).order_by(StationType.min_hz, StationType.name)
                )
            )

    @staticmethod
    def get(type_id: int) -> StationType | None:
        with session_scope() as session:
            return session.get(StationType, type_id)

    @staticmethod
    def create(name: str, min_hz: int | None, max_hz: int | None) -> StationType:
        clean, low, high = _validate_type(name, min_hz, max_hz)
        with session_scope() as session:
            if _type_named(session, clean) is not None:
                raise ServiceError(_("The type «{name}» already exists.").format(name=clean))
            station_type = StationType(name=clean, min_hz=low, max_hz=high)
            session.add(station_type)
            session.flush()
            return station_type

    @staticmethod
    def update(type_id: int, name: str, min_hz: int | None, max_hz: int | None) -> StationType:
        clean, low, high = _validate_type(name, min_hz, max_hz)
        with session_scope() as session:
            station_type = session.get(StationType, type_id)
            if station_type is None:
                raise ServiceError(_("Equipment type not found."))
            other = _type_named(session, clean)
            if other is not None and other.id != type_id:
                raise ServiceError(_("The type «{name}» already exists.").format(name=clean))
            station_type.name = clean
            station_type.min_hz = low
            station_type.max_hz = high
            session.flush()
            return station_type

    @staticmethod
    def delete(type_id: int) -> None:
        with session_scope() as session:
            station_type = session.get(StationType, type_id)
            if station_type is None:
                raise ServiceError(_("Equipment type not found."))
            # The ORM only clears link rows for relationships it knows from
            # this side, and types do not list their stations.
            session.execute(
                station_type_links.delete().where(station_type_links.c.type_id == type_id)
            )
            session.delete(station_type)

    @staticmethod
    def resolve(text: str) -> list[int]:
        """Type ids for a comma separated list of names, case insensitive.

        Raises:
            ServiceError: naming a type that does not exist.
        """
        known = {t.name.lower(): t.id for t in StationTypeService.list_all()}
        ids: list[int] = []
        for raw in text.replace(";", ",").split(","):
            name = raw.strip()
            if not name:
                continue
            type_id = known.get(name.lower())
            if type_id is None:
                raise ServiceError(_("There is no type «{name}».").format(name=name))
            if type_id not in ids:
                ids.append(type_id)
        return ids


class AntennaService:
    """Antennas and the amateur bands each one works on."""

    @staticmethod
    def list_all() -> list[Antenna]:
        with session_scope() as session:
            return list(session.scalars(select(Antenna).order_by(Antenna.name)))

    @staticmethod
    def get(antenna_id: int) -> Antenna | None:
        with session_scope() as session:
            return session.get(Antenna, antenna_id)

    @staticmethod
    def create(
        name: str, bands: list[str] | None = None, notes: str = "", brand: str = ""
    ) -> Antenna:
        clean = _antenna_name(name)
        with session_scope() as session:
            if _antenna_named(session, clean) is not None:
                raise ServiceError(_("The antenna «{name}» already exists.").format(name=clean))
            antenna = Antenna(
                name=clean, brand=brand.strip(), bands=list(bands or []), notes=notes.strip()
            )
            antenna.code = next_code(session, Antenna)
            session.add(antenna)
            session.flush()
            return antenna

    @staticmethod
    def update(
        antenna_id: int, name: str, bands: list[str], notes: str = "", brand: str = ""
    ) -> Antenna:
        clean = _antenna_name(name)
        with session_scope() as session:
            antenna = session.get(Antenna, antenna_id)
            if antenna is None:
                raise ServiceError(_("Antenna not found."))
            _refuse_preset(antenna, _("«{name}» is a catalog antenna"))
            other = _antenna_named(session, clean)
            if other is not None and other.id != antenna_id:
                raise ServiceError(_("The antenna «{name}» already exists.").format(name=clean))
            antenna.name = clean
            antenna.brand = brand.strip()
            antenna.bands = list(bands)
            antenna.notes = notes.strip()
            session.flush()
            return antenna

    @staticmethod
    def delete(antenna_id: int) -> None:
        with session_scope() as session:
            antenna = session.get(Antenna, antenna_id)
            if antenna is None:
                raise ServiceError(_("Antenna not found."))
            _refuse_preset(antenna, _("«{name}» is a catalog antenna"))
            # Past QSOs keep their contact, only the antenna reference goes.
            session.query(Qso).filter(Qso.antenna_id == antenna_id).update({"antenna_id": None})
            session.execute(
                station_antenna_links.delete().where(
                    station_antenna_links.c.antenna_id == antenna_id
                )
            )
            session.execute(
                equipment_antenna_links.delete().where(
                    equipment_antenna_links.c.antenna_id == antenna_id
                )
            )
            session.delete(antenna)

    @staticmethod
    def resolve_bands(text: str) -> list[str]:
        """Band names for a comma separated list, in the plan's order.

        Raises:
            ServiceError: naming something that is not an amateur band.
        """
        found: list[str] = []
        for raw in text.replace(";", ",").split(","):
            name = raw.strip()
            if not name:
                continue
            band = bands.get(name)
            if band is None:
                raise ServiceError(
                    _("«{name}» is not a known band (2m, 70cm, 20m...).").format(name=name)
                )
            if band.name not in found:
                found.append(band.name)
        order = [band.name for band in bands.BANDS]
        return sorted(found, key=order.index)


class PowerSupplyService:
    """Power supplies, registered on their own and grouped into sets."""

    @staticmethod
    def list_all() -> list[PowerSupply]:
        with session_scope() as session:
            return list(session.scalars(select(PowerSupply).order_by(PowerSupply.name)))

    @staticmethod
    def get(supply_id: int) -> PowerSupply | None:
        with session_scope() as session:
            return session.get(PowerSupply, supply_id)

    @staticmethod
    def create(
        name: str,
        voltage_v: float | None = None,
        current_a: float | None = None,
        notes: str = "",
        brand: str = "",
    ) -> PowerSupply:
        clean = _supply_name(name)
        _check_supply_values(voltage_v, current_a)
        with session_scope() as session:
            if _supply_named(session, clean) is not None:
                raise ServiceError(
                    _("The power supply «{name}» already exists.").format(name=clean)
                )
            supply = PowerSupply(
                name=clean,
                brand=brand.strip(),
                voltage_v=voltage_v,
                current_a=current_a,
                notes=notes.strip(),
            )
            supply.code = next_code(session, PowerSupply)
            session.add(supply)
            session.flush()
            return supply

    @staticmethod
    def update(
        supply_id: int,
        name: str,
        voltage_v: float | None = None,
        current_a: float | None = None,
        notes: str = "",
        brand: str = "",
    ) -> PowerSupply:
        clean = _supply_name(name)
        _check_supply_values(voltage_v, current_a)
        with session_scope() as session:
            supply = session.get(PowerSupply, supply_id)
            if supply is None:
                raise ServiceError(_("Power supply not found."))
            _refuse_preset(supply, _("«{name}» is a catalog power supply"))
            other = _supply_named(session, clean)
            if other is not None and other.id != supply_id:
                raise ServiceError(
                    _("The power supply «{name}» already exists.").format(name=clean)
                )
            supply.name = clean
            supply.brand = brand.strip()
            supply.voltage_v = voltage_v
            supply.current_a = current_a
            supply.notes = notes.strip()
            session.flush()
            return supply

    @staticmethod
    def delete(supply_id: int) -> None:
        with session_scope() as session:
            supply = session.get(PowerSupply, supply_id)
            if supply is None:
                raise ServiceError(_("Power supply not found."))
            _refuse_preset(supply, _("«{name}» is a catalog power supply"))
            session.execute(
                equipment_supply_links.delete().where(
                    equipment_supply_links.c.supply_id == supply_id
                )
            )
            session.delete(supply)


def _supply_name(name: str) -> str:
    clean = name.strip()
    if not clean:
        raise ServiceError(_("The power supply name cannot be empty."))
    return clean


def _check_supply_values(voltage_v: float | None, current_a: float | None) -> None:
    if voltage_v is not None and voltage_v <= 0:
        raise ServiceError(_("The voltage must be positive."))
    if current_a is not None and current_a <= 0:
        raise ServiceError(_("The current must be positive."))


def _supply_named(session: Any, name: str) -> PowerSupply | None:
    return session.scalars(
        select(PowerSupply).where(func.lower(PowerSupply.name) == name.lower())
    ).first()


class EquipmentService:
    """Equipment sets: at least one radio, plus antennas and power supplies."""

    @staticmethod
    def _query():  # type: ignore[no-untyped-def]
        return select(Equipment).options(
            selectinload(Equipment.stations),
            selectinload(Equipment.antennas),
            selectinload(Equipment.supplies),
        )

    @staticmethod
    def list_all() -> list[Equipment]:
        with session_scope() as session:
            return list(
                session.scalars(EquipmentService._query().order_by(Equipment.name))
            )

    @staticmethod
    def get(equipment_id: int) -> Equipment | None:
        with session_scope() as session:
            return session.scalars(
                EquipmentService._query().where(Equipment.id == equipment_id)
            ).first()

    @staticmethod
    def create(
        name: str,
        station_ids: list[int],
        antenna_ids: list[int] | None = None,
        supply_ids: list[int] | None = None,
        notes: str = "",
    ) -> Equipment:
        clean = _equipment_name(name)
        with session_scope() as session:
            if _equipment_named(session, clean) is not None:
                raise ServiceError(_("The set «{name}» already exists.").format(name=clean))
            equipment = Equipment(name=clean, notes=notes.strip())
            _fill_equipment(session, equipment, station_ids, antenna_ids, supply_ids)
            session.add(equipment)
            session.flush()
            return equipment

    @staticmethod
    def update(
        equipment_id: int,
        name: str,
        station_ids: list[int],
        antenna_ids: list[int] | None = None,
        supply_ids: list[int] | None = None,
        notes: str = "",
    ) -> Equipment:
        clean = _equipment_name(name)
        with session_scope() as session:
            equipment = session.get(Equipment, equipment_id)
            if equipment is None:
                raise ServiceError(_("Equipment set not found."))
            other = _equipment_named(session, clean)
            if other is not None and other.id != equipment_id:
                raise ServiceError(_("The set «{name}» already exists.").format(name=clean))
            equipment.name = clean
            equipment.notes = notes.strip()
            _fill_equipment(session, equipment, station_ids, antenna_ids, supply_ids)
            session.flush()
            return equipment

    @staticmethod
    def delete(equipment_id: int) -> None:
        """Remove the set; its radios, antennas and supplies stay.

        QSOs made with it keep their radio and antenna, only the set goes.
        """
        with session_scope() as session:
            equipment = session.get(Equipment, equipment_id)
            if equipment is None:
                raise ServiceError(_("Equipment set not found."))
            session.query(Qso).filter(Qso.equipment_id == equipment_id).update(
                {"equipment_id": None}
            )
            session.query(Profile).filter(Profile.equipment_id == equipment_id).update(
                {"equipment_id": None}
            )
            session.delete(equipment)


def _equipment_name(name: str) -> str:
    clean = name.strip()
    if not clean:
        raise ServiceError(_("The set name cannot be empty."))
    return clean


def _equipment_named(session: Any, name: str) -> Equipment | None:
    return session.scalars(
        select(Equipment).where(func.lower(Equipment.name) == name.lower())
    ).first()


def _fill_equipment(
    session: Any,
    equipment: Equipment,
    station_ids: list[int],
    antenna_ids: list[int] | None,
    supply_ids: list[int] | None,
) -> None:
    """Replace what a set is made of, checking that every part exists."""
    if not station_ids:
        raise ServiceError(_("A set needs at least one radio."))
    equipment.stations = _load_all(
        session, Station, station_ids, _("One of the radios no longer exists.")
    )
    equipment.antennas = _load_all(
        session, Antenna, antenna_ids or [], _("One of the antennas no longer exists.")
    )
    equipment.supplies = _load_all(
        session, PowerSupply, supply_ids or [], _("One of the power supplies no longer exists.")
    )


def _load_all(session: Any, model: Any, ids: list[int], missing: str) -> list[Any]:
    """Load every row of ``ids``; ``missing`` is the error when one is gone."""
    if not ids:
        return []
    found = list(session.scalars(select(model).where(model.id.in_(ids))))
    if len(found) != len(set(ids)):
        raise ServiceError(missing)
    return found


def _antenna_name(name: str) -> str:
    clean = name.strip()
    if not clean:
        raise ServiceError(_("The antenna name cannot be empty."))
    return clean


def _antenna_named(session: Any, name: str) -> Antenna | None:
    return session.scalars(
        select(Antenna).where(func.lower(Antenna.name) == name.lower())
    ).first()


def _validate_type(name: str, min_hz: int | None, max_hz: int | None) -> tuple[str, int, int]:
    clean = name.strip()
    if not clean:
        raise ServiceError(_("The type name cannot be empty."))
    if min_hz is None or max_hz is None:
        raise ServiceError(_("Both the minimum and the maximum frequency are needed."))
    if min_hz <= 0 or min_hz >= max_hz:
        raise ServiceError(
            _("The minimum frequency must be positive and lower than the maximum.")
        )
    return clean, min_hz, max_hz


def _type_named(session: Any, name: str) -> StationType | None:
    return session.scalars(
        select(StationType).where(func.lower(StationType.name) == name.lower())
    ).first()


# --------------------------------------------------------------------------- #
# Repeaters
# --------------------------------------------------------------------------- #

class RepeaterService:
    """Repeaters the operator works through.

    A callsign may name several repeaters (one club, several bands or modes),
    so a repeater is told apart by its callsign and output frequency.
    """

    @staticmethod
    def list_all(include_inactive: bool = False) -> list[Repeater]:
        with session_scope() as session:
            stmt = select(Repeater).order_by(Repeater.band, Repeater.output_hz)
            if not include_inactive:
                stmt = stmt.where(Repeater.is_active.is_(True))
            return list(session.scalars(stmt))

    @staticmethod
    def search(text: str = "", *, limit: int = 1000) -> list[Repeater]:
        """By callsign, URE number, channel, who runs it, place, locator or mode.

        Every word must match somewhere, so «R5 Madrid» or «DMR IN80» narrow
        the list down. Ordered by callsign, then output frequency.
        """
        with session_scope() as session:
            stmt = (
                select(Repeater)
                .order_by(Repeater.callsign, Repeater.output_hz)
                .limit(limit)
            )
            for word in text.split():
                pattern = f"%{word}%"
                stmt = stmt.where(
                    or_(
                        Repeater.callsign.ilike(pattern),
                        func.lower(Repeater.ure_number) == word.lower(),
                        Repeater.channel.ilike(pattern),
                        Repeater.name.ilike(pattern),
                        Repeater.qth.ilike(pattern),
                        Repeater.gridsquare.ilike(f"{word}%"),
                        Repeater.mode.ilike(word.replace("-", "")),
                        Repeater.band.ilike(word),
                        Repeater.notes.ilike(pattern),
                    )
                )
            return list(session.scalars(stmt))

    @staticmethod
    def count(text: str = "") -> int:
        """How many repeaters match ``text`` the way ``search`` reads it."""
        return len(RepeaterService.search(text, limit=100_000))

    @staticmethod
    def get(repeater_id: int) -> Repeater | None:
        with session_scope() as session:
            return session.get(Repeater, repeater_id)

    @staticmethod
    def get_by_callsign(call: str) -> Repeater | None:
        """The first repeater with that callsign; see ``resolve`` for the rest."""
        found = RepeaterService.with_callsign(call)
        return found[0] if found else None

    @staticmethod
    def with_callsign(call: str) -> list[Repeater]:
        """Every repeater with that callsign, by output frequency."""
        with session_scope() as session:
            return list(
                session.scalars(
                    select(Repeater)
                    .where(Repeater.callsign == callsign_module.normalize(call))
                    .order_by(Repeater.output_hz)
                )
            )

    @staticmethod
    def label(repeater: Repeater) -> str:
        """How the operator names it: the callsign, plus the output when shared.

        ``resolve`` reads it back, so a profile edited and saved keeps its
        repeater.
        """
        if len(RepeaterService.with_callsign(repeater.callsign)) > 1:
            return _callsign_and_output(repeater)
        return repeater.callsign

    @staticmethod
    def labels() -> list[str]:
        """The label of every repeater, for completion."""
        everything = RepeaterService.search(limit=100_000)
        counts: dict[str, int] = {}
        for repeater in everything:
            counts[repeater.callsign] = counts.get(repeater.callsign, 0) + 1
        shared = {call for call, count in counts.items() if count > 1}
        return [
            _callsign_and_output(r) if r.callsign in shared else r.callsign for r in everything
        ]

    @staticmethod
    def resolve(text: str) -> Repeater:
        """The repeater the operator means: «ED7ZAE», «ED4ZAH 438.325», «ED4ZAH DMR».

        After the callsign may come its output frequency, its mode or its
        band, which pick one when the callsign names several.

        Raises:
            ServiceError: no repeater has that callsign, or several fit.
        """
        call, _sep, rest = text.strip().partition(" ")
        found = RepeaterService.with_callsign(call)
        if not found:
            raise ServiceError(
                _("No repeater registered with the callsign «{call}»").format(call=call)
            )
        hint = rest.strip()
        if hint:
            mode = modes.get(hint)
            freq_hz = bands.parse_frequency(hint)
            found = [
                r
                for r in found
                if (freq_hz is not None and r.output_hz == freq_hz)
                or (mode is not None and r.mode == mode.name)
                or r.band.lower() == hint.lower()
            ]
            if not found:
                raise ServiceError(
                    _("No repeater {call} matches «{hint}»").format(
                        call=callsign_module.normalize(call), hint=hint
                    )
                )
        if len(found) > 1:
            message = _("{call} names {count} repeaters; add the frequency or the mode: {options}")
            raise ServiceError(
                message.format(
                    call=found[0].callsign,
                    count=len(found),
                    options=", ".join(f"{_callsign_and_output(r)} ({r.mode})" for r in found),
                )
            )
        return found[0]

    @staticmethod
    def _check_unique(
        session: Any, callsign: str, output_hz: int, exclude_id: int | None = None
    ) -> None:
        stmt = select(Repeater.id).where(
            Repeater.callsign == callsign, Repeater.output_hz == output_hz
        )
        if exclude_id is not None:
            stmt = stmt.where(Repeater.id != exclude_id)
        if session.scalars(stmt).first() is not None:
            raise ServiceError(
                _("Repeater {call} already exists on {freq}.").format(
                    call=callsign, freq=bands.format_frequency(output_hz)
                )
            )

    @staticmethod
    def create(
        callsign_text: str,
        *,
        name: str = "",
        output_hz: int | None = None,
        shift_hz: int | None = None,
        input_hz: int | None = None,
        mode: str = "FM",
        ctcss_tx: str = "",
        ctcss_rx: str = "",
        dcs: str = "",
        qth: str = "",
        gridsquare: str = "",
        ure_number: str = "",
        channel: str = "",
        digital_data: dict[str, str] | None = None,
        notes: str = "",
    ) -> Repeater:
        """Register a repeater.

        Args:
            callsign_text: Repeater callsign, e.g. "ED7ZAE".
            output_hz: Frequency it transmits on, the one you tune.
            shift_hz: Offset to its input. When omitted, ``input_hz`` sets
                it, and without that the conventional shift for the band.
            input_hz: Frequency it listens on, the one you transmit on.
            ure_number: The number the URE gives it, e.g. "R5".

        Raises:
            ServiceError: On a callsign already registered on that output,
                or a missing frequency.
        """
        normalized = callsign_module.normalize(callsign_text)
        if not normalized:
            raise ServiceError(_("The repeater callsign cannot be empty."))
        if not output_hz:
            raise ServiceError(_("The repeater output frequency is missing."))

        band = bands.from_frequency(output_hz)
        band_name = band.name if band else ""
        if shift_hz is None:
            shift_hz = (
                input_hz - output_hz if input_hz else repeaters.default_shift(band_name)
            )

        with session_scope() as session:
            RepeaterService._check_unique(session, normalized, output_hz)
            repeater = Repeater(
                callsign=normalized,
                name=name.strip(),
                ure_number=ure_number.strip().upper(),
                channel=channel.strip().upper(),
                band=band_name,
                output_hz=output_hz,
                input_hz=repeaters.input_frequency(output_hz, shift_hz),
                shift_hz=shift_hz,
                mode=(modes.get(mode).name if modes.get(mode) else "FM"),
                ctcss_tx=repeaters.normalize_tone(ctcss_tx),
                ctcss_rx=repeaters.normalize_tone(ctcss_rx),
                dcs=dcs.strip(),
                qth=qth.strip(),
                gridsquare=gridsquare.strip().upper(),
                digital_data=dict(digital_data or {}),
                notes=notes.strip(),
            )
            session.add(repeater)
            session.flush()
            return repeater

    @staticmethod
    def update(repeater_id: int, **changes: Any) -> Repeater:
        """Update a repeater, keeping band, input and shift consistent.

        ``input_hz`` may be given instead of ``shift_hz``: the shift follows.

        Raises:
            ServiceError: it is not there, it comes from the URE list, or
                its callsign and output are another repeater's.
        """
        with session_scope() as session:
            repeater = session.get(Repeater, repeater_id)
            if repeater is None:
                raise ServiceError(_("Repeater not found."))
            _refuse_preset_repeater(repeater)

            if "callsign" in changes:
                changes["callsign"] = callsign_module.normalize(str(changes["callsign"]))
                if not changes["callsign"]:
                    raise ServiceError(_("The repeater callsign cannot be empty."))
            for key in ("ctcss_tx", "ctcss_rx"):
                if key in changes:
                    changes[key] = repeaters.normalize_tone(str(changes[key]))
            input_hz = changes.pop("input_hz", None)

            for key, value in changes.items():
                if hasattr(repeater, key):
                    setattr(repeater, key, value)

            if repeater.output_hz:
                if input_hz:
                    repeater.shift_hz = input_hz - repeater.output_hz
                band = bands.from_frequency(repeater.output_hz)
                repeater.band = band.name if band else ""
                repeater.input_hz = repeaters.input_frequency(
                    repeater.output_hz, repeater.shift_hz or 0
                )
                RepeaterService._check_unique(
                    session, repeater.callsign, repeater.output_hz, exclude_id=repeater.id
                )
            session.flush()
            return repeater

    @staticmethod
    def delete(repeater_id: int) -> None:
        """Remove a repeater, leaving logged contacts intact.

        Contacts keep ``repeater_call``, so the log still records which
        repeater was used even after the entry is gone.
        """
        with session_scope() as session:
            repeater = session.get(Repeater, repeater_id)
            if repeater is None:
                raise ServiceError(_("Repeater not found."))
            _refuse_preset_repeater(repeater)
            session.query(Qso).filter(Qso.repeater_id == repeater_id).update(
                {"repeater_id": None}
            )
            session.query(Profile).filter(Profile.repeater_id == repeater_id).update(
                {"repeater_id": None}
            )
            session.delete(repeater)

    @staticmethod
    def apply_to_state(repeater_id: int, state: SessionState) -> SessionState:
        """Put the session on a repeater, adopting all of its settings."""
        repeater = RepeaterService.get(repeater_id)
        if repeater is None:
            raise ServiceError(_("Repeater not found."))
        state.set_repeater(
            repeater.id,
            repeater.callsign,
            output_hz=repeater.output_hz,
            input_hz=repeater.input_hz,
            band=repeater.band,
            mode=repeater.mode,
            digital_data=dict(repeater.digital_data or {}),
        )
        return state


# --------------------------------------------------------------------------- #
# Profiles
# --------------------------------------------------------------------------- #

class ProfileService:
    """Profiles: what every QSO inherits while one of them is active.

    A profile holds the operator, the setup, the frequency, the mode, the
    power, the repeater and the digital values. Ten of them can take the keys
    Ctrl+0 to Ctrl+9, and one can be the default, activated on start.
    """

    #: The keys a main profile can take.
    SLOTS = range(10)

    @staticmethod
    def _query():  # type: ignore[no-untyped-def]
        return select(Profile).options(
            joinedload(Profile.operator),
            joinedload(Profile.repeater),
            joinedload(Profile.equipment).options(*_EQUIPMENT_PARTS),
            selectinload(Profile.stations),
        )

    @staticmethod
    def list_all() -> list[Profile]:
        """The main profiles by their key, then the rest by name."""
        with session_scope() as session:
            return list(
                session.scalars(
                    ProfileService._query().order_by(
                        Profile.slot.is_(None), Profile.slot, Profile.name
                    )
                ).unique()
            )

    @staticmethod
    def get_by_slot(slot: int) -> Profile | None:
        with session_scope() as session:
            return session.scalars(
                ProfileService._query().where(Profile.slot == slot)
            ).first()

    @staticmethod
    def save(
        profile_id: int | None,
        *,
        name: str,
        slot: int | None = None,
        operator_id: int | None = None,
        equipment_id: int | None = None,
        repeater_id: int | None = None,
        freq_hz: int | None = None,
        mode: str = "",
        power_w: int | None = None,
        digital_data: dict[str, str] | None = None,
    ) -> Profile:
        """Create (``profile_id`` None) or update a profile.

        The band follows from the frequency. A key already held by another
        profile moves to this one: there is only one Ctrl+<slot>.

        Raises:
            ServiceError: empty or repeated name, or a key outside 0-9.
        """
        clean = name.strip()
        if not clean:
            raise ServiceError(_("The profile name cannot be empty."))
        if slot is not None and slot not in ProfileService.SLOTS:
            raise ServiceError(_("The key of a profile goes from 0 to 9."))
        with session_scope() as session:
            other = session.scalars(select(Profile).where(Profile.name == clean)).first()
            if other is not None and other.id != profile_id:
                raise ServiceError(_("The profile «{name}» already exists.").format(name=clean))
            if profile_id is None:
                profile = Profile(name=clean)
                session.add(profile)
                session.flush()
            else:
                profile = session.get(Profile, profile_id)
                if profile is None:
                    raise ServiceError(_("Profile not found."))
            if slot is not None:
                session.query(Profile).filter(
                    Profile.slot == slot, Profile.id != profile.id
                ).update({"slot": None})
            found = bands.from_frequency(freq_hz) if freq_hz else None
            profile.name = clean
            profile.slot = slot
            profile.operator_id = operator_id
            profile.equipment_id = equipment_id
            profile.repeater_id = repeater_id
            profile.freq_hz = freq_hz
            profile.band = found.name if found else ""
            profile.mode = mode
            profile.power_w = power_w
            profile.digital_data = dict(digital_data or {})
            session.flush()
            profile_id = profile.id
        saved = ProfileService.get(profile_id)
        assert saved is not None
        return saved

    @staticmethod
    def get(profile_id: int) -> Profile | None:
        with session_scope() as session:
            return session.scalars(
                ProfileService._query().where(Profile.id == profile_id)
            ).first()

    @staticmethod
    def get_default() -> Profile | None:
        with session_scope() as session:
            return session.scalars(select(Profile).where(Profile.is_default.is_(True))).first()

    @staticmethod
    def for_station(station_id: int, antenna_id: int | None = None) -> list[Profile]:
        """Configurations assigned to a station, by name.

        With ``antenna_id``, only those on a band the antenna works on.
        """
        with session_scope() as session:
            profiles = list(
                session.scalars(
                    ProfileService._query()
                    .join(station_profile_links)
                    .where(station_profile_links.c.station_id == station_id)
                    .order_by(Profile.name)
                )
            )
            antenna = session.get(Antenna, antenna_id) if antenna_id else None
        if antenna is None:
            return profiles
        return [profile for profile in profiles if antenna.covers_band(profile.band)]

    @staticmethod
    def unassigned() -> list[Profile]:
        """Configurations no station offers yet."""
        with session_scope() as session:
            assigned = select(station_profile_links.c.profile_id)
            return list(
                session.scalars(
                    ProfileService._query()
                    .where(Profile.id.not_in(assigned))
                    .order_by(Profile.name)
                )
            )

    @staticmethod
    def save_from_state(
        name: str,
        state: SessionState,
        *,
        overwrite: bool = False,
        station_id: int | None = None,
        antenna_id: int | None = None,
    ) -> Profile:
        """Store the current working configuration under ``name``.

        With ``station_id`` the configuration is also assigned to that
        station, and refused if the station cannot tune it or, when
        ``antenna_id`` is given too, if that antenna does not work on its band.
        """
        clean = name.strip()
        if not clean:
            raise ServiceError(_("The profile name cannot be empty."))
        with session_scope() as session:
            profile = session.scalars(select(Profile).where(Profile.name == clean)).first()
            if profile is not None and not overwrite:
                raise ServiceError(_("The profile «{name}» already exists.").format(name=clean))
            station = _station_for_assignment(session, station_id) if station_id else None
            if station is not None:
                _check_covers(station, clean, state.freq_hz)
            antenna = session.get(Antenna, antenna_id) if antenna_id else None
            if antenna is not None and not antenna.covers_band(state.band):
                raise ServiceError(
                    _("The antenna «{antenna}» does not work on {band} ({bands}).").format(
                        antenna=antenna.name, band=state.band, bands=antenna.band_names
                    )
                )
            if profile is None:
                profile = Profile(name=clean)
                session.add(profile)
            profile.operator_id = state.operator_id
            profile.repeater_id = state.repeater_id
            profile.band = state.band
            profile.freq_hz = state.freq_hz
            profile.mode = state.mode
            profile.power_w = state.power_w
            profile.equipment_id = state.equipment_id
            profile.digital_data = dict(state.digital_data)
            profile.field_order = list(state.field_order)
            profile.separator = state.separator
            if station is not None and station not in profile.stations:
                profile.stations.append(station)
            session.flush()
            return profile

    @staticmethod
    def rename(profile_id: int, name: str) -> Profile:
        clean = name.strip()
        if not clean:
            raise ServiceError(_("The profile name cannot be empty."))
        with session_scope() as session:
            profile = session.get(Profile, profile_id)
            if profile is None:
                raise ServiceError(_("Profile not found."))
            other = session.scalars(select(Profile).where(Profile.name == clean)).first()
            if other is not None and other.id != profile_id:
                raise ServiceError(_("The profile «{name}» already exists.").format(name=clean))
            profile.name = clean
            session.flush()
            return profile

    @staticmethod
    def assign(profile_id: int, station_id: int) -> None:
        """Offer a configuration under a station.

        Raises:
            ServiceError: the configuration is tuned outside every type of
                the station.
        """
        with session_scope() as session:
            profile = session.get(Profile, profile_id)
            if profile is None:
                raise ServiceError(_("Profile not found."))
            station = _station_for_assignment(session, station_id)
            _check_covers(station, profile.name, profile.freq_hz)
            if station not in profile.stations:
                profile.stations.append(station)

    @staticmethod
    def unassign(profile_id: int, station_id: int) -> None:
        """Stop offering a configuration under a station; it is not deleted."""
        with session_scope() as session:
            session.execute(
                station_profile_links.delete().where(
                    station_profile_links.c.profile_id == profile_id,
                    station_profile_links.c.station_id == station_id,
                )
            )

    @staticmethod
    def apply_to_state(
        profile_id: int,
        state: SessionState,
        *,
        station_id: int | None = None,
        antenna_id: int | None = None,
    ) -> SessionState:
        """Make a profile the active one: overwrite ``state`` in place.

        With a setup, the station and antenna are the ones of the set that
        suit the frequency. Without one they only change when a station is
        given.
        """
        profile = ProfileService.get(profile_id)
        if profile is None:
            raise ServiceError(_("Profile not found."))
        state.operator_id = profile.operator_id or state.operator_id
        if station_id is not None:
            state.station_id = station_id
            state.antenna_id = antenna_id
        state.band = profile.band
        state.freq_hz = profile.freq_hz
        state.mode = profile.mode or modes.DEFAULT_MODE
        state.power_w = profile.power_w
        state.digital_data = dict(profile.digital_data or {})
        state.equipment_id = profile.equipment_id
        if profile.equipment is not None:
            station, antenna = profile.equipment.parts_for(profile.freq_hz, profile.band)
            state.station_id = station.id if station else None
            state.antenna_id = antenna.id if antenna else None
        # The repeater is restored last so it is not cleared by the fields
        # above, and only when it still exists.
        state.clear_repeater()
        if profile.repeater_id is not None:
            repeater = RepeaterService.get(profile.repeater_id)
            if repeater is not None:
                state.repeater_id = repeater.id
                state.repeater_call = repeater.callsign
                state.freq_tx_hz = repeater.input_hz
        state.field_order = tuple(profile.field_order) if profile.field_order else state.field_order
        state.separator = profile.separator or ","
        state.profile_id = profile.id
        state.profile_name = profile.name
        return state

    @staticmethod
    def clear_default() -> None:
        """No profile is activated on start any more."""
        with session_scope() as session:
            session.query(Profile).update({"is_default": False})

    @staticmethod
    def set_default(profile_id: int) -> None:
        with session_scope() as session:
            session.query(Profile).update({"is_default": False})
            profile = session.get(Profile, profile_id)
            if profile is None:
                raise ServiceError(_("Profile not found."))
            profile.is_default = True

    @staticmethod
    def delete(profile_id: int) -> None:
        with session_scope() as session:
            profile = session.get(Profile, profile_id)
            if profile is None:
                raise ServiceError(_("Profile not found."))
            session.delete(profile)


def _station_for_assignment(session: Any, station_id: int) -> Station:
    station = session.scalars(
        select(Station).options(selectinload(Station.types)).where(Station.id == station_id)
    ).first()
    if station is None:
        raise ServiceError(_("Rig not found."))
    return station


def _check_covers(station: Station, profile_name: str, freq_hz: int | None) -> None:
    if not station.covers(freq_hz):
        raise ServiceError(
            _(
                "«{profile}» ({frequency}) is outside the types of «{radio}» ({types})."
            ).format(
                profile=profile_name,
                frequency=bands.format_frequency(freq_hz),
                radio=station.name,
                types=station.type_names,
            )
        )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

class SettingsService:
    """Key/value store for anything that is not a first class entity."""

    STATE_KEY = "session_state"

    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        with session_scope() as session:
            row = session.scalars(select(Setting).where(Setting.key == key)).first()
            return row.value if row is not None else default

    @staticmethod
    def set(key: str, value: Any) -> None:
        with session_scope() as session:
            row = session.scalars(select(Setting).where(Setting.key == key)).first()
            if row is None:
                session.add(Setting(key=key, value=value))
            else:
                row.value = value

    @classmethod
    def load_state(cls) -> SessionState:
        return SessionState.from_dict(cls.get(cls.STATE_KEY, {}) or {})

    @classmethod
    def save_state(cls, state: SessionState) -> None:
        cls.set(cls.STATE_KEY, state.to_dict())


# --------------------------------------------------------------------------- #
# QSOs
# --------------------------------------------------------------------------- #

#: What a set needs loaded to tell whether a frequency and band suit it.
_EQUIPMENT_PARTS = (
    selectinload(Equipment.stations).selectinload(Station.types),
    selectinload(Equipment.antennas),
)

#: Eager loads that let a QSO row say whether its equipment set fits it.
_EQUIPMENT_LOAD = (
    selectinload(Qso.equipment).selectinload(Equipment.stations).selectinload(Station.types),
    selectinload(Qso.equipment).selectinload(Equipment.antennas),
)


def _book_names(session: Any, qsos: list[Qso]) -> dict[str, tuple[str, str]]:
    """First and full name in the address book of each station, one query."""
    calls = {qso.base_call for qso in qsos if qso.base_call}
    if not calls:
        return {}
    rows = session.execute(
        select(Contact.base_call, Contact.first_name, Contact.last_name).where(
            Contact.base_call.in_(calls)
        )
    ).all()
    return {base: (first, f"{first} {last}".strip()) for base, first, last in rows}


def _name_drifts(name: str, book: tuple[str, str] | None) -> bool:
    """Whether a QSO's name disagrees with the address book.

    It agrees with the first name or the full one, ignoring case and extra
    spaces; a QSO without a name, or a station the book has no name for, has
    nothing to disagree with.
    """
    heard = " ".join(name.split()).casefold()
    if not heard or book is None or not any(book):
        return False
    return heard not in {part.casefold() for part in book}


def _to_rows(session: Any, qsos: list[Qso]) -> list[QsoRow]:
    book = _book_names(session, qsos)
    return [_to_row(qso, book) for qso in qsos]


def _to_row(qso: Qso, book: dict[str, tuple[str, str]] | None = None) -> QsoRow:
    """Convert an eagerly loaded QSO into the UI/API shape.

    ``book`` holds the address book names of the stations, from
    _book_names(); without it the QSO is not compared with the book.
    """
    names = (book or {}).get(qso.base_call)
    return QsoRow(
        id=qso.id,
        call=qso.call,
        name=qso.name,
        qso_utc=qso.qso_utc,
        band=qso.band,
        freq_hz=qso.freq_hz,
        freq_tx_hz=qso.freq_tx_hz,
        mode=qso.mode,
        rst_sent=qso.rst_sent,
        rst_rcvd=qso.rst_rcvd,
        qth=qso.qth,
        gridsquare=qso.gridsquare,
        country=qso.country,
        comment=qso.comment,
        entry_mode=qso.entry_mode.value if qso.entry_mode else EntryMode.AUTO.value,
        operator_callsign=qso.operator.callsign if qso.operator else "",
        station_name=qso.station.name if qso.station else "",
        repeater_call=qso.repeater_call or "",
        antenna_name=qso.antenna.name if qso.antenna else "",
        digital_data=dict(qso.digital_data or {}),
        equipment_name=qso.equipment.name if qso.equipment else "",
        equipment_mismatch=(
            qso.equipment is not None and not qso.equipment.fits(qso.freq_hz, qso.band)
        ),
        book_name=names[1] if names else "",
        name_drift=_name_drifts(qso.name, names),
    )


def _radio_part(station: Station) -> PartRow:
    power = f"{station.power_w} W" if station.power_w else ""
    details = " · ".join(part for part in (power, station.type_names) if part)
    return PartRow(station.code or "", station.name, details)


def _antenna_part(antenna: Antenna) -> PartRow:
    return PartRow(antenna.code or "", antenna.name, antenna.band_names)


def _supply_part(supply: PowerSupply) -> PartRow:
    def number(value: float | None, unit: str) -> str:
        return f"{value:g} {unit}" if value is not None else ""

    details = " · ".join(
        part for part in (number(supply.voltage_v, "V"), number(supply.current_a, "A")) if part
    )
    return PartRow(supply.code or "", supply.name, details)


def _repeater_summary(repeater: Repeater) -> str:
    parts = [
        repeater.callsign,
        _("listen {rx} · transmit {tx}").format(
            rx=bands.format_frequency(repeater.output_hz),
            tx=bands.format_frequency(repeater.input_hz),
        ),
    ]
    if repeater.ctcss_tx:
        parts.append(_("tone {tone} Hz").format(tone=repeater.ctcss_tx))
    if repeater.name:
        parts.append(repeater.name)
    return " · ".join(parts)


def _fill_from_address_book(fields: dict[str, Any], call: str) -> None:
    """Complete blank fields from the address book.

    What the operator typed always wins: the book only supplies values that
    were left empty, so a name heard on air is never replaced by a stale one
    from a downloaded list.
    """
    wanted = ("name", "qth", "gridsquare", "country")
    if all(str(fields.get(key, "")).strip() for key in wanted):
        return

    entry = ContactService.lookup(call)
    if entry is None:
        return

    if not str(fields.get("name", "")).strip():
        name = entry.first_name or entry.full_name
        if name:
            fields["name"] = name
    if not str(fields.get("qth", "")).strip() and (entry.city or entry.state):
        fields["qth"] = entry.city or entry.state
    if not str(fields.get("gridsquare", "")).strip() and entry.gridsquare:
        fields["gridsquare"] = entry.gridsquare
    if not str(fields.get("country", "")).strip() and entry.country:
        fields["country"] = entry.country


def _remember_in_address_book(row: QsoRow) -> None:
    """Add a station to the address book the first time it is worked.

    An entry already there keeps what it has, since what the operator typed
    into it is worth more than what a single QSO happens to carry. What it
    lacks, it takes from the QSO: a name when it has none, the QTH as its city
    when it has none. Only the address book changes: earlier QSOs with the
    station are left exactly as they were logged. The entry is filed under
    the home callsign, so working the same person portable does not produce a
    second one.

    The name heard on air goes whole into the first name: «Jose Manuel» is
    one name, not a name and a surname, and there is no telling them apart.

    A failure here must never cost the operator the QSO, so it is swallowed.
    """
    base = callsign_module.base_call(row.call)
    if not base:
        return
    name = " ".join(row.name.split())
    try:
        known = ContactService.lookup(base)
        if known is not None:
            missing: dict[str, Any] = {}
            if row.name and not (known.first_name or known.last_name):
                missing["first_name"] = name
            if row.qth and not known.city:
                missing["city"] = row.qth
            if missing:
                ContactService.update(known.id, **missing)
            return
        ContactService.create(
            base,
            first_name=name,
            city=row.qth,
            country=row.country,
            gridsquare=row.gridsquare,
            source="log",
        )
    except ServiceError:
        # Someone else got there first, or the callsign is unusable.
        return


class QsoService:
    """Logging, searching and editing contacts."""

    @staticmethod
    def log(
        fields: dict[str, Any],
        state: SessionState,
        *,
        digital: dict[str, str] | None = None,
        qso_utc: dt.datetime | None = None,
    ) -> QsoRow:
        """Create a contact from parsed entry fields plus the session defaults.

        Args:
            fields: Values coming from the fast entry parser.
            state: Working configuration supplying band, mode, operator, rig.
            digital: Mode specific values overriding the session ones.
            qso_utc: Explicit timestamp. Supplying it marks the QSO as manual,
                which is what unlocks full editing later.

        Returns:
            The stored contact as a QsoRow.
        """
        if state.operator_id is None:
            raise ServiceError(_("No operator is selected."))

        call_raw = str(fields.get("call", "")).strip()
        if not call_raw:
            raise ServiceError(_("The callsign is missing."))

        entry_mode = EntryMode.MANUAL if qso_utc is not None else EntryMode.AUTO
        timestamp = qso_utc or dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None, microsecond=0
        )

        merged_digital = dict(state.digital_data)
        merged_digital.update(digital or {})

        if state.autofill_from_book:
            _fill_from_address_book(fields, call_raw)

        freq_hz = fields.get("freq_hz", state.freq_hz)
        band = str(fields.get("band") or state.band or "")
        if not band and freq_hz:
            found = bands.from_frequency(int(freq_hz))
            band = found.name if found else ""

        # An explicit frequency on the entry line means simplex on that
        # frequency, so it overrides the repeater rather than contradicting it.
        overridden = "freq_hz" in fields or "band" in fields
        repeater_id = None if overridden else state.repeater_id
        repeater_call = "" if overridden else state.repeater_call
        freq_tx_hz = None if overridden else state.freq_tx_hz

        with session_scope() as session:
            station_id, antenna_id = state.station_id, state.antenna_id
            equipment = (
                session.scalars(
                    select(Equipment)
                    .options(*_EQUIPMENT_PARTS)
                    .where(Equipment.id == state.equipment_id)
                ).first()
                if state.equipment_id is not None
                else None
            )
            if equipment is not None:
                # As when a setup is assigned by hand: the radio and antenna
                # of the set that suit this QSO.
                station, antenna = equipment.parts_for(
                    int(freq_hz) if freq_hz else None, band
                )
                station_id = station.id if station else None
                antenna_id = antenna.id if antenna else None
            qso = Qso(
                operator_id=state.operator_id,
                station_id=station_id,
                antenna_id=antenna_id,
                equipment_id=equipment.id if equipment else None,
                repeater_id=repeater_id,
                repeater_call=repeater_call,
                call=callsign_module.normalize(call_raw),
                base_call=callsign_module.base_call(call_raw),
                name=str(fields.get("name", "")),
                qso_utc=timestamp,
                band=band,
                freq_hz=int(freq_hz) if freq_hz else None,
                freq_tx_hz=freq_tx_hz,
                mode=str(fields.get("mode") or state.mode or ""),
                rst_sent=str(fields.get("rst_sent", "")),
                rst_rcvd=str(fields.get("rst_rcvd", "")),
                qth=str(fields.get("qth", "")),
                gridsquare=str(fields.get("gridsquare", "")),
                country=str(fields.get("country") or callsign_module.country_for(call_raw)),
                comment=str(fields.get("comment", "")),
                power_w=fields.get("power_w", state.power_w),
                entry_mode=entry_mode,
                digital_data=merged_digital,
                extra=dict(fields.get("extra", {}) or {}),
            )
            session.add(qso)
            session.flush()
            loaded = session.scalars(
                select(Qso)
                .options(
                    joinedload(Qso.operator),
                    joinedload(Qso.station),
                    joinedload(Qso.antenna),
                    joinedload(Qso.repeater),
                    *_EQUIPMENT_LOAD,
                )
                .where(Qso.id == qso.id)
            ).one()
            row = _to_rows(session, [loaded])[0]

        if state.add_to_book:
            _remember_in_address_book(row)
        return row

    @staticmethod
    def recent(limit: int = 200, operator_id: int | None = None) -> list[QsoRow]:
        """Most recent contacts, newest last so the panel reads top to bottom."""
        with session_scope() as session:
            stmt = (
                select(Qso)
                .options(
                    joinedload(Qso.operator),
                    joinedload(Qso.station),
                    joinedload(Qso.antenna),
                    joinedload(Qso.repeater),
                    *_EQUIPMENT_LOAD,
                )
                .order_by(Qso.qso_utc.desc(), Qso.id.desc())
                .limit(limit)
            )
            if operator_id is not None:
                stmt = stmt.where(Qso.operator_id == operator_id)
            rows = list(session.scalars(stmt))
            return _to_rows(session, list(reversed(rows)))

    @staticmethod
    def search(
        text: str = "",
        *,
        band: str | None = None,
        mode: str | None = None,
        operator_id: int | None = None,
        limit: int = 500,
    ) -> list[QsoRow]:
        """Free text search over callsign, name, QTH, country and comment."""
        with session_scope() as session:
            stmt = (
                select(Qso)
                .options(
                    joinedload(Qso.operator),
                    joinedload(Qso.station),
                    joinedload(Qso.antenna),
                    joinedload(Qso.repeater),
                    *_EQUIPMENT_LOAD,
                )
                .order_by(Qso.qso_utc.desc(), Qso.id.desc())
                .limit(limit)
            )
            needle = text.strip()
            if needle:
                pattern = f"%{needle}%"
                stmt = stmt.where(
                    or_(
                        Qso.call.ilike(pattern),
                        Qso.name.ilike(pattern),
                        Qso.qth.ilike(pattern),
                        Qso.country.ilike(pattern),
                        Qso.comment.ilike(pattern),
                    )
                )
            if band:
                stmt = stmt.where(Qso.band == band)
            if mode:
                stmt = stmt.where(Qso.mode == mode)
            if operator_id is not None:
                stmt = stmt.where(Qso.operator_id == operator_id)
            return _to_rows(session, list(session.scalars(stmt)))

    @staticmethod
    def card(qso_id: int) -> QsoCard | None:
        """Everything about a QSO: the station worked, ours, and the setup."""
        with session_scope() as session:
            qso = session.scalars(
                select(Qso)
                .options(
                    joinedload(Qso.operator),
                    joinedload(Qso.station).selectinload(Station.types),
                    joinedload(Qso.antenna),
                    joinedload(Qso.repeater),
                    *_EQUIPMENT_LOAD,
                    selectinload(Qso.equipment).selectinload(Equipment.supplies),
                )
                .where(Qso.id == qso_id)
            ).first()
            if qso is None:
                return None
            row = _to_rows(session, [qso])[0]
            contact = session.scalars(
                select(Contact).where(Contact.base_call == qso.base_call).limit(1)
            ).first() if qso.base_call else None
            setup = qso.equipment
            operator = qso.operator
            return QsoCard(
                row=row,
                contact=_contact_to_row(contact) if contact else None,
                operator_name=operator.name if operator else "",
                operator_gridsquare=operator.gridsquare if operator else "",
                operator_qth=operator.qth if operator else "",
                power_w=qso.power_w,
                station=_radio_part(qso.station) if qso.station else None,
                antenna=_antenna_part(qso.antenna) if qso.antenna else None,
                repeater=_repeater_summary(qso.repeater) if qso.repeater else "",
                setup_name=setup.name if setup else "",
                setup_notes=setup.notes if setup else "",
                radios=tuple(_radio_part(s) for s in setup.stations) if setup else (),
                antennas=tuple(_antenna_part(a) for a in setup.antennas) if setup else (),
                supplies=tuple(_supply_part(s) for s in setup.supplies) if setup else (),
            )

    @staticmethod
    def get(qso_id: int) -> QsoRow | None:
        with session_scope() as session:
            qso = session.scalars(
                select(Qso)
                .options(
                    joinedload(Qso.operator),
                    joinedload(Qso.station),
                    joinedload(Qso.antenna),
                    joinedload(Qso.repeater),
                    *_EQUIPMENT_LOAD,
                )
                .where(Qso.id == qso_id)
            ).first()
            return _to_rows(session, [qso])[0] if qso else None

    @staticmethod
    def editable_fields(qso_id: int) -> frozenset[str]:
        """Which fields the edit screen may offer for this QSO."""
        with session_scope() as session:
            qso = session.get(Qso, qso_id)
            if qso is None:
                raise ServiceError(_("QSO not found."))
            return MANUAL_EDITABLE_FIELDS if qso.is_manual else AUTO_EDITABLE_FIELDS

    @staticmethod
    def update(qso_id: int, changes: dict[str, Any]) -> QsoRow:
        """Apply changes, enforcing the automatic/manual edit rules.

        A QSO logged with the automatic clock is evidence of when the contact
        happened, so its timestamp cannot be changed; everything else can. A
        manually dated one was typed from paper and stays fully editable.
        """
        with session_scope() as session:
            qso = session.get(Qso, qso_id)
            if qso is None:
                raise ServiceError(_("QSO not found."))

            allowed = MANUAL_EDITABLE_FIELDS if qso.is_manual else AUTO_EDITABLE_FIELDS
            rejected = sorted(set(changes) - allowed)
            if rejected:
                raise ServiceError(
                    _(
                        "This QSO is automatic: its date and time cannot be changed. "
                        "Rejected fields: {fields}."
                    ).format(fields=", ".join(rejected))
                )

            equipment = None
            if changes.get("equipment_id") is not None:
                equipment = session.scalars(
                    select(Equipment)
                    .options(*_EQUIPMENT_PARTS)
                    .where(Equipment.id == changes["equipment_id"])
                ).first()
                if equipment is None:
                    raise ServiceError(_("Equipment set not found."))

            for key, value in changes.items():
                setattr(qso, key, value)

            if "call" in changes:
                qso.call = callsign_module.normalize(str(changes["call"]))
                qso.base_call = callsign_module.base_call(qso.call)
                detected = callsign_module.country_for(qso.call)
                if detected:
                    qso.country = detected
            if "freq_hz" in changes and changes["freq_hz"]:
                band = bands.from_frequency(int(changes["freq_hz"]))
                if band:
                    qso.band = band.name
            if equipment is not None:
                # The set says which radio and antenna, as far as it can: the
                # ones that suit the QSO, so MY_RIG and MY_ANTENNA follow.
                station, antenna = equipment.parts_for(qso.freq_hz, qso.band)
                qso.station_id = station.id if station else None
                qso.antenna_id = antenna.id if antenna else None

            session.flush()
            # The relationships still point where the old ids did.
            session.expire(qso)
            loaded = session.scalars(
                select(Qso)
                .options(
                    joinedload(Qso.operator),
                    joinedload(Qso.station),
                    joinedload(Qso.antenna),
                    joinedload(Qso.repeater),
                    *_EQUIPMENT_LOAD,
                )
                .where(Qso.id == qso.id)
            ).one()
            return _to_rows(session, [loaded])[0]

    @staticmethod
    def delete(qso_id: int) -> None:
        with session_scope() as session:
            qso = session.get(Qso, qso_id)
            if qso is None:
                raise ServiceError(_("QSO not found."))
            session.delete(qso)

    @staticmethod
    def find_duplicates(
        call: str, band: str, mode: str, *, within_minutes: int = 0
    ) -> list[QsoRow]:
        """Previous contacts with the same station on the same band and mode.

        ``within_minutes`` restricts the search to a recent window, which is
        the usual contest rule; zero means the whole log.
        """
        base = callsign_module.base_call(call)
        with session_scope() as session:
            stmt = (
                select(Qso)
                .options(
                    joinedload(Qso.operator),
                    joinedload(Qso.station),
                    joinedload(Qso.antenna),
                    joinedload(Qso.repeater),
                    *_EQUIPMENT_LOAD,
                )
                .where(Qso.base_call == base)
                .order_by(Qso.qso_utc.desc())
            )
            if band:
                stmt = stmt.where(Qso.band == band)
            if mode:
                stmt = stmt.where(Qso.mode == mode)
            if within_minutes > 0:
                since = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(
                    minutes=within_minutes
                )
                stmt = stmt.where(Qso.qso_utc >= since)
            return _to_rows(session, list(session.scalars(stmt)))

    @staticmethod
    def stats(operator_id: int | None = None) -> LogStats:
        """Aggregate counters for the footer and the metrics exporter."""
        with session_scope() as session:
            def scoped(stmt):  # type: ignore[no-untyped-def]
                return stmt.where(Qso.operator_id == operator_id) if operator_id else stmt

            total = session.scalar(scoped(select(func.count(Qso.id)))) or 0
            start_of_day = dt.datetime.now(dt.timezone.utc).replace(
                tzinfo=None, hour=0, minute=0, second=0, microsecond=0
            )
            today = (
                session.scalar(
                    scoped(select(func.count(Qso.id))).where(Qso.qso_utc >= start_of_day)
                )
                or 0
            )
            unique_calls = (
                session.scalar(scoped(select(func.count(func.distinct(Qso.base_call))))) or 0
            )
            countries = (
                session.scalar(
                    scoped(select(func.count(func.distinct(Qso.country)))).where(Qso.country != "")
                )
                or 0
            )
            by_band = dict(
                session.execute(
                    scoped(select(Qso.band, func.count(Qso.id))).group_by(Qso.band)
                ).all()
            )
            by_mode = dict(
                session.execute(
                    scoped(select(Qso.mode, func.count(Qso.id))).group_by(Qso.mode)
                ).all()
            )
            return LogStats(
                total=total,
                today=today,
                unique_calls=unique_calls,
                countries=countries,
                by_band={k: v for k, v in by_band.items() if k},
                by_mode={k: v for k, v in by_mode.items() if k},
            )


# --------------------------------------------------------------------------- #
# Address book
# --------------------------------------------------------------------------- #

def _contact_to_row(contact: Contact, qso_count: int = 0) -> ContactRow:
    """Convert an address book entry into the UI/API shape."""
    return ContactRow(
        id=contact.id,
        callsign=contact.callsign,
        first_name=contact.first_name,
        last_name=contact.last_name,
        dmr_id=contact.dmr_id,
        city=contact.city,
        state=contact.state,
        country=contact.country,
        gridsquare=contact.gridsquare,
        email=contact.email,
        notes=contact.notes,
        source=contact.source,
        is_favorite=contact.is_favorite,
        qso_count=qso_count,
    )


def _contact_filter(text: str) -> Any:
    """The search condition for the address book, None for no search."""
    needle = text.strip()
    if not needle:
        return None
    pattern = f"%{needle}%"
    conditions = [
        Contact.callsign.ilike(pattern),
        Contact.first_name.ilike(pattern),
        Contact.last_name.ilike(pattern),
        Contact.city.ilike(pattern),
        Contact.state.ilike(pattern),
        Contact.country.ilike(pattern),
    ]
    if needle.isdigit():
        conditions.append(Contact.dmr_id == int(needle))
    return or_(*conditions)


class ContactService:
    """The address book.

    Entries usually arrive in bulk from a DMR user list, so imports are
    written in batches and lookups go through indexed columns: a list of the
    whole of Spain is tens of thousands of rows and the entry line queries it
    on every keystroke.
    """

    #: Rows per INSERT batch during an import.
    BATCH_SIZE = 1_000

    @staticmethod
    def lookup(call: str) -> ContactRow | None:
        """Find a station by callsign, ignoring portable prefixes.

        This runs while the operator types, so it must stay cheap.
        """
        base = callsign_module.base_call(call)
        if len(base) < 3:
            return None
        with session_scope() as session:
            contact = session.scalars(
                select(Contact).where(Contact.base_call == base).limit(1)
            ).first()
            return _contact_to_row(contact) if contact else None

    @staticmethod
    def lookup_by_dmr_id(dmr_id: int) -> ContactRow | None:
        with session_scope() as session:
            contact = session.scalars(
                select(Contact).where(Contact.dmr_id == dmr_id)
            ).first()
            return _contact_to_row(contact) if contact else None

    @staticmethod
    def get(contact_id: int) -> ContactRow | None:
        with session_scope() as session:
            contact = session.get(Contact, contact_id)
            return _contact_to_row(contact) if contact else None

    @staticmethod
    def search(text: str = "", *, limit: int = 500, with_counts: bool = True) -> list[ContactRow]:
        """Search by callsign, name, city, province, country or DMR ID."""
        with session_scope() as session:
            stmt = select(Contact).order_by(
                Contact.is_favorite.desc(), Contact.callsign
            ).limit(limit)
            condition = _contact_filter(text)
            if condition is not None:
                stmt = stmt.where(condition)
            contacts = list(session.scalars(stmt))

            counts: dict[str, int] = {}
            if with_counts and contacts:
                # One grouped query rather than one per row.
                calls = {contact.base_call for contact in contacts if contact.base_call}
                if calls:
                    counts = dict(
                        session.execute(
                            select(Qso.base_call, func.count(Qso.id))
                            .where(Qso.base_call.in_(calls))
                            .group_by(Qso.base_call)
                        ).all()
                    )
            return [
                _contact_to_row(contact, counts.get(contact.base_call, 0))
                for contact in contacts
            ]

    @staticmethod
    def count(text: str = "") -> int:
        """How many entries there are, or match ``text`` as search() does."""
        with session_scope() as session:
            stmt = select(func.count(Contact.id))
            condition = _contact_filter(text)
            if condition is not None:
                stmt = stmt.where(condition)
            return session.scalar(stmt) or 0

    @staticmethod
    def create(
        call: str,
        *,
        first_name: str = "",
        last_name: str = "",
        dmr_id: int | None = None,
        city: str = "",
        state: str = "",
        country: str = "",
        gridsquare: str = "",
        email: str = "",
        notes: str = "",
        source: str = "manual",
    ) -> ContactRow:
        normalized = callsign_module.normalize(call)
        if not normalized:
            raise ServiceError(_("The callsign cannot be empty."))
        with session_scope() as session:
            base = callsign_module.base_call(normalized)
            if session.scalars(select(Contact).where(Contact.base_call == base)).first():
                raise ServiceError(
                    _("{call} is already in the address book.").format(call=base)
                )
            if dmr_id is not None:
                clash = session.scalars(
                    select(Contact).where(Contact.dmr_id == dmr_id)
                ).first()
                if clash is not None:
                    raise ServiceError(
                        _("DMR ID {dmr_id} already belongs to {call}.").format(
                            dmr_id=dmr_id, call=clash.callsign
                        )
                    )
            contact = Contact(
                callsign=normalized,
                base_call=base,
                dmr_id=dmr_id,
                first_name=first_name.strip(),
                last_name=last_name.strip(),
                city=city.strip(),
                state=state.strip(),
                country=country.strip() or callsign_module.country_for(normalized),
                gridsquare=gridsquare.strip().upper(),
                email=email.strip(),
                notes=notes.strip(),
                source=source,
            )
            session.add(contact)
            session.flush()
            return _contact_to_row(contact)

    @staticmethod
    def update(contact_id: int, **changes: Any) -> ContactRow:
        with session_scope() as session:
            contact = session.get(Contact, contact_id)
            if contact is None:
                raise ServiceError(_("Contact not found in the address book."))
            if "callsign" in changes:
                normalized = callsign_module.normalize(str(changes.pop("callsign")))
                if not normalized:
                    raise ServiceError(_("The callsign cannot be empty."))
                base = callsign_module.base_call(normalized)
                clash = session.scalars(
                    select(Contact).where(Contact.base_call == base, Contact.id != contact_id)
                ).first()
                if clash is not None:
                    raise ServiceError(
                        _("{call} is already in the address book.").format(call=base)
                    )
                contact.callsign = normalized
                contact.base_call = base
            if changes.get("dmr_id") is not None:
                clash = session.scalars(
                    select(Contact).where(
                        Contact.dmr_id == changes["dmr_id"], Contact.id != contact_id
                    )
                ).first()
                if clash is not None:
                    raise ServiceError(
                        _("DMR ID {dmr_id} already belongs to {call}.").format(
                            dmr_id=changes["dmr_id"], call=clash.callsign
                        )
                    )
            if "gridsquare" in changes:
                changes["gridsquare"] = str(changes["gridsquare"]).strip().upper()
            for key, value in changes.items():
                if hasattr(contact, key):
                    setattr(contact, key, value)
            session.flush()
            return _contact_to_row(contact)

    @staticmethod
    def delete(contact_id: int) -> None:
        with session_scope() as session:
            contact = session.get(Contact, contact_id)
            if contact is None:
                raise ServiceError(_("Contact not found in the address book."))
            session.delete(contact)

    @staticmethod
    def clear(source: str | None = None) -> int:
        """Empty the address book, or just the entries from one source.

        Returns:
            How many entries were removed.
        """
        with session_scope() as session:
            stmt = session.query(Contact)
            if source:
                stmt = stmt.filter(Contact.source == source)
            removed = stmt.delete(synchronize_session=False)
            return int(removed or 0)

    @staticmethod
    def sources() -> dict[str, int]:
        """How many entries came from each source."""
        with session_scope() as session:
            return dict(
                session.execute(
                    select(Contact.source, func.count(Contact.id)).group_by(Contact.source)
                ).all()
            )
