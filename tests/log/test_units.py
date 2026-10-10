"""Frequency units, separators and the formats built from them."""

from __future__ import annotations

import pytest

from ic705suite.apps.log.core import bands, repeaters, units
from ic705suite.apps.log.core.units import FrequencyFormat, UnitError

# --------------------------------------------------------------- units ----

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("m", "MHz"), ("M", "MHz"), ("mhz", "MHz"), ("MHZ", "MHz"), ("mHz", "MHz"),
        ("k", "kHz"), ("K", "kHz"), ("khz", "kHz"), ("KHZ", "kHz"), ("kHz", "kHz"),
        ("hz", "Hz"), ("HZ", "Hz"), ("hZ", "Hz"), ("Hz", "Hz"),
    ],
)
def test_units_are_normalised(text, expected):
    """Lower case k becomes K, and any spelling of hz becomes Hz."""
    assert units.normalize_unit(text) == expected


@pytest.mark.parametrize("text", ["x", "mega", "", "ghz", "MM"])
def test_unknown_units_are_rejected(text):
    with pytest.raises(UnitError):
        units.normalize_unit(text)


# ---------------------------------------------------------- separators ----

def test_thousands_and_decimal_cannot_be_the_same():
    with pytest.raises(UnitError, match="no pueden ser el mismo"):
        FrequencyFormat("Hz", ".", ".")
    with pytest.raises(UnitError, match="no pueden ser el mismo"):
        FrequencyFormat("MHz", ",", ",")


def test_thousands_can_be_hidden_but_the_decimal_cannot():
    assert FrequencyFormat("Hz", ".", "").thousands == ""
    assert units.parse_separator("ocultar", allow_hidden=True) == ""
    with pytest.raises(UnitError, match="no se puede ocultar"):
        units.parse_separator("ocultar", allow_hidden=False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [(".", "."), (",", ","), ("espacio", " "), ("'", "'"), ("ninguno", ""), ("no", "")],
)
def test_separators_are_read_from_their_names(text, expected):
    assert units.parse_separator(text, allow_hidden=True) == expected


@pytest.mark.parametrize("text", ["..", "abc", "x"])
def test_bad_separators_are_rejected(text):
    with pytest.raises(UnitError):
        units.parse_separator(text, allow_hidden=True)


# -------------------------------------------------------------- format ----

@pytest.mark.parametrize(
    ("unit", "decimal", "thousands", "expected"),
    [
        ("MHz", ".", "", "7.130 MHz"),
        ("MHz", ",", ".", "7,130 MHz"),
        ("kHz", ".", ",", "7,130 kHz"),
        ("kHz", ",", ".", "7.130 kHz"),
        ("kHz", ".", "", "7130 kHz"),
        ("Hz", ".", ",", "7,130,000 Hz"),
        ("Hz", ",", ".", "7.130.000 Hz"),
        ("Hz", ".", " ", "7 130 000 Hz"),
        ("Hz", ".", "", "7130000 Hz"),
    ],
)
def test_the_same_frequency_in_every_format(unit, decimal, thousands, expected):
    assert FrequencyFormat(unit, decimal, thousands).format(7_130_000) == expected


def test_megahertz_keeps_kilohertz_resolution():
    """Radios show three decimals, so a round frequency is not shown as '7'."""
    chosen = FrequencyFormat("MHz", ".", "")
    assert chosen.format(7_000_000) == "7.000 MHz"
    assert chosen.format(14_250_500) == "14.2505 MHz"


def test_format_without_the_unit():
    assert FrequencyFormat().format(7_130_000, with_unit=False) == "7.130"


def test_empty_frequency():
    assert FrequencyFormat().format(None) == "-"
    assert FrequencyFormat().format(0) == "-"


# --------------------------------------------------------------- parse ----

def test_a_bare_number_uses_the_configured_unit():
    assert FrequencyFormat("MHz", ".", "").parse("7.130") == 7_130_000
    assert FrequencyFormat("kHz", ".", "").parse("7130") == 7_130_000
    assert FrequencyFormat("Hz", ".", "").parse("7130000") == 7_130_000


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7130 k", 7_130_000),
        ("7130 K", 7_130_000),
        ("7130k", 7_130_000),
        ("7130 khz", 7_130_000),
        ("7130 KHZ", 7_130_000),
        ("7.130 M", 7_130_000),
        ("7130000 Hz", 7_130_000),
        ("7130000hz", 7_130_000),
    ],
)
def test_a_written_unit_wins_over_the_configured_one(text, expected):
    """Configured as megahertz, '7130 K' still means kilohertz."""
    assert FrequencyFormat("MHz", ".", "").parse(text) == expected


def test_separators_are_read_as_configured():
    european = FrequencyFormat("kHz", ",", ".")
    assert european.parse("7.130") == 7_130_000
    assert european.parse("7.130,5") == 7_130_500


def test_spaces_and_apostrophes_always_group():
    """Whatever the preference, these never mean a decimal point."""
    chosen = FrequencyFormat("Hz", ".", "")
    assert chosen.parse("7 130 000") == 7_130_000
    assert chosen.parse("7'130'000") == 7_130_000


@pytest.mark.parametrize("text", ["", "   ", "basura", "7.1.3", "M"])
def test_unreadable_frequencies(text):
    assert FrequencyFormat().parse(text) is None


def test_format_and_parse_round_trip():
    for unit in units.UNITS:
        for decimal, thousands in ((".", ""), (",", "."), (".", ","), (".", " ")):
            chosen = FrequencyFormat(unit, decimal, thousands)
            for freq in (7_130_000, 145_500_000, 438_750_000, 1_840_000):
                assert chosen.parse(chosen.format(freq)) == freq, chosen.summary


# ------------------------------------------------- the rest of the app ----

def test_the_active_format_drives_the_whole_application():
    units.set_active(FrequencyFormat("Hz", ",", "."))
    assert bands.format_frequency(7_130_000) == "7.130.000 Hz"
    assert bands.parse_frequency("7.130.000") == 7_130_000

    units.set_active(FrequencyFormat("MHz", ".", ""))
    assert bands.format_frequency(7_130_000) == "7.130 MHz"
    assert bands.parse_frequency("7.130") == 7_130_000


def test_band_ranges_follow_the_format():
    units.set_active(FrequencyFormat("kHz", ".", ","))
    text = bands.get("40m").range_text
    assert "7,000" in text and "7,200 kHz" in text


def test_repeater_shifts_stay_in_kilohertz_by_default():
    """A shift of -600 must never be read as megahertz."""
    units.set_active(FrequencyFormat("MHz", ".", ""))
    assert repeaters.parse_shift("-600") == -600_000
    assert repeaters.parse_shift("-600 k") == -600_000
    assert repeaters.parse_shift("-7.6 M") == -7_600_000
    assert repeaters.format_shift(-600_000) == "-600 kHz"
    assert repeaters.format_shift(-7_600_000) == "-7.600 MHz"
    assert repeaters.format_shift(0) == "simplex"


def test_adif_export_ignores_the_display_format():
    """The specification fixes megahertz with a dot; files must stay portable."""
    units.set_active(FrequencyFormat("Hz", ",", "."))
    assert bands.frequency_mhz(7_130_000) == "7.130000"


def test_units_use_the_si_spelling():
    """Kilo is a lower case k; capital K is the kelvin."""
    assert units.UNIT_KHZ == "kHz"
    assert units.UNIT_MHZ == "MHz"
    assert units.UNIT_HZ == "Hz"
    assert FrequencyFormat("kHz", ".", "").format(7_130_000) == "7130 kHz"


@pytest.mark.parametrize("stored", ["KHz", "kHz", "k", "K", "khz", "KHZ"])
def test_settings_written_in_any_spelling_still_work(stored):
    """Including 'KHz', which an earlier version of this program wrote."""
    chosen = FrequencyFormat(stored, ".", "")
    assert chosen.unit == "kHz"
    assert chosen.format(7_130_000) == "7130 kHz"


def test_a_session_keeps_a_unit_stored_by_an_older_version():
    from ic705suite.apps.log.core.state import SessionState

    state = SessionState(freq_unit="KHz")
    assert state.frequency_format.unit == "kHz"

    # A genuinely unusable value falls back rather than refusing to start.
    assert SessionState(freq_unit="parsecs").frequency_format.unit == "MHz"
