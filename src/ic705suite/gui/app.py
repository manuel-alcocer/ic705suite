"""The graphical client (ic705suite --gui): one window that shows the dashboard and, in turn,
each application it opens, as the terminal suite does.

Qt runs on the same asyncio loop as the core (qasync), so the core, the link and the
terminal's own key handling run unchanged. Priority keys are caught before any widget, as in
Textual; the rest reach the focused widget first and then the page.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from ic705suite.config import Config
from ic705suite.gui import theme
from ic705suite.gui.page import Page
from ic705suite.i18n import _
from ic705suite.station.links import Link

log = logging.getLogger(__name__)


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
        QApplication.instance().installEventFilter(self)
        if start_app is None:
            self.show_page(self.dashboard)
        else:
            self.open_app(start_app, standalone=True)

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
            from ic705suite.apps.packet import config as packet_config
            from ic705suite.gui.packet import PacketPage

            page = PacketPage(self, packet_config.load(None))
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
