"""Packet on another machine's core: a core with no radio (the Raspberry Pi with the
Digirig and the BBS) served over WebSocket, and a client whose F7 opens packet there while
the rest of the suite stays on its own core."""

from __future__ import annotations

import asyncio
import socket

from ic705suite.apps.packet.app import PacketApp, TextView
from ic705suite.config import Config
from ic705suite.station.core import Core
from ic705suite.station.server import CoreServer
from tests.packet.fake_tnc import FakeTnc
from tests.packet.test_app import shown

TOKEN = "secret-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def pi_core(tmp_path, tnc: FakeTnc) -> tuple[Core, asyncio.Event, int]:
    """What runs on the Pi: ic705suite --serve with [rig] connection = "none"."""
    config = Config(connection="none", swr_web=False, callsign="EA7KLX",
                    database_url=f"sqlite:///{tmp_path / 'pi.db'}", packet_host="127.0.0.1",
                    packet_tcp_port=tnc.port, packet_mycall="EA7KLX-4", packet_bbs=True)
    core = Core(None, config)
    port = free_port()
    stop = asyncio.Event()
    asyncio.create_task(CoreServer(core, "127.0.0.1", port, TOKEN).run(stop))
    await core.start(retry_forever=True)
    return core, stop, port


def test_a_core_with_no_radio(tmp_path):
    async def scenario():
        tnc = await FakeTnc(echo=False).start()
        core, stop, _port = await pi_core(tmp_path, tnc)
        try:
            assert core.started and not core.station.connected
            assert core.station.transport.name == "none"
            texts = [e.get("text") for e in core.hub.backlog]
            assert "no radio here: this core serves packet only" in texts
            for _ in range(100):
                if core.packet.bbs_registered and core.packet.registered:
                    break
                await asyncio.sleep(0.02)
            assert tnc.registered == {"EA7KLX-4", "EA7KLX-1"}
        finally:
            stop.set()
            await core.stop()
            await tnc.stop()

    asyncio.run(scenario())


def test_f7_opens_packet_on_the_pi(tmp_path):
    from ic705suite.launcher import run_app

    async def scenario():
        tnc = await FakeTnc(echo=False).start()
        core, stop, port = await pi_core(tmp_path, tnc)
        desk = Config(connection="sim", swr_web=False, callsign="EA7KLX",
                      packet_core=f"127.0.0.1:{port}", packet_core_token=TOKEN)
        made: list[PacketApp] = []
        original = PacketApp.__init__

        def remember(self, link, owns_link=False):
            original(self, link, owns_link)
            made.append(self)

        PacketApp.__init__ = remember
        try:
            async def drive() -> None:
                while not made or not made[0].is_running:
                    await asyncio.sleep(0.05)
                app = made[0]
                assert app.owns_link and app.link.uri == f"ws://127.0.0.1:{port}"
                for _ in range(200):
                    if (app.client.state.get("bbs") or {}).get("registered"):
                        break
                    await asyncio.sleep(0.05)
                channel = app.query_one("#channel", TextView)
                app.client.link_task(app.client.submit("/c EA7KLX-1"))
                for _ in range(200):
                    if "de EA7KLX>" in shown(channel):
                        break
                    await asyncio.sleep(0.05)
                assert "Connected to EA7KLX-1, this BBS (no radio)" in shown(channel)
                app.action_leave()

            async def headless(self, **_kw):
                async with self.run_test(size=(130, 36)):
                    await drive()
                    while self.is_running:
                        await asyncio.sleep(0.05)
                return self.return_value

            PacketApp.run_async = headless
            assert await run_app("packet", None, desk) == 0
            # The session to the BBS lives on the Pi's core, which keeps it.
            assert core.packet.channels[1].call == "EA7KLX-1"
        finally:
            PacketApp.__init__ = original
            del PacketApp.run_async
            stop.set()
            await core.stop()
            await tnc.stop()

    asyncio.run(scenario())


def test_the_dashboard_offers_packet_on_another_core():
    from ic705suite.dashboard.app import DashboardApp

    config = Config(connection="lan", host="", callsign="EA7KLX", packet_core="pi:7373")
    app = DashboardApp(None, config)
    packet = {"id": "packet", "needs_rig": True, "ready": True, "available": True}
    view = {"id": "view", "needs_rig": True, "ready": True, "available": True}
    assert app._usable(packet) and not app._usable(view)


def test_with_packet_elsewhere_this_core_runs_none(tmp_path):
    from ic705suite.apps.packet.service import PacketService

    config = Config(callsign="EA7KLX", packet_host="127.0.0.1", packet_bbs=True,
                    packet_core="pi:7373")
    assert not PacketService(config, None).configured
