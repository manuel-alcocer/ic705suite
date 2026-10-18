"""D-STAR in the window, as the suite's pages: the lamps and the reflector, the station on
air, the history (a row clicked shows that over; Esc, live again), the reflector chosen
and changed ([ ] and C, or the list and the button), the MY note typed in place, on the
simulated radio in DV and INT Terminal Mode."""

from PySide6.QtCore import Qt

from ic705suite.apps.dstar import service as service_module
from ic705suite.config import Config
from ic705suite.station.links import LocalLink
from tests.gui.test_pages import key, until


def test_the_dstar_page(tmp_path, run, monkeypatch):
    monkeypatch.setattr(service_module, "FAST_POLL", 0.1)
    monkeypatch.setattr(service_module, "RX_END_GRACE", 0.4)
    monkeypatch.setattr(service_module, "lookup_name", lambda call: ("Pepe", "Sevilla"))

    async def scenario():
        from ic705suite.gui.app import MainWindow
        from ic705suite.gui.dstar import DStarPage

        config = Config(connection="sim", swr_web=False, callsign="EA4SIM", locator="IM77",
                        dstar_server="server1.dstar.es",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.core.dstar.call("reflector.save", {"item": {
            "to": "/XLX030C", "name": "Test", "server": "server1.dstar.es"}})
        win = MainWindow(link, config, start_app="dstar")
        win.resize(1300, 900)
        win.show()
        page = win.page
        assert isinstance(page, DStarPage)
        await until(lambda: link.connected)
        sim = link.core.station.transport
        sim.mode = 0x17
        sim.dv_tx_calls = b"/XLX214D" + b"EA4SIM Z" + b"EA4SIM G"
        try:
            await until(lambda: page.state.get("radio_mode") == "int"
                        and page.state.get("mode") == "DV" and page.reflector_box.count() > 1)
            assert "D-STAR Spain" in page.reflector_label.text()
            assert "INT" in page.lamps.text() and "⚠" not in page.reflector_label.text()
            sim.dv_receive("EA7ABC", "705", message="hola")
            await until(lambda: "Pepe" in page.air_label.text())
            assert "EA7ABC /705" in page.air_label.text() and "RX" in page.air_label.text()
            sim.dv_end()
            await until(lambda: page.table.rowCount() == 1
                        and page.table.item(0, 6).text() == "hola")
            assert "Last heard here" in page.air_label.text()
            # The reflector's status (its pages, given here): the lamps, the box, its heard
            link.core.dstar.statuses["/XLX214D"] = {
                "online": True, "users": 5, "module_name": "D-Star Spain", "linked": True,
                "heard": [{"callsign": "EA1ZZZ", "name": "Ana", "via": "", "time": "10:21"}],
                "nodes": [], "comment": "", "country": "ES", "dashboard": "", "checked": 0}
            link.core.dstar.internet = True
            link.core.dstar._touch()
            await until(lambda: "5 nodes" in page.reflector_label.text())
            assert "linked" in page.reflector_label.text() and "NET" in page.lamps.text()
            await until(lambda: "EA1ZZZ" in page.air_label.text())
            assert "Last heard on the reflector" in page.air_label.text()
            link.core.dstar.statuses.clear()
            link.core.dstar._touch()
            page.table.cellClicked.emit(0, 1)              # that over in the on-air box
            assert page.review["callsign"] == "EA7ABC" and "HISTORY" in page.air_label.text()
            await key(Qt.Key.Key_Escape)                   # live again; the page stays
            assert page.review is None and win.page is page
            # [ ] choose, C changes: the TO written (R1/R2 kept) and read back
            page.table.setFocus()
            await key(None, text="]")
            assert page.choice and page.choice != "/XLX214D"
            assert "C changes to" in page.reflector_label.text()
            chosen = page.choice
            await key(None, text="c")
            await until(lambda: sim.dv_tx_calls[:8] == chosen.encode().ljust(8))
            assert sim.dv_tx_calls[8:] == b"EA4SIM Z" + b"EA4SIM G"
            await until(lambda: "PTT" in page.message.text())
            # The list and the button do the same
            index = page.reflector_box.findData("/XLX214D")
            page.reflector_box.setCurrentIndex(index)
            page.reflector_box.activated.emit(index)       # chosen with the mouse
            page.change_button.click()
            await until(lambda: sim.dv_tx_calls[:8] == b"/XLX214D")
            page.note_edit.setText("P")
            page.note_edit.returnPressed.emit()
            await until(lambda: sim.dv_my == b"EA4SIM  P   ")
        finally:
            await link.close()

    run(scenario())


def test_dstar_opens_from_the_dashboard(tmp_path, run):
    from ic705suite.gui.dstar import DStarPage
    from tests.gui.test_pages import window

    async def scenario():
        win, link = await window(tmp_path)
        try:
            await key(Qt.Key.Key_F4)                       # as in the terminal
            await until(lambda: isinstance(win.page, DStarPage))
            await key(Qt.Key.Key_Escape)
            await until(lambda: win.page is win.dashboard)
        finally:
            await link.close()

    run(scenario())


def test_dprs_views_and_the_beacon_dialog(tmp_path, run, monkeypatch):
    monkeypatch.setattr(service_module, "FAST_POLL", 0.1)
    monkeypatch.setattr(service_module, "RX_END_GRACE", 0.4)
    monkeypatch.setattr(service_module, "DPRS_AFTER", 0.2)
    monkeypatch.setattr(service_module, "lookup_name", lambda call: ("", ""))

    async def scenario():
        from ic705suite.gui.app import MainWindow
        from ic705suite.gui.dstar import BeaconDialog

        config = Config(connection="sim", swr_web=False, callsign="EA4SIM", locator="IM77",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="dstar")
        win.resize(1300, 900)
        win.show()
        page = win.page
        await until(lambda: link.connected)
        sim = link.core.station.transport
        sim.mode = 0x17
        try:
            await until(lambda: page.state.get("dprs"))
            sim.dv_position("EA7ABC-9", 37.39, -5.98)
            sim.dv_receive("EA7ABC", "705")
            await until(lambda: page.dprs_table.rowCount() == 1)
            assert page.dprs_table.item(0, 1).text() == "EA7ABC-9"
            assert "km" in page.dprs_table.item(0, 3).text()
            page.table.setFocus()
            await key(None, text="v")                      # V: the next view
            assert page.views.currentIndex() == 1 and page.view == "dprs"
            await key(None, text="v")
            assert page.view == "weather"
            await key(None, text="v")                      # the map: the window's own view
            assert page.view == "map" and page.views.currentWidget() is page.map
            assert "EA7ABC-9" in page.map.heard and page.map.home is not None
            spot = page.map._xy(37.39, -5.98)
            page.map.repaint()                             # its spots, where they are drawn
            assert "EA7ABC-9" in page.map.under(spot) and "km" in page.map.under(spot)
            page.map.station_double_clicked.emit(0.0, page.map.heard["EA7ABC-9"].decode)
            assert page.view == "dprs" and page.dprs_table.currentRow() == 0
            await key(None, text="v")
            await key(None, text="v")
            await key(None, text="v")
            assert page.view == "history"                  # and round again
            await key(None, text="v")
            await key(None, text="v")
            await key(None, text="b")                      # B: the beacon, from the radio
            await until(lambda: page.findChildren(BeaconDialog))
            dialog = page.findChildren(BeaconDialog)[0]
            assert dialog.symbol.count() == 4 and dialog.source.currentData() == "gps"
            dialog.send.setChecked(True)
            dialog.source.setCurrentIndex(1)
            dialog.locator.setText("IN80DK")
            dialog.comment.setText("hola")
            dialog._save()
            await until(lambda: (page.state.get("dprs") or {}).get("send"))
            assert page.state["dprs"]["source"] == "manual"
            assert page.state["dprs"]["comment"] == "hola"
            assert "confirmed" in page.message.text()
        finally:
            await link.close()

    run(scenario())


def test_the_map_groups_stations_on_the_same_spot(qapp):
    """Two stations 13 km apart (EA2AFZ-W and EB2EMZ-W, Bilbao) at a world's zoom: one dot,
    called "+1", both under the mouse; the view fitted to them; our call not in the way."""
    import time

    from ic705suite.apps.dstar.app import map_stations
    from ic705suite.gui.dstarmap import DStarMap

    now = time.time()
    rows = [{"name": "EA2AFZ-W", "lat": 43.2535, "lon": -2.911, "kind": "position",
             "symbol": "/r", "received": now - 60, "via": "ED2YAV"},
            {"name": "EB2EMZ-W", "lat": 43.1333, "lon": -2.95, "kind": "weather",
             "symbol": "/_", "received": now - 1200, "via": "ED2YAV"}]
    m = DStarMap()
    m.resize(1100, 520)
    m.set_home(37.3125, -5.9583, "EA7KLX")
    m.set_stations(map_stations(rows, now))
    m.show()
    m.grab()                                         # drawn: fitted to them
    assert m.zoom > 1 and 36 < m.center[0] < 44
    m.zoom, m.center = 1.0, (25.0, -6.0)
    m.grab()
    assert [h.call for _r, h in m._spots] == ["EA2AFZ-W"]
    assert [h.call for h in m.groups["EA2AFZ-W"]] == ["EA2AFZ-W", "EB2EMZ-W"]
    assert m.under(m._xy(43.2535, -2.911)).startswith("EA2AFZ-W, EB2EMZ-W")
