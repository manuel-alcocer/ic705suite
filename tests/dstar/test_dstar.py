"""D-STAR in the core: the CI-V replies (as the operator's IC-705 gave them), the overs
followed on the simulated radio (history, resumed overs, system replies, TX), the TO
written and confirmed, and the reflector list."""

import asyncio

import pytest

from ic705suite.apps.dstar import civ
from ic705suite.apps.dstar import service as service_module
from ic705suite.apps.dstar.reflectors import Registry, normalize_to, shipped
from ic705suite.config import Config
from ic705suite.station.links import LocalLink


def test_the_replies_of_the_operators_radio():
    my = civ.parse_reply(bytes.fromhex("1f 00 45 41 37 4b 4c 58 20 20 37 30 35 20"))
    assert my == ("my_call", ("EA7KLX", "705"))
    calls = civ.parse_reply(bytes.fromhex("1f 01 43 51 43 51 43 51 20 20") + b" " * 16)
    assert calls == ("tx_calls", ("CQCQCQ", "", ""))
    assert civ.parse_reply(bytes.fromhex("1f 02 ff")) == ("tx_message", "")
    assert civ.parse_reply(bytes.fromhex("20 00 02 ff")) == ("rx_calls", None)
    kind, status = civ.parse_reply(bytes.fromhex("20 02 02 20"))
    assert kind == "rx_status" and status.last_call_finished and not status.voice


def test_reflectors_shipped_and_own(tmp_path):
    assert normalize_to("/xlx214d") == "/XLX214D" and normalize_to("ref001c", "ext") == "REF001 C"
    with pytest.raises(ValueError):
        normalize_to("nonsense!")
    ships = {r["to"] for r in shipped()}
    assert "/XLX214D" in ships
    registry = Registry(f"sqlite:///{tmp_path / 'suite.db'}")
    registry.save({"to": "/xlx214d", "name": "Mine", "server": ""})   # renamed, no server
    mine = registry.get("/XLX214D")
    assert mine["name"] == "Mine" and mine["own"] and mine["server"] == "server1.dstar.es"
    registry.delete("/XLX214D")
    assert registry.get("/XLX214D")["own"] is False


async def until(condition, timeout=8.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition never met")


def test_overs_on_the_simulated_radio(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "FAST_POLL", 0.1)
    monkeypatch.setattr(service_module, "RX_END_GRACE", 0.4)
    monkeypatch.setattr(service_module, "lookup_name",
                        lambda call: ("Pepe Pérez", "Sevilla, Spain") if call == "EA7ABC"
                        else ("", ""))

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA4SIM", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        sim, dstar = link.core.station.transport, link.core.dstar
        sim.mode = 0x17
        sim.dv_tx_calls = b"/XLX214D" + b"EA4SIM Z" + b"EA4SIM G"       # INT Terminal Mode
        try:
            await link.call("dstar.start")
            await until(lambda: dstar.my_call == "EA4SIM" and dstar.ur == "/XLX214D")
            assert dstar.radio_mode() == "int" and dstar.mode == "DV"
            # An over heard, its message, its end; the same header again: the same over
            sim.dv_receive("EA7ABC", "705", rpt1="EA4SIM G", rpt2="EA4SIM Z",
                           message="hola desde Sevilla")
            await until(lambda: dstar.rx_info and dstar.rx_info.get("name") == "Pepe Pérez")
            await until(lambda: dstar.rx_info.get("message") == "hola desde Sevilla")
            sim.dv_end()
            await until(lambda: not dstar.rx_active)
            sim.dv_voice = True                      # the voice back within 8 s
            await until(lambda: dstar.rx_active)
            sim.dv_end()
            await until(lambda: not dstar.rx_active)
            rows = (await link.call("dstar.history"))["history"]
            assert len(rows) == 1 and rows[0]["callsign"] == "EA7ABC"
            assert rows[0]["direction"] == "RX" and rows[0]["message"] == "hola desde Sevilla"
            assert rows[0]["name"] == "Pepe Pérez" and rows[0]["reflector"] == "/XLX214D"
            # Our own gateway answering: no conversation
            sim.dv_receive("EA4SIM", "INFO", called="EA4SIM")
            await asyncio.sleep(0.5)
            sim.dv_end()
            await asyncio.sleep(0.6)
            assert len((await link.call("dstar.history"))["history"]) == 1
            # Our transmission
            sim.ptt = True
            await until(lambda: dstar.transmitting)
            sim.ptt = False
            await until(lambda: not dstar.transmitting)
            rows = (await link.call("dstar.history"))["history"]
            assert rows[0]["direction"] == "TX" and rows[0]["callsign"] == "EA4SIM"
            assert rows[0]["ended"] is not None
            # The TO changed (INT), R1 / R2 kept, read back
            assert await link.call("dstar.change", to="/xlx030c") == {"to": "/XLX030C"}
            assert sim.dv_tx_calls == b"/XLX030C" + b"EA4SIM Z" + b"EA4SIM G"
            state = (await link.call("dstar.state"))
            assert state["ur"] == "/XLX030C" and state["on"]
            assert (await link.call("dstar.note", note="P"))["note"] == "P"
            assert sim.dv_my == b"EA4SIM  P   "
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_terminal_screen(tmp_path, monkeypatch):
    """The same keys in the terminal: the reflector chosen ([ ]) and changed (C), the over
    on air, the history, Esc to leave."""
    from textual.widgets import DataTable, Static

    from ic705suite.apps.dstar.app import DStarApp

    monkeypatch.setattr(service_module, "FAST_POLL", 0.1)
    monkeypatch.setattr(service_module, "RX_END_GRACE", 0.4)
    monkeypatch.setattr(service_module, "lookup_name", lambda call: ("Pepe", "Sevilla"))

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA4SIM", locator="IM77",
                        dstar_server="server1.dstar.es",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        sim = link.core.station.transport
        sim.mode = 0x17
        sim.dv_tx_calls = b"/XLX214D" + b"EA4SIM Z" + b"EA4SIM G"
        await link.call("dstar.reflector.save",
                        item={"to": "/XLX030C", "name": "Test", "server": "server1.dstar.es"})
        app = DStarApp(link, config=config)
        try:
            async with app.run_test(size=(140, 40)) as pilot:
                for _ in range(80):
                    await pilot.pause(0.1)
                    if app.state.get("radio_mode") == "int" and len(app.reflectors) > 1:
                        break
                text = str(app.query_one("#reflector", Static).render())
                assert "D-STAR Spain" in text and "/XLX214D" in text
                sim.dv_receive("EA7ABC", "705", message="hola")
                for _ in range(60):
                    await pilot.pause(0.1)
                    if "Pepe" in str(app.query_one("#onair", Static).render()):
                        break
                assert "EA7ABC /705" in str(app.query_one("#onair", Static).render())
                sim.dv_end()
                for _ in range(60):
                    await pilot.pause(0.1)
                    if app.query_one("#history", DataTable).row_count == 1:
                        break
                await pilot.press("right_square_bracket")
                chosen = app.choice
                assert chosen and chosen != "/XLX214D"
                await pilot.press("c")
                for _ in range(60):
                    await pilot.pause(0.1)
                    if sim.dv_tx_calls[:8] == chosen.encode().ljust(8):
                        break
                assert sim.dv_tx_calls[:8] == chosen.encode().ljust(8)
                await pilot.press("v")                    # the D-PRS reports, the weather
                assert app.view == "dprs"
                assert app.query_one("#history", DataTable).border_title == "D-PRS"
                await pilot.press("v")
                await pilot.press("v")                    # and the history again
                assert app.view == "history"
                await pilot.press("b")                    # the beacon's form, from the radio
                from ic705suite.apps.dstar.app import BeaconScreen

                for _ in range(40):
                    await pilot.pause(0.1)
                    if isinstance(app.screen, BeaconScreen):
                        break
                assert isinstance(app.screen, BeaconScreen)
                await pilot.press("escape")               # left as it was
                await pilot.pause(0.2)
                assert not isinstance(app.screen, BeaconScreen)
                await pilot.press("escape")
                await pilot.pause(0.3)
            assert app.return_value == 0
        finally:
            await link.close()

    asyncio.run(scenario())


def test_dprs_reports_and_our_beacon(tmp_path, monkeypatch):
    """A D-PRS position heard with an over is kept and placed on it (distance from us);
    our beacon's settings are read, written and confirmed; after our over its position
    goes to APRS-IS (a fake server)."""
    from ic705suite.apps.dstar import aprs, dprs

    monkeypatch.setattr(service_module, "FAST_POLL", 0.1)
    monkeypatch.setattr(service_module, "RX_END_GRACE", 0.4)
    monkeypatch.setattr(service_module, "DPRS_AFTER", 0.2)
    monkeypatch.setattr(service_module, "lookup_name", lambda call: ("", ""))
    sent: list[str] = []

    async def scenario():
        async def server(reader, writer):
            login = (await reader.readline()).decode()
            assert login.startswith("user EA4SIM-10 pass ")
            assert str(aprs.passcode("EA4SIM")) in login
            writer.write(b"# logresp EA4SIM-10 verified, server TEST\r\n")
            await writer.drain()
            while line := await reader.readline():
                sent.append(line.decode().strip())

        fake = await asyncio.start_server(server, "127.0.0.1", 0)
        port = fake.sockets[0].getsockname()[1]
        config = Config(connection="sim", swr_web=False, callsign="EA4SIM", locator="IM77",
                        tx_enabled=True, dstar_aprs=True, dstar_aprs_server=f"127.0.0.1:{port}",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        sim, dstar = link.core.station.transport, link.core.dstar
        sim.mode = 0x17
        try:
            await link.call("dstar.start")
            await until(lambda: dstar.settings() is not None and dstar.my_call)
            settings = (await link.call("dstar.dprs_settings"))["settings"]
            assert settings["source"] == "gps" and settings["symbols"][1] == "/>"
            assert not settings["send"]
            # A position heard with an over: kept, placed on it
            sim.dv_position("EA7ABC-9", 37.39, -5.98)
            sim.dv_receive("EA7ABC", "705")
            await until(lambda: dstar.rx_info and dstar.rx_info.get("distance") is not None)
            sim.dv_end()
            await until(lambda: not dstar.rx_active)
            reports = (await link.call("dstar.dprs"))["dprs"]
            assert reports[0]["name"] == "EA7ABC-9" and reports[0]["kind"] == "position"
            row = (await link.call("dstar.history"))["history"][0]
            assert row["lat"] == pytest.approx(37.39) and row["lon"] == pytest.approx(-5.98)
            # Our beacon: a manual position, symbol 2 (car), SSID -9, a comment
            form = {"send": True, "source": "manual", "lat": 40.4168, "lon": -3.7038,
                    "alt": 650, "symbol": 1, "ssid": dprs.SSID_CHOICES.index("-9"),
                    "comment": "IC-705 portable"}
            saved = (await link.call("dstar.dprs_save", **form))["settings"]
            assert saved["send"] and saved["source"] == "manual" and saved["symbol"] == 1
            assert saved["lat"] == pytest.approx(40.4168, abs=1e-4)
            assert saved["comment"] == "IC-705 portable"
            assert (await link.call("dstar.state"))["dprs"]["send"]
            # Our over: its position to APRS-IS
            sim.ptt = True
            await until(lambda: dstar.transmitting)
            sim.ptt = False
            await until(lambda: sent, 10)
            assert sent[0].startswith("EA4SIM-9>API705,TCPIP*:!4025.01N/00342.23W>")
            assert sent[0].endswith("IC-705 portable")
        finally:
            await link.close()
            fake.close()

    asyncio.run(scenario())


def test_the_beacon_form():
    from ic705suite.apps.dstar.app import beacon_form

    form = beacon_form(True, "manual", "", "", "", "IM77AH", 0, 0, "x")
    assert form["lat"] == pytest.approx(37.31, abs=0.05) and form["alt"] is None
    assert isinstance(beacon_form(True, "manual", "95", "0", "", "", 0, 0, ""), str)
    assert beacon_form(False, "gps", "", "", "", "", 2, 5, "c") == \
        {"send": False, "source": "gps", "symbol": 2, "ssid": 5, "comment": "c"}
