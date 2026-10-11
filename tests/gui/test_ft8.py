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
