"""Phone: the radio for talking, in one page. The rig view's digits, meters and scope
(gui.view, whose page this one extends), with what phone needs around them: buttons for the
bands (at each band's phone segment, or where it was left), the voice modes and the
filters; the receive and transmit controls a phone operator turns (AF, SQL, PWR, MIC,
COMP, VOX, NB, NR...); the PTT, with the computer's microphone or the radio's own, held or
latched, with the mouse or the space bar; the memory channels (the suite's and the
radio's: gui.memories); and the quick log with today's contacts.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

from ic705suite.apps.view.app import digits_text
from ic705suite.gui import theme
from ic705suite.gui.dashboard import DashboardPage
from ic705suite.gui.memories import MemoriesPane
from ic705suite.gui.view import ViewPage
from ic705suite.gui.widgets import (
    ControlGrid,
    Footer,
    Frame,
    LoadLine,
    Scope,
    SevenSegment,
    StatusLine,
    Table,
)
from ic705suite.i18n import N_, _, render
from ic705suite.ui.panelmodel import PanelModel

# The front-panel controls a phone operator turns, in five short columns of their own (the
# rig's groups would make one nine rows tall), in this order.
PHONE_GROUPS = (("rx", N_("Receive")), ("front", N_("Front end")), ("dsp", N_("DSP")),
                ("tx", N_("Transmit")), ("txaudio", N_("TX audio")))
PHONE_LAYOUT = {"af": "rx", "rf": "rx", "sql": "rx", "agc": "rx",
                "preamp": "front", "att": "front", "filter": "front", "filter_width": "front",
                "nb": "dsp", "nr": "dsp", "nr_level": "dsp",
                "power": "tx", "mic": "tx", "comp": "tx", "comp_level": "tx",
                "tx_width": "txaudio", "vox": "txaudio", "vox_gain": "txaudio",
                "monitor": "txaudio", "monitor_gain": "txaudio"}
PHONE_CONTROLS = tuple(PHONE_LAYOUT)
# Each band's phone segment, and its sideband (the mode for 2 m and 70 cm: FM).
PHONE_BANDS = (("160m", 1_850_000, "LSB"), ("80m", 3_700_000, "LSB"),
               ("60m", 5_360_000, "USB"), ("40m", 7_150_000, "LSB"),
               ("20m", 14_200_000, "USB"), ("17m", 18_140_000, "USB"),
               ("15m", 21_250_000, "USB"), ("12m", 24_950_000, "USB"),
               ("10m", 28_500_000, "USB"), ("6m", 50_150_000, "USB"),
               ("2m", 145_500_000, "FM"), ("70cm", 433_500_000, "FM"))
VOICE_MODES = ("LSB", "USB", "AM", "FM")
SOURCES = (("pc", N_("PC microphone")), ("radio", N_("Radio microphone")))
KEY_MODES = (("hold", N_("Space held")), ("toggle", N_("Space toggles")),
             ("mouse", N_("Mouse only")))
HEARTBEAT_MS = 400       # the PTT said again while held, with the radio's microphone


class PhonePage(ViewPage):
    def __init__(self, window, link, config, owns_link: bool = False) -> None:
        self.left: dict[str, tuple[int, str]] = {}   # band -> where it was left, its mode
        self.microphone = None
        self.keyed = False                  # the PTT down (or latched)
        self.keyed_at = 0.0
        self._by_key = False                # keyed with the space bar (held)
        super().__init__(window, link, config, owns_link)
        self.heartbeat = QTimer(self, interval=HEARTBEAT_MS,
                                timeout=lambda: self.run_worker(self._heartbeat()))
        self.tx_clock = QTimer(self, interval=250, timeout=self._show_ptt)

    # ---- layout -------------------------------------------------------------------------

    def _build(self) -> None:
        self.status = StatusLine(self.link.description)
        # The VFO: digits, the line under them, and the buttons for bands, modes, filters.
        self.vfo_frame = Frame(_("Phone"))
        self.digits = self.vfo_frame.add(SevenSegment(digits_text(None)), 1)
        self.digits.wheel.connect(lambda d: self.run_worker(self.action_tune(d)))
        self.vfo_label = self.vfo_frame.add(QLabel())
        self.vfo_label.setTextFormat(Qt.TextFormat.RichText)
        self.band_buttons: dict[str, QPushButton] = {}
        bands_row = QHBoxLayout()
        bands_row.setSpacing(3)
        for name, _hz, _mode in PHONE_BANDS:
            button = self._button(name, lambda _c=False, n=name: self.run_worker(
                self.go_band(n)))
            self.band_buttons[name] = button
            bands_row.addWidget(button)
        bands_row.addStretch(1)
        self.vfo_frame.body.addLayout(bands_row)
        modes_row = QHBoxLayout()
        modes_row.setSpacing(3)
        self.mode_buttons: dict[str, QPushButton] = {}
        for mode in VOICE_MODES:
            button = self._button(mode, lambda _c=False, m=mode: self.run_worker(
                self._command(f"/mode {m}")))
            self.mode_buttons[mode] = button
            modes_row.addWidget(button)
        modes_row.addSpacing(12)
        self.filter_buttons: dict[str, QPushButton] = {}
        for number in ("1", "2", "3"):
            button = self._button(f"FIL{number}", lambda _c=False, n=number: self.run_worker(
                self._set("filter", n)))
            self.filter_buttons[number] = button
            modes_row.addWidget(button)
        modes_row.addSpacing(12)
        step = self._button(_("Step"), lambda _c=False: self.action_step())
        step.setCheckable(False)
        modes_row.addWidget(step)
        modes_row.addStretch(1)
        self.vfo_frame.body.addLayout(modes_row)

        self.meters_frame = Frame(_("Meters"))
        self.meters_box = QVBoxLayout()
        self.meters_box.setSpacing(2)
        self.meters_frame.body.addLayout(self.meters_box)
        self.meters_frame.body.addStretch(1)
        self._make_meters([])

        self.scope_frame = Frame(_("Scope"))
        self.scope = self.scope_frame.add(Scope(spectrum_share=0.45), 1)
        self.scope.setMinimumHeight(90)
        self.scope.clicked.connect(lambda hz: self.run_worker(self._command(f"/f {hz}")))
        self.scope.wheel.connect(lambda d: self.run_worker(self.action_tune(d)))

        # The PTT and how it is worked.
        self.ptt_frame = Frame(_("Transmit"))
        self.ptt_button = QPushButton(_("PTT"))
        self.ptt_button.setObjectName("ptt")
        self.ptt_button.setMinimumSize(170, 110)
        self.ptt_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ptt_button.pressed.connect(self._ptt_pressed)
        self.ptt_button.released.connect(self._ptt_released)
        self.ptt_frame.add(self.ptt_button, 1)
        options = QGridLayout()
        self.source_box = QComboBox()
        for key, name in SOURCES:
            self.source_box.addItem(_(name), key)
        self.key_box = QComboBox()
        for key, name in KEY_MODES:
            self.key_box.addItem(_(name), key)
        for box in (self.source_box, self.key_box):
            box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.source_box.setToolTip(_("The computer's microphone goes to the radio over the "
                                     "network; with the radio's, the PTT only keys it"))
        self.key_box.setToolTip(_("How the PTT is worked: the space bar held down, the space "
                                  "bar pressed once to talk and again to stop, or only the "
                                  "button with the mouse"))
        options.addWidget(QLabel(_("Microphone")), 0, 0)
        options.addWidget(self.source_box, 0, 1)
        options.addWidget(QLabel(_("PTT key")), 1, 0)
        options.addWidget(self.key_box, 1, 1)
        self.ptt_frame.body.addLayout(options)
        self.tx_label = self.ptt_frame.add(QLabel())
        self.tx_label.setTextFormat(Qt.TextFormat.RichText)

        # The controls phone needs (the rig view's grid, with only those).
        self.panel_frame = Frame(_("Controls"))
        self.model = PanelModel(PHONE_GROUPS)
        self.grid = self.panel_frame.add(ControlGrid(self.model))
        self.grid.request.connect(self._grid_request)
        self.grid.set_value.connect(lambda cid, value: self.run_worker(self._set(cid, value)))

        self.memories_frame = Frame(_("Memories"))
        self.memories = self.memories_frame.add(MemoriesPane(self), 1)

        # The quick log (the dashboard's: hamrlog's fast entry and today's contacts).
        self.quick_frame = Frame(_("Quick log · today"))
        self.qso_input = self.quick_frame.add(QLineEdit())
        self.qso_input.returnPressed.connect(lambda: self.run_worker(self._log_line()))
        self.qso_table = self.quick_frame.add(Table(0, 8), 1)
        self.qso_table.setHorizontalHeaderLabels([_("UTC"), _("Call"), _("Name"), _("Band"),
                                                  _("Mode"), _("Sent"), _("Rcvd"), _("QTH")])
        self.qso_table.verticalHeader().hide()
        self.qso_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.qso_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.qso_table.setShowGrid(False)
        self.qso_table.horizontalHeader().setStretchLastSection(True)

        # The rig command line, kept for anything else (and where messages go).
        self.entry_frame = Frame(_("Rig commands"))
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(_("/f 7.030 · /b 40 · /mode USB · /set af 40 · Esc back"))
        self.entry.returnPressed.connect(lambda: self.run_worker(self._submit()))
        self.entry_frame.add(self.entry)
        self.footer = Footer([_("Space PTT · PgUp/PgDn Tune · F2 Step · F3 Mode · F4/F5 Band · "
                                "F6 Filter · / command · Esc Back"), _("Space PTT · Esc Back")])

        top = QHBoxLayout()
        top.addWidget(self.vfo_frame, 3)
        top.addWidget(self.meters_frame, 2)
        middle = QHBoxLayout()
        middle.addWidget(self.ptt_frame, 1)
        middle.addWidget(self.panel_frame, 3)
        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.addWidget(self.memories_frame)
        bottom.addWidget(self.quick_frame)
        bottom.setChildrenCollapsible(False)
        bottom.setSizes([620, 520])
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 6, 0)
        box.setSpacing(6)
        box.addWidget(self.status)
        box.addLayout(top)
        box.addWidget(self.scope_frame, 1)
        box.addLayout(middle)
        box.addWidget(bottom, 2)
        box.addWidget(self.entry_frame)
        box.addWidget(LoadLine())
        box.addWidget(self.footer)
        for widget in (self.digits, self.vfo_label, self.scope, self.status, self.footer):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._show_vfo()
        self._show_ptt()

    @staticmethod
    def _button(text: str, action) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCheckable(True)
        button.clicked.connect(action)
        return button

    # ---- life ---------------------------------------------------------------------------

    def start(self) -> None:
        super().start()
        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self._app_state)
        self.memories.reload()
        self.run_worker(self._quicklog_start())

    def stop(self) -> None:
        QApplication.instance().removeEventFilter(self)
        if self.keyed:
            self.run_worker(self._talk(False))
        super().stop()

    async def _leave(self) -> None:
        if self.keyed:
            await self._talk(False)
        await super()._leave()

    def _apply_welcome(self, event: dict) -> None:
        rig = event.get("rig", {})
        by_id = {c["id"]: c for c in rig.get("controls", [])}
        self.controls = rig.get("controls", [])
        self.model.set_controls([{**by_id[cid], "group": PHONE_LAYOUT[cid]}
                                 for cid in PHONE_CONTROLS if cid in by_id])
        self._make_meters(rig.get("meters", []))

    def _apply_rig(self, event: dict) -> None:
        super()._apply_rig(event)
        band, mode = event.get("band"), event.get("mode")
        if band and event.get("frequency") and mode in VOICE_MODES:
            self.left[band] = (event["frequency"], mode)
        for name, button in self.band_buttons.items():
            button.setChecked(name == band)
        for name, button in self.mode_buttons.items():
            button.setChecked(name == mode)
        current = str((event.get("controls") or {}).get("filter") or "")
        for number, button in self.filter_buttons.items():
            button.setChecked(number == current)
        if not self.keyed and event.get("transmitting") is not None:
            self._show_ptt()

    # Quick log: the dashboard's own code (its widgets have the same names here).
    _quicklog_start = DashboardPage._quicklog_start
    _quicklog_reload = DashboardPage._quicklog_reload
    _log_line = DashboardPage._log_line

    # ---- tuning -------------------------------------------------------------------------

    async def go_band(self, name: str) -> None:
        """A band's button: where it was left in this session, else its phone segment."""
        default = next(((hz, mode) for n, hz, mode in PHONE_BANDS if n == name), None)
        if default is None:
            return
        hz, mode = self.left.get(name, default)
        await self._command(f"/f {hz}")
        await self._command(f"/mode {mode}")

    def tune_memory(self, memory: dict) -> None:
        self.run_worker(self._tune_memory(memory), group="tune")

    async def _tune_memory(self, memory: dict) -> None:
        fields = {k: v for k, v in memory.items() if k not in ("id", "added", "group",
                                                               "channel", "note")}
        reply = await self.link.call("memory.tune", **fields)
        if reply.get("error"):
            self._message("error", reply["error"])
        else:
            self._message("info", _("Tuned to {name}").format(
                name=memory.get("name") or memory.get("frequency")))

    def current_memory(self) -> dict | None:
        """The VFO now, as a memory (frequency, mode, filter, data mode)."""
        if not self.rig.get("connected") or not self.rig.get("frequency"):
            return None
        controls = self.rig.get("controls") or {}
        return {"frequency": self.rig["frequency"], "mode": self.rig.get("mode") or "USB",
                "filter": int(controls.get("filter") or 1),
                "data": int(bool(controls.get("data_mode"))), "name": ""}

    # ---- the PTT ------------------------------------------------------------------------

    @property
    def key_mode(self) -> str:
        return self.key_box.currentData()

    def _ptt_pressed(self) -> None:
        if self.key_mode == "toggle":
            self.set_ptt(not self.keyed)
        else:
            self.set_ptt(True)

    def _ptt_released(self) -> None:
        if self.key_mode != "toggle":
            self.set_ptt(False)

    def set_ptt(self, on: bool) -> None:
        if on == self.keyed:
            return
        self.keyed = on
        self.keyed_at = time.monotonic()
        self._show_ptt()
        self.run_worker(self._talk(on), group="ptt")

    async def _talk(self, on: bool) -> None:
        from ic705suite.speaker import Microphone, SpeakerError

        source = self.source_box.currentData()
        if not on:
            self.heartbeat.stop()
            self.tx_clock.stop()
            microphone, self.microphone = self.microphone, None
            await self.link.ptt(False, source)
            if microphone is not None:
                microphone.stop()
            self._show_ptt()
            return
        if source == "pc":
            rate = (self.link.welcome or {}).get("audio_rate", 0)
            try:
                if not rate:
                    raise SpeakerError(_("no audio from the radio reaches the core"))
                microphone = Microphone(rate, self.link.send_voice)
                microphone.start()
            except SpeakerError as exc:
                self._ptt_failed(_("cannot use the microphone: {error}").format(error=exc))
                return
            self.microphone = microphone
        reply = await self.link.ptt(True, source)
        if reply.get("error"):
            if self.microphone is not None:
                self.microphone.stop()
                self.microphone = None
            self._ptt_failed(render(reply) if reply.get("msgid") else reply["error"])
            return
        if not self.keyed:                         # let go while it was keying
            await self._talk(False)
            return
        if source == "radio":
            self.heartbeat.start()
        self.tx_clock.start()
        self._show_ptt()

    async def _heartbeat(self) -> None:
        if self.keyed:
            await self.link.ptt(True, "radio")

    def _ptt_failed(self, text: str) -> None:
        self.keyed = False
        self._message("error", text)
        self._show_ptt()

    def _show_ptt(self) -> None:
        on = self.keyed
        self.ptt_button.setText(_("TX") if on else _("PTT"))
        self.ptt_button.setStyleSheet(
            f"QPushButton#ptt {{ background: {theme.RED}; color: #ffffff; font-size: 26px; "
            f"font-weight: bold; }}" if on else
            "QPushButton#ptt { font-size: 26px; font-weight: bold; }")
        if on:
            seconds = int(time.monotonic() - self.keyed_at)
            self.tx_label.setText(f"<b style='color:{theme.RED}'>TX "
                                  f"{seconds // 60:02d}:{seconds % 60:02d}</b> · "
                                  + _("3 min at most"))
        elif self.rig.get("transmitting"):
            self.tx_label.setText(f"<span style='color:{theme.YELLOW}'>"
                                  + _("the radio is transmitting") + "</span>")
        else:
            self.tx_label.setText(f"<span style='color:{theme.DIM}'>"
                                  + _("receiving") + "</span>")

    # ---- the space bar ---------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's
        kind = event.type()
        if kind not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) or \
                event.key() != Qt.Key.Key_Space or self.key_mode == "mouse" or \
                not self._space_is_ptt():
            return False
        if event.isAutoRepeat():
            return True                            # held: one press, one release
        if kind == QEvent.Type.KeyPress:
            if self.key_mode == "toggle":
                self.set_ptt(not self.keyed)
            else:
                self._by_key = True
                self.set_ptt(True)
        elif self.key_mode == "hold" and self._by_key:
            self._by_key = False
            self.set_ptt(False)
        return True

    def _space_is_ptt(self) -> bool:
        """This page shown in the active window, and no field that takes a space."""
        if not self.isVisible() or not self.window().isActiveWindow() or \
                QApplication.activeModalWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        return not isinstance(focus, (QLineEdit, QAbstractSpinBox, QComboBox)) and \
            not (isinstance(focus, QAbstractItemView) and focus.state() ==
                 QAbstractItemView.State.EditingState)

    def _app_state(self, state) -> None:
        """The window left with the space bar held: no stuck transmitter."""
        if state != Qt.ApplicationState.ApplicationActive and self._by_key:
            self._by_key = False
            self.set_ptt(False)

    def _message(self, kind: str, text: str) -> None:
        self.entry_frame.note(text[:200], kind)

