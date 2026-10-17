"""The phone application: bands at their phone segment, the voice modes and filters, only
the controls phone needs, memories tuned and saved, and the PTT (mouse, space bar, the
radio's microphone) on the simulated radio."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from ic705suite.config import Config
from ic705suite.station.links import LocalLink
from tests.gui.test_pages import until


def space(kind) -> None:
    QApplication.sendEvent(QApplication.focusWidget() or QApplication.activeWindow(),
                           QKeyEvent(kind, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier,
                                     " "))


def test_the_phone_page(tmp_path, run):
    async def scenario():
        from ic705suite.gui.app import MainWindow
        from ic705suite.gui.phone import PhonePage

        config = Config(connection="sim", swr_web=False, callsign="EA7KLX", tx_enabled=True,
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="phone")
        win.resize(1360, 900)
        win.show()
        win.activateWindow()
        page = win.page
        assert isinstance(page, PhonePage)
        sim = link.core.station.transport
        try:
            await until(lambda: page.rig.get("connected"))
            ids = [c["id"] for c in page.model.controls]
            assert ids[:3] == ["af", "sql", "rf"] and "key_speed" not in ids and "mic" in ids
            page.band_buttons["40m"].click()
            await until(lambda: sim.frequency == 7_150_000 and sim.mode == 0x00)    # LSB
            await until(lambda: page.band_buttons["40m"].isChecked()
                        and page.mode_buttons["LSB"].isChecked())
            page.mode_buttons["USB"].click()
            await until(lambda: sim.mode == 0x01)
            page.filter_buttons["2"].click()
            await until(lambda: sim.filter == 2)
            # A memory: the current frequency saved, then tuned back to from elsewhere.
            await until(lambda: page.current_memory() is not None
                        and page.current_memory()["mode"] == "USB")
            await page.memories._save({**page.current_memory(), "name": "net"})
            assert [m["name"] for m in page.memories.memories] == ["net"]
            page.band_buttons["20m"].click()
            await until(lambda: sim.frequency == 14_200_000)
            page.memories._tune_suite(0)
            await until(lambda: sim.frequency == 7_150_000 and sim.mode == 0x01)
            # The PTT with the radio's microphone: the mouse held, then the space bar.
            page.source_box.setCurrentIndex(1)
            page.ptt_button.pressed.emit()
            await until(lambda: sim.ptt and page.keyed)
            assert page.ptt_button.text() == "TX"
            page.ptt_button.released.emit()
            await until(lambda: not sim.ptt and not page.keyed)
            page.grid.setFocus()
            space(QEvent.Type.KeyPress)
            await until(lambda: sim.ptt)
            space(QEvent.Type.KeyRelease)
            await until(lambda: not sim.ptt)
            page.key_box.setCurrentIndex(1)                  # space toggles
            space(QEvent.Type.KeyPress)
            space(QEvent.Type.KeyRelease)
            await until(lambda: sim.ptt)
            space(QEvent.Type.KeyPress)
            await until(lambda: not sim.ptt)
        finally:
            await link.close()

    run(scenario())
