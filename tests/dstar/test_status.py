"""The reflectors' status from their own pages (no network: pages given)."""

import json
import time

from ic705suite.apps.dstar import status

XLX = """<reflectorlist><reflector><name>XLX214</name>
<dashboardurl>https://dcs214.xreflector.net/dashboard/</dashboardurl>
<lastcontact>{now}</lastcontact><comment>D-STAR Spain</comment><country>Spain</country>
</reflector></reflectorlist>"""


def test_the_xlx_directory_and_our_terminal():
    found = status.parse_xlx(XLX.format(now=int(time.time())))
    assert found["XLX214"]["country"] == "Spain"
    term = status.terminal_regex("EA7KLX Z")
    assert term.search("EA7KLX-Z") and term.search("EA7KLX Z") and not term.search("EA7KLXB")


def test_a_reflector_with_the_xlxd_api():
    users = [{"callsign": "EA7KLX Z", "module": "D"}, {"callsign": "EA1ABC", "module": "D"},
             {"callsign": "F5XYZ", "module": "A"}]
    heard = [{"callsign": "EA1ABC", "name": "Pepe", "via": "EA1ZZ B", "time": "12:00"}]
    pages = {"https://x/index.php?api=users": json.dumps(users),
             "https://x/index.php?api=heard": json.dumps(heard)}
    item = {"to": "/XLX214D", "via": "int", "api": "https://x/index.php", "dashboard": ""}
    xlx = status.parse_xlx(XLX.format(now=int(time.time())))
    result = status.check(item, xlx, "EA7KLX Z", get=pages.__getitem__)
    assert result["online"] and result["users"] == 2 and result["linked"] is True
    assert result["nodes"] == ["EA7KLX Z", "EA1ABC"] and result["heard"][0]["name"] == "Pepe"
    assert result["country"] == "Spain"


def test_a_reflector_seen_on_its_dashboard_only():
    modules = ("<table><tr><td>D</td><td>Spain</td><td>7</td><td>REF214DL</td></tr>"
               "<tr><td>A</td><td>World</td><td>3</td><td>REF214AL</td></tr></table>")

    def get(url):
        if url.endswith("show=modules"):
            return modules
        if url.endswith("show=repeaters"):
            return "<td>EA1ZZ B</td>"
        raise OSError("not there")

    item = {"to": "/XLX214D", "via": "int", "api": "",
            "dashboard": "https://dcs214.xreflector.net/dashboard/"}
    result = status.check(item, {}, "EA7KLX Z", get=get)
    assert result["online"] and result["users"] == 7 and result["module_name"] == "Spain"
    assert result["linked"] is False                 # its own dashboard: not there, not linked
    guessed = dict(item, dashboard="")
    xlx = status.parse_xlx(XLX.format(now=int(time.time())))
    assert status.check(guessed, xlx, "EA7KLX Z", get=get)["linked"] is None   # unknown


def test_the_status_in_the_core_and_on_the_screen(tmp_path, monkeypatch):
    """The reflector in use checked by the core (pages given), its status in the state and
    on the terminal screen, its last heard instead of ours; R checks again."""
    import asyncio

    from textual.widgets import Static

    from ic705suite.apps.dstar import service as service_module
    from ic705suite.apps.dstar.app import DStarApp
    from ic705suite.config import Config
    from ic705suite.station.links import LocalLink

    monkeypatch.setattr(service_module, "FAST_POLL", 0.1)
    monkeypatch.setattr(service_module, "STATUS_POLL", 0.3)
    xlx = XLX.format(now=int(time.time()))
    users = [{"callsign": "EA4SIM Z", "module": "D"}, {"callsign": "EA1ABC", "module": "D"}]
    heard = [{"callsign": "EA1ABC", "name": "Pepe", "via": "", "time": "2026-10-18 10:21:33"}]
    pages = {status.XLX_API_URL: xlx, "https://x/api.php?api=users": json.dumps(users),
             "https://x/api.php?api=heard": json.dumps(heard)}
    asked = []

    def get(url, *args, **kwargs):
        asked.append(url)
        if url not in pages:
            raise OSError(url)
        return pages[url]

    monkeypatch.setattr(status, "http_get", get)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA4SIM", locator="IM77",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        sim = link.core.station.transport
        sim.mode = 0x17
        sim.dv_tx_calls = b"/XLX214D" + b"EA4SIM Z" + b"EA4SIM G"
        await link.call("dstar.reflector.save",
                        item={"to": "/XLX214D", "name": "Spain", "api": "https://x/api.php"})
        app = DStarApp(link, config=config)
        try:
            async with app.run_test(size=(140, 40)) as pilot:
                for _ in range(100):
                    await pilot.pause(0.1)
                    if (app.state.get("status") or {}).get("linked"):
                        break
                found = app.state["status"]
                assert found["online"] and found["users"] == 2 and found["linked"] is True
                assert app.state["internet"] is True
                await pilot.pause(0.3)
                shown = str(app.query_one("#reflector", Static).render())
                assert "2 nodes" in shown and "linked" in shown and "ONLINE" in shown
                onair = str(app.query_one("#onair", Static).render())
                assert "Last heard on the reflector" in onair and "10:21" in onair
                every = await link.call("dstar.statuses")
                assert "/XLX214D" in every["statuses"]
                before = len(asked)
                await pilot.press("r")
                for _ in range(30):
                    await pilot.pause(0.1)
                    if len(asked) > before:
                        break
                assert len(asked) > before
                await pilot.press("escape")
                await pilot.pause(0.3)
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_status_pieces():
    from ic705suite.apps.dstar.app import heard_list, reflector_status, short_time

    assert reflector_status({"ur": "CQCQCQ"}) == []
    assert reflector_status({"ur": "/XLX214D"})[0][1] == "dim"
    pieces = reflector_status({"ur": "/XLX214D", "status": {"online": False, "linked": False}})
    assert pieces[0][1] == "bad" and pieces[-1][1] == "warn"
    assert short_time("18.10.2026 09:05") == "09:05"
    title, rows = heard_list({"ur": "/XLX214D", "status": None}, [])
    assert title == "Last heard here" and rows == []


def test_a_tabbed_dashboard():
    """DCS214's own dashboard (EA7IYR's): no ?show= pages, its nodes and heard in tabs."""
    home = "<a href='?tab=nodes'>Nodos</a><a href='?tab=heard'>Heard</a>"
    nodes = ("<tr><td>EA7IYR G</td><td>GPS</td><td>***.***.***.125</td><td>D</td>"
             "<td>D-STAR SPAIN</td><td>DPlus</td></tr>"
             "<tr><td>EA4SIM Z</td><td>GPS</td><td>***.***.***.9</td><td>D</td>"
             "<td>D-STAR SPAIN</td><td>DCS</td></tr>"
             "<tr><td>PA7LIM</td><td>GPS</td><td>***.***.***.249</td><td>Z</td>"
             "<td>ECHO</td><td>DCS</td></tr>")
    heard = ("<tr><td>Spain</td><td>ED2YAV</td><td>DATA</td><td>Equipo indicado</td>"
             "<td>GPS</td><td>EA7IYR G</td><td>D</td><td>D-STAR SPAIN</td><td>XLX214</td>"
             "<td>Sunday Sun Oct 18 06:15:14 2026</td></tr>"
             "<tr><td>Guatemala</td><td>TG9AUO</td><td>Jose M Ortiz</td><td>—</td>"
             "<td>GPS</td><td>EA7IYR G</td><td>D</td><td>D-STAR SPAIN</td><td>XLX214</td>"
             "<td>Sunday Sun Oct 18 05:01:24 2026</td></tr>"
             "<tr><td>Spain</td><td>EA7KLX</td><td>Manuel Alcocer</td><td>IC-705</td>"
             "<td>Portable / base</td><td>GPS</td><td>EA7KLX Z</td><td>D</td>"
             "<td>D-STAR SPAIN</td><td>XLX214</td><td>Saturday Sat Oct 17 22:08:58 2026</td>"
             "</tr>")
    base = "https://dcs214.xreflector.net/dashboard/"
    pages = {base + "index.php?show=modules": home, base + "?tab=nodes": nodes,
             base + "?tab=heard": heard}

    def get(url):
        if url not in pages:
            raise OSError(url)
        return pages[url]

    item = {"to": "/XLX214D", "via": "int", "api": "", "dashboard": base}
    result = status.check(item, {}, "EA4SIM Z", get=get)
    assert result["online"] and result["users"] == 2 and result["linked"] is True
    assert result["module_name"] == "D-Star Spain"
    assert [h["callsign"] for h in result["heard"]] == ["ED2YAV", "TG9AUO", "EA7KLX"]
    assert result["heard"][0]["name"] == "" and result["heard"][1]["name"] == "Jose M Ortiz"
    assert result["heard"][2] == {"callsign": "EA7KLX", "name": "Manuel Alcocer",
                                  "via": "EA7KLX Z", "time": "22:08:58"}
    assert status.check(item, {}, "EA7KLX Z", get=get)["linked"] is False


def test_the_stations_on_the_map():
    from ic705suite.apps.dstar.app import base_call, map_stations

    now = 1_000_000.0
    rows = [{"name": "EA7ABC-9", "lat": 37.4, "lon": -6.0, "received": now - 60},
            {"name": "EA7ABC-9", "lat": 37.0, "lon": -6.5, "received": now - 3600},
            {"name": "EA1WX", "lat": 43.3, "lon": -8.4, "received": now - 120},
            {"name": "OLD", "lat": 40.0, "lon": -3.7, "received": now - 8 * 86400},
            {"name": "NOWHERE", "lat": None, "lon": None, "received": now}]
    shown = map_stations(rows, now)
    assert [r["name"] for r in shown] == ["EA1WX", "EA7ABC-9"]    # oldest first
    assert shown[1]["lat"] == 37.4                                 # its latest place
    assert base_call("EA7ABC-9") == base_call("EA7ABC /705") == "EA7ABC"
