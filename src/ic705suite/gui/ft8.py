"""FT8 in the graphical client: the terminal's screen (apps.ft8.app) with the same keys, and
the mouse: a double click answers a station, buttons call CQ and halt, the band from a list,
Stop/Start, the CQ-only box."""

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
    STYLE_HEADING,
    STYLE_ME,
    STYLE_TX,
    Ft8App,
    extra,
    merge,
    rows,
    sent_line,
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
        self.lines: list[tuple] = []
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
        for key, label, action in (("Enter", _("Answer"), self.action_reply),
                                   ("Q", "CQ", self.action_cq),
                                   ("H", _("Halt"), self.action_halt),
                                   ("F", _("TX freq"), self.action_tx_freq)):
            button = QPushButton(f"{key}  {label}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _c=False, a=action: a())
            controls.addWidget(button)
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
        self.table.cellDoubleClicked.connect(self._double_click)
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
            merge(self.cycles, event)
            self._fill(keep_end=True)
            self._message("info", "")
        elif kind == "ft8.qso":
            self.run_worker(self._logbook(event["qso"]))
        elif kind == "ft8.tx":
            merge(self.cycles, sent_line(event))
            self._fill(keep_end=True)
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _colours(self, style: str) -> tuple[QColor | None, QColor | None, bool]:
        """(foreground, background, bold) of a line drawn with a terminal style."""
        if style == STYLE_ME:
            return QColor("#ffffff"), QColor("#8b1a1a"), True
        if style == STYLE_CQ:
            return QColor(theme.GREEN), None, True
        if style == STYLE_DOUBT or style == STYLE_HEADING:
            return QColor(theme.DIM), None, False
        if style == STYLE_TX:
            return QColor(theme.YELLOW), None, True
        return None, None, False

    def _fill(self, keep_end: bool = False) -> None:
        table = self.table
        bar = table.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        chosen = table.currentRow()
        self.lines = rows(self.cycles, self.cq_only, self.mycall)
        table.setRowCount(len(self.lines))
        for row, (_start, _decode, cells, style) in enumerate(self.lines):
            fg, bg, bold = self._colours(style)
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
        if self.lines and (at_end or not keep_end):
            table.scrollToBottom()
        if 0 <= chosen < len(self.lines):
            table.selectRow(chosen)

    def _double_click(self, row: int, _column: int) -> None:
        self.table.selectRow(row)
        self.action_reply()

    def _chosen(self) -> tuple[float, dict] | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.lines):
            start, decode, _cells, _style = self.lines[row]
            if decode is not None and decode.get("kind") != "tx":
                return start, decode
        return None

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

    async def _logbook(self, fields: dict) -> None:
        """The QSO completed into this machine's logbook (hamrlog), besides the core's
        ft8_log.adi."""
        import asyncio

        from ic705suite.apps.ft8.logbook import log_qso

        try:
            row, why = await asyncio.to_thread(log_qso, fields)
        except Exception as exc:  # noqa: BLE001 - shown on screen
            row, why = None, str(exc)
        if row is not None:
            self._message("info", _("{call} logged in the logbook (FT8 {band}, sent {sent}, "
                                    "received {rcvd})").format(
                call=fields["CALL"], band=fields.get("BAND", ""),
                sent=fields.get("RST_SENT", ""), rcvd=fields.get("RST_RCVD", "")))
        elif why != "already logged":
            self._message("error", _("Not in the logbook: {error} (it is in ft8_log.adi)")
                          .format(error=why))

    def action_reply(self) -> None:
        chosen = self._chosen()
        if chosen is None:
            self._message("error", _("Choose a station's line first (↑↓)"))
            return
        self.run_worker(self._call(_("Answering: {message}"), "ft8.reply",
                                   decode=chosen[1], start=chosen[0]), group="tx")

    def action_cq(self) -> None:
        self.run_worker(self._call(_("Calling: {message}"), "ft8.cq"), group="tx")

    def action_halt(self) -> None:
        self.run_worker(self._call(_("Transmission halted"), "ft8.halt"), group="tx")

    def action_tx_freq(self) -> None:
        chosen = self._chosen()
        if chosen is None:
            self._message("error", _("Choose a line first (↑↓): its frequency"))
            return
        self.run_worker(self._call(_("Transmitting on {freq} Hz"), "ft8.txfreq",
                                   freq=chosen[1]["freq"]), group="tx")

    async def _call(self, done: str, method: str, **params) -> None:
        reply = await self.link.call(method, **params)
        if reply.get("error"):
            self._message("error", self._error(reply))
        else:
            self._message("info", done.format(**{k: v for k, v in reply.items()
                                                 if isinstance(v, (str, int))}))

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
