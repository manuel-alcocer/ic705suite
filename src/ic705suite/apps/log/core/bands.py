"""Amateur radio band plan.

Band edges follow IARU Region 1 where regions differ. Frequencies are stored
as integer hertz everywhere in the application to avoid float rounding; ADIF
export converts to megahertz only at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..i18n import N_, _
from . import units

KHZ = 1_000
MHZ = 1_000_000


@dataclass(frozen=True, slots=True)
class Band:
    """A single amateur band.

    Attributes:
        name: ADIF band identifier, e.g. "20m".
        low_hz: Lower edge, inclusive.
        high_hz: Upper edge, inclusive.
        default_hz: Sensible starting frequency (calling/activity centre).
        label: Human readable wavelength plus frequency range, in English;
            translated with ``_()`` where it is shown.
    """

    name: str
    low_hz: int
    high_hz: int
    default_hz: int
    label: str

    def contains(self, freq_hz: int) -> bool:
        return self.low_hz <= freq_hz <= self.high_hz

    @property
    def range_text(self) -> str:
        """Edges rendered in the operator's chosen unit and separators."""
        low = units.active().format(self.low_hz, with_unit=False)
        high = units.active().format(self.high_hz)
        return f"{_(self.label)} · {low} - {high}"


# Ordered from lowest to highest frequency; the order drives the selector list.
BANDS: tuple[Band, ...] = (
    Band("2190m", 135_700, 137_800, 136_000, N_("2190 m · 135.7-137.8 kHz")),
    Band("630m", 472 * KHZ, 479 * KHZ, 475 * KHZ, N_("630 m · 472-479 kHz")),
    Band("160m", 1_810 * KHZ, 2_000 * KHZ, 1_840 * KHZ, N_("160 m · 1.810-2.000 MHz")),
    Band("80m", 3_500 * KHZ, 3_800 * KHZ, 3_700 * KHZ, N_("80 m · 3.500-3.800 MHz")),
    Band("60m", 5_351_500, 5_366_500, 5_355 * KHZ, N_("60 m · 5.3515-5.3665 MHz")),
    Band("40m", 7_000 * KHZ, 7_200 * KHZ, 7_130 * KHZ, N_("40 m · 7.000-7.200 MHz")),
    Band("30m", 10_100 * KHZ, 10_150 * KHZ, 10_120 * KHZ, N_("30 m · 10.100-10.150 MHz")),
    Band("20m", 14_000 * KHZ, 14_350 * KHZ, 14_250 * KHZ, N_("20 m · 14.000-14.350 MHz")),
    Band("17m", 18_068 * KHZ, 18_168 * KHZ, 18_130 * KHZ, N_("17 m · 18.068-18.168 MHz")),
    Band("15m", 21_000 * KHZ, 21_450 * KHZ, 21_300 * KHZ, N_("15 m · 21.000-21.450 MHz")),
    Band("12m", 24_890 * KHZ, 24_990 * KHZ, 24_950 * KHZ, N_("12 m · 24.890-24.990 MHz")),
    Band("10m", 28_000 * KHZ, 29_700 * KHZ, 28_400 * KHZ, N_("10 m · 28.000-29.700 MHz")),
    Band("6m", 50 * MHZ, 52 * MHZ, 50_150 * KHZ, N_("6 m · 50-52 MHz")),
    Band("4m", 70 * MHZ, 70_500 * KHZ, 70_200 * KHZ, N_("4 m · 70-70.5 MHz")),
    Band("2m", 144 * MHZ, 146 * MHZ, 145_500 * KHZ, N_("2 m · 144-146 MHz")),
    Band("1.25m", 222 * MHZ, 225 * MHZ, 223_500 * KHZ, N_("1.25 m · 222-225 MHz (R2)")),
    Band("70cm", 430 * MHZ, 440 * MHZ, 433_500 * KHZ, N_("70 cm · 430-440 MHz")),
    Band("33cm", 902 * MHZ, 928 * MHZ, 906 * MHZ, N_("33 cm · 902-928 MHz (R2)")),
    Band("23cm", 1_240 * MHZ, 1_300 * MHZ, 1_297 * MHZ, N_("23 cm · 1240-1300 MHz")),
    Band("13cm", 2_300 * MHZ, 2_450 * MHZ, 2_320 * MHZ, N_("13 cm · 2300-2450 MHz")),
    Band("9cm", 3_300 * MHZ, 3_500 * MHZ, 3_400 * MHZ, N_("9 cm · 3300-3500 MHz")),
    Band("6cm", 5_650 * MHZ, 5_850 * MHZ, 5_760 * MHZ, N_("6 cm · 5650-5850 MHz")),
    Band("3cm", 10_000 * MHZ, 10_500 * MHZ, 10_368 * MHZ, N_("3 cm · 10.0-10.5 GHz")),
)

BY_NAME: dict[str, Band] = {band.name: band for band in BANDS}


def get(name: str) -> Band | None:
    """Look up a band by its ADIF name, case-insensitively."""
    return BY_NAME.get(name.strip().lower())


def from_frequency(freq_hz: int) -> Band | None:
    """Return the band containing ``freq_hz``, or None if out of any band."""
    for band in BANDS:
        if band.contains(freq_hz):
            return band
    return None


def format_frequency(freq_hz: int | None, *, with_unit: bool = True) -> str:
    """Render hertz the way the operator has asked to see frequencies.

    Delegates to the active format, so unit and separators are decided once
    in the settings rather than in each widget.
    """
    return units.active().format(freq_hz, with_unit=with_unit)


def frequency_mhz(freq_hz: int | None) -> str:
    """Render hertz as the decimal MHz string ADIF expects, e.g. '7.130000'.

    Trailing zeros are kept: logging programs read FREQ at fixed precision and
    a trimmed '7.13' is technically valid but unusual enough to confuse some.
    """
    if not freq_hz:
        return ""
    return f"{freq_hz / MHZ:.6f}"


def parse_frequency(text: str) -> int | None:
    """Parse a frequency the operator typed, in hertz.

    A unit written by hand wins; without one, the configured default applies.
    Guessing the unit from the magnitude, as this used to do, made the same
    text mean different things depending on the number.
    """
    return units.active().parse(text)


def frequency_help() -> str:
    """One line telling the operator what shape a frequency should have."""
    current = units.active()
    return _(
        "Default unit {unit} (example: {example}). You can type another: M, k or Hz."
    ).format(unit=current.unit, example=current.example)
