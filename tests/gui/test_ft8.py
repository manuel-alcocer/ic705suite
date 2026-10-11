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
