"""End-to-end tests against the simulated radio (run in real time, a few seconds each)."""

import asyncio

from ic705suite.config import Config
from tests.cw.helpers import make_session, start, stop
from ic705suite.rig.icom.sim import SimStation, SimTransport


class RecordingUi:
    def __init__(self):
        self.rx, self.tx, self.infos, self.errors = [], [], [], []

    def rx_text(self, text):
        self.rx.append(text)

    def tx_text(self, text):
        self.tx.append(text)

    def info(self, msgid, **args):
        self.infos.append(msgid.format(**args) if args else msgid)

    def error(self, msgid, **args):
        self.errors.append(msgid.format(**args) if args else msgid)

    def status_changed(self):
        pass


async def _session(transport, tx_enabled=False):
    ui = RecordingUi()
    session = make_session(transport, Config(callsign="EA4TST", wpm=30, tx_enabled=tx_enabled), ui)
    await start(session)
    return session, ui


def test_tune_decode_and_transmit():
    async def scenario():
        sim = SimTransport(stations=[SimStation(7_012_000, "CQ DE EA4SIM K", 25, 0.3, pause=0.5)],
                           seed=1)
        session, ui = await _session(sim, tx_enabled=True)
        try:
            await session.execute("/f 7.012")
            await asyncio.sleep(8)  # the Bayesian decoder writes words with ~2 words of delay
            assert "DE EA4SIM" in "".join(ui.rx)
            await session.execute("/wpm 30")
            await session.execute("cq de {call} <ar>")
            await session.execute("/wait")
            assert sim.sent_text == ["CQ DE EA4TST +"]
            assert ui.tx == ["CQ DE EA4TST +"]
            assert not ui.errors
        finally:
            await stop(session)

    asyncio.run(scenario())


def test_scan_finds_station_skips_carrier_and_learns_sideband():
    async def scenario():
        stations = [SimStation(7_004_000, None, amplitude=0.2),                     # birdie
                    SimStation(7_006_330, "CQ CQ DE EA4SIM K", 22, 0.2, pause=0.3)]
        sim = SimTransport(stations=stations, sideband_sign=-1, frequency=7_000_000, seed=2)
        session, ui = await _session(sim)
        session.scanner.dwell = 1.0
        try:
            await session.execute("/f 7.000")
            session.start_scan(1)
            await asyncio.wait_for(session._scan_task, 30)
            assert abs(sim.frequency - 7_006_330) <= 30, sim.frequency
            assert session.scanner.sideband_sign == -1
            assert sim.filter_index == 9  # filter restored to 500 Hz after the search
            assert any("signal found" in i for i in ui.infos)
        finally:
            await stop(session)

    asyncio.run(scenario())


def test_receive_only_never_keys_the_radio():
    async def scenario():
        sim = SimTransport(stations=[], seed=3)
        sent = []
        original = sim.send_civ
        sim.send_civ = lambda frame: (sent.append(frame), original(frame))
        session, ui = await _session(sim)
        try:
            await session.execute("CQ CQ DE EA4TST")
            await session.execute("/raw 17 41 42")   # send CW
            await session.execute("/raw 1c 00 01")   # PTT on
            session.send_macro("F2")
            await asyncio.sleep(0.3)
            assert sim.sent_text == []
            assert not any(f[4] in (0x17, 0x1C) for f in sent)
            # Break-in is read for the front panel but never written.
            assert not any(f[4:6] == b"\x16\x47" and len(f) > 7 for f in sent)
            assert len(ui.errors) == 4
        finally:
            await stop(session)

    asyncio.run(scenario())


def test_play_recording_through_the_skimmer(tmp_path):
    import json
    import wave

    import numpy as np

    from ic705suite.synth import cw_signal, noise

    fs, seconds = 8000, 40
    n = fs * seconds
    x = noise(n, 0.03, np.random.default_rng(9))
    for tone, text, wpm in ((700, "CQ DE EA1AAA EA1AAA K", 20), (1300, "TEST DL2BBB DL2BBB", 26)):
        sig = cw_signal(text, wpm, tone, fs, 0.25, lead=0.5, tail=1.5)
        x += np.tile(sig, n // len(sig) + 1)[:n]
    wav_path = tmp_path / "rec.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(fs)
        wav.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    (tmp_path / "rec.json").write_text(json.dumps({"frequency": [[0, 7_010_000]]}))

    async def scenario():
        sim = SimTransport(stations=[], frequency=14_060_000, seed=4)
        session, ui = await _session(sim)
        try:
            await session.execute("/skim")
            await session.execute(f"/play {wav_path} x10")
            await asyncio.sleep(seconds / 10 + 0.5)
            spots = {i for i in ui.infos if i.startswith("spot")}
            assert any("7.010.10" in s and "EA1AAA" in s for s in spots), spots
            assert any("7.010.70" in s and "DL2BBB" in s for s in spots), spots
            assert any("end of the recording" in i for i in ui.infos)
            assert sim.frequency == 14_060_000  # the radio was never retuned
        finally:
            await stop(session)

    asyncio.run(scenario())


def test_recording_writes_frequency_sidecar(tmp_path):
    import json

    async def scenario():
        sim = SimTransport(stations=[], frequency=7_030_000, seed=5)
        session, ui = await _session(sim)
        session.record_to(str(tmp_path / "out.wav"))
        await asyncio.sleep(0.5)
        await session.execute("/f 7.012")
        await asyncio.sleep(0.5)
        await stop(session)

    asyncio.run(scenario())
    timeline = json.loads((tmp_path / "out.json").read_text())["frequency"]
    assert [f for _, f in timeline] == [7_030_000, 7_012_000]
