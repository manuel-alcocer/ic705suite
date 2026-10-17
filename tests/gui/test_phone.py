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
            assert ids[:4] == ["af", "rf", "sql", "agc"] and "key_speed" not in ids
            # Five short columns: no more than five rows in any.
            columns = page.model.columns() if hasattr(page.model, "columns") else [
                [c for c in page.model.controls if c["group"] == g]
                for g, _l in page.model.visible_groups()]
            assert len(columns) == 5 and max(len(c) for c in columns) <= 5
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


def test_phone_opens_from_the_dashboard(tmp_path, run):
    """Every application with a graphical page opens from the dashboard (phone was left
    out of its list once)."""
    from ic705suite.apps import APPS
    from ic705suite.gui.dashboard import GUI_APPS
    from ic705suite.gui.phone import PhonePage
    from tests.gui.test_pages import window

    assert {a.id for a in APPS if a.ready} == GUI_APPS

    async def scenario():
        win, link = await window(tmp_path)
        try:
            win.dashboard.action_open("phone")
            await until(lambda: isinstance(win.page, PhonePage))
        finally:
            await link.close()

    run(scenario())


def test_typing_the_frequency_on_the_digits(tmp_path, run):
    """A click on the digits: the frequency in kHz to type (dots the thousands, a comma the
    decimals), the arrows and Backspace edit it, Enter tunes, Esc leaves it; meanwhile the
    page's keys (Esc, the space bar's PTT) wait."""
    from ic705suite.gui.widgets import khz_text, parse_khz
    from tests.gui.test_pages import key, window

    assert khz_text(28_750_340) == "28.750,34" and khz_text(7_150_000) == "7.150"
    assert parse_khz("145.650") == 145_650_000 and parse_khz("28.750,34") == 28_750_340
    assert parse_khz("14200") == 14_200_000 and parse_khz(",") is None

    async def scenario():
        win, link = await window(tmp_path, start_app="phone")
        page = win.page
        sim = link.core.station.transport
        digits = page.digits
        try:
            await until(lambda: digits.frequency)
            start = sim.frequency
            click(digits)
            assert digits.editing and digits.buffer == khz_text(start)
            for _ in range(len(digits.buffer)):
                await key(Qt.Key.Key_Backspace)
            for ch in "28.750,3":
                await key(None, text=ch)
            await key(Qt.Key.Key_Left)
            await key(None, text="4")                  # before the last digit: 28.750,43
            await key(Qt.Key.Key_Space, text=" ")     # not the PTT, not typed
            assert digits.buffer == "28.750,43" and not page.keyed
            await key(Qt.Key.Key_Return)
            await until(lambda: sim.frequency == 28_750_430)
            assert not digits.editing
            click(digits)
            await key(None, text="9")
            await key(Qt.Key.Key_Escape)               # as it was; the page stays open
            assert not digits.editing and win.page is page
            assert sim.frequency == 28_750_430
        finally:
            await link.close()

    run(scenario())


def click(widget) -> None:
    from PySide6.QtTest import QTest

    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def test_cb_mode(tmp_path, run):
    """CB mode: to channel 19 in FM, its two digits between down and up (round from 40 to
    1; a double click types one), the dial still tunes between them ("--"), and off goes
    back to where the dial was; the rig view has it too."""
    from PySide6.QtTest import QTest

    from ic705suite.rig import cb
    from tests.gui.test_pages import key, window

    assert len(cb.CHANNELS) == 40 and cb.channel_frequency(19) == 27_185_000
    assert cb.channel_for(27_255_000) == 23 and cb.channel_for(27_185_100) is None

    async def scenario():
        win, link = await window(tmp_path, start_app="phone")
        page = win.page
        sim = link.core.station.transport
        try:
            page.band_buttons["40m"].click()
            await until(lambda: sim.frequency == 7_150_000 and sim.mode == 0x00)
            await until(lambda: page.rig.get("frequency") == 7_150_000)
            assert not page.cb_button.isChecked() and not page.cb_up.isEnabled()
            page.cb_button.click()
            await until(lambda: sim.frequency == 27_185_000 and sim.mode == 0x05)   # FM
            await until(lambda: page.cb_button.isChecked() and page.cb_display.text == "19")
            page.cb_up.click()
            page.cb_up.click()                                     # quickly: 19, 20, 21
            await until(lambda: sim.frequency == cb.channel_frequency(21))
            assert page.cb_display.text == "21"
            await page.go_channel(1)
            page.cb_down.click()                                   # round: 1 -> 40
            await until(lambda: sim.frequency == cb.channel_frequency(40))
            # A double click: the channel typed, Enter; Esc leaves it.
            QTest.mouseDClick(page.cb_display, Qt.MouseButton.LeftButton)
            assert page.cb_display.editing
            await key(None, text="9")
            await key(Qt.Key.Key_Return)
            await until(lambda: page.rig.get("frequency") == 27_065_000)    # channel 9
            QTest.mouseDClick(page.cb_display, Qt.MouseButton.LeftButton)
            await key(None, text="4")
            await key(None, text="5")                              # 45: no such channel
            await key(Qt.Key.Key_Return)
            QTest.mouseDClick(page.cb_display, Qt.MouseButton.LeftButton)
            await key(None, text="3")
            await key(Qt.Key.Key_Escape)                           # as it was, page open
            assert not page.cb_display.editing and win.page is page
            assert sim.frequency == 27_065_000
            await page.action_tune(1)
            await until(lambda: page.rig.get("frequency") == 27_065_100)
            assert page.cb_button.isChecked() and page.cb_display.text == "--"
            page.cb_button.click()
            await until(lambda: sim.frequency == 7_150_000 and sim.mode == 0x00)
            await until(lambda: not page.cb_button.isChecked())
            page.cb_button.click()                    # the channel last used there
            await until(lambda: sim.frequency == 27_065_000)
        finally:
            await link.close()

    run(scenario())

    async def rig_view():
        win, link = await window(tmp_path, start_app="view")
        page = win.page
        sim = link.core.station.transport
        try:
            page.cb_button.click()
            await until(lambda: sim.frequency == 27_185_000 and page.cb_button.isChecked())
        finally:
            await link.close()

    run(rig_view())
