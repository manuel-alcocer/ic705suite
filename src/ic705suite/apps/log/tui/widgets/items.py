"""The inventory view (F2): a row of tabs over a list, in place of the log.

It behaves like the log on purpose. The list is never focused; the entry
line keeps the keyboard, its arrows move this cursor, and the last row,
``<New ...>``, is where a new item is written.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Static

from ...i18n import _
from ..inventory import Item, Kind

#: Row key of the insert row. Item rows are keyed by their id.
INSERT_ROW_KEY = "__insert__"


class ItemTable(DataTable):
    """Read-only list of one kind of item, ending in its insert row."""

    can_focus = False

    @dataclass
    class SelectionChanged(Message):
        """The cursor moved. ``item`` is None on the insert row."""

        item: Item | None

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._items: dict[str, Item] = {}
        self._has_insert_row = True

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True

    @property
    def item_count(self) -> int:
        return len(self._items)

    def show(self, kind: Kind, items: list[Item], keep_id: int | None = None) -> None:
        """Replace the list, keeping the cursor on ``keep_id`` when given."""
        self.clear(columns=True)
        justify = [
            "right" if label in kind.right_aligned else "left" for label, _width in kind.columns
        ]
        for (label, width), side in zip(kind.columns, justify, strict=True):
            self.add_column(
                Text(_(label), style="bold", justify=side), width=width, key=label
            )
        self._items = {}
        for item in items:
            style = "dim" if item.locked else ""
            self.add_row(
                *(
                    Text(cell or "", style=style, justify=side)
                    for cell, side in zip(item.cells, justify, strict=False)
                ),
                key=str(item.id),
            )
            self._items[str(item.id)] = item
        self._has_insert_row = kind.can_add
        if kind.can_add:
            self._add_insert_row(kind)

        # Without an insert row the cursor starts on the first item.
        target = self.row_count - 1 if kind.can_add else 0
        if keep_id is not None and str(keep_id) in self._items:
            target = self.get_row_index(str(keep_id))
        self.move_cursor(row=target, scroll=True)
        self.post_message(self.SelectionChanged(self.selected_item()))

    def _add_insert_row(self, kind: Kind) -> None:
        # The label goes in the widest column, where it is not cut short,
        # unless the kind names one.
        widest = kind.insert_column
        if widest is None:
            widest = max(
                range(len(kind.columns)), key=lambda index: kind.columns[index][1] or 0
            )
        cells = [Text("") for _ in kind.columns]
        cells[widest] = Text(f"▸ {_(kind.insert_label)}", style="bold green")
        self.add_row(*cells, key=INSERT_ROW_KEY)

    def go_to_insert_row(self) -> None:
        """Back to the insert row; a list without one stays where it is."""
        if not self._has_insert_row:
            return
        if self.row_count:
            self.move_cursor(row=self.row_count - 1, scroll=True)
        self.post_message(self.SelectionChanged(None))

    def on_resize(self) -> None:
        """Keep the cursor in view when the table gets its real size.

        The view is shown and filled in one go, so the cursor is scrolled to
        before the table has settled its height; on a long list that left
        the insert row hidden just below the edge.
        """
        if self.row_count:
            self._scroll_cursor_into_view()

    def move_selection(self, delta: int) -> None:
        if not self.row_count:
            return
        target = max(0, min(self.row_count - 1, self.cursor_row + delta))
        if target != self.cursor_row:
            self.move_cursor(row=target, scroll=True)
        self.post_message(self.SelectionChanged(self.selected_item()))

    @property
    def on_insert_row(self) -> bool:
        return self.selected_item() is None

    def selected_item(self) -> Item | None:
        if not self.row_count:
            return None
        try:
            key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
        except Exception:  # noqa: BLE001 - cursor may be stale after a reload
            return None
        return self._items.get(str(key)) if key != INSERT_ROW_KEY else None


class InventoryView(Vertical):
    """Tab row plus the list of the active tab."""

    def compose(self) -> ComposeResult:
        yield Static("", id="inventory-tabs")
        yield ItemTable(id="inventory-table")

    def set_tabs(self, text: Text) -> None:
        self.query_one("#inventory-tabs", Static).update(text)
