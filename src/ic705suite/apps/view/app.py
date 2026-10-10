"""Rig view: the radio's front panel in the terminal (the start of our own wfview).

Frequency in large digits, band, mode, S-meter and power, tuning from the keyboard and
the rig commands (/f /b /mode /power /filter /raw). Works on any rig the suite knows.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Digits, Input, Label, Static

from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.rig.base import smeter_text
from ic705suite.station.links import Link, LinkError
from ic705suite.ui.rigline import RigStatusLine, smeter_bar
from ic705suite.ui.widgets import clock_footer, css

STEPS = (10, 100, 1_000, 10_000, 100_000)
CYCLE_MODES = ("CW", "USB", "LSB", "FM", "AM", "DV")
HINT = N_("PgUp/PgDn Tune · F2 Step · F3 Mode · F4-F5 Band · Esc Back")
SHORT_HINT = N_("PgUp/PgDn Tune · Esc Back")


def digits_text(hz: int | None) -> str:
    if not hz:
        return "---.---.--"
    return f"{hz // 1_000_000}.{hz // 1000 % 1000:03d}.{hz % 1000 // 10:02d}"


class MeterPanel(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rig: dict = {}
        self.step = 100

    def render(self) -> Text:
        rig, text = self.rig, Text()
        band = rig.get("band") or "-"
        text.append(f"{_('Band')} ", style="dim")
        text.append(f"{band:<6}", style="bold white")
        text.append(f"{_('Mode')} ", style="dim")
        text.append(f"{rig.get('mode') or '-':<7}", style="bold green")
        text.append(f"{_('Step')} ", style="dim")
        text.append(f"{self.step:,} Hz".replace(",", "."), style="bold white")
        text.append("\n\n")
        smeter = rig.get("smeter", 0)
        filled, empty = smeter_bar(smeter, 30)
        text.append("S  ", style="dim")
        text.append(filled, style="green")
        text.append(empty, style="dim")
        text.append(f"  {smeter_text(smeter)}\n", style="bold white")
        text.append("   1 3 5 7 9 +20 +40 +60\n", style="dim")
        if rig.get("power") is not None:
            watts = rig["power"] * rig.get("max_power_w", 10) / 100
            text.append(f"\n{_('Power')} ", style="dim")
            text.append(f"{rig['power']}% ({watts:.1f} W)", style="white")
        if rig.get("transmitting"):
            text.append("   TX", style="bold white on red")
        return text


class ViewApp(App[int]):
    CSS = css("ic705suite.apps.view")
    TITLE = "ic705suite View"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("pageup", "tune(1)", show=False, priority=True),
        Binding("pagedown", "tune(-1)", show=False, priority=True),
        Binding("f2", "step", show=False, priority=True),
        Binding("f3", "mode", show=False, priority=True),
        Binding("f4", "band(-1)", show=False, priority=True),
        Binding("f5", "band(1)", show=False, priority=True),
        Binding("escape", "leave", show=False),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link, owns_link: bool = False) -> None:
        super().__init__()
        self.link = link
        self.owns_link = owns_link
        self.rig: dict = {}
        self.step = 100

    def compose(self) -> ComposeResult:
        yield RigStatusLine(self.link.description, id="statusline")
        with Vertical(id="panel", classes="frame"):
            yield Digits(digits_text(None), id="frequency")
            yield MeterPanel(id="meters")
        with Vertical(id="entry"):
            with Horizontal(id="entry-row"):
                yield Label(_("COMMAND"), id="entry-label")
                yield Input(id="entry-input", placeholder=_("/f 7.030 · /b 40 · /mode USB · /power 50"))
        yield Static(id="stats")

    def on_mount(self) -> None:
        self.query_one("#panel").border_title = _("Rig")
        self.query_one("#entry").border_title = _("Rig commands")
        self.query_one("#entry-input", Input).focus()
        self.set_interval(1.0, self._footer)
        self._footer()
        self.link.attach(self._on_event)
        self.run_worker(self._connect(), exclusive=True)

    async def _connect(self) -> None:
        try:
            await self.link.connect()
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._message("error", _("cannot connect: {error}").format(error=exc))

    def _footer(self) -> None:
        stats = self.query_one("#stats", Static)
        stats.update(clock_footer(stats.size.width, [], [_(HINT), _(SHORT_HINT)]))

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "rig":
            self.rig = event
            status = self.query_one("#statusline", RigStatusLine)
            status.rig = event
            status.refresh()
            self.query_one("#frequency", Digits).update(digits_text(event.get("frequency")))
            meters = self.query_one("#meters", MeterPanel)
            meters.rig, meters.step = event, self.step
            meters.refresh()
            panel = self.query_one("#panel")
            panel.border_title = f"{event.get('model_name', _('Rig'))} · {event.get('radio', '')}"
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _message(self, kind: str, text: str) -> None:
        self.query_one("#entry").border_subtitle = Text(
            text[:200], style="bold red" if kind == "error" else "cyan")

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

    async def action_tune(self, direction: int) -> None:
        freq = self.rig.get("frequency")
        if freq:
            # Snap to the step grid: up to the next multiple, down to the previous one.
            target = (freq // self.step + 1) * self.step if direction > 0 \
                else (freq - 1) // self.step * self.step
            await self._command(f"/f {target}")

    def action_step(self) -> None:
        self.step = STEPS[(STEPS.index(self.step) + 1) % len(STEPS)]
        meters = self.query_one("#meters", MeterPanel)
        meters.step = self.step
        meters.refresh()

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

    def action_leave(self) -> None:
        self.run_worker(self._leave(), exclusive=True, group="leave")

    async def _leave(self) -> None:
        if self.owns_link:
            await self.link.close()
        else:
            self.link.detach()
        self.exit(0)
