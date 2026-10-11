"""The dashboard: the suite's first screen, from which every application is opened.

Left, the applications (with what this rig can run) and a quick logbook entry with today's
contacts; right, the rig at a glance with a small band scope and its basic settings, and the
station; below, the messages of the core. Tab goes from one window to the next. It returns the id of the application to
open, or "quit".
"""

from __future__ import annotations

import base64

import numpy as np
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Static

from ic705suite import __version__
from ic705suite.apps import APPS, BY_ID, catalog
from ic705suite.config import Config
from ic705suite.dashboard.quicklog import QuickLog
from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.rig.base import smeter_text
from ic705suite.rig.controls import CHOICE, LEVEL
from ic705suite.station.links import Link, LinkError, LocalLink
from ic705suite.ui.channels import ChannelSaving
from ic705suite.ui.controlpanel import ControlPanel
from ic705suite.ui.panelkeys import PanelKeys
from ic705suite.ui.rigkeys import tune_target
from ic705suite.ui.rigline import RigStatusLine, smeter_bar
from ic705suite.ui.waterfall import Waterfall
from ic705suite.ui.widgets import clock_footer, css

HINT = N_("Tab Window · ↑↓ Enter or F2-F7 Open · PgUp/PgDn Tune · F9 Reconnect · Ctrl+Q Quit")
SHORT_HINT = N_("Tab Window · ↑↓ Enter Open · Ctrl+Q Quit")
MAX_MESSAGES = 200
SCOPE_KEEPALIVE = 5.0
TUNE_STEP = 100

# The basic settings on the dashboard, in three columns: the rig's own controls (by id) and
# frequency, band and mode, which the dashboard sets with rig commands.
DASH_GROUPS = (("vfo", N_("VFO")), ("rx", N_("Receive")), ("front", N_("Front end")))
DASH_CONTROLS = {"power": "vfo", "af": "rx", "rf": "rx", "sql": "rx", "agc": "rx",
                 "preamp": "front", "att": "front", "filter": "front"}
DEFAULT_MODES = ("LSB", "USB", "AM", "CW", "RTTY", "FM", "WFM", "CW-R", "RTTY-R", "DV")


def vfo_controls(modes: list[str] | None) -> list[dict]:
    """Frequency (MHz), band and mode as front-panel options."""
    def option(**fields) -> dict:
        base = {"group": "vfo", "choices": [], "minimum": 0, "maximum": 0, "step": 1,
                "unit": "", "help": ""}
        return {**base, **fields}

    return [
        option(id="frequency", label=N_("FREQ"), kind=LEVEL, minimum=0.03, maximum=470.0,
               step=TUNE_STEP / 1e6, unit="MHz", decimals=4, bar=False,
               help=N_("Frequency: ←→ ↑↓ by 100 Hz (Shift: 1 kHz), or type it "
                       "(7.030 MHz, 7030.5 kHz)")),
        option(id="band", label=N_("BAND"), kind=CHOICE,
               choices=[[b.name, b.name] for b in bands.BANDS], help=N_("Band")),
        option(id="mode", label=N_("MODE"), kind=CHOICE,
               choices=[[m, m] for m in (modes or DEFAULT_MODES)], help=N_("Mode")),
    ]


def dashboard_controls(rig: dict) -> list[dict]:
    controls = vfo_controls(rig.get("modes"))
    for control in rig.get("controls", []):
        if control["id"] in DASH_CONTROLS:
            controls.append({**control, "group": DASH_CONTROLS[control["id"]]})
    return controls

FEATURE_NAMES = {"cw_keyer": N_("CW keyer"), "audio": N_("audio"), "dstar": "D-STAR",
                 "gps": "GPS", "scope": N_("scope"), "swr_meter": N_("SWR meter"),
                 "data_mode": N_("data mode")}


class RigPanel(Static):
    """The rig at a glance: model, connection, frequency, band, mode, meters."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rig: dict = {}
        self.welcome: dict = {}
        self.problem = ""

    def render(self) -> Text:
        rig, text = self.rig, Text()
        if not rig and not self.welcome:
            text.append(self.problem or _("connecting to the core..."), style="dim")
            return text
        info = self.welcome.get("rig", {})
        name = f"{info.get('manufacturer', '')} {info.get('name', rig.get('model_name', ''))}".strip()
        text.append(name or "-", style="bold cyan")
        if rig.get("radio") and rig["radio"] != info.get("name"):
            text.append(f"  ({rig['radio']})", style="dim")
        text.append("\n")
        state = (_("connected") if rig.get("connected") else _("not connected"))
        text.append(f"{_('Link')}: ", style="dim")
        text.append(f"{rig.get('transport', '-')} · ", style="white")
        text.append(state + "\n", style="bold green" if rig.get("connected") else "bold red")
        freq = bands.format_frequency(rig.get("frequency")) if rig.get("frequency") else "-"
        text.append(f"  {freq}", style="bold yellow")
        text.append(" MHz", style="dim")
        if rig.get("band"):
            text.append(f"   {rig['band']}", style="bold white")
        text.append(f"   {rig.get('mode') or '-'}\n", style="bold green")
        smeter = rig.get("smeter", 0)
        filled, empty = smeter_bar(smeter, 20)
        text.append(f"  {smeter_text(smeter):<7}", style="bold white")
        text.append(filled, style="green")
        text.append(empty + "\n", style="dim")
        if rig.get("power") is not None:
            watts = rig["power"] * rig.get("max_power_w", 10) / 100
            text.append(f"  {_('Power')} ", style="dim")
            text.append(f"{rig['power']}% ({watts:.1f} W)\n", style="white")
        text.append("  ")
        if rig.get("tx_enabled"):
            text.append(_("Transmission enabled"), style="bold red")
        else:
            text.append(_("Receive only (start with --tx to transmit)"), style="bold yellow")
        return text


class StationPanel(Static):
    def __init__(self, config: Config, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.welcome: dict = {}

    def render(self) -> Text:
        from ic705suite import db

        text = Text(no_wrap=True, overflow="ellipsis")
        call = self.welcome.get("callsign") or self.config.callsign or "-"
        text.append(f"{_('Callsign')}: ", style="dim")
        text.append(call + "\n", style="bold white")
        text.append(f"{_('Database')}: ", style="dim")
        text.append(db.describe(self.config.database_url or None) + "\n", style="white")
        features = [_(FEATURE_NAMES.get(f, f)) for f in self.welcome.get("rig", {}).get("features", [])]
        if features:
            text.append(f"{_('Can do')}: ", style="dim")
            text.append(", ".join(features) + "\n", style="white")
        port = self.welcome.get("swr_port")
        if port:
            from ic705suite.apps.swr.web import lan_address

            text.append(f"{_('Antenna page')}: ", style="dim")
            text.append(f"http://{lan_address()}:{port}\n", style="white")
        text.append(f"ic705suite {__version__}", style="dim")
        return text


class Footer(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.messages = 0

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Text:
        return clock_footer(self.size.width, [], [_(HINT), _(SHORT_HINT)])


class DashboardApp(PanelKeys, ChannelSaving, App[str]):
    CSS = css("ic705suite.dashboard")
    TITLE = "ic705suite"
    ENABLE_COMMAND_PALETTE = False

    chosen: str | None = None     # the application last opened, chosen again on return

    BINDINGS = [
        *(Binding(app.key.lower(), f"open('{app.id}')", show=False, priority=True) for app in APPS),
        # Tab goes between the windows (the screen's own focus keys). On the applications ↑↓
        # choose and Enter or Space opens (or the function key directly); on the settings the
        # keys of the rig view's front panel. Not priority bindings: in a dialog, Enter and
        # Space belong to its buttons.
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
        Binding("enter", "press", show=False),
        Binding("space", "press", show=False),
        Binding("escape", "escape", show=False),
        Binding("pageup", "tune(1)", show=False, priority=True),
        Binding("pagedown", "tune(-1)", show=False, priority=True),
        Binding("f9", "reconnect", show=False, priority=True),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link | None, config: Config, problem: str = "",
                 messages: list[tuple[str, str]] | None = None) -> None:
        super().__init__()
        self.link = link
        self.config = config
        self.problem = problem   # why there is no core (no radio configured...)
        self.messages = messages if messages is not None else []
        self.apps = catalog(None)
        self.rig: dict = {}
        self._scope_asked = False

    def compose(self) -> ComposeResult:
        yield RigStatusLine(self.link.description if self.link else _("no core"), id="statusline")
        with Horizontal(id="top"):
            with Vertical(id="left"):
                with Vertical(id="apps-frame", classes="frame"):
                    yield DataTable(id="apps", cursor_type="row", zebra_stripes=False)
                yield QuickLog(demo=self.config.connection == "sim", id="quicklog",
                               classes="frame")
            with Vertical(id="side"):
                yield RigPanel(id="rig", classes="frame")
                yield Waterfall(id="scope", classes="frame")
                yield ControlPanel(groups=DASH_GROUPS, bars=False, id="controls", classes="frame")
                yield StationPanel(self.config, id="station", classes="frame")
        yield Static(id="log", classes="frame")
        yield Footer(id="stats")

    def on_mount(self) -> None:
        self.query_one("#apps-frame").border_title = _("Applications")
        self.query_one("#rig").border_title = _("Rig")
        self.query_one("#station").border_title = _("Station")
        self.query_one("#scope").border_title = _("Scope")
        self.query_one("#controls").border_title = _("Settings")
        scope = self.query_one("#scope", Waterfall)
        scope.can_focus = False
        scope.spectrum_rows = 2
        self.set_interval(SCOPE_KEEPALIVE, self._keep_scope)
        self.query_one("#log").border_title = _("Messages")
        table = self.query_one("#apps", DataTable)
        table.add_columns(_("Key"), _("Application"), _("What it does"))
        self._fill_apps()
        table.focus()
        panel = self.query_one("#rig", RigPanel)
        panel.problem = self.problem
        if self.link is not None:
            # The link replays the core's messages: start from them, not from a copy.
            self.messages.clear()
            self.link.attach(self._on_event)
        elif self.problem:
            self._message("error", self.problem)
        self._show_messages()

    # ---- events -------------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "rig":
            self.rig = event
            status = self.query_one("#statusline", RigStatusLine)
            status.rig = event
            status.refresh()
            panel = self.query_one("#rig", RigPanel)
            panel.rig = event
            panel.refresh(layout=True)        # its height follows what there is to say
            self._apply_rig(event)
        elif kind == "scope":
            self._apply_scope(event)
        elif kind == "welcome":
            self.apps = event.get("apps") or self.apps
            self.query_one("#rig", RigPanel).welcome = event
            self.query_one("#station", StationPanel).welcome = event
            self.query_one("#station", StationPanel).refresh(layout=True)
            self._panel().set_controls(dashboard_controls(event.get("rig", {})))
            self._fill_apps()
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _apply_rig(self, event: dict) -> None:
        from ic705suite.apps.view.app import passband

        if event.get("connected") and not self._scope_asked:
            self._scope_asked = True          # the radio is here: start the scope now
            self.run_worker(self._keep_scope())
        elif not event.get("connected"):
            self._scope_asked = False

        values = dict(event.get("controls", {}))
        frequency = event.get("frequency")
        values["frequency"] = frequency / 1e6 if frequency else None
        values["band"] = event.get("band")
        values["mode"] = event.get("mode")
        self._panel().set_values(values)
        scope = self.query_one("#scope", Waterfall)
        scope.view.dial = frequency
        scope.view.passband = passband(event.get("mode"), frequency, values.get("filter_width"))

    def _apply_scope(self, event: dict) -> None:
        data = np.frombuffer(base64.b64decode(event["data"]), dtype=np.uint8) / 255.0
        scope = self.query_one("#scope", Waterfall)
        scope.add_frame(data, int(event["start"]), int(event["end"]))
        span = (event["end"] - event["start"]) / 2000
        scope.border_title = f"{_('Scope')} ±{span:g} kHz"

    async def _keep_scope(self) -> None:
        """The scope streams only while someone watches: ask again every few seconds."""
        if self.link is None or not self.rig.get("connected"):
            return
        try:
            await self.link.call("scope.watch", seconds=SCOPE_KEEPALIVE * 3)
        except (LinkError, OSError):
            pass

    # ---- the settings (front panel keys) ----------------------------------------------------

    def _panel(self) -> ControlPanel:
        return self.query_one("#controls", ControlPanel)

    def _panel_active(self) -> bool:
        return self.focused is self._panel() and len(self.screen_stack) == 1

    def _note(self, kind: str, text: str) -> None:
        """Help while moving goes on the settings' edge, not into the messages."""
        self._panel().border_subtitle = Text(text[:120], style="bold red" if kind == "error"
                                             else "cyan")

    def _typed_value(self, panel: ControlPanel):
        if panel.selected == "frequency":
            return bands.parse_frequency(panel.buffer) / 1e6
        return panel.typed_value()

    async def _set_special(self, control_id: str, value) -> bool:
        if control_id == "frequency":
            line = f"/f {round(value * 1e6 / 10) * 10}"
        elif control_id == "band":
            line = f"/b {value}"
        elif control_id == "mode":
            line = f"/mode {value}"
        else:
            return False
        if self.link is None:
            self._message("error", _("no radio"))
            return True
        # Kept at once, so a second key press starts from it, not from the radio's last report.
        panel = self._panel()
        panel.values[control_id] = value
        panel.refresh()
        try:
            await self.link.command(line, app="rig")
        except LinkError as exc:
            self._message("error", str(exc))
        return True

    def _message(self, kind: str, text: str) -> None:
        if self.messages and self.messages[-1] == (kind, text):
            return
        self.messages.append((kind, text))
        del self.messages[:-MAX_MESSAGES]
        if self.is_mounted:
            self._show_messages()

    def _show_messages(self) -> None:
        log = self.query_one("#log", Static)
        rows = max(1, log.size.height - 2) if log.size.height else 6
        text = Text(no_wrap=True, overflow="ellipsis")
        for i, (kind, line) in enumerate(self.messages[-rows:]):
            if i:
                text.append("\n")
            text.append(line, style="bold red" if kind == "error" else "cyan")
        log.update(text)

    def _fill_apps(self) -> None:
        table = self.query_one("#apps", DataTable)
        row = table.cursor_row if table.row_count else 0
        table.clear()
        model = self.query_one("#rig", RigPanel).welcome.get("rig", {}).get("name", "IC-705")
        for app in self.apps:
            usable = self._usable(app)
            style = "bold white" if usable else "dim"
            name = Text(_(app["name"]).format(model=model), style=style)
            desc = Text(_(app["description"]), style="white" if usable else "dim")
            if not app["ready"]:
                desc = Text(_("coming soon") + " · ", style="italic yellow") + desc
            elif not app["available"]:
                desc = Text(_("this rig cannot") + " · ", style="italic") + desc
            elif self._lacks_core(app):
                desc = Text(_("needs the radio") + " · ", style="italic") + desc
            table.add_row(Text(app["key"], style="bold cyan"), name, desc)
        ids = [a["id"] for a in self.apps]
        if DashboardApp.chosen in ids:      # back from an application: still on it
            row = ids.index(DashboardApp.chosen)
            DashboardApp.chosen = None
        if self.apps:
            table.move_cursor(row=min(row, len(self.apps) - 1))

    def _usable(self, app: dict) -> bool:
        return app["ready"] and app["available"] and not self._lacks_core(app)

    def _lacks_core(self, app: dict) -> bool:
        """It runs on a core and there is none here (packet may use another machine's)."""
        if app["id"] == "packet" and self.config.packet_core:
            return False
        return app["needs_rig"] and self.link is None

    # ---- actions ------------------------------------------------------------------------

    def action_open_chosen(self) -> None:
        table = self.query_one("#apps", DataTable)
        if table.row_count:
            self.action_open(self.apps[table.cursor_row]["id"])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open_chosen()          # Enter on the applications

    async def action_press(self) -> None:
        """Enter or Space: the settings edit; on the applications, open the chosen one."""
        if self._panel_active():
            await self.action_enter()
        elif self.focused is self.query_one("#apps", DataTable):
            self.action_open_chosen()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "qso-input" or not event.value.strip():
            return
        if await self.query_one(QuickLog).submit(event.value, self.rig):
            event.input.value = ""

    async def action_escape(self) -> None:
        panel = self._panel()
        if isinstance(self.focused, Input) and self.focused.id == "qso-input":
            if self.focused.value:
                self.focused.value = ""            # first Esc clears the line
            else:
                self.query_one("#apps", DataTable).focus()
        elif self._panel_active() and panel.editing:
            await self._cancel_edit()
        elif self._panel_active():
            self.query_one("#apps", DataTable).focus()

    async def action_tune(self, direction: int) -> None:
        if len(self.screen_stack) > 1:
            return
        freq = self.rig.get("frequency")
        if freq and self.link is not None:
            await self._set_special("frequency", tune_target(freq, TUNE_STEP, direction) / 1e6)

    def action_open(self, app_id: str) -> None:
        app = next((a for a in self.apps if a["id"] == app_id), None)
        if app is None:
            return
        if not self._usable(app):
            name = _(BY_ID[app_id].name).format(model=self.rig.get("model_name", "IC-705"))
            reason = (_("coming soon") if not app["ready"] else _("this rig cannot")
                      if not app["available"] else _("needs the radio"))
            self._message("error", f"{name}: {reason}")
            return
        if self.link is not None:
            self.link.detach()
        DashboardApp.chosen = app_id
        self.exit(app_id)

    def action_reconnect(self) -> None:
        if self.link is not None and not self.link.connected:
            from ic705suite.launcher import start_link

            start_link(self.link)

    def action_leave(self) -> None:
        self.run_worker(self._leave(), exclusive=True, group="leave")

    async def _leave(self) -> None:
        # A local core ends with the suite: its channels are lost unless saved now. A remote
        # core keeps running (and archives them itself when it stops).
        if isinstance(self.link, LocalLink) and self.link.connected \
                and not await self.offer_save_channels():
            return
        if self.link is not None:
            self.link.detach()
        self.exit("quit")
