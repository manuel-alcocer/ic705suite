"""FT8 in the graphical client: the terminal's screen (apps.ft8.app) with the same keys, and
the mouse: a double click answers a station, buttons call CQ and halt, the band from a list,
Stop/Start, the CQ-only box; above them the waterfall (gui.waterfall: a click there puts
our transmit frequency, a call on it chooses that station), beside the band's activity the
QSO, and under them what we transmit with: the audio frequency, the audio level, the
radio's power, and the ALC."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from ic705suite.apps.ft8.app import (
    ALC_GOOD,
    ALC_HOT,
    FREQ_STEP,
    FT8_BANDS,
    HINT,
    SHORT_HINT,
    STYLE_B4,
    STYLE_CONFIRMED,
    STYLE_CQ,
    STYLE_DOUBT,
    STYLE_HEADING,
    STYLE_ME,
    STYLE_NEW_BAND,
    STYLE_NEW_DXCC,
    STYLE_NEW_GRID,
    STYLE_TX,
    Ft8App,
    conversation,
    extra,
    mark,
    merge,
    power_percent,
    power_step,
    qso_status,
    rows,
    sent_line,
    watts,
)
from ic705suite.apps.ft8.waterfall import decode as waterfall_levels
from ic705suite.gui import theme
from ic705suite.gui.ft8map import MapView
from ic705suite.gui.page import Page
from ic705suite.gui.waterfall import WaterfallView
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
        self.rig: dict = {}
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
                                   ("A", _("Auto"), self.action_auto),
                                   ("H", _("Halt"), self.action_halt),
                                   ("T", _("Tone"), self.action_tone),
                                   ("F", _("TX freq"), self.action_tx_freq)):
            button = QPushButton(f"{key}  {label}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _c=False, a=action: a())
            controls.addWidget(button)
            if key == "T":
                self.tone_button = button
            if key == "A":
                self.auto_button = button
                button.setToolTip(_("Super-automatic: answers new countries first, then "
                                    "the least worked, then ours; three calls each; after "
                                    "six stations without a QSO, six CQs"))
        self.frame.body.addLayout(controls)
        self.table = self.frame.add(self._log_table(placed=True), 1)
        self.table.pass_letters = True                       # C, S are the page's
        self.table.cellDoubleClicked.connect(self._double_click)
        self.qso_frame = Frame(_("QSO"))
        # How far the QSO under way has gone: its five messages, both ways.
        self.qso_bar = self.qso_frame.add(QProgressBar())
        self.qso_bar.setRange(0, 100)
        self.qso_bar.setTextVisible(True)
        self.qso_bar.setMinimumHeight(22)
        self.qso_bar.setToolTip(_("The QSO's five messages: those gone by of all"))
        self.completed = ""                     # the call of the last QSO completed
        self.qso_table = self.qso_frame.add(self._log_table(), 1)
        self.qso_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.qso_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.frame)
        self.splitter.addWidget(self.qso_frame)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([600, 400])
        self.wf_frame = Frame(_("Waterfall"))
        self.wf_frame.body.addLayout(self._wf_controls())
        self.waterfall = self.wf_frame.add(WaterfallView(), 1)
        self.waterfall.freq_clicked.connect(lambda hz: self.run_worker(self._call(
            _("Transmitting on {freq} Hz"), "ft8.txfreq", freq=hz), group="tx"))
        self.waterfall.station_clicked.connect(self._choose)
        self.waterfall.station_double_clicked.connect(
            lambda start, decode: self._choose(start, decode) and self.action_reply())
        # Beside the waterfall, in a tab: the map (gui.ft8map), as GridTracker's.
        self.map_frame = Frame(_("Map"))
        self.map = self.map_frame.add(MapView(), 1)
        self.map.setToolTip(_("Green CQ · red calling you · magenta ring a new DXCC · hollow: "
                              "placed by its country · squares: orange worked, green "
                              "confirmed · wheel zooms, drag moves, click chooses, double "
                              "click answers"))
        self.map.station_clicked.connect(self._choose)
        self.map.station_double_clicked.connect(
            lambda start, decode: self._choose(start, decode) and self.action_reply())
        self.top_tabs = QTabWidget()
        self.top_tabs.addTab(self.wf_frame, _("Waterfall"))
        self.top_tabs.addTab(self.map_frame, _("Map"))
        self.top_tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.top_tabs.currentChanged.connect(self._tab_changed)
        self.panes = QSplitter(Qt.Orientation.Vertical)
        self.panes.addWidget(self.top_tabs)
        self.panes.addWidget(self.splitter)
        self.panes.setChildrenCollapsible(False)
        self.panes.setSizes([330, 400])
        self._watch_turn = 0
        self.tx_frame = Frame(_("Transmit"))
        self.tx_frame.body.addLayout(self._tx_controls())
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.footer = Footer([HINT, SHORT_HINT])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.status)
        body = QVBoxLayout()
        body.setContentsMargins(8, 4, 8, 4)
        body.addWidget(self.panes, 1)
        body.addWidget(self.tx_frame)
        body.addWidget(self.message)
        layout.addLayout(body, 1)
        layout.addWidget(self.footer)
        self.timer = QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()
        self._tick()

    def _log_table(self, placed: bool = False) -> Table:
        """A list of decodes; `placed`: with where the sender is and its mark (rows())."""
        labels = [_("UTC"), "dB", "DT", _("Freq"), _("Message")]
        if placed:
            labels += [_("Country"), _("Distance"), ""]
        table = Table(0, len(labels))
        table.setHorizontalHeaderLabels(labels)
        table.right_align(1, 2, 3)
        table.verticalHeader().hide()
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.setFont(theme.mono(10.5))
        table.verticalHeader().setDefaultSectionSize(
            table.fontMetrics().height() + 4)               # compact, as a log
        header = table.horizontalHeader()
        for column in range(len(labels)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        return table

    def _tx_controls(self) -> QHBoxLayout:
        """TX audio frequency, audio level, the radio's power, and the ALC."""
        row = QHBoxLayout()

        def spin(box, label: str, tip: str):
            box.setKeyboardTracking(False)               # typed: taken on Enter
            box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            box.setToolTip(tip)
            box.editingFinished.connect(lambda: self.table.setFocus())   # the keys again
            row.addWidget(QLabel(label))
            row.addWidget(box)
            return box

        self.freq_spin = spin(QSpinBox(), _("Audio"), _(
            "Audio frequency of what we send (F takes the chosen line's; [ ] move it)"))
        self.freq_spin.setRange(200, 3000)
        self.freq_spin.setSingleStep(FREQ_STEP)
        self.freq_spin.setSuffix(" Hz")
        self.freq_spin.valueChanged.connect(lambda v: self._changed(
            "freq", v, lambda: self.run_worker(self._call(
                _("Transmitting on {freq} Hz"), "ft8.txfreq", freq=v), group="tx")))
        self.level_spin = spin(QSpinBox(), _("Level"), _(
            "Transmit audio: the ALC within its zone, under 0.5 best (- +)"))
        self.level_spin.setRange(5, 100)
        self.level_spin.setSingleStep(5)
        self.level_spin.setSuffix(" %")
        self.level_spin.valueChanged.connect(lambda v: self._changed(
            "level", v, lambda: self.run_worker(self._call(
                _("TX level {percent} %"), "ft8.level", level=v / 100), group="tx")))
        self.power_spin = spin(QDoubleSpinBox(), _("Power"), _(
            "The radio's RF power (, .)"))
        self.power_spin.setDecimals(1)
        self.power_spin.setRange(0, 10)
        self.power_spin.setSingleStep(0.5)
        self.power_spin.setSuffix(" W")
        self.power_spin.valueChanged.connect(lambda v: self._changed(
            "power", v, lambda: self.run_worker(self._power(
                v * 100 / self._max_watts()), group="power", exclusive=True)))
        row.addSpacing(12)
        row.addWidget(QLabel("ALC"))
        self.alc_bar = QProgressBar()
        self.alc_bar.setRange(0, 100)
        self.alc_bar.setTextVisible(False)
        self.alc_bar.setFixedWidth(140)
        self.alc_bar.setMaximumHeight(14)
        self.alc_bar.setToolTip(_("The radio's ALC while sending, 1.0 the end of its zone: "
                                  "green up to 0.5, yellow up to 1.0; past it, lower the "
                                  "level"))
        row.addWidget(self.alc_bar)
        self.alc_label = QLabel()
        self.alc_label.setTextFormat(Qt.TextFormat.RichText)
        self.alc_label.setMinimumWidth(self.alc_label.fontMetrics().horizontalAdvance("x" * 34))
        row.addWidget(self.alc_label)
        row.addStretch(1)
        self._shown: dict[str, float] = {}
        return row

    def _wf_controls(self) -> QHBoxLayout:
        """Gain and zero of the waterfall's colours, as WSJT-X's sliders."""
        row = QHBoxLayout()
        row.addWidget(QLabel(_("Click: TX frequency · a call: that station (double: answer)")))
        row.addStretch(1)
        for label, low, high, value, tip, apply in (
                (_("Gain"), 3, 30, 10, _("Contrast: how many dB go from black to red"),
                 lambda v: self.waterfall.set_colours(gain=v / 10)),
                (_("Zero"), -10, 20, 0, _("dB over the noise that stays black"),
                 lambda v: self.waterfall.set_colours(zero=float(v)))):
            row.addWidget(QLabel(label))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(low, high)
            slider.setValue(value)
            slider.setFixedWidth(110)
            slider.setToolTip(tip)
            slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            slider.valueChanged.connect(apply)
            row.addWidget(slider)
        return row

    def _tab_changed(self, index: int) -> None:
        """The map wants room: the top part takes most of the height while it shows."""
        total = sum(self.panes.sizes()) or self.height()
        share = 0.6 if self.top_tabs.widget(index) is self.map_frame else 0.42
        self.panes.setSizes([int(total * share), total - int(total * share)])

    def _choose(self, start: float, decode: dict) -> bool:
        """The line of a station clicked on the waterfall, chosen in the list."""
        for row, (line_start, line, _cells, _style) in enumerate(self.lines):
            if line is not None and line_start == start and \
                    line.get("message") == decode.get("message"):
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                self.table.setFocus()
                return True
        return False

    def _changed(self, name: str, value: float, send) -> None:
        """A box changed by the operator (not by an update from the core)."""
        if self._shown.get(name) != value:
            self._shown[name] = value
            send()

    def _show(self, box, name: str, value: float) -> None:
        if box.hasFocus() or self._shown.get(name) == value:
            return
        self._shown[name] = value
        box.blockSignals(True)
        box.setValue(value)
        box.blockSignals(False)

    def _max_watts(self) -> float:
        return float(self.rig.get("max_power_w") or 10)

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
        self.map.mycall = self.mycall
        for cycle in (await self.link.call("ft8.history")).get("cycles", []):
            merge(self.cycles, cycle)       # what the core kept (after a reconnection: more)
            self.map.add_decodes(cycle["start"], cycle["decodes"])
        await self._read_worked()
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

    async def _read_worked(self) -> None:
        """Our place and the squares worked and confirmed, for the map."""
        reply = await self.link.call("ft8.worked")
        if reply.get("error"):
            return
        self.map.set_home(reply.get("lat"), reply.get("lon"), reply.get("locator", ""))
        self.map.set_worked(reply.get("grids") or {})

    @staticmethod
    def _error(reply: dict) -> str:
        return render({"msgid": reply.get("msgid"), "args": reply.get("args", {}),
                       "text": reply["error"]})

    # ---- events -------------------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "rig":
            self.status.update_rig(event)
            self.rig = event
            self._show_tx()
        elif kind == "ft8":
            them = (self.state.get("tx") or {}).get("them")
            self.state = event
            if (event.get("tx") or {}).get("on"):
                self.completed = ""                     # a new QSO (or CQ) under way
            if (event.get("tx") or {}).get("them") != them:
                self._fill_qso()
            self._tick()
        elif kind == "ft8.sent":
            self._show_tx()
        elif kind == "reconnected":          # the core is back (perhaps restarted): again
            self.run_worker(self._start(), group="start", exclusive=True)
        elif kind == "ft8.wf":
            self.waterfall.add_row(waterfall_levels(event["levels"]), event["time"],
                                   event.get("low", 0), event.get("step", 5))
        elif kind == "ft8.cycle":
            self.waterfall.add_labels(event["start"], event["decodes"])
            self.map.add_decodes(event["start"], event["decodes"])
            merge(self.cycles, event)
            self._fill(keep_end=True)
            self._message("info", "")
        elif kind == "ft8.qso":
            self.completed = event["qso"].get("CALL", "")
            self._show_progress()
            self.run_worker(self._read_worked(), group="worked", exclusive=True)
            if not event.get("logbook"):         # the core's shared logbook has it
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
        self._paint(table, self.lines)
        if self.lines and (at_end or not keep_end):
            table.scrollToBottom()
        if 0 <= chosen < len(self.lines):
            table.selectRow(chosen)
        self._fill_qso()

    def _paint(self, table: Table, lines: list[tuple]) -> None:
        table.setRowCount(len(lines))
        for row, (_start, decode, cells, style) in enumerate(lines):
            fg, bg, bold = self._colours(style)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                colour, strong = fg, bold
                if column == 6:                              # how far: quiet
                    colour, strong = QColor(theme.DIM), False
                elif column == 7 and decode is not None:     # the logbook's mark
                    colour, strong = self._mark_colour(mark(decode)[1])
                if colour is not None:
                    item.setForeground(QBrush(colour))
                if bg is not None:
                    item.setBackground(QBrush(bg))
                if strong:
                    font = QFont(table.font())
                    font.setBold(True)
                    item.setFont(font)
                if column == 6 and decode is not None and decode.get("approx"):
                    item.setToolTip(_("From the country's middle: no grid heard from it yet"))
                table.setItem(row, column, item)

    @staticmethod
    def _mark_colour(style: str) -> tuple[QColor | None, bool]:
        return {STYLE_NEW_DXCC: (QColor(theme.MAGENTA), True),
                STYLE_NEW_BAND: (QColor(theme.MAGENTA), False),
                STYLE_NEW_GRID: (QColor(theme.CYAN), True),
                STYLE_B4: (QColor(theme.DIM), False),
                STYLE_CONFIRMED: (QColor(theme.GREEN), False)}.get(style, (None, False))

    def _show_progress(self) -> None:
        status = qso_status(self.state.get("tx") or {}, self.completed)
        if status is None:
            self.qso_bar.setValue(0)
            self.qso_bar.setFormat(_("No QSO under way"))
            self.qso_bar.setStyleSheet("")
            return
        done, total, text = status
        percent = done * 100 // total
        self.qso_bar.setValue(percent)
        self.qso_bar.setFormat(f"{percent}% · {text}")
        colour = theme.GREEN if done == total else theme.CYAN
        self.qso_bar.setStyleSheet(
            f"QProgressBar {{ background: {theme.RAISED}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 3px; text-align: center; color: {theme.TEXT}; }} "
            f"QProgressBar::chunk {{ background: {colour}; }}")

    def _fill_qso(self) -> None:
        them = (self.state.get("tx") or {}).get("them") or ""
        self._paint(self.qso_table, conversation(self.cycles, self.mycall, them))
        self.qso_table.scrollToBottom()

    def _show_tx(self) -> None:
        tx = self.state.get("tx") or {}
        if tx.get("freq"):
            self._show(self.freq_spin, "freq", int(tx["freq"]))
        if tx.get("level") is not None:
            self._show(self.level_spin, "level", round(float(tx["level"]) * 100))
        self.power_spin.setMaximum(self._max_watts())
        percent = power_percent(self.rig)
        if percent is not None:
            self._show(self.power_spin, "power",
                       round(percent * self._max_watts() / 100 * 2) / 2)
        if self.rig.get("transmitting"):
            alc, note = float((self.rig.get("meters") or {}).get("alc") or 0), ""
        elif tx.get("last"):
            alc, note = float(tx["last"]["alc"]), _("last")
        else:
            alc, note = 0.0, ""
        hot = alc > ALC_HOT
        colour = theme.RED if hot else theme.YELLOW if alc > ALC_GOOD else theme.GREEN
        self.alc_bar.setValue(min(100, round(alc / (ALC_HOT * 1.25) * 100)))   # 1.0 at 80 %
        self.alc_bar.setStyleSheet(
            f"QProgressBar {{ background: {theme.RAISED}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 3px; }} QProgressBar::chunk {{ background: {colour}; }}")
        text = f"{alc:.2f}" + (f" ({note})" if note else "")
        if hot:
            text += " · " + _("lower the level")
        self.alc_label.setText(span(text, colour, hot))

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
        self._show_tx()
        self._show_progress()
        tx = self.state.get("tx") or {}
        toning = bool(tx.get("tone"))
        self.tone_button.setText(f"T  {_('Stop tone') if toning else _('Tone')}")
        self.tone_button.setStyleSheet(f"background: {theme.RED}; color: #ffffff;"
                                       if toning else "")
        auto = bool((self.state.get("auto") or {}).get("on"))
        self.auto_button.setText(f"A  {_('Auto on') if auto else _('Auto')}")
        self.auto_button.setStyleSheet(f"background: {theme.MAGENTA}; color: #ffffff;"
                                       if auto else "")
        self.waterfall.set_markers(int(tx.get("freq") or 1500), int(tx.get("rx") or 0))
        self.map.set_them((tx.get("them") or "") if tx.get("on") else "")
        self._watch_turn += 1
        if self._watch_turn % 60 == 0 and self.timer.isActive():
            self.run_worker(self._read_worked(), group="worked", exclusive=True)
        if on and self._watch_turn % 5 == 1 and self.timer.isActive():
            # The core sends the waterfall only while someone asks for it.
            self.run_worker(self.link.call("ft8.waterfall", seconds=15), group="watch",
                            exclusive=True)

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
            row, why = await asyncio.to_thread(
                log_qso, fields, self.config.ft8_setup if self.config else "",
                ((self.link.welcome or {}).get("rig") or {}).get("name") or "")
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

    def action_auto(self) -> None:
        on = not (self.state.get("auto") or {}).get("on")
        self.run_worker(self._call(_("Super-automatic mode on: it answers and calls CQ by "
                                     "itself (A or H stops it)") if on else
                                   _("Super-automatic mode off"), "ft8.auto", on=on),
                        group="tx")

    def action_tx_freq(self) -> None:
        chosen = self._chosen()
        if chosen is None:
            self._message("error", _("Choose a line first (↑↓): its frequency"))
            return
        self.run_worker(self._call(_("Transmitting on {freq} Hz"), "ft8.txfreq",
                                   freq=chosen[1]["freq"]), group="tx")

    def action_tone(self) -> None:
        if (self.state.get("tx") or {}).get("tone"):
            self.run_worker(self._call(_("Tone stopped"), "ft8.tone", on=False), group="tx")
        else:
            self.run_worker(self._call(_("Tone on {freq} Hz for 10 s: watch the ALC (T or H "
                                         "stops it)").replace("{freq}", str(
                                             (self.state.get("tx") or {}).get("freq", ""))),
                                       "ft8.tone", on=True), group="tx")

    def action_move_tx(self, step: int) -> None:
        freq = int((self.state.get("tx") or {}).get("freq") or 1500) + step * FREQ_STEP
        self.run_worker(self._call(_("Transmitting on {freq} Hz"), "ft8.txfreq", freq=freq),
                        group="tx")

    def action_level(self, step: int) -> None:
        level = float((self.state.get("tx") or {}).get("level") or 0.5) + step * 0.05
        self.run_worker(self._call(_("TX level {percent} %"), "ft8.level", level=level),
                        group="tx")

    def action_power(self, step: int) -> None:
        percent = power_step(self.rig, power_percent(self.rig), step)
        self.run_worker(self._power(percent), group="power", exclusive=True)

    async def _power(self, percent: float) -> None:
        reply = await self.link.call("rig.set", id="power", value=percent)
        if reply.get("error"):
            self._message("error", reply["error"])
            return
        value = reply.get("value", percent)
        self.rig.setdefault("controls", {})["power"] = value    # before the next rig event
        self._message("info", _("Power {watts}").format(watts=watts(self.rig, value)))
        self._show_tx()

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
