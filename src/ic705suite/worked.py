"""What has been worked and confirmed, from the logbook: the calls (on which bands), the DXCC
entities and the grid squares, each worked or confirmed (LoTW: LOTW_QSL_RCVD). The core
keeps one, read again every minute and after each QSO, to mark what FT8 hears as
GridTracker and JTAlert do: a new entity, a new grid, a station worked before.

    status(call, band, grid) -> {"b4": worked on this band, "b4_any": on any,
                                 "dxcc": new | new_band | worked | confirmed,
                                 "dxcc_count": QSOs with its entity,
                                 "grid_state": new | new_band | worked | confirmed}
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from ic705suite import geo

log = logging.getLogger(__name__)

CONFIRMED = "LOTW_QSL_RCVD"


@dataclass
class Seen:
    bands: set[str] = field(default_factory=set)
    confirmed: set[str] = field(default_factory=set)      # bands confirmed on
    count: int = 0                                        # QSOs

    def state(self, band: str) -> str:
        """new (never), new_band (not on this band), confirmed (on some band), worked."""
        if not self.bands:
            return "new"
        if band and band not in self.bands:
            return "new_band"
        return "confirmed" if self.confirmed else "worked"


def _base(call: str) -> str:
    from ic705suite.apps.log.core import callsign

    return callsign.base_call(call or "")


def entity_name(call: str, fallback: str = "") -> str:
    entity = geo.entity_for(call)
    return entity.name if entity is not None else fallback


class WorkedIndex:
    def __init__(self) -> None:
        self.calls: dict[str, Seen] = defaultdict(Seen)
        self.entities: dict[str, Seen] = defaultdict(Seen)
        self.grids: dict[str, Seen] = defaultdict(Seen)
        self.count = 0

    def add(self, call: str, band: str, grid: str, entity: str, confirmed: bool) -> None:
        band = (band or "").lower()
        for table, key in ((self.calls, _base(call)), (self.entities, entity),
                           (self.grids, (grid or "")[:4].upper())):
            if not key:
                continue
            seen = table[key]
            seen.bands.add(band)
            seen.count += 1
            if confirmed:
                seen.confirmed.add(band)
        self.count += 1

    def status(self, call: str, band: str, grid: str = "") -> dict:
        band = (band or "").lower()
        mine = self.calls.get(_base(call))
        entity = entity_name(call)
        out = {"b4": bool(mine and band in mine.bands), "b4_any": bool(mine and mine.bands),
               "dxcc": self.entities.get(entity, Seen()).state(band) if entity else "",
               "dxcc_count": self.entities[entity].count if entity in self.entities else 0}
        if grid and geo.is_locator(grid):
            out["grid_state"] = self.grids.get(grid[:4].upper(), Seen()).state(band)
        return out

    def summary(self) -> dict:
        """For the map: each grid and entity worked, and whether it is confirmed."""
        def states(table: dict[str, Seen]) -> dict[str, str]:
            return {k: ("confirmed" if v.confirmed else "worked") for k, v in table.items()
                    if v.bands}
        return {"grids": states(self.grids), "entities": states(self.entities),
                "qsos": self.count}


def from_logbook(own_call: str) -> WorkedIndex:
    """The index of the logbook hamrlog opens (the shared one, in the core): the QSOs of
    the operator `own_call`."""
    from sqlalchemy import select

    from ic705suite.apps.log.db.models import Operator, Qso
    from ic705suite.apps.log.db.session import session_scope

    index = WorkedIndex()
    with session_scope() as session:
        rows = session.execute(
            select(Qso.call, Qso.band, Qso.gridsquare, Qso.country, Qso.extra,
                   Operator.callsign).join(Operator, Qso.operator_id == Operator.id)).all()
    for call, band, grid, country, extra, operator in rows:
        if own_call and _base(operator) != _base(own_call):
            continue
        index.add(call, band, grid, entity_name(call, country or ""),
                  (extra or {}).get(CONFIRMED) == "Y")
    return index
