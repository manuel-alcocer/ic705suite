"""Fast entry panel: the only widget the operator touches during a session.

Each field of the active profile gets its own box, laid out on one line. Tab
moves to the next, Shift+Tab to the previous, Enter logs the QSO from
wherever the cursor is. Splitting the line into boxes means the operator sees
which value goes where instead of counting separators.

The panel has two states. On the insert row it is this form. On a logged QSO
it becomes a bar of actions (delete, edit, repeat), because the arrows have
turned the screen into a log browser and letters would otherwise be ambiguous.
Editing brings the form back, filled with that QSO, plus a second row for the
frequency and mode the entry line normally inherits from the session. The band
has no box: it follows from the frequency.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Input, Label, Static

from ...i18n import N_, _

#: Keys that act on the QSO under the cursor while browsing the log.
BROWSE_ACTIONS: dict[str, str] = {
    "d": "delete", "e": "edit", "r": "repeat", " ": "mark", "*": "default", "v": "view",
}

#: The only boxes an edit of several QSOs at once offers.
BULK_FIELDS: tuple[str, ...] = ("freq_hz", "mode", "equipment")

#: What the entry line offers while the cursor sits on a logged QSO. The
#: prompts and help lines below are translated where they are shown.
BROWSE_PROMPT = N_(
    "V view · D delete · E edit · R repeat · Space mark · Ctrl+A all · ↓ back to typing"
)

#: Offered on a second row while editing, unless the entry line has them.
#: The band is not among them: the application works it out from the frequency.
EDIT_EXTRA_FIELDS: tuple[str, ...] = ("freq_hz", "mode", "equipment")

#: Short label and box width per field. Width None means "take what is left",
#: so the free-text field grows with the terminal.
FIELD_LAYOUT: dict[str, tuple[str, int | None]] = {
    "call": (N_("CALL"), 13),
    "name": (N_("NAME"), 14),
    "rst_sent": (N_("SENT"), 5),
    "rst_rcvd": (N_("RCVD"), 5),
    "qth": ("QTH", 14),
    "gridsquare": (N_("GRID"), 8),
    "comment": (N_("NOTES"), None),
    "band": (N_("BAND"), 7),
    "mode": (N_("MODE"), 8),
    "freq_hz": (N_("FREQ"), 13),
    "power_w": (N_("PWR"), 5),
    "talkgroup": ("TG", 8),
    "reflector": ("REFL", 10),
    "room": ("ROOM", 10),
    "network": (N_("NET"), 10),
    # Inventory view (F2).
    "brand": (N_("BRAND"), 12),
    "rig": (N_("MODEL"), 14),
    "types": (N_("TYPES"), 12),
    "bands": (N_("BANDS"), 20),
    "voltage_v": (N_("VOLTAGE"), 6),
    "current_a": (N_("CURRENT"), 6),
    "stations": (N_("RADIOS"), 22),
    "antennas": (N_("ANTENNAS"), 22),
    "supplies": (N_("SUPPLIES"), 16),
    # Equipment set of a logged QSO, offered only while editing it.
    "equipment": (N_("SETUP"), 22),
    # Address book view (F3).
    "first_name": (N_("NAME"), 14),
    "last_name": (N_("SURNAME"), 16),
    "dmr_id": (N_("DMR ID"), 9),
    "city": (N_("CITY"), 14),
    "state": (N_("PROVINCE"), 14),
    "country": (N_("COUNTRY"), 14),
    "email": (N_("EMAIL"), 22),
    "notes": (N_("NOTES"), None),
    # Profiles view (F4).
    "slot": ("CTRL", 4),
    "operator": (N_("OPERATOR"), 12),
    "repeater": (N_("REPEATER"), 12),
    "digital": (N_("DIGITAL"), None),
    # Repeaters view (F5).
    "output": (N_("OUTPUT"), 12),
    "input": (N_("INPUT"), 12),
    "tone": (N_("TONE"), 7),
    "ure_number": ("URE", 6),
    "channel": (N_("CHANNEL"), 8),
    "owner": (N_("OWNER"), 24),
    # Tools view (F8): the filter box of a read-only list.
    "filter": (N_("FILTER"), None),
    # Settings view (F9).
    "value": (N_("VALUE"), None),
}

DEFAULT_LAYOUT: tuple[str, int | None] = (N_("FIELD"), 12)


class EntryField(Input):
    """One box of the entry line.

    The arrow keys drive the history panel from here, so browsing the log
    never requires leaving the form or moving the focus out of it.
    """

    BINDINGS = [
        Binding("up", "move_history(-1)", N_("Up the log"), show=False),
        Binding("down", "move_history(1)", N_("Down the log"), show=False),
        Binding("pageup", "move_history(-10)", N_("Up 10"), show=False),
        Binding("pagedown", "move_history(10)", N_("Down 10"), show=False),
        Binding("ctrl+up", "recall(-1)", N_("Previous entry"), show=False),
        Binding("ctrl+down", "recall(1)", N_("Next entry"), show=False),
    ]

    @dataclass
    class Recall(Message):
        """Request for the previous (-1) or next (+1) entry typed."""

        direction: int

    @dataclass
    class MoveHistory(Message):
        """Request to move the history cursor by ``delta`` rows."""

        delta: int

    def __init__(self, field_name: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.field_name = field_name

    def action_recall(self, direction: int) -> None:
        self.post_message(self.Recall(direction))

    def action_move_history(self, delta: int) -> None:
        self.post_message(self.MoveHistory(delta))


class BrowseBar(Static, can_focus=True):
    """Action bar on the last row of the main window while browsing a row.

    It belongs to the main window, but the EntryPanel drives it: browsing
    is one of the entry's states, and the bar holds the keyboard meanwhile.
    """

    BINDINGS = [
        Binding("up", "move_history(-1)", N_("Up"), show=False),
        Binding("down", "move_history(1)", N_("Down"), show=False),
        Binding("pageup", "move_history(-10)", N_("Up 10"), show=False),
        Binding("pagedown", "move_history(10)", N_("Down 10"), show=False),
        # This bar holds the keyboard while browsing, so it owns these too.
        Binding("delete", "run('delete')", N_("Delete"), show=False),
        Binding("enter", "run('activate')", N_("Activate"), show=False),
    ]

    @dataclass
    class Action(Message):
        """A key was pressed while browsing: delete, edit, repeat, activate..."""

        action: str

    class UnknownKey(Message):
        """A printable key with no meaning while browsing."""

    def action_move_history(self, delta: int) -> None:
        self.post_message(EntryField.MoveHistory(delta))

    def action_run(self, action: str) -> None:
        self.post_message(self.Action(action))

    prompt: str = BROWSE_PROMPT

    def set_prompt(self, prompt: str) -> None:
        self.prompt = prompt
        self.refresh()

    def render(self) -> Text:
        return Text(f"  {_(self.prompt)}", style="bold")

    async def _on_key(self, event: events.Key) -> None:
        """Letters are actions here, never text.

        D, E and R are ordinary letters in a callsign, so a bar that sometimes
        typed and sometimes deleted would be a trap. Typing stays off and an
        unrecognised key explains where you are.
        """
        character = (event.character or "").lower()
        action = BROWSE_ACTIONS.get(character)
        if action is not None:
            event.prevent_default()
            event.stop()
            self.post_message(self.Action(action))
        elif event.is_printable:
            event.prevent_default()
            event.stop()
            self.post_message(self.UnknownKey())


class EntryPanel(Vertical):
    """The form; while browsing it stays in view, dimmed, holding the draft."""

    class Submitted(Message):
        """Enter was pressed: log what the form holds."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._field_order: tuple[str, ...] = ()
        self._extra_fields: tuple[str, ...] = EDIT_EXTRA_FIELDS
        #: Boxes of a second row that is always part of the form.
        self._second_fields: tuple[str, ...] = ()
        #: Plain text of the message on the frame, empty when there is none.
        self.message = ""
        #: Values put aside while browsing, so it never costs a half-typed QSO.
        self._draft: dict[str, str] = {}
        self._browsing = False
        #: True while the form holds a logged QSO being corrected.
        self._editing = False
        #: True while one edit applies to several marked QSOs.
        self._bulk = False
        #: Pending rebuild of the field boxes, awaited by ``ready()`` so the
        #: caller can focus them without racing the layout.
        self._pending_mount: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Horizontal(id="entry-fields")
        yield Horizontal(id="entry-second")
        yield Horizontal(id="entry-extra")

    def on_mount(self) -> None:
        self._bar.display = False
        self.query_one("#entry-extra", Horizontal).display = False
        self.query_one("#entry-second", Horizontal).display = False

    @property
    def _bar(self) -> BrowseBar:
        """The action bar, which lives in the main window."""
        return self.screen.query_one("#entry-browse", BrowseBar)

    # -------------------------------------------------------------- fields --
    @property
    def fields(self) -> list[EntryField]:
        """The boxes in use: the second row only counts while editing.

        A new QSO takes its frequency and mode from the session. An edit of
        several QSOs only offers BULK_FIELDS.
        """
        if self._editing and self._bulk:
            return [box for box in self.query(EntryField) if box.field_name in BULK_FIELDS]
        return [
            box
            for box in self.query(EntryField)
            if self._editing or not box.has_class("entry-extra-field")
        ]

    @property
    def bulk(self) -> bool:
        return self._editing and self._bulk

    @property
    def browsing(self) -> bool:
        return self._browsing

    @property
    def editing(self) -> bool:
        return self._editing

    def build_fields(
        self,
        field_order: tuple[str, ...],
        extra: tuple[str, ...] = EDIT_EXTRA_FIELDS,
        second: tuple[str, ...] = (),
    ) -> None:
        """Lay out one box per field of the active profile.

        Rebuilt only when the order actually changes, so a status refresh does
        not throw away what is being typed. ``extra`` lists the boxes of a
        row offered only while editing; ``second`` those of a row that is
        always part of the form, for lists with more fields than fit in one.
        """
        if (field_order, extra, second) == (
            self._field_order, self._extra_fields, self._second_fields
        ):
            return
        self._field_order = field_order
        self._extra_fields = extra
        self._second_fields = second
        # Removing is asynchronous, so the new boxes must wait for the old
        # ones to go or their ids would collide. Chained, so two rebuilds in a
        # row never interleave.
        previous = self._pending_mount
        self._pending_mount = asyncio.ensure_future(
            self._rebuild_after(previous, field_order, extra, second)
        )

    async def _rebuild_after(
        self,
        previous: asyncio.Task[None] | None,
        field_order: tuple[str, ...],
        extra: tuple[str, ...],
        second: tuple[str, ...] = (),
    ) -> None:
        if previous is not None:
            await previous
        await self._rebuild(field_order, extra, second)

    def set_suggesters(self, suggesters: dict[str, object]) -> None:
        """Inline completions per box; boxes not named get none."""
        for box in self.query(EntryField):
            box.suggester = suggesters.get(box.field_name)  # type: ignore[assignment]

    async def _rebuild(
        self,
        field_order: tuple[str, ...],
        extra: tuple[str, ...] = EDIT_EXTRA_FIELDS,
        second: tuple[str, ...] = (),
    ) -> None:
        """Replace the boxes with one per field, in order.

        Deliberately does not grab the focus: this runs on every status
        refresh, and stealing the keyboard from a dialog would be a bug. The
        application focuses the form when it means to.
        """
        row = self.query_one("#entry-fields", Horizontal)
        second_row = self.query_one("#entry-second", Horizontal)
        extra_row = self.query_one("#entry-extra", Horizontal)
        await row.remove_children()
        await second_row.remove_children()
        await extra_row.remove_children()

        await row.mount_all(self._boxes(field_order))
        await second_row.mount_all(self._boxes(second))
        second_row.display = bool(second) and row.display
        await extra_row.mount_all(
            self._boxes(
                tuple(name for name in extra if name not in field_order),
                classes="entry-field entry-extra-field",
            )
        )

    @staticmethod
    def _boxes(
        names: tuple[str, ...], classes: str = "entry-field"
    ) -> list[Label | EntryField]:
        widgets: list[Label | EntryField] = []
        for name in names:
            label, width = FIELD_LAYOUT.get(name, DEFAULT_LAYOUT)
            box = EntryField(name, id=f"entry-{name}", classes=classes)
            box.styles.width = width if width is not None else "1fr"
            widgets.append(Label(_(label), classes="entry-label"))
            widgets.append(box)
        return widgets

    async def ready(self) -> None:
        """Wait until the field boxes exist, so they can be focused."""
        pending = self._pending_mount
        self._pending_mount = None
        if pending is not None:
            await pending

    def focus_first(self) -> None:
        """Put the cursor in the first box, which is where a QSO starts."""
        boxes = self.fields
        if boxes:
            boxes[0].focus()

    def values(self) -> dict[str, str]:
        """Current contents, keyed by field name."""
        return {box.field_name: box.value.strip() for box in self.fields}

    def set_values(self, values: dict[str, str]) -> None:
        """Fill the boxes, used by repeat and by entry recall.

        Leaves browse mode first and drops the saved draft: the values being
        written are what the operator asked for, and restoring the draft
        afterwards would undo them.
        """
        self._draft = {}
        self.set_browsing(False)
        for box in self.fields:
            box.value = values.get(box.field_name, "")
        self.focus_first()

    def clear(self) -> None:
        for box in self.fields:
            box.value = ""
        self.focus_first()

    @property
    def first_value(self) -> str:
        """Contents of the leading box, where callsigns and commands go."""
        boxes = self.fields
        return boxes[0].value.strip() if boxes else ""

    def set_first_value(self, text: str) -> None:
        boxes = self.fields
        if boxes:
            boxes[0].value = text

    # ------------------------------------------------------------ browsing --
    def set_browsing(self, browsing: bool) -> None:
        """Switch between filling in a QSO and acting on one."""
        if browsing == self._browsing:
            return
        if self._editing:
            self._leave_edit()
        self._browsing = browsing

        bar = self._bar

        if browsing:
            self._draft = self.values()
            self._dim_form(True)
            bar.display = True
            bar.focus()
        else:
            bar.display = False
            self._dim_form(False)
            for box in self.fields:
                box.value = self._draft.get(box.field_name, "")
            self._draft = {}
            self.focus_first()

    def _dim_form(self, dimmed: bool) -> None:
        """Keep the form in view but out of reach while the bar acts."""
        for box in self.query(EntryField):
            box.disabled = dimmed

    # ------------------------------------------------------------- editing --
    def start_edit(self, values: dict[str, str], *, bulk: bool = False) -> None:
        """Turn the action bar back into the form, holding a logged QSO.

        The draft put aside on entering browse mode is left alone, so it
        still comes back once the cursor returns to the insert row. With
        ``bulk`` the edit covers several QSOs and only BULK_FIELDS can be
        typed in; any other box is shown disabled, or hidden with its row.
        """
        self._editing = True
        self._bulk = bulk
        self._bar.display = False
        main = self.query_one("#entry-fields", Horizontal)
        extra = self.query_one("#entry-extra", Horizontal)
        extra.display = bool(extra.children)
        main.display = not bulk or any(
            box.field_name in BULK_FIELDS for box in main.query(EntryField)
        )
        self._show_second(not bulk)
        for box in self.query(EntryField):
            box.disabled = bulk and box.field_name not in BULK_FIELDS
        for box in self.fields:
            box.value = values.get(box.field_name, "")
        self.focus_first()

    def stop_edit(self) -> None:
        """Back to the action bar, on the same QSO."""
        if not self._editing:
            return
        self._leave_edit()
        for box in self.fields:
            box.value = self._draft.get(box.field_name, "")
        self._dim_form(True)
        bar = self._bar
        bar.display = True
        bar.focus()

    def _leave_edit(self) -> None:
        self._editing = False
        self._bulk = False
        for box in self.query(EntryField):
            box.disabled = False
        self.query_one("#entry-extra", Horizontal).display = False
        self.query_one("#entry-fields", Horizontal).display = True
        self._show_second(True)
        for box in self.query(EntryField):
            box.value = ""

    def reset(self) -> None:
        """Back to an empty form, out of browsing and editing, draft dropped."""
        if self._editing:
            self._leave_edit()
        self._browsing = False
        self._draft = {}
        self._bar.display = False
        self.query_one("#entry-fields", Horizontal).display = True
        self._show_second(True)
        for box in self.query(EntryField):
            box.disabled = False
            box.value = ""

    def _show_second(self, visible: bool) -> None:
        """Show the always-on second row along with the form, if there is one."""
        row = self.query_one("#entry-second", Horizontal)
        row.display = visible and bool(self._second_fields)

    @property
    def pending_values(self) -> dict[str, str]:
        """What a new entry holds, even while browsing has put it aside."""
        if self._browsing:
            return dict(self._draft)
        return self.values()

    def set_browse_prompt(self, prompt: str) -> None:
        self._bar.set_prompt(prompt)

    def focus_input(self) -> None:
        """Return the keyboard to wherever input belongs right now."""
        if self._browsing and not self._editing:
            self._bar.focus()
        else:
            self.focus_first()

    def feedback(self, message: str, level: str = "info") -> None:
        """Show a transient message on the bottom edge of the entry window.

        It sits on the frame rather than on a row of its own, so nothing
        moves when a message comes or goes.

        Args:
            message: Text to show; empty clears it.
            level: One of "info", "ok", "warning", "error", "dup".
        """
        styles = {
            "info": "italic",
            "ok": "bold green",
            "warning": "bold yellow",
            "error": "bold red",
            "dup": "bold black on yellow",
        }
        self.message = message
        self.border_subtitle = (
            Text(f" {message} ", style=styles.get(level, "italic")) if message else None
        )

    @on(Input.Submitted)
    def _on_any_submitted(self, event: Input.Submitted) -> None:
        """Enter logs the QSO from any box, not just the last one."""
        event.stop()
        self.post_message(self.Submitted())
