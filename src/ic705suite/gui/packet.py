"""The packet terminal (hamrpkt) in the graphical client.

As with the logbook, hamrpkt's terminal application (apps.packet.tui.app.HamrpktApp) holds
the behaviour: the link with the node (AGWPE or Telnet), sessions, the monitor, the heard
stations, the /commands and the setup form. This page runs that code with Qt widgets offering
the methods of its Textual ones. Same keys; the mouse picks a heard station (double click
connects to it).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace as Event

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from rich.text import Text

from ic705suite.apps.packet.i18n import _ as pkt_
from ic705suite.apps.packet.tui import app as pkt_app
from ic705suite.apps.packet.tui.app import HamrpktApp
from ic705suite.apps.packet.tui.screens import ConfirmScreen, FormScreen
from ic705suite.apps.packet.tui.widgets import (
    CONTROL,
    HEARD_COLUMNS,
    HELP_HINT,
    LINE_BREAK,
    SHORT_HINTS,
    Counters,
    _human_bytes,
)
from ic705suite.apps.packet.tui.widgets import StatusLine as PktStatusLine
from ic705suite.gui import theme
from ic705suite.gui.borrow import borrow
from ic705suite.gui.page import Page, _Dialog, _wait
from ic705suite.gui.widgets import Frame, Table, hint_html, rich_html, span, with_load_html


def feedback_colour(level: str) -> str:
    return {"info": theme.DIM, "ok": theme.GREEN, "warning": theme.YELLOW,
            "error": theme.RED}.get(level, theme.DIM)
MAX_BLOCKS = 5000


class QtTextView(QTextEdit):
    """A scrolling log of whole lines fed as text arrives (widgets.TextView): complete lines
    at once, the rest after a moment so a prompt is never hidden."""

    FLUSH_DELAY = 0.4

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(theme.mono(10.5))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.document().setMaximumBlockCount(MAX_BLOCKS)
        self._partial = ""
        self._partial_style = ""
        self._timer = QTimer(self, singleShot=True, interval=int(self.FLUSH_DELAY * 1000),
                             timeout=self._flush)

    @property
    def display(self) -> bool:
        return self.isVisible()

    @display.setter
    def display(self, shown: bool) -> None:
        self.setVisible(shown)

    def feed(self, text: str, style: str = "") -> None:
        if self._partial and style != self._partial_style:
            self._flush()
        self._partial += text
        self._partial_style = style
        *lines, self._partial = LINE_BREAK.split(self._partial)
        for line in lines:
            self._write_line(line, style)
        self._timer.stop()
        if self._partial:
            self._timer.start()

    def _flush(self) -> None:
        self._timer.stop()
        if self._partial:
            self._write_line(self._partial, self._partial_style)
            self._partial = ""

    def _write_line(self, line: str, style: str) -> None:
        line = CONTROL.sub("", line)
        rendered = Text.from_ansi(line) if "\x1b" in line else Text(line)
        if style:
            rendered.stylize(style)
        self.write(rendered)

    def write(self, text: Text) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock()
        cursor.insertHtml(f'<span style="white-space:pre-wrap">{rich_html(text) or "&nbsp;"}'
                          "</span>")
        if at_bottom:
            bar.setValue(bar.maximum())

    def say(self, message: str, style: str) -> None:
        self._flush()
        self.write(Text(message, style=style))

    def clear(self) -> None:  # noqa: D102 - RichLog's
        super().clear()

    def scroll_page_up(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - bar.pageStep())

    def scroll_page_down(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + bar.pageStep())


class QtHeardTable(Table):
    """Stations heard, the most recent first (widgets.HeardTable); double click connects."""

    def __init__(self, page: PacketPage) -> None:
        super().__init__(0, len(HEARD_COLUMNS))
        self.page = page
        self._calls: list[str] = []
        self.setHorizontalHeaderLabels([pkt_(label) for label, _w in HEARD_COLUMNS])
        self.verticalHeader().hide()
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.horizontalHeader().setStretchLastSection(True)
        char = self.fontMetrics().averageCharWidth()
        for column, (_label, width) in enumerate(HEARD_COLUMNS):
            if width:
                self.setColumnWidth(column, width * char + 18)
        self.cellDoubleClicked.connect(lambda row, _c: self._connect(row))

    @property
    def display(self) -> bool:
        return self.isVisible()

    @display.setter
    def display(self, shown: bool) -> None:
        self.setVisible(shown)

    @property
    def row_count(self) -> int:
        return self.rowCount()

    @property
    def cursor_row(self) -> int:
        return max(0, self.currentRow())

    def move_cursor(self, row: int) -> None:
        self.selectRow(row)

    def load(self, stations: list) -> None:
        selected = self.selected_call
        self._calls = [s.call for s in stations]
        self.setRowCount(len(stations))
        for i, s in enumerate(stations):
            cells = (s.call, f"{s.last:%Y-%m-%d %H:%M:%S}", str(s.frames), str(s.port), s.dest,
                     s.via)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setForeground(QColor(theme.CYAN))
                self.setItem(i, column, item)
        if selected in self._calls:
            self.selectRow(self._calls.index(selected))
        elif self._calls:
            self.selectRow(0)                 # as Textual's table: the cursor on a row

    @property
    def selected_call(self) -> str:
        row = self.currentRow()
        return self._calls[row] if 0 <= row < len(self._calls) else ""

    def scroll_page_up(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - bar.pageStep())

    def scroll_page_down(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + bar.pageStep())

    def _connect(self, row: int) -> None:
        self.selectRow(row)
        self.page._connect_selected()


class QtLine(QLineEdit):
    """The command line's box: Textual's Input as hamrpkt uses it."""

    def __init__(self, page: PacketPage) -> None:
        super().__init__()
        self.page = page
        self.setFont(theme.mono(10.5))
        self.returnPressed.connect(
            lambda: self.page._on_line(Event(value=self.text(), input=self)))

    @property
    def value(self) -> str:
        return self.text()

    @value.setter
    def value(self, text: str) -> None:
        self.setText(text)

    @property
    def cursor_position(self) -> int:
        return self.cursorPosition()

    @cursor_position.setter
    def cursor_position(self, position: int) -> None:
        self.setCursorPosition(position)

    def focus(self) -> None:
        self.setFocus()


class QtPktEntry(QWidget):
    """The command line: who the text goes to, the box, the keys and a message line."""

    def __init__(self, page: PacketPage) -> None:
        super().__init__()
        self.target = QLabel()
        self.line = QtLine(page)
        self.hint = QLabel()
        self.hint.setTextFormat(Qt.TextFormat.RichText)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.RichText)
        row = QHBoxLayout()
        row.addWidget(self.target)
        row.addWidget(self.line, 1)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addLayout(row)
        box.addWidget(self.hint)
        box.addWidget(self.message)
        self.set_target("")

    def set_target(self, call: str) -> None:
        self.target.setText(call or pkt_("CMD"))
        background = theme.BG_GREEN if call else "transparent"
        colour = "#000000" if call else theme.CYAN
        self.target.setStyleSheet(f"background:{background};color:{colour};font-weight:bold;"
                                  "padding:2px 6px;")

    def show_keys(self, keys: str) -> None:
        self.hint.setText(f"<i>{hint_html(pkt_(keys))}</i>")

    def feedback(self, message: str, level: str = "info") -> None:
        self.message.setText(span(message, feedback_colour(level), level != "info")
                             if message else "")


class QtPktStatus(QLabel):
    """Who we are, where the node is and who we talk to (widgets.StatusLine)."""

    def __init__(self) -> None:
        super().__init__()
        self.mycall = self.link_kind = self.host = self.session = ""
        self.link_state = "offline"
        self.radio_port = 1
        self.monitor = False
        self.timer = QTimer(self, interval=2000, timeout=self.redraw)   # the load
        self.timer.start()
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setContentsMargins(6, 3, 6, 3)
        self.setStyleSheet(f"background:{theme.PANEL};border-bottom:1px solid {theme.BORDER};")

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        if name in ("mycall", "link_kind", "host", "link_state", "radio_port", "session",
                    "monitor") and hasattr(self, "monitor"):
            self.redraw()

    def redraw(self) -> None:
        self.setText(with_load_html(rich_html(PktStatusLine.line(self))))


class QtPktFooter(QLabel):
    """Counters and the UTC clock (widgets.StatsFooter)."""

    def __init__(self) -> None:
        super().__init__()
        self._counters = Counters()
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setContentsMargins(0, 2, 6, 2)
        self.setStyleSheet(f"background:{theme.PANEL};border-top:1px solid {theme.BORDER};")
        self.timer = QTimer(self, interval=1000, timeout=self.redraw)
        self.timer.start()

    @property
    def counters(self) -> Counters:
        return self._counters

    @counters.setter
    def counters(self, counters: Counters) -> None:
        self._counters = counters
        self.redraw()

    def redraw(self) -> None:
        now = dt.datetime.now(dt.UTC)
        c = self._counters
        parts = [f'<span style="background:{theme.BG_BLUE};color:#000;font-weight:bold">'
                 f"&nbsp;{now:%Y-%m-%d %H:%M:%S} UTC&nbsp;</span>"]
        for label, value in (("RX", _human_bytes(c.rx_bytes)), ("TX", _human_bytes(c.tx_bytes)),
                             (pkt_("frames"), c.frames), (pkt_("heard"), c.heard)):
            parts.append(span(f"   {label} ", theme.DIM) + span(value, theme.TEXT, True))
        if c.session_start is not None:
            elapsed = int((now - c.session_start).total_seconds())
            hours, rest = divmod(elapsed, 3600)
            parts.append(span(f"   {hours:02}:{rest // 60:02}:{rest % 60:02}", theme.YELLOW))
        hints = [pkt_(HELP_HINT), *(pkt_(h) for h in SHORT_HINTS)]
        chars = max(1, self.width() // max(1, self.fontMetrics().averageCharWidth()))
        hint = next((h for h in hints if len(h) + 60 < chars), hints[-1])
        self.setText(f'<table width="100%"><tr><td>{"".join(parts)}</td>'
                     f'<td align="right">{hint_html(hint)}</td></tr></table>')


class TitledFrame(Frame):
    @property
    def border_title(self) -> str:
        return self.title.text()

    @border_title.setter
    def border_title(self, text: str) -> None:
        self.set_title(text)


class PacketPage(Page):
    TUI = HamrpktApp

    def __init__(self, window, config, config_path=None) -> None:
        super().__init__(window, None, config)
        # HamrpktApp's state, as its __init__ sets it (self.link is the node's link here).
        self.config_path = config_path
        self.link = None
        self.link_state = "offline"
        self.view = "terminal"
        self.radio_port = config.link.radio_port
        self.session = ""
        self.session_port = self.radio_port
        self.session_start = None
        self.heard: dict = {}
        self.frames = 0
        self._history: list[str] = []
        self._history_index: int | None = None
        self._session_log = None
        self._reopen = False
        self._bpq_target = ""
        self.status = QtPktStatus()
        self.frame = TitledFrame()
        self.terminal_view = self.frame.add(QtTextView(), 1)
        self.monitor_view = self.frame.add(QtTextView(), 1)
        self.heard_table = self.frame.add(QtHeardTable(self), 1)
        self.entry_frame = Frame("")
        self.entry_panel = self.entry_frame.add(QtPktEntry(self))
        self.footer = QtPktFooter()
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 6, 0)
        box.setSpacing(6)
        box.addWidget(self.status)
        box.addWidget(self.frame, 1)
        box.addWidget(self.entry_frame)
        box.addWidget(self.footer)
        self.counter_timer = QTimer(self, interval=1000, timeout=self._refresh_counters)

    # ---- what hamrpkt's code asks of a Textual App ----

    def query_one(self, selector, _type=None):
        if isinstance(selector, str):
            return {"#terminal": self.terminal_view, "#monitor": self.monitor_view,
                    "#log-frame": self.frame, "#entry": self.entry_panel,
                    "#heard": self.heard_table}[selector]
        return {QtPktEntry: self.entry_panel, QtHeardTable: self.heard_table,
                QtPktStatus: self.status, QtPktFooter: self.footer}[selector]

    def push_screen(self, screen, callback=None) -> None:
        async def show() -> None:
            if isinstance(screen, ConfirmScreen):
                from ic705suite.gui.log import confirm

                result = await confirm(self, screen.question, "", screen.danger)
            elif isinstance(screen, FormScreen):
                result = await packet_form(self, screen)
            else:
                raise TypeError(f"no dialog for {type(screen).__name__}")
            if callback is not None:
                outcome = callback(result)
                if asyncio.iscoroutine(outcome):
                    await outcome
            self.entry_panel.line.setFocus()
        self.run_worker(show())

    def call_after_refresh(self, callback, *args) -> None:
        QTimer.singleShot(0, lambda: self._call(callback, *args))

    def call_later(self, callback, *args) -> None:
        QTimer.singleShot(0, lambda: self._call(callback, *args))

    def _call(self, callback, *args) -> None:
        result = callback(*args)
        if asyncio.iscoroutine(result):
            self.run_worker(result)

    def set_interval(self, seconds: float, callback) -> None:
        self.counter_timer.setInterval(int(seconds * 1000))

    # ---- life ----

    def start(self) -> None:
        """What HamrpktApp.on_mount does."""
        self._show_view("terminal")
        self._refresh_status()
        self.counter_timer.start()
        self.entry.line.focus()
        if self.config.is_complete:
            self.start_link()
        else:
            self.entry.feedback(pkt_("Set your callsign and the node address with F9"),
                                "warning")
            self.call_after_refresh(self.action_setup)

    def stop(self) -> None:
        self.counter_timer.stop()
        self._close_session_log()
        link, self.link = self.link, None
        if link is not None:
            self.run_worker(link.close())
        self.pop_overlay()

    def restore_focus(self) -> None:
        self.entry.line.setFocus()

    def _show_view(self, view: str) -> None:
        """HamrpktApp._show_view, with the Qt frame's title."""
        from ic705suite.apps.packet.tui.app import VIEWS

        self.view = view
        self.terminal_view.display = view == "terminal"
        self.monitor_view.display = view == "monitor"
        self.heard_table.display = view == "heard"
        self.frame.border_title = pkt_(VIEWS[view][0])
        self.refresh_keys()

    def exit(self, result: object = None) -> None:
        Page.exit(self, result)


# hamrpkt's own methods, with the Qt widgets in place of the Textual ones.
QT_WIDGETS = {"EntryPanel": QtPktEntry, "HeardTable": QtHeardTable, "StatusLine": QtPktStatus,
              "StatsFooter": QtPktFooter, "TextView": QtTextView, "Input": QtLine}
borrow(HamrpktApp, PacketPage, QT_WIDGETS, own={"compose", "on_mount", "_show_view"})
assert pkt_app.EntryPanel is not QtPktEntry              # the terminal keeps its own


async def packet_form(parent: QWidget, screen: FormScreen) -> dict[str, str] | None:
    """hamrpkt's FormScreen: boxes (passwords hidden) with their hints; Enter or Ctrl+S
    saves unless `validate` objects (the form stays open with the error), Esc cancels."""
    dialog = _Dialog(parent, screen.title_text, screen.subtitle_text)
    layout = QFormLayout()
    boxes: dict[str, QLineEdit] = {}
    for field in screen.fields:
        box = QLineEdit(field.value)
        if field.password:
            box.setEchoMode(QLineEdit.EchoMode.Password)
        box.setPlaceholderText(field.hint)
        box.setToolTip(field.hint)
        box.returnPressed.connect(lambda: save())
        boxes[field.key] = box
        layout.addRow(field.label, box)
    dialog.layout_.addLayout(layout)
    error = QLabel("")
    error.setStyleSheet(f"color:{theme.RED};font-weight:bold;")
    dialog.layout_.addWidget(error)
    row = QHBoxLayout()
    row.addStretch(1)
    save_button = QPushButton(f"{screen.save_label} (Ctrl+S)")
    cancel = QPushButton(pkt_("Cancel (Esc)"))
    row.addWidget(save_button)
    row.addWidget(cancel)
    dialog.layout_.addLayout(row)

    def save() -> None:
        values = {key: box.text().strip() for key, box in boxes.items()}
        problem = screen.validate(values) if screen.validate else None
        if problem:
            error.setText(problem)
            return
        dialog.answer = values
        dialog.accept()

    save_button.clicked.connect(save)
    cancel.clicked.connect(dialog.reject)
    QShortcut(QKeySequence("Ctrl+S"), dialog, activated=save)
    next(iter(boxes.values())).setFocus()
    await _wait(dialog)
    return dialog.answer if dialog.result() == QDialog.DialogCode.Accepted else None

