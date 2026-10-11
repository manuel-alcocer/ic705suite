"""The rig view of the graphical client: the terminal's (apps.view.app), with pixels.

Top: the frequency on seven-segment digits with the VFO, mode, filter and RIT, and the
meters. Middle: the band scope (spectrum over waterfall). Bottom: every front-panel control
of the rig, and the rig command line. The keys are the terminal view's; the mouse tunes
(click or wheel on the scope, wheel on the digits) and picks and turns the controls.
"""

from __future__ import annotations

import base64

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from ic705suite.apps.view.app import (
    HINT,
    SCOPE_KEEPALIVE,
    SHORT_HINT,
    ViewApp,
    digits_text,
    passband,
)
from ic705suite.gui import theme
from ic705suite.gui.page import Page
from ic705suite.gui.widgets import (
    SMETER_SCALE,
    ControlGrid,
    Footer,
    Frame,
    Meter,
    Scope,
    SevenSegment,
    StatusLine,
    badge,
    smeter,
    smeter_fraction,
    span,
)
from ic705suite.i18n import _, render
from ic705suite.station.links import Link, LinkError
from ic705suite.ui.panelkeys import PanelKeys
from ic705suite.ui.panelmodel import PanelModel, format_value
from ic705suite.ui.rigkeys import (
    band_beside,
    next_choice,
    next_mode,
    next_step,
    signal_beside,
    tune_target,
)


class ViewPage(PanelKeys, Page):
    TUI = ViewApp

    def __init__(self, window, link: Link, config, owns_link: bool = False) -> None:
        super().__init__(window, link, config)
        self.owns_link = owns_link
        self.rig: dict = {}
        self.step = 100
        self.controls: list[dict] = []
        self.meter_defs: list[dict] = []
        self.meters: dict[str, Meter] = {}
        self.model = PanelModel()
        self._build()
        self.scope_timer = QTimer(self, interval=int(SCOPE_KEEPALIVE * 1000),
                                  timeout=lambda: self.run_worker(self._keep_scope()))

    def _build(self) -> None:
        self.status = StatusLine(self.link.description)
        self.vfo_frame = Frame(_("Rig"))
        self.digits = self.vfo_frame.add(SevenSegment(digits_text(None)), 1)
        self.digits.wheel.connect(lambda d: self.run_worker(self.action_tune(d)))
        self.vfo_label = self.vfo_frame.add(QLabel())
        self.vfo_label.setTextFormat(Qt.TextFormat.RichText)
        self.meters_frame = Frame(_("Meters"))
        self.meters_box = QVBoxLayout()
        self.meters_box.setSpacing(2)
        self.meters_frame.body.addLayout(self.meters_box)
        self.meters_frame.body.addStretch(1)
        self._make_meters([])
        self.scope_frame = Frame(_("Scope"))
        self.scope = self.scope_frame.add(Scope(), 1)
        self.scope.clicked.connect(lambda hz: self.run_worker(self._command(f"/f {hz}")))
        self.scope.wheel.connect(lambda d: self.run_worker(self.action_tune(d)))
        self.panel_frame = Frame(_("Front panel"))
        self.grid = self.panel_frame.add(ControlGrid(self.model))
        self.grid.request.connect(self._grid_request)
        self.entry_frame = Frame(_("Rig commands"))
        row = QHBoxLayout()
        label = QLabel(_("COMMAND"))
        label.setStyleSheet(f"color:{theme.CYAN};font-weight:bold;")
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(_("/f 7.030 · /b 40 · /mode USB · /set af 40 · Esc back"))
        self.entry.returnPressed.connect(lambda: self.run_worker(self._submit()))
        row.addWidget(label)
        row.addWidget(self.entry, 1)
        self.entry_frame.body.addLayout(row)
        self.footer = Footer([HINT, SHORT_HINT])

        top = QHBoxLayout()
        top.addWidget(self.vfo_frame, 3)
        top.addWidget(self.meters_frame, 2)
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 6, 0)
        box.setSpacing(6)
        box.addWidget(self.status)
        box.addLayout(top)
        box.addWidget(self.scope_frame, 1)
        box.addWidget(self.panel_frame)
        box.addWidget(self.entry_frame)
        box.addWidget(self.footer)
        for widget in (self.digits, self.vfo_label, self.scope, self.status, self.footer):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._show_vfo()

    def _make_meters(self, meters: list[dict]) -> None:
        while self.meters_box.count():
            item = self.meters_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.meters = {}
        defs = meters or [{"id": "s", "label": "S", "unit": "", "maximum": 241, "tx_only": False}]
        for meter in defs:
            widget = Meter(_(meter["label"]), SMETER_SCALE if meter["id"] == "s" else None)
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if meter["id"] == "s":
                widget.warm_from = smeter_fraction(120)
            self.meters_box.addWidget(widget)
            self.meters[meter["id"]] = widget
        self.meter_defs = defs

    # ---- life ---------------------------------------------------------------------------

    def start(self) -> None:
        self.link.attach(self._on_event)
        self.grid.setFocus()
        self.scope_timer.start()
        self.run_worker(self._connect())

    def restore_focus(self) -> None:
        self.grid.setFocus()

    def stop(self) -> None:
        self.scope_timer.stop()
        super().stop()

    async def _connect(self) -> None:
        try:
            await self.link.connect()
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._message("error", _("cannot connect: {error}").format(error=exc))
            return
        if self.link.welcome:
            self._apply_welcome(self.link.welcome)
        await self._keep_scope()

    async def _keep_scope(self) -> None:
        if not self.link.connected:
            return
        try:
            reply = await self.link.call("scope.watch", seconds=SCOPE_KEEPALIVE * 3)
        except (LinkError, OSError):
            return
        if reply.get("error"):
            self.scope_frame.note(reply["error"], "error")

    # ---- events -------------------------------------------------------------------------

    def _apply_welcome(self, event: dict) -> None:
        rig = event.get("rig", {})
        self.controls = rig.get("controls", [])
        self.model.set_controls(self.controls)
        self._make_meters(rig.get("meters", []))

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "rig":
            self._apply_rig(event)
        elif kind == "scope":
            self._apply_scope(event)
        elif kind == "welcome":
            self._apply_welcome(event)
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _apply_rig(self, event: dict) -> None:
        self.rig = event
        self.status.update_rig(event)
        self.digits.set_text(digits_text(event.get("frequency")))
        self._show_vfo()
        self._show_meters()
        if not self.model.editing:
            self.model.set_values(event.get("controls", {}))
        self.scope.view.dial = event.get("frequency")
        self.scope.view.passband = passband(event.get("mode"), event.get("frequency"),
                                            event.get("controls", {}).get("filter_width"))
        self.scope.update()
        self.vfo_frame.set_title(f"{event.get('model_name', '')} · {event.get('radio', '')}")

    def _apply_scope(self, event: dict) -> None:
        data = np.frombuffer(base64.b64decode(event["data"]), dtype=np.uint8) / 255.0
        self.scope.add_frame(data, int(event["start"]), int(event["end"]))
        span_khz = (event["end"] - event["start"]) / 2000
        mode = _("centre") if event.get("center") else _("fixed")
        self.scope_frame.set_title(f"{_('Scope')} · {mode} ±{span_khz:g} kHz")
        self.scope_frame.note(_("out of range") if event.get("out_of_range") else "", "error")

    def _value(self, control_id: str) -> str | None:
        control = next((c for c in self.controls if c["id"] == control_id), None)
        if control is None:
            return None
        return format_value(control, self.rig.get("controls", {}).get(control_id))

    def _show_vfo(self) -> None:
        """Two lines under the digits, as the terminal's VfoPanel: band, mode, filter and
        width; VFO, split, RIT, step and lock."""
        rig, controls = self.rig, self.rig.get("controls", {})
        first: list[str] = []
        second: list[str] = []

        def chunk(line: list[str], label: str, value: str | None,
                  colour: str = theme.TEXT) -> None:
            if value is not None:
                line.append(span(f"{label} ", theme.DIM) + span(value, colour, True))

        chunk(first, _("Band"), rig.get("band") or "-")
        mode = (rig.get("mode") or "-") + ("-D" if controls.get("data_mode") else "")
        chunk(first, _("Mode"), mode, theme.GREEN)
        chunk(first, _("Filter"), self._value("filter"))
        chunk(first, _("Width"), self._value("filter_width"))
        chunk(second, "VFO", self._value("vfo"), theme.CYAN)
        if controls.get("split"):
            second.append(badge("SPLIT", "#000000", theme.YELLOW))
        if controls.get("rit"):
            chunk(second, "RIT", self._value("rit_offset"), theme.YELLOW)
        chunk(second, _("Step"), f"{self.step:,} Hz".replace(",", "."))
        if controls.get("dial_lock"):
            second.append(badge("LOCK", "#ffffff", theme.RED))
        gap = "&nbsp;" * 4
        self.vfo_label.setText(gap.join(first) + "<br>" + gap.join(second))

    def _show_meters(self) -> None:
        values = self.rig.get("meters", {})
        transmitting = self.rig.get("transmitting")
        for meter in self.meter_defs:
            widget = self.meters[meter["id"]]
            shown = not meter["tx_only"] or transmitting or meter["id"] in ("po", "swr")
            widget.setVisible(bool(shown))
            value = values.get(meter["id"])
            if meter["id"] == "s":
                smeter(widget, value if value is not None else self.rig.get("smeter"))
                continue
            if value is None:
                widget.set(None, "–")
                continue
            share = min(1.0, value / meter["maximum"]) if meter["maximum"] else 0.0
            hot = meter["id"] == "swr" and value >= 2 or meter["id"] == "alc" and value > 0.5
            widget.set(share, f"{value:.1f} {meter['unit']}".strip(), hot)

    def _message(self, kind: str, text: str) -> None:
        self.entry_frame.note(text[:200], kind)

    # ---- commands -------------------------------------------------------------------------

    async def _submit(self) -> None:
        line = self.entry.text().strip()
        self.entry.clear()
        if line.lower() in ("/quit", "/q", "/exit"):
            self.action_leave()
        elif line:
            await self._command(line if line.startswith("/") else f"/f {line}")

    async def _command(self, line: str) -> None:
        try:
            await self.link.command(line, app="rig")
        except LinkError as exc:
            self._message("error", str(exc))

    def _entry_focused(self) -> bool:
        return self.focused is self.entry

    def _panel(self) -> PanelModel:
        return self.model

    def _panel_active(self) -> bool:
        return not self._entry_focused()

    def _grid_request(self, what: str, direction: int) -> None:
        if what == "enter":
            self.run_worker(self.action_enter())
        else:
            self.run_worker(self.action_adjust(direction))

    # ---- keys (the terminal view's actions) ------------------------------------------------

    async def action_signal(self, direction: int) -> None:
        if self._entry_focused():
            return
        dial = self.rig.get("frequency")
        signals = self.scope.signals()
        if dial is None or not signals:
            self._message("info", _("no signals on the scope"))
            return
        target = signal_beside(dial, signals, direction)
        if target is None:
            self._message("info", _("no more signals that way"))
            return
        await self._command(f"/f {target}")

    async def action_tune(self, direction: int) -> None:
        freq = self.rig.get("frequency")
        if freq:
            await self._command(f"/f {tune_target(freq, self.step, direction)}")

    def action_step(self) -> None:
        self.step = next_step(self.step)
        self._show_vfo()

    async def action_mode(self) -> None:
        await self._command(f"/mode {next_mode(self.rig.get('mode'))}")

    async def action_band(self, direction: int) -> None:
        band = band_beside(self.rig.get("frequency"), direction)
        if band is not None:
            await self._command(f"/b {band}")

    async def action_cycle(self, control_id: str) -> None:
        control = next((c for c in self.controls if c["id"] == control_id), None)
        if control is not None:
            current = self.rig.get("controls", {}).get(control_id)
            await self._set(control_id, next_choice(control, current))

    def action_control(self, step: int) -> None:
        if self._entry_focused() or self.model.editing:
            return
        self.model.move(step)
        self._message("info", self.model.help_line())

    def action_group(self, step: int) -> None:
        if self._entry_focused() or self.model.editing:
            return
        self.model.move_group(step)
        self._message("info", self.model.help_line())

    def action_contrast(self, direction: int) -> None:
        self.scope.floor = max(0.0, min(0.8, self.scope.floor + 0.05 * direction))
        self.scope.recolour()
        self._message("info", _("waterfall floor {floor:.0f} %").format(
            floor=self.scope.floor * 100))

    def action_command_line(self) -> None:
        self.entry.setFocus()
        if not self.entry.text():
            self.entry.setText("/")
            self.entry.setCursorPosition(1)

    async def action_escape(self) -> None:
        if self._entry_focused():
            self.entry.clear()
            self.grid.setFocus()
            return
        if self.model.editing:
            await self._cancel_edit()
            return
        self.action_leave()

    def action_leave(self) -> None:
        self.run_worker(self._leave())

    async def _leave(self) -> None:
        if self.owns_link:
            await self.link.close()
        self.exit(0)

