"""Memory channels for the phone application, in two tabs: the suite's own (the core's
database: station.memories), and the radio's (read a group of the IC-705's memories, write
one, clear one). A double click tunes to a memory; the current frequency is saved as one;
hamrlog's repeaters come in as memories.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ic705suite.gui.page import ask_text, choose
from ic705suite.i18n import _
from ic705suite.rig import bands

ROW = Qt.ItemDataRole.UserRole


def frequency_text(hz: int | None) -> str:
    return bands.format_frequency(hz) if hz else "-"


def repeater_text(memory: dict) -> str:
    """The duplex and offset ("-0.600") and the tone ("T 88.5", "TSQL 88.5")."""
    parts = []
    if memory.get("duplex"):
        parts.append(f"{memory['duplex']}{memory.get('offset', 0) / 1e6:.3f}")
    if memory.get("tone_mode") == "tone":
        parts.append(f"T {memory.get('tone', 0):g}")
    elif memory.get("tone_mode") == "tsql":
        parts.append(f"TSQL {memory.get('tsql', 0):g}")
    return " ".join(parts)


def table(headers: list[str], stretch: int) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    header = widget.horizontalHeader()
    for column in range(len(headers)):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch if column == stretch
                                    else QHeaderView.ResizeMode.ResizeToContents)
    return widget


def item(text: str, editable: bool = False) -> QTableWidgetItem:
    cell = QTableWidgetItem(text)
    if not editable:
        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return cell


class MemoriesPane(QTabWidget):
    def __init__(self, page) -> None:
        super().__init__()
        self.page = page
        self.memories: list[dict] = []        # the suite's
        self.radio: list[dict] = []           # the group of the radio's last read
        self._filling = False
        self.addTab(self._suite_tab(), _("Suite"))
        self.addTab(self._radio_tab(), _("Radio"))

    # ---- the suite's ----------------------------------------------------------------------

    def _suite_tab(self) -> QWidget:
        tab = QWidget()
        box = QVBoxLayout(tab)
        box.setContentsMargins(0, 4, 0, 0)
        self.suite_table = table([_("Name"), _("Frequency"), _("Mode"), _("Repeater"),
                                  _("Note")], 4)
        self.suite_table.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed
                                         | QAbstractItemView.EditTrigger.SelectedClicked)
        self.suite_table.cellDoubleClicked.connect(lambda row, _c: self._tune_suite(row))
        self.suite_table.itemChanged.connect(self._edited)
        box.addWidget(self.suite_table, 1)
        row = QHBoxLayout()
        for label, action in ((_("Tune"), lambda: self._tune_suite(self._row(self.suite_table))),
                              (_("Save the current one"), self._save_current),
                              (_("Delete"), self._delete),
                              (_("Repeaters…"), self._import)):
            button = QPushButton(label)
            button.clicked.connect(lambda _c=False, a=action: a())
            row.addWidget(button)
        row.addStretch(1)
        box.addLayout(row)
        return tab

    def reload(self) -> None:
        self.page.run_worker(self._reload(), group="memories", exclusive=True)

    async def _reload(self) -> None:
        self._suite_answer(await self.page.link.call("memories"))

    def _suite_answer(self, reply: dict) -> bool:
        if reply.get("error"):
            self.page._message("error", reply["error"])
            self._fill_suite()
            return False
        self.memories = reply.get("memories", self.memories)
        self._fill_suite()
        return True

    def _fill_suite(self) -> None:
        self._filling = True
        self.suite_table.setRowCount(len(self.memories))
        for row, memory in enumerate(self.memories):
            cells = [item(memory.get("name") or "", True),
                     item(frequency_text(memory.get("frequency"))),
                     item(memory.get("mode") or ""), item(repeater_text(memory)),
                     item(memory.get("note") or "", True)]
            for column, cell in enumerate(cells):
                cell.setData(ROW, row)
                self.suite_table.setItem(row, column, cell)
        self._filling = False

    @staticmethod
    def _row(widget: QTableWidget) -> int:
        rows = widget.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _tune_suite(self, row: int) -> None:
        if 0 <= row < len(self.memories):
            self.page.tune_memory(self.memories[row])

    def _edited(self, cell: QTableWidgetItem) -> None:
        if self._filling or cell.column() not in (0, 4):
            return
        memory = dict(self.memories[cell.data(ROW)])
        memory["name" if cell.column() == 0 else "note"] = cell.text()
        self.page.run_worker(self._save(memory), group="memories")

    async def _save(self, memory: dict) -> None:
        self._suite_answer(await self.page.link.call("memory.save", **memory))

    def _save_current(self) -> None:
        self.page.run_worker(self._save_current_named(), group="memories")

    async def _save_current_named(self) -> None:
        current = self.page.current_memory()
        if current is None:
            self.page._message("error", _("No frequency to save: the radio is not connected"))
            return
        name = await ask_text(self, _("Save the current one"),
                              _("A name for {frequency} {mode}").format(
                                  frequency=frequency_text(current["frequency"]),
                                  mode=current["mode"]))
        if name is None:
            return
        await self._save({**current, "name": name})

    def _delete(self) -> None:
        row = self._row(self.suite_table)
        if 0 <= row < len(self.memories):
            self.page.run_worker(self._delete_memory(self.memories[row]), group="memories")

    async def _delete_memory(self, memory: dict) -> None:
        answer = await choose(self, _("Delete"), _("Delete the memory {name} ({frequency})?")
                              .format(name=memory.get("name") or "-",
                                      frequency=frequency_text(memory.get("frequency"))),
                              [("yes", _("Delete")), ("no", _("Cancel"))])
        if answer == "yes":
            self._suite_answer(await self.page.link.call("memory.delete", id=memory["id"]))

    def _import(self) -> None:
        dialog = RepeaterDialog(self)
        dialog.finished.connect(lambda _r: dialog.chosen and self.page.run_worker(
            self._import_chosen(dialog.chosen), group="memories"))
        dialog.open()

    async def _import_chosen(self, chosen: list[dict]) -> None:
        known = {(m["frequency"], m.get("name")) for m in self.memories}
        added = 0
        for memory in chosen:
            if (memory["frequency"], memory["name"]) in known:
                continue
            if not self._suite_answer(await self.page.link.call("memory.save", **memory)):
                return
            added += 1
        self.page._message("info", _("{count} repeaters saved as memories").format(count=added))

    # ---- the radio's ----------------------------------------------------------------------

    def _radio_tab(self) -> QWidget:
        tab = QWidget()
        box = QVBoxLayout(tab)
        box.setContentsMargins(0, 4, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel(_("Group")))
        self.group_spin = QSpinBox()
        self.group_spin.setRange(0, 99)
        top.addWidget(self.group_spin)
        read = QPushButton(_("Read"))
        read.clicked.connect(lambda: self.page.run_worker(self._read(), group="radio",
                                                          exclusive=True))
        top.addWidget(read)
        top.addStretch(1)
        box.addLayout(top)
        self.radio_table = table([_("Ch"), _("Name"), _("Frequency"), _("Mode"),
                                  _("Repeater")], 1)
        self.radio_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.radio_table.cellDoubleClicked.connect(lambda row, _c: self._tune_radio(row))
        box.addWidget(self.radio_table, 1)
        row = QHBoxLayout()
        tune = QPushButton(_("Tune"))
        tune.clicked.connect(lambda: self._tune_radio(self._row(self.radio_table)))
        copy = QPushButton(_("Into the suite's"))
        copy.clicked.connect(self._copy)
        row.addWidget(tune)
        row.addWidget(copy)
        row.addSpacing(16)
        row.addWidget(QLabel(_("Channel")))
        self.channel_spin = QSpinBox()
        self.channel_spin.setRange(0, 99)
        row.addWidget(self.channel_spin)
        write = QPushButton(_("Write the current one"))
        write.setToolTip(_("The frequency and mode now (or the suite's memory chosen in its "
                           "tab) into that channel of the radio"))
        write.clicked.connect(lambda: self.page.run_worker(self._write(), group="radio"))
        clear = QPushButton(_("Clear"))
        clear.clicked.connect(lambda: self.page.run_worker(self._clear(), group="radio"))
        row.addWidget(write)
        row.addWidget(clear)
        row.addStretch(1)
        box.addLayout(row)
        return tab

    async def _read(self) -> None:
        group = self.group_spin.value()
        self.page._message("info", _("Reading the radio's group {group}…").format(group=group))
        reply = await self.page.link.call("rig.memories", group=group)
        if reply.get("error"):
            self.page._message("error", reply["error"])
            return
        self.radio = reply.get("memories", [])
        self._fill_radio()
        self.page._message("info", _("Group {group}: {count} memories").format(
            group=group, count=len(self.radio)))

    def _fill_radio(self) -> None:
        self.radio_table.setRowCount(len(self.radio))
        for row, memory in enumerate(self.radio):
            cells = [f"{memory['channel']:02d}", memory.get("name") or "",
                     frequency_text(memory.get("frequency")), memory.get("mode") or "",
                     repeater_text(memory)]
            for column, text in enumerate(cells):
                self.radio_table.setItem(row, column, item(text))

    def _tune_radio(self, row: int) -> None:
        if 0 <= row < len(self.radio):
            self.page.tune_memory(self.radio[row])

    def _copy(self) -> None:
        row = self._row(self.radio_table)
        if 0 <= row < len(self.radio):
            memory = {k: v for k, v in self.radio[row].items()
                      if k not in ("group", "channel")}
            self.page.run_worker(self._save(memory), group="memories")

    async def _write(self) -> None:
        group, channel = self.group_spin.value(), self.channel_spin.value()
        row = self._row(self.suite_table)
        memory = dict(self.memories[row]) if 0 <= row < len(self.memories) \
            else self.page.current_memory()
        if memory is None:
            self.page._message("error", _("No frequency to save: the radio is not connected"))
            return
        if any(m["channel"] == channel for m in self.radio):
            answer = await choose(self, _("Write"), _("Channel {channel} of group {group} has "
                                                      "a memory: write over it?").format(
                channel=channel, group=group), [("yes", _("Write")), ("no", _("Cancel"))])
            if answer != "yes":
                return
        memory = {k: v for k, v in memory.items() if k not in ("id", "added", "note")}
        reply = await self.page.link.call("rig.memory.write", group=group, channel=channel,
                                          memory=memory)
        if reply.get("error"):
            self.page._message("error", reply["error"])
            return
        self.page._message("info", _("Written into the radio's channel {group}/{channel}")
                           .format(group=group, channel=channel))
        await self._read()

    async def _clear(self) -> None:
        group, channel = self.group_spin.value(), self.channel_spin.value()
        answer = await choose(self, _("Clear"), _("Clear the radio's channel {channel} of group "
                                                  "{group}?").format(channel=channel,
                                                                     group=group),
                              [("yes", _("Clear")), ("no", _("Cancel"))])
        if answer != "yes":
            return
        reply = await self.page.link.call("rig.memory.write", group=group, channel=channel,
                                          memory=None)
        if reply.get("error"):
            self.page._message("error", reply["error"])
            return
        await self._read()


class RepeaterDialog(QDialog):
    """hamrlog's repeaters, searched (callsign, channel, place...), the rows chosen saved as
    memories."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Repeaters from the logbook"))
        self.resize(820, 480)
        self.found: list[dict] = []
        self.chosen: list[dict] = []
        box = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search: callsign, channel, place, locator (Enter)"))
        self.search.returnPressed.connect(self._search)
        box.addWidget(self.search)
        self.table = table([_("Name"), _("Frequency"), _("Mode"), _("Repeater"), _("Note")], 4)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        box.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.note = QLabel()
        row.addWidget(self.note, 1)
        save = QPushButton(_("Save the rows chosen"))
        save.clicked.connect(self._save)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(self.reject)
        row.addWidget(save)
        row.addWidget(cancel)
        box.addLayout(row)
        asyncio.ensure_future(self._load(""))

    def _search(self) -> None:
        asyncio.ensure_future(self._load(self.search.text()))

    async def _load(self, text: str) -> None:
        from ic705suite.dashboard.quicklog import repeaters

        try:
            self.found = await asyncio.to_thread(repeaters, text)
        except Exception as exc:  # noqa: BLE001 - said in the dialog
            self.note.setText(_("logbook not available: {error}").format(error=exc))
            return
        self.table.setRowCount(len(self.found))
        for row, memory in enumerate(self.found):
            cells = [memory["name"], frequency_text(memory["frequency"]), memory["mode"],
                     repeater_text(memory), memory["note"]]
            for column, text in enumerate(cells):
                self.table.setItem(row, column, item(text))
        self.note.setText(_("{count} repeaters").format(count=len(self.found)))

    def _save(self) -> None:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        self.chosen = [self.found[row] for row in rows]
        self.accept()
