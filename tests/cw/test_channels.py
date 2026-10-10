import asyncio
import json
import wave

import numpy as np

from ic705suite.apps.cw.channels import AudioSpool, ChannelBook, ChannelStore
from ic705suite.config import Config
from ic705suite.apps.cw.playback import load_sidecar, load_wav
from tests.cw.helpers import make_session, start, stop
from ic705suite.synth import cw_signal, noise
from ic705suite.rig.icom.sim import SimTransport
from ic705suite.apps.cw.flactags import read_tags


def make_store(tmp_path) -> ChannelStore:
    return ChannelStore(f"sqlite:///{tmp_path / 'channels.db'}", audio_dir=tmp_path / "audio")


def test_callsign_channels_append_and_statistics_are_not_counted_twice(tmp_path):
    store, spool, book = make_store(tmp_path), AudioSpool(8000), ChannelBook()
    try:
        spool.write(np.zeros(8000, np.float32), 7_007_240)
        book.tuned_rx(7_007_240, "CQ CQ DE SP3BZU SP3BZU K ", 25, 14, spool.position)
        spool.write(np.zeros(4000, np.float32), 7_007_240)
        record = book.tuned
        record.tick()
        assert record.callsign == "SP3BZU"  # the station calling CQ owns the channel
        archive_id, name = store.save(record, record.callsign, None, spool, with_audio=True)
        assert name == "SP3BZU"
        book.tuned_rx(7_007_240, "R TU 73 SK ", 25, 14, spool.position)
        store.save(record, None, store.by_key("SP3BZU", 7_007_400), spool, with_audio=False)
        saved = store.transcript(archive_id)
        assert saved["stats"]["words"] == 6 + 4
        assert [e["kind"] for e in saved["entries"]].count("rx") == 2
        tags = read_tags(saved["audio"][0]["path"])
        assert tags["ic705suite_callsign"] == ["SP3BZU"] and tags["ic705suite_frequency"] == ["7007240"]
        assert load_sidecar(saved["audio"][0]["path"]) == [(0.0, 7_007_240)]
        samples, rate = load_wav(saved["audio"][0]["path"])
        assert rate == 8000 and len(samples) == 12000  # 10 s lookback, capped at the start
    finally:
        spool.close()
        store.close()


def test_naming_rules_without_callsign(tmp_path):
    async def scenario():
        config = Config(channels_db=f"sqlite:///{tmp_path / 'c.db'}")
        session = make_session(SimTransport(stations=[], seed=1), config, _Ui())
        session.store.audio_dir = tmp_path / "audio"
        for text in ("QRL? ", "TNX FB ", "HW? "):
            session.book.end_tuned()
            session.book.tuned_rx(7_030_000, text, 20, 10, None)
        first, second, third = session.book.records
        assert session.save_channel(first.id)["status"] == "needs_name"
        assert session.save_channel(first.id, name="", auto=True)["name"] == "Channel 1"
        assert session.save_channel(second.id, name="Night QSO")["status"] == "saved"
        assert session.save_channel(third.id, name="Night QSO")["status"] == "name_exists"
        result = session.save_channel(third.id, name="Night QSO", append=True)
        assert result["status"] == "appended"
        saved = {c["name"]: c for c in session.store.list()}
        assert set(saved) == {"Channel 1", "Night QSO"} and saved["Night QSO"]["stats"]["words"] == 3
        assert not any(r.unsaved for r in session.book.records)
        await session.transport.close()
        session.spool.close()

    asyncio.run(scenario())


class _Ui:
    def __init__(self):
        self.infos, self.histories = [], []

    def rx_text(self, text): ...
    def tx_text(self, text): ...
    def info(self, msgid, **args): self.infos.append(msgid.format(**args) if args else msgid)
    def error(self, msgid, **args): ...
    def status_changed(self): ...
    def history(self, title, text): self.histories.append((title, text))


def test_skimmer_channels_follow_the_station_and_survive_the_round_trip(tmp_path):
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
        ui = _Ui()
        config = Config(channels_db=f"sqlite:///{tmp_path / 'c.db'}")
        session = make_session(SimTransport(stations=[], frequency=14_060_000, seed=4), config, ui)
        await start(session)
        try:
            await session.execute("/skim")
            await session.execute(f"/play {wav_path} x10")
            await asyncio.sleep(3.0)
            calls = {r.callsign for r in session.book.records}
            assert {"EA1AAA", "DL2BBB"} <= calls, calls
            index = next(i for i, ch in enumerate(session.skimmer.channels) if ch.callsign == "EA1AAA")
            record = session.book.record_for(session.skimmer.channels[index])
            await session.execute(f"/go {index + 1}")
            assert session.book.tuned is record          # same channel, now tuned
            assert ui.histories and ui.histories[-1][0] == "EA1AAA" and "EA1AAA" in ui.histories[-1][1]
            await session.execute("/skim")              # back to the skimmer
            assert session.skimmer is not None and len(session.skimmer.channels) >= 2
            assert any("skimmer resumed" in i for i in ui.infos)
        finally:
            await stop(session)

    asyncio.run(scenario())


def test_station_picked_before_validation_gets_its_callsign_later():
    class Station:  # stand-in for a skimmer channel
        callsign, text, wpm, snr_db = None, "CQ DE EA1AAA", 20.0, 20.0

    book, station = ChannelBook(), Station()
    record = book._new("tuned", 7_010_000)
    record.seed(station.text)
    book.attach(station, record)
    station.callsign = "EA1AAA"
    assert book.skimmer_validated(station, 7_010_000) is record
    assert record.callsign == "EA1AAA" and len(book.records) == 1


def test_flac_tags_round_trip(tmp_path):
    """Tags written by the suite read back, also by mutagen-style keys; audio untouched."""
    import numpy as np
    import soundfile

    from ic705suite.apps.cw.flactags import write_tags

    path = tmp_path / "x.flac"
    samples = (np.sin(np.arange(8000) / 5) * 8000).astype(np.int16)
    soundfile.write(str(path), samples, 8000, subtype="PCM_16", format="FLAC")
    write_tags(path, {"IC705SUITE_callsign": "EA7XYZ", "title": "Canal ñ", "skip": None})
    write_tags(path, {"ic705suite_callsign": "EA7KLX", "title": "otra vez"})   # replaces
    assert read_tags(path) == {"ic705suite_callsign": ["EA7KLX"], "title": ["otra vez"]}
    back, rate = soundfile.read(str(path), dtype="int16")
    assert rate == 8000 and (back == samples).all()
