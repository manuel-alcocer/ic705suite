"""ic705suite --logbook-json: what the Plasma applet reads."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ic705suite import logbook_summary
from tests.log.test_services import log_line


def config():
    return SimpleNamespace(connection="lan", connect="", client_token="",
                           logbook_database="", logbook_password_file="", callsign="EA7KLX")


def answer(capsys, *words):
    assert logbook_summary.run(config(), list(words)) == 0
    return json.loads(capsys.readouterr().out)


def test_counters_and_latest_first(state, capsys):
    log_line("ea1aaa", state)                       # 40m SSB
    state.set_band("20m")
    state.set_mode("CW")
    log_line("ea2bbb", state)
    got = answer(capsys)
    assert got["ok"] and got["source"] == "local" and got["callsign"] == "EA7KLX"
    assert got["stats"]["total"] == 2
    assert [qso["call"] for qso in got["qsos"]] == ["EA2BBB", "EA1AAA"]
    assert got["bands"] == ["40m", "20m"] and got["modes"] == ["CW", "SSB"]


def test_by_band_and_mode(state, capsys):
    log_line("ea1aaa", state)
    state.set_band("20m")
    log_line("ea2bbb", state)
    got = answer(capsys, "20m", "ssb")
    assert got["filter"] == {"band": "20m", "mode": "SSB"}
    assert [qso["call"] for qso in got["qsos"]] == ["EA2BBB"]


def test_a_word_neither_band_nor_mode(state, capsys):
    got = answer(capsys, "xyz")
    assert not got["ok"] and "xyz" in got["error"]
