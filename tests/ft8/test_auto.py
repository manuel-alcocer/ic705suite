"""The super-automatic mode: whom it calls first, how many times, and CQ when nobody
answers."""

import asyncio

import numpy as np
import pytest

from ic705suite import geo
from ic705suite.apps.ft8 import auto as auto_module
from ic705suite.apps.ft8 import jt9
from ic705suite.apps.ft8 import service as service_module
from ic705suite.apps.ft8.auto import Auto
from ic705suite.config import Config, load_config
from ic705suite.station.links import LocalLink
from tests.ft8.test_ft8 import short_turns, until
from tests.ft8.test_worked_ft8 import CTY


def cq(call, snr=-10, **marks):
    entity = geo.entity_for(call)
    return {"kind": "cq", "sender": call, "snr": snr, "message": f"CQ {call}",
            "country": entity.name if entity else "",
            "continent": entity.continent if entity else "", **marks}


def test_whom_it_calls_first(monkeypatch):
    table = geo.Cty(CTY)
    monkeypatch.setattr(geo, "cty", lambda: table)
    auto = Auto()
    auto.start()
    spain = cq("EA1ABC", dxcc="worked", dxcc_count=40)
    france = cq("F5ABC", dxcc="worked", dxcc_count=7)
    england = cq("G4XYZ", dxcc="worked", dxcc_count=2)
    japan = cq("JA1ZZZ", snr=-20, dxcc="new")
    pick = lambda *decodes: auto.pick(list(decodes), "EA7KLX", 1000.0)   # noqa: E731
    assert pick(spain, france, england, japan) is japan          # a new DXCC first
    assert pick(spain, france, england) is england                # then the least worked
    assert pick(spain) is spain                                   # our own country last
    assert pick(spain, cq("F5ABC", dxcc="new_band"), france)["dxcc"] == "new_band"
    louder = cq("G0AAA", snr=+5, dxcc="worked", dxcc_count=2)
    assert pick(england, louder) is louder                        # the same: the loudest
    # Never one worked on this band, nor a CQ meant for others, nor one just called.
    assert pick(cq("F5ABC", b4=True), spain) is spain
    assert pick(cq("DL1ABC", cq_to="DX"), spain) is spain         # Europe to Europe
    assert pick(cq("JA1ZZZ", cq_to="DX", dxcc="new")) is not None
    assert pick(cq("F5ABC", cq_to="POTA", dxcc="new"), spain) is spain
    assert pick(cq("F5ABC", cq_to="EU", dxcc="new"))["sender"] == "F5ABC"
    auto.missed_one("G4XYZ", 1000.0)
    assert pick(england, spain) is spain
    assert auto.pick([england], "EA7KLX", 1000.0 + auto_module.RETRY_AFTER + 1) is england
    # A station calling us comes before anything else, worked before or not.
    to_us = {"kind": "call", "sender": "DL1ABC", "to": "EA7KLX", "grid": "JO62", "snr": -15,
             "b4": True}
    assert pick(japan, to_us) is to_us
    assert pick({**to_us, "grid": "", "end": "RR73"}, spain) is spain


def test_when_it_calls_cq():
    auto = Auto()
    auto.start()
    for n in range(auto_module.STATIONS):
        assert not auto.wants_cq()
        auto.missed_one(f"F{n}ABC", 0.0)
    assert auto.wants_cq()
    auto.cq_done()
    assert not auto.wants_cq() and auto.phase == "hunt"
    auto.quiet = auto_module.QUIET_CYCLES
    assert auto.wants_cq()
    auto.missed = 3
    auto.worked_one()
    assert (auto.missed, auto.quiet) == (0, 0)


def test_the_mode_on_air(tmp_path, monkeypatch):
    """Two stations calling CQ that never answer: each called twice, then CQ twice, then
    answering again; a third that answers is worked, and the mode goes on until halted."""
    air = short_turns(monkeypatch)
    monkeypatch.setattr(service_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(service_module, "TRIES_EACH", 2)
    monkeypatch.setattr(service_module, "CQS", 2)
    monkeypatch.setattr(auto_module, "STATIONS", 2)
    table = geo.Cty(CTY)
    monkeypatch.setattr(geo, "cty", lambda: table)
    air["says"] = ""

    def decode(program, wav, depth=3, timeout=30.0, band=None):
        stamp = wav.stem.split("_")[1]
        lines = [f"{stamp} -12  0.1  900 ~  CQ F5ABC JN12",
                 f"{stamp}  -8  0.1 1700 ~  CQ G4XYZ IO91"]
        if air["says"]:
            lines.append(f"{stamp} -11  0.1 1200 ~  {air['says']}")
        return jt9.parse("\n".join(lines))

    sent: list[str] = []

    def audio(program, message, frequency, rate=12_000):
        sent.append(message)
        if message.startswith("CQ "):
            air["says"] = "EA7KLX DL1ABC JO62"            # someone answers our CQ
        answers = {"DL1ABC EA7KLX -11": "EA7KLX DL1ABC R-07",
                   "DL1ABC EA7KLX RR73": "EA7KLX DL1ABC 73"}
        air["says"] = answers.get(message, air["says"])
        return np.zeros(int(rate * 0.2), np.float32)

    monkeypatch.setattr(service_module.jt9, "decode", decode)
    monkeypatch.setattr(service_module.encode, "audio", audio)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        ft8_auto=True,
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        said: list[str] = []
        link.attach(lambda e: e.get("type") == "info" and said.append(e.get("text", "")))
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            state = await link.call("ft8.auto", on=True)
            assert state["on"] and state["phase"] == "hunt"
            await until(lambda: any("complete" in s for s in said), 40)
            calls = [m for m in sent if not m.startswith("DL1ABC")]
            # No logbook here: the loudest first (England), then France; twice each.
            assert calls[:4] == ["G4XYZ EA7KLX IM77"] * 2 + ["F5ABC EA7KLX IM77"] * 2
            assert calls[4] == "CQ EA7KLX IM77"
            assert "DL1ABC EA7KLX RR73" in sent
            assert any(s.startswith("FT8 auto: calling G4XYZ") for s in said)
            assert any(s == "FT8 auto: calling CQ (2 times)" for s in said)
            assert (await link.call("ft8.state"))["auto"]["on"]       # still on
            await link.call("ft8.halt")
            assert not (await link.call("ft8.state"))["auto"]["on"]
            count = len(sent)
            await asyncio.sleep(2.5)
            assert len(sent) == count and not link.core.station.transport.ptt
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_mode_needs_transmission(tmp_path):
    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", ft8_auto=True,
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        try:
            await link.call("ft8.start")
            reply = await link.call("ft8.auto", on=True)
            assert reply.get("error")
            assert not (await link.call("ft8.state"))["auto"]["on"]
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_mode_only_where_the_configuration_allows_it(tmp_path):
    """Without [ft8] super_auto the core refuses it and says so in its state (the clients
    hide its button and key)."""
    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            reply = await link.call("ft8.auto", on=True)
            assert "not available" in reply.get("error", "")
            state = (await link.call("ft8.state"))["auto"]
            assert not state["on"] and not state["allowed"]
        finally:
            await link.close()

    asyncio.run(scenario())


def test_super_auto_is_read_from_the_configuration(tmp_path):
    config_file = tmp_path / "config.toml"
    assert not load_config(config_file).ft8_auto
    config_file.write_text("[ft8]\nsuper_auto = true\n", encoding="utf-8")
    assert load_config(config_file).ft8_auto


@pytest.mark.skipif(jt9.find_jt9() is None, reason="WSJT-X's jt9 is not installed")
def test_the_mode_needs_the_radio_in_a_data_mode(tmp_path):
    """Not turned on with the radio in USB: the reason is said at once, not on its first
    call."""
    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        ft8_auto=True,
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        try:
            await link.call("ft8.start")
            reply = await link.call("ft8.auto", on=True)
            assert "data mode" in reply.get("error", "")
            assert not (await link.call("ft8.state"))["auto"]["on"]
            await link.call("ft8.band", band="20m")             # USB-D
            assert (await link.call("ft8.auto", on=True))["on"]
        finally:
            await link.close()

    asyncio.run(scenario())


def test_a_busy_station_and_an_ignored_country(tmp_path, monkeypatch):
    """France ignored: its CQ is not answered, nor its call to our CQ. England is called; it
    answers another station: left at once for the next (nobody: CQ)."""
    air = short_turns(monkeypatch)
    monkeypatch.setattr(service_module, "data_dir", lambda: tmp_path)
    table = geo.Cty(CTY)
    monkeypatch.setattr(geo, "cty", lambda: table)
    air["says"] = ""

    def decode(program, wav, depth=3, timeout=30.0, band=None):
        stamp = wav.stem.split("_")[1]
        lines = [f"{stamp} -12  0.1  900 ~  CQ F5ABC JN12"]
        if air["says"]:
            lines.append(f"{stamp}  -8  0.1 1700 ~  {air['says']}")
        return jt9.parse("\n".join(lines))

    sent: list[str] = []

    def audio(program, message, frequency, rate=12_000):
        sent.append(message)
        if message == "G4XYZ EA7KLX IM77":
            air["says"] = "EA1AAA G4XYZ -05"              # England works someone else
        elif message.startswith("CQ "):
            air["says"] = "EA7KLX F5ABC JN12"             # France calls us: ignored
        return np.zeros(int(rate * 0.2), np.float32)

    monkeypatch.setattr(service_module.jt9, "decode", decode)
    monkeypatch.setattr(service_module.encode, "audio", audio)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        ft8_auto=True,
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        said: list[str] = []
        link.attach(lambda e: e.get("type") == "info" and said.append(e.get("text", "")))
        try:
            reply = await link.call("ft8.ignore", country="France")
            assert reply == {"calls": [], "countries": ["France"]}
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            air["says"] = "CQ G4XYZ IO91"
            await link.call("ft8.auto", on=True)
            await until(lambda: any("is working EA1AAA" in s for s in said), 20)
            await until(lambda: any(m.startswith("CQ ") for m in sent), 20)
            await until(lambda: sent.count("CQ EA7KLX IM77") >= 3, 20)
            assert not any(m.startswith("F5ABC") for m in sent)       # never France
            assert sent.count("G4XYZ EA7KLX IM77") < 3                 # left early
            state = (await link.call("ft8.state"))
            assert state["ignore"]["countries"] == ["France"]
            assert state["tx"]["step"] == "cq"                         # not answered
            await link.call("ft8.halt")
        finally:
            await link.close()
        # Kept for the next time.
        link = LocalLink(None, config)
        await link.connect()
        try:
            assert (await link.call("ft8.state"))["ignore"]["countries"] == ["France"]
            assert (await link.call("ft8.ignore", country="France", on=False))["countries"] == []
        finally:
            await link.close()

    asyncio.run(scenario())


def test_ignored_lines_are_marked():
    from ic705suite.apps.ft8.app import mark

    ignore = {"calls": ["DL1ABC"], "countries": ["France"]}
    assert mark({"sender": "DL1ABC/P", "dxcc": "new"}, ignore)[0] == "IGN"
    assert mark({"sender": "F5ABC", "country": "France", "b4": True}, ignore)[0] == "IGN"
    assert mark({"sender": "G4XYZ", "country": "England", "b4": True}, ignore)[0] == "B4"
