"""The logbook (hamrlog) in the graphical client.

hamrlog's terminal application (apps.log.tui.app.HamrlogApp) holds all of the logbook's
behaviour: the entry line and its /commands, browsing and editing the log, the views (F1-F9),
profiles, repeaters, the address book... This page runs that very code: its methods are
borrowed, with the Textual widgets they ask for replaced by the Qt ones here, which offer the
same methods. So the graphical logbook does exactly what the terminal one does, keys
included; it adds the mouse (pick a row, double click for the QSO card) and the theme.
"""

from __future__ import annotations

from types import SimpleNamespace as Event

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ic705suite.apps.log.core.state import SessionState
from ic705suite.apps.log.i18n import _ as log_
from ic705suite.apps.log.tui import app as log_app
from ic705suite.apps.log.tui.app import LIST_VIEWS, HamrlogApp
from ic705suite.apps.log.tui.inventory import Item
from ic705suite.apps.log.tui.screens.base import ConfirmScreen, FormScreen
from ic705suite.apps.log.tui.screens.card import QsoCardScreen, render_card
from ic705suite.apps.log.tui.widgets.detail import DetailPanel
from ic705suite.apps.log.tui.widgets.entry import (
    BROWSE_ACTIONS,
    BROWSE_PROMPT,
    BULK_FIELDS,
    DEFAULT_LAYOUT,
    EDIT_EXTRA_FIELDS,
    FIELD_LAYOUT,
)
from ic705suite.apps.log.tui.widgets.footer import HELP_HINT, SHORT_HINTS, StatsFooter
from ic705suite.apps.log.tui.widgets.history import (
    COLUMNS,
    DAY_ROW_PREFIX,
    INSERT_LABEL,
    INSERT_ROW_KEY,
    ORDER_NEWEST_FIRST,
    ORDER_OLDEST_FIRST,
    HistoryPanel,
)
from ic705suite.apps.log.tui.widgets.statusline import StatusLine as LogStatusLine
from ic705suite.gui import theme
from ic705suite.gui.borrow import borrow
from ic705suite.gui.page import Page, _Dialog, _wait
from ic705suite.gui.widgets import Frame, Table, hint_html, rich_colour, rich_html, span

FEEDBACK = {"info": (theme.TEXT, False, True), "ok": (theme.GREEN, True, False),
            "warning": (theme.YELLOW, True, False), "error": (theme.RED, True, False)}


def _font_bold(item: QTableWidgetItem, cell) -> None:
    style = str(getattr(cell, "style", "")) + " ".join(str(s.style) for s in
                                                       getattr(cell, "spans", []))
    if "bold" in style:
        font = item.font()
        font.setBold(True)
        item.setFont(font)


# ---- the log ---------------------------------------------------------------------------------

class QtHistoryPanel(Table):
    """The log with its insert row (history.HistoryPanel): never focused, the entry line's
    arrows move it; a click picks a QSO, a double click opens its card."""

    _cells = HistoryPanel._cells
    _header = staticmethod(HistoryPanel._header)

    def __init__(self, page: LogPage) -> None:
        super().__init__(0, len(COLUMNS))
        self.page = page
        self.order = ORDER_OLDEST_FIRST
        self._rows: list = []
        self._keys: list[str] = []
        self.marked: set[int] = set()
        self.day_separator = True
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setMinimumSectionSize(2)       # the rule between days
        self.verticalHeader().setDefaultSectionSize(22)
        self.horizontalHeader().setStretchLastSection(True)
        char = self.fontMetrics().averageCharWidth()
        for column, (_label, width) in enumerate(COLUMNS):
            if width:
                self.setColumnWidth(column, width * char + 18)
        self.refresh_headers()
        self.cellClicked.connect(lambda row, _c: self._clicked(row))
        self.cellDoubleClicked.connect(lambda row, _c: self._double_clicked(row))

    # ---- what the logbook code asks ----

    @property
    def display(self) -> bool:
        return self.isVisible()

    @display.setter
    def display(self, shown: bool) -> None:
        self.setVisible(shown)

    @property
    def cursor_row(self) -> int:
        return self.currentRow()

    def refresh_headers(self) -> None:
        self.setHorizontalHeaderLabels([self._header(label) for label, _w in COLUMNS])

    @property
    def qso_count(self) -> int:
        return len(self._rows)

    @property
    def on_insert_row(self) -> bool:
        return self._key_at(self.currentRow()) == INSERT_ROW_KEY

    def _key_at(self, row: int) -> str | None:
        return self._keys[row] if 0 <= row < len(self._keys) else None

    def is_day_row(self, row: int) -> bool:
        key = self._key_at(row)
        return key is not None and key.startswith(DAY_ROW_PREFIX)

    def set_day_separator(self, enabled: bool) -> None:
        if enabled != self.day_separator:
            self.day_separator = enabled
            self.load(self._rows)

    def set_order(self, order: str) -> None:
        if order != self.order:
            self.order = order
            self.load(self._rows)

    def load(self, rows: list) -> None:
        self._rows = list(rows)
        self.marked &= {row.id for row in self._rows}
        self._keys = []
        self.clearSpans()
        self.setRowCount(0)
        ordered = self._rows if self.order == ORDER_OLDEST_FIRST else list(reversed(self._rows))
        if self.order == ORDER_NEWEST_FIRST:
            self._add_insert_row()
        previous = None
        for number, row in enumerate(ordered):
            if self.day_separator and previous is not None \
                    and previous.qso_utc.date() != row.qso_utc.date():
                self._add_day_row(number)
            self._add(str(row.id), self._cells(row))
            previous = row
        if self.order == ORDER_OLDEST_FIRST:
            self._add_insert_row()
        self.go_to_insert_row()

    def _add(self, key: str, cells: list) -> None:
        index = self.rowCount()
        self.insertRow(index)
        self._keys.append(key)
        self._fill(index, cells)

    def _fill(self, index: int, cells: list) -> None:
        for column, cell in enumerate(cells):
            item = QTableWidgetItem(cell.plain if hasattr(cell, "plain") else str(cell))
            colour = rich_colour(cell)
            if colour:
                item.setForeground(QColor(colour))
            _font_bold(item, cell)
            self.setItem(index, column, item)

    def _add_day_row(self, number: int) -> None:
        index = self.rowCount()
        self.insertRow(index)
        self._keys.append(f"{DAY_ROW_PREFIX}{number}")
        item = QTableWidgetItem("")        # the terminal's dashed line: a thin rule here
        item.setBackground(QColor(theme.BORDER))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.setItem(index, 0, item)
        self.setSpan(index, 0, 1, len(COLUMNS))
        self.setRowHeight(index, 3)

    def _add_insert_row(self) -> None:
        from rich.text import Text

        cells = [Text("") for _ in COLUMNS]
        cells[0] = Text("▸", style="bold green")
        cells[1] = Text(log_(INSERT_LABEL), style="bold green")
        self._add(INSERT_ROW_KEY, cells)

    def append_row_for(self, row) -> None:
        self._rows.append(row)
        self.load(self._rows)

    def _move_cursor(self, row: int) -> None:
        self.selectRow(row)
        self.setCurrentCell(row, 0)
        self._keep_in_view()

    def _keep_in_view(self) -> None:
        """Now and once the layout settles (the action bar may have just taken a row)."""
        def scroll() -> None:
            item = self.item(self.currentRow(), 0)
            if item is not None:
                self.scrollToItem(item)
        scroll()
        QTimer.singleShot(0, scroll)

    def go_to_insert_row(self) -> None:
        if self.rowCount():
            self._move_cursor(self._keys.index(INSERT_ROW_KEY))
        self.page._on_selection_changed(Event(qso_id=None))

    def move_selection(self, delta: int) -> None:
        if not self.rowCount():
            return
        last = self.rowCount() - 1
        target = max(0, min(last, self.currentRow() + delta))
        if self.is_day_row(target):
            step = 1 if delta >= 0 else -1
            ahead = target + step
            target = ahead if 0 <= ahead <= last else target - step
        if target != self.currentRow():
            self._move_cursor(target)
        self.page._on_selection_changed(Event(qso_id=self.selected_qso_id()))

    def selected_qso_id(self) -> int | None:
        key = self._key_at(self.currentRow())
        if key is None or key == INSERT_ROW_KEY or key.startswith(DAY_ROW_PREFIX):
            return None
        return int(key)

    def selected_row(self):
        qso_id = self.selected_qso_id()
        return next((row for row in self._rows if row.id == qso_id), None) \
            if qso_id is not None else None

    def toggle_mark(self, qso_id: int) -> None:
        self.marked ^= {qso_id}
        self._redraw_marks([qso_id])

    def toggle_all(self) -> None:
        everything = {row.id for row in self._rows}
        changed = everything if self.marked != everything else set(self.marked)
        self.marked = set() if self.marked == everything else everything
        self._redraw_marks(changed)

    def marked_rows(self) -> list:
        return [row for row in self._rows if row.id in self.marked]

    def _redraw_marks(self, qso_ids) -> None:
        by_id = {row.id: row for row in self._rows}
        for qso_id in qso_ids:
            if qso_id in by_id and str(qso_id) in self._keys:
                index = self._keys.index(str(qso_id))
                self._fill(index, self._cells(by_id[qso_id]))

    def replace_row(self, row) -> None:
        self._rows = [row if known.id == row.id else known for known in self._rows]
        if str(row.id) in self._keys:
            self._fill(self._keys.index(str(row.id)), self._cells(row))

    def visible_rows(self) -> int:
        row = max(1, self.verticalHeader().defaultSectionSize())
        return max(1, self.viewport().height() // row)

    # ---- the mouse ----

    def _clicked(self, row: int) -> None:
        if self.page._editing_id is not None or self.is_day_row(row):
            return
        self.move_selection(row - self.currentRow())
        self.page.entry.focus_input()

    def _double_clicked(self, row: int) -> None:
        self._clicked(row)
        qso_id = self.selected_qso_id()
        if qso_id is not None and self.page._editing_id is None:
            self.page._show_card(qso_id)


class QtItemTable(Table):
    """The list of a list view (widgets.items.ItemTable), ending in its insert row."""

    def __init__(self, page: LogPage) -> None:
        super().__init__(0, 0)
        self.page = page
        self._items: dict[str, Item] = {}
        self._keys: list[str] = []
        self._has_insert_row = True
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(22)
        self.horizontalHeader().setStretchLastSection(True)
        self.cellClicked.connect(lambda row, _c: self._clicked(row))

    @property
    def item_count(self) -> int:
        return len(self._items)

    @property
    def cursor_row(self) -> int:
        return self.currentRow()

    def show(self, kind, items: list, keep_id: int | None = None) -> None:  # noqa: A003
        right = [label in kind.right_aligned for label, _w in kind.columns]
        self.clear()
        self.setColumnCount(len(kind.columns))
        self.setHorizontalHeaderLabels([log_(label) for label, _w in kind.columns])
        char = self.fontMetrics().averageCharWidth()
        for column, (_label, width) in enumerate(kind.columns):
            if width:
                self.setColumnWidth(column, width * char + 18)
        self.setRowCount(0)
        self._items, self._keys = {}, []
        for item in items:
            self._add(str(item.id), list(item.cells), right, theme.DIM if item.locked else None)
            self._items[str(item.id)] = item
        self._has_insert_row = kind.can_add
        if kind.can_add:
            widest = kind.insert_column
            if widest is None:
                widest = max(range(len(kind.columns)), key=lambda i: kind.columns[i][1] or 0)
            cells = [""] * len(kind.columns)
            cells[widest] = f"▸ {log_(kind.insert_label)}"
            self._add(INSERT_ROW_KEY, cells, right, theme.GREEN)
        target = self.rowCount() - 1 if kind.can_add else 0
        if keep_id is not None and str(keep_id) in self._items:
            target = self._keys.index(str(keep_id))
        self._move_cursor(target)
        self.page._on_item_selection(Event(item=self.selected_item()))

    def _add(self, key: str, cells: list, right: list[bool], colour: str | None) -> None:
        index = self.rowCount()
        self.insertRow(index)
        self._keys.append(key)
        for column, text in enumerate(cells[:self.columnCount()]):
            item = QTableWidgetItem(text or "")
            if colour:
                item.setForeground(QColor(colour))
            if column < len(right) and right[column]:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.setItem(index, column, item)

    def _move_cursor(self, row: int) -> None:
        if 0 <= row < self.rowCount():
            self.selectRow(row)
            self.setCurrentCell(row, 0)
            item = self.item(row, 0)
            if item is not None:
                self.scrollToItem(item)

    def go_to_insert_row(self) -> None:
        if not self._has_insert_row:
            return
        if self.rowCount():
            self._move_cursor(self.rowCount() - 1)
        self.page._on_item_selection(Event(item=None))

    def move_selection(self, delta: int) -> None:
        if not self.rowCount():
            return
        target = max(0, min(self.rowCount() - 1, self.currentRow() + delta))
        if target != self.currentRow():
            self._move_cursor(target)
        self.page._on_item_selection(Event(item=self.selected_item()))

    @property
    def on_insert_row(self) -> bool:
        return self.selected_item() is None

    def selected_item(self) -> Item | None:
        row = self.currentRow()
        key = self._keys[row] if 0 <= row < len(self._keys) else None
        return self._items.get(key) if key and key != INSERT_ROW_KEY else None

    def visible_rows(self) -> int:
        row = max(1, self.verticalHeader().defaultSectionSize())
        return max(1, self.viewport().height() // row)

    def _clicked(self, row: int) -> None:
        if self.page._editing_id is None:
            self.move_selection(row - self.currentRow())
            self.page.entry.focus_input()


class QtInventoryView(QWidget):
    """Tab row plus the list of the active tab (widgets.items.InventoryView)."""

    def __init__(self, page: LogPage) -> None:
        super().__init__()
        self.tabs = QLabel()
        self.tabs.setTextFormat(Qt.TextFormat.RichText)
        self.table = QtItemTable(page)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.tabs)
        box.addWidget(self.table, 1)

    @property
    def display(self) -> bool:
        return self.isVisible()

    @display.setter
    def display(self, shown: bool) -> None:
        self.setVisible(shown)

    def set_tabs(self, text) -> None:
        self.tabs.setText(rich_html(text))


# ---- detail, status line, footer ------------------------------------------------------------

class QtDetailPanel(QLabel):
    """Three lines about the selected QSO, or the one being written (widgets.detail): the
    terminal's own text."""

    show_qso = DetailPanel.show_qso
    show_session = DetailPanel.show_session
    show_lines = DetailPanel.show_lines
    show_nothing = DetailPanel.show_nothing

    def __init__(self) -> None:
        super().__init__()
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(self.fontMetrics().height() * 3 + 4)

    def update(self, text=None) -> None:  # noqa: D401 - Textual's Static.update
        if text is None:
            super().update()
        else:
            self.setText(rich_html(text))


class QtStatusLine(QLabel):
    """The configuration a new QSO inherits (widgets.statusline), on one line."""

    def __init__(self) -> None:
        super().__init__()
        for name in ("operator", "repeater", "band", "mode", "digital_summary", "station",
                     "profile"):
            setattr(self, name, "")
        self.freq_hz = None
        self.demo = False
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setContentsMargins(6, 3, 6, 3)
        self.setStyleSheet(f"background:{theme.PANEL};border-bottom:1px solid {theme.BORDER};")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def refresh(self) -> None:
        self.setText(rich_html(LogStatusLine.render(self)))


class QtStatsFooter(QLabel):
    """Totals, the UTC clock (and the local time) and the keys (widgets.footer)."""

    _local = StatsFooter._local

    def __init__(self) -> None:
        super().__init__()
        self.stats = None
        self.timezone = ""
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setContentsMargins(0, 2, 6, 2)
        self.setStyleSheet(f"background:{theme.PANEL};border-top:1px solid {theme.BORDER};")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.timer = QTimer(self, interval=1000, timeout=self.redraw)
        self.timer.start()

    def redraw(self) -> None:
        counters = StatsFooter._counters(self, "   ", True, True)
        hints = [log_(HELP_HINT), *(log_(h) for h in SHORT_HINTS)]
        chars = max(1, self.width() // max(1, self.fontMetrics().averageCharWidth()))
        hint = next((h for h in hints if len(h) + len(counters.plain) + 4 < chars), hints[-1])
        self.setText(f'<table width="100%"><tr><td>{rich_html(counters)}</td>'
                     f'<td align="right">{hint_html(hint)}</td></tr></table>')


# ---- the entry line ----------------------------------------------------------------------------

class QtEntryField(QLineEdit):
    """One box of the entry line (widgets.entry.EntryField): ↑↓ move the log, Ctrl+↑↓
    recall earlier entries, Enter logs from any box; a list of names completes inline."""

    def __init__(self, page: LogPage, field_name: str, extra: bool) -> None:
        super().__init__()
        self.page = page
        self.field_name = field_name
        self.extra = extra
        self.suggester = None
        self._suggesting = False
        self.textChanged.connect(self._changed)
        self.textEdited.connect(self._suggest)
        self.returnPressed.connect(lambda: self.page._on_entry_submitted())

    @property
    def value(self) -> str:
        return self.text()

    @value.setter
    def value(self, text: str) -> None:
        self.setText(text)

    @property
    def disabled(self) -> bool:
        return not self.isEnabled()

    @disabled.setter
    def disabled(self, off: bool) -> None:
        self.setEnabled(not off)

    def _changed(self, text: str) -> None:
        if not self._suggesting:
            self.page._on_entry_changed(Event(input=self, value=text))

    def _suggest(self, text: str) -> None:
        """Inline completion as ListSuggester gives it: the rest of the name, selected, so
        typing goes on over it and → (or End) keeps it."""
        names = getattr(self.suggester, "names", None)
        if not names or self.cursorPosition() != len(text):
            return
        token = text.rpartition(",")[2].lstrip()
        if not token:
            return
        needle = token.lower()
        match = next((n for n in names if n.lower().startswith(needle) and len(n) > len(token)),
                     None)
        if match is None:
            return
        self._suggesting = True
        self.setText(text + match[len(token):])
        self.setSelection(len(text), len(match) - len(token))
        self._suggesting = False

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt's name
        key, ctrl = event.key(), bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if key == Qt.Key.Key_Up else 1
            if ctrl:
                self.page._on_recall(Event(direction=step))
            else:
                self.page._on_move_history(Event(delta=step))
            return
        super().keyPressEvent(event)


class QtBrowseBar(QLabel):
    """The action bar while the cursor is on a logged QSO or an item (BrowseBar): letters
    are actions, never text."""

    def __init__(self, page: LogPage) -> None:
        super().__init__()
        self.page = page
        self.prompt = BROWSE_PROMPT
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(f"background:{theme.SELECTED};padding:4px 8px;font-weight:bold;")
        self.setVisible(False)
        self.set_prompt(BROWSE_PROMPT)

    @property
    def display(self) -> bool:
        return self.isVisible()

    @display.setter
    def display(self, shown: bool) -> None:
        self.setVisible(shown)

    def set_prompt(self, prompt: str) -> None:
        self.prompt = prompt
        self.setText(span(log_(prompt), theme.TEXT, True))

    def focus(self) -> None:
        self.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.page._on_move_history(Event(delta=-1 if key == Qt.Key.Key_Up else 1))
            return
        if key == Qt.Key.Key_Delete:
            self.page._on_browse_action(Event(action="delete"))
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.page._on_browse_action(Event(action="activate"))
            return
        character = event.text().lower()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            character = ""
        action = BROWSE_ACTIONS.get(character)
        if action is not None:
            self.page._on_browse_action(Event(action=action))
        elif character and character.isprintable():
            self.page._on_unknown_browse_key()
        else:
            event.ignore()                     # Esc and the rest: the page's keys


class QtEntryPanel(QWidget):
    """The entry line (widgets.entry.EntryPanel): a box per field; the action bar while
    browsing; a second row while editing; messages on the frame's edge."""

    def __init__(self, page: LogPage, frame: Frame) -> None:
        super().__init__()
        self.page = page
        self.frame = frame
        self._field_order: tuple[str, ...] = ()
        self._extra_fields: tuple[str, ...] = EDIT_EXTRA_FIELDS
        self._second_fields: tuple[str, ...] = ()
        self.message = ""
        self._draft: dict[str, str] = {}
        self._browsing = False
        self._editing = False
        self._bulk = False
        self._boxes: list[QtEntryField] = []
        self.rows = [QWidget(), QWidget(), QWidget()]        # fields, second, extra
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        for row in self.rows:
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            box.addWidget(row)
        self.rows[1].setVisible(False)
        self.rows[2].setVisible(False)

    @property
    def _bar(self) -> QtBrowseBar:
        return self.page.browse_bar

    @property
    def fields(self) -> list[QtEntryField]:
        if self._editing and self._bulk:
            return [b for b in self._boxes if b.field_name in BULK_FIELDS]
        return [b for b in self._boxes if self._editing or not b.extra]

    @property
    def bulk(self) -> bool:
        return self._editing and self._bulk

    @property
    def browsing(self) -> bool:
        return self._browsing

    @property
    def editing(self) -> bool:
        return self._editing

    def build_fields(self, field_order, extra=EDIT_EXTRA_FIELDS, second=()) -> None:
        if (tuple(field_order), tuple(extra), tuple(second)) == \
                (self._field_order, self._extra_fields, self._second_fields):
            return
        self._field_order, self._extra_fields, self._second_fields = \
            tuple(field_order), tuple(extra), tuple(second)
        for row in self.rows:
            layout = row.layout()
            while layout.count():
                child = layout.takeAt(0).widget()
                if child is not None:
                    child.deleteLater()
        self._boxes = []
        self._fill(self.rows[0], field_order, False)
        self._fill(self.rows[1], second, False)
        self._fill(self.rows[2], tuple(n for n in extra if n not in field_order), True)
        self.rows[1].setVisible(bool(second) and self.rows[0].isVisibleTo(self))
        self.rows[2].setVisible(False)

    def _fill(self, row: QWidget, names, extra: bool) -> None:
        layout = row.layout()
        for name in names:
            label_text, width = FIELD_LAYOUT.get(name, DEFAULT_LAYOUT)
            label = QLabel(log_(label_text))
            label.setStyleSheet(f"color:{theme.CYAN};font-weight:bold;")
            box = QtEntryField(self.page, name, extra)
            box.setFont(theme.mono(10.5))
            if width is not None:
                box.setFixedWidth(width * box.fontMetrics().averageCharWidth() + 18)
                layout.addWidget(label)
                layout.addWidget(box)
            else:
                box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                layout.addWidget(label)
                layout.addWidget(box, 1)
            # Shown now rather than when the layout gets round to it: the logbook code
            # focuses the first box straight away.
            label.show()
            box.show()
            self._boxes.append(box)
        if names and all(FIELD_LAYOUT.get(n, DEFAULT_LAYOUT)[1] is not None for n in names):
            layout.addStretch(1)

    def set_suggesters(self, suggesters: dict) -> None:
        for box in self._boxes:
            box.suggester = suggesters.get(box.field_name)

    async def ready(self) -> None:
        """The boxes are made at once here: nothing to wait for."""

    def focus_first(self) -> None:
        boxes = self.fields
        if boxes:
            boxes[0].setFocus()

    def values(self) -> dict[str, str]:
        return {b.field_name: b.text().strip() for b in self.fields}

    def set_values(self, values: dict[str, str]) -> None:
        self._draft = {}
        self.set_browsing(False)
        for box in self.fields:
            box.setText(values.get(box.field_name, ""))
        self.focus_first()

    def clear(self) -> None:
        for box in self.fields:
            box.setText("")
        self.focus_first()

    @property
    def first_value(self) -> str:
        boxes = self.fields
        return boxes[0].text().strip() if boxes else ""

    def set_first_value(self, text: str) -> None:
        boxes = self.fields
        if boxes:
            boxes[0].setText(text)

    def set_browsing(self, browsing: bool) -> None:
        if browsing == self._browsing:
            return
        if self._editing:
            self._leave_edit()
        self._browsing = browsing
        if browsing:
            self._draft = self.values()
            self._dim_form(True)
            self._bar.display = True
            self._bar.focus()
        else:
            self._bar.display = False
            self._dim_form(False)
            for box in self.fields:
                box.setText(self._draft.get(box.field_name, ""))
            self._draft = {}
            self.focus_first()

    def _dim_form(self, dimmed: bool) -> None:
        for box in self._boxes:
            box.setEnabled(not dimmed)

    def start_edit(self, values: dict[str, str], *, bulk: bool = False) -> None:
        self._editing = True
        self._bulk = bulk
        self._bar.display = False
        self.rows[2].setVisible(any(b.extra for b in self._boxes))
        self.rows[0].setVisible(not bulk or any(b.field_name in BULK_FIELDS
                                                for b in self._boxes if not b.extra))
        self._show_second(not bulk)
        for box in self._boxes:
            box.setEnabled(not (bulk and box.field_name not in BULK_FIELDS))
        for box in self.fields:
            box.setText(values.get(box.field_name, ""))
        self.focus_first()

    def stop_edit(self) -> None:
        if not self._editing:
            return
        self._leave_edit()
        for box in self.fields:
            box.setText(self._draft.get(box.field_name, ""))
        self._dim_form(True)
        self._bar.display = True
        self._bar.focus()

    def _leave_edit(self) -> None:
        self._editing = False
        self._bulk = False
        for box in self._boxes:
            box.setEnabled(True)
            box.setText("")
        self.rows[2].setVisible(False)
        self.rows[0].setVisible(True)
        self._show_second(True)

    def reset(self) -> None:
        if self._editing:
            self._leave_edit()
        self._browsing = False
        self._draft = {}
        self._bar.display = False
        self.rows[0].setVisible(True)
        self._show_second(True)
        for box in self._boxes:
            box.setEnabled(True)
            box.setText("")

    def _show_second(self, visible: bool) -> None:
        self.rows[1].setVisible(visible and bool(self._second_fields))

    @property
    def pending_values(self) -> dict[str, str]:
        return dict(self._draft) if self._browsing else self.values()

    def set_browse_prompt(self, prompt: str) -> None:
        self._bar.set_prompt(prompt)

    def focus_input(self) -> None:
        if self._browsing and not self._editing:
            self._bar.focus()
        else:
            self.focus_first()

    def feedback(self, message: str, level: str = "info") -> None:
        self.message = message
        if not message:
            self.frame.subtitle.setText("")
            return
        if level == "dup":
            html = (f'<span style="background:{theme.YELLOW};color:#000;font-weight:bold">'
                    f"&nbsp;{span(message, '#000000')}&nbsp;</span>")
        else:
            colour, bold, italic = FEEDBACK.get(level, FEEDBACK["info"])
            html = span(message, colour, bold)
            if italic:
                html = f"<i>{html}</i>"
        self.frame.subtitle.setText(html)
        self.frame.subtitle.setToolTip(message)


class LogFrame(Frame):
    """A frame whose title the logbook code sets as Textual's border_title."""

    @property
    def border_title(self) -> str:
        return self.title.text()

    @border_title.setter
    def border_title(self, text: str) -> None:
        self.set_title(text)


# ---- the page --------------------------------------------------------------------------------

class LogPage(Page):
    TUI = HamrlogApp

    def __init__(self, window, link, config, demo: bool = False,
                 database_url: str | None = None) -> None:
        super().__init__(window, link, config)
        # HamrlogApp's state, as its __init__ sets it.
        self.database_url = database_url
        self.demo = demo
        self.state = SessionState()
        self._line_history: list[dict[str, str]] = []
        self._history_index: int | None = None
        self._last_dup_check = ""
        self._showing_selection = False
        self._editing_id: int | None = None
        self._bulk_ids: list[int] = []
        self._view = "log"
        self._tabs: dict[str, int] = dict.fromkeys(LIST_VIEWS, 0)
        self._queries: dict[str, str] = {}
        self._brands: dict[str, str] = {}
        self._drafts: dict[str, dict[str, str]] = {}
        self._build()

    def _build(self) -> None:
        self.statusline = QtStatusLine()
        self.main_frame = LogFrame(log_("Log"))
        self.history = QtHistoryPanel(self)
        self.inventory = QtInventoryView(self)
        self.inventory.setVisible(False)
        self.browse_bar = QtBrowseBar(self)
        self.main_frame.add(self.history, 1)
        self.main_frame.add(self.inventory, 1)
        self.main_frame.add(self.browse_bar)
        self.detail_frame = LogFrame(log_("Detail"))
        self.detail = self.detail_frame.add(QtDetailPanel())
        self.entry_frame = LogFrame(log_("Entry"))
        self.entry = QtEntryPanel(self, self.entry_frame)
        self.entry_frame.add(self.entry)
        self.footer = QtStatsFooter()
        self.toast = QLabel(self)
        self.toast.setStyleSheet(f"background:{theme.RAISED};border:1px solid {theme.FOCUS};"
                                 "border-radius:6px;padding:8px 12px;")
        self.toast.setVisible(False)
        self.toast_timer = QTimer(self, singleShot=True, interval=4500,
                                  timeout=lambda: self.toast.setVisible(False))
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 6, 0)
        box.setSpacing(6)
        box.addWidget(self.statusline)
        box.addWidget(self.main_frame, 1)
        box.addWidget(self.detail_frame)
        box.addWidget(self.entry_frame)
        box.addWidget(self.footer)
        self._widgets = {
            QtEntryPanel: self.entry, QtHistoryPanel: self.history,
            QtItemTable: self.inventory.table, QtInventoryView: self.inventory,
            QtDetailPanel: self.detail, QtStatusLine: self.statusline,
            QtStatsFooter: self.footer, QtBrowseBar: self.browse_bar,
        }

    # ---- what the logbook code asks of a Textual App ----

    def query_one(self, selector, _type=None):
        if isinstance(selector, str):
            return {"#log-frame": self.main_frame, "#entry-browse": self.browse_bar,
                    "#history": self.history, "#detail": self.detail_frame,
                    "#entry": self.entry_frame}[selector]
        return self._widgets[selector]

    def notify(self, message: str, *, title: str = "", severity: str = "information",
               timeout: float | None = None) -> None:
        colour = theme.RED if severity == "error" else theme.YELLOW if severity == "warning" \
            else theme.TEXT
        self.toast.setText(span(message, colour, severity != "information"))
        self.toast.adjustSize()
        self.toast.move(self.width() - self.toast.width() - 20, 40)
        self.toast.raise_()
        self.toast.setVisible(True)
        self.toast_timer.start()

    def call_after_refresh(self, callback, *args) -> None:
        QTimer.singleShot(0, lambda: callback(*args))

    def push_screen(self, screen, callback=None) -> None:
        self.run_worker(self._screen(screen, callback))

    async def _screen(self, screen, callback) -> None:
        if isinstance(screen, ConfirmScreen):
            result = await confirm(self, screen.question, screen.detail, screen.danger)
        elif isinstance(screen, FormScreen):
            result = await form(self, screen)
        elif isinstance(screen, QsoCardScreen):
            result = await card(self, screen)
        else:
            raise TypeError(f"no dialog for {type(screen).__name__}")
        if callback is not None:
            callback(result)
        self.entry.focus_input()

    # ---- life ----

    def start(self) -> None:
        """What HamrlogApp.on_mount does."""
        from ic705suite.apps.log.db.session import init_engine

        init_engine(self.database_url)
        self._load_initial_state()
        self.state.apply_frequency_format()
        self._maybe_start_metrics()
        self.history.order = self.state.history_order
        self._apply_config()
        from ic705suite.apps.log.core.services import QsoService

        self.history.load(QsoService.recent())
        self._refresh_status()
        self._refresh_stats()
        self.statusline.demo = self.demo
        self.statusline.refresh()
        self.main_frame.border_title = log_("Log")
        self.inventory.display = False
        self._refresh_detail(None)
        self.entry.focus_input()
        if self.state.operator_id is None:
            self.call_after_refresh(self._first_run_wizard)

    def stop(self) -> None:
        self._persist_state()
        super().stop()

    def restore_focus(self) -> None:
        self.entry.focus_input()

    def _page(self, direction: int) -> None:
        """PgUp/PgDn: a screenful of the list in view (HamrlogApp._page, measured in Qt)."""
        table = self.history if self._view == "log" else self.inventory.table
        table.move_selection(direction * table.visible_rows())

    def action_quit(self) -> None:
        self._persist_state()
        self.exit()


# The logbook's own methods, with the Qt widgets in place of the Textual ones.
QT_WIDGETS = {"EntryPanel": QtEntryPanel, "EntryField": QtEntryField, "BrowseBar": QtBrowseBar,
              "HistoryPanel": QtHistoryPanel, "ItemTable": QtItemTable,
              "InventoryView": QtInventoryView, "DetailPanel": QtDetailPanel,
              "StatusLine": QtStatusLine, "StatsFooter": QtStatsFooter}
borrow(HamrlogApp, LogPage, QT_WIDGETS, own={"compose", "on_mount", "_page", "action_quit"})
assert log_app.EntryPanel is not QtEntryPanel           # the terminal keeps its own


# ---- dialogs: the logbook's screens -----------------------------------------------------------

async def confirm(parent: QWidget, question: str, detail: str = "", danger: bool = False) -> bool:
    """ConfirmScreen: Yes (Y or S) / No (N or Esc), No by default."""
    dialog = _Dialog(parent, question, detail)
    dialog.answer = False
    row = QHBoxLayout()
    row.addStretch(1)
    yes = QPushButton(log_("Yes (Y)"))
    no = QPushButton(log_("No (Esc)"))
    if danger:
        yes.setStyleSheet(f"border:1px solid {theme.RED};color:{theme.RED};")

    def answer(value: bool) -> None:
        dialog.answer = value
        dialog.accept()

    yes.clicked.connect(lambda: answer(True))
    no.clicked.connect(lambda: answer(False))
    for key, value in (("Y", True), ("S", True), ("N", False)):
        QShortcut(QKeySequence(key), dialog, activated=lambda v=value: answer(v))
    row.addWidget(yes)
    row.addWidget(no)
    dialog.layout_.addLayout(row)
    no.setDefault(True)
    no.setFocus()
    await _wait(dialog)
    return bool(dialog.answer)


async def form(parent: QWidget, screen: FormScreen) -> dict[str, str] | None:
    """FormScreen: a box per field; Enter or Ctrl+S saves, Esc cancels."""
    dialog = _Dialog(parent, screen.title_text, screen.subtitle_text)
    layout = QFormLayout()
    boxes: dict[str, QLineEdit] = {}
    for field in screen.fields:
        if field.editable:
            box = QLineEdit(field.value)
            box.setPlaceholderText(field.placeholder)
            box.returnPressed.connect(lambda: save())
            boxes[field.key] = box
            layout.addRow(field.label, box)
        else:
            layout.addRow(field.label, QLabel(field.value or "-"))
    dialog.layout_.addLayout(layout)
    row = QHBoxLayout()
    row.addStretch(1)
    save_button = QPushButton(f"{screen.save_label} (Ctrl+S)")
    cancel = QPushButton(log_("Cancel (Esc)"))
    row.addWidget(save_button)
    row.addWidget(cancel)
    dialog.layout_.addLayout(row)

    def save() -> None:
        dialog.answer = {key: box.text().strip() for key, box in boxes.items()}
        dialog.accept()

    save_button.clicked.connect(save)
    cancel.clicked.connect(dialog.reject)
    QShortcut(QKeySequence("Ctrl+S"), dialog, activated=save)
    if boxes:
        next(iter(boxes.values())).setFocus()
    await _wait(dialog)
    return dialog.answer if dialog.result() == QDialog.DialogCode.Accepted else None


async def card(parent: QWidget, screen: QsoCardScreen) -> None:
    """QsoCardScreen: everything about a QSO; Esc, Enter, V or Q close it."""
    dialog = _Dialog(parent, screen.card.row.call, "")
    dialog.setMinimumSize(720, 460)
    text = QTextBrowser()
    text.setFont(theme.mono(10))
    text.setHtml(f'<div style="white-space:pre">{rich_html(render_card(screen.card))}</div>')
    dialog.layout_.addWidget(text, 1)
    for key in ("Return", "Enter", "V", "Q"):
        QShortcut(QKeySequence(key), dialog, activated=dialog.accept)
    await _wait(dialog)

