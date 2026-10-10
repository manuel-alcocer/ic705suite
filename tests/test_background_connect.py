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
