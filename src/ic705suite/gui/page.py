"""A page of the graphical client: the twin of one terminal screen.

It takes the screen's key bindings (``TUI``) and offers what the shared terminal code expects
of a Textual App, so that code runs unchanged: ``link``, ``_message()``, ``run_worker()``,
``focused``, ``screen_stack`` and ``push_screen_wait()`` (the suite's modal screens shown as
Qt dialogs).
"""

from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ic705suite.config import Config
from ic705suite.gui import theme
from ic705suite.gui.keys import KeyMap, key_name, run_action
from ic705suite.station.links import Link

log = logging.getLogger(__name__)


class Page(QWidget):
    TUI: type          # the Textual App this page mirrors (its BINDINGS)

    def __init__(self, window, link: Link | None, config: Config) -> None:
        super().__init__()
        self.window_ = window
        self.link = link
        self.config = config
        self.keymap = KeyMap(self.TUI)
        self.overlay: Overlay | None = None
        self._tasks: set[asyncio.Future] = set()
        self._groups: dict[str, asyncio.Future] = {}
        # The page itself can hold the focus, so its keys arrive when no widget has it.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # ---- keys ---------------------------------------------------------------------------

    def handle_key(self, event: QKeyEvent, priority: bool) -> bool:
        name = key_name(event)
        if self.overlay is not None and priority:
            # A screen on top (as Textual's pushed screens) has its keys first.
            binding = self.overlay.keymap.by_key.get(name)
            if binding is not None:
                return run_action(self.overlay, binding.action)
        binding = self.keymap.find(name, priority)
        if binding is None:
            return False
        return run_action(self, binding.action)

    def push_overlay(self, overlay: Overlay) -> None:
        """Show a screen of its own over the page (the terminal's pushed screens)."""
        self.overlay = overlay
        self.window_.stack.addWidget(overlay)
        self.window_.stack.setCurrentWidget(overlay)
        overlay.start()

    def pop_overlay(self) -> None:
        overlay, self.overlay = self.overlay, None
        if overlay is None:
            return
        self.window_.stack.setCurrentWidget(self)
        self.window_.stack.removeWidget(overlay)
        overlay.deleteLater()
        self.restore_focus()

    def restore_focus(self) -> None:
        """Back on the page: its usual place for the keys (the page itself by default)."""
        self.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt's name
        if not self.handle_key(event, priority=False):
            super().keyPressEvent(event)

    # ---- what the shared terminal code expects ---------------------------------------------

    @property
    def focused(self) -> QWidget | None:
        return QApplication.focusWidget()

    @property
    def screen_stack(self) -> list:
        """One screen, unless a dialog is open (as Textual's stack with a modal on top)."""
        return [self] if QApplication.activeModalWidget() is None else [self, None]

    def run_worker(self, awaitable, *, group: str = "", exclusive: bool = False,
                   **_options) -> asyncio.Future:
        """As Textual's: an exclusive worker cancels the one running in its group."""
        if exclusive and group:
            previous = self._groups.get(group)
            if previous is not None and not previous.done():
                previous.cancel()
        task = asyncio.ensure_future(awaitable)
        self._tasks.add(task)
        if group:
            self._groups[group] = task
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Future) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None \
                and type(task.exception()).__name__ != "SkipAction":
            log.error("task failed", exc_info=task.exception())

    def _message(self, kind: str, text: str) -> None:
        raise NotImplementedError

    def show_text(self, title: str, lines: list[str]) -> None:
        """Lines to read (the terminal's TextScreen): Esc, Enter or F1 closes them."""
        self.run_worker(show_text(self, title, lines))

    async def push_screen_wait(self, screen) -> object:
        """The suite's modal screens (ui.widgets) as Qt dialogs, same answers."""
        from ic705suite.ui.widgets import ChoiceScreen, NameScreen

        if isinstance(screen, ChoiceScreen):
            return await choose(self, screen.title_text, screen.message, screen.options)
        if isinstance(screen, NameScreen):
            return await ask_text(self, screen.title_text, screen.message, screen.placeholder,
                                  screen.value)
        raise TypeError(f"no dialog for {type(screen).__name__}")

    # ---- life ---------------------------------------------------------------------------

    def start(self) -> None:
        """Shown: take the link's events."""

    def stop(self) -> None:
        """Left: stop taking them."""
        self.pop_overlay()
        if self.link is not None:
            self.link.detach()
        for task in list(self._tasks):
            task.cancel()

    def exit(self, result: object = None) -> None:
        self.window_.page_done(self, result)


class Overlay(QWidget):
    """A screen shown over a page with keys of its own (TUI: the Textual Screen it mirrors);
    "app.pop_screen" closes it, as in the terminal."""

    TUI: type

    def __init__(self, page: Page) -> None:
        super().__init__()
        self.page = page
        self.keymap = KeyMap(self.TUI)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def start(self) -> None:
        """Shown."""

    def action_pop_screen(self) -> None:
        self.page.pop_overlay()

    def run_worker(self, awaitable, **_options) -> asyncio.Future:
        return self.page.run_worker(awaitable)


# ---- dialogs --------------------------------------------------------------------------------

class _Dialog(QDialog):
    def __init__(self, parent: QWidget, title: str, message: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.layout_ = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setStyleSheet(f"color:{theme.CYAN};font-weight:bold;font-size:12pt;")
        self.layout_.addWidget(heading)
        text = QLabel(message)
        text.setWordWrap(True)
        self.layout_.addWidget(text)
        self.answer: object = None


async def _wait(dialog: QDialog) -> None:
    """Show the dialog and wait for it; then the focus goes back where it was."""
    before = QApplication.focusWidget()
    done = asyncio.get_running_loop().create_future()
    dialog.finished.connect(lambda _code: done.done() or done.set_result(None))
    dialog.open()
    await done
    # Back where it was; or, when nothing had it (one dialog right after another), to what
    # the dialog was opened over.
    target = before if before is not None and before.isVisible() else dialog.parentWidget()
    if target is not None and target.isVisible():
        target.window().activateWindow()       # not every window manager gives it back
        target.setFocus()


async def choose(parent: QWidget, title: str, message: str,
                 options: list[tuple[str, str]]) -> str:
    """A question with a few answers: the chosen key, "" on Esc (as ChoiceScreen)."""
    dialog = _Dialog(parent, title, message)
    dialog.answer = ""
    row = QHBoxLayout()
    row.addStretch(1)
    for i, (key, label) in enumerate(options):
        button = QPushButton(label)
        button.setDefault(i == 0)
        button.setAutoDefault(True)

        def pick(_checked=False, key=key) -> None:
            dialog.answer = key
            dialog.accept()

        button.clicked.connect(pick)
        row.addWidget(button)
        if i == 0:
            button.setFocus()
    dialog.layout_.addLayout(row)
    await _wait(dialog)
    return str(dialog.answer)


async def show_text(parent: QWidget, title: str, lines: list[str]) -> None:
    """Lines to read in a fixed-width font; Esc, Enter or F1 closes (as TextScreen)."""
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import QPlainTextEdit

    from ic705suite.i18n import _

    dialog = _Dialog(parent, title, "")
    dialog.setMinimumSize(760, 480)
    text = QPlainTextEdit("\n".join(lines))
    text.setReadOnly(True)
    text.setFont(theme.mono(10))
    text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    dialog.layout_.addWidget(text, 1)
    hint = QLabel(_("Esc, Enter or F1 closes"))
    hint.setStyleSheet(f"color:{theme.DIM};")
    dialog.layout_.addWidget(hint)
    for key in ("Return", "Enter", "F1"):
        QShortcut(QKeySequence(key), dialog, activated=dialog.accept)
    await _wait(dialog)


async def ask_text(parent: QWidget, title: str, message: str, placeholder: str = "",
                   value: str = "") -> str | None:
    """A line of text: what was typed (maybe empty), None on Esc (as NameScreen)."""
    dialog = _Dialog(parent, title, message)
    field = QLineEdit(value)
    field.setPlaceholderText(placeholder)
    dialog.layout_.addWidget(field)

    def accept() -> None:
        dialog.answer = field.text()
        dialog.accept()

    field.returnPressed.connect(accept)
    field.setFocus(Qt.FocusReason.OtherFocusReason)
    await _wait(dialog)
    return dialog.answer if dialog.result() == QDialog.DialogCode.Accepted else None
