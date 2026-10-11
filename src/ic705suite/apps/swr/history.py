"""The history of a band (or a preset) for the active antenna, in the terminal: every sweep and
saved position, newest first. Enter shows one: its details and its curve, and there its
position and note can be changed and a photo added, replaced, opened or removed."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ic705suite.apps.swr.photos import PhotoError, download, open_with_viewer, read_photo, \
    save_copy, upload
from ic705suite.i18n import N_, _
from ic705suite.rig.bands import format_frequency
from ic705suite.station.links import Link
from ic705suite.ui.widgets import ChoiceScreen, NameScreen

KINDS = {"swr": N_("SWR sweep"), "noise": N_("Noise sweep"), "final": N_("Position saved")}


def kind_text(entry: dict) -> str:
    return _(KINDS.get(entry.get("kind", ""), entry.get("kind", "")))


def best_text(entry: dict) -> str:
    best = entry.get("best")
    if best is None:
        return ""
    return f"{best:.0f}" if entry.get("kind") == "noise" else f"{best:.2f}"


def coil_text(entry: dict) -> str:
    """The position, as typed (older histories kept it as text, maybe with a comma)."""
    coil = entry.get("coil")
    if coil is None or coil == "":
        return ""
    try:
        return f"{float(str(coil).replace(',', '.')):g}"
    except ValueError:
        return str(coil)


def marks(entry: dict) -> str:
    """What else an entry has: a photo, a note, a problem."""
    return ("📷" if entry.get("photo") else "") + ("✎" if entry.get("note") else "") + \
        ("⚠" if entry.get("error") or entry.get("warning") else "")


def mine(entries: list[dict], row: dict) -> list[dict]:
    """The entries of a row of the list: the band's own, or the preset's."""
    return [e for e in entries if (e.get("preset") or None) == (row.get("preset") or None)]


async def edit_position(app, link: Link, entry: dict) -> str | None:
    """Ask for the entry's position; '' error text or None when nothing changed."""
    coil = await app.push_screen_wait(NameScreen(
        _("Position"), _("Which number was the coil (or adjustment) on? (empty: none)"),
        value=coil_text(entry), help_text=_("Enter saves · Esc cancels")))
    if coil is None:
        return None
    reply = await link.call("swr.entry.update", id=entry["id"], coil=coil)
    return reply.get("error", "")


async def edit_note(app, link: Link, entry: dict) -> str | None:
    note = await app.push_screen_wait(NameScreen(
        _("Note"), _("A note on this measurement (the weather, what was changed...)"),
        value=entry.get("note", ""), help_text=_("Enter saves · Esc cancels")))
    if note is None:
        return None
    reply = await link.call("swr.entry.update", id=entry["id"], note=note)
    return reply.get("error", "")


class HistoryScreen(ModalScreen[None]):
    """↑↓ choose · Enter details · D delete · Esc back."""

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("enter", "open", show=False, priority=True),
        Binding("d", "delete", show=False),
    ]

    def __init__(self, link: Link, row: dict, label: str) -> None:
        super().__init__()
        self.link, self.row, self.label = link, row, label
        self.entries: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal wide") as box:
            box.border_title = _("History of {band}").format(band=self.label)
            yield DataTable(id="history", cursor_type="row", zebra_stripes=False)
            yield Static(id="history-notes", classes="modal-subtitle")
            yield Static(_("↑↓ choose · Enter details · D delete · Esc back"),
                         classes="modal-help")

    async def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(_("Date"), _("What"), _("Position"), _("Resonance"), _("Best"), "")
        table.styles.height = "auto"
        table.styles.max_height = 16
        await self._load()
        table.focus()

    async def _load(self, keep: int | None = None) -> None:
        reply = await self.link.call("swr.history", band=self.row.get("band", self.row["name"]))
        self.entries = mine(reply.get("history", []), self.row)
        table = self.query_one(DataTable)
        row = table.cursor_row if table.row_count else 0
        table.clear()
        for i, entry in enumerate(self.entries):
            table.add_row(entry["ts"][5:16], kind_text(entry),
                          Text(coil_text(entry), justify="right"),
                          Text(format_frequency(entry["resonance"]) if entry.get("resonance")
                               else "", justify="right"),
                          Text(best_text(entry), justify="right"), marks(entry))
            if entry["id"] == keep:
                row = i
        if self.entries:
            table.move_cursor(row=min(row, len(self.entries) - 1))
        self.query_one("#history-notes", Static).update(Text(
            _("Every sweep and saved position, the newest first (📷 photo, ✎ note).")
            if self.entries else _("Nothing yet: the sweeps and saved positions appear here."),
            style="dim"))

    def _chosen(self) -> dict | None:
        table = self.query_one(DataTable)
        if 0 <= table.cursor_row < len(self.entries):
            return self.entries[table.cursor_row]
        return None

    def action_open(self) -> None:
        entry = self._chosen()
        if entry is not None:
            self.run_worker(self._open(entry), exclusive=True)

    async def _open(self, entry: dict) -> None:
        await self.app.push_screen_wait(EntryScreen(self.link, entry["id"], self.label))
        await self._load(keep=entry["id"])

    def action_delete(self) -> None:
        entry = self._chosen()
        if entry is not None:
            self.run_worker(self._delete(entry), exclusive=True)

    async def _delete(self, entry: dict) -> None:
        if await confirm_delete(self.app, entry):
            reply = await self.link.call("swr.entry.delete", id=entry["id"])
            if reply.get("error"):
                self.query_one("#history-notes", Static).update(
                    Text(reply["error"], style="bold red"))
            await self._load()


async def confirm_delete(app, entry: dict) -> bool:
    answer = await app.push_screen_wait(ChoiceScreen(
        _("Delete from the history"),
        _("Delete from the history: {what}, {date}? Its photo goes with it.").format(
            what=kind_text(entry), date=entry["ts"][:16]),
        [("no", _("Keep it")), ("yes", _("Delete"))]))
    return answer == "yes"


class EntryScreen(ModalScreen[None]):
    """One entry: details and curve. C position · O note · F photo from a file · V view
    the photo · X remove the photo · D delete · Esc back."""

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("c", "position", show=False),
        Binding("o", "note", show=False),
        Binding("f", "photo", show=False),
        Binding("v", "view", show=False),
        Binding("x", "remove_photo", show=False),
        Binding("d", "delete", show=False),
    ]

    def __init__(self, link: Link, entry_id: int, label: str) -> None:
        super().__init__()
        self.link, self.entry_id, self.label = link, entry_id, label
        self.entry: dict = {}

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal wide") as box:
            box.border_title = _("History of {band}").format(band=self.label)
            with VerticalScroll(id="entry-scroll"):
                yield Static(id="entry-details")
            yield Static(id="entry-message", classes="modal-subtitle")
            yield Static(_("C position · O note · F photo from a file · V view photo · "
                           "X remove photo · D delete · Esc back"), classes="modal-help")

    async def on_mount(self) -> None:
        scroll = self.query_one("#entry-scroll")
        scroll.styles.height = "auto"
        scroll.styles.max_height = 24
        await self._load()

    async def _load(self) -> None:
        reply = await self.link.call("swr.entry", id=self.entry_id)
        if reply.get("error"):
            self._message(reply["error"])
            return
        self.entry = reply["entry"]
        self.query_one("#entry-details", Static).update(details(self.entry))

    def _message(self, text: str, error: bool = True) -> None:
        self.query_one("#entry-message", Static).update(
            Text(text, style="bold red" if error else "cyan"))

    async def _after(self, error: str | None, done: str) -> None:
        if error is None:
            return
        if error:
            self._message(error)
            return
        await self._load()
        self._message(done, error=False)

    def action_position(self) -> None:
        self.run_worker(self._position(), exclusive=True)

    async def _position(self) -> None:
        await self._after(await edit_position(self.app, self.link, self.entry), _("Saved."))

    def action_note(self) -> None:
        self.run_worker(self._note(), exclusive=True)

    async def _note(self) -> None:
        await self._after(await edit_note(self.app, self.link, self.entry), _("Saved."))

    def action_photo(self) -> None:
        self.run_worker(self._photo(), exclusive=True)

    async def _photo(self) -> None:
        path = await self.app.push_screen_wait(NameScreen(
            _("Photo"), _("Path of the photo (JPEG, PNG, WebP or GIF; it replaces the one "
                          "there is)"),
            placeholder="~/Pictures/antenna.jpg", help_text=_("Enter sends · Esc cancels")))
        if not path or not path.strip():
            return
        try:
            self._message(_("Sending the photo..."), error=False)
            await upload(self.link, self.entry_id, read_photo(path))
        except (PhotoError, OSError) as exc:
            self._message(str(exc))
            return
        await self._load()
        self._message(_("Photo saved."), error=False)

    def action_view(self) -> None:
        self.run_worker(self._view(), exclusive=True)

    async def _view(self) -> None:
        if not self.entry.get("photo"):
            self._message(_("No photo yet: F adds one."))
            return
        try:
            found = await download(self.link, self.entry_id)
            if found is None:
                self._message(_("No photo yet: F adds one."))
                return
            open_with_viewer(save_copy(self.entry_id, *found))
        except (PhotoError, OSError) as exc:
            self._message(str(exc))
            return
        self._message(_("Photo opened in the image viewer."), error=False)

    def action_remove_photo(self) -> None:
        if self.entry.get("photo"):
            self.run_worker(self._remove_photo(), exclusive=True)

    async def _remove_photo(self) -> None:
        reply = await self.link.call("swr.photo.delete", id=self.entry_id)
        await self._after(reply.get("error", ""), _("Photo removed."))

    def action_delete(self) -> None:
        self.run_worker(self._delete(), exclusive=True)

    async def _delete(self) -> None:
        if await confirm_delete(self.app, self.entry):
            reply = await self.link.call("swr.entry.delete", id=self.entry_id)
            if reply.get("error"):
                self._message(reply["error"])
                return
            self.dismiss()


def details(entry: dict) -> Text:
    """The facts of an entry, its note and photo, and its curve."""
    from ic705suite.apps.swr.app import sweep_bars

    text = details_facts(entry)
    if entry.get("points"):
        text.append("\n")
        text.append_text(sweep_bars(entry["kind"], entry["points"], entry.get("target")))
    return text


def details_facts(entry: dict) -> Text:
    """The facts of an entry, its note and whether it has a photo."""
    text = Text()
    text.append(f"{kind_text(entry)} · {entry.get('band', '')} · {entry['ts'][:16]}\n",
                style="bold cyan")

    def fact(label: str, value: str, style: str = "bold white") -> None:
        text.append(f"{label}: ", style="dim")
        text.append(value + "\n", style=style)

    if entry.get("target"):
        fact(_("Target"), f"{format_frequency(entry['target'])} MHz")
    fact(_("Position"), coil_text(entry) or "—")
    if entry.get("resonance"):
        fact(_("Resonance"), f"{format_frequency(entry['resonance'])} MHz"
             + (" (" + _("at the edge") + ")" if entry.get("at_edge") else ""))
    if entry.get("best") is not None:
        fact(_("Noise peak") if entry.get("kind") == "noise" else _("Lowest SWR"),
             best_text(entry))
    analysis = entry.get("analysis") or {}
    if analysis.get("delta_khz") is not None and entry.get("resonance"):
        fact(_("Off target"), f"{analysis['delta_khz']:+.0f} kHz")
    if entry.get("points") and not entry.get("confident", True) and not entry.get("error"):
        fact(_("Result"), _("no clear resonance"), "yellow")
    if entry.get("error"):
        fact(_("Problem"), _("stopped") if entry["error"] == "stopped" else entry["error"],
             "yellow")
    if entry.get("warning") == "no_power":
        fact(_("Problem"), _("the radio was hardly giving power"), "yellow")
    fact(_("Note"), entry.get("note") or "—", "white")
    fact(_("Photo"), _("yes (V opens it)") if entry.get("photo") else _("none (F adds one)"),
         "white")
    if not entry.get("points"):
        text.append(_("No curve kept for this one (measured before version 0.3.1, or a "
                      "position saved without a sweep).") + "\n", style="dim")
    return text
