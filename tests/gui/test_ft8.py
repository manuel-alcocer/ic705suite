"""FT8 in the window: the same keys as the terminal, and the mouse (band list, Stop/Start,
CQ only)."""

from PySide6.QtCore import Qt

from ic705suite.apps.ft8 import service as service_module
from ic705suite.config import Config
from ic705suite.station.links import LocalLink
from tests.ft8.test_ft8 import fake_jt9
from tests.gui.test_pages import key, until


def test_band_activity_in_the_window(tmp_path, run, monkeypatch):
    fake_jt9(monkeypatch)

    async def scenario():
        from ic705suite.gui.app import MainWindow
        from ic705suite.gui.ft8 import Ft8Page

        config = Config(connection="sim", swr_web=False, callsign="EA7KLX",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="ft8")
        win.resize(1200, 800)
        win.show()
        win.activateWindow()
        page = win.page
        assert isinstance(page, Ft8Page)
        try:
            await until(lambda: page.table.rowCount() >= 4)
            messages = [page.table.item(r, 4).text() for r in range(page.table.rowCount())]
            assert "CQ EA1ABC IN73" in messages and "EA7KLX DL1ABC -12" in messages
            row = messages.index("EA7KLX DL1ABC -12")
            assert page.table.item(row, 4).font().bold()                 # sent to us
            assert link.core.ft8.on and page.toggle_button.text().endswith("Stop")

            await key(Qt.Key.Key_C)                                       # CQ only
            assert page.cq_box.isChecked()
            shown = {page.table.item(r, 4).text() for r in range(page.table.rowCount())}
            assert "G4XYZ F5ABC JN12" not in shown
            page.cq_box.setChecked(False)                                 # the mouse
            assert not page.cq_only

            sim = link.core.station.transport
            band = service_module.bands.band_for(sim.frequency).name
            await key(Qt.Key.Key_Right)
            await until(lambda: sim.frequency == service_module.DIALS[band])
            page.band_box.activated.emit(page.band_box.findText("20m"))   # chosen from the list
            await until(lambda: sim.frequency == 14_074_000)
            await until(lambda: page.band_box.currentText() == "20m")

            page.toggle_button.click()
            await until(lambda: not link.core.ft8.on and not page.state.get("on"))
            assert page.toggle_button.text().endswith("Start")
            await key(Qt.Key.Key_S)
            await until(lambda: link.core.ft8.on)
            await key(Qt.Key.Key_Escape)
            await until(lambda: link.closed.is_set())
            assert not link.core.ft8.on
        finally:
            await link.close()
            win.close()

    run(scenario())


def test_a_qso_from_the_window(tmp_path, run, monkeypatch):
    from tests.ft8.test_ft8 import short_turns

    short_turns(monkeypatch)

    async def scenario():
        from ic705suite.gui.app import MainWindow

        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="ft8")
        win.resize(1300, 800)
        win.show()
        page = win.page
        try:
            await until(lambda: page.state.get("on"))
            await link.call("ft8.band", band="20m")
            await until(lambda: any(d and d.get("message") == "CQ DL1ABC JO62"
                                    for _s, d, _c, _st in page.lines))
            row = next(i for i, (_s, d, _c, _st) in enumerate(page.lines)
                       if d and d.get("message") == "CQ DL1ABC JO62")
            page.table.cellDoubleClicked.emit(row, 4)         # the mouse answers
            await until(lambda: any(cells[4] == "DL1ABC EA7KLX 73"
                                    for _s, _d, cells, _st in page.lines), 15)
            await until(lambda: not (page.state.get("tx") or {}).get("on"))
            texts = [page.table.item(r, 4).text() for r in range(page.table.rowCount())]
            assert "DL1ABC EA7KLX 73" in texts
            assert page.table.item(texts.index("DL1ABC EA7KLX 73"), 1).text() == "TX"
            # Beside it, the QSO alone: what we sent and what DL1ABC said.
            qso = [page.qso_table.item(r, 4).text() for r in range(page.qso_table.rowCount())]
            assert {"DL1ABC EA7KLX IM77", "EA7KLX DL1ABC -07", "DL1ABC EA7KLX 73"} <= set(qso)
            assert all("DL1ABC" in line for line in qso)
            await until(lambda: page.qso_bar.value() == 100)          # the bar: complete
            assert "QSO with DL1ABC complete" in page.qso_bar.format()
        finally:
            await link.close()
            win.close()

    run(scenario())


def test_opened_from_the_dashboard(tmp_path, run, monkeypatch):
    """F8 on the dashboard opens FT8 in the window (not 'not in the graphical client yet')."""
    fake_jt9(monkeypatch)

    async def scenario():
        from ic705suite.gui.ft8 import Ft8Page
        from tests.gui.test_pages import window

        win, link = await window(tmp_path)
        try:
            assert win.page is win.dashboard
            await key(Qt.Key.Key_F8)
            await until(lambda: isinstance(win.page, Ft8Page))
            await until(lambda: link.latest.get("ft8", {}).get("on"))
        finally:
            await link.close()

    run(scenario())


def test_what_we_transmit_with(tmp_path, run, monkeypatch):
    """The audio frequency, the level and the power from the boxes and the keys."""
    fake_jt9(monkeypatch)

    async def scenario():
        from ic705suite.gui.app import MainWindow

        config = Config(connection="sim", swr_web=False, callsign="EA7KLX",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="ft8")
        win.resize(1300, 800)
        win.show()
        win.activateWindow()
        page = win.page
        ft8 = None
        try:
            await until(lambda: page.state.get("on"))
            ft8 = link.core.ft8
            await until(lambda: page.freq_spin.value() == 1500 and page.level_spin.value() == 50)
            page.freq_spin.setValue(1720)                                  # the mouse
            await until(lambda: page.state["tx"]["freq"] == 1720)
            await key(Qt.Key.Key_BracketRight, text="]")                   # 25 Hz up
            await until(lambda: ft8.tx_freq == 1745)
            await until(lambda: page.freq_spin.value() == 1745)
            page.level_spin.setValue(35)
            await until(lambda: page.state["tx"]["level"] == 0.35)
            await key(Qt.Key.Key_Plus, text="+")
            await until(lambda: ft8.tx_level == 0.4)
            await until(lambda: page.level_spin.value() == 40)

            rig = link.core.station.rig
            page.power_spin.setValue(2.0)                                  # 2 W of 10
            await until(lambda: page.rig.get("controls", {}).get("power") == 20)
            await key(Qt.Key.Key_Period, text=".")                         # a watt more
            await until(lambda: rig.state.controls.get("power") == 30)
            await until(lambda: page.power_spin.value() == 3.0)
            assert page.alc_bar.value() == 0
        finally:
            await link.close()
            win.close()

    run(scenario())


def test_the_waterfall(tmp_path, run, monkeypatch):
    """Rows come while the page is open; a click sets the TX frequency, a click on a call
    chooses that station and a double click answers it."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    fake_jt9(monkeypatch)
    monkeypatch.setattr(service_module.encode, "find_ft8code", lambda configured="": "ft8code")

    async def scenario():
        from ic705suite.gui.app import MainWindow

        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="ft8")
        win.resize(1300, 860)
        win.show()
        page = win.page
        view = page.waterfall
        try:
            await until(lambda: view.total >= 6)                     # rows, asked by the page
            await until(lambda: page.table.rowCount() >= 4)
            x = round(view._x(2200))
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(x, 23))   # a gap
            await until(lambda: abs(link.core.ft8.tx_freq - 2200) <= 3)     # a pixel's Hz
            clicked = link.core.ft8.tx_freq
            await until(lambda: view.tx_freq == clicked)

            # A call on the waterfall: its line chosen in the list.
            start, decode = next((s, d) for s, d, _c, _st in page.lines
                                 if d and d.get("message") == "CQ EA1ABC IN73")
            view.labels.clear()
            view.times.clear()
            view.times.append((view.total - 30, start + 7))       # 30 rows down
            view.add_labels(start, [decode])
            view.repaint()
            rect, label = view._label_rects[0]
            assert label.text == "EA1ABC"
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=rect.center())
            row = page.table.currentRow()
            assert page.lines[row][1]["message"] == "CQ EA1ABC IN73"
            assert link.core.ft8.tx_freq == clicked                  # not moved by it
            QTest.mouseDClick(view, Qt.MouseButton.LeftButton, pos=rect.center())
            await until(lambda: link.core.ft8.tx_on and link.core.ft8.qso.them == "EA1ABC")
            await link.call("ft8.halt")
        finally:
            await link.close()
            win.close()

    run(scenario())


def test_the_core_lost_and_back(tmp_path, run, monkeypatch):
    """FT8 on another machine's core: the core goes away (restarted) and comes back. The
    window shows the radio disconnected, starts again by itself, and Esc always leaves."""
    import asyncio

    from ic705suite.rig.icom.sim import SimTransport
    from ic705suite.station.core import Core
    from ic705suite.station.links import RemoteLink
    from ic705suite.station.server import CoreServer
    from tests.cw.test_client_server import start_server

    fake_jt9(monkeypatch)

    async def scenario():
        from ic705suite.gui.app import MainWindow

        config = Config(connection="sim", swr_web=False, callsign="EA7KLX",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        core, stop, task, port = await start_server(config, SimTransport(stations=[], seed=5))
        link = RemoteLink(f"127.0.0.1:{port}", "secret")
        link.RETRY = 0.3
        win = MainWindow(link, config, start_app="ft8")
        win.resize(1200, 800)
        win.show()
        page = win.page
        try:
            await until(lambda: page.state.get("on") and page.table.rowCount() >= 2)
            stop.set()
            await task
            await core.stop()
            await until(lambda: page.status.rig.get("connected") is False)
            assert "connection to the server lost" in page.message.text()

            core = Core(SimTransport(stations=[], seed=6), config)
            await core.start()
            stop = asyncio.Event()
            task = asyncio.create_task(CoreServer(core, "127.0.0.1", port, "secret").run(stop))
            await until(lambda: core.ft8.on)                   # started again by the page
            await until(lambda: page.status.rig.get("connected"))

            stop.set()                                          # gone again: Esc still leaves
            await task
            await core.stop()
            await until(lambda: not link.connected)
            await key(Qt.Key.Key_Escape)
            await until(lambda: win.finished.done(), 5)
        finally:
            await link.close()
            stop.set()
            win.close()

    run(scenario())


def test_the_tone_button(tmp_path, run, monkeypatch):
    fake_jt9(monkeypatch)

    async def scenario():
        from ic705suite.gui.app import MainWindow

        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", tx_enabled=True,
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="ft8")
        win.resize(1200, 800)
        win.show()
        win.activateWindow()
        page = win.page
        sim = None
        try:
            await until(lambda: page.state.get("on"))
            sim = link.core.station.transport
            await link.call("ft8.band", band="20m")
            page.tone_button.click()                                  # the mouse
            await until(lambda: sim.ptt and page.tone_button.text().endswith("Stop tone"))
            await key(Qt.Key.Key_T)                                   # T again: off
            await until(lambda: not sim.ptt and page.tone_button.text().endswith("Tone"))
        finally:
            if sim is not None:
                assert not sim.ptt
            await link.close()
            win.close()

    run(scenario())


def test_the_map_in_the_page(tmp_path, run, monkeypatch):
    """The map tab: our place from the core, the stations heard on it, room for it."""
    from ic705suite import geo
    from tests.ft8.test_worked_ft8 import CTY

    fake_jt9(monkeypatch)
    table = geo.Cty(CTY)
    monkeypatch.setattr(geo, "cty", lambda: table)

    async def scenario():
        from ic705suite.gui.app import MainWindow

        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77AH",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="ft8")
        win.resize(1300, 860)
        win.show()
        page = win.page
        try:
            await until(lambda: page.map.home is not None)
            assert page.map.home == geo.locator_to_latlon("IM77AH")
            await until(lambda: "EA1ABC" in page.map.heard)
            ea1 = page.map.heard["EA1ABC"]
            assert (ea1.lat, ea1.lon) == tuple(round(v, 3) for v in
                                               geo.locator_to_latlon("IN73"))
            assert page.map.heard["DL1ABC"].approx               # placed by its country
            before = page.panes.sizes()[0]
            page.top_tabs.setCurrentWidget(page.map_frame)
            assert page.panes.sizes()[0] > before                # the map gets room
            row = next(i for i, (_s, d, _c, _st) in enumerate(page.lines)
                       if d and d.get("sender") == "EA1ABC")
            assert page.table.item(row, 5).text() == "Spain"      # English: no locale here
            assert page.table.item(row, 6).text().endswith(" km 6°")
            assert page.table.item(row, 7).text() == ""          # no logbook here: no mark
        finally:
            await link.close()
            win.close()

    run(scenario())
