"""Amateur bands covered by the IC-705 with their CW segments (IARU Region 1)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    name: str
    start: int        # whole band, Hz
    end: int
    cw_start: int     # CW segment used by the automatic search
    cw_end: int
    default: int      # frequency used by '/band' (QRP CW calling frequency where one exists)


BANDS: list[Band] = [
    Band("160m", 1_810_000, 2_000_000, 1_810_000, 1_838_000, 1_836_000),
    Band("80m", 3_500_000, 3_800_000, 3_500_000, 3_570_000, 3_560_000),
    Band("60m", 5_351_500, 5_366_500, 5_351_500, 5_354_000, 5_352_000),
    Band("40m", 7_000_000, 7_200_000, 7_000_000, 7_040_000, 7_030_000),
    Band("30m", 10_100_000, 10_150_000, 10_100_000, 10_130_000, 10_116_000),
    Band("20m", 14_000_000, 14_350_000, 14_000_000, 14_070_000, 14_060_000),
    Band("17m", 18_068_000, 18_168_000, 18_068_000, 18_095_000, 18_086_000),
    Band("15m", 21_000_000, 21_450_000, 21_000_000, 21_070_000, 21_060_000),
    Band("12m", 24_890_000, 24_990_000, 24_890_000, 24_915_000, 24_906_000),
    Band("10m", 28_000_000, 29_700_000, 28_000_000, 28_070_000, 28_060_000),
    Band("6m", 50_000_000, 52_000_000, 50_000_000, 50_100_000, 50_090_000),
    Band("2m", 144_000_000, 146_000_000, 144_000_000, 144_150_000, 144_050_000),
    Band("70cm", 430_000_000, 440_000_000, 432_000_000, 432_150_000, 432_050_000),
]


def find_band(name: str) -> Band | None:
    key = name.lower().strip()
    if key and key[-1].isdigit():
        key += "m"
    for band in BANDS:
        if band.name == key:
            return band
    return None


def band_for(freq: int) -> Band | None:
    for band in BANDS:
        if band.start <= freq <= band.end:
            return band
    return None


def parse_frequency(text: str) -> int:
    """'7.030' (MHz), '7030.5' (kHz) or '7030500' (Hz) -> Hz."""
    value = float(text.replace(",", "."))
    if value <= 0:
        raise ValueError("frequency must be positive")
    if value < 1000:
        hz = value * 1e6
    elif value < 1e6:
        hz = value * 1e3
    else:
        hz = value
    return int(round(hz))


def format_frequency(hz: int | None) -> str:
    """7030120 -> '7.030.12' (MHz.kHz.10Hz, as on the radio display)."""
    if hz is None:
        return "-.---.--"
    mhz, rest = divmod(hz, 1_000_000)
    khz, hz_rest = divmod(rest, 1000)
    return f"{mhz}.{khz:03d}.{hz_rest // 10:02d}"
