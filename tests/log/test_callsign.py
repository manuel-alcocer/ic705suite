"""Callsign normalisation and country lookup."""

from __future__ import annotations

import pytest

from ic705suite.apps.log.core import callsign


@pytest.mark.parametrize(
    ("raw", "base"),
    [
        ("ea7wm", "EA7WM"),
        ("  ea7wm  ", "EA7WM"),
        ("F/EA7WM/P", "EA7WM"),
        ("VE3ZZZ/M", "VE3ZZZ"),
        ("DL2JKL/QRP", "DL2JKL"),
    ],
)
def test_base_call(raw, base):
    assert callsign.base_call(raw) == base


@pytest.mark.parametrize(
    ("raw", "valid"),
    [("EA7WM", True), ("9A1AA", True), ("K1ABC", True), ("BAD", False), ("", False)],
)
def test_is_valid(raw, valid):
    assert callsign.is_valid(raw) is valid


@pytest.mark.parametrize(
    ("raw", "country"),
    [
        ("EA7WM", "Spain"),
        ("EA8ABC", "Canary Islands"),
        ("EA9XX", "Ceuta & Melilla"),
        ("DL2JKL", "Germany"),
        ("9A1AA", "Croatia"),
        ("ZZ9ZZ", ""),
    ],
)
def test_country_for(raw, country):
    assert callsign.country_for(raw) == country


def test_portable_prefix_wins_over_home_call():
    """F/EA7WM is a Spaniard operating in France, so the entity is France."""
    assert callsign.country_for("F/EA7WM") == "France"


@pytest.mark.parametrize(
    "raw",
    ["EA7WM", "K1ABC", "9A1AA", "3DA0RS", "VP2E", "AM500ITU", "F/EA7WM/P", "VE3ZZZ/M"],
)
def test_validate_accepts_real_callsigns(raw):
    """Special event and portable calls are legitimate and must pass."""
    assert callsign.validate(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected_reason"),
    [
        ("", "Falta"),
        ("EA", "corto"),
        ("EA7WMXYZQ", "largo"),
        ("QWERTY", "número"),
        ("EA7-WM", "no permitidos"),
        ("EA7WM.", "no permitidos"),
        ("EA77", "forma de indicativo"),
    ],
)
def test_validate_explains_what_is_wrong(raw, expected_reason):
    """The message must name the problem, not just say 'invalid'."""
    problem = callsign.validate(raw)
    assert problem is not None
    assert expected_reason in problem


def test_override_marker_is_stripped():
    assert callsign.strip_override("bv100!") == ("bv100", True)
    assert callsign.strip_override("ea7wm") == ("ea7wm", False)
