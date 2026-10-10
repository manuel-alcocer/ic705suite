"""The pieces of the main screen, drawn the way hamrlog draws its own."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import DataTable, Input, Label, RichLog, Static

from ..i18n import N_, _

# ------------------------------------------------------------ status line --


class StatusLine(Static):
    """One line with who we are, where the node is and who we talk to."""

    mycall: reactive[str] = reactive("")
    link_kind: reactive[str] = reactive("")
    host: reactive[str] = reactive("")
    link_state: reactive[str] = reactive("offline")
    radio_port: reactive[int] = reactive(1)
    session: reactive[str] = reactive("")
    monitor: reactive[bool] = reactive(False)

    def render(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")

        def chunk(label: str, value: str, style: str = "bold white") -> None:
            if not value:
                return
            if text.plain:
                text.append(" · ", style="dim")
            if label:
                text.append(f"{label} ", style="dim")
            text.append(value, style=style)

        chunk("OP", self.mycall or _("no callsign"), "bold cyan")
        link_style = {"up": "bold green", "connecting": "bold yellow"}.get(
            self.link_state, "bold red"
        )
        state = {"up": self.host, "connecting": _("connecting")}.get(
            self.link_state, _("offline")
        )
        chunk(_("LINK"), f"{self.link_kind} {state}".strip(), link_style)
        chunk(_("PORT"), str(self.radio_port), "bold yellow")
        chunk("MON", "ON" if self.monitor else "OFF",
              "bold magenta" if self.monitor else "dim")
        if self.session:
            chunk(_("SESSION"), self.session, "bold black on green")
        else:
            chunk(_("SESSION"), _("idle"), "dim")
        return text


# ---------------------------------------------------------------- footer --

#: Shown at the right edge of the footer, translated where it is drawn.
HELP_HINT = N_("F1 Terminal · F2 Monitor · F3 Heard · Ctrl+Q Quit")

#: What is left of it when the terminal cannot hold the whole hint.
SHORT_HINTS = (N_("F1 Terminal · F2 Monitor · F3 Heard"), "F1 · F2 · F3")


@dataclass(slots=True)
class Counters:
    rx_bytes: int = 0
    tx_bytes: int = 0
    frames: int = 0
    heard: int = 0
    session_start: dt.datetime | None = None


def _human_bytes(count: int) -> str:
    if count < 1024:
        return str(count)
    if count < 1024 * 1024:
        return f"{count / 1024:.1f}k"
    return f"{count / (1024 * 1024):.1f}M"


class StatsFooter(Static):
    """Counters plus a live UTC clock, as in hamrlog."""

    counters: reactive[Counters] = reactive(Counters, always_update=True)

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Text:
        now = dt.datetime.now(dt.UTC)
        text = Text(no_wrap=True)
        text.append(f" {now:%Y-%m-%d %H:%M:%S} UTC ", style="bold black on rgb(120,180,255)")

        counters = self.counters

        def chunk(label: str, value: object) -> None:
            text.append("   ")
            text.append(f"{label} ", style="dim")
            text.append(str(value), style="bold")

        chunk("RX", _human_bytes(counters.rx_bytes))
        chunk("TX", _human_bytes(counters.tx_bytes))
        chunk(_("frames"), counters.frames)
        chunk(_("heard"), counters.heard)
        if counters.session_start is not None:
            elapsed = int((now - counters.session_start).total_seconds())
            hours, rest = divmod(elapsed, 3600)
            text.append("   ")
            text.append(f"{hours:02}:{rest // 60:02}:{rest % 60:02}", style="dim yellow")

        # Pad so the hint sits flush against the right edge; on a narrow
        # terminal it gets shorter, and only goes when even that does not fit.
        width = self.size.width
        for hint in (_(HELP_HINT), *(_(short) for short in SHORT_HINTS)):
            padding = width - text.cell_len - len(hint) - 1
            if width and padding >= 2:
                text.append(" " * padding)
                text.append(hint, style="dim")
                text.append(" ")
                break
        return text

    def on_resize(self) -> None:
        self.refresh()


# ------------------------------------------------------------- text views --

#: Control characters other than tab and escape (kept for ANSI colours).
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")

#: Packet stations end lines with CR; some with LF or both.
LINE_BREAK = re.compile(r"\r\n|\n\r|\r|\n")


class TextView(RichLog):
    """A scrolling log of whole lines, fed with text as it arrives.

    Packet text comes in pieces that rarely end at a line break, and prompts
    never do. Complete lines are written at once; what is left waits a moment
    for the rest and is then shown anyway, so a prompt is never hidden.
    """

    #: Seconds to wait for the end of a line before showing it as it is.
    FLUSH_DELAY = 0.4

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
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

    def say(self, message: str, style: str) -> None:
        """A whole line from the application itself."""
        self._flush()
        self.write(Text(message, style=style))


# ------------------------------------------------------------ heard table --

HEARD_COLUMNS: tuple[tuple[str, int | None], ...] = (
    (N_("CALL"), 12), (N_("LAST HEARD"), 21), (N_("FRAMES"), 8), (N_("PORT"), 7),
    (N_("TO"), 12), (N_("VIA"), None),
)


@dataclass(slots=True)
class HeardStation:
    call: str
    last: dt.datetime
    frames: int
    port: int
    dest: str
    via: str


class HeardTable(DataTable):
    """Stations heard on the air, the most recent first."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.can_focus = False
        for label, width in HEARD_COLUMNS:
            self.add_column(Text(_(label), style="bold"), width=width, key=label)

    def load(self, stations: list[HeardStation]) -> None:
        selected = self.selected_call
        self.clear()
        for station in stations:
            self.add_row(
                Text(station.call, style="bold cyan"),
                f"{station.last:%Y-%m-%d %H:%M:%S}",
                str(station.frames),
                str(station.port),
                station.dest,
                station.via,
                key=station.call,
            )
        if selected:
            try:
                self.move_cursor(row=self.get_row_index(selected))
            except Exception:  # noqa: BLE001 - the station may have gone
                pass

    @property
    def selected_call(self) -> str:
        if not self.row_count:
            return ""
        try:
            key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key
        except Exception:  # noqa: BLE001 - empty table
            return ""
        return str(key.value or "")


# ------------------------------------------------------------ entry panel --


class EntryPanel(Vertical):
    """The command line: a prompt saying where the text goes, and one box."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="entry-fields"):
            yield Label(_("CMD"), id="entry-target", classes="entry-label")
            yield Input(id="line", classes="entry-field")
        yield Static("", id="entry-hint")
        yield Static("", id="entry-feedback")

    @property
    def line(self) -> Input:
        return self.query_one("#line", Input)

    def set_target(self, call: str) -> None:
        label = self.query_one("#entry-target", Label)
        label.update(call or _("CMD"))
        label.set_class(bool(call), "-session")

    def show_keys(self, keys: str) -> None:
        self.query_one("#entry-hint", Static).update(Text(f"  {_(keys)}", style="dim italic"))

    def feedback(self, message: str, level: str = "info") -> None:
        """A transient message under the box: info, ok, warning or error."""
        styles = {
            "info": "dim",
            "ok": "bold green",
            "warning": "bold yellow",
            "error": "bold red",
        }
        widget = self.query_one("#entry-feedback", Static)
        widget.update(Text(f"  {message}" if message else "", style=styles.get(level, "dim")))
