"""The radio is looked for in the background, for as long as it takes."""

import asyncio

from ic705suite.config import Config
from ic705suite.rig.transport import TransportError
from ic705suite.station import core as core_module
from ic705suite.station.links import LocalLink


def sim_link(tmp_path) -> LocalLink:
    return LocalLink(None, Config(connection="sim", swr_web=False,
                                  database_url=f"sqlite:///{tmp_path / 'suite.db'}"))


def radio_away(link: LocalLink, attempts: int | None) -> list[int]:
    """The station fails to start `attempts` times (None: always)."""
    station = link.core.station
    real_start, tries = station.start, []

    async def start():
        tries.append(1)
        if attempts is None or len(tries) <= attempts:
            raise TransportError("Network is unreachable")
        await real_start()

    station.start = start
    return tries


def test_keeps_trying_until_the_radio_appears(tmp_path, monkeypatch):
    monkeypatch.setattr(core_module, "RETRY_DELAY", 0.01)

    async def run():
        link = sim_link(tmp_path)
        events = []
        link.attach(events.append)
        tries = radio_away(link, attempts=5)
        try:
            await asyncio.wait_for(link.connect(), 10)
            assert link.connected and len(tries) == 6
            errors = [e for e in events if e["type"] == "error"]
            assert len(errors) == 1 and "background" in errors[0]["text"]
        finally:
            await link.close()

    asyncio.run(run())


def test_leaving_while_the_radio_is_away(tmp_path, monkeypatch):
    monkeypatch.setattr(core_module, "RETRY_DELAY", 0.01)

    async def run():
        link = sim_link(tmp_path)
        radio_away(link, attempts=None)
        connecting = asyncio.ensure_future(link.connect())
        await asyncio.sleep(0.2)
        assert not connecting.done()
        await asyncio.wait_for(link.close(), 2)      # does not wait for the radio
        await asyncio.sleep(0.01)
        assert connecting.done()

    asyncio.run(run())


def test_a_half_dead_session_is_connected_again(tmp_path, monkeypatch):
    """The link up (the radio answers its pings) but CI-V unanswered, or the audio gone:
    taken as lost and connected again, not left looking connected and dead."""
    from ic705suite.rig.base import RigError
    from ic705suite.station import station as station_module

    monkeypatch.setattr(station_module, "SILENT_CIV", 0.6)
    monkeypatch.setattr(station_module, "SILENT_AUDIO", 0.6)
    monkeypatch.setattr(station_module, "RECONNECT_DELAY", 0.05)

    async def run():
        link = sim_link(tmp_path)
        texts: list[str] = []
        link.attach(lambda e: texts.append(e.get("text", "")))
        await asyncio.wait_for(link.connect(), 10)
        station = link.core.station
        sim = station.transport
        try:
            for _ in range(100):
                if station.connected and station._audio_at is not None:
                    break
                await asyncio.sleep(0.05)
            real = station.rig.read_smeter

            async def unanswered():
                raise RigError("no answer to command 15 02")

            station.rig.read_smeter = unanswered            # CI-V gone, audio still on
            for _ in range(100):
                if any("no answer to CI-V" in t for t in texts):
                    break
                await asyncio.sleep(0.05)
            assert any("no answer to CI-V" in t for t in texts)
            station.rig.read_smeter = real
            for _ in range(200):
                # Audio heard again first: only then is its silence taken for a loss.
                if station.connected and station._audio_at is not None:
                    break
                await asyncio.sleep(0.05)
            assert station.connected and station._audio_at is not None

            texts.clear()
            emit, sim._emit_audio = sim._emit_audio, lambda samples: None   # audio gone
            for _ in range(100):
                if any("no audio" in t for t in texts):
                    break
                await asyncio.sleep(0.05)
            assert any("no audio" in t for t in texts)
            sim._emit_audio = emit
        finally:
            await link.close()

    asyncio.run(run())
