"""The dashboard: the suite's first screen, from which every application is opened.

Left, the applications (with what this rig can run); right, the rig at a glance and the
station; below, the messages of the core. It returns the id of the application to open,
or "quit".
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from ic705suite import __version__
from ic705suite.apps import APPS, BY_ID, catalog
from ic705suite.config import Config
from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.rig.base import smeter_text
from ic705suite.station.links import Link, LocalLink
from ic705suite.ui.channels import ChannelSaving
from ic705suite.ui.rigline import RigStatusLine, smeter_bar
from ic705suite.ui.widgets import clock_footer, css

HINT = N_("↑↓ Enter or F2-F7 Open · F9 Reconnect · Ctrl+Q Quit")
SHORT_HINT = N_("↑↓ Enter or F2-F7 Open · Ctrl+Q Quit")
MAX_MESSAGES = 200

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
        text.append("\n")
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
        text.append("\n")
        if rig.get("tx_enabled"):
            text.append(f"  {_('Transmission enabled')}\n", style="bold red")
        else:
            text.append(f"  {_('Receive only (start with --tx to transmit)')}\n", style="bold yellow")
        features = [_(FEATURE_NAMES.get(f, f)) for f in info.get("features", [])]
        if features:
            text.append(f"  {_('Can do')}: ", style="dim")
            text.append(", ".join(features), style="white")
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


class DashboardApp(ChannelSaving, App[str]):
    CSS = css("ic705suite.dashboard")
    TITLE = "ic705suite"
    ENABLE_COMMAND_PALETTE = False

    chosen: str | None = None     # the application last opened, chosen again on return

    BINDINGS = [
        *(Binding(app.key.lower(), f"open('{app.id}')", show=False, priority=True) for app in APPS),
        # ↑↓ choose an application, Enter or Space opens it (or its function key directly).
        # Not priority bindings: in a dialog, Enter and Space belong to its buttons.
        Binding("up", "choose(-1)", show=False),
        Binding("down", "choose(1)", show=False),
        Binding("enter", "open_chosen", show=False),
        Binding("space", "open_chosen", show=False),
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

    def compose(self) -> ComposeResult:
        yield RigStatusLine(self.link.description if self.link else _("no core"), id="statusline")
        with Horizontal(id="top"):
            with Vertical(id="apps-frame", classes="frame"):
                yield DataTable(id="apps", cursor_type="row", zebra_stripes=False)
            with Vertical(id="side"):
                yield RigPanel(id="rig", classes="frame")
                yield StationPanel(self.config, id="station", classes="frame")
        yield Static(id="log", classes="frame")
        yield Footer(id="stats")

    def on_mount(self) -> None:
        self.query_one("#apps-frame").border_title = _("Applications")
        self.query_one("#rig").border_title = _("Rig")
        self.query_one("#station").border_title = _("Station")
        self.query_one("#log").border_title = _("Messages")
        table = self.query_one("#apps", DataTable)
        table.add_columns(_("Key"), _("Application"), _("What it does"))
        table.can_focus = False   # the keys are the dashboard's (↑↓ Enter, F2-F7)
        self._fill_apps()
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
            panel.refresh()
        elif kind == "welcome":
            self.apps = event.get("apps") or self.apps
            self.query_one("#rig", RigPanel).welcome = event
            self.query_one("#station", StationPanel).welcome = event
            self.query_one("#station", StationPanel).refresh()
            self._fill_apps()
        elif kind in ("info", "error"):
            self._message(kind, render(event))

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
            elif app["needs_rig"] and self.link is None:
                desc = Text(_("needs the radio") + " · ", style="italic") + desc
            table.add_row(Text(app["key"], style="bold cyan"), name, desc)
        ids = [a["id"] for a in self.apps]
        if DashboardApp.chosen in ids:      # back from an application: still on it
            row = ids.index(DashboardApp.chosen)
            DashboardApp.chosen = None
        if self.apps:
            table.move_cursor(row=min(row, len(self.apps) - 1))

    def _usable(self, app: dict) -> bool:
        return app["ready"] and app["available"] and (self.link is not None or not app["needs_rig"])

    # ---- actions ------------------------------------------------------------------------

    def action_choose(self, step: int) -> None:
        table = self.query_one("#apps", DataTable)
        if table.row_count:
            table.move_cursor(row=(table.cursor_row + step) % table.row_count)

    def action_open_chosen(self) -> None:
        table = self.query_one("#apps", DataTable)
        if table.row_count:
            self.action_open(self.apps[table.cursor_row]["id"])

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
