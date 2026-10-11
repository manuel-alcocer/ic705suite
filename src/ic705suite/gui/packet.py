"""The packet terminal in the graphical client: the terminal's screen (apps.packet.app) with
the same PacketClient underneath, so the keys, the commands and what is shown are the same.
The mouse picks a channel from the bar and a station from the heard list (double click
connects to it).
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)
from rich.text import Text

from ic705suite.apps.packet.app import (
    CONTROL,
    HEARD_HINT,
    HINT,
    LINE_BREAK,
    SHORT_HINT,
    HeardScreen,
    PacketApp,
)
from ic705suite.apps.packet.client import CHANNELS, STYLES, PacketClient, piece_text
from ic705suite.gui import theme
from ic705suite.gui.page import Overlay, Page
from ic705suite.gui.widgets import Footer, Frame, Table, rich_html, span, with_load_html
from ic705suite.i18n import _
from ic705suite.station.links import Link

MAX_BLOCKS = 5000


def feedback_colour(level: str) -> str:
    return {"ok": theme.GREEN, "warning": theme.YELLOW, "error": theme.RED}.get(level,
                                                                               theme.CYAN)


class TextView(QTextEdit):
    """A scrolling log fed as text arrives: whole lines at once, the rest a moment later
    so a prompt is never hidden (apps.packet.app.TextView)."""

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
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock()
        cursor.insertHtml(f'<span style="white-space:pre-wrap">{rich_html(rendered) or "&nbsp;"}'
                          "</span>")
        if at_bottom:
            bar.setValue(bar.maximum())

    def reset(self) -> None:
        self._timer.stop()
        self._partial = ""
        self.clear()

    def text(self) -> str:
        return self.toPlainText()

    def scroll_page(self, direction: int) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * bar.pageStep())


class HeardView(Overlay):
    """The stations heard, the latest first; Enter or a double click connects."""

    TUI = HeardScreen

    def __init__(self, page: PacketPage) -> None:
        super().__init__(page)
        self.heard = page.client.heard()
        frame = Frame(_("Heard stations"))
        self.table = frame.add(Table(0, 5), 1)
        self.table.setHorizontalHeaderLabels([_("Callsign"), _("Last heard"), _("Frames"),
                                              _("To"), _("Via")])
        self.table.right_align(2)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda row, _c: self._choose(row))
        for row, station in enumerate(self.heard):
            last = dt.datetime.fromtimestamp(station["last"], dt.UTC)
            self.table.insertRow(row)
            for column, value in enumerate((station["call"], f"{last:%H:%M:%S} UTC",
                                            str(station["frames"]), station["dest"],
                                            station["via"])):
                self.table.setItem(row, column, QTableWidgetItem(value))
        hint = QLabel(_(HEARD_HINT) if self.heard else _("Nobody heard yet"))
        hint.setStyleSheet(f"color:{theme.DIM};")
        frame.add(hint)
        layout = QVBoxLayout(self)
        layout.addWidget(frame)

    def start(self) -> None:
        self.table.setFocus()
        if self.heard:
            self.table.selectRow(0)

    def action_dismiss(self, _result: str = "") -> None:
        self.page.pop_overlay()

    def action_choose(self) -> None:
        self._choose(self.table.currentRow())

    def _choose(self, row: int) -> None:
        if 0 <= row < len(self.heard):
            call = self.heard[row]["call"]
            self.page.pop_overlay()
            self.page.run_worker(self.page.client.connect_to(call))


class PacketPage(Page):
    TUI = PacketApp

    def __init__(self, window, link: Link, config, owns_link: bool = False) -> None:
        super().__init__(window, link, config)
        self.owns_link = owns_link
        self.setWindowTitle(_("Packet"))
        self.client = PacketClient(link, self._on_text, self._on_reload, self._on_state,
                                   self._feedback)
        self.client.link_task = lambda awaitable: self.run_worker(awaitable)
        self._build()
        self.link.attach(self.client.handle)
        self.run_worker(self._start())

    def _build(self) -> None:
        self.status = QLabel()
        self.status.setObjectName("statusbar")
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setContentsMargins(6, 2, 6, 2)
        self.monitor_frame = Frame(_("Monitor"))
        self.monitor = self.monitor_frame.add(TextView(), 1)
        self.tabs: list[QPushButton] = []
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        for number in range(CHANNELS + 1):
            button = QPushButton()
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, n=number: self.action_channel(n))
            tabs.addWidget(button)
            self.tabs.append(button)
        tabs.addStretch(1)
        self.channel_frame = Frame()
        self.channel_view = self.channel_frame.add(TextView(), 1)
        self.line = QLineEdit()
        self.line.setFont(theme.mono(11))
        self.line.returnPressed.connect(self._submit)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.RichText)
        self.footer = Footer([HINT, SHORT_HINT])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.status)
        body = QVBoxLayout()
        body.setContentsMargins(8, 4, 8, 4)
        body.addWidget(self.monitor_frame, 2)
        body.addLayout(tabs)
        body.addWidget(self.channel_frame, 5)
        body.addWidget(self.line)
        body.addWidget(self.feedback)
        layout.addLayout(body, 1)
        layout.addWidget(self.footer)
        self.timer = QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()
        self._on_state()

    async def _start(self) -> None:
        try:
            await self.client.start()
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._feedback("error", _("cannot connect: {error}").format(error=exc))
            return
        if not self.client.configured:
            self._feedback("warning", _("Packet is not set up: set the TNC in [packet] "
                                        "(ic705suite --configure)"))

    def restore_focus(self) -> None:
        self.line.setFocus()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self.line.setFocus()

    # ---- painting ------------------------------------------------------------------------

    def _views(self, number: int) -> list[TextView]:
        views = []
        if number == 0:
            views.append(self.monitor)
        if number == self.client.channel:
            views.append(self.channel_view)
        return views

    def _on_text(self, number: int, piece: dict) -> None:
        for view in self._views(number):
            view.feed(piece_text(piece), STYLES.get(piece.get("kind"), "dim"))

    def _on_reload(self, number: int, pieces: list[dict]) -> None:
        for view in self._views(number):
            view.reset()
            for piece in pieces:
                view.feed(piece_text(piece), STYLES.get(piece.get("kind"), "dim"))

    def _on_state(self) -> None:
        client = self.client
        colours = {"connected": theme.GREEN, "calling": theme.YELLOW, "leaving": theme.YELLOW}
        for tab, button in zip(client.tabs(), self.tabs, strict=True):
            label = f"{tab.number}: {tab.label}" + (f"  •{tab.unread}" if tab.unread else "")
            button.setText(label)
            button.setChecked(tab.current)
            colour = colours.get(tab.state, theme.TEXT if tab.number == 0 else theme.DIM)
            button.setStyleSheet(f"color:{colour};" + ("font-weight:bold;" if tab.current
                                                        or tab.unread else ""))
        info = client.channel_info()
        if client.channel == 0:
            title = _("Monitor")
        else:
            title = _("Channel {channel}").format(channel=client.channel)
            if info.get("call"):
                title += f" · {info['call']}"
        self.channel_frame.set_title(title)
        target = client.target()
        self.line.setPlaceholderText(_("to {call}").format(call=target) if target
                                     else _("/c CALL connects this channel · /help"))
        self._tick()

    def _tick(self) -> None:
        parts = []
        for label, value, style in self.client.status():
            colour = {"bold green": theme.GREEN, "bold yellow": theme.YELLOW,
                      "bold red": theme.RED, "bold cyan": theme.CYAN}.get(style, theme.TEXT)
            parts.append((span(label, theme.DIM) + " " if label else "")
                         + span(value, colour, bold=True))
        self.status.setText(with_load_html("&nbsp;&nbsp;&nbsp;".join(parts)))
        self.footer.counters = self.client.counters()
        self.footer.redraw()

    def _feedback(self, level: str, text: str) -> None:
        self.feedback.setText(span(text, feedback_colour(level)) if text else "")

    def retheme(self) -> None:
        self._on_state()
        for number in (0, self.client.channel):
            self.run_worker(self.client.reload(number))

    # ---- keys ----------------------------------------------------------------------------

    def _submit(self) -> None:
        text = self.line.text()
        self.line.clear()
        self._feedback("", "")
        self.run_worker(self._do_submit(text))

    async def _do_submit(self, text: str) -> None:
        if await self.client.submit(text) == "quit":
            self.action_leave()

    def action_channel(self, number: int) -> None:
        self.run_worker(self.client.select(number), group="channel", exclusive=True)
        self.line.setFocus()

    def action_monitor(self) -> None:
        self.monitor_frame.setVisible(not self.monitor_frame.isVisible())

    def action_heard(self) -> None:
        self.push_overlay(HeardView(self))

    def action_scroll(self, direction: int) -> None:
        self.channel_view.scroll_page(direction)

    def action_clear(self) -> None:
        self.run_worker(self.client.clear())

    def action_recall(self, delta: int) -> None:
        text = self.client.recall(delta)
        if text is not None:
            self.line.setText(text)
            self.line.setCursorPosition(len(text))

    def action_escape(self) -> None:
        self.line.clear()
        self._feedback("", "")
        self.client.history_index = None

    def action_leave(self) -> None:
        self.run_worker(self._leave(), group="leave", exclusive=True)

    async def _leave(self) -> None:
        # The sessions stay up in the core; coming back shows them as they are.
        self.timer.stop()
        if self.owns_link:
            await self.link.close()
        self.exit(0)
