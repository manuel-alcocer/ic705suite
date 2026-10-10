"""Parser for the single-line fast entry field.

During a radio session the operator should only type a callsign and, at most,
a few optional values. A line is split on a configurable separator and mapped
positionally onto a configurable field order, e.g.::

    ea7wm,victor,59,57        -> call=EA7WM name=Victor rst_sent=59 rst_rcvd=57

Any token shaped ``key=value`` is taken out of the positional sequence and
assigned by name, so ``ea7wm,victor,grid=IM76`` still works. Lines starting
with ``/`` or ``:`` are commands handled by the TUI, not QSO data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..i18n import N_, _
from . import bands, callsign, modes

#: Field order used when the active profile does not override it.
DEFAULT_FIELD_ORDER: tuple[str, ...] = (
    "call",
    "name",
    "rst_sent",
    "rst_rcvd",
    "qth",
    "comment",
)

#: Every field the fast entry line may set, with the Spanish aliases an
#: operator is likely to type in ``key=value`` form.
FIELD_ALIASES: dict[str, str] = {
    "call": "call", "indicativo": "call", "ind": "call", "dx": "call",
    "name": "name", "nombre": "name", "nom": "name",
    "rst_sent": "rst_sent", "rsts": "rst_sent", "rst_env": "rst_sent",
    "env": "rst_sent", "tx": "rst_sent",
    "rst_rcvd": "rst_rcvd", "rstr": "rst_rcvd", "rst_rec": "rst_rcvd",
    "rec": "rst_rcvd", "rx": "rst_rcvd",
    "qth": "qth", "loc": "qth",
    "gridsquare": "gridsquare", "grid": "gridsquare", "locator": "gridsquare",
    "comment": "comment", "nota": "comment", "notas": "comment", "com": "comment",
    "freq": "freq_hz", "frecuencia": "freq_hz", "qrg": "freq_hz",
    "band": "band", "banda": "band",
    "mode": "mode", "modo": "mode",
    "power": "power_w", "potencia": "power_w", "pwr": "power_w",
    "talkgroup": "talkgroup", "tg": "talkgroup",
    "reflector": "reflector", "ref": "reflector",
    "room": "room", "network": "network", "red": "network",
}

#: Fields that live in Qso.digital_data rather than in a column.
DIGITAL_KEYS: frozenset[str] = frozenset(
    {"talkgroup", "reflector", "room", "network", "color_code", "repeater", "gateway",
     "dg_id", "module"}
)

COMMAND_PREFIXES = ("/", ":")

#: Field -> name shown in the hint under the entry line. English, translated
#: with _() where shown; every one is also accepted as a ``key=`` alias.
ORDER_HINTS: dict[str, str] = {
    "call": N_("call"),
    "name": N_("name"),
    "rst_sent": N_("rst_sent"),
    "rst_rcvd": N_("rst_rcvd"),
    "qth": "qth",
    "gridsquare": "locator",
    "comment": N_("comment"),
    "band": N_("band"),
    "mode": N_("mode"),
    "freq_hz": N_("freq"),
    "power_w": N_("power"),
}

#: How a malformed callsign is treated.
#: "strict" refuses the line, "warn" logs it with a notice, "off" accepts it.
VALIDATION_MODES: tuple[str, ...] = ("strict", "warn", "off")
DEFAULT_VALIDATION = "strict"


@dataclass(slots=True)
class ParsedEntry:
    """Result of parsing one fast entry line.

    Attributes:
        fields: Column values destined for the Qso row.
        digital: Values destined for Qso.digital_data.
        warnings: Non-fatal notes shown to the operator, e.g. a doubtful call.
        error: Set when the line cannot become a QSO at all.
    """

    fields: dict[str, object] = field(default_factory=dict)
    digital: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class ParsedCommand:
    """A ``/command arg`` typed into the entry line."""

    name: str
    argument: str


def is_command(line: str) -> bool:
    return line.strip().startswith(COMMAND_PREFIXES)


def parse_command(line: str) -> ParsedCommand:
    """Split ``/band 20m`` into its name and argument."""
    stripped = line.strip()[1:].strip()
    name, _sep, argument = stripped.partition(" ")
    return ParsedCommand(name=name.strip().lower(), argument=argument.strip())


def _canonical_key(raw_key: str) -> str | None:
    return FIELD_ALIASES.get(raw_key.strip().lower())


def parse(
    line: str,
    *,
    field_order: tuple[str, ...] = DEFAULT_FIELD_ORDER,
    separator: str = ",",
    mode_name: str | None = None,
    validation: str = DEFAULT_VALIDATION,
) -> ParsedEntry:
    """Turn a fast entry line into QSO field values.

    Args:
        line: Raw text typed by the operator.
        field_order: Positional field mapping, from the active profile.
        separator: Token separator, from the active profile.
        mode_name: Active mode, used to pick the default signal report.
        validation: "strict" refuses a malformed callsign, "warn" accepts it
            with a notice, "off" skips the check. Ending the callsign with
            ``!`` overrides a strict refusal for that one entry.

    Returns:
        A ParsedEntry; check ``ok`` before using it.
    """
    result = ParsedEntry()
    text = line.strip()
    if not text:
        result.error = _("The line is empty.")
        return result

    tokens = [token.strip() for token in text.split(separator)]

    positional: list[str] = []
    for token in tokens:
        if "=" in token:
            raw_key, _sep, value = token.partition("=")
            key = _canonical_key(raw_key)
            if key is None:
                result.warnings.append(
                    _("Unknown field «{field}», ignored.").format(field=raw_key.strip())
                )
                continue
            if key in DIGITAL_KEYS:
                result.digital[key] = value.strip()
            else:
                result.fields[key] = value.strip()
        else:
            positional.append(token)

    # Map remaining tokens onto the positional order, skipping fields already
    # set by name so `ea7wm,call=EA4ABC` cannot produce two callsigns.
    available = [name for name in field_order if name not in result.fields]
    # Lengths differ on purpose: a short line simply leaves fields unset.
    for name, value in zip(available, positional, strict=False):
        if value:
            result.fields[name] = value
    if len(positional) > len(available):
        extra = separator.join(positional[len(available):])
        result.warnings.append(
            _("Extra values, added to the comment: «{extra}»").format(extra=extra)
        )
        previous = str(result.fields.get("comment", "")).strip()
        result.fields["comment"] = f"{previous} {extra}".strip()

    raw_call = str(result.fields.get("call", "")).strip()
    if not raw_call:
        result.error = _("The callsign is missing.")
        return result

    raw_call, forced = callsign.strip_override(raw_call)
    if not raw_call:
        result.error = _("The callsign is missing.")
        return result

    return finish(result, mode_name=mode_name, validation=validation)


def finish(
    result: ParsedEntry,
    *,
    mode_name: str | None = None,
    validation: str = DEFAULT_VALIDATION,
) -> ParsedEntry:
    """Validate and coerce raw field values into what the log expects.

    Shared by the text parser and by the field-by-field entry panel, so both
    apply the same callsign rules, the same defaults and the same conversions.

    Args:
        result: Carries the raw values in ``fields`` and ``digital``.
        mode_name: Active mode, used to pick the default signal report.
        validation: "strict", "warn" or "off".

    Returns:
        The same object, with values normalised; check ``ok``.
    """
    raw_call = str(result.fields.get("call", "")).strip()
    if not raw_call:
        result.error = _("The callsign is missing.")
        return result

    raw_call, forced = callsign.strip_override(raw_call)
    if not raw_call:
        result.error = _("The callsign is missing.")
        return result

    result.fields["call"] = callsign.normalize(raw_call)
    problem = callsign.validate(raw_call) if validation != "off" else None
    if problem is not None:
        if validation == "strict" and not forced:
            result.error = problem
            return result
        result.warnings.append(problem)
    elif forced:
        result.warnings.append(_("No need to force it: the callsign is valid."))

    country = callsign.country_for(raw_call)
    if country:
        result.fields.setdefault("country", country)

    _normalize_values(result, mode_name)
    return result


def from_fields(
    values: dict[str, str],
    *,
    mode_name: str | None = None,
    validation: str = DEFAULT_VALIDATION,
) -> ParsedEntry:
    """Build an entry from values already split into fields.

    This is the path the entry panel uses now that each field has its own box:
    there is nothing to split, only to validate and convert.
    """
    result = ParsedEntry()
    for name, value in values.items():
        text = value.strip()
        if not text:
            continue
        if name in DIGITAL_KEYS:
            result.digital[name] = text
        else:
            result.fields[name] = text
    return finish(result, mode_name=mode_name, validation=validation)


def _normalize_values(result: ParsedEntry, mode_name: str | None) -> None:
    """Coerce free text into the types the persistence layer expects."""
    fields = result.fields

    if "name" in fields:
        fields["name"] = str(fields["name"]).strip().title()

    report = modes.default_rst(mode_name)
    fields.setdefault("rst_sent", report)
    fields.setdefault("rst_rcvd", report)

    if "gridsquare" in fields:
        fields["gridsquare"] = str(fields["gridsquare"]).strip().upper()

    if "freq_hz" in fields:
        parsed = bands.parse_frequency(str(fields["freq_hz"]))
        if parsed is None:
            result.warnings.append(
                _("Frequency not recognised: «{value}»").format(value=fields["freq_hz"])
            )
            del fields["freq_hz"]
        else:
            fields["freq_hz"] = parsed
            band = bands.from_frequency(parsed)
            if band:
                fields.setdefault("band", band.name)

    if "band" in fields:
        band = bands.get(str(fields["band"]))
        if band is None:
            result.warnings.append(_("Unknown band: «{value}»").format(value=fields["band"]))
            del fields["band"]
        else:
            fields["band"] = band.name
            fields.setdefault("freq_hz", band.default_hz)

    if "mode" in fields:
        mode = modes.get(str(fields["mode"]))
        if mode is None:
            result.warnings.append(_("Unknown mode: «{value}»").format(value=fields["mode"]))
            del fields["mode"]
        else:
            fields["mode"] = mode.name

    if "power_w" in fields:
        try:
            fields["power_w"] = int(float(str(fields["power_w"]).replace(",", ".")))
        except ValueError:
            result.warnings.append(
                _("Power is not a number: «{value}»").format(value=fields["power_w"])
            )
            del fields["power_w"]


def format_line(
    row: object,
    field_order: tuple[str, ...] = DEFAULT_FIELD_ORDER,
    separator: str = ",",
) -> str:
    """Rebuild an entry line from a logged QSO.

    Used by "repeat": the operator gets the previous contact back in the entry
    line, edits what changed and presses Enter to log it again. Trailing empty
    fields are dropped so the line does not end in a run of separators.

    Args:
        row: A QsoRow, or anything exposing the same attributes.
        field_order: Positional mapping of the active profile.
        separator: Token separator of the active profile.
    """
    values: list[str] = []
    for name in field_order:
        if name == "freq_hz":
            freq = getattr(row, "freq_hz", None)
            values.append(bands.format_frequency(freq) if freq else "")
        elif name == "power_w":
            power = getattr(row, "power_w", None)
            values.append(str(power) if power else "")
        else:
            values.append(str(getattr(row, name, "") or ""))

    while values and not values[-1]:
        values.pop()
    return separator.join(values)


def values_from_row(
    row: object, field_order: tuple[str, ...] = DEFAULT_FIELD_ORDER
) -> dict[str, str]:
    """Field values taken from a logged QSO, for the repeat action."""
    values: dict[str, str] = {}
    for name in field_order:
        if name == "freq_hz":
            freq = getattr(row, "freq_hz", None)
            values[name] = bands.format_frequency(freq) if freq else ""
        elif name == "power_w":
            power = getattr(row, "power_w", None)
            values[name] = str(power) if power else ""
        else:
            values[name] = str(getattr(row, name, "") or "")
    return values


def describe_order(field_order: tuple[str, ...], separator: str = ",") -> str:
    """Hint line shown under the entry field, e.g. 'call , name , ...'.

    Each hint is a key the operator may type in ``key=value`` form, in the
    language of the interface.
    """
    return f" {separator} ".join(_(ORDER_HINTS.get(name, name)) for name in field_order)
