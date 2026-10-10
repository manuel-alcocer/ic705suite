"""Core over WebSocket with the simulated radio, plus translation catalog checks."""

import asyncio

import pytest

from ic705suite import i18n
from ic705suite.config import Config
from ic705suite.station.core import Core
from ic705suite.station.links import LinkError, RemoteLink
from ic705suite.station.server import CoreServer
from ic705suite.rig.icom.sim import SimStation, SimTransport

async def start_server(config: Config, sim: SimTransport, token: str = "secret"):
    core = Core(sim, config)
    await core.start()
    server = CoreServer(core, "127.0.0.1", 0, token)
    stop = asyncio.Event()
    task = asyncio.create_task(server.run(stop))
    port = None
    while port is None:  # the bound port is announced through an info event
        await asyncio.sleep(0.05)
        for event in core.hub.backlog:
            if event.get("msgid", "").startswith("server listening"):
                port = event["args"]["port"]
    return core, stop, task, port


def test_remote_client_receives_events_and_runs_commands():
    async def scenario():
        sim = SimTransport(stations=[SimStation(7_012_000, "CQ DE EA4SIM K", 25, 0.3, pause=0.5)],
                           seed=1)
        core, stop, task, port = await start_server(Config(), sim)
        events: list[dict] = []
        link = RemoteLink(f"127.0.0.1:{port}", "secret")
        link.on_event = events.append
        try:
            await link.connect()
            await link.command("/f 7.012")
            await asyncio.sleep(8)  # the Bayesian decoder writes words with ~2 words of delay
            kinds = {e["type"] for e in events}
            assert {"welcome", "state", "info", "rx"} <= kinds
            assert sim.frequency == 7_012_000
            assert "DE EA4SIM" in "".join(e["text"] for e in events if e["type"] == "rx")
            state = [e for e in events if e["type"] == "state"][-1]
            assert state["frequency"] == 7_012_000 and state["tx_enabled"] is False
        finally:
            await link.close()
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_wrong_token_is_rejected():
    async def scenario():
        core, stop, task, port = await start_server(Config(), SimTransport(stations=[], seed=2))
        try:
            with pytest.raises(LinkError, match="authentication failed"):
                await RemoteLink(f"127.0.0.1:{port}", "wrong").connect()
        finally:
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_remote_client_cannot_transmit_or_enable_tx():
    async def scenario():
        sim = SimTransport(stations=[], seed=3)
        frames = []
        original = sim.send_civ
        sim.send_civ = lambda frame: (frames.append(frame), original(frame))
        core, stop, task, port = await start_server(Config(), sim)
        link = RemoteLink(f"127.0.0.1:{port}", "secret")
        try:
            await link.connect()
            for line in ("CQ CQ DE EA4TST", "/raw 17 41 42", "/raw 1c 00 01", "/macro F2",
                         "/tx on", "/wpm 25"):
                await link.command(line)
            await asyncio.sleep(0.5)
            assert sim.sent_text == []
            assert not any(f[4] in (0x17, 0x1C) for f in frames)
            assert core.config.tx_enabled is False
        finally:
            await link.close()
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_network_server_requires_token():
    core = Core(SimTransport(stations=[]), Config())
    with pytest.raises(ValueError, match="token"):
        CoreServer(core, "0.0.0.0", 7373, "")


def test_render_translates_core_events():
    event = {"type": "info", "msgid": "signal found at {freq} MHz (/next to keep searching)",
             "args": {"freq": "7.012.00"}, "text": "signal found at 7.012.00 MHz"}
    try:
        i18n.set_language("es")
        assert i18n.render(event) == "señal encontrada en 7.012.00 MHz (/next para seguir buscando)"
        i18n.set_language("en")
        assert i18n.render(event) == "signal found at 7.012.00 MHz (/next to keep searching)"
    finally:
        i18n.set_language("en")
