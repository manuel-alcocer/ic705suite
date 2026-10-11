"""The graphical client (ic705suite --gui): one window that shows the dashboard and, in turn,
each application it opens, as the terminal suite does.

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
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStackedWidget

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
        self.setWindowTitle("ic705suite")
        self.resize(1360, 860)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.dashboard = DashboardPage(self, link, config, problem)
        self.stack.addWidget(self.dashboard)
        self.page: Page | None = None
        self._corner_buttons()
        QApplication.instance().installEventFilter(self)
        if start_app is None:
            self.show_page(self.dashboard)
        else:
            self.open_app(start_app, standalone=True)

    # ---- the corner of the menu bar: the theme and the help ------------------------------

    def _corner_buttons(self) -> None:
        """Two buttons instead of menus: the theme (sun, moon or auto: a click goes to the
        next one) and ? for About, which has the project's and the help's links."""
        from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

        bar = self.menuBar()
        bar.setNativeMenuBar(False)     # macOS: the corner stays in the window
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
        for index in range(self.stack.count()):
            page = self.stack.widget(index)
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

    def show_page(self, page: Page) -> None:
        if self.page is not None and self.page is not page:
            self.page.stop()
        self.page = page
        self.stack.setCurrentWidget(page)
        page.start()

    def open_app(self, app_id: str, standalone: bool = False) -> None:
        if app_id == "view":
            from ic705suite.gui.view import ViewPage

            page = ViewPage(self, self.link, self.config, owns_link=standalone)
        elif app_id == "packet":
            from ic705suite.gui.packet import PacketPage
            from ic705suite.launcher import packet_link

            remote = packet_link(self.config)      # packet on another machine's core
            page = PacketPage(self, remote or self.link, self.config,
                              owns_link=standalone or remote is not None)
        elif app_id == "log":
            from ic705suite.gui.log import LogPage

            # hamrlog keeps its own database and settings, and needs no radio.
            page = LogPage(self, None, self.config, demo=self.config.connection == "sim")
        elif app_id == "swr":
            from ic705suite.gui.swr import SwrPage

            page = SwrPage(self, self.link, self.config, owns_link=standalone)
        elif app_id == "cw":
            from ic705suite.gui.cw import CwPage

            page = CwPage(self, self.link, self.config, scp=self.scp, owns_link=standalone)
        else:
            raise ValueError(f"no graphical page for {app_id}")
        self.stack.addWidget(page)
        self.show_page(page)
        self.setWindowTitle(f"ic705suite · {page.windowTitle() or app_id}")

    def page_done(self, page: Page, result: object) -> None:
        """A page ends: the dashboard opens an application or quits; an application goes
        back to the dashboard (or quits, when it was opened on its own)."""
        if page is self.dashboard:
            if result == "quit":
                self._finish()
            elif isinstance(result, str):
                self.open_app(result)
            return
        page.stop()
        self.stack.removeWidget(page)
        page.deleteLater()
        self.page = None
        self.setWindowTitle("ic705suite")
        if self.start_app is not None:
            self._finish()
        else:
            self.show_page(self.dashboard)

    def _finish(self) -> None:
        QApplication.instance().removeEventFilter(self)
        if self.page is not None:
            self.page.stop()
            self.page = None
        if not self.finished.done():
            self.finished.set_result(None)
        self.hide()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """The window's close button: as Ctrl+Q on the dashboard (asks about unsaved
        channels), or leaving the application opened on its own."""
        if self.finished.done():
            event.accept()
            return
        event.ignore()
        if self.page is self.dashboard:
            self.dashboard.action_leave()
        elif self.page is not None:
            self.page.action_leave()

    # ---- priority keys --------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and self.page is not None \
                and self.isActiveWindow() and QApplication.activeModalWidget() is None:
            key_event = QKeyEvent(event)
            if self.page.handle_key(key_event, priority=True):
                return True
        return False


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
