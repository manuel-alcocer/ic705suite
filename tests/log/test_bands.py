"""Band plan and frequency parsing."""

from __future__ import annotations

import pytest

from ic705suite.apps.log.core import bands


@pytest.mark.parametrize(
    ("text", "expected_hz"),
    [
        # Without a unit, the configured default applies (megahertz).
        ("14.250", 14_250_000),
        ("7.130", 7_130_000),
        ("145.500", 145_500_000),
        # A unit written by hand wins over the default, in any capitalisation.
        ("433.500 MHz", 433_500_000),
        ("433.500 mhz", 433_500_000),
        ("433.500m", 433_500_000),
        ("7130 k", 7_130_000),
        ("7130 kHz", 7_130_000),
        ("7130 KHz", 7_130_000),
        ("7130khz", 7_130_000),
        ("145500000 Hz", 145_500_000),
        ("145500000 HZ", 145_500_000),
        ("", None),
        ("no soy una frecuencia", None),
    ],
)
def test_parse_frequency(text, expected_hz):
    assert bands.parse_frequency(text) == expected_hz


def test_parse_frequency_no_longer_guesses_from_magnitude():
    """A bare number means the configured unit, whatever its size.

    Guessing made the same text mean different things depending on the
    number, which is worse than asking for a unit.
    """
    assert bands.parse_frequency("7130") == 7130 * bands.MHZ


@pytest.mark.parametrize(
    ("freq_hz", "band_name"),
    [
        (7_130_000, "40m"),
        (14_250_000, "20m"),
        (145_500_000, "2m"),
        (433_500_000, "70cm"),
        (5_000_000, None),
    ],
)
def test_band_from_frequency(freq_hz, band_name):
    band = bands.from_frequency(freq_hz)
    assert (band.name if band else None) == band_name


def test_format_frequency_uses_the_active_format():
    assert bands.format_frequency(7_130_000) == "7.130 MHz"
    assert bands.format_frequency(7_130_000, with_unit=False) == "7.130"
    assert bands.format_frequency(None) == "-"


def test_adif_frequency_keeps_six_decimals():
    """LoTW and other services expect a fixed precision MHz value."""
    assert bands.frequency_mhz(7_130_000) == "7.130000"
    assert bands.frequency_mhz(None) == ""


def test_every_band_default_is_inside_its_own_edges():
    for band in bands.BANDS:
        assert band.contains(band.default_hz), band.name
