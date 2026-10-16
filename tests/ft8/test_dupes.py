"""Dupes: a station worked on a band is not worked there again, unless allowed."""

import asyncio
from types import SimpleNamespace

import pytest

from ic705suite.apps.ft8 import service as service_module
from ic705suite.apps.ft8.service import Ft8Error, Ft8Service, parity
from ic705suite.config import Config
from ic705suite.worked import WorkedIndex

DIAL_20M = 14_074_000


class Hub:
    def __init__(self):
        self.said = []

    def info(self, msgid, **args):
        self.said.append(msgid.format(**args))

    error = info

    def emit(self, event):
        pass


def make(tmp_path, monkeypatch) -> Ft8Service:
    monkeypatch.setattr(service_module, "data_dir", lambda: tmp_path)
    station = SimpleNamespace(rig=SimpleNamespace(state=SimpleNamespace(frequency=DIAL_20M)))
    service = Ft8Service(station, Config(callsign="EA7KLX", locator="IM77", tx_enabled=True),
                         Hub())
    service.on, service.coder = True, "ft8code"
    return service


def calling_us(call, b4=False):
    return {"kind": "call", "sender": call, "to": "EA7KLX", "grid": "JO62", "snr": -11,
            "message": f"EA7KLX {call} JO62", "b4": b4}


def test_not_allowed_by_default_and_kept(tmp_path, monkeypatch):
    service = make(tmp_path, monkeypatch)
    assert not service.allow_dupes and service.state()["dupes"] is False
    assert service.dupe(calling_us("DL1ABC", b4=True))
    assert not service.dupe(calling_us("DL1ABC"))
    assert asyncio.run(service.call("dupes", {"on": True})) == {"allow": True}
    assert not service.dupe(calling_us("DL1ABC", b4=True))
    assert make(tmp_path, monkeypatch).allow_dupes                  # kept for next time


def test_a_qso_logged_here_counts_before_the_logbook_is_read(tmp_path, monkeypatch):
    service = make(tmp_path, monkeypatch)
    service._worked_now.add(("DL1ABC", "20m"))
    assert service.dupe(calling_us("DL1ABC/P"))
    service.station.rig.state.frequency = 7_074_000                 # another band
    assert not service.dupe(calling_us("DL1ABC"))


def test_the_worked_index_marks_b4_on_the_band(tmp_path, monkeypatch):
    service = make(tmp_path, monkeypatch)
    service.worked = WorkedIndex()
    service.worked.add("DL1ABC", "20m", "JO62", "Germany", False)
    entry = calling_us("DL1ABC")
    del entry["b4"]
    service._place(entry)
    assert entry["b4"] and service.dupe(entry)


def test_not_answered_by_hand(tmp_path, monkeypatch):
    service = make(tmp_path, monkeypatch)
    with pytest.raises(Ft8Error, match="already worked on this band"):
        asyncio.run(service.reply(calling_us("DL1ABC", b4=True), 0.0))
    service.allow_dupes = True
    assert asyncio.run(service.reply(calling_us("DL1ABC", b4=True), 0.0)) == \
        "DL1ABC EA7KLX -11"


def test_not_answered_when_it_answers_our_cq(tmp_path, monkeypatch):
    service = make(tmp_path, monkeypatch)
    asyncio.run(service.call_cq())
    start = 15.0 * (1 - service.tx_parity)                          # their turn
    assert parity(start) != service.tx_parity
    service._hear(start, [calling_us("DL1ABC", b4=True)])
    assert service.qso.step == "cq" and not service.qso.them
    service._hear(start, [calling_us("G4XYZ")])
    assert service.qso.them == "G4XYZ"


def test_the_auto_mode_leaves_a_dupe_calling_us(tmp_path, monkeypatch):
    service = make(tmp_path, monkeypatch)
    service.auto.start()
    service.history.append({"start": 0.0, "decodes": [calling_us("DL1ABC", b4=True)]})
    asyncio.run(service._auto_turn(0.0))
    assert not service.tx_on
    service.allow_dupes = True
    asyncio.run(service._auto_turn(0.0))
    assert service.tx_on and service.qso.them == "DL1ABC"
