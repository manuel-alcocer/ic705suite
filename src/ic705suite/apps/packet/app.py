"""The packet terminal, BayCom style, on the core's TNC.

The status line on top; the monitor (channel 0) in a window that stays in sight; the bar of
channels; the chosen channel's window; the command line, which sends to that channel's
station; the clock and the keys at the bottom. Sessions live in the core: leaving this
screen does not drop them.
"""

from __future__ import annotations

import re

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import DataTable, Input, RichLog, Static

from ic705suite.apps.packet.client import CHANNELS, STYLES, PacketClient, Tab, piece_text
from ic705suite.i18n import N_, _
from ic705suite.station.links import Link
from ic705suite.ui.sysload import with_load
from ic705suite.ui.widgets import ChoiceScreen, TextScreen, clock_footer, css

HINT = N_("F1-F8 Channel · Enter sends · /c CALL connects · /d disconnects · /help · "
          "F9 Monitor · F10 Heard · F12 BBS · PgUp/PgDn · Ctrl+L Clear · Ctrl+Q Back")
SHORT_HINT = N_("F1-F8 Channel · /help · F9 Monitor · F10 Heard · F12 BBS · Ctrl+Q Back")
HEARD_HINT = N_("↑↓ choose · Enter connects · Esc back")
BBS_HINT = N_("↑↓ choose · Enter reads · K kills · U users/messages · Esc back")

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")
LINE_BREAK = re.compile(r"\r\n|\n\r|\r|\n")
TAB_STYLES = {"connected": "bold green", "calling": "bold yellow", "leaving": "yellow"}
FEEDBACK_STYLES = {"ok": "green", "warning": "yellow", "error": "bold red", "info": "cyan"}


class TextView(RichLog):
    """A scrolling log fed with text as it arrives. Packet text comes in pieces that rarely
    end at a line break, and prompts never do: whole lines are written at once, the rest a
    moment later, so a prompt is never hidden."""

    FLUSH_DELAY = 0.4

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=False, markup=False, wrap=True, max_lines=5000, **kwargs)
        self._partial = ""
        self._partial_style = ""
        self._timer: Timer | None = None

    def feed(self, text: str, style: str = "") -> None:
        if self._partial and style != self._partial_style:
            self._flush()
        self._partial += text
        self._partial_style = style
        *lines, self._partial = LINE_BREAK.split(self._partial)
        for line in lines:
            self._write_line(line, style)
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._partial:
            self._timer = self.set_timer(self.FLUSH_DELAY, self._flush)

    def _flush(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._partial:
            self._write_line(self._partial, self._partial_style)
            self._partial = ""

    def _write_line(self, line: str, style: str) -> None:
        line = CONTROL.sub("", line)
        rendered = Text.from_ansi(line) if "\x1b" in line else Text(line)
        if style:
            rendered.stylize(style)
        self.write(rendered)

    def reset(self) -> None:
        self._partial = ""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.clear()


def tabs_text(tabs: list[Tab]) -> Text:
    """The channel bar: number and station, the chosen one reversed, unread text marked."""
    text = Text(no_wrap=True)
    for tab in tabs:
        style = TAB_STYLES.get(tab.state, "dim" if tab.number else "")
        if tab.current:
            style = "bold reverse " + (style.replace("bold", "").strip() or "white")
        label = f" {tab.number}:{tab.label}"
        if tab.unread:
            label += f" •{tab.unread}"
        text.append(label + " ", style=style)
    return text


def status_text(pieces: list[tuple[str, str, str]]) -> Text:
    text = Text(no_wrap=True)
    for label, value, style in pieces:
        if label:
            text.append(f"{label} ", style="dim")
        text.append(value, style=style)
        text.append("   ")
    return text


class HeardScreen(ModalScreen[str]):
    """The stations heard, the latest first; Enter connects to the chosen one."""

    BINDINGS = [
        Binding("escape", "dismiss('')", show=False),
        Binding("enter", "choose", show=False, priority=True),
    ]

    def __init__(self, heard: list[dict]) -> None:
        super().__init__()
        self.heard = heard

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal wide") as box:
            box.border_title = _("Heard stations")
            yield DataTable(id="heard", cursor_type="row", zebra_stripes=False)
            yield Static(_(HEARD_HINT), classes="modal-help")

    def on_mount(self) -> None:
        import datetime as dt

        table = self.query_one(DataTable)
        table.add_columns(_("Callsign"), _("Last heard"), _("Frames"), _("To"), _("Via"))
        table.styles.height = "auto"
        table.styles.max_height = 18
        for station in self.heard:
            last = dt.datetime.fromtimestamp(station["last"], dt.UTC)
            table.add_row(station["call"], f"{last:%H:%M:%S} UTC",
                          Text(str(station["frames"]), justify="right"), station["dest"],
                          station["via"])
        if not self.heard:
            table.add_row(_("Nobody heard yet"), "", "", "", "")
        table.focus()

    def action_choose(self) -> None:
        table = self.query_one(DataTable)
        if self.heard and 0 <= table.cursor_row < len(self.heard):
            self.dismiss(self.heard[table.cursor_row]["call"])


def message_lines(message: dict) -> list[str]:
    """A BBS message as the sysop reads it."""
    import datetime as dt

    at = f"@{message['at']}" if message["at"] else ""
    created = dt.datetime.fromtimestamp(message["created"], dt.UTC)
    return [f"{_('From')}: {message['from_call']}", f"{_('To')}: {message['to_call']}{at}",
            f"{_('Date')}: {created:%Y-%m-%d %H:%M} UTC", f"Bid: {message['bid']}",
            f"{_('Title')}: {message['title']}", "", *message["body"].splitlines()]


def bbs_who(state: dict) -> str:
    """Who is on the BBS now, for the sysop page's heading."""
    bbs = state.get("bbs") or {}
    sessions = bbs.get("sessions") or []
    if not sessions:
        return _("{call}: nobody connected · {count} messages").format(
            call=bbs.get("call", ""), count=bbs.get("messages", 0))
    names = ", ".join(s["call"] + (" (" + _("here") + ")" if s["local"] else "")
                      for s in sessions)
    return _("{call}: connected {names} · {count} messages").format(
        call=bbs.get("call", ""), names=names, count=bbs.get("messages", 0))


class BbsScreen(ModalScreen[None]):
    """The sysop's page: the BBS's messages (read, kill) and its users."""

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("enter", "read", show=False, priority=True),
        Binding("k", "kill", show=False),
        Binding("u", "users", show=False),
    ]

    def __init__(self, link: Link) -> None:
        super().__init__()
        self.link = link
        self.showing = "messages"
        self.rows: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal wide") as box:
            box.border_title = _("BBS")
            yield Static(id="bbs-who", classes="modal-subtitle")
            yield DataTable(id="bbs", cursor_type="row", zebra_stripes=False)
            yield Static(_(BBS_HINT), classes="modal-help")

    async def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.styles.height = "auto"
        table.styles.max_height = 18
        await self._load()
        table.focus()

    async def _load(self) -> None:
        import datetime as dt

        state = await self.link.call("pkt.state")
        self.query_one("#bbs-who", Static).update(Text(bbs_who(state), style="dim"))
        table = self.query_one(DataTable)
        row = table.cursor_row if table.row_count else 0
        table.clear(columns=True)
        if self.showing == "messages":
            self.rows = (await self.link.call("pkt.bbs.messages")).get("messages", [])
            table.add_columns("#", _("Date"), _("Type"), _("To"), _("From"), _("Size"),
                              _("Title"))
            for m in self.rows:
                at = f"@{m['at']}" if m["at"] else ""
                table.add_row(Text(str(m["id"]), justify="right"),
                              f"{dt.datetime.fromtimestamp(m['created'], dt.UTC):%d-%b %H:%M}",
                              m["kind"] + m["status"], m["to_call"] + at, m["from_call"],
                              Text(str(m["size"]), justify="right"), m["title"])
            if not self.rows:
                table.add_row("", _("No messages yet"), "", "", "", "", "")
        else:
            self.rows = (await self.link.call("pkt.bbs.users")).get("users", [])
            table.add_columns(_("Callsign"), _("Name"), _("Last seen"), _("Connects"))
            for u in self.rows:
                seen = dt.datetime.fromtimestamp(u["last_seen"], dt.UTC)
                table.add_row(u["call"], u["name"], f"{seen:%Y-%m-%d %H:%M}",
                              Text(str(u["connects"]), justify="right"))
            if not self.rows:
                table.add_row(_("Nobody yet"), "", "", "")
        if self.rows:
            table.move_cursor(row=min(row, len(self.rows) - 1))

    def _chosen(self) -> dict | None:
        table = self.query_one(DataTable)
        if self.rows and 0 <= table.cursor_row < len(self.rows):
            return self.rows[table.cursor_row]
        return None

    def action_read(self) -> None:
        if self.showing == "messages" and (chosen := self._chosen()):
            self.run_worker(self._read(chosen["id"]))

    async def _read(self, number: int) -> None:
        message = (await self.link.call("pkt.bbs.message", id=number)).get("message")
        if message:
            self.app.push_screen(TextScreen(_("Message {number}").format(number=number),
                                            message_lines(message)))

    def action_kill(self) -> None:
        if self.showing == "messages" and (chosen := self._chosen()):
            self.run_worker(self._kill(chosen))

    async def _kill(self, message: dict) -> None:
        answer = await self.app.push_screen_wait(ChoiceScreen(
            _("Kill message"), _("Kill message {number} from {call}, \"{title}\"?").format(
                number=message["id"], call=message["from_call"], title=message["title"]),
            [("yes", _("Kill")), ("no", _("Keep"))]))
        if answer == "yes":
            await self.link.call("pkt.bbs.kill", id=message["id"])
            await self._load()

    def action_users(self) -> None:
        self.showing = "users" if self.showing == "messages" else "messages"
        self.run_worker(self._load())


class PacketApp(App[int]):
    CSS = css("ic705suite.apps.packet")
    TITLE = "ic705suite Packet"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        *(Binding(f"f{n}", f"channel({n})", show=False, priority=True)
          for n in range(1, CHANNELS + 1)),
        Binding("f9", "monitor", show=False, priority=True),
        Binding("f10", "heard", show=False, priority=True),
        Binding("f12", "bbs", show=False, priority=True),
        Binding("pageup", "scroll(-1)", show=False),
        Binding("pagedown", "scroll(1)", show=False),
        Binding("ctrl+l", "clear", show=False, priority=True),
        Binding("up", "recall(-1)", show=False),
        Binding("down", "recall(1)", show=False),
        Binding("escape", "escape", show=False),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link, owns_link: bool = False) -> None:
        super().__init__()
        self.link = link
        self.owns_link = owns_link
        self.client = PacketClient(link, self._on_text, self._on_reload, self._on_state,
                                   self._feedback, self._show)
        self.client.link_task = lambda awaitable: self.run_worker(awaitable)

    def compose(self) -> ComposeResult:
        yield Static(id="statusline")
        yield TextView(id="monitor", classes="frame")
        yield Static(id="tabs")
        yield TextView(id="channel", classes="frame")
        with Vertical(id="entry"):
            yield Input(id="line")
        yield Static(id="stats")

    def on_mount(self) -> None:
        self.query_one("#monitor").border_title = _("Monitor")
        self.query_one("#monitor").can_focus = False
        self.query_one("#channel").can_focus = False
        self.set_interval(1.0, self._tick)
        self._on_state()
        self.query_one("#line", Input).focus()
        self.link.attach(self.client.handle)
        self.run_worker(self._start(), exclusive=True, group="start")

    async def _start(self) -> None:
        try:
            await self.client.start()
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._feedback("error", _("cannot connect: {error}").format(error=exc))
            return
        if not self.client.configured:
            self._feedback("warning", _("Packet is not set up: set the TNC in [packet] "
                                        "(ic705suite --configure)"))

    # ---- painting ------------------------------------------------------------------------

    def _view(self, number: int) -> TextView | None:
        if number == 0:
            return self.query_one("#monitor", TextView)
        if number == self.client.channel:
            return self.query_one("#channel", TextView)
        return None

    def _on_text(self, number: int, piece: dict) -> None:
        if number == 0 and self.client.channel == 0:
            self.query_one("#channel", TextView).feed(piece_text(piece),
                                                     STYLES.get(piece.get("kind"), "dim"))
        view = self._view(number)
        if view is not None:
            view.feed(piece_text(piece), STYLES.get(piece.get("kind"), "dim"))

    def _on_reload(self, number: int, pieces: list[dict]) -> None:
        views = [self._view(number)]
        if number == 0 and self.client.channel == 0:
            views.append(self.query_one("#channel", TextView))
        for view in filter(None, views):
            view.reset()
            for piece in pieces:
                view.feed(piece_text(piece), STYLES.get(piece.get("kind"), "dim"))

    def _on_state(self) -> None:
        client = self.client
        self.query_one("#tabs", Static).update(tabs_text(client.tabs()))
        info = client.channel_info()
        title = _("Channel {channel}").format(channel=client.channel)
        if client.channel == 0:
            title = _("Monitor")
        elif info.get("call"):
            title += f" · {info['call']}"
        self.query_one("#channel").border_title = title
        line = self.query_one("#line", Input)
        target = client.target()
        line.placeholder = (_("to {call}").format(call=target) if target
                            else _("/c CALL connects this channel · /help"))
        self._tick()

    def _tick(self) -> None:
        status = self.query_one("#statusline", Static)
        status.update(with_load(status_text(self.client.status()), status.size.width or 120))
        stats = self.query_one("#stats", Static)
        stats.update(clock_footer(stats.size.width, self.client.counters(),
                                  [_(HINT), _(SHORT_HINT)]))

    def _show(self, title: str, lines: list[str]) -> None:
        self.push_screen(TextScreen(title, lines))

    def _feedback(self, level: str, text: str) -> None:
        entry = self.query_one("#entry")
        entry.border_subtitle = Text(" " + text[:200] + " ",
                                     style=FEEDBACK_STYLES.get(level, "cyan")) if text else ""

    # ---- keys ----------------------------------------------------------------------------

    @on(Input.Submitted, "#line")
    def _submit(self, event: Input.Submitted) -> None:
        text = event.value
        event.input.value = ""
        self._feedback("", "")
        self.run_worker(self._do_submit(text))

    async def _do_submit(self, text: str) -> None:
        if await self.client.submit(text) == "quit":
            self.action_leave()

    def action_channel(self, number: int) -> None:
        self.run_worker(self.client.select(number), exclusive=True, group="channel")

    def action_monitor(self) -> None:
        monitor = self.query_one("#monitor")
        monitor.display = not monitor.display

    def action_heard(self) -> None:
        def chosen(call: str | None) -> None:
            if call:
                self.run_worker(self.client.connect_to(call))
            self.query_one("#line", Input).focus()

        self.push_screen(HeardScreen(self.client.heard()), chosen)

    def action_bbs(self) -> None:
        if not ((self.client.state.get("bbs") or {}).get("on")):
            self._feedback("warning", _("The BBS is off: [packet] bbs = true "
                                        "(ic705suite --configure)"))
            return
        if len(self.screen_stack) == 1:
            self.push_screen(BbsScreen(self.link), lambda _r: self.query_one("#line").focus())

    def action_scroll(self, direction: int) -> None:
        view = self.query_one("#channel", TextView)
        if direction < 0:
            view.scroll_page_up()
        else:
            view.scroll_page_down()

    def action_clear(self) -> None:
        self.run_worker(self.client.clear())

    def action_recall(self, delta: int) -> None:
        text = self.client.recall(delta)
        if text is not None:
            line = self.query_one("#line", Input)
            line.value = text
            line.cursor_position = len(text)

    def action_escape(self) -> None:
        self.query_one("#line", Input).value = ""
        self._feedback("", "")
        self.client.history_index = None

    def action_leave(self) -> None:
        self.run_worker(self._leave(), exclusive=True, group="leave")

    async def _leave(self) -> None:
        # The sessions stay up in the core; coming back shows them as they are.
        if self.owns_link:
            await self.link.close()
        else:
            self.link.detach()
        self.exit(0)
