"""FT8's lists in a tab: the stations and countries ignored and the stations wanted (Auto
CQ calls them when heard), as the core keeps them in the database (apps.ft8.lists). A table
to see them, filtered by list; the name and the note edited in place; a row to add one;
the rows chosen removed. Every change goes to the core, and comes back to every client.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ic705suite.i18n import N_, _, render

KINDS = (("call", N_("Ignored station")), ("country", N_("Ignored country")),
         ("wanted", N_("CQ auto")))
NAMES = dict(KINDS)
ID = Qt.ItemDataRole.UserRole


class ListsPane(QWidget):
    def __init__(self, page) -> None:
        super().__init__()
        self.page = page
        self.entries: list[dict] = []
        self._filling = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem(_("Every list"), "")
        for kind, name in KINDS:
            self.filter.addItem(_(name), kind)
        self.filter.currentIndexChanged.connect(lambda _i: self._fill())
        top.addWidget(self.filter)
        top.addStretch(1)
        self.remove_button = QPushButton(_("Remove the rows chosen"))
        self.remove_button.clicked.connect(self._remove)
        top.addWidget(self.remove_button)
        layout.addLayout(top)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("List"), _("Name"), _("Note"), _("Added")])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._edited)
        layout.addWidget(self.table, 1)
        add = QHBoxLayout()
        self.kind_box = QComboBox()
        for kind, name in KINDS:
            self.kind_box.addItem(_(name), kind)
        add.addWidget(self.kind_box)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("Callsign, or country as cty.dat names it"))
        add.addWidget(self.name_edit, 1)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText(_("Note"))
        add.addWidget(self.note_edit, 2)
        self.add_button = QPushButton(_("Add"))
        self.add_button.clicked.connect(self._add)
        self.name_edit.returnPressed.connect(self._add)
        self.note_edit.returnPressed.connect(self._add)
        add.addWidget(self.add_button)
        layout.addLayout(add)

    # ---- from the core ------------------------------------------------------------------------

    def reload(self) -> None:
        self.page.run_worker(self._reload(), group="lists", exclusive=True)

    async def _reload(self) -> None:
        self._answer(await self.page.link.call("ft8.lists"))

    def _answer(self, reply: dict) -> bool:
        if reply.get("error"):
            self.page._message("error", render(reply) if reply.get("msgid") else reply["error"])
            self._fill()                         # what was typed in a cell goes back
            return False
        self.entries = reply.get("entries", [])
        self._fill()
        return True

    def _fill(self) -> None:
        wanted = self.filter.currentData()
        shown = [e for e in self.entries if not wanted or e["kind"] == wanted]
        self._filling = True
        self.table.setRowCount(len(shown))
        for row, entry in enumerate(shown):
            cells = [_(NAMES.get(entry["kind"], entry["kind"])),
                     _(entry["name"]) if entry["kind"] == "country" else entry["name"],
                     entry.get("note") or "",
                     time.strftime("%Y-%m-%d %H:%M", time.gmtime(entry.get("added") or 0))]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(ID, entry["id"])
                editable = column == 2 or (column == 1 and entry["kind"] != "country")
                if not editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self._filling = False

    # ---- changes ------------------------------------------------------------------------------

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() not in (1, 2):
            return
        field = "name" if item.column() == 1 else "note"
        self.page.run_worker(self._edit(item.data(ID), field, item.text()), group="lists")

    async def _edit(self, entry_id: int, field: str, value: str) -> None:
        self._answer(await self.page.link.call("ft8.list.edit", id=entry_id,
                                               **{field: value}))

    def _add(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            return
        self.page.run_worker(self._add_entry(self.kind_box.currentData(), name,
                                             self.note_edit.text().strip()), group="lists")

    async def _add_entry(self, kind: str, name: str, note: str) -> None:
        if self._answer(await self.page.link.call("ft8.list.add", kind=kind, name=name,
                                                  note=note)):
            self.name_edit.clear()
            self.note_edit.clear()

    def _remove(self) -> None:
        ids = sorted({self.table.item(index.row(), 0).data(ID)
                      for index in self.table.selectionModel().selectedRows()})
        if ids:
            self.page.run_worker(self._remove_entries(ids), group="lists")

    async def _remove_entries(self, ids: list[int]) -> None:
        reply = {}
        for entry_id in ids:
            reply = await self.page.link.call("ft8.list.remove", id=entry_id)
            if reply.get("error"):
                break
        self._answer(reply)
