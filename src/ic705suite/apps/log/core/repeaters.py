"""Repeater specific domain rules.

A repeater is worked in split: you listen on its output and transmit on its
input, separated by a fixed shift that depends on the band. Most repeaters
also need a CTCSS sub-audible tone to open them, and digital ones need their
own parameters (color code, talkgroup, reflector, room).
"""

from __future__ import annotations

from . import units
from .bands import KHZ, MHZ

#: Conventional repeater shift per band, IARU Region 1. Negative means the
#: input is below the output, which is the usual case in Europe.
DEFAULT_SHIFTS: dict[str, int] = {
    "10m": -100 * KHZ,
    "6m": -500 * KHZ,
    "4m": -1600 * KHZ,
    "2m": -600 * KHZ,
    "1.25m": -1600 * KHZ,
    "70cm": -7600 * KHZ,
    "33cm": -12 * MHZ,
    "23cm": -6 * MHZ,
}

#: Standard CTCSS tones in hertz, as text because that is how radios show them.
CTCSS_TONES: tuple[str, ...] = (
    "67.0", "69.3", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4", "88.5",
    "91.5", "94.8", "97.4", "100.0", "103.5", "107.2", "110.9", "114.8",
    "118.8", "123.0", "127.3", "131.8", "136.5", "141.3", "146.2", "151.4",
    "156.7", "162.2", "167.9", "173.8", "179.9", "186.2", "192.8", "203.5",
    "206.5", "210.7", "218.1", "225.7", "229.1", "233.6", "241.8", "250.3",
    "254.1",
)


def default_shift(band_name: str) -> int:
    """Conventional shift for a band, or zero when the band has no repeaters."""
    return DEFAULT_SHIFTS.get(band_name, 0)


def input_frequency(output_hz: int, shift_hz: int) -> int:
    """Frequency you transmit on, given the repeater output and its shift."""
    return output_hz + shift_hz


def parse_shift(text: str) -> int | None:
    """Parse a repeater shift as the operator writes it on a radio.

    Accepts ``-600``, ``-600 k``, ``-7.6 MHz``, ``+5 M`` and plain hertz, in
    any capitalisation. A bare number is read as kilohertz whatever the
    display preference is: shifts are spoken in kilohertz, and reading "-600"
    as megahertz would be nonsense.

    Returns:
        The shift in hertz, or None when it cannot be understood. Zero is a
        valid result and means simplex.
    """
    raw = text.strip()
    if not raw:
        return None

    sign = 1
    if raw.startswith("-"):
        sign, raw = -1, raw[1:].strip()
    elif raw.startswith("+"):
        raw = raw[1:].strip()

    kilohertz = units.FrequencyFormat(
        units.UNIT_KHZ, units.active().decimal, units.active().thousands
    )
    magnitude = kilohertz.parse(raw)
    if magnitude is None:
        return None
    return sign * magnitude


def format_shift(shift_hz: int | None) -> str:
    """Render a shift the way a radio displays it, e.g. '-600 kHz'.

    Always in kilohertz below a megahertz and megahertz above, regardless of
    the display preference: that is how repeater shifts are written.
    """
    if not shift_hz:
        return "simplex"
    sign = "-" if shift_hz < 0 else "+"
    magnitude = abs(shift_hz)
    chosen = units.UNIT_MHZ if magnitude >= MHZ else units.UNIT_KHZ
    current = units.active()
    rendered = units.FrequencyFormat(
        chosen, current.decimal, current.thousands
    ).format(magnitude)
    return f"{sign}{rendered}"


def normalize_tone(text: str) -> str:
    """Normalise a CTCSS tone to one decimal place, e.g. '88' -> '88.5'.

    An empty value means no tone. Unknown values are returned as typed so an
    unusual repeater is never rejected; ``is_standard_tone`` reports on them.
    """
    raw = text.strip().replace(",", ".").rstrip("hHzZ ").strip()
    if not raw:
        return ""
    try:
        value = float(raw)
    except ValueError:
        return raw
    formatted = f"{value:.1f}"
    if formatted in CTCSS_TONES:
        return formatted
    # Tolerate '88' for '88.5' by matching on the integer part when unique.
    candidates = [tone for tone in CTCSS_TONES if tone.startswith(f"{int(value)}.")]
    if len(candidates) == 1:
        return candidates[0]
    return formatted


def is_standard_tone(tone: str) -> bool:
    """True when the tone is one of the standard CTCSS frequencies."""
    return not tone or tone in CTCSS_TONES
