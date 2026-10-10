"""The profiles view (F4): what every new QSO inherits.

A profile holds the operator, the setup, the frequency, the mode, the power,
the repeater and the digital values. Enter on a profile makes it the active
one; ten of them take the keys Ctrl+0 to Ctrl+9, which activate them from
any view, and the default one is activated on start.

Like the other list views, the list is never focused: the entry line keeps
the keyboard and its boxes fill in each profile.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.suggester import Suggester

from ..core import bands, modes
from ..core.services import (
    EquipmentService,
    OperatorService,
    ProfileService,
    RepeaterService,
    ServiceError,
)
from ..i18n import N_, _
from .inventory import Item, Kind, ListSuggester

#: Marks of the INFO column.
ACTIVE_MARK = "▶"
DEFAULT_MARK = "★"


def _alias(name: str) -> str:
    """«DG-ID», «dg id» and «dgid» are the same name."""
    return "".join(name.lower().replace("-", "").replace("_", "").split())


def digital_key(name: str) -> str | None:
    """The digital value a name typed in the DIGITAL box stands for.

    The internal key, the short name the status line uses (in English or in
    the operator's language) or its prefix: «TG», «talkgroup», «Red».
    """
    aliases = {_alias(key): key for key in modes.SHORT_FIELD_NAMES}
    for key, short in modes.SHORT_FIELD_NAMES.items():
        aliases.setdefault(_alias(short), key)
        aliases.setdefault(_alias(_(short)), key)
    for key, prefix in modes.STATUS_PREFIXES.items():
        aliases.setdefault(_alias(prefix), key)
    return aliases.get(_alias(name))


def format_digital(data: dict[str, Any]) -> str:
    """«TG=214, CC=1»: what the DIGITAL box shows for stored values."""
    return ", ".join(
        f"{modes.STATUS_PREFIXES.get(key) or _(modes.SHORT_FIELD_NAMES.get(key, key))}={value}"
        for key, value in data.items()
        if value
    )


def parse_digital(text: str, mode_name: str) -> dict[str, str]:
    """Read «TG=214, CC=1» into digital values the mode accepts.

    Raises:
        ServiceError: a pair without «=», or a value the mode does not use.
    """
    values: dict[str, str] = {}
    allowed = {key for key, _label in modes.digital_fields(mode_name)}
    for raw in text.replace(";", ",").split(","):
        pair = raw.strip()
        if not pair:
            continue
        name, sep, value = pair.partition("=")
        key = digital_key(name)
        if not sep or not value.strip():
            raise ServiceError(
                _("Write the digital values as NAME=VALUE: «{text}»").format(text=pair)
            )
        if key is None or key not in allowed:
            mode = modes.get(mode_name)
            raise ServiceError(
                _("{mode} does not use «{name}»; it uses: {fields}").format(
                    mode=mode_name or "-",
                    name=name.strip(),
                    fields=(modes.short_fields(mode) if mode else "") or "—",
                )
            )
        values[key] = value.strip()
    return values


class ProfileKind(Kind):
    key = "profiles"
    title = N_("Profiles")
    insert_label = N_("<New profile>")
    guide = (
        N_("NEW PROFILE   what every QSO inherits while it is active"),
        N_("CTRL: 0-9, activated with Ctrl+digit · OPERATOR: callsign · SETUP from F2"),
        N_("REPEATER: callsign · PWR: watts · DIGITAL: TG=214, CC=1"),
    )
    fields = ("slot", "name", "operator", "equipment")
    second_row = ("freq_hz", "mode", "power_w", "repeater", "digital")
    columns = (
        ("INFO", 4),
        ("CTRL", 4),
        (N_("NAME"), 16),
        (N_("FREQ"), 12),
        (N_("MODE"), 6),
        (N_("SETUP"), 14),
        (N_("OPERATOR"), 9),
        (N_("REPEATER"), 9),
        (N_("PWR"), 5),
        (N_("DIGITAL"), None),
    )
    right_aligned = frozenset({"PWR"})

    def __init__(self) -> None:
        #: The profile the session has active, marked in the list.
        self.active_id: int | None = None

    def items(self, query: str = "") -> list[Item]:
        rows = []
        for profile in ProfileService.list_all():
            active = profile.id == self.active_id
            info = (ACTIVE_MARK if active else " ") + (
                DEFAULT_MARK if profile.is_default else ""
            )
            operator = profile.operator.callsign if profile.operator else ""
            setup = profile.equipment.name if profile.equipment else ""
            freq = bands.format_frequency(profile.freq_hz) if profile.freq_hz else ""
            repeater = profile.repeater.callsign if profile.repeater else ""
            power = f"{profile.power_w} W" if profile.power_w else ""
            digital = modes.status_summary(dict(profile.digital_data or {}))

            states = []
            if active:
                states.append(_("active"))
            if profile.is_default:
                states.append(_("activated on start"))
            if profile.slot is not None:
                states.append(_("Ctrl+{key} activates it").format(key=profile.slot))
            notes = []
            if profile.equipment is not None and not profile.equipment.fits(
                profile.freq_hz, profile.band
            ):
                notes.append(
                    _("⚠ the setup «{name}» does not cover this frequency").format(name=setup)
                )
            notes.append(_("Enter activates · * default · E edit · D delete"))
            rows.append(
                Item(
                    profile.id,
                    profile.name,
                    (
                        info,
                        "" if profile.slot is None else str(profile.slot),
                        profile.name,
                        freq,
                        profile.mode,
                        setup,
                        operator,
                        repeater,
                        power,
                        digital,
                    ),
                    (
                        " · ".join([profile.name, *states]),
                        " · ".join(
                            part
                            for part in (
                                operator or _("current operator"),
                                setup or _("no setup"),
                                profile.band,
                                freq,
                                _("via {call}").format(call=repeater) if repeater else "",
                                profile.mode,
                                digital,
                                power,
                            )
                            if part
                        ),
                        " · ".join(notes),
                    ),
                )
            )
        return rows

    def values(self, item_id: int) -> dict[str, str]:
        profile = ProfileService.get(item_id)
        if profile is None:
            return {}
        return {
            "slot": "" if profile.slot is None else str(profile.slot),
            "name": profile.name,
            "operator": profile.operator.callsign if profile.operator else "",
            "equipment": profile.equipment.name if profile.equipment else "",
            "freq_hz": bands.format_frequency(profile.freq_hz) if profile.freq_hz else "",
            "mode": profile.mode,
            "power_w": str(profile.power_w or ""),
            "repeater": RepeaterService.label(profile.repeater) if profile.repeater else "",
            "digital": format_digital(dict(profile.digital_data or {})),
        }

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        slot_text = values.get("slot", "").strip()
        if slot_text and not (slot_text.isdigit() and len(slot_text) == 1):
            raise ServiceError(_("The key of a profile goes from 0 to 9."))

        operator_id = None
        call = values.get("operator", "").strip()
        if call:
            operator = OperatorService.get_by_callsign(call) or OperatorService.create(call)
            operator_id = operator.id

        equipment_id = None
        setup_text = values.get("equipment", "").strip()
        if setup_text:
            setup = next(
                (e for e in EquipmentService.list_all() if e.name.lower() == setup_text.lower()),
                None,
            )
            if setup is None:
                raise ServiceError(_("There is no setup «{name}».").format(name=setup_text))
            equipment_id = setup.id

        repeater = None
        repeater_text = values.get("repeater", "").strip()
        if repeater_text:
            repeater = RepeaterService.resolve(repeater_text)

        freq_hz = None
        freq_text = values.get("freq_hz", "").strip()
        if freq_text:
            freq_hz = bands.parse_frequency(freq_text)
            if freq_hz is None:
                raise ServiceError(
                    _("Frequency not recognised: «{text}»").format(text=freq_text)
                )
        elif repeater is not None:
            # Working through a repeater means listening on its output.
            freq_hz = repeater.output_hz

        mode_name = ""
        mode_text = values.get("mode", "").strip()
        if mode_text:
            mode = modes.get(mode_text)
            if mode is None:
                raise ServiceError(_("Unknown mode: «{mode}»").format(mode=mode_text))
            mode_name = mode.name
        elif repeater is not None:
            mode_name = repeater.mode

        digital = parse_digital(values.get("digital", ""), mode_name)
        if not digital and repeater is not None and repeater.mode == mode_name:
            digital = dict(repeater.digital_data or {})

        power_text = values.get("power_w", "").strip().upper().removesuffix("W").strip()
        if power_text and not power_text.isdigit():
            raise ServiceError(
                _("The power is a whole number of watts: «{text}»").format(text=power_text)
            )

        return ProfileService.save(
            item_id,
            name=values.get("name", ""),
            slot=int(slot_text) if slot_text else None,
            operator_id=operator_id,
            equipment_id=equipment_id,
            repeater_id=repeater.id if repeater else None,
            freq_hz=freq_hz,
            mode=mode_name,
            power_w=int(power_text) if power_text else None,
            digital_data=digital,
        ).name

    def delete(self, item_id: int) -> None:
        ProfileService.delete(item_id)

    def suggesters(self) -> dict[str, Suggester]:
        return {
            "operator": ListSuggester([o.callsign for o in OperatorService.list_all()]),
            "equipment": ListSuggester([e.name for e in EquipmentService.list_all()]),
            "mode": ListSuggester([mode.name for mode in modes.MODES]),
            "repeater": ListSuggester(RepeaterService.labels()),
        }


def legend() -> Text:
    """The row over the list: what its marks mean."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{ACTIVE_MARK} ", style="bold")
    text.append(_("active"), style="dim")
    text.append(f"   {DEFAULT_MARK} ", style="bold")
    text.append(_("activated on start"), style="dim")
    text.append("   Ctrl+0…9 ", style="bold")
    text.append(_("activate the main ones"), style="dim")
    return text


#: The profiles view has a single list.
PROFILE_KINDS: tuple[Kind, ...] = (ProfileKind(),)
