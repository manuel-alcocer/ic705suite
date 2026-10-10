"""Dialogs and the channels view of the Textual client (HAMRLOG-style panels)."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ic705suite.rig import bands
from ic705suite.i18n import N_, _
from ic705suite.ui.widgets import ChoiceScreen, NameScreen  # noqa: F401 - re-exported


SOURCES = {"tuned": N_("tuned"), "skimmer": N_("skimmer")}


def stats_text(stats: dict) -> Text:
    text = Text(no_wrap=False)

    def chunk(label: str, value: str, style: str = "bold white") -> None:
        if len(text):
            text.append(" · ", style="dim")
        text.append(f"{label} ", style="dim")
        text.append(value, style=style)

    chunk(_("words"), str(stats.get("words", 0)))
    chunk(_("recognized"), f"{stats.get('recognized_pct', 0):.0f}%", "bold green")
    chunk(_("noise"), f"{stats.get('noise_pct', 0):.0f}%", "bold red" if stats.get("noise_pct", 0) > 30 else "white")
    chunk(_("unreadable"), str(stats.get("garbled", 0)), "white")
    chunk(_("fragments"), str(stats.get("fragments", 0)), "white")
    chunk(_("callsigns"), f"{stats.get('callsigns', 0)} ({stats.get('scp_callsigns', 0)} SCP)", "white")
    chunk(_("transmissions"), str(stats.get("transmissions", 0)), "white")
    if stats.get("wpm"):
        chunk(_("speed"), f"{stats['wpm']:.0f} {_('wpm')}", "white")
    if stats.get("snr_db") is not None:
        chunk("SNR", f"{stats['snr_db']:.0f} dB", "white")
    return text


class ChannelsScreen(Screen):
    """Channels of this session and the archive: statistics, transcript, save, tune, play."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", show=False),
        Binding("f11", "app.pop_screen", show=False),
        Binding("s", "save", show=False),
        Binding("t", "tune", show=False),
        Binding("p", "play", show=False),
        Binding("r", "reload", show=False),
        # Up/Down or Tab / Shift+Tab choose the channel, Enter shows it.
        Binding("up", "select(-1)", show=False),
        Binding("down", "select(1)", show=False),
        Binding("tab", "select(1)", show=False, priority=True),
        Binding("shift+tab", "select(-1)", show=False, priority=True),
        Binding("enter", "show", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, dict]] = []  # ("session"|"saved", channel)
        self.transcript: dict | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="chan-title")
        with Vertical(id="chan-frame"):
            yield DataTable(id="chan-table", cursor_type="row")
        with VerticalScroll(id="chan-detail"):
            yield Static(id="chan-text")
        yield Static(id="chan-hint")

    def on_mount(self) -> None:
        table = self.query_one("#chan-table", DataTable)
        table.add_columns("", _("Name"), _("QRG"), _("Source"), _("Last (UTC)"), _("Words"),
                          _("OK %"), _("Noise %"), _("WPM"), _("Audio"))
        table.can_focus = False
        self.query_one("#chan-frame").border_title = _("Channels")
        self.query_one("#chan-detail").border_title = _("Transcript")
        hint = Text(no_wrap=True)
        for key, label in (("↑↓", _("choose")), ("Enter", _("show")), ("S", _("save")), ("T", _("tune")),
                           ("P", _("play audio")), ("R", _("reload")), ("Esc", _("back"))):
            hint.append(f" {key} ", style="bold")
            hint.append(label + "  ", style="dim")
        self.query_one("#chan-hint", Static).update(hint)
        self.run_worker(self.reload())

    async def reload(self) -> None:
        listing = await self.app.link.call("channels")
        self.rows = [("session", c) for c in reversed(listing.get("session", []))] + \
                    [("saved", c) for c in listing.get("saved", [])]
        title = Text(" " + _("Channels") + " ", style="bold")
        title.append(_("{n} in this session, {m} archived").format(
            n=len(listing.get("session", [])), m=len(listing.get("saved", []))), style="dim")
        if listing.get("error"):
            title.append("  " + _("archive unavailable: {error}").format(error=listing["error"]),
                         style="bold red")
        self.query_one("#chan-title", Static).update(title)
        table = self.query_one("#chan-table", DataTable)
        row = table.cursor_row if table.row_count else 0
        table.clear()
        for kind, c in self.rows:
            s = c.get("stats", {})
            mark = Text("●", style="bold yellow") if kind == "session" and c.get("unsaved") \
                else Text("✓", style="green") if kind == "saved" or c.get("archive_name") else Text("")
            name = c.get("label") or c.get("name")
            source = _(SOURCES.get(c.get("source", ""), N_("archive"))) if kind == "session" \
                else _("archive")
            last = (c.get("last") or c.get("updated") or "")[11:16]
            audio = "♪" if (c.get("audio") and kind == "saved") else ""
            table.add_row(mark, name, bands.format_frequency(c.get("frequency")), source, last,
                          str(s.get("words", 0)), f"{s.get('recognized_pct', 0):.0f}",
                          f"{s.get('noise_pct', 0):.0f}",
                          f"{s['wpm']:.0f}" if s.get("wpm") else "-", audio)
        if self.rows:
            table.move_cursor(row=min(row, len(self.rows) - 1))

    def _selected(self) -> tuple[str, dict] | None:
        table = self.query_one("#chan-table", DataTable)
        if not self.rows or not 0 <= table.cursor_row < len(self.rows):
            return None
        return self.rows[table.cursor_row]

    def action_select(self, step: int) -> None:
        table = self.query_one("#chan-table", DataTable)
        if table.row_count:
            table.move_cursor(row=(table.cursor_row + step) % table.row_count)

    def action_show(self) -> None:
        self.run_worker(self.show())

    async def show(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        kind, c = selected
        reply = await self.app.link.call("transcript", id=c["id"], saved=kind == "saved")
        t = reply.get("transcript")
        self.transcript = t
        text = Text()
        if t is None:
            text.append(_("Not found"), style="bold red")
        else:
            text.append(f"{t.get('name') or ''}  {bands.format_frequency(t.get('frequency'))} MHz\n",
                        style="bold cyan")
            text.append_text(stats_text(t.get("stats", {})))
            text.append("\n\n")
            styles = {"rx": "white", "tx": "bold yellow", "summary": "bold green"}
            for e in t.get("entries", []):
                text.append(f"{e['utc'][11:19]} ", style="dim")
                prefix = {"tx": "TX> ", "summary": "≈ "}.get(e["kind"], "")
                text.append(prefix + e["text"] + "\n", style=styles.get(e["kind"], ""))
            for a in t.get("audio", []):
                text.append(f"♪ {a['path']} ({a['seconds']:.0f} s)\n", style="magenta")
        self.query_one("#chan-text", Static).update(text)

    def action_reload(self) -> None:
        self.run_worker(self.reload())

    def action_save(self) -> None:
        selected = self._selected()
        if selected and selected[0] == "session":
            self.run_worker(self._save(selected[1]))

    async def _save(self, channel: dict) -> None:
        await self.app.save_channel_flow(channel, ask_audio=True)
        await self.reload()

    def action_tune(self) -> None:
        selected = self._selected()
        if selected and selected[1].get("frequency"):
            self.run_worker(self.app.link.command(f"/f {selected[1]['frequency']}"))
            self.app.pop_screen()

    def action_play(self) -> None:
        selected = self._selected()
        if not selected or selected[0] != "saved":
            return

        async def play() -> None:
            reply = await self.app.link.call("transcript", id=selected[1]["id"], saved=True)
            audio = (reply.get("transcript") or {}).get("audio") or []
            if audio:
                await self.app.link.command(f"/play {audio[-1]['path']}")
                self.app.pop_screen()

        self.run_worker(play())
