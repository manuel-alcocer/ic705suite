"""The packet terminal on the core (simulated radio) against a fake Direwolf: the keys, the
commands, the channel bar and the heard list."""

from __future__ import annotations

import asyncio

from ic705suite.apps.packet.app import PacketApp, TextView
from ic705suite.apps.packet.client import parse_connect
from ic705suite.config import Config
from ic705suite.station.links import LocalLink
from tests.packet.fake_tnc import FakeTnc


def packet_config(tmp_path, port: int) -> Config:
    return Config(connection="sim", swr_web=False, callsign="EA7KLX",
                  database_url=f"sqlite:///{tmp_path / 'suite.db'}",
                  packet_host="127.0.0.1", packet_tcp_port=port, packet_mycall="EA7KLX-4")


def shown(view: TextView) -> str:
    return "\n".join(strip.text for strip in view.lines)


async def wait(pilot, condition, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition never met")


def test_parse_connect():
    assert parse_connect(["ea1abc"]) == ("EA1ABC", [])
    assert parse_connect(["EA1ABC", "v", "ea7urs-1,EA7RCM"]) == ("EA1ABC", ["EA7URS-1", "EA7RCM"])
    assert parse_connect(["EA1ABC", "via", "A", "B"]) == ("EA1ABC", ["A", "B"])
    assert parse_connect([]) is None


def test_a_session_in_the_terminal(tmp_path):
    async def scenario():
        tnc = await FakeTnc().start()
        link = LocalLink(None, packet_config(tmp_path, tnc.port))
        app = PacketApp(link, owns_link=True)
        try:
            async with app.run_test(size=(130, 36)) as pilot:
                client = app.client
                await wait(pilot, lambda: (client.state.get("tnc") or {}).get("state") == "up")
                monitor = app.query_one("#monitor", TextView)
                channel = app.query_one("#channel", TextView)
                await wait(pilot, lambda: "Fm EA1ABC-7 To ID" in shown(monitor))
                assert "0:Monitor" in str(app.query_one("#tabs").render())

                await pilot.press("f2")
                await wait(pilot, lambda: client.channel == 2)
                await pilot.press(*"/c ea1abc v ea7urs-1", "enter")
                await wait(pilot, lambda: client.channel_info(2).get("state") == "connected")
                await wait(pilot, lambda: "BBS>" in shown(channel))
                assert "*** Calling EA1ABC via EA7URS-1…" in shown(channel)
                assert "2:EA1ABC" in str(app.query_one("#tabs").render())
                assert app.query_one("#line").placeholder == "to EA1ABC"

                await pilot.press(*"hola", "enter")
                await wait(pilot, lambda: "ECHO hola" in shown(channel))
                await pilot.press("up")
                assert app.query_one("#line").value == "hola"
                await pilot.press("escape")
                assert app.query_one("#line").value == ""

                # Another channel; text arriving on channel 2 meanwhile is counted unread.
                await pilot.press("f3")
                await wait(pilot, lambda: client.channel == 3)
                assert "ECHO" not in shown(channel)
                tnc.push(tnc.reply_from("EA1ABC", "EA7KLX-4", b"news\r"))
                await wait(pilot, lambda: client.channel_info(2).get("unread") == 1)
                assert "2:EA1ABC •1" in str(app.query_one("#tabs").render())
                await pilot.press("f2")
                await wait(pilot, lambda: "news" in shown(channel))
                await wait(pilot, lambda: client.channel_info(2).get("unread") == 0)

                await pilot.press(*"/help", "enter")
                await wait(pilot, lambda: "/ui DEST text" in shown(channel))
                await pilot.press(*"/ex", "enter")              # not ours: to the station
                await wait(pilot, lambda: "ECHO /ex" in shown(channel))

                await pilot.press(*"/d", "enter")
                await wait(pilot, lambda: client.channel_info(2).get("state") == "free")
                await pilot.press(*"/nonsense", "enter")        # with nobody: a mistake
                await wait(pilot, lambda: "Unknown command" in str(
                    app.query_one("#entry").border_subtitle))

                await pilot.press(*"/tnc", "enter")                 # what the TNC is
                await wait(pilot, lambda: len(app.screen_stack) > 1)
                shown_text = "\n".join(str(w.render()) for w in app.screen.query("Static"))
                assert f"TNC: agwpe at 127.0.0.1:{tnc.port}, up" in shown_text
                await pilot.press("escape")
                await wait(pilot, lambda: len(app.screen_stack) == 1)

                # F10: the heard list; Enter connects on this channel (free again).
                await pilot.press("f10")
                await pilot.pause(0.2)
                await pilot.press("enter")
                await wait(pilot, lambda: client.channel_info(2).get("call") == "EA1ABC-7")

                await pilot.press("f9")
                assert not monitor.display
                await pilot.press("ctrl+q")
                await pilot.pause(0.2)
            assert app.return_value == 0
        finally:
            await tnc.stop()

    asyncio.run(scenario())


def test_sessions_carry_on_after_leaving(tmp_path):
    """The core keeps the sessions: a screen opened later shows them, text included."""

    async def scenario():
        tnc = await FakeTnc().start()
        link = LocalLink(None, packet_config(tmp_path, tnc.port))
        try:
            app = PacketApp(link)
            async with app.run_test(size=(130, 36)) as pilot:
                await wait(pilot, lambda: (app.client.state.get("tnc") or {}).get("state")
                           == "up")
                await pilot.press(*"/c EA1ABC", "enter")
                await wait(pilot, lambda: app.client.channel_info(1).get("state") == "connected")
                await pilot.press("ctrl+q")
                await pilot.pause(0.2)
            assert link.core.packet.channels[1].state == "connected"
            again = PacketApp(link)
            async with again.run_test(size=(130, 36)) as pilot:
                channel = again.query_one("#channel", TextView)
                await wait(pilot, lambda: "Welcome to EA1ABC" in shown(channel))
                assert "1:EA1ABC" in str(again.query_one("#tabs").render())
                await pilot.press("ctrl+q")
                await pilot.pause(0.2)
        finally:
            await link.close()
            await tnc.stop()

    asyncio.run(scenario())


def test_not_set_up_says_how(tmp_path):
    async def scenario():
        config = packet_config(tmp_path, 1)
        config.packet_host = ""
        link = LocalLink(None, config)
        app = PacketApp(link, owns_link=True)
        async with app.run_test(size=(130, 36)) as pilot:
            await wait(pilot, lambda: "Packet is not set up" in str(
                app.query_one("#entry").border_subtitle))
            await pilot.press("ctrl+q")
            await pilot.pause(0.2)

    asyncio.run(scenario())


def test_the_sysop_on_the_bbs(tmp_path):
    """/c to the BBS's own callsign enters it with no radio; F12 is the sysop's page."""
    from textual.widgets import DataTable

    from ic705suite.apps.packet.app import BbsScreen
    from ic705suite.ui.widgets import ChoiceScreen, TextScreen

    async def scenario():
        tnc = await FakeTnc(echo=False).start()
        config = packet_config(tmp_path, tnc.port)
        config.packet_bbs = True
        link = LocalLink(None, config)
        app = PacketApp(link, owns_link=True)
        try:
            async with app.run_test(size=(140, 40)) as pilot:
                client = app.client
                await wait(pilot, lambda: (client.state.get("bbs") or {}).get("registered"))
                assert "BBS EA7KLX-1" in str(app.query_one("#statusline").render())
                channel = app.query_one("#channel", TextView)
                await pilot.press(*"/c ea7klx-1", "enter")
                await wait(pilot, lambda: "de EA7KLX>" in shown(channel))
                assert "*** Connected to EA7KLX-1, this BBS (no radio)" in shown(channel)
                for line in ("SB ALL", "Club meeting", "Friday 20h", "/ex"):
                    await pilot.press(*line, "enter")
                await wait(pilot, lambda: "Message: 1 Bid: 1_EA7KLX" in shown(channel))

                await pilot.press("f12")
                await wait(pilot, lambda: isinstance(app.screen, BbsScreen))
                screen = app.screen
                await wait(pilot, lambda: screen.rows)
                assert "connected EA7KLX (here)" in str(screen.query_one("#bbs-who").render())
                assert screen.query_one(DataTable).row_count == 1
                await pilot.press("enter")
                await wait(pilot, lambda: isinstance(app.screen, TextScreen))
                assert "Friday 20h" in "\n".join(app.screen.lines)
                await pilot.press("escape")
                await pilot.press("u")
                await wait(pilot, lambda: screen.showing == "users" and screen.rows)
                assert screen.rows[0]["call"] == "EA7KLX"
                await pilot.press("u")
                await wait(pilot, lambda: screen.showing == "messages" and screen.rows)
                await pilot.press("k")
                await wait(pilot, lambda: isinstance(app.screen, ChoiceScreen))
                await pilot.press("enter")                      # Kill
                await wait(pilot, lambda: isinstance(app.screen, BbsScreen) and not screen.rows)
                await pilot.press("escape")
                await pilot.press(*"/d", "enter")
                await wait(pilot, lambda: client.channel_info(1).get("state") == "free")
                await pilot.press("ctrl+q")
                await pilot.pause(0.2)
        finally:
            await tnc.stop()

    asyncio.run(scenario())


def test_f12_with_the_bbs_off(tmp_path):
    async def scenario():
        tnc = await FakeTnc().start()
        app = PacketApp(LocalLink(None, packet_config(tmp_path, tnc.port)), owns_link=True)
        try:
            async with app.run_test(size=(130, 36)) as pilot:
                await wait(pilot, lambda: (app.client.state.get("tnc") or {}).get("state") == "up")
                await pilot.press("f12")
                assert "The BBS is off" in str(app.query_one("#entry").border_subtitle)
                await pilot.press("ctrl+q")
                await pilot.pause(0.2)
        finally:
            await tnc.stop()

    asyncio.run(scenario())
