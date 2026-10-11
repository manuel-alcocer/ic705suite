"""FT8 in the graphical client: the terminal's screen (apps.ft8.app) with the same keys, and
the mouse: the band from a list, Stop/Start, the CQ-only box."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
)

from ic705suite.apps.ft8.app import (
    FT8_BANDS,
    HINT,
    SHORT_HINT,
    STYLE_CQ,
    STYLE_DOUBT,
    STYLE_ME,
    Ft8App,
    extra,
    heading,
    kind_style,
)
from ic705suite.gui import theme
from ic705suite.gui.page import Page
from ic705suite.gui.widgets import Footer, Frame, StatusLine, Table, span
from ic705suite.i18n import _, render
from ic705suite.rig import bands
from ic705suite.station.links import Link


class Ft8Page(Page):
    TUI = Ft8App

    def __init__(self, window, link: Link, config, owns_link: bool = False) -> None:
        super().__init__(window, link, config)
        self.owns_link = owns_link
        self.setWindowTitle("FT8")
        self.state: dict = {}
        self.cycles: list[dict] = []
        self.cq_only = False
        self.started_here = False
        self.mycall = ""
        self._build()

    def _build(self) -> None:
        self.status = StatusLine(self.link.description)
        self.frame = Frame(_("Band activity"))
        controls = QHBoxLayout()
        controls.addWidget(QLabel(_("Band")))
        self.band_box = QComboBox()
        self.band_box.addItems(FT8_BANDS)
        self.band_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.band_box.activated.connect(lambda index: self.run_worker(
            self._band(FT8_BANDS[index])))
        controls.addWidget(self.band_box)
        self.toggle_button = QPushButton()
        self.toggle_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle_button.clicked.connect(self.action_toggle)
        controls.addWidget(self.toggle_button)
        self.cq_box = QCheckBox(_("Only the CQs"))
        self.cq_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cq_box.toggled.connect(lambda on: on != self.cq_only and self.action_cq_only())
        controls.addWidget(self.cq_box)
        controls.addStretch(1)
        self.frame.body.addLayout(controls)
        self.table = self.frame.add(Table(0, 5), 1)
        self.table.pass_letters = True                       # C, S are the page's
        self.table.setHorizontalHeaderLabels([_("UTC"), "dB", "DT", _("Freq"), _("Message")])
        self.table.right_align(1, 2, 3)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setFont(theme.mono(10.5))
        self.table.verticalHeader().setDefaultSectionSize(
            self.table.fontMetrics().height() + 4)          # compact, as a log
        header = self.table.horizontalHeader()
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.footer = Footer([HINT, SHORT_HINT])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.status)
        body = QVBoxLayout()
        body.setContentsMargins(8, 4, 8, 4)
        body.addWidget(self.frame, 1)
        body.addWidget(self.message)
        layout.addLayout(body, 1)
        layout.addWidget(self.footer)
        self.timer = QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()
        self._tick()

    def start(self) -> None:
        self.link.attach(self._on_event)
        self.run_worker(self._start())
        self.table.setFocus()

    def restore_focus(self) -> None:
        self.table.setFocus()

    async def _start(self) -> None:
        try:
            await self.link.connect()
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._message("error", _("cannot connect: {error}").format(error=exc))
            return
        self.mycall = ((self.link.welcome or {}).get("callsign") or "").upper()
        self.cycles = (await self.link.call("ft8.history")).get("cycles", [])
        self._fill()
        state = await self.link.call("ft8.state")
        self.state = state
        if not state.get("on"):
            reply = await self.link.call("ft8.start")
            if reply.get("error"):
                self._message("error", self._error(reply))
                return
            self.started_here = True
            self._message("info", _("Decoding: the first cycle comes when the next 15 s "
                                    "are heard whole"))
        self._tick()

    @staticmethod
    def _error(reply: dict) -> str:
        return render({"msgid": reply.get("msgid"), "args": reply.get("args", {}),
                       "text": reply["error"]})

    # ---- events -------------------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "rig":
            self.status.update_rig(event)
        elif kind == "ft8":
            self.state = event
            self._tick()
        elif kind == "ft8.cycle":
            if event.get("deep"):
                known = next((c for c in self.cycles if c["start"] == event["start"]), None)
                if known is not None:
                    known["decodes"] = known["decodes"] + event["decodes"]
            else:
                self.cycles.append(dict(event))
                del self.cycles[:-60]
            self._fill(keep_end=True)
            self._message("info", "")
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _colours(self, style: str) -> tuple[QColor | None, QColor | None, bool]:
        """(foreground, background, bold) of a line drawn with a terminal style."""
        if style == STYLE_ME:
            return QColor("#ffffff"), QColor("#8b1a1a"), True
        if style == STYLE_CQ:
            return QColor(theme.GREEN), None, True
        if style == STYLE_DOUBT:
            return QColor(theme.DIM), None, False
        return None, None, False

    def _fill(self, keep_end: bool = False) -> None:
        table = self.table
        bar = table.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        rows: list[tuple[list[str], str]] = []
        for cycle in self.cycles:
            rows.append((["────────", "", "", "", heading(cycle)], "heading"))
            shown = [d for d in cycle["decodes"] if not self.cq_only or d.get("kind") == "cq"
                     or kind_style(d, self.mycall) == STYLE_ME]
            for d in sorted(shown, key=lambda d: d["freq"]):
                rows.append(([f"{d['time'][:2]}:{d['time'][2:4]}:{d['time'][4:]}",
                              str(d["snr"]), f"{d['dt']:.1f}", str(d["freq"]), d["message"]],
                             kind_style(d, self.mycall)))
        table.setRowCount(len(rows))
        for row, (cells, style) in enumerate(rows):
            fg, bg, bold = (QColor(theme.DIM), None, False) if style == "heading" \
                else self._colours(style)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if fg is not None:
                    item.setForeground(QBrush(fg))
                if bg is not None:
                    item.setBackground(QBrush(bg))
                if bold:
                    font = QFont(table.font())
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row, column, item)
        if rows and (at_end or not keep_end):
            table.scrollToBottom()

    def _tick(self) -> None:
        self.status.extra = extra(self.state)
        self.status.redraw()
        on = self.state.get("on")
        self.toggle_button.setText(f"S  {_('Stop') if on else _('Start')}")
        band = self.state.get("band")
        if band in FT8_BANDS and self.band_box.currentText() != band:
            self.band_box.setCurrentText(band)
        self.footer.counters = [(_("Decoded"), sum(len(c["decodes"]) for c in self.cycles[-1:]))]

    def _message(self, kind: str, text: str) -> None:
        self.message.setText(span(text, theme.RED if kind == "error" else theme.CYAN,
                                  kind == "error"))

    def retheme(self) -> None:
        self._fill()
        self._tick()

    # ---- keys (the terminal's Ft8App actions) -------------------------------------------------

    def action_band(self, step: int) -> None:
        current = self.state.get("band") or "20m"
        index = FT8_BANDS.index(current) if current in FT8_BANDS else FT8_BANDS.index("20m")
        band = FT8_BANDS[(index + step) % len(FT8_BANDS)] if self.state.get("ft8_dial") \
            else FT8_BANDS[index]
        self.run_worker(self._band(band), group="band", exclusive=True)

    async def _band(self, band: str) -> None:
        reply = await self.link.call("ft8.band", band=band)
        if reply.get("error"):
            self._message("error", self._error(reply))
        else:
            self._message("info", _("FT8 on {band}: {frequency} USB-D").format(
                band=band, frequency=bands.format_frequency(reply["dial"])))

    def action_cq_only(self) -> None:
        self.cq_only = not self.cq_only
        self.cq_box.setChecked(self.cq_only)
        self._fill()
        self._message("info", _("Only the CQs (and what is sent to you)") if self.cq_only
                      else _("Every message"))

    def action_toggle(self) -> None:
        self.run_worker(self._toggle(), group="toggle", exclusive=True)

    async def _toggle(self) -> None:
        if self.state.get("on"):
            await self.link.call("ft8.stop")
            self.started_here = False
            self._message("info", _("Decoding stopped (S starts it again)"))
        else:
            reply = await self.link.call("ft8.start")
            if reply.get("error"):
                self._message("error", self._error(reply))
            else:
                self.started_here = True
                self._message("info", _("Decoding"))

    def action_leave(self) -> None:
        self.run_worker(self._leave(), group="leave", exclusive=True)

    async def _leave(self) -> None:
        self.timer.stop()
        if self.started_here:
            await self.link.call("ft8.stop")
        if self.owns_link:
            await self.link.close()
        self.exit(0)
