"""Fast entry line parsing."""

from __future__ import annotations

import pytest

from ic705suite.apps.log.core import entry


def test_positional_fields_in_default_order():
    result = entry.parse("ea7wm,victor,59,57,Sevilla,primer contacto", mode_name="SSB")
    assert result.ok
    assert result.fields["call"] == "EA7WM"
    assert result.fields["name"] == "Victor"
    assert result.fields["rst_sent"] == "59"
    assert result.fields["rst_rcvd"] == "57"
    assert result.fields["qth"] == "Sevilla"
    assert result.fields["comment"] == "primer contacto"


def test_callsign_alone_uses_default_report_for_the_mode():
    ssb = entry.parse("ea7wm", mode_name="SSB")
    assert ssb.fields["rst_sent"] == "59"
    cw = entry.parse("ea7wm", mode_name="CW")
    assert cw.fields["rst_sent"] == "599"
    ft8 = entry.parse("ea7wm", mode_name="FT8")
    assert ft8.fields["rst_sent"] == "-10"


def test_named_fields_can_appear_anywhere():
    result = entry.parse("ea7wm,victor,grid=im76,tg=214", mode_name="DMR")
    assert result.fields["gridsquare"] == "IM76"
    assert result.fields["name"] == "Victor"
    assert result.digital["talkgroup"] == "214"


def test_named_field_is_not_overwritten_by_position():
    """`call=` set by name must win; the positional token must not duplicate it."""
    result = entry.parse("call=EA4ABC,victor", mode_name="SSB")
    assert result.fields["call"] == "EA4ABC"
    assert result.fields["name"] == "Victor"


def test_country_is_detected_from_the_prefix():
    assert entry.parse("dl2jkl", mode_name="SSB").fields["country"] == "Germany"


def test_missing_callsign_is_an_error():
    result = entry.parse(",victor,59", mode_name="SSB")
    assert not result.ok
    assert "indicativo" in result.error.lower()


def test_empty_line_is_an_error():
    assert not entry.parse("   ", mode_name="SSB").ok


def test_malformed_callsign_is_refused_by_default():
    """Strict validation is the default: a typo must not reach the log."""
    result = entry.parse("noesuncall", mode_name="SSB")
    assert not result.ok
    assert "largo" in result.error


@pytest.mark.parametrize(
    "line", ["ea77", "qwerty", "ea", "ea7-wm", "12345", "ea7wm."]
)
def test_strict_mode_refuses_typos(line):
    assert not entry.parse(line, mode_name="SSB").ok


@pytest.mark.parametrize(
    "line", ["ea7wm", "k1abc", "9a1aa", "3da0rs", "vp2e", "am500itu", "f/ea7wm/p"]
)
def test_strict_mode_accepts_real_callsigns(line):
    """Special event and portable calls must not be caught by the filter."""
    result = entry.parse(line, mode_name="SSB")
    assert result.ok, result.error


def test_trailing_bang_forces_an_unusual_callsign():
    result = entry.parse("bv100!,chen", mode_name="SSB")
    assert result.ok
    assert result.fields["call"] == "BV100"
    assert result.warnings


def test_forcing_a_correct_callsign_says_it_was_unnecessary():
    result = entry.parse("ea7wm!", mode_name="SSB")
    assert result.ok
    assert result.fields["call"] == "EA7WM"
    assert any("No hacía falta" in warning for warning in result.warnings)


def test_warn_mode_logs_the_contact_with_a_notice():
    result = entry.parse("noesuncall", mode_name="SSB", validation="warn")
    assert result.ok
    assert result.warnings


def test_validation_can_be_switched_off():
    result = entry.parse("noesuncall", mode_name="SSB", validation="off")
    assert result.ok
    assert not result.warnings


def test_extra_tokens_go_to_the_comment():
    result = entry.parse("ea7wm,v,59,57,Sevilla,nota,de,mas", mode_name="SSB")
    assert result.ok
    assert "de,mas" in str(result.fields["comment"])
    assert result.warnings


def test_unknown_named_field_warns_and_is_ignored():
    result = entry.parse("ea7wm,xyz=1", mode_name="SSB")
    assert result.ok
    assert "xyz" in result.warnings[0]


def test_band_and_frequency_stay_consistent():
    by_freq = entry.parse("ea7wm,freq=14.250", mode_name="SSB")
    assert by_freq.fields["freq_hz"] == 14_250_000
    assert by_freq.fields["band"] == "20m"

    by_band = entry.parse("ea7wm,banda=20m", mode_name="SSB")
    assert by_band.fields["band"] == "20m"
    assert by_band.fields["freq_hz"] == 14_250_000


def test_custom_field_order_and_separator():
    order = ("call", "rst_sent", "rst_rcvd", "name")
    result = entry.parse("ea7wm;59;57;Victor", field_order=order, separator=";", mode_name="SSB")
    assert result.fields["rst_sent"] == "59"
    assert result.fields["name"] == "Victor"


def test_commands_are_detected_and_split():
    assert entry.is_command("/banda 20m")
    command = entry.parse_command("/banda 20m")
    assert command.name == "banda"
    assert command.argument == "20m"
    assert entry.parse_command("/config").argument == ""
