"""What the inventory view (F2) lists in each of its tabs.

Each tab is one kind of item: equipment sets, radios, antennas and power
supplies. A kind says which columns the list shows, which boxes the entry
line offers, and how the typed values become a service call. The view and the
entry line are the same widgets the log uses; only this description changes.

The texts of a kind (title, insert label, guide, column headings) are class
attributes, built at import time, so they hold the English text marked with
``N_()`` and are translated with ``_()`` where they are shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.suggester import Suggester

from ..core import units
from ..core.services import (
    AntennaService,
    EquipmentService,
    PowerSupplyService,
    ServiceError,
    StationService,
    StationTypeService,
)
from ..i18n import N_, _


@dataclass(frozen=True, slots=True)
class Item:
    """One row of a tab: the id, its cells and what the detail pane says."""

    id: int
    name: str
    cells: tuple[str, ...]
    detail: tuple[str, ...]
    #: Manufacturer, what the brand filter matches; empty for sets.
    brand: str = ""
    #: From the catalog: shown, usable in sets, never changed or deleted.
    locked: bool = False


class ListSuggester(Suggester):
    """Completes the last name of a comma separated list.

    Matching ignores case; the completion keeps what was typed and adds the
    rest of the name, which is what Textual needs to show it inline.
    """

    def __init__(self, names: list[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self.names = names

    async def get_suggestion(self, value: str) -> str | None:
        token = value.rpartition(",")[2].lstrip()
        if not token:
            return None
        needle = token.lower()
        for name in self.names:
            if name.lower().startswith(needle) and len(name) > len(token):
                return value + name[len(token) :]
        return None


class Kind:
    """Base for one list of a list view: an inventory tab, the address book."""

    key: str = ""
    title: str = ""
    #: Label of the insert row, e.g. «<New radio>».
    insert_label: str = ""
    #: Shown by the detail pane on the insert row: what each box expects.
    guide: tuple[str, ...] = ()
    #: Boxes of the entry line, in order.
    fields: tuple[str, ...] = ()
    #: (heading, width); None takes the remaining width.
    columns: tuple[tuple[str, int | None], ...] = ()
    #: False for lists the operator cannot add to: no insert row.
    can_add: bool = True
    #: False for lists whose rows cannot be deleted.
    can_delete: bool = True
    #: False for read-only lists: the cursor moves over them while the entry
    #: line keeps the keyboard as a filter box, instead of the action bar.
    browses: bool = True
    #: Headings of the columns aligned to the right, numbers mostly.
    right_aligned: frozenset[str] = frozenset()
    #: Boxes of a second row, always shown, for lists with many fields.
    second_row: tuple[str, ...] = ()
    #: True when the list is searched with /search rather than shown whole.
    searchable: bool = False
    #: Column of the insert row's label; the widest fixed one when None.
    insert_column: int | None = None
    #: What the top row of a searchable list says when all of it is shown.
    count_text: str = N_("{total} items")

    def items(self, query: str = "") -> list[Item]:
        """The rows to show; ``query`` is the /search text of searchable lists."""
        raise NotImplementedError

    def total(self, query: str = "") -> int:
        """How many items match ``query``, beyond the ones shown."""
        return len(self.items(query))

    def values(self, item_id: int) -> dict[str, str]:
        """What the entry line shows when editing an item."""
        raise NotImplementedError

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        """Create (``item_id`` None) or update an item; returns its name.

        Raises:
            ServiceError: with a message for the operator.
        """
        raise NotImplementedError

    def delete(self, item_id: int) -> None:
        raise NotImplementedError

    def suggesters(self) -> dict[str, Suggester]:
        return {}

    @property
    def has_brands(self) -> bool:
        return "brand" in self.fields


CATALOG_NOTE = N_("from the catalog: only used in setups, never changed or deleted")


def _brands(objects: list[Any]) -> ListSuggester:
    return ListSuggester(sorted({obj.brand for obj in objects if obj.brand}, key=str.lower))


# ------------------------------------------------------------------ helpers --
def _number(text: str, what: str) -> float | None:
    clean = text.strip().replace(",", ".")
    if not clean:
        return None
    try:
        return float(clean)
    except ValueError:
        raise ServiceError(
            _("{what} is not a number: «{text}»").format(what=what, text=text)
        ) from None


def _format_number(value: float | None, unit: str) -> str:
    if value is None:
        return ""
    # The same decimal separator as the frequencies, which the operator chose.
    shown = f"{value:g}".replace(".", units.active().decimal)
    return f"{shown} {unit}" if unit else shown


def _names(objects: list[Any]) -> str:
    return ", ".join(obj.name for obj in objects)


def _label(obj: Any) -> str:
    """«E0001 · Icom IC-705»: the code the operator reads, then the name."""
    return f"{obj.code} · {obj.name}" if getattr(obj, "code", None) else obj.name


def _names_and_codes(objects: list[Any]) -> list[str]:
    return [obj.name for obj in objects] + [obj.code for obj in objects if obj.code]


def _resolve(text: str, known: list[Any], missing: str) -> list[int]:
    """Ids for a comma separated list of names or codes, case insensitive.

    ``missing`` is the translated error for an unknown name, with a
    ``{name}`` placeholder.
    """
    by_name = {obj.name.lower(): obj.id for obj in known}
    by_name.update({obj.code.lower(): obj.id for obj in known if getattr(obj, "code", None)})
    ids: list[int] = []
    for raw in text.replace(";", ",").split(","):
        name = raw.strip()
        if not name:
            continue
        found = by_name.get(name.lower())
        if found is None:
            raise ServiceError(missing.format(name=name))
        if found not in ids:
            ids.append(found)
    return ids


# -------------------------------------------------------------------- kinds --
class EquipmentKind(Kind):
    key = "equipment"
    title = N_("Setups")
    insert_label = N_("<New setup>")
    guide = (
        N_("NEW SETUP   a set of at least one radio, with its antennas and supplies"),
        N_("RADIOS, ANTENNAS, SUPPLIES: IDs (E0001, A0001, S0001) or names, comma separated"),
        N_("→ at the end of a box completes the suggested name"),
    )
    fields = ("name", "stations", "antennas", "supplies", "notes")
    columns = (
        (N_("NAME"), 20),
        (N_("RADIOS"), 26),
        (N_("ANTENNAS"), 26),
        (N_("SUPPLIES"), 20),
        (N_("NOTES"), None),
    )

    def items(self, query: str = "") -> list[Item]:
        return [
            Item(
                equipment.id,
                equipment.name,
                (
                    equipment.name,
                    _names(equipment.stations),
                    _names(equipment.antennas),
                    _names(equipment.supplies),
                    equipment.notes,
                ),
                (
                    f"{equipment.name}",
                    _("Radios: {names}").format(names=_names(equipment.stations) or "—"),
                    _("Antennas: {antennas} · Supplies: {supplies}").format(
                        antennas=_names(equipment.antennas) or "—",
                        supplies=_names(equipment.supplies) or "—",
                    ),
                ),
            )
            for equipment in EquipmentService.list_all()
        ]

    def values(self, item_id: int) -> dict[str, str]:
        equipment = EquipmentService.get(item_id)
        if equipment is None:
            return {}
        return {
            "name": equipment.name,
            "stations": _names(equipment.stations),
            "antennas": _names(equipment.antennas),
            "supplies": _names(equipment.supplies),
            "notes": equipment.notes,
        }

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        if not StationService.list_all():
            raise ServiceError(_("A setup has at least one radio: add one first."))
        args = dict(
            name=values.get("name", ""),
            station_ids=_resolve(
                values.get("stations", ""),
                StationService.list_all(),
                _("There is no radio «{name}»."),
            ),
            antenna_ids=_resolve(
                values.get("antennas", ""),
                AntennaService.list_all(),
                _("There is no antenna «{name}»."),
            ),
            supply_ids=_resolve(
                values.get("supplies", ""),
                PowerSupplyService.list_all(),
                _("There is no supply «{name}»."),
            ),
            notes=values.get("notes", ""),
        )
        if item_id is None:
            return EquipmentService.create(**args).name
        return EquipmentService.update(item_id, **args).name

    def delete(self, item_id: int) -> None:
        EquipmentService.delete(item_id)

    def suggesters(self) -> dict[str, Suggester]:
        return {
            "stations": ListSuggester(_names_and_codes(StationService.list_all())),
            "antennas": ListSuggester(_names_and_codes(AntennaService.list_all())),
            "supplies": ListSuggester(_names_and_codes(PowerSupplyService.list_all())),
        }


class StationKind(Kind):
    key = "stations"
    title = N_("Radios")
    insert_label = N_("<New radio>")
    guide = (
        N_("NEW RADIO   brand and model; without a name it is called «brand model»"),
        N_("PWR: watts · TYPES: ranges it covers, separated by commas (HF, VHF, UHF, CB)"),
        N_("→ at the end of a box completes the suggestion"),
    )
    fields = ("brand", "rig", "name", "power_w", "types", "notes")
    columns = (
        (N_("ID"), 6),
        (N_("BRAND"), 12),
        (N_("MODEL"), 16),
        (N_("NAME"), 24),
        (N_("POWER"), 9),
        (N_("TYPES"), 14),
        (N_("NOTES"), None),
    )

    def items(self, query: str = "") -> list[Item]:
        rows = []
        for station in StationService.list_all():
            power = f"{station.power_w} W" if station.power_w else ""
            rows.append(
                Item(
                    station.id,
                    station.name,
                    (
                        station.code or "",
                        station.brand,
                        station.rig,
                        station.name,
                        power,
                        station.type_names,
                        station.notes,
                    ),
                    (
                        _label(station) + (f" · {station.rig}" if station.rig else ""),
                        " · ".join(p for p in (power, station.type_names) if p) or "—",
                        _(CATALOG_NOTE) if station.preset else station.notes or "",
                    ),
                    brand=station.brand,
                    locked=station.preset,
                )
            )
        return rows

    def values(self, item_id: int) -> dict[str, str]:
        station = StationService.get(item_id)
        if station is None:
            return {}
        return {
            "brand": station.brand,
            "name": station.name,
            "rig": station.rig,
            "power_w": str(station.power_w or ""),
            "types": station.type_names,
            "notes": station.notes,
        }

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        power = _number(values.get("power_w", ""), _("Power"))
        brand = values.get("brand", "").strip()
        rig = values.get("rig", "").strip()
        args: dict[str, Any] = dict(
            name=values.get("name", "").strip() or f"{brand} {rig}".strip(),
            brand=brand,
            rig=rig,
            power_w=int(power) if power is not None else None,
            notes=values.get("notes", "").strip(),
        )
        type_ids = StationTypeService.resolve(values.get("types", ""))
        if item_id is None:
            return _label(StationService.create(**args, type_ids=type_ids))
        return _label(StationService.update(item_id, type_ids=type_ids, **args))

    def delete(self, item_id: int) -> None:
        StationService.delete(item_id)

    def suggesters(self) -> dict[str, Suggester]:
        return {
            "brand": _brands(StationService.list_all()),
            "types": ListSuggester([t.name for t in StationTypeService.list_all()]),
        }


class AntennaKind(Kind):
    key = "antennas"
    title = N_("Antennas")
    insert_label = N_("<New antenna>")
    guide = (
        N_("NEW ANTENNA   brand and name (with the model)"),
        N_("BANDS: amateur radio bands separated by commas (40m, 2m, 70cm...)"),
        N_("→ at the end of a box completes the suggestion"),
    )
    fields = ("brand", "name", "bands", "notes")
    columns = (
        (N_("ID"), 6),
        (N_("BRAND"), 12),
        (N_("NAME"), 26),
        (N_("BANDS"), 28),
        (N_("NOTES"), None),
    )

    def items(self, query: str = "") -> list[Item]:
        return [
            Item(
                antenna.id,
                antenna.name,
                (antenna.code or "", antenna.brand, antenna.name, antenna.band_names,
                 antenna.notes),
                (
                    _label(antenna),
                    _("Bands: {bands}").format(bands=antenna.band_names or "—"),
                    _(CATALOG_NOTE) if antenna.preset else antenna.notes or "",
                ),
                brand=antenna.brand,
                locked=antenna.preset,
            )
            for antenna in AntennaService.list_all()
        ]

    def values(self, item_id: int) -> dict[str, str]:
        antenna = AntennaService.get(item_id)
        if antenna is None:
            return {}
        return {
            "brand": antenna.brand,
            "name": antenna.name,
            "bands": antenna.band_names,
            "notes": antenna.notes,
        }

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        band_list = AntennaService.resolve_bands(values.get("bands", ""))
        name = values.get("name", "")
        notes = values.get("notes", "")
        brand = values.get("brand", "")
        if item_id is None:
            return _label(AntennaService.create(name, band_list, notes, brand=brand))
        return _label(AntennaService.update(item_id, name, band_list, notes, brand=brand))

    def delete(self, item_id: int) -> None:
        AntennaService.delete(item_id)

    def suggesters(self) -> dict[str, Suggester]:
        return {"brand": _brands(AntennaService.list_all())}


class SupplyKind(Kind):
    key = "supplies"
    title = N_("Supplies")
    insert_label = N_("<New supply>")
    guide = (
        N_("NEW SUPPLY   power supply, battery...: brand and name (with the model)"),
        N_("VOLTAGE in volts and maximum CURRENT in amperes; a decimal comma is fine"),
        N_("→ at the end of a box completes the suggestion"),
    )
    fields = ("brand", "name", "voltage_v", "current_a", "notes")
    columns = (
        (N_("ID"), 6),
        (N_("BRAND"), 12),
        (N_("NAME"), 26),
        (N_("VOLTAGE"), 9),
        (N_("CURRENT"), 10),
        (N_("NOTES"), None),
    )

    def items(self, query: str = "") -> list[Item]:
        rows = []
        for supply in PowerSupplyService.list_all():
            volts = _format_number(supply.voltage_v, "V")
            amps = _format_number(supply.current_a, "A")
            rows.append(
                Item(
                    supply.id,
                    supply.name,
                    (supply.code or "", supply.brand, supply.name, volts, amps, supply.notes),
                    (
                        _label(supply),
                        " · ".join(p for p in (volts, amps) if p) or "—",
                        _(CATALOG_NOTE) if supply.preset else supply.notes or "",
                    ),
                    brand=supply.brand,
                    locked=supply.preset,
                )
            )
        return rows

    def values(self, item_id: int) -> dict[str, str]:
        supply = PowerSupplyService.get(item_id)
        if supply is None:
            return {}
        return {
            "brand": supply.brand,
            "name": supply.name,
            "voltage_v": _format_number(supply.voltage_v, ""),
            "current_a": _format_number(supply.current_a, ""),
            "notes": supply.notes,
        }

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        args = dict(
            name=values.get("name", ""),
            brand=values.get("brand", ""),
            voltage_v=_number(values.get("voltage_v", ""), _("Voltage")),
            current_a=_number(values.get("current_a", ""), _("Current")),
            notes=values.get("notes", ""),
        )
        if item_id is None:
            return _label(PowerSupplyService.create(**args))
        return _label(PowerSupplyService.update(item_id, **args))

    def delete(self, item_id: int) -> None:
        PowerSupplyService.delete(item_id)

    def suggesters(self) -> dict[str, Suggester]:
        return {"brand": _brands(PowerSupplyService.list_all())}


#: The tabs, in order.
KINDS: tuple[Kind, ...] = (EquipmentKind(), StationKind(), AntennaKind(), SupplyKind())


def tab_bar(
    kinds: tuple[Kind, ...],
    active: int,
    brand: str = "",
    query: str = "",
    shown: int = 0,
    total: int = 0,
    *,
    show_tabs: bool = False,
) -> Text:
    """The row at the top of a list view.

    The tab names, the active one marked, when the view has several lists
    (or ``show_tabs``); then the brand filter, or the search or the filter
    and how much of it is shown.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    if len(kinds) > 1 or show_tabs:
        for index, kind in enumerate(kinds):
            if index:
                text.append("  ")
            if index == active:
                text.append(f" {_(kind.title)} ", style="bold black on rgb(120,180,255)")
            else:
                text.append(f" {_(kind.title)} ", style="bold")
        text.append("   ")
    kind = kinds[active]
    if not kind.browses:
        if shown < total:
            text.append(
                _("{shown} of {total}").format(shown=shown, total=total), style="dim"
            )
        else:
            text.append(_(kind.count_text).format(total=total), style="dim")
    if kind.has_brands:
        text.append(f"{_('BRAND')} ", style="dim")
        text.append(brand or _("all"), style="bold yellow" if brand else "dim")
    if kind.searchable:
        text.append(f"{_('SEARCH')} ", style="dim")
        text.append(query or "—", style="bold yellow" if query else "dim")
        text.append("   ")
        if shown < total:
            text.append(
                _("{shown} of {total} · narrow it with /search").format(
                    shown=shown, total=total
                ),
                style="dim",
            )
        else:
            text.append(_(kind.count_text).format(total=total), style="dim")
    return text
