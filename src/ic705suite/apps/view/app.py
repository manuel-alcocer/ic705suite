"""Rig view: the radio's front panel in the terminal (our own wfview, for the console).

Top: the frequency in large digits with the VFO, mode, filter and RIT, and the meters.
Middle: the band scope as a spectrum over a waterfall; F7/F8 jump between its signals. Bottom: every
front-panel control the rig offers, as knobs and switches, and the rig command line.
Works on any rig the suite knows: what is shown comes from the rig's own description.
"""

from __future__ import annotations

import base64

import numpy as np
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, Static

from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.rig.base import smeter_text
from ic705suite.station.links import Link, LinkError
from ic705suite.ui import glyphs
from ic705suite.ui.bigdigits import BigDigits
from ic705suite.ui.controlpanel import ControlPanel, format_value
from ic705suite.ui.rigline import RigStatusLine
from ic705suite.ui.waterfall import Waterfall
from ic705suite.ui.widgets import clock_footer, css

STEPS = (10, 100, 1_000, 10_000, 100_000)
CYCLE_MODES = ("CW", "USB", "LSB", "FM", "AM", "DV")
SCOPE_KEEPALIVE = 5.0
HINT = N_("PgUp/PgDn Tune · F7-F8 Signal · ↑↓ ←→ Move · Enter Edit · F2 Step · F3 Mode · "
          "F4-F5 Band · F6 Filter · [ ] Contrast · / Command · Esc Back")
SHORT_HINT = N_("PgUp/PgDn Tune · ↑↓ ←→ Move · Enter Edit · Esc Back")
METER_CELLS = 16


def digits_text(hz: int | None) -> str:
    if not hz:
        return "---.---.--"
    return f"{hz // 1_000_000}.{hz // 1000 % 1000:03d}.{hz % 1000 // 10:02d}"


def passband(mode: str | None, dial: int | None, width: float | None) -> tuple[int, int] | None:
    """RF range the receiver hears, for the shading on the scope."""
    if dial is None or not width:
        return None
    width = int(width)
    if mode in ("USB", "RTTY-R") or (mode or "").startswith("USB"):
        return dial, dial + width
    if mode in ("LSB", "RTTY"):
        return dial - width, dial
    return dial - width // 2, dial + width // 2


class VfoPanel(Static):
    """Mode, filter, VFO, split, RIT, step under the digits."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rig: dict = {}
        self.controls: list[dict] = []
        self.step = 100

    def _value(self, control_id: str) -> str | None:
        control = next((c for c in self.controls if c["id"] == control_id), None)
        if control is None:
            return None
        return format_value(control, self.rig.get("controls", {}).get(control_id))

    def render(self) -> Text:
        rig, controls, text = self.rig, self.rig.get("controls", {}), Text()

        def chunk(label: str, value: str | None, style: str = "bold white") -> None:
            if value is None:
                return
            text.append(f"{label} ", style="dim")
            text.append(f"{value}   ", style=style)

        chunk(_("Band"), rig.get("band") or "-")
        mode = rig.get("mode") or "-"
        if controls.get("data_mode"):
            mode += "-D"
        chunk(_("Mode"), mode, "bold green")
        chunk(_("Filter"), self._value("filter"))
        chunk(_("Width"), self._value("filter_width"))
        text.append("\n")
        chunk("VFO", self._value("vfo"), "bold cyan")
        if controls.get("split"):
            text.append("SPLIT   ", style="bold black on yellow")
        if controls.get("rit"):
            chunk("RIT", self._value("rit_offset"), "bold yellow")
        chunk(_("Step"), f"{self.step:,} Hz".replace(",", "."))
        if controls.get("dial_lock"):
            text.append("LOCK", style="bold white on red")
        return text


class MeterPanel(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rig: dict = {}
        self.meters: list[dict] = []

    def render(self) -> Text:
        text = Text(no_wrap=True)
        values = self.rig.get("meters", {})
        transmitting = self.rig.get("transmitting")
        meters = self.meters or [{"id": "s", "label": "S", "unit": "", "maximum": 241,
                                  "tx_only": False}]
        first = True
        for meter in meters:
            if meter["tx_only"] and not transmitting and meter["id"] not in ("po", "swr"):
                continue
            value = values.get(meter["id"])
            if not first:
                text.append("\n")
            first = False
            text.append(f"{_(meter['label']):<5}", style="dim")
            if value is None:
                text.append("·" * METER_CELLS, style="dim")
                text.append("  –", style="dim")
                continue
            if meter["id"] == "s":
                cells = min(METER_CELLS, round(value / 120 * 9) if value <= 120
                            else 9 + round((value - 120) / 121 * (METER_CELLS - 9)))
                label = smeter_text(int(value))
            else:
                cells = round(min(1.0, value / meter["maximum"]) * METER_CELLS) \
                    if meter["maximum"] else 0
                label = f"{value:.1f} {meter['unit']}".strip()
            hot = meter["id"] == "swr" and value >= 2 or meter["id"] == "alc" and value > 0.5
            text.append(glyphs.BAR * cells, style="red" if hot else "green")
            text.append("·" * (METER_CELLS - cells), style="dim")
            text.append(f"  {label}", style="bold white")
        return text


class ViewApp(App[int]):
    CSS = css("ic705suite.apps.view")
    TITLE = "ic705suite View"
    ENABLE_COMMAND_PALETTE = False

    # Keyboard only. On the front panel the cursor keys move between the options; Space or
    # Enter edits one (cursor keys change it, or type the value), Enter again goes back to
    # moving, Esc cancels the edit. Function keys work in both modes; "/" opens the
    # command line.
    BINDINGS = [
        Binding("pageup", "tune(1)", show=False, priority=True),
        Binding("pagedown", "tune(-1)", show=False, priority=True),
        Binding("f2", "step", show=False, priority=True),
        Binding("f3", "mode", show=False, priority=True),
        Binding("f4", "band(-1)", show=False, priority=True),
        Binding("f5", "band(1)", show=False, priority=True),
        Binding("f6", "cycle('filter')", show=False, priority=True),
        Binding("f7", "signal(-1)", show=False, priority=True),
        Binding("f8", "signal(1)", show=False, priority=True),
        Binding("f9", "group(-1)", show=False, priority=True),
        Binding("f10", "group(1)", show=False, priority=True),
        Binding("tab", "control(1)", show=False, priority=True),
        Binding("shift+tab", "control(-1)", show=False, priority=True),
        Binding("up", "arrow(0, -1)", show=False),
        Binding("down", "arrow(0, 1)", show=False),
        Binding("left", "arrow(-1, 0)", show=False),
        Binding("right", "arrow(1, 0)", show=False),
        Binding("shift+up", "arrow(0, -1, True)", show=False),
        Binding("shift+down", "arrow(0, 1, True)", show=False),
        Binding("shift+left", "arrow(-1, 0, True)", show=False),
        Binding("shift+right", "arrow(1, 0, True)", show=False),
        Binding("minus", "adjust(-1)", show=False),
        Binding("plus", "adjust(1)", show=False),
        Binding("less_than_sign", "adjust(-1, True)", show=False),
        Binding("greater_than_sign", "adjust(1, True)", show=False),
        Binding("enter", "enter", show=False),
        Binding("space", "enter", show=False),
        Binding("left_square_bracket", "contrast(-1)", show=False),
        Binding("right_square_bracket", "contrast(1)", show=False),
        Binding("slash", "command_line", show=False),
        Binding("escape", "escape", show=False, priority=True),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link, owns_link: bool = False) -> None:
        super().__init__()
        self.link = link
        self.owns_link = owns_link
        self.rig: dict = {}
        self.step = 100
        self.controls: list[dict] = []

    def compose(self) -> ComposeResult:
        yield RigStatusLine(self.link.description, id="statusline")
        with Horizontal(id="top"):
            with Vertical(id="vfo-frame", classes="frame"):
                yield BigDigits(digits_text(None), id="frequency")
                yield VfoPanel(id="vfo")
            yield MeterPanel(id="meters", classes="frame")
        yield Waterfall(id="scope", classes="frame")
        yield ControlPanel(id="panel", classes="frame")
        with Vertical(id="entry"):
            with Horizontal(id="entry-row"):
                yield Label(_("COMMAND"), id="entry-label")
                yield Input(id="entry-input", select_on_focus=False,
                            placeholder=_("/f 7.030 · /b 40 · /mode USB · /set af 40 · Esc back"))
        yield Static(id="stats")

    def on_mount(self) -> None:
        self.query_one("#vfo-frame").border_title = _("Rig")
        self.query_one("#meters").border_title = _("Meters")
        self.query_one("#scope").border_title = _("Scope")
        self.query_one("#panel").border_title = _("Front panel")
        self.query_one("#entry").border_title = _("Rig commands")
        self.query_one("#panel", ControlPanel).focus()
        self.set_interval(1.0, self._footer)
        self.set_interval(SCOPE_KEEPALIVE, self._keep_scope)
        self._footer()
        self.link.attach(self._on_event)
        self.run_worker(self._connect(), exclusive=True)

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
            self.query_one("#scope", Waterfall).border_subtitle = reply["error"]

    def _footer(self) -> None:
        stats = self.query_one("#stats", Static)
        stats.update(clock_footer(stats.size.width, [], [_(HINT), _(SHORT_HINT)]))

    # ---- events ---------------------------------------------------------------------------

    def _apply_welcome(self, event: dict) -> None:
        rig = event.get("rig", {})
        self.controls = rig.get("controls", [])
        self.query_one("#panel", ControlPanel).set_controls(self.controls)
        self.query_one("#vfo", VfoPanel).controls = self.controls
        self.query_one("#meters", MeterPanel).meters = rig.get("meters", [])

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
        status = self.query_one("#statusline", RigStatusLine)
        status.rig = event
        status.refresh()
        self.query_one("#frequency", BigDigits).update(digits_text(event.get("frequency")))
        vfo = self.query_one("#vfo", VfoPanel)
        vfo.rig, vfo.step = event, self.step
        vfo.refresh()
        meters = self.query_one("#meters", MeterPanel)
        meters.rig = event
        meters.refresh()
        self.query_one("#panel", ControlPanel).set_values(event.get("controls", {}))
        scope = self.query_one("#scope", Waterfall)
        scope.view.dial = event.get("frequency")
        scope.view.passband = passband(event.get("mode"), event.get("frequency"),
                                       event.get("controls", {}).get("filter_width"))
        title = f"{event.get('model_name', '')} · {event.get('radio', '')}"
        self.query_one("#vfo-frame").border_title = title

    def _apply_scope(self, event: dict) -> None:
        data = np.frombuffer(base64.b64decode(event["data"]), dtype=np.uint8) / 255.0
        scope = self.query_one("#scope", Waterfall)
        scope.add_frame(data, int(event["start"]), int(event["end"]))
        span = (event["end"] - event["start"]) / 2000
        mode = _("centre") if event.get("center") else _("fixed")
        scope.border_title = f"{_('Scope')} · {mode} ±{span:g} kHz"
        scope.border_subtitle = _("out of range") if event.get("out_of_range") else ""

    def _message(self, kind: str, text: str) -> None:
        self.query_one("#entry").border_subtitle = Text(
            text[:200], style="bold red" if kind == "error" else "cyan")

    # ---- commands -------------------------------------------------------------------------

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        line = message.value.strip()
        message.input.value = ""
        if line.lower() in ("/quit", "/q", "/exit"):
            self.action_leave()
        elif line:
            await self._command(line if line.startswith("/") else f"/f {line}")

    async def _command(self, line: str) -> None:
        try:
            await self.link.command(line, app="rig")
        except LinkError as exc:
            self._message("error", str(exc))

    async def _set(self, control_id: str, value) -> None:
        try:
            reply = await self.link.call("rig.set", id=control_id, value=value)
        except LinkError as exc:
            self._message("error", str(exc))
            return
        if reply.get("error"):
            self._message("error", reply["error"])
            return
        panel = self.query_one("#panel", ControlPanel)
        panel.values[control_id] = reply.get("value")
        panel.refresh()
        control = panel.control(control_id)
        if control:
            self._message("info", f"{_(control['label'])} = {format_value(control, reply.get('value'))}")

    async def action_signal(self, direction: int) -> None:
        """Jump to the next signal on the scope above (or below) the dial."""
        if self._entry_focused():
            return
        dial = self.rig.get("frequency")
        signals = self.query_one("#scope", Waterfall).signals()
        if dial is None or not signals:
            self._message("info", _("no signals on the scope"))
            return
        guard = 150   # skip the signal already tuned
        ahead = [f for f in signals if f > dial + guard] if direction > 0 \
            else [f for f in reversed(signals) if f < dial - guard]
        if not ahead:
            self._message("info", _("no more signals that way"))
            return
        target = round(ahead[0] / 10) * 10
        await self._command(f"/f {target}")

    def _entry_focused(self) -> bool:
        return isinstance(self.focused, Input)

    async def action_tune(self, direction: int) -> None:
        freq = self.rig.get("frequency")
        if freq:
            # Snap to the step grid: up to the next multiple, down to the previous one.
            target = (freq // self.step + 1) * self.step if direction > 0 \
                else (freq - 1) // self.step * self.step
            await self._command(f"/f {target}")

    def action_step(self) -> None:
        self.step = STEPS[(STEPS.index(self.step) + 1) % len(STEPS)]
        vfo = self.query_one("#vfo", VfoPanel)
        vfo.step = self.step
        vfo.refresh()

    async def action_mode(self) -> None:
        mode = self.rig.get("mode")
        index = CYCLE_MODES.index(mode) + 1 if mode in CYCLE_MODES else 0
        await self._command(f"/mode {CYCLE_MODES[index % len(CYCLE_MODES)]}")

    async def action_band(self, direction: int) -> None:
        freq = self.rig.get("frequency") or 0
        names = [b.name for b in bands.BANDS]
        current = bands.band_for(freq)
        if current is not None:
            index = names.index(current.name) + direction
        else:
            index = next((i for i, b in enumerate(bands.BANDS) if b.start > freq), len(names))
            index = index if direction > 0 else index - 1
        if 0 <= index < len(names):
            await self._command(f"/b {names[index]}")

    async def action_cycle(self, control_id: str) -> None:
        control = next((c for c in self.controls if c["id"] == control_id), None)
        if control is not None:
            current = self.rig.get("controls", {}).get(control_id)
            keys = [k for k, _ in control["choices"]]
            new = keys[(keys.index(current) + 1) % len(keys)] if current in keys else keys[0]
            await self._set(control_id, new)

    def _panel(self) -> ControlPanel:
        return self.query_one("#panel", ControlPanel)

    async def action_arrow(self, dx: int, dy: int, big: bool = False) -> None:
        """Navigation: move between the options. Editing: change the value
        (→ and ↑ up, ← and ↓ down)."""
        if self._entry_focused():
            return
        panel = self._panel()
        if panel.editing:
            await self.action_adjust(1 if dx > 0 or dy < 0 else -1, big)
            return
        if dy:
            panel.move_vertical(dy)
        else:
            panel.move_horizontal(dx)
        self._message("info", panel.help_line())

    async def action_enter(self) -> None:
        if self._entry_focused():
            return
        panel = self._panel()
        if not panel.editing:
            panel.start_edit()
            self._message("info", _("editing {option}: ←→ ↑↓ change it or type the value · "
                                    "Enter done · Esc cancel").format(
                option=_(panel.control(panel.selected)["label"])))
            return
        if panel.buffer.strip():
            try:
                value = panel.typed_value()
            except ValueError:
                self._message("error", _("«{text}» is not a value for {option}").format(
                    text=panel.buffer, option=_(panel.control(panel.selected)["label"])))
                panel.buffer = ""
                panel.refresh()
                return
            await self._set(panel.selected, value)
        panel.end_edit()

    async def _cancel_edit(self) -> None:
        panel = self._panel()
        control_id, original = panel.selected, panel.original
        changed = panel.values.get(control_id) != original
        panel.end_edit()
        if changed and original is not None:
            await self._set(control_id, original)
        self._message("info", _("edit cancelled"))

    def action_control(self, step: int) -> None:
        if self._entry_focused() or self._panel().editing:
            return
        panel = self.query_one("#panel", ControlPanel)
        panel.move(step)
        self._message("info", panel.help_line())

    def action_group(self, step: int) -> None:
        if self._entry_focused() or self._panel().editing:
            return
        panel = self.query_one("#panel", ControlPanel)
        panel.move_group(step)
        self._message("info", panel.help_line())

    async def action_adjust(self, direction: int, big: bool = False) -> None:
        if self._entry_focused():
            return
        change = self.query_one("#panel", ControlPanel).adjusted(direction, big)
        if change is not None:
            await self._set(*change)

    def action_contrast(self, direction: int) -> None:
        scope = self.query_one("#scope", Waterfall)
        scope.floor = max(0.0, min(0.8, scope.floor + 0.05 * direction))
        self._message("info", _("waterfall floor {floor:.0f} %").format(floor=scope.floor * 100))

    def action_command_line(self) -> None:
        field = self.query_one("#entry-input", Input)
        field.focus()
        if not field.value:
            field.value = "/"
            field.cursor_position = 1

    async def action_escape(self) -> None:
        if self._entry_focused():
            self.query_one("#entry-input", Input).value = ""
            self._panel().focus()
            return
        if self._panel().editing:
            await self._cancel_edit()
            return
        self.action_leave()

    def action_leave(self) -> None:
        self.run_worker(self._leave(), exclusive=True, group="leave")

    async def _leave(self) -> None:
        if self.owns_link:
            await self.link.close()
        else:
            self.link.detach()
        self.exit(0)
