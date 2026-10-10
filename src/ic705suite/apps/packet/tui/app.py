"""The packet terminal.

One screen, as in hamrlog: the status line on top, a framed area that shows
the terminal, the monitor or the heard stations (F1, F2, F3), the command
line, and the counters with the UTC clock at the bottom. The keyboard always
stays on the command line; every other key is a function key or a shortcut.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from importlib import resources
from pathlib import Path
from typing import TextIO

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input

from .. import config as config_module
from ..config import DEFAULT_TCP_PORTS, LINK_TYPES, Config
from ..i18n import N_, _
from ..link.agw import AgwLink, parse_monitor
from ..link.base import Link, LinkEvent
from ..link.telnet import TelnetLink
from .screens import ConfirmScreen, Field, FormScreen
from .widgets import (
    Counters,
    EntryPanel,
    HeardStation,
    HeardTable,
    StatsFooter,
    StatusLine,
    TextView,
)

#: Frame title and help line of each view, translated where they are shown.
VIEWS: dict[str, tuple[str, str]] = {
    "terminal": (
        N_("Terminal"),
        N_("Enter sends · /c CALL connects · /d disconnects · ↑↓ history · F4 connect · "
           "F5 disconnect · F9 setup · /help"),
    ),
    "monitor": (
        N_("Monitor"),
        N_("PgUp/PgDn scroll · Ctrl+L clear · F10 monitor on/off · F1 back to the terminal"),
    ),
    "heard": (
        N_("Heard stations"),
        N_("↑↓ choose station · Enter connects · F1 back to the terminal"),
    ),
}

#: Listed by /help.
HELP_TEXT = N_(
    "/c [port] CALL [v DIGI …]    connect (also F4)\n"
    "/c bpq:CALL                  enter a station of LinBPQ (bpq:EA7KLX-1 BBS…)\n"
    "/d                           disconnect (also F5)\n"
    "/ui DEST text                send an unconnected UI frame\n"
    "/port N                      default radio port\n"
    "/mon                         monitor on/off (also F10)\n"
    "/clear                       clear the view (also Ctrl+L)\n"
    "/reconnect                   redo the link with the node (also F6)\n"
    "/quit                        quit (also Ctrl+Q)\n"
    "//text                       send a line starting with /"
)

#: How many typed lines the up/down keys can recall.
LINE_HISTORY_LIMIT = 200

#: Answers taken as yes in the setup form.
YES = {"y", "yes", "s", "si", "sí", "true", "1", "on"}

#: Styles of the text in the terminal, following hamrlog's colours.
STYLE_SENT = "bold cyan"
STYLE_SYSTEM = "bold yellow"
STYLE_OK = "bold green"
STYLE_ERROR = "bold red"


def parse_connect(args: list[str], default_port: int) -> tuple[str, list[str], int] | None:
    """``[port] CALL [v|via] [DIGI …]`` -> call, digipeaters and port."""
    port = default_port
    if args and args[0].isdigit():
        port = int(args.pop(0))
    if not args:
        return None
    call = args.pop(0).upper()
    if args and args[0].lower() in ("v", "via"):
        args.pop(0)
    via = [digi.upper() for arg in args for digi in arg.split(",") if digi]
    return call, via, port


class HamrpktApp(App[None]):
    """Keyboard-only packet radio terminal."""

    CSS = resources.files("ic705suite.apps.packet.tui").joinpath("styles.tcss").read_text(encoding="utf-8")
    TITLE = "hamrpkt"

    # priority=True so the shortcuts work while the command line has focus.
    BINDINGS = [
        Binding("f1", "view('terminal')", "Terminal", priority=True, show=False),
        Binding("f2", "view('monitor')", "Monitor", priority=True, show=False),
        Binding("f3", "view('heard')", "Heard", priority=True, show=False),
        Binding("f4", "connect_form", "Connect", priority=True, show=False),
        Binding("f5", "disconnect", "Disconnect", priority=True, show=False),
        Binding("f6", "reconnect", "Reconnect", priority=True, show=False),
        Binding("f9", "setup", "Setup", priority=True, show=False),
        Binding("f10", "toggle_monitor", "Monitor", priority=True, show=False),
        Binding("pageup", "scroll(-1)", "Page up", priority=True, show=False),
        Binding("pagedown", "scroll(1)", "Page down", priority=True, show=False),
        Binding("ctrl+l", "clear", "Clear", priority=True, show=False),
        Binding("up", "arrow(-1)", "Previous", show=False),
        Binding("down", "arrow(1)", "Next", show=False),
        Binding("escape", "escape", "Back", show=False),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, config: Config, config_path: Path | None = None) -> None:
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.link: Link | None = None
        #: "offline", "connecting" or "up": the TCP link with the node.
        self.link_state = "offline"
        self.view = "terminal"
        self.radio_port = config.link.radio_port
        self.session = ""
        self.session_port = self.radio_port
        self.session_start: dt.datetime | None = None
        self.heard: dict[str, HeardStation] = {}
        self.frames = 0
        self._history: list[str] = []
        self._history_index: int | None = None
        self._session_log: TextIO | None = None
        #: Open the link again as soon as it drops (Telnet after F5).
        self._reopen = False
        #: "bpq:CALL" while the link is a detour through the node's console;
        #: the usual link comes back when it ends.
        self._bpq_target = ""

    # ------------------------------------------------------------- layout --
    def compose(self) -> ComposeResult:
        yield StatusLine(id="statusline")
        with Vertical(id="log-frame"):
            yield TextView(id="terminal")
            yield TextView(id="monitor")
            yield HeardTable(id="heard")
        yield EntryPanel(id="entry")
        yield StatsFooter(id="stats")

    def on_mount(self) -> None:
        self._show_view("terminal")
        self._refresh_status()
        self.set_interval(1.0, self._refresh_counters)
        self.entry.line.focus()
        if self.config.is_complete:
            self.start_link()
        else:
            self.entry.feedback(_("Set your callsign and the node address with F9"), "warning")
            self.call_after_refresh(self.action_setup)

    @property
    def entry(self) -> EntryPanel:
        return self.query_one(EntryPanel)

    @property
    def terminal(self) -> TextView:
        return self.query_one("#terminal", TextView)

    @property
    def monitor(self) -> TextView:
        return self.query_one("#monitor", TextView)

    def _show_view(self, view: str) -> None:
        self.view = view
        self.terminal.display = view == "terminal"
        self.monitor.display = view == "monitor"
        self.query_one(HeardTable).display = view == "heard"
        title, _keys = VIEWS[view]
        self.query_one("#log-frame", Vertical).border_title = _(title)
        self.refresh_keys()

    def refresh_keys(self) -> None:
        """Put the current view's keys back on the help line."""
        self.entry.show_keys(VIEWS[self.view][1])

    def _refresh_status(self) -> None:
        status = self.query_one(StatusLine)
        link = self.link
        status.mycall = self.config.mycall
        status.link_kind = link.kind if link else ""
        settings = link.config.link if link else self.config.link
        status.host = f"{settings.host}:{settings.port}"
        status.link_state = self.link_state
        status.radio_port = self.session_port if self.session else self.radio_port
        status.monitor = isinstance(link, AgwLink) and link.monitoring
        status.session = self.session
        self.entry.set_target(self.session)

    def _refresh_counters(self) -> None:
        link = self.link
        self.query_one(StatsFooter).counters = Counters(
            rx_bytes=link.rx_bytes if link else 0,
            tx_bytes=link.tx_bytes if link else 0,
            frames=self.frames,
            heard=len(self.heard),
            session_start=self.session_start,
        )

    def _say(self, message: str, style: str = STYLE_SYSTEM, level: str = "info") -> None:
        """A message in the terminal and, briefly, under the command line."""
        self.terminal.say(f"*** {message}", style)
        self.entry.feedback(message, level)

    # --------------------------------------------------------------- link --
    def start_link(
        self, config: Config | None = None, after_login: list[str] | None = None
    ) -> None:
        """Open the link with the node, replacing the current one if any.

        ``config`` replaces the saved settings for this link only (the detour
        of ``/c bpq:CALL``). The old link's worker is cancelled (same
        exclusive group), and its last events are ignored because it is no
        longer ``self.link``.
        """
        config = config or self.config
        if config is self.config:
            self._bpq_target = ""
        cls = TelnetLink if config.link.type == "telnet" else AgwLink
        link = cls(config, lambda event: self.on_link_event(link, event))
        if isinstance(link, TelnetLink):
            link.after_login = after_login or []
        self.link = link
        self._end_session()
        self.link_state = "connecting"
        self._refresh_status()
        self._say(_("Connecting to {host}:{port} ({kind})…").format(
            host=config.link.host, port=config.link.port, kind=link.kind))
        self.run_worker(link.run(), group="link", exclusive=True)

    def on_link_event(self, link: Link, event: LinkEvent) -> None:
        if link is not self.link:
            return
        handler = getattr(self, f"_on_{event.kind}", None)
        if handler is not None:
            handler(event)
        self._refresh_counters()

    def _on_link_up(self, event: LinkEvent) -> None:
        self.link_state = "up"
        settings = self.link.config.link if self.link else self.config.link
        self._say(_("Link up with {host}:{port}").format(host=settings.host, port=settings.port),
                  STYLE_OK, "ok")
        if self._bpq_target:
            self.session = self._bpq_target
            self.session_start = dt.datetime.now(dt.UTC)
            self._open_session_log(self._bpq_target)
        self._refresh_status()

    def _on_link_down(self, event: LinkEvent) -> None:
        self._end_session()
        settings = self.link.config.link if self.link else self.config.link
        host, port = settings.host, settings.port
        if event.extra.get("failed_open"):
            self._say(_("Cannot reach {host}:{port}: {error}").format(
                host=host, port=port, error=event.text), STYLE_ERROR, "error")
        elif event.text:
            self._say(_("Link lost: {error}").format(error=event.text), STYLE_ERROR, "error")
        else:
            self._say(_("Link closed"), STYLE_SYSTEM)
        self.link_state = "offline"
        self._refresh_status()
        if self._bpq_target:
            # The detour through the node is over: back to the usual link.
            self._reopen = False
            self.start_link()
        elif self._reopen:
            self._reopen = False
            self.start_link()
        else:
            self.terminal.say(_("F6 or /reconnect to try again"), "dim")

    def _on_session_up(self, event: LinkEvent) -> None:
        if self._bpq_target:
            # Inside the node the session is the detour itself; its own
            # "Connected to BBS" is just text.
            return
        self.session = event.call
        self.session_port = int(event.extra.get("port", self.radio_port))  # type: ignore[arg-type]
        self.session_start = dt.datetime.now(dt.UTC)
        if event.text:
            self.terminal.say(event.text, "dim")
        self._say(_("Connected to {call}").format(call=event.call), STYLE_OK, "ok")
        self._open_session_log(event.call)
        self._refresh_status()

    def _on_session_down(self, event: LinkEvent) -> None:
        if self._bpq_target:
            return
        call = event.call or self.session
        if event.text:
            self.terminal.say(event.text, "dim")
        self._say(_("Disconnected from {call}").format(call=call), STYLE_SYSTEM, "warning")
        self._end_session()
        self._refresh_status()

    def _on_rx(self, event: LinkEvent) -> None:
        self.terminal.feed(event.text)
        self._log(event.text.replace("\r\n", "\n").replace("\r", "\n"))

    def _on_monitor(self, event: LinkEvent) -> None:
        self.frames += 1
        lines = [line for line in event.text.replace("\r\n", "\r").split("\r") if line.strip()]
        if not lines:
            return
        header_style = "bold cyan" if event.own else "bold yellow"
        self.monitor.say(lines[0], header_style)
        for line in lines[1:]:
            self.monitor.say(line, "")
        heard = parse_monitor(lines[0])
        if heard is None or event.own:
            return
        now = dt.datetime.now(dt.UTC)
        station = self.heard.get(heard.src)
        if station is None:
            station = HeardStation(heard.src, now, 0, heard.port, heard.dest, heard.via)
            self.heard[heard.src] = station
        station.last, station.port = now, heard.port
        station.dest, station.via = heard.dest, heard.via
        station.frames += 1
        self.query_one(HeardTable).load(
            sorted(self.heard.values(), key=lambda item: item.last, reverse=True)
        )

    def _on_info(self, event: LinkEvent) -> None:
        self._say(event.text, STYLE_SYSTEM)

    def _on_error(self, event: LinkEvent) -> None:
        self._say(event.text, STYLE_ERROR, "error")

    # ------------------------------------------------------- session log --
    def _open_session_log(self, call: str) -> None:
        self._close_session_log()
        folder = config_module.data_dir() / "sessions"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            safe = "".join(ch if ch.isalnum() or ch == "-" else "_" for ch in call)
            path = folder / f"{dt.datetime.now(dt.UTC):%Y-%m-%d}_{safe}.log"
            self._session_log = path.open("a", encoding="utf-8")
            stamp = f"{dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S} UTC"
            self._session_log.write(f"\n=== {stamp} {self.config.mycall} <> {call} ===\n")
            self.terminal.say(_("Session log: {path}").format(path=path), "dim")
        except OSError:
            self._session_log = None

    def _log(self, text: str) -> None:
        if self._session_log is not None:
            self._session_log.write(text)
            self._session_log.flush()

    def _close_session_log(self) -> None:
        if self._session_log is not None:
            self._session_log.close()
            self._session_log = None

    def _end_session(self) -> None:
        self.session = ""
        self.session_start = None
        self._close_session_log()

    # ------------------------------------------------------- command line --
    @on(Input.Submitted, "#line")
    def _on_line(self, event: Input.Submitted) -> None:
        text = event.value
        event.input.value = ""
        self._history_index = None
        if not text and self.view == "heard":
            self._connect_selected()
            return
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
            del self._history[:-LINE_HISTORY_LIMIT]
        if text.startswith("/") and not text.startswith("//"):
            self._command(text[1:])
            return
        self._send(text[1:] if text.startswith("//") else text)

    def _send(self, text: str) -> None:
        link = self.link
        if link is None or not link.is_open:
            self.entry.feedback(_("No link. F6 or /reconnect"), "error")
            return
        if isinstance(link, AgwLink) and not self.session:
            self.entry.feedback(
                _("Not connected. Use /c CALL to connect, or /ui DEST text for an unproto frame."),
                "warning",
            )
            return
        link.send_line(text, self.session, self.session_port)
        self.terminal.say(text, STYLE_SENT)
        self._log(f"> {text}\n")
        self.entry.feedback("")

    def _command(self, line: str) -> None:
        parts = line.split()
        if not parts:
            return
        name, args = parts[0].lower(), parts[1:]
        if name in ("c", "connect"):
            parsed = parse_connect(args, self.radio_port)
            if parsed is None:
                self.entry.feedback(_("Usage: {usage}").format(usage="/c [port] CALL [v DIGI …]"),
                                    "warning")
                return
            self._connect(*parsed)
        elif name in ("d", "disconnect", "bye"):
            self.action_disconnect()
        elif name == "ui":
            self._unproto(args)
        elif name == "port":
            if not args or not args[0].isdigit() or int(args[0]) < 1:
                self.entry.feedback(_("Usage: {usage}").format(usage="/port 1"), "warning")
                return
            self.radio_port = int(args[0])
            self.entry.feedback(_("Port set to {port}").format(port=self.radio_port), "ok")
            self._refresh_status()
        elif name in ("mon", "monitor"):
            self.action_toggle_monitor()
        elif name in ("clear", "cls"):
            self.action_clear()
        elif name in ("reconnect", "link"):
            self.action_reconnect()
        elif name in ("help", "h", "?"):
            self.terminal.say(_("Commands"), STYLE_SYSTEM)
            for help_line in _(HELP_TEXT).splitlines():
                self.terminal.say("  " + help_line, "")
            self._show_view("terminal")
        elif name in ("q", "quit", "exit"):
            self.action_quit()
        else:
            self.entry.feedback(
                _("Unknown command: {cmd}. /help lists them").format(cmd="/" + name), "error"
            )

    def _require_link(self) -> Link | None:
        if self.link is None or not self.link.is_open:
            self.entry.feedback(_("No link. F6 or /reconnect"), "error")
            return None
        return self.link

    def _connect(self, call: str, via: list[str], port: int) -> None:
        if self.session:
            self.entry.feedback(
                _("Already connected to {call}: /d first").format(call=self.session), "warning"
            )
            return
        if call.startswith("BPQ:"):
            self._connect_bpq(call.removeprefix("BPQ:"))
            return
        link = self._require_link()
        if link is None:
            return
        target = call + (" via " + ",".join(via) if via else "")
        self._say(_("Calling {call}…").format(call=target), STYLE_SYSTEM)
        self.session_port = port
        link.connect(call, via, port)
        self._show_view("terminal")

    def _connect_bpq(self, call: str) -> None:
        """Reach one of the node's own stations through its Telnet console.

        Direwolf cannot connect to the node sharing it on the same radio, so
        ``bpq:CALL`` logs into LinBPQ and enters the matching application,
        as LinPac's ``bpq`` port does on the Pi.
        """
        bpq = self.config.bpq
        if not call or not bpq.user:
            self.entry.feedback(_("Set the LinBPQ user and password with F9"), "warning")
            return
        settings = dataclasses.replace(self.config.link, type="telnet", port=bpq.port,
                                       user=bpq.user, password=bpq.password)
        command = bpq.command_for(call)
        self._say(_("Calling {call}…").format(call=f"bpq:{call}"), STYLE_SYSTEM)
        self.start_link(dataclasses.replace(self.config, link=settings),
                        [command] if command else [])
        self._bpq_target = f"bpq:{call}"
        self._show_view("terminal")

    def _connect_selected(self) -> None:
        call = self.query_one(HeardTable).selected_call
        if not call:
            self.entry.feedback(_("No station selected"), "warning")
            return
        self._connect(call, [], self.heard[call].port if call in self.heard else self.radio_port)

    def _unproto(self, args: list[str]) -> None:
        link = self._require_link()
        if link is None:
            return
        if len(args) < 2:
            self.entry.feedback(_("Usage: {usage}").format(usage="/ui DEST text"), "warning")
            return
        if not link.supports_monitor:
            self.entry.feedback(_("The monitor needs an AGWPE link"), "warning")
            return
        dest, text = args[0].upper(), " ".join(args[1:])
        link.unproto(dest, text, self.radio_port, [])
        self.terminal.say(f"UI {self.config.mycall}>{dest}: {text}", STYLE_SENT)

    # ------------------------------------------------------------ actions --
    def action_view(self, view: str) -> None:
        self._show_view(view)

    def action_arrow(self, delta: int) -> None:
        """Arrows move the heard list in F3, and recall typed lines elsewhere."""
        if self.view == "heard":
            table = self.query_one(HeardTable)
            if table.row_count:
                row = max(0, min(table.row_count - 1, table.cursor_row + delta))
                table.move_cursor(row=row)
            return
        if not self._history:
            return
        if self._history_index is None:
            if delta > 0:
                return
            index = len(self._history) - 1
        else:
            index = self._history_index + delta
        line = self.entry.line
        if index >= len(self._history):
            self._history_index = None
            line.value = ""
            return
        self._history_index = max(0, index)
        line.value = self._history[self._history_index]
        line.cursor_position = len(line.value)

    def action_scroll(self, direction: int) -> None:
        widget = {"terminal": self.terminal, "monitor": self.monitor}.get(self.view)
        if widget is None:
            widget = self.query_one(HeardTable)
        if direction < 0:
            widget.scroll_page_up()
        else:
            widget.scroll_page_down()

    def action_clear(self) -> None:
        if self.view == "terminal":
            self.terminal.clear()
        elif self.view == "monitor":
            self.monitor.clear()
        else:
            self.heard.clear()
            self.query_one(HeardTable).load([])

    def action_escape(self) -> None:
        self.entry.line.value = ""
        self.entry.feedback("")
        self._history_index = None
        if self.view != "terminal":
            self._show_view("terminal")

    def action_connect_form(self) -> None:
        if self._require_link() is None:
            return
        selected = self.query_one(HeardTable).selected_call if self.view == "heard" else ""
        fields = [
            Field("call", _("Callsign"), selected),
            Field("via", _("Via (digipeaters)")),
            Field("port", _("Radio port"), str(self.radio_port)),
        ]

        def validate(values: dict[str, str]) -> str | None:
            if not values["call"]:
                return _("The callsign is required")
            if not values["port"].isdigit() or int(values["port"]) < 1:
                return _("The radio port must be a number from 1")
            return None

        def done(values: dict[str, str] | None) -> None:
            if values:
                via = [d.upper() for d in values["via"].replace(",", " ").split()]
                self._connect(values["call"].upper(), via, int(values["port"]))

        self.push_screen(FormScreen(_("Connect"), fields, save_label=_("Connect"),
                                    validate=validate), done)

    def action_disconnect(self) -> None:
        link = self._require_link()
        if link is None:
            return
        if not self.session and not isinstance(link, TelnetLink):
            self.entry.feedback(_("Nothing to disconnect"), "warning")
            return
        if isinstance(link, TelnetLink):
            self._reopen = True
        link.disconnect(self.session, self.session_port)

    def action_reconnect(self) -> None:
        self.start_link()

    def action_toggle_monitor(self) -> None:
        link = self._require_link()
        if link is None:
            return
        if not isinstance(link, AgwLink):
            self.entry.feedback(_("The monitor needs an AGWPE link"), "warning")
            return
        link.set_monitor(not link.monitoring)
        self.entry.feedback(_("Monitor on") if link.monitoring else _("Monitor off"), "ok")
        self._refresh_status()

    def action_setup(self) -> None:
        link = self.config.link
        fields = [
            Field("mycall", _("My callsign"), self.config.mycall),
            Field("type", _("Link type"), link.type, _("agw or telnet")),
            Field("host", _("Node host"), link.host),
            Field("port", _("TCP port"), str(link.port), _("AGWPE 8000 · Telnet 8010")),
            Field("user", _("User"), link.user, _("Only if the node asks for it")),
            Field("password", _("Password"), link.password, password=True),
            Field("radio_port", _("Radio port"), str(link.radio_port)),
            Field("monitor", _("Monitor at start"), _("yes") if link.monitor else _("no"),
                  _("yes or no")),
            Field("bpq_port", _("LinBPQ Telnet port"), str(self.config.bpq.port),
                  _("for /c bpq:CALL")),
            Field("bpq_user", _("LinBPQ user"), self.config.bpq.user),
            Field("bpq_password", _("LinBPQ password"), self.config.bpq.password, password=True),
        ]

        def validate(values: dict[str, str]) -> str | None:
            if not values["mycall"]:
                return _("The callsign is required")
            if values["type"].lower() not in LINK_TYPES:
                return _("The link type must be agw or telnet")
            if values["port"] and not values["port"].isdigit():
                return _("The TCP port must be a number")
            if not values["radio_port"].isdigit() or int(values["radio_port"]) < 1:
                return _("The radio port must be a number from 1")
            if values["bpq_port"] and not values["bpq_port"].isdigit():
                return _("The TCP port must be a number")
            return None

        def done(values: dict[str, str] | None) -> None:
            if not values:
                return
            self.config.mycall = values["mycall"].upper()
            link.type = values["type"].lower()
            link.host = values["host"]
            link.port = int(values["port"] or DEFAULT_TCP_PORTS[link.type])
            link.user = values["user"]
            link.password = values["password"]
            link.radio_port = int(values["radio_port"])
            link.monitor = values["monitor"].lower() in YES
            self.config.bpq.port = int(values["bpq_port"] or DEFAULT_TCP_PORTS["telnet"])
            self.config.bpq.user = values["bpq_user"]
            self.config.bpq.password = values["bpq_password"]
            self.radio_port = link.radio_port
            config_module.save(self.config, self.config_path)
            self.entry.feedback(_("Settings saved"), "ok")
            self.start_link()

        self.push_screen(FormScreen(_("Setup"), fields, validate=validate), done)

    async def action_quit(self) -> None:
        if not self.session:
            await self._shutdown()
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                if self.link is not None and self.link.is_open:
                    self.link.disconnect(self.session, self.session_port)
                self.call_later(self._shutdown)

        self.push_screen(ConfirmScreen(
            _("Still connected to {call}. Disconnect and quit?").format(call=self.session),
            danger=True), done)

    async def _shutdown(self) -> None:
        self._close_session_log()
        link, self.link = self.link, None
        if link is not None:
            await link.close()
        self.exit()
