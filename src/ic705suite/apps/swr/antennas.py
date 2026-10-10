"""The saved antennas: choose the active one, add, edit or delete them (keyboard only)."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ic705suite.i18n import N_, _
from ic705suite.station.links import Link
from ic705suite.ui.widgets import ChoiceScreen, NameScreen

SIGNS = {-1: N_("a higher number lowers the frequency"),
         1: N_("a higher number raises the frequency"),
         0: N_("not known yet (learnt from the saved positions)")}


def settings_text(settings: dict) -> str:
    """'40m 12.2 · 20m 4.5': where the antenna was left on each band."""
    return " · ".join(f"{band} {entry['coil']:g}" for band, entry in settings.items())


class AntennaScreen(ModalScreen[None]):
    """↑↓ choose · Enter makes it the active one · N new · E edit · D delete · Esc back."""

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("enter", "select", show=False, priority=True),
        Binding("n", "new", show=False),
        Binding("e", "edit", show=False),
        Binding("d", "delete", show=False),
    ]

    def __init__(self, link: Link) -> None:
        super().__init__()
        self.link = link
        self.antennas: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal wide") as box:
            box.border_title = _("Antennas")
            yield DataTable(id="antennas", cursor_type="row", zebra_stripes=False)
            yield Static(id="antenna-notes", classes="modal-subtitle")
            yield Static(_("↑↓ choose · Enter use it · N new · E edit · D delete · Esc back"),
                         classes="modal-help")

    async def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", _("Antenna"), _("Saved settings"))
        table.styles.height = "auto"
        table.styles.max_height = 14
        await self._load()
        table.focus()

    async def _load(self, keep: int | None = None) -> None:
        reply = await self.link.call("swr.antennas")
        self.antennas = reply.get("antennas", [])
        table = self.query_one(DataTable)
        table.clear()
        row = 0
        for i, antenna in enumerate(self.antennas):
            mark = Text("●", style="bold green") if antenna["active"] else Text(" ")
            table.add_row(mark, Text(antenna["name"], style="bold" if antenna["active"] else ""),
                          settings_text(antenna.get("settings", {})) or "–")
            if antenna["id"] == keep or keep is None and antenna["active"]:
                row = i
        if self.antennas:
            table.move_cursor(row=row)
        self._show_notes()

    def _chosen(self) -> dict | None:
        table = self.query_one(DataTable)
        if 0 <= table.cursor_row < len(self.antennas):
            return self.antennas[table.cursor_row]
        return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_notes()

    def _show_notes(self) -> None:
        antenna = self._chosen()
        text = Text()
        if antenna:
            if antenna["notes"]:
                text.append(antenna["notes"] + "\n", style="white")
            text.append(_("Adjustment") + ": ", style="dim")
            text.append(_(SIGNS.get(antenna["coil_sign"], SIGNS[0])), style="white")
        self.query_one("#antenna-notes", Static).update(text)

    def _message(self, text: str) -> None:
        self.query_one("#antenna-notes", Static).update(Text(text, style="bold red"))

    async def action_select(self) -> None:
        antenna = self._chosen()
        if antenna is None:
            return
        await self.link.call("swr.antenna.select", id=antenna["id"])
        self.dismiss()

    def action_new(self) -> None:
        self.run_worker(self._edit(None), exclusive=True)

    def action_edit(self) -> None:
        antenna = self._chosen()
        if antenna is not None:
            self.run_worker(self._edit(antenna), exclusive=True)

    async def _edit(self, antenna: dict | None) -> None:
        title = _("Edit antenna") if antenna else _("New antenna")
        name = await self.app.push_screen_wait(NameScreen(
            title, _("Name (make and model, or how you call it)"),
            placeholder="Komunica HF-PRO-2-PLUS-T", value=antenna["name"] if antenna else "",
            help_text=_("Enter goes on · Esc cancels")))
        if not name or not name.strip():
            return
        notes = await self.app.push_screen_wait(NameScreen(
            title, _("Notes (optional): where it is, radials, what the numbers mean..."),
            value=antenna["notes"] if antenna else "",
            help_text=_("Enter goes on · Esc cancels")))
        if notes is None:
            return
        sign = await self.app.push_screen_wait(ChoiceScreen(
            title, _("How does its adjustment (coil, tap, length) move the resonance?"),
            [("-1", _("Higher number, lower frequency")),
             ("1", _("Higher number, higher frequency")),
             ("0", _("Not known yet"))]))
        if not sign:
            return
        params = {"name": name, "notes": notes, "coil_sign": int(sign)}
        if antenna:
            params["id"] = antenna["id"]
        reply = await self.link.call("swr.antenna.save", **params)
        if reply.get("error"):
            self._message(reply["error"])
            return
        saved = next((a for a in reply.get("antennas", []) if a["name"] == name.strip()), None)
        await self._load(keep=saved["id"] if saved else None)

    def action_delete(self) -> None:
        antenna = self._chosen()
        if antenna is not None:
            self.run_worker(self._delete(antenna), exclusive=True)

    async def _delete(self, antenna: dict) -> None:
        answer = await self.app.push_screen_wait(ChoiceScreen(
            _("Delete antenna"),
            _("Delete {name} and all its sweeps and saved settings?").format(name=antenna["name"]),
            [("no", _("Keep it")), ("yes", _("Delete"))]))
        if answer != "yes":
            return
        reply = await self.link.call("swr.antenna.delete", id=antenna["id"])
        if reply.get("error"):
            self._message(reply["error"])
            return
        await self._load()
