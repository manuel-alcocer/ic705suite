"""Full-screen client (Textual), laid out and styled after HAMRLOG.

It only talks to a Link: events in, command lines out. The same client works with the
core in this process or on a remote `ic705suite --serve` (e.g. a Raspberry Pi).
"""

from __future__ import annotations

import datetime as dt

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label, Static

from ic705suite.rig import bands
from ic705suite.i18n import N_, _, render
from ic705suite.apps.cw.interpret import Interpretation, Interpreter, interpret
from ic705suite.station.links import Link, LinkError
from ic705suite.rig.base import RigError, smeter_text
from ic705suite.apps.cw.screens import ChannelsScreen
from ic705suite.apps.cw.session import HELP
from ic705suite.ui.channels import ChannelSaving
from ic705suite.ui.widgets import append_keys as _append_keys
from ic705suite.ui.widgets import TextScreen, css
from ic705suite.rig.transport import TransportError
from ic705suite.ui import glyphs
from ic705suite.ui.sysload import with_load

MAX_LINES = 2000
LINE_HISTORY = 100

STYLES = {"rx": "white", "tx": "bold yellow", "info": "cyan", "error": "bold red",
          "spot": "bold green", "summary": "bold white", "glossary": "dim",
          "history": "dim italic"}

HELP_HINT = N_("F1 Help · F2-F9 Macros · F10 Skimmer · F11 Channels · Esc Stop · "
               "PgUp/PgDn Tune · Ctrl+Q Quit")
SHORT_HINT = N_("F1 Help · F10 Skimmer · F11 Channels · Ctrl+Q Quit")
class StatusLine(Static):
    """One line: radio, frequency, mode, S-meter, keyer and the state badges."""

    def __init__(self, link_description: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.state: dict = {}
        self.link_description = link_description

    def render(self) -> Text:
        st = self.state
        text = Text(no_wrap=True, overflow="ellipsis")

        def badge(label: str, style: str) -> None:
            text.append(f" {label} ", style=glyphs.style(style))
            text.append(" ")

        if st and not st.get("connected"):
            badge(_("DISCONNECTED"), "bold white on red")
        if st.get("transmitting"):
            badge(_("TX"), "bold white on red")
        elif st and not st.get("tx_enabled"):
            badge(_("RX ONLY"), "bold black on yellow")
        play = st.get("playback")
        if play:
            badge(f"▶ {play['file']} {int(play['elapsed']) // 60}:{int(play['elapsed']) % 60:02d}"
                  f"/{int(play['duration']) // 60}:{int(play['duration']) % 60:02d}",
                  "bold black on magenta")
        if st.get("scanning"):
            badge(f"{_('SCAN')} {bands.format_frequency(st.get('scan_freq'))}", "bold black on cyan")
        if st.get("skimming"):
            badge(_("SKIMMER"), "bold black on green")
        start = len(text)

        def chunk(label: str, value: str, style: str = "bold white") -> None:
            if not value:
                return
            if len(text) > start:
                text.append(" · ", style="dim")
            if label:
                text.append(f"{label} ", style="dim")
            text.append(value, style=style)

        smeter = st.get("smeter", 0)
        cells = min(15, round(smeter / 120 * 9) if smeter <= 120 else 9 + round((smeter - 120) / 20))
        chunk("", st.get("radio") or "IC-705", "bold cyan")
        chunk(_("QRG"), bands.format_frequency(st.get("frequency")), "bold yellow")
        chunk(_("MODE"), st.get("mode") or "-", "bold green")
        if len(text) > start:
            text.append(" · ", style="dim")
        text.append(f"{smeter_text(smeter):<6}", style="bold white")
        text.append(glyphs.BAR * cells, style="green")
        text.append("·" * (15 - cells), style="dim")
        chunk(_("KEY"), f"{st.get('key_wpm', '-')} {_('wpm')}", "white")
        if st.get("power") is not None:
            chunk(_("PWR"), f"{st['power']}%", "white")
        chunk(_("LINK"), self.link_description, "bold blue")
        return with_load(text, self.size.width)

    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)     # CPU and RAM, even with the radio quiet


class TextPane(Widget):
    """Decoded text and messages; the newest lines at the bottom."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.lines: list[tuple[str, str]] = []

    def add(self, kind: str, text: str, append: bool = False) -> None:
        if append and self.lines and self.lines[-1][0] == kind:
            self.lines[-1] = (kind, self.lines[-1][1] + text)
        else:
            self.lines.append((kind, text))
        del self.lines[:-MAX_LINES]
        self.refresh()

    def clear(self) -> None:
        self.lines.clear()
        self.refresh()

    def render(self) -> Text:
        width, height = max(1, self.size.width), max(1, self.size.height)
        rows: list[tuple[str, str]] = []
        for kind, line in reversed(self.lines):
            wrapped = [line[i:i + width] for i in range(0, max(len(line), 1), width)]
            rows[:0] = [(kind, chunk) for chunk in wrapped]
            if len(rows) >= height:
                break
        rows = rows[-height:]
        text = Text(no_wrap=True)
        text.append("\n" * (height - len(rows)))
        for i, (kind, chunk) in enumerate(rows):
            text.append(chunk, style=STYLES.get(kind, ""))
            if i < len(rows) - 1:
                text.append("\n")
        return text


class DetailPanel(Static):
    """Three lines: the decoder (or the selected skimmer station) and what to do next."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.state: dict = {}
        self.channels: list[dict] = []
        self.selected = 0
        self.meaning = ""  # plain-language summary of the selected skimmer station

    def render(self) -> Text:
        st = self.state
        text = Text(no_wrap=True, overflow="ellipsis")

        def chunk(label: str, value: str, style: str = "bold white", first: bool = False) -> None:
            if not first:
                text.append(" · ", style="dim")
            text.append(f"{label} ", style="dim")
            text.append(value, style=style)

        if st.get("skimming"):
            span = st.get("skim_range")
            if span:
                chunk(_("RANGE"), f"{bands.format_frequency(span[0])} – "
                                  f"{bands.format_frequency(span[1])}", "bold yellow", first=True)
                chunk(_("WIDTH"), f"{(span[1] - span[0]) / 1000:.2f} kHz", "white")
                chunk(_("STATIONS"), str(len(self.channels)))
            else:
                chunk(_("STATIONS"), str(len(self.channels)), first=True)
            chunk(_("SPOTS"), str(st.get("spots", 0)))
            text.append("\n")
            if self.channels and 0 <= self.selected < len(self.channels):
                ch = self.channels[self.selected]
                chunk(_("QRG"), bands.format_frequency(ch["frequency"]), "bold yellow", first=True)
                chunk(_("CALL"), ch["callsign"] or "-", "bold cyan")
                chunk(_("SPEED"), f"{ch['wpm']:.0f} {_('wpm')}")
                chunk(_("SNR"), f"{ch['snr_db']:.0f} dB")
            else:
                text.append(_("Looking for CW signals..."), style="dim")
            text.append("\n")
            if self.meaning:
                text.append("≈ ", style="bold green")
                text.append(self.meaning, style="white")
            else:
                _append_keys(text, _("Tab select a station · Enter tune it (or /go N) · "
                                     "F10 back to single decoding"))
            return text

        d = st.get("decoder", {})
        if not d:
            return text
        chunk(_("TONE"), f"{d['tone']:.0f} Hz", "bold yellow", first=True)
        chunk(_("SPEED"), f"{d['wpm']:.1f} {_('wpm')}")
        chunk(_("SNR"), f"{d['snr_db']:.1f} dB")
        chunk(_("AFC"), _("on") if d["afc"] else _("off"), "white")
        chunk(_("SQUELCH"), _("closed") if d["squelched"] else _("open"),
              "dim" if d["squelched"] else "bold green")
        text.append("\n")
        text.append(f"{_('LEVEL')} ", style="dim")
        cells = round(d["level"] * 30)
        text.append("█" * cells, style="bold green" if not d["squelched"] else "dim")
        text.append("░" * (30 - cells), style="dim")
        text.append("\n")
        text.append(f"{_('PITCH')} ", style="dim")
        text.append(f"{st.get('pitch', '-')} Hz", style="white")
        text.append("   ")
        _append_keys(text, _("PgUp/PgDn tune · /scan search · F10 skimmer"))
        return text


class StatsFooter(Static):
    """UTC clock, counters and the keys (as HAMRLOG's footer)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rx_chars = 0
        self.spots = 0

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Text:
        now = dt.datetime.now(dt.timezone.utc)
        text = Text(no_wrap=True)
        text.append(f" {now:%Y-%m-%d %H:%M:%S} UTC ", style=glyphs.style("bold black on rgb(120,180,255)"))
        for label, value in ((_("RX chars"), self.rx_chars), (_("spots"), self.spots)):
            text.append("   ")
            text.append(f"{label} ", style="dim")
            text.append(str(value), style="bold")
        width = self.size.width
        for hint in (_(HELP_HINT), _(SHORT_HINT)):
            padding = width - text.cell_len - len(hint) - 1
            if padding >= 2:
                text.append(" " * padding)
                _append_keys(text, hint)
                return text
        return text


KEYS = [
    N_("Tab / Shift+Tab         choose a skimmer station; Enter tunes it"),
    N_("F10 skimmer · F11 channels · F2-F9 macros · PgUp/PgDn tune"),
    N_("Ctrl+P / Ctrl+N         earlier lines · F1 this help · Ctrl+Q back"),
]


class CwApp(ChannelSaving, App[int]):
    CSS = css("ic705suite.apps.cw")
    TITLE = "ic705suite CW"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("f1", "help", show=False, priority=True),
        *(Binding(f"f{n}", f"command('/macro F{n}')", show=False, priority=True)
          for n in range(2, 10)),
        Binding("f10", "command('/skim')", show=False, priority=True),
        Binding("f11", "channels", show=False, priority=True),
        Binding("escape", "command('/stop')", show=False),
        Binding("pageup", "command('/up 100')", show=False),
        Binding("pagedown", "command('/down 100')", show=False),
        # No cursor keys: Tab / Shift+Tab choose a skimmer station, Ctrl+P / Ctrl+N recall
        # earlier command lines.
        Binding("tab", "station(1)", show=False, priority=True),
        Binding("shift+tab", "station(-1)", show=False, priority=True),
        Binding("ctrl+p", "recall(-1)", show=False, priority=True),
        Binding("ctrl+n", "recall(1)", show=False, priority=True),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link, startup: list[str] | None = None,
                 scp: set[str] | None = None, owns_link: bool = True) -> None:
        super().__init__()
        self.link = link
        self.owns_link = owns_link
        self.scp = scp or set()
        self.startup = startup or []
        self.state: dict = {}
        self.welcome: dict = {}
        self.exit_code = 0
        self.interpreter = Interpreter(scp=self.scp)
        self.state_frequency: int | None = None
        self._history: list[str] = []
        self._history_index = 0

    # ---- layout ------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield StatusLine(self.link.description, id="statusline")
        with Vertical(id="main-frame"):
            yield TextPane(id="rx")
            yield DataTable(id="skim", cursor_type="row", zebra_stripes=False)
        with Vertical(id="meaning-frame"):
            yield TextPane(id="meaning")
        yield DetailPanel(id="detail")
        with Vertical(id="entry"):
            with Horizontal(id="entry-row"):
                yield Label(_("COMMAND"), id="entry-label")
                yield Input(id="entry-input", placeholder=_("type /help for the commands"))
        yield StatsFooter(id="stats")

    def on_mount(self) -> None:
        table = self.query_one("#skim", DataTable)
        table.add_columns("#", _("QRG"), _("Call"), _("WPM"), _("SNR"), _("Text"))
        table.can_focus = False
        table.display = False
        self.query_one("#main-frame").border_title = _("Receive")
        self.query_one("#detail").border_title = _("Detail")
        self.query_one("#meaning-frame").border_title = _("Translation")
        self.query_one("#meaning", TextPane).add(
            "glossary", _("The meaning of each transmission appears here once it ends."))
        self.set_interval(1.0, self._interpreter_tick)
        self._update_entry_title()
        self.query_one("#entry-input", Input).focus()
        self.link.attach(self._on_event)
        self.run_worker(self._connect(), exclusive=True)

    async def _connect(self) -> None:
        try:
            await self.link.connect()
        except (LinkError, TransportError, RigError, OSError) as exc:
            self._message("error", _("cannot connect: {error}").format(error=exc))
            self._message("info", _("press Ctrl+Q to leave"))
            self.exit_code = 1
            return
        try:
            await self.link.call("cw.activate")  # the radio goes to CW while this is open
        except (LinkError, OSError) as exc:
            self._message("error", str(exc))
        for line in self.startup:
            await self.link.command(line)
        await self.link.closed.wait()
        self._message("error", _("the link to the core has ended; press Ctrl+Q to leave"))

    # ---- events from the core ------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        pane = self.query_one("#rx", TextPane)
        if kind == "state":
            self._apply_state(event)
        elif kind == "skim":
            self._apply_skim(event["channels"])
        elif kind == "rx":
            pane.add("rx", event["text"], append=True)
            for result in self.interpreter.feed(event["text"]):
                self._show_meaning(result)
            footer = self.query_one("#stats", StatsFooter)
            footer.rx_chars += len(event["text"].strip())
        elif kind == "tx":
            pane.add("tx", f"TX> {event['text']}")
        elif kind in ("info", "error"):
            spot = str(event.get("msgid", "")).startswith("spot ")
            self._message("spot" if spot else kind, render(event))
        elif kind == "welcome":
            self.welcome = event
            self._update_entry_title()
        elif kind == "history":
            # Picked from the skimmer: what was heard before keeps the thread of the QSO.
            pane.add("history", "── " + _("earlier on {title}").format(title=event["title"]) + " ──")
            pane.add("history", event["text"][-600:])

    def _interpreter_tick(self) -> None:
        result = self.interpreter.tick()
        if result is not None:
            self._show_meaning(result)

    def _show_meaning(self, result: Interpretation) -> None:
        pane = self.query_one("#meaning", TextPane)
        if result.summary:
            pane.add("summary", "≈ " + ". ".join(result.summary) + ".")
        if result.glossary:
            pane.add("glossary", "   " + " · ".join(f"{w} = {m}" for w, m in result.glossary))

    def _message(self, kind: str, text: str) -> None:
        self.query_one("#rx", TextPane).add(kind, text)
        entry = self.query_one("#entry")
        entry.border_subtitle = Text(text[:200], style=STYLES.get(kind, ""))

    def _apply_state(self, state: dict) -> None:
        was_skimming = self.state.get("skimming", False)
        self.state = state
        status = self.query_one("#statusline", StatusLine)
        status.state = state
        status.refresh()
        detail = self.query_one("#detail", DetailPanel)
        detail.state = state
        detail.refresh()
        self.query_one("#stats", StatsFooter).spots = state.get("spots", 0)
        if state.get("frequency") != self.state_frequency:
            self.state_frequency = state.get("frequency")
            self.interpreter = Interpreter(scp=self.scp)  # a new frequency is a new conversation
        if state.get("skimming") != was_skimming:
            skimming = state.get("skimming", False)
            self.query_one("#rx").display = not skimming
            self.query_one("#meaning-frame").display = not skimming
            self.query_one("#skim").display = skimming
            frame = self.query_one("#main-frame")
            frame.border_title = _("Skimmer") if skimming else _("Receive")
            if not skimming:
                self._apply_skim([])
        self._update_entry_title()

    def _apply_skim(self, channels: list[dict]) -> None:
        table = self.query_one("#skim", DataTable)
        detail = self.query_one("#detail", DetailPanel)
        row = table.cursor_row if table.row_count else 0
        # Newest text matters most: show the tail that fits beside the other columns.
        room = max(10, table.size.width - 40)
        table.clear()
        for ch in channels:
            call = Text(ch["callsign"] or "-", style="bold cyan" if ch["callsign"] else "dim")
            if ch.get("known"):
                call.append(" ✓", style="green")
            table.add_row(str(ch["index"]), bands.format_frequency(ch["frequency"]), call,
                          f"{ch['wpm']:.0f}", f"{ch['snr_db']:.0f}", ch["text"][-room:])
        if channels:
            table.move_cursor(row=min(row, len(channels) - 1))
        detail.channels = channels
        detail.selected = table.cursor_row if channels else 0
        self._update_selected_meaning()

    def _update_selected_meaning(self) -> None:
        detail = self.query_one("#detail", DetailPanel)
        detail.meaning = ""
        if 0 <= detail.selected < len(detail.channels):
            words = detail.channels[detail.selected]["text"].split()[1:][-15:]  # first is cut
            detail.meaning = ". ".join(interpret(words, self.scp).summary)
        detail.refresh()

    def _update_entry_title(self) -> None:
        tx = self.state.get("tx_enabled", self.welcome.get("tx_enabled", False))
        self.query_one("#entry").border_title = _("Transmit") if tx else _("Command (receive only)")
        self.query_one("#entry-label", Label).update(_("SEND") if tx else _("COMMAND"))

    # ---- input --------------------------------------------------------------------------------------

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        line = message.value.strip()
        message.input.value = ""
        if not line:
            if self.state.get("skimming"):
                table = self.query_one("#skim", DataTable)
                if table.row_count:
                    await self.action_command(f"/go {table.cursor_row + 1}")
            return
        if not self._history or self._history[-1] != line:
            self._history = (self._history + [line])[-LINE_HISTORY:]
        self._history_index = len(self._history)
        if line.lower() in ("/quit", "/q", "/exit"):
            await self.action_leave()
        elif line.lower() in ("/help", "/h", "/?"):
            self.action_help()
        elif line.lower() == "/clear":
            self.query_one("#rx", TextPane).clear()
        else:
            await self.action_command(line)

    def action_help(self) -> None:
        """The commands in a window of their own: the message pane is hidden while skimming."""
        if not isinstance(self.screen, TextScreen):
            self.push_screen(TextScreen(_("CW commands"),
                                        [_(row) for row in HELP] + [""] + [_(row) for row in KEYS]))

    async def action_command(self, line: str) -> None:
        self.run_worker(self.link.command(line))

    def action_station(self, step: int) -> None:
        if not self.state.get("skimming"):
            return
        table = self.query_one("#skim", DataTable)
        if table.row_count:
            table.move_cursor(row=(table.cursor_row + step) % table.row_count)
            detail = self.query_one("#detail", DetailPanel)
            detail.selected = table.cursor_row
            self._update_selected_meaning()

    def action_recall(self, step: int) -> None:
        if not self._history:
            return
        self._history_index = max(0, min(len(self._history), self._history_index + step))
        field = self.query_one("#entry-input", Input)
        field.value = self._history[self._history_index] if self._history_index < len(self._history) else ""
        field.cursor_position = len(field.value)

    def action_channels(self) -> None:
        if not isinstance(self.screen, ChannelsScreen):
            self.push_screen(ChannelsScreen())

    async def action_leave(self) -> None:
        self.run_worker(self._leave(), exclusive=True, group="leave")

    async def _leave(self) -> None:
        """Standalone: offer to archive the unsaved channels, then quit. Inside the suite:
        back to the dashboard (the channels stay in the core; leaving the suite asks)."""
        if self.owns_link:
            if not await self.offer_save_channels():
                return
            await self.link.close()
        else:
            self.link.detach()
        self.exit(self.exit_code)
