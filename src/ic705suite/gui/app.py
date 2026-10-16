"""The graphical client (ic705suite --gui): the main window, with the dashboard in its first
tab and each application opened in a tab after it (in the order they were opened) or in a
window of its own, as [ui] open_apps says (a right click on an application's icon offers
both). Each application is open once: opening it again shows it.

Qt runs on the same asyncio loop as the core (qasync), so the core, the link and the
terminal's own key handling run unchanged. Priority keys are caught before any widget, as in
Textual; the rest reach the focused widget first and then the page.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QIcon, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QTabBar,
)

from ic705suite.apps import BY_ID
from ic705suite.config import Config
from ic705suite.gui import theme
from ic705suite.gui.page import Page
from ic705suite.i18n import N_, _
from ic705suite.station.links import Link

log = logging.getLogger(__name__)


def save_theme(config: Config) -> None:
    """[ui] theme in the configuration file, the rest of it as it is (no file: nothing)."""
    from ic705suite.configure import save, set_values

    path = config.source
    if path is None or not path.exists():
        return
    try:
        save(path, set_values(path.read_text(encoding="utf-8"), {("ui", "theme"): config.theme}))
    except (OSError, ValueError) as exc:
        log.warning("cannot save the theme in %s: %s", path, exc)


PROJECT_URL = "https://github.com/manuel-alcocer/ic705suite"
# The user's guide, once it is published (GitHub Pages of the project).
HELP_URL = "https://manuel-alcocer.github.io/ic705suite/"

# The theme button goes round these, showing the one in use.
THEMES = ("light", "dark", "auto")
THEME_ICONS = {"light": "\u2600", "dark": "\u263e", "auto": "\u25d0"}   # sun, moon, half
THEME_NAMES = {"light": N_("Light"), "dark": N_("Dark"), "auto": N_("As the system")}


class PageHost(QStackedWidget):
    """Where one page is shown, with the screens it puts over it (its overlays): a tab of
    the main window or a window of its own. The page reaches the window through it."""

    def __init__(self, window: MainWindow, app_id: str) -> None:
        super().__init__()
        self.window = window
        self.app_id = app_id
        self.page: Page | None = None

    @property
    def stack(self) -> QStackedWidget:
        return self

    def page_done(self, page: Page, result: object) -> None:
        self.window.page_done(page, result)

    def open_app(self, app_id: str, mode: str | None = None) -> None:
        self.window.open_app(app_id, mode)


class AppWindow(QMainWindow):
    """An application in a window of its own, beside the main one: its close button leaves
    the application, as Esc does."""

    def __init__(self, main: MainWindow, host: PageHost, title: str) -> None:
        super().__init__()
        self.main = main
        self.host = host
        self.closing = False
        self.setWindowTitle(f"ic705suite · {title}")
        self.setCentralWidget(host)
        self.resize(main.size())

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.closing:
            event.accept()
            return
        event.ignore()
        if self.host.page is not None:
            leave(self.host.page)


def leave(page: Page) -> None:
    """Leave a page as its own key does (Esc: it may ask first, it stops what it started)."""
    for name in ("action_leave", "action_quit"):
        action = getattr(page, name, None)
        if action is not None:
            action()
            return
    page.exit()


class MainWindow(QMainWindow):
    def __init__(self, link: Link | None, config: Config, problem: str = "",
                 start_app: str | None = None, scp: set[str] | None = None) -> None:
        super().__init__()
        from ic705suite.gui.dashboard import DashboardPage

        self.link = link
        self.config = config
        self.scp = scp or set()
        self.start_app = start_app
        self.finished = asyncio.get_event_loop().create_future()
        self._quitting = False
        self.setWindowTitle("ic705suite")
        self.resize(1360, 860)
        # The tabs, at the top (in the menu bar's row), over the pages they show.
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.tab_bar = QTabBar()
        self.tab_bar.setObjectName("apptabs")
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setMovable(False)            # the dashboard first, then in order
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setUsesScrollButtons(False)  # every tab in full, no arrows
        self.tab_bar.setExpanding(False)
        self.tab_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tab_bar.currentChanged.connect(self._tab_changed)
        self.tab_bar.tabCloseRequested.connect(lambda index: leave(self.tabs[index].page))
        self.tabs: list[PageHost] = []            # in the tab bar's order
        self.windows: dict[str, AppWindow] = {}   # the applications in windows of their own
        self.dashboard_host = PageHost(self, "dashboard")
        self.dashboard = DashboardPage(self.dashboard_host, link, config, problem)
        self._add_tab(self.dashboard_host, self.dashboard, _("Dashboard"))
        self.tab_bar.setTabButton(0, QTabBar.ButtonPosition.RightSide, None)   # always there
        self.tab_bar.setTabButton(0, QTabBar.ButtonPosition.LeftSide, None)
        self._corner_buttons()
        QApplication.instance().installEventFilter(self)
        if start_app is None:
            self.dashboard.start()
            self.dashboard.restore_focus()
        else:
            self.tab_bar.hide()                   # the application alone, as in the terminal
            self.open_app(start_app, standalone=True)

    def _fit_tabs(self) -> None:
        """The menu bar gives its corner the room the tabs take now (it sizes it once)."""
        self.tab_bar.setFixedSize(self.tab_bar.sizeHint())
        bar = self.menuBar()
        bar.setCornerWidget(self.tab_bar, Qt.Corner.TopLeftCorner)
        bar.adjustSize()
        bar.updateGeometry()
        bar.resizeEvent(QResizeEvent(bar.size(), bar.size()))

    @property
    def page(self) -> Page | None:
        """The page of the tab shown."""
        index = self.tab_bar.currentIndex()
        return self.tabs[index].page if 0 <= index < len(self.tabs) else None

    @property
    def stack(self) -> QStackedWidget:
        """The tab shown: its page and the screens over it."""
        return self.pages.currentWidget()

    def _add_tab(self, host: PageHost, page: Page, title: str) -> None:
        host.page = page
        host.addWidget(page)
        self.pages.addWidget(host)
        self.tabs.append(host)
        self.tab_bar.addTab(title)
        self._fit_tabs()

    # ---- the corner of the menu bar: the theme and the help ------------------------------

    def _corner_buttons(self) -> None:
        """Two buttons instead of menus: the theme (sun, moon or auto: a click goes to the
        next one) and ? for About, which has the project's and the help's links."""
        from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

        bar = self.menuBar()
        bar.setNativeMenuBar(False)     # macOS: the corner stays in the window
        bar.setCornerWidget(self.tab_bar, Qt.Corner.TopLeftCorner)
        corner = QWidget(bar)            # owned by the bar (PySide would delete it)
        corner.setObjectName("menucorner")
        row = QHBoxLayout(corner)
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(2)
        self.theme_button = QToolButton()
        self.theme_button.clicked.connect(self.next_theme)
        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setToolTip(_("About ic705suite"))
        self.help_button.clicked.connect(self.show_about)
        for button in (self.theme_button, self.help_button):
            button.setObjectName("cornerbutton")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # the keys stay with the page
            button.setAutoRaise(True)
            row.addWidget(button)
        bar.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self._show_theme()
        # Auto follows the desktop while the window is open.
        with contextlib.suppress(AttributeError):
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                lambda _scheme: self.config.theme == "auto" and self.set_theme("auto", save=False))

    def _show_theme(self) -> None:
        name = self.config.theme or "auto"
        if name not in THEMES:
            name = "auto"
        following = THEMES[(THEMES.index(name) + 1) % len(THEMES)]
        self.theme_button.setText(THEME_ICONS[name])
        self.theme_button.setToolTip(_("Theme: {now} (click: {next})").format(
            now=_(THEME_NAMES[name]), next=_(THEME_NAMES[following])))

    def next_theme(self) -> None:
        """Sun, moon, auto, and round again."""
        name = self.config.theme or "auto"
        index = THEMES.index(name) if name in THEMES else -1
        self.set_theme(THEMES[(index + 1) % len(THEMES)])

    def set_theme(self, name: str, save: bool = True) -> None:
        """Dark, light or as the system, at once; kept in the configuration file."""
        self.config.theme = name
        theme.switch(name)
        self._show_theme()
        for host in self._hosts():
            for index in range(host.count()):
                page = host.widget(index)
                if hasattr(page, "retheme"):
                    page.retheme()
        if save:
            save_theme(self.config)

    # ---- About --------------------------------------------------------------------------

    def show_about(self) -> QMessageBox:
        """Name, version, what it is, the author and licence, and what it runs on."""
        import platform

        import PySide6

        from ic705suite import __version__

        link = f"color:{theme.CYAN}"
        box = QMessageBox(self)
        box.setWindowTitle(_("About ic705suite"))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<h2>ic705suite {__version__}</h2>"
                    + "<p>" + _("Station suite for the Icom IC-705: dashboard, rig view, CW, "
                                "antenna tuning, logbook and packet; in the terminal and in "
                                "this window.") + "</p>"
                    + "<p>© Manuel Alcocer · PolyForm Noncommercial 1.0.0</p>"
                    + f'<p><a href="{PROJECT_URL}" style="{link}">' + _("Project page")
                    + f'</a> · <a href="{HELP_URL}" style="{link}">' + _("Help") + "</a></p>"
                    + f"<p><small>Python {platform.python_version()} · Qt {PySide6.__version__}"
                    f" · {platform.system()} {platform.release()}</small></p>")
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)  # the link
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton(_("Close"), QMessageBox.ButtonRole.AcceptRole)
        box.open()                      # not modal-blocking: the loop (and the radio) go on
        return box

    # ---- pages --------------------------------------------------------------------------

    def _hosts(self) -> list[PageHost]:
        return self.tabs + [w.host for w in self.windows.values()]

    def _tab_changed(self, index: int) -> None:
        if not 0 <= index < len(self.tabs):
            return
        host = self.tabs[index]
        self.pages.setCurrentWidget(host)
        title = self.tab_bar.tabText(index)
        self.setWindowTitle("ic705suite" if index == 0 else f"ic705suite · {title}")
        if host.page is not None and host.page.overlay is None:
            host.page.restore_focus()

    def open_app(self, app_id: str, mode: str | None = None, standalone: bool = False) -> None:
        """The application in a tab or a window of its own (`mode`, else [ui] open_apps);
        already open: shown where it is."""
        for index, host in enumerate(self.tabs):
            if host.app_id == app_id:
                self.tab_bar.setCurrentIndex(index)
                return
        if app_id in self.windows:
            shown = self.windows[app_id]
            shown.showNormal()
            shown.raise_()
            shown.activateWindow()
            return
        mode = "tab" if standalone else (mode or self.config.open_apps)
        host = PageHost(self, app_id)
        page = self._make_page(app_id, host, standalone)
        title = page.windowTitle() or _(BY_ID[app_id].name).format(
            model=(self.link.welcome or {}).get("rig", {}).get("name", "IC-705")
            if self.link is not None else "IC-705")
        if mode == "window":
            host.page = page
            host.addWidget(page)
            window = AppWindow(self, host, title)
            self.windows[app_id] = window
            window.show()
            window.activateWindow()
            page.start()
            page.restore_focus()
            return
        self._add_tab(host, page, title)
        page.start()
        self.tab_bar.setCurrentIndex(len(self.tabs) - 1)

    def _make_page(self, app_id: str, host: PageHost, standalone: bool) -> Page:
        if app_id == "view":
            from ic705suite.gui.view import ViewPage

            return ViewPage(host, self.link, self.config, owns_link=standalone)
        if app_id == "packet":
            from ic705suite.gui.packet import PacketPage
            from ic705suite.launcher import packet_link

            remote = packet_link(self.config)      # packet on another machine's core
            return PacketPage(host, remote or self.link, self.config,
                              owns_link=standalone or remote is not None)
        if app_id == "ft8":
            from ic705suite.gui.ft8 import Ft8Page

            return Ft8Page(host, self.link, self.config, owns_link=standalone)
        if app_id == "log":
            from ic705suite.gui.log import LogPage

            # hamrlog keeps its own database and settings, and needs no radio.
            return LogPage(host, None, self.config, demo=self.config.connection == "sim")
        if app_id == "swr":
            from ic705suite.gui.swr import SwrPage

            return SwrPage(host, self.link, self.config, owns_link=standalone)
        if app_id == "cw":
            from ic705suite.gui.cw import CwPage

            return CwPage(host, self.link, self.config, scp=self.scp, owns_link=standalone)
        raise ValueError(f"no graphical page for {app_id}")

    def page_done(self, page: Page, result: object) -> None:
        """A page ends: the dashboard opens an application or quits (the applications open
        leave first); an application closes its tab, back on the dashboard, or its window
        (quits, when it was opened on its own)."""
        if page is self.dashboard:
            if result == "quit":
                self._quit()
            elif isinstance(result, str):
                self.open_app(result)
            return
        page.stop()
        host = page.window_
        if host in self.tabs:
            index = self.tabs.index(host)
            self.tabs.pop(index)
            self.tab_bar.removeTab(index)
            self._fit_tabs()
            self.pages.removeWidget(host)
            self.tab_bar.setCurrentIndex(0)
            self._tab_changed(0)
        else:
            window = self.windows.pop(host.app_id, None)
            if window is not None:
                window.closing = True
                window.close()
                window.deleteLater()
        host.deleteLater()
        if self.start_app is not None or (self._quitting and not self._apps_open()):
            self._finish()

    def _apps_open(self) -> bool:
        return len(self.tabs) > 1 or bool(self.windows)

    def _quit(self) -> None:
        """Every application open leaves as its Esc does (FT8 stops decoding...), then the
        client ends."""
        self._quitting = True
        if not self._apps_open():
            self._finish()
            return
        for host in self._hosts():
            if host.page is not None and host.page is not self.dashboard:
                leave(host.page)

    def _finish(self) -> None:
        QApplication.instance().removeEventFilter(self)
        for host in self._hosts():
            if host.page is not None:
                host.page.stop()
        for window in list(self.windows.values()):
            window.closing = True
            window.close()
        if not self.finished.done():
            self.finished.set_result(None)
        self.hide()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """The window's close button: as Ctrl+Q on the dashboard (asks about unsaved
        channels; the applications open leave too), or leaving the application opened on
        its own."""
        if self.finished.done():
            event.accept()
            return
        event.ignore()
        if self.start_app is None:
            self.dashboard.action_leave()
        elif self.page is not None:
            leave(self.page)

    # ---- priority keys --------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress or QApplication.activeModalWidget() is not None:
            return False
        # The page of the window the keys go to: the tab shown here, or an application's
        # own window.
        page = self.page if self.isActiveWindow() else next(
            (w.host.page for w in self.windows.values() if w.isActiveWindow()), None)
        return page is not None and page.handle_key(QKeyEvent(event), priority=True)


async def _run(window: MainWindow, link: Link | None, owns_link: bool) -> int:
    if link is not None and window.start_app is None:
        from ic705suite.launcher import start_link

        start_link(link)
    try:
        await window.finished
    finally:
        if link is not None and owns_link:
            await link.close()
    return 0


def run_gui(link: Link | None, config: Config, problem: str = "",
            start_app: str | None = None, scp: set[str] | None = None) -> int:
    """The graphical client until its window closes."""
    import qasync

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("ic705suite")
    app.setApplicationDisplayName("ic705suite")
    app.setWindowIcon(QIcon.fromTheme("network-wireless"))
    theme.use(config.theme)        # after the application: "auto" asks the desktop
    theme.apply(app)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    try:
        window = MainWindow(link, config, problem, start_app, scp)
        window.show()
        _signals(window)
        log.info("graphical client ready")       # Ctrl+C handled from here on
        return loop.run_until_complete(_run(window, link, owns_link=start_app is None))
    finally:
        # The logbook's queries run in threads: let them end before Qt goes.
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


def _signals(window: MainWindow) -> None:
    """Ctrl+C in the terminal it was started from (Ctrl+Break, SIGTERM) closes the window as
    its close button does. Python only sees a signal while it runs, so a timer wakes it."""
    import signal

    def close(signum, frame) -> None:
        if window.isVisible():
            window.close()

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:                         # SIGBREAK: Ctrl+Break, on Windows
            signal.signal(sig, close)
    window.signal_timer = QTimer(window, interval=250, timeout=lambda: None)
    window.signal_timer.start()


def gui_available() -> str:
    """"" when the graphical client can run, else why not."""
    try:
        import PySide6  # noqa: F401
        import qasync  # noqa: F401
    except ImportError as exc:
        return _("the graphical client needs PySide6 and qasync "
                 "(pip install 'ic705suite[gui]'): {error}").format(error=exc)
    return ""
