"""Applications in tabs after the dashboard's, or in windows of their own ([ui] open_apps,
or the icon's right click); each open once, all of them receiving the core's events."""

import asyncio

from PySide6.QtCore import Qt

from tests.ft8.test_ft8 import fake_jt9
from tests.gui.test_pages import key, rig, until, window


def tab_titles(win) -> list[str]:
    return [win.tab_bar.tabText(i) for i in range(win.tab_bar.count())]


def test_tabs_in_the_order_opened(tmp_path, run, monkeypatch):
    fake_jt9(monkeypatch)
    from ic705suite.gui.ft8 import Ft8Page
    from ic705suite.gui.view import ViewPage

    async def scenario():
        win, link = await window(tmp_path)
        try:
            assert tab_titles(win) == ["Dashboard"] and win.page is win.dashboard
            win.dashboard.action_open("ft8")
            await until(lambda: isinstance(win.page, Ft8Page))
            ft8 = win.page
            win.dashboard.action_open("view")
            await until(lambda: isinstance(win.page, ViewPage))
            assert tab_titles(win)[1:] == ["FT8", win.page.windowTitle() or tab_titles(win)[2]]
            assert len(win.tabs) == 3
            # Open already: shown, not opened twice.
            win.dashboard.action_open("ft8")
            assert win.page is ft8 and len(win.tabs) == 3
            # The dashboard keeps its events while the others are open.
            win.tab_bar.setCurrentIndex(0)
            assert win.page is win.dashboard
            start = rig(link)["frequency"]
            await link.command(f"/f {start + 1000}", app="rig")
            await until(lambda: win.dashboard.rig.get("frequency") == start + 1000)
            await until(lambda: ft8.rig.get("frequency") == start + 1000)
            # Esc in an application closes its tab, back on the dashboard.
            win.tab_bar.setCurrentIndex(1)
            await key(Qt.Key.Key_Escape)
            await until(lambda: len(win.tabs) == 2)
            assert win.page is win.dashboard and "FT8" not in tab_titles(win)
            assert not link.core.ft8.on                     # it stopped what it started
            # The dashboard's tab cannot be closed; the others can.
            assert win.tab_bar.tabButton(0, win.tab_bar.ButtonPosition.RightSide) is None
            win.tab_bar.tabCloseRequested.emit(1)
            await until(lambda: len(win.tabs) == 1)
        finally:
            await link.close()

    run(scenario())


def test_an_application_in_a_window_of_its_own(tmp_path, run, monkeypatch):
    fake_jt9(monkeypatch)
    from ic705suite.gui.ft8 import Ft8Page

    async def scenario():
        win, link = await window(tmp_path)
        try:
            win.dashboard.action_open("ft8", "window")
            await until(lambda: "ft8" in win.windows)
            own = win.windows["ft8"]
            assert isinstance(own.host.page, Ft8Page) and own.isVisible()
            assert len(win.tabs) == 1 and win.page is win.dashboard
            await until(lambda: link.core.ft8.on)
            win.dashboard.action_open("ft8", "tab")          # open: shown where it is
            assert len(win.tabs) == 1 and "ft8" in win.windows
            own.close()                                       # its close button: it leaves
            await until(lambda: "ft8" not in win.windows)
            assert not link.core.ft8.on and win.isVisible()
        finally:
            await link.close()

    run(scenario())


def test_the_default_is_the_configuration(tmp_path, run, monkeypatch):
    fake_jt9(monkeypatch)

    async def scenario():
        win, link = await window(tmp_path)
        try:
            win.config.open_apps = "window"
            win.dashboard.action_open("ft8")
            await until(lambda: "ft8" in win.windows)
            assert len(win.tabs) == 1
            # Quitting: the applications open leave first, their windows close.
            win.dashboard.action_leave()
            await until(lambda: win.finished.done())
            assert not win.windows and not link.core.ft8.on
        finally:
            await link.close()

    run(scenario())


def test_the_right_click_offers_both(tmp_path, run):
    from PySide6.QtWidgets import QMenu

    async def scenario():
        win, link = await window(tmp_path)
        try:
            grid = win.dashboard.table
            ft8 = next(r for r in range(grid.count()) if "F8" in grid.item(r).text())
            grid.customContextMenuRequested.emit(grid.visualItemRect(grid.item(ft8)).center())
            await asyncio.sleep(0.05)
            menu = win.dashboard.findChild(QMenu)
            assert [a.text() for a in menu.actions()] == ["Open in a tab",
                                                          "Open in a new window"]
            menu.actions()[1].trigger()
            await until(lambda: "ft8" in win.windows)
        finally:
            await link.close()

    run(scenario())


def test_link_slots_side_by_side():
    from ic705suite.station.links import Link

    class Bare(Link):
        async def _connect(self): ...
        async def close(self): ...
        async def command(self, line, app="cw"): ...
        async def call(self, method, **params): return {}

    link = Bare()
    got: dict[str, list] = {"a": [], "b": [], "main": []}
    link.attach(lambda e: got["main"].append(e))
    link.attach(lambda e: got["a"].append(e), slot="a")
    link.attach(lambda e: got["b"].append(e), slot="b")
    link._dispatch({"type": "info", "text": "x"})
    assert all(len(v) == 1 for v in got.values())
    link.attach(lambda e: got["main"].append(("again", e)))        # the main slot: replaced
    link.detach(slot="a")
    link._dispatch({"type": "info", "text": "y"})
    assert len(got["a"]) == 1 and len(got["b"]) == 2 and got["main"][-1][0] == "again"
    assert len([e for e in got["main"] if not isinstance(e, tuple)]) == 1
