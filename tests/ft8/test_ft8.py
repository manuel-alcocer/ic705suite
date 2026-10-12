"""FT8: what jt9 prints and what the messages say, the audio it is given, the core's cycles
(with the simulator and short cycles) and the screens."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import numpy as np
import pytest

from ic705suite.apps.ft8 import jt9
from ic705suite.apps.ft8 import service as service_module
from ic705suite.apps.ft8.messages import base, parse
from ic705suite.config import Config
from ic705suite.station.links import LocalLink

DATA = Path(__file__).parent / "data"
RECORDING = DATA / "20m_261011_170615.wav"     # 20 m, 11 Oct 2026 17:06:15 UTC, the IC-705

OUTPUT = """\
 EOF on input file ft8_261011_170615.wav
170615 -11  0.0 1497 ~  OE8PPL IK1MJK JN35
170615  -6  0.3 2454 ~  CQ E70A JN94
170615 -14 -0.5 1769 ~  SP2KW EA8CVR RR73
170615  -4  0.3 1131 ~  <...> F1PPH R-09
170615  -8  0.1  979 ~  CQ S57ACD JN75                      ? a1
<DecodeFinished>   0  28        0
"""


def test_what_jt9_prints():
    decodes = jt9.parse(OUTPUT)
    assert len(decodes) == 5
    first = decodes[0]
    assert (first.time, first.snr, first.dt, first.freq, first.message) == \
        ("170615", -11, 0.0, 1497, "OE8PPL IK1MJK JN35")
    assert decodes[2].dt == -0.5 and decodes[4].doubtful and not decodes[0].doubtful
    assert decodes[4].message == "CQ S57ACD JN75"


def test_what_the_messages_say():
    cq = parse("CQ E70A JN94")
    assert (cq.kind, cq.sender, cq.grid, cq.to) == ("cq", "E70A", "JN94", "")
    dx = parse("CQ DX EA8CVR IL18")
    assert (dx.kind, dx.cq_to, dx.sender, dx.grid) == ("cq", "DX", "EA8CVR", "IL18")
    assert parse("CQ RU DM2DXA JO64").cq_to == "RU"
    call = parse("OE8PPL IK1MJK JN35")
    assert (call.kind, call.to, call.sender, call.grid) == ("call", "OE8PPL", "IK1MJK", "JN35")
    assert parse("EA7KLX DL1ABC -12").report == "-12"
    assert parse("EA7KLX DL1ABC R-07").report == "R-07"
    assert parse("SP2KW EA8CVR RR73").end == "RR73"
    assert parse("Z21ML ON6NL 73").end == "73"
    hashed = parse("<...> F1PPH R-09")
    assert hashed.kind == "call" and hashed.sender == "F1PPH" and hashed.to == "..."
    assert parse("TNX FER QSO").kind == "free"
    assert parse("").kind == "free"
    assert parse("EA7KLX/P DL1ABC -10").for_me("EA7KLX")
    assert not parse("EA7KLY DL1ABC -10").for_me("EA7KLX")
    assert base("<EA7KLX/P>") == "EA7KLX" and base("F/EA7KLX") == "EA7KLX"


def test_the_audio_goes_at_12_khz():
    rate = 8000
    t = np.arange(rate * 2) / rate
    tone = np.sin(2 * np.pi * 1500 * t).astype(np.float32)
    up = jt9.resample(tone, rate)
    assert len(up) == 24_000
    peak = np.argmax(np.abs(np.fft.rfft(up))) * jt9.RATE / len(up)
    assert abs(peak - 1500) < 1
    assert np.allclose(jt9.resample(tone, 12_000), tone)


def test_the_wav_of_a_cycle(tmp_path):
    import calendar

    name = jt9.cycle_name(calendar.timegm((2026, 10, 11, 17, 6, 15, 0, 0, 0)))
    assert name == "261011_170615.wav"
    path = tmp_path / name
    jt9.write_wav(path, np.random.default_rng(1).standard_normal(8000).astype(np.float32),
                  8000)
    with wave.open(str(path)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (12000, 1, 2)
        samples = np.frombuffer(wav.readframes(wav.getnframes()), "<i2")
    assert len(samples) == 12000 and 0.6 < np.abs(samples).max() / 32767 <= 0.71


@pytest.mark.skipif(jt9.find_jt9() is None, reason="WSJT-X's jt9 is not installed")
def test_jt9_on_a_cycle_heard_on_20m(tmp_path):
    """The recording of the IC-705 over WiFi, at 8 kHz as the radio gives it."""
    with wave.open(str(RECORDING)) as wav:
        samples = np.frombuffer(wav.readframes(wav.getnframes()), "<i2").astype(np.float32)
    heard = jt9.resample(samples / 32768, 12_000, 8000)
    path = tmp_path / RECORDING.name.removeprefix("20m_")
    jt9.write_wav(path, heard, 8000)
    decodes = jt9.decode(jt9.find_jt9(), path, depth=1)
    assert len(decodes) >= 20
    assert "CQ E70A JN94" in {d.message for d in decodes}


# ---- the core's service, with short cycles -------------------------------------------------

def fake_jt9(monkeypatch, slow: dict[int, float] | None = None) -> list[int]:
    """jt9 replaced: each depth "decodes" more (as the real one does) and takes its time."""
    depths: list[int] = []
    monkeypatch.setattr(service_module.jt9, "find_jt9", lambda configured="": "jt9")

    def decode(program, wav, depth=3, timeout=30.0, band=None):
        import time

        depths.append(depth)
        time.sleep((slow or {}).get(depth, 0))
        stamp = wav.stem.split("_")[1]
        found = [f"{stamp} -10  0.1 1200 ~  CQ EA1ABC IN73",
                 f"{stamp}  -3  0.2 1500 ~  EA7KLX DL1ABC -12",
                 f"{stamp} -15  0.0  800 ~  G4XYZ F5ABC JN12"]
        if depth >= 2:
            found.append(f"{stamp} -22  0.3 2000 ~  CQ DX JA1ZZZ PM95")
        return jt9.parse("\n".join(found))

    monkeypatch.setattr(service_module.jt9, "decode", decode)
    monkeypatch.setattr(service_module, "CYCLE", 1)
    return depths


async def sim_link(tmp_path) -> LocalLink:
    config = Config(connection="sim", swr_web=False, callsign="EA7KLX",
                    database_url=f"sqlite:///{tmp_path / 'suite.db'}")
    link = LocalLink(None, config)
    await link.connect()
    return link


async def until(condition, timeout: float = 8.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition never met")


def test_cycles_decoded_by_the_core(tmp_path, monkeypatch):
    depths = fake_jt9(monkeypatch)

    async def scenario():
        link = await sim_link(tmp_path)
        cycles: list[dict] = []
        link.attach(lambda e: e.get("type") == "ft8.cycle" and cycles.append(e))
        try:
            assert (await link.call("ft8.band", band="20m")) == {"dial": 14_074_000}
            sim = link.core.station.transport
            assert sim.frequency == 14_074_000 and sim.data_mode == (1, 1)
            assert (await link.call("ft8.start")) == {"ok": True}
            await until(lambda: len([c for c in cycles if c["pass"] == "quick"]) >= 2)
            first = cycles[0]
            assert first["depth"] == 1 and first["pass"] == "quick"
            assert {d["message"] for d in first["decodes"]} == {
                "CQ EA1ABC IN73", "EA7KLX DL1ABC -12", "G4XYZ F5ABC JN12"}
            cq = next(d for d in first["decodes"] if d["kind"] == "cq")
            assert (cq["sender"], cq["grid"], cq["snr"]) == ("EA1ABC", "IN73", -10)
            # The deeper pass, done in time, adds only what the first one missed.
            await until(lambda: any(c["pass"] == "deep" for c in cycles))
            deep = next(c for c in cycles if c["pass"] == "deep")
            assert [d["message"] for d in deep["decodes"]] == ["CQ DX JA1ZZZ PM95"]
            assert deep["depth"] == 3
            history = (await link.call("ft8.history"))["cycles"]
            merged = next(c for c in history if c["start"] == deep["start"])
            assert len(merged["decodes"]) == 4
            state = await link.call("ft8.state")
            assert state["on"] and state["band"] == "20m" and state["ft8_dial"]
            # On a fast machine every depth is quick: one deep pass from the start.
            assert (await link.call("ft8.state"))["depths"] == [3, None]
            assert 3 in depths
            assert (await link.call("ft8.stop")) == {"ok": True}
            assert not link.core.ft8.on
            assert link.core.ft8._audio not in link.core.station._audio
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_passes_fit_the_machine(monkeypatch):
    service = service_module.Ft8Service(None, Config(), None)
    assert service.depths() == (1, 3)                       # not known yet: try deep
    service.timings = {1: 2.8, 3: 22.8}                     # a Raspberry Pi 4
    assert service.depths() == (1, 2)
    service.timings = {1: 2.8, 2: 8.8, 3: 22.8}
    assert service.depths() == (1, 2)
    service.timings = {1: 3.6, 2: 14.0, 3: 22.8}            # busier: no time for more
    assert service.depths() == (1, None)
    service.timings = {1: 0.3, 2: 0.6, 3: 1.1}              # a desktop computer
    assert service.depths() == (3, None)
    service.config.ft8_depth = 2
    assert service.depths() == (2, None)


def test_without_jt9_or_a_radio(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module.jt9, "find_jt9", lambda configured="": None)

    async def scenario():
        link = await sim_link(tmp_path)
        try:
            reply = await link.call("ft8.start")
            assert reply["msgid"].startswith("WSJT-X's jt9 is not installed")
            assert not link.core.ft8.on
            assert (await link.call("ft8.band", band="11m"))["msgid"] == \
                "No FT8 frequency for {band}"
        finally:
            await link.close()

    asyncio.run(scenario())


# ---- the screens -------------------------------------------------------------------------------

def test_the_terminal_screen(tmp_path, monkeypatch):
    from textual.widgets import DataTable

    from ic705suite.apps.ft8.app import STYLE_CQ, STYLE_ME, Ft8App

    fake_jt9(monkeypatch)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        app = Ft8App(link, owns_link=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await until(lambda: app.cycles)
            table = app.query_one(DataTable)
            await until(lambda: table.row_count >= 4)
            styles = {str(table.get_row_at(r)[4]): table.get_row_at(r)[4].style
                      for r in range(table.row_count)}
            assert styles["CQ EA1ABC IN73"] == STYLE_CQ
            assert styles["EA7KLX DL1ABC -12"] == STYLE_ME          # sent to us
            assert link.core.ft8.on and app.started_here
            await pilot.press("c")                                   # the CQs (and ours)
            messages = {str(table.get_row_at(r)[4]) for r in range(table.row_count)}
            assert "G4XYZ F5ABC JN12" not in messages and "CQ EA1ABC IN73" in messages
            await pilot.press("c")
            sim = link.core.station.transport
            band = service_module.bands.band_for(sim.frequency).name
            await pilot.press("right")              # not on FT8 yet: this band's frequency
            await until(lambda: sim.frequency == service_module.DIALS[band])
            await until(lambda: app.state.get("ft8_dial"))
            following = list(service_module.DIALS)[list(service_module.DIALS).index(band) + 1]
            await pilot.press("right")                               # then the next band
            await until(lambda: sim.frequency == service_module.DIALS[following])
            await pilot.press("s")
            await until(lambda: not link.core.ft8.on and not app.state.get("on"))
            await pilot.press("s")
            await until(lambda: link.core.ft8.on)
            ft8, rig = link.core.ft8, link.core.station.rig
            await until(lambda: app.state.get("tx", {}).get("freq") == 1500)
            await pilot.press("right_square_bracket")                # TX 25 Hz up
            await until(lambda: ft8.tx_freq == 1525)
            await until(lambda: app.state["tx"]["level"] == 0.5)
            await pilot.press("minus")                               # the audio 5 % down
            await until(lambda: ft8.tx_level == 0.45)
            await until(lambda: app.rig.get("controls", {}).get("power") is not None)
            before = app.rig["controls"]["power"]
            await pilot.press("comma")                               # a watt less
            await until(lambda: rig.state.controls.get("power") == max(0, before - 10))
            await pilot.press("escape")
            await pilot.pause(0.3)
        assert not link.core.ft8.on                                  # stopped on leaving

    asyncio.run(scenario())


# ---- transmitting ------------------------------------------------------------------------------

def short_turns(monkeypatch) -> dict:
    """1-second cycles, 0.2-second messages, and the other station (DL1ABC) scripted: what it
    sends in its turns follows what it heard from us, as WSJT-X would."""
    monkeypatch.setattr(service_module, "CYCLE", 1)
    for name, value in (("EARLY", 0.75), ("START", 0.1), ("LATE", 0.6), ("LEAD", 0.0),
                        ("TAIL", 0.02), ("PIECE", 0.02)):
        monkeypatch.setattr(service_module, name, value)
    monkeypatch.setattr(service_module.jt9, "find_jt9", lambda configured="": "jt9")
    monkeypatch.setattr(service_module.encode, "find_ft8code", lambda configured="": "ft8code")
    air = {"sent": [], "says": "CQ DL1ABC JO62"}

    def audio(program, message, frequency, rate=12_000):
        air["sent"].append(message)
        answers = {"DL1ABC EA7KLX IM77": "EA7KLX DL1ABC -07",
                   "DL1ABC EA7KLX R-11": "EA7KLX DL1ABC RR73"}
        air["says"] = answers.get(message, air["says"])
        return np.zeros(int(rate * 0.2), np.float32)

    def decode(program, wav, depth=3, timeout=30.0, band=None):
        stamp = wav.stem.split("_")[1]
        return jt9.parse(f"{stamp} -11  0.1 1200 ~  {air['says']}")

    monkeypatch.setattr(service_module.encode, "audio", audio)
    monkeypatch.setattr(service_module.jt9, "decode", decode)
    return air


def test_a_qso_answering_a_cq(tmp_path, monkeypatch):
    short_turns(monkeypatch)
    monkeypatch.setattr(service_module, "data_dir", lambda: tmp_path)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        events: list[dict] = []
        link.attach(lambda e: e.get("type") in ("ft8.tx", "info", "ft8.cycle", "ft8.qso")
                    and events.append(e))
        sim = link.core.station.transport
        keyed: list[bool] = []
        sim.on_tx_audio = lambda pcm, ptt: keyed.append(ptt)
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            await until(lambda: any(e.get("type") == "ft8.cycle" and e["decodes"]
                                    for e in events))
            cycle = next(e for e in events if e.get("type") == "ft8.cycle" and e["decodes"])
            cq = cycle["decodes"][0]
            reply = await link.call("ft8.reply", decode=cq, start=cycle["start"])
            assert reply == {"message": "DL1ABC EA7KLX IM77"}
            state = (await link.call("ft8.state"))["tx"]
            assert state["on"] and state["parity"] == 1 - service_module.parity(cycle["start"])
            await until(lambda: any("complete" in e.get("text", "") for e in events), 15)
            sent = [e["message"] for e in events if e.get("type") == "ft8.tx"]
            assert sent[0] == "DL1ABC EA7KLX IM77"
            assert "DL1ABC EA7KLX R-11" in sent and sent[-1] == "DL1ABC EA7KLX 73"
            assert keyed and all(keyed)                   # audio only while keyed
            assert not sim.ptt                            # released
            done = next(e for e in events if "complete" in e.get("text", ""))
            assert done["text"] == "FT8 QSO with DL1ABC complete (reports -11 and -07)"
            # Logged: ft8_log.adi, and an ft8.qso event for the screens' logbooks.
            qso = next(e for e in events if e.get("type") == "ft8.qso")["qso"]
            assert {k: qso[k] for k in ("CALL", "GRIDSQUARE", "MODE", "RST_SENT", "RST_RCVD",
                                        "BAND", "FREQ", "STATION_CALLSIGN", "MY_GRIDSQUARE")} \
                == {"CALL": "DL1ABC", "GRIDSQUARE": "JO62", "MODE": "FT8", "RST_SENT": "-11",
                    "RST_RCVD": "-07", "BAND": "20M", "FREQ": "14.075500",
                    "STATION_CALLSIGN": "EA7KLX", "MY_GRIDSQUARE": "IM77"}
            written = (tmp_path / "ft8" / "ft8_log.adi").read_text()
            assert written.startswith("ic705suite FT8 log") and "<EOH>" in written
            assert "<CALL:6>DL1ABC" in written and written.count("<EOR>") == 1
            log = await link.call("ft8.log")
            assert log["qsos"][0]["CALL"] == "DL1ABC" and "<CALL:6>DL1ABC" in log["adif"]
            assert not (await link.call("ft8.state"))["tx"]["on"]
            # Every message of a turn starts in it, on our side (odd or even) only.
            starts = [e["start"] for e in events if e.get("type") == "ft8.tx"]
            assert {service_module.parity(s) for s in starts} == {state["parity"]}
        finally:
            await link.close()

    asyncio.run(scenario())


def test_calling_cq_and_halting(tmp_path, monkeypatch):
    air = short_turns(monkeypatch)
    air["says"] = "G4XYZ F5ABC JN12"                     # nobody for us

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            assert (await link.call("ft8.txfreq", freq=1800)) == {"freq": 1800}
            assert (await link.call("ft8.cq")) == {"message": "CQ EA7KLX IM77"}
            await until(lambda: len(air["sent"]) >= 2)
            assert air["sent"][:2] == ["CQ EA7KLX IM77"] * 2
            await link.call("ft8.halt")
            count = len(air["sent"])
            await asyncio.sleep(2.5)
            assert len(air["sent"]) == count and not link.core.station.transport.ptt
        finally:
            await link.close()

    asyncio.run(scenario())


def test_what_stops_a_transmission(tmp_path, monkeypatch):
    short_turns(monkeypatch)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=False, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        texts: list[str] = []
        link.attach(lambda e: e.get("type") == "info" and texts.append(e["text"]))
        try:
            assert (await link.call("ft8.cq"))["msgid"] == "FT8 is stopped: S starts it"
            await link.call("ft8.start")
            assert (await link.call("ft8.cq"))["msgid"].startswith("Transmission is off")
            # Allowed to transmit, but not in a data mode: refused at the first turn.
            link.core.config.tx_enabled = True
            link.core.station.rig.tx_enabled = True
            assert "message" in await link.call("ft8.cq")
            await until(lambda: any("not in a data mode" in t for t in texts))
            assert not link.core.ft8.tx_on and not link.core.station.transport.ptt
        finally:
            await link.close()

    asyncio.run(scenario())


def test_a_qso_from_the_terminal(tmp_path, monkeypatch):
    from ic705suite.apps.ft8.app import STYLE_TX, Ft8App

    short_turns(monkeypatch)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        app = Ft8App(link, owns_link=True)
        async with app.run_test(size=(140, 40)) as pilot:
            await until(lambda: app.state.get("on"))
            await pilot.press("right")                         # onto an FT8 frequency
            await until(lambda: app.state.get("ft8_dial"))
            await until(lambda: any(d and d.get("message") == "CQ DL1ABC JO62"
                                    for _s, d, _c, _st in app.lines))
            row = next(i for i, (_s, d, _c, _st) in enumerate(app.lines)
                       if d and d.get("message") == "CQ DL1ABC JO62")
            from textual.widgets import DataTable

            app.query_one(DataTable).move_cursor(row=row)
            await pilot.press("enter")
            await until(lambda: any(cells[4] == "DL1ABC EA7KLX 73"
                                    for _s, _d, cells, _st in app.lines), 15)
            await until(lambda: not (app.state.get("tx") or {}).get("on"))
            assert any(style == STYLE_TX and cells[4] == "DL1ABC EA7KLX 73"
                       for _s, _d, cells, style in app.lines)
            qso_area = app.query_one("#qso", DataTable)
            await until(lambda: "100% · QSO with DL1ABC complete" in str(
                qso_area.border_subtitle))
            await pilot.press("q")                              # and now our own CQ
            await until(lambda: (app.state.get("tx") or {}).get("message") ==
                        "CQ EA7KLX IM77")
            await until(lambda: "% · CQ" in str(qso_area.border_subtitle)
                        or "% · Calling CQ" in str(qso_area.border_subtitle))
            await pilot.press("h")
            await until(lambda: not (app.state.get("tx") or {}).get("on"))
            await pilot.press("escape")
            await pilot.pause(0.3)

    asyncio.run(scenario())



def test_the_adif_record():
    import calendar

    from ic705suite.apps.ft8 import adif

    began = calendar.timegm((2026, 10, 11, 18, 1, 15, 0, 0, 0))
    fields = adif.record(call="DL1ABC", grid="JO62", sent="-11", received="-07", began=began,
                         ended=began + 45, dial=14_074_000, audio=1500, mycall="EA7KLX",
                         mygrid="IM77", power_w=5.0)
    assert fields["QSO_DATE"] == "20261011" and fields["TIME_ON"] == "180115"
    assert fields["TIME_OFF"] == "180200" and fields["FREQ"] == "14.075500"
    assert fields["TX_PWR"] == "5" and fields["BAND"] == "20M"
    text = adif.text(fields)
    assert text.startswith("<CALL:6>DL1ABC <GRIDSQUARE:4>JO62 <MODE:3>FT8")
    assert text.endswith("<EOR>\n")
    assert "GRIDSQUARE" not in adif.text(adif.record(
        call="DL1ABC", grid="", sent="-11", received="-07", began=began, ended=began,
        dial=14_074_000, audio=1500, mycall="EA7KLX", mygrid="", power_w=None))


def test_into_the_logbook_once(tmp_path):
    """A QSO completed goes into hamrlog's log with its data, and only once."""
    import datetime as dt

    from ic705suite.apps.ft8.logbook import log_qso
    from ic705suite.apps.log.core.services import OperatorService, QsoService, SettingsService
    from ic705suite.apps.log.db.session import init_engine

    init_engine(None)                      # the test's own logbook (tests/conftest.py)
    operator = OperatorService.create("EA7KLX", "Manuel")
    state = SettingsService.load_state()
    state.operator_id = operator.id
    SettingsService.save_state(state)
    fields = {"CALL": "DL1ABC", "GRIDSQUARE": "JO62", "MODE": "FT8", "RST_SENT": "-11",
              "RST_RCVD": "-07", "QSO_DATE": "20261011", "TIME_ON": "180115",
              "BAND": "20M", "FREQ": "14.075500", "TX_PWR": "5", "COMMENT": "FT8 ic705suite"}
    row, why = log_qso(fields)
    assert row is not None and why == ""
    assert (row.call, row.mode, row.band, row.freq_hz, row.rst_sent, row.rst_rcvd,
            row.gridsquare) == ("DL1ABC", "FT8", "20m", 14_075_500, "-11", "-07", "JO62")
    assert row.qso_utc == dt.datetime(2026, 10, 11, 18, 1, 15)
    assert log_qso(fields) == (None, "already logged")
    assert len([r for r in QsoService.recent(limit=10) if r.call == "DL1ABC"]) == 1


def test_the_transmit_level_and_the_alc(tmp_path, monkeypatch):
    """Too strong an audio moves the radio's ALC: the message says so, and a lower level
    sends it clean (the simulator's ALC rises over 0.6 of full scale)."""
    air = short_turns(monkeypatch)
    monkeypatch.setattr(service_module, "LONGEST_KEY", 5.0)

    def audio(program, message, frequency, rate=12_000):
        air["sent"].append(message)
        t = np.arange(int(rate * 0.7)) / rate
        return np.sin(2 * np.pi * frequency * t).astype(np.float32)   # full scale

    monkeypatch.setattr(service_module.encode, "audio", audio)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        sent: list[dict] = []
        texts: list[str] = []
        link.attach(lambda e: sent.append(e) if e.get("type") == "ft8.sent" else
                    e.get("type") == "info" and texts.append(e["text"]))
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            assert (await link.call("ft8.level", level=2)) == {"level": 1.0, "percent": 100}
            await link.call("ft8.cq")
            await until(lambda: sent)
            assert sent[0]["alc"] > 0.3 and sent[0]["level"] == 1.0
            await until(lambda: any("lower the TX level" in t for t in texts))
            assert (await link.call("ft8.level", level=0.3))["percent"] == 30
            await until(lambda: len(sent) >= 2 and sent[-1]["level"] == 0.3)
            assert sent[-1]["alc"] == 0
            state = await link.call("ft8.state")
            assert state["tx"]["level"] == 0.3 and state["tx"]["last"]["alc"] == 0
            await link.call("ft8.halt")
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_qso_lines():
    """The QSO's area: what we sent, what is sent to us, and the station we work."""
    from ic705suite.apps.ft8.app import STYLE_ME, STYLE_TX, conversation, power_step, tx_pieces

    def line(message, sender, to, kind="call"):
        return {"time": "120015", "snr": -9, "dt": 0.2, "freq": 1200, "message": message,
                "kind": kind, "sender": sender, "to": to}

    cycles = [{"start": 0, "decodes": [line("CQ DL1ABC JO62", "DL1ABC", "", "cq"),
                                       line("CQ F5ABC JN12", "F5ABC", "", "cq")]},
              {"start": 15, "decodes": [{"kind": "tx", "time": "120015", "snr": None,
                                         "dt": None, "freq": 1500,
                                         "message": "DL1ABC EA7KLX IM77"}]},
              {"start": 30, "decodes": [line("EA7KLX DL1ABC -07", "DL1ABC", "EA7KLX"),
                                        line("G4XYZ F5ABC JN12", "F5ABC", "G4XYZ")]}]
    shown = conversation(cycles, "EA7KLX", "DL1ABC")
    assert [cells[4] for _s, _d, cells, _st in shown] == [
        "CQ DL1ABC JO62", "DL1ABC EA7KLX IM77", "EA7KLX DL1ABC -07"]
    assert [style for *_r, style in shown][1:] == [STYLE_TX, STYLE_ME]
    assert len(conversation(cycles, "EA7KLX", "")) == 2           # nobody worked yet

    rig = {"max_power_w": 10, "controls": {"power": 20.0}, "meters": {"alc": 0.5},
           "transmitting": True}
    assert power_step(rig, 20.0, 1) == 30 and power_step(rig, 5.0, -1) == 0
    pieces = dict(tx_pieces({"tx": {"freq": 1500, "level": 0.45}}, rig))
    assert pieces["TX"] == "1500 Hz" and pieces["PWR"] == "2.0 W" and pieces["ALC"] == "0.50"


def test_timers_waking_early_send_once_per_turn(tmp_path, monkeypatch):
    """Windows' timers may wake a few ms early: each cycle is still taken once (it was taken
    again, decoding and transmitting twice in a turn)."""
    air = short_turns(monkeypatch)
    real_sleep = asyncio.sleep

    async def early(delay, *args, **kwargs):
        return await real_sleep(max(0.0, delay - 0.03), *args, **kwargs)

    class EarlyAsyncio:                         # the service's asyncio, its timers early
        sleep = staticmethod(early)

        def __getattr__(self, name):
            return getattr(asyncio, name)

    monkeypatch.setattr(service_module, "asyncio", EarlyAsyncio())

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        starts: list[float] = []
        link.attach(lambda e: e.get("type") == "ft8.tx" and starts.append(e["start"]))
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            await link.call("ft8.cq")
            await until(lambda: len(starts) >= 3)
            await link.call("ft8.halt")
            assert len(set(starts)) == len(starts)
            assert all(b - a == 2 for a, b in zip(starts, starts[1:], strict=False))  # turns
            assert len(air["sent"]) == len(starts)
        finally:
            await link.close()

    asyncio.run(scenario())


def test_a_waterfall_row():
    """A tone shows in its 5 Hz column, well over the noise; the rest stays near the floor."""
    from ic705suite.apps.ft8 import waterfall

    for rate in (8000, 12000, 48000):
        t = np.arange(rate) / rate
        noise = np.random.default_rng(1).standard_normal(rate) * 0.01
        levels = np.frombuffer(waterfall.row((0.3 * np.sin(2 * np.pi * 1503 * t) + noise)
                                             .astype(np.float32), rate), np.uint8)
        assert len(levels) == waterfall.columns() == 640
        assert int(levels.argmax()) * waterfall.STEP == 1500
        assert waterfall.db_over_floor(int(levels.max())) > 30
        assert abs(waterfall.db_over_floor(int(np.median(levels)))) < 1
        assert waterfall.decode(waterfall.encode(levels.tobytes())) == levels.tobytes()


def test_the_waterfall_only_while_watched(tmp_path, monkeypatch):
    fake_jt9(monkeypatch)

    async def scenario():
        link = await sim_link(tmp_path)
        rows: list[dict] = []
        link.attach(lambda e: e.get("type") == "ft8.wf" and rows.append(e))
        try:
            await link.call("ft8.start")
            await asyncio.sleep(0.8)
            assert not rows                                  # nobody asked
            reply = await link.call("ft8.waterfall", seconds=1)
            assert reply == {"low": 0, "high": 3200, "step": 5}
            await until(lambda: len(rows) >= 2)
            assert len(service_module.waterfall.decode(rows[-1]["levels"])) == 640
            await asyncio.sleep(1.3)
            count = len(rows)
            await asyncio.sleep(0.6)
            assert len(rows) == count                        # no longer asked: no rows
        finally:
            await link.close()

    asyncio.run(scenario())


def test_quiet_cycles_take_no_room():
    from ic705suite.apps.ft8.app import rows

    cycles = [{"start": 0, "dial": 14_074_000, "decodes": []},
              {"start": 15, "dial": 14_074_000, "decodes": [
                  {"time": "000015", "snr": -9, "dt": 0.1, "freq": 900,
                   "message": "G4XYZ F5ABC JN12",
                   "kind": "call", "sender": "F5ABC", "to": "G4XYZ"}]}]
    assert [start for start, _d, _c, _s in rows(cycles, False, "EA7KLX")] == [15, 15]
    assert rows(cycles, True, "EA7KLX") == []              # only CQs: nothing to show


def test_the_tune_tone(tmp_path, monkeypatch):
    """A steady tone on our frequency with the level set: the ALC shows it (too strong at
    full scale), T or H stops it, and it is refused where a message would be."""
    fake_jt9(monkeypatch)
    monkeypatch.setattr(service_module.encode, "find_ft8code", lambda configured="": "ft8code")

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=False, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        sent: list[dict] = []
        link.attach(lambda e: e.get("type") == "ft8.sent" and sent.append(e))
        sim = link.core.station.transport
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            assert "transmission is off" in (await link.call("ft8.tone"))["error"]
            link.core.config.tx_enabled = True
            link.core.station.rig.tx_enabled = True

            await link.call("ft8.level", level=1.0)
            assert (await link.call("ft8.tone", seconds=1.5)) == {"ok": True}
            await until(lambda: sim.ptt)
            assert (await link.call("ft8.state"))["tx"]["tone"]
            assert (await link.call("ft8.cq"))["msgid"] == "The tone is on: T stops it"
            await link.call("ft8.halt")
            await until(lambda: not sim.ptt and not link.core.ft8.toning)

            before = sim.tx_audio_bytes
            await link.call("ft8.tone", seconds=1.2)                # runs to its end
            await until(lambda: sent and not link.core.ft8.toning, 5)
            assert not sim.ptt and sim.tx_audio_bytes - before >= 1.0 * sim.sample_rate * 2
            assert sent[-1]["message"] == "tone" and sent[-1]["alc"] > 0.3

            await link.call("ft8.level", level=0.3)
            await link.call("ft8.tone", seconds=1.2)
            await until(lambda: len(sent) >= 2 and not link.core.ft8.toning, 5)
            assert sent[-1]["alc"] == 0 and sent[-1]["level"] == 0.3
            await link.call("ft8.tone", seconds=60)                  # never over 15 s
            await until(lambda: sim.ptt)
            assert (await link.call("ft8.tone", on=False)) == {"ok": True}
            await until(lambda: not sim.ptt)
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_power_logged_is_the_one_measured(tmp_path, monkeypatch):
    """TX_PWR is the most the radio gave while sending the QSO's messages (a high SWR makes
    it give less than set), not the setting; the tone does not count; with no reading,
    the setting."""
    monkeypatch.setattr(service_module, "data_dir", lambda: tmp_path)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        ft8 = link.core.ft8
        try:
            link.core.station.rig.state.power = 100                     # set to 10 W
            qso = ft8.qso
            qso.them, qso.their_grid, qso.sent_report, qso.got_report = \
                "F5RRS", "JN36", "-16", "-08"
            ft8.tx_on = True
            ft8._sent(0, "F5RRS EA7KLX -16", 0.1, 5.6)
            ft8._sent(15, "F5RRS EA7KLX RR73", 0.1, 4.9)
            ft8.tx_on = False
            ft8._sent(30, "tone", 0.0, 9.8)                            # not the QSO's
            ft8._log()
            assert ft8.logged[-1]["TX_PWR"] == "5.6"
            assert ft8.logged[-1]["MY_GRIDSQUARE"] == "IM77"
            link.core.config.locator = "im77ah"          # six characters: the ADIF has them
            ft8._log()
            assert ft8.logged[-1]["MY_GRIDSQUARE"] == "IM77AH"
            assert ft8.qso.call_cq() == "CQ EA7KLX IM77"   # the message, four (as FT8 has)
            ft8.qso_watts = 0.0                                         # nothing measured
            ft8._log()
            assert ft8.logged[-1]["TX_PWR"] == "10"
        finally:
            await link.close()

    asyncio.run(scenario())


def test_into_the_logbook_with_the_ft8_setup(tmp_path):
    """The QSO goes with the setup of the radio FT8 uses (and its antenna for the band), not
    the one the logbook has active (2 m FM, say); or with the one named in [ft8] setup."""
    from ic705suite.apps.ft8.logbook import log_qso
    from ic705suite.apps.log.core.services import (
        AntennaService,
        EquipmentService,
        OperatorService,
        SettingsService,
        StationService,
    )
    from ic705suite.apps.log.db.session import init_engine

    init_engine(None)
    operator = OperatorService.create("EA7KLX", "Manuel")
    kenwood = StationService.create("Kenwood TM-241E")
    icom = StationService.create("Icom IC-705")
    vertical = AntennaService.create("Diamond X-300N", ["2m", "70cm"])
    komunica = AntennaService.create("Komunica HF-PRO2 PLUS T", ["40m", "20m", "2m"])
    fm = EquipmentService.create("TM-241E casa", [kenwood.id], [vertical.id])
    EquipmentService.create("IC-705 V/UHF casa", [icom.id], [vertical.id])
    hf = EquipmentService.create("IC-705 HF casa", [icom.id], [komunica.id])
    portable = EquipmentService.create("IC-705 HF portable", [icom.id], [komunica.id])
    state = SettingsService.load_state()
    state.operator_id, state.equipment_id = operator.id, fm.id    # FM was the last one
    SettingsService.save_state(state)

    def qso(call: str) -> dict:
        return {"CALL": call, "MODE": "FT8", "RST_SENT": "-16", "RST_RCVD": "-08",
                "QSO_DATE": "20261012", "TIME_ON": "070430", "FREQ": "14.075686",
                "TX_PWR": "5.6"}

    row, why = log_qso(qso("F5RRS"), radio="IC-705 (simulator)")
    assert why == "" and row.equipment_name == "IC-705 HF casa"
    assert (row.station_name, row.antenna_name) == ("Icom IC-705", "Komunica HF-PRO2 PLUS T")
    assert not row.equipment_mismatch
    row, _ = log_qso(qso("HA7TM"), setup="ic-705 hf portable", radio="IC-705")
    assert row.equipment_name == portable.name
    assert log_qso(qso("F1PBZ"), setup="nothing such") == \
        (None, "no setup called 'nothing such' in the logbook ([ft8] setup)")
    row, _ = log_qso(qso("2E0OEW"), radio="FT-891")               # no setup for that radio
    assert row.equipment_name == "TM-241E casa"                    # the logbook's own
    assert SettingsService.load_state().equipment_id == fm.id     # not changed by FT8
    assert hf.id != portable.id


def test_no_audio_no_waterfall_rows(tmp_path, monkeypatch):
    """With the audio stopped the waterfall stands still (it repeated its last row, which
    hid that nothing was coming)."""
    fake_jt9(monkeypatch)

    async def scenario():
        link = await sim_link(tmp_path)
        rows: list[dict] = []
        link.attach(lambda e: e.get("type") == "ft8.wf" and rows.append(e))
        sim = link.core.station.transport
        try:
            await link.call("ft8.start")
            await link.call("ft8.waterfall", seconds=10)
            await until(lambda: len(rows) >= 2)
            emit, sim._emit_audio = sim._emit_audio, lambda samples: None
            await asyncio.sleep(0.4)
            count = len(rows)
            await asyncio.sleep(1.0)
            assert len(rows) == count
            sim._emit_audio = emit
            await until(lambda: len(rows) > count)
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_transmit_level_and_frequency_are_kept(tmp_path):
    """The level found for the ALC, and the transmit frequency, come back after a restart
    of the core (over [ft8] level and tx_freq)."""
    async def scenario(level=None, freq=None) -> tuple[float, int]:
        link = await sim_link(tmp_path)
        try:
            if level is not None:
                await link.call("ft8.level", level=level)
            if freq is not None:
                await link.call("ft8.txfreq", freq=freq)
            tx = (await link.call("ft8.state"))["tx"]
            return tx["level"], tx["freq"]
        finally:
            await link.close()

    assert asyncio.run(scenario()) == (0.5, 1500)                 # the first time: config
    assert asyncio.run(scenario(level=0.08, freq=1720)) == (0.08, 1720)
    assert asyncio.run(scenario()) == (0.08, 1720)                # kept
    assert (service_module.data_dir() / "ft8" / "settings.json").exists()


def test_the_alc_as_icom_wants_it(tmp_path):
    """Within its zone the ALC is right (Icom, data modes): no word; past it, lower the level;
    still at 0 with the radio short of its power, the level can go up."""
    async def scenario():
        link = await sim_link(tmp_path)
        texts: list[str] = []
        link.attach(lambda e: e.get("type") == "info" and texts.append(e["text"]))
        ft8 = link.core.ft8
        try:
            link.core.station.rig.state.power = 100                      # 10 W set
            await asyncio.sleep(0.1)
            texts.clear()
            ft8._sent(0, "CQ EA7KLX IM77", 0.6, 9.5)                    # within: fine
            ft8._sent(15, "CQ EA7KLX IM77", 0.2, 9.8)
            assert texts == []
            ft8._sent(30, "CQ EA7KLX IM77", 1.4, 10.0)                  # past the zone
            assert "past its zone (1.40)" in texts[-1]
            ft8._sent(45, "CQ EA7KLX IM77", 0.0, 2.9)                   # short of power
            assert "2.9 W of the 10 W set" in texts[-1] and "can go up" in texts[-1]
            count = len(texts)
            ft8._sent(60, "CQ EA7KLX IM77", 0.0, 9.0)                   # near enough
            assert len(texts) == count
        finally:
            await link.close()

    asyncio.run(scenario())


def test_their_rr73_just_after_the_last_try(tmp_path, monkeypatch):
    """As with MW3FLI: they send our report, go back to a CQ VK on another frequency while
    our R-report repeats, and their RR73 comes in the cycle after our sixth try, seen only
    by the full pass once our turn has begun. Their frequency is followed (the narrow pass
    looks there), and the QSO is logged."""
    air = short_turns(monkeypatch)
    monkeypatch.setattr(service_module, "data_dir", lambda: tmp_path)
    air.update(freq=1200, r_sent=0)

    def audio(program, message, frequency, rate=12_000):
        air["sent"].append(message)
        if message == "DL1ABC EA7KLX IM77":
            air["says"] = "EA7KLX DL1ABC -07"
        elif message == "DL1ABC EA7KLX R-11":
            air["r_sent"] += 1
            air["says"], air["freq"] = ("EA7KLX DL1ABC RR73" if air["r_sent"] >= 6
                                        else "CQ VK DL1ABC JO62"), 1600
        return np.zeros(int(rate * 0.2), np.float32)

    def decode(program, wav, depth=3, timeout=30.0, band=None):
        import time

        stamp = wav.stem.split("_")[1]
        if band is not None and (not band[0] <= air["freq"] <= band[1]
                                 or "RR73" in air["says"]):
            return []                # the narrow pass looks elsewhere, or misses the RR73
        if "RR73" in air["says"]:
            # The full pass takes its time: done after our next turn began (on a fast
            # machine too), when the tries have run out.
            time.sleep(0.4)
        return jt9.parse(f"{stamp} -11  0.1 {air['freq']} ~  {air['says']}")

    monkeypatch.setattr(service_module.encode, "audio", audio)
    monkeypatch.setattr(service_module.jt9, "decode", decode)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        events: list[dict] = []
        link.attach(lambda e: e.get("type") in ("info", "ft8.qso", "ft8.cycle")
                    and events.append(e))
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            await until(lambda: any(e.get("type") == "ft8.cycle" and e["decodes"]
                                    for e in events))
            cycle = next(e for e in events if e.get("type") == "ft8.cycle" and e["decodes"])
            await link.call("ft8.reply", decode=cycle["decodes"][0], start=cycle["start"])
            await until(lambda: air["r_sent"] >= 2)
            await until(lambda: link.core.ft8.their_freq == 1600)       # followed
            await until(lambda: any(e.get("type") == "ft8.qso" for e in events), 20)
            texts = [e.get("text", "") for e in events]
            assert any("no answer from DL1ABC" in t for t in texts)
            assert any("came just after the last try" in t for t in texts)
            qso = next(e for e in events if e.get("type") == "ft8.qso")["qso"]
            assert (qso["CALL"], qso["RST_SENT"], qso["RST_RCVD"]) == ("DL1ABC", "-11", "-07")
            assert air["sent"].count("DL1ABC EA7KLX R-11") == 6
            assert not link.core.ft8.tx_on and not link.core.station.transport.ptt
        finally:
            await link.close()

    asyncio.run(scenario())
