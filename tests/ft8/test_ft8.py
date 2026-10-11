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

    def decode(program, wav, depth=3, timeout=30.0):
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
            await until(lambda: len([c for c in cycles if not c["deep"]]) >= 2)
            first = cycles[0]
            assert first["depth"] == 1 and not first["deep"]
            assert {d["message"] for d in first["decodes"]} == {
                "CQ EA1ABC IN73", "EA7KLX DL1ABC -12", "G4XYZ F5ABC JN12"}
            cq = next(d for d in first["decodes"] if d["kind"] == "cq")
            assert (cq["sender"], cq["grid"], cq["snr"]) == ("EA1ABC", "IN73", -10)
            # The deeper pass, done in time, adds only what the first one missed.
            await until(lambda: any(c["deep"] for c in cycles))
            deep = next(c for c in cycles if c["deep"])
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
            await pilot.press("escape")
            await pilot.pause(0.3)
        assert not link.core.ft8.on                                  # stopped on leaving

    asyncio.run(scenario())
