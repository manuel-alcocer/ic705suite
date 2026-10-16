"""The graphical client with the simulator: the terminal's keys do the same, and the mouse
works too."""

import asyncio

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ic705suite.config import Config


async def until(condition, timeout: float = 15.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def key(key, modifiers=Qt.KeyboardModifier.NoModifier, text=None) -> None:
    """A key press where the focus is, as the operator's; then the loop runs the action."""
    widget = QApplication.focusWidget()
    assert widget is not None, "nothing has the focus"
    if text is not None:
        QTest.keyClicks(widget, text)
    else:
        QTest.keyClick(widget, key, modifiers)
    await asyncio.sleep(0.05)


async def window(tmp_path, start_app=None):
    from ic705suite.gui.app import MainWindow
    from ic705suite.launcher import start_link
    from ic705suite.station.links import LocalLink

    config = Config(connection="sim", swr_web=False, callsign="EA7KLX",
                    database_url=f"sqlite:///{tmp_path / 'suite.db'}")
    link = LocalLink(None, config)
    win = MainWindow(link, config, start_app=start_app)
    win.resize(1300, 850)
    win.show()
    win.activateWindow()
    if start_app is None:
        start_link(link)
    await until(lambda: link.latest.get("rig", {}).get("connected"))
    return win, link


def rig(link) -> dict:
    return link.latest.get("rig", {})



def test_the_applications_as_icons_all_in_view(tmp_path, run):
    """Every application an icon, all of them in view at any width (no scroll bar): the
    arrows choose, Enter opens the one chosen, a click opens one too."""
    from ic705suite.gui.ft8 import Ft8Page
    from ic705suite.gui.view import ViewPage

    async def scenario():
        win, link = await window(tmp_path)
        try:
            grid = win.dashboard.table
            assert grid.count() == len(win.dashboard.apps) >= 7
            for width in (1300, 900, 700):
                win.resize(width, 850)
                await asyncio.sleep(0.1)
                for row in range(grid.count()):
                    rect = grid.visualItemRect(grid.item(row))
                    assert grid.viewport().rect().contains(rect), (width, row)
            assert grid.currentRow() == 0 and "F2" in grid.item(0).text()
            grid.setFocus()
            await key(Qt.Key.Key_Return)
            await until(lambda: isinstance(win.page, ViewPage))
            await key(Qt.Key.Key_Escape)
            await until(lambda: win.page is win.dashboard)
            ft8 = next(r for r in range(grid.count()) if "F8" in grid.item(r).text())
            QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton,
                             pos=grid.visualItemRect(grid.item(ft8)).center())
            await until(lambda: isinstance(win.page, Ft8Page))
        finally:
            await link.close()

    run(scenario())

def test_dashboard_opens_the_view_and_comes_back(tmp_path, run):
    async def scenario():
        win, link = await window(tmp_path)
        try:
            from ic705suite.gui.view import ViewPage

            assert win.page is win.dashboard
            await key(Qt.Key.Key_F2)                                   # as in the terminal
            await until(lambda: isinstance(win.page, ViewPage))
            await key(Qt.Key.Key_Escape)
            await until(lambda: win.page is win.dashboard)
            # An application not there yet (in the terminal either) says so.
            win.dashboard.action_open("dstar")
            assert "coming soon" in win.dashboard.messages[-1][1]
            await key(Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
            await until(lambda: win.finished.done())
        finally:
            await link.close()

    run(scenario())


def test_view_keys_tune_change_mode_and_edit_a_control(tmp_path, run):
    async def scenario():
        win, link = await window(tmp_path, start_app="view")
        page = win.page
        await until(lambda: page.model.controls and rig(link).get("frequency"))
        start = rig(link)["frequency"]
        await key(Qt.Key.Key_PageUp)
        await until(lambda: rig(link)["frequency"] == (start // 100 + 1) * 100)
        mode = rig(link)["mode"]
        await key(Qt.Key.Key_F3)
        await until(lambda: rig(link)["mode"] != mode)
        # Tab: the next option; Enter, type, Enter: a new value, as on the terminal panel.
        first = page.model.selected
        await key(Qt.Key.Key_Tab)
        assert page.model.selected != first
        page.model.selected = "af"
        await key(Qt.Key.Key_Return)
        assert page.model.editing
        await key(None, text="40")
        assert page.model.buffer == "40"
        await key(Qt.Key.Key_Return)
        await until(lambda: rig(link).get("controls", {}).get("af") == 40)
        assert not page.model.editing
        # The arrows turn the option while editing; Esc puts the old value back.
        await key(Qt.Key.Key_Return)
        await key(Qt.Key.Key_Right)
        await until(lambda: rig(link).get("controls", {}).get("af") == 41)
        await key(Qt.Key.Key_Escape)
        await until(lambda: rig(link).get("controls", {}).get("af") == 40)
        # "/" opens the command line; Esc empties it and goes back to the panel.
        await key(Qt.Key.Key_Slash, text=None)
        assert QApplication.focusWidget() is page.entry and page.entry.text() == "/"
        await key(Qt.Key.Key_Escape)
        assert QApplication.focusWidget() is page.grid and page.entry.text() == ""
        # Esc on the panel leaves; opened on its own, the window is done.
        await key(Qt.Key.Key_Escape)
        await until(lambda: win.finished.done())

    run(scenario())


def test_the_mouse_tunes_on_the_scope_and_turns_a_control(tmp_path, run):
    async def scenario():
        win, link = await window(tmp_path, start_app="view")
        page = win.page
        try:
            await until(lambda: page.scope.view.end > page.scope.view.start
                        and page.model.controls)
            scope, clicked = page.scope, []
            scope.clicked.connect(clicked.append)
            point = QPointF(scope.width() * 0.25, 40).toPoint()
            QTest.mouseClick(scope, Qt.MouseButton.LeftButton, pos=point)
            assert clicked == [round(scope.hz_at(point.x()) / 10) * 10]
            await until(lambda: rig(link)["frequency"] == clicked[0])
            # The wheel on an option turns it.
            page.model.selected = "af"
            before = rig(link)["controls"]["af"]
            page.grid.request.emit("adjust", 1)
            await until(lambda: rig(link)["controls"]["af"] == before + 1)
        finally:
            await link.close()

    run(scenario())


def test_terminal_dialogs_become_qt_dialogs(tmp_path, run):
    from ic705suite.ui.widgets import ChoiceScreen, NameScreen

    async def scenario():
        win, link = await window(tmp_path)
        try:
            page = win.dashboard
            asking = asyncio.ensure_future(page.push_screen_wait(ChoiceScreen(
                "Save channels", "2 channels are not saved", [("audio", "With audio"),
                                                              ("no", "Don't save")])))
            await until(lambda: QApplication.activeModalWidget() is not None)
            buttons = [b for b in QApplication.activeModalWidget().findChildren(
                __import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)]
            next(b for b in buttons if b.text() == "Don't save").click()
            assert await asking == "no"
            asking = asyncio.ensure_future(page.push_screen_wait(NameScreen(
                "Name the channel", "No callsign. Name:")))
            await until(lambda: QApplication.activeModalWidget() is not None)
            await key(None, text="Pepe")
            await key(Qt.Key.Key_Return)
            assert await asking == "Pepe"
        finally:
            await link.close()

    run(scenario())


def test_front_panel_knobs_switches_and_drop_downs(tmp_path, run):
    """The rig view's panel as a radio's: a click flips a switch, dragging a knob up turns it,
    a drop-down offers its choices; each is set on the rig."""
    from PySide6.QtWidgets import QMenu

    async def scenario():
        win, link = await window(tmp_path, start_app="view")
        page = win.page
        grid = page.grid
        try:
            await until(lambda: page.model.controls and grid._values
                        and rig(link)["controls"].get("af") is not None)

            def spot(control_id):
                return next(r for r, cid in grid._values if cid == control_id).center()

            # The switch: a click flips it.
            before = bool(rig(link)["controls"].get("nb"))
            QTest.mouseClick(grid, Qt.MouseButton.LeftButton, pos=spot("nb").toPoint())
            await until(lambda: bool(rig(link)["controls"].get("nb")) is not before)
            # The knob: dragged up 40 pixels, a quarter of its range more.
            af = rig(link)["controls"]["af"]
            start = spot("af").toPoint()
            QTest.mousePress(grid, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(grid, start - QPointF(0, 40).toPoint())
            QTest.mouseRelease(grid, Qt.MouseButton.LeftButton,
                               pos=start - QPointF(0, 40).toPoint())
            await until(lambda: rig(link)["controls"]["af"] == min(100, af + 25))
            # The drop-down: its list, and the one chosen set.
            QTest.mouseClick(grid, Qt.MouseButton.LeftButton, pos=spot("agc").toPoint())
            await until(lambda: any(m.isVisible() for m in grid.findChildren(QMenu)))
            menu = next(m for m in grid.findChildren(QMenu) if m.isVisible())
            choice = next(a for a in menu.actions() if not a.isChecked())
            choice.trigger()
            menu.close()
            control = page.model.control("agc")
            wanted = next(k for k, label in control["choices"] if choice.text() in (label, k))
            await until(lambda: rig(link)["controls"]["agc"] == wanted)
        finally:
            await link.close()

    run(scenario())
