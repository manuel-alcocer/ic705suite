"""Operating modes, including digital voice and data modes.

ADIF does not treat DMR/D-STAR/C4FM as top level modes: they are submodes of
DIGITALVOICE. The same applies to FT4 and JS8, which are MFSK submodes. Each
entry therefore carries the ADIF pair it must be exported as, while the TUI
shows the name operators actually use.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..i18n import N_, _

# Extra fields a digital mode needs beyond the common QSO data. The keys match
# the columns of Qso.digital_data so screens can be generated from this table.
# Labels are English, marked with N_(); translate them with _() where shown.
DigitalField = tuple[str, str]  # (key, label shown in the UI)


@dataclass(frozen=True, slots=True)
class Mode:
    """An operating mode as presented to the operator.

    Attributes:
        name: Identifier used inside the application, e.g. "C4FM".
        adif_mode: MODE value written to ADIF.
        adif_submode: SUBMODE value written to ADIF, empty when not needed.
        category: One of "voice", "cw", "digital_voice", "data".
        default_rst: Report pre-filled when the operator omits it.
        digital_fields: Extra prompts asked after picking the mode.
    """

    name: str
    adif_mode: str
    adif_submode: str = ""
    category: str = "voice"
    default_rst: str = "59"
    digital_fields: tuple[DigitalField, ...] = field(default_factory=tuple)

    @property
    def is_digital(self) -> bool:
        return self.category in ("digital_voice", "data")

    @property
    def label(self) -> str:
        if self.adif_submode and self.adif_submode != self.name:
            return f"{self.name} ({self.adif_mode}/{self.adif_submode})"
        return self.name


_DMR_FIELDS: tuple[DigitalField, ...] = (
    ("talkgroup", "Talkgroup (TG)"),
    ("network", N_("Network (Brandmeister, TGIF, DMR+)")),
    ("color_code", "Color Code"),
    ("repeater", N_("Repeater / hotspot")),
)
_DSTAR_FIELDS: tuple[DigitalField, ...] = (
    ("reflector", N_("Reflector (e.g. REF001C)")),
    ("gateway", N_("Gateway / repeater")),
)
_C4FM_FIELDS: tuple[DigitalField, ...] = (
    ("room", "Room Wires-X"),
    ("dg_id", "DG-ID"),
    ("repeater", N_("Repeater / hotspot")),
)
_M17_FIELDS: tuple[DigitalField, ...] = (
    ("reflector", "Reflector"),
    ("module", N_("Module")),
)
_NET_FIELDS: tuple[DigitalField, ...] = (
    ("network", N_("Network / server")),
    ("repeater", N_("Node")),
)

# Analogue and CW modes.
ANALOG_MODES: tuple[Mode, ...] = (
    Mode("SSB", "SSB", "", "voice", "59"),
    Mode("USB", "SSB", "USB", "voice", "59"),
    Mode("LSB", "SSB", "LSB", "voice", "59"),
    Mode("CW", "CW", "", "cw", "599"),
    Mode("FM", "FM", "", "voice", "59"),
    Mode("AM", "AM", "", "voice", "59"),
)

# Digital voice modes.
DIGITAL_VOICE_MODES: tuple[Mode, ...] = (
    Mode("DMR", "DIGITALVOICE", "DMR", "digital_voice", "59", _DMR_FIELDS),
    Mode("DSTAR", "DIGITALVOICE", "DSTAR", "digital_voice", "59", _DSTAR_FIELDS),
    Mode("C4FM", "DIGITALVOICE", "C4FM", "digital_voice", "59", _C4FM_FIELDS),
    Mode("M17", "DIGITALVOICE", "M17", "digital_voice", "59", _M17_FIELDS),
    Mode("P25", "DIGITALVOICE", "P25", "digital_voice", "59", _NET_FIELDS),
    Mode("NXDN", "DIGITALVOICE", "NXDN", "digital_voice", "59", _NET_FIELDS),
    Mode("ECHOLINK", "FM", "", "digital_voice", "59", _NET_FIELDS),
)

# Data modes.
DATA_MODES: tuple[Mode, ...] = (
    Mode("FT8", "FT8", "", "data", "-10"),
    Mode("FT4", "MFSK", "FT4", "data", "-10"),
    Mode("JS8", "MFSK", "JS8", "data", "-10"),
    Mode("JT65", "JT65", "", "data", "-10"),
    Mode("MSK144", "MSK144", "", "data", "-10"),
    Mode("WSPR", "WSPR", "", "data", "-20"),
    Mode("PSK31", "PSK", "PSK31", "data", "599"),
    Mode("PSK63", "PSK", "PSK63", "data", "599"),
    Mode("RTTY", "RTTY", "", "data", "599"),
    Mode("OLIVIA", "OLIVIA", "", "data", "599"),
    Mode("SSTV", "SSTV", "", "data", "59"),
    Mode("PACKET", "PKT", "", "data", "599"),
    Mode("VARA", "DYNAMIC", "VARA HF", "data", "599"),
)

MODES: tuple[Mode, ...] = ANALOG_MODES + DIGITAL_VOICE_MODES + DATA_MODES
BY_NAME: dict[str, Mode] = {mode.name: mode for mode in MODES}

DEFAULT_MODE = "SSB"


#: Compact labels for the digital extras, used where space is tight. English,
#: translated with _() where shown.
SHORT_FIELD_NAMES: dict[str, str] = {
    "talkgroup": "TG",
    "network": N_("Network"),
    "color_code": "CC",
    "repeater": N_("Repeater"),
    "reflector": "Reflector",
    "gateway": "Gateway",
    "room": "Room",
    "dg_id": "DG-ID",
    "module": N_("Module"),
}


#: Prefixes used when squeezing digital values into the status line.
STATUS_PREFIXES: dict[str, str] = {
    "talkgroup": "TG",
    "color_code": "CC",
    "dg_id": "DG",
}

#: Values already implied by the repeater shown next to them.
STATUS_SKIP: frozenset[str] = frozenset({"repeater", "gateway"})


def status_summary(digital_data: dict[str, str], *, has_repeater: bool = False) -> str:
    """Render digital values compactly, e.g. 'TG214 CC1 Brandmeister'.

    The status line is a single row shared with the operator, band, frequency,
    mode, rig and profile, so verbose ``key=value`` pairs would push the rest
    off the edge.
    """
    parts = []
    for key, value in sorted(digital_data.items()):
        if not value or (has_repeater and key in STATUS_SKIP):
            continue
        prefix = STATUS_PREFIXES.get(key)
        parts.append(f"{prefix}{value}" if prefix else str(value))
    return " ".join(parts)


def short_fields(mode: Mode) -> str:
    """Comma separated short names of a mode's extra fields."""
    return ", ".join(
        _(SHORT_FIELD_NAMES.get(key, key)) for key, _label in mode.digital_fields
    )


def get(name: str | None) -> Mode | None:
    """Look up a mode by name, case-insensitively."""
    if not name:
        return None
    return BY_NAME.get(name.strip().upper())


def default_rst(mode_name: str | None) -> str:
    """Report to pre-fill for a mode, falling back to the SSB convention."""
    mode = get(mode_name)
    return mode.default_rst if mode else "59"


def digital_fields(mode_name: str | None) -> tuple[DigitalField, ...]:
    """Extra prompts to ask for after picking a mode."""
    mode = get(mode_name)
    return mode.digital_fields if mode else ()
