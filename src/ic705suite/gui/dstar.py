"""D-STAR in the graphical client: the terminal's screen (apps.dstar.app), with pixels. The
same parts (the reflector with D-STAR's lamps, the station on air, the history), the same
keys and actions; the mouse on top: the reflector chosen from a list and changed with a
button, the note and the message typed in place, a row of the history clicked to show
that over.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
)

from ic705suite.apps.dstar.app import (
    HINT,
    SHORT_HINT,
    DStarApp,
    error_text,
    extra,
    heard_here,
    history_cells,
    history_columns,
    indicators,
    on_air,
    reflector_info,
    warning,
)
from ic705suite.gui import theme
from ic705suite.gui.page import Page
from ic705suite.gui.widgets import Footer, Frame, LoadLine, StatusLine, Table, badge, esc, span
from ic705suite.i18n import _, render
from ic705suite.station.links import Link


class DStarPage(Page):
    TUI = DStarApp

    def __init__(self, window, link: Link, config, owns_link: bool = False) -> None:
        super().__init__(window, link, config)
        self.owns_link = owns_link
        self.state: dict = {}
        self.rows: list[dict] = []
        self.shown: list[dict] = []
        self.reflectors: list[dict] = []
        self.choice = ""
        self.review: dict | None = None
        self.hide_mine = False
        self.history_version = -1
        self.started_here = False
        self._build()

    # ---- layout -------------------------------------------------------------------------

    def _build(self) -> None:
        self.status = StatusLine(self.link.description)
        self.reflector_frame = Frame(_("Reflector"))
        self.lamps = self.reflector_frame.add(QLabel())
        self.lamps.setTextFormat(Qt.TextFormat.RichText)
        self.reflector_label = self.reflector_frame.add(QLabel())
        self.reflector_label.setTextFormat(Qt.TextFormat.RichText)
        self.reflector_label.setWordWrap(True)
        row = QHBoxLayout()
        self.reflector_box = QComboBox()
        self.reflector_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reflector_box.activated.connect(self._picked_reflector)
        row.addWidget(self.reflector_box, 1)
        self.change_button = QPushButton(f"C  {_('Change')}")
        self.change_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.change_button.setToolTip(_("Write this TO to the radio (Terminal Mode with its "
                                        "internal gateway); then a short PTT registers on "
                                        "the reflector"))
        self.change_button.clicked.connect(self._change_clicked)
        row.addWidget(self.change_button)
        self.reflector_frame.body.addLayout(row)
        fields = QHBoxLayout()
        fields.addWidget(QLabel(_("MY note")))
        self.note_edit = QLineEdit()
        self.note_edit.setMaxLength(4)
        self.note_edit.setFixedWidth(64)
        self.note_edit.setToolTip(_("The 4 characters after your call sign (/xxxx), written "
                                    "to the radio with Enter"))
        self.note_edit.returnPressed.connect(lambda: self.run_worker(self._typed_note(),
                                                                     group="tx"))
        fields.addWidget(self.note_edit)
        fields.addSpacing(12)
        fields.addWidget(QLabel(_("TX message")))
        self.message_edit = QLineEdit()
        self.message_edit.setMaxLength(20)
        self.message_edit.setToolTip(_("The 20-character message your overs carry, written "
                                       "to the radio with Enter"))
        self.message_edit.returnPressed.connect(lambda: self.run_worker(
            self._typed_message(), group="tx"))
        fields.addWidget(self.message_edit, 1)
        self.reflector_frame.body.addLayout(fields)
        self.reflector_frame.body.addStretch(1)

        self.air_frame = Frame(_("On air"))
        self.air_label = self.air_frame.add(QLabel(), 1)
        self.air_label.setTextFormat(Qt.TextFormat.RichText)
        self.air_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.air_label.setWordWrap(True)
        self.air_label.setMinimumHeight(170)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self.reflector_frame)
        top.addWidget(self.air_frame)
        top.setChildrenCollapsible(False)
        top.setSizes([620, 620])

        self.history_frame = Frame(_("History"))
        self.mine_box = QCheckBox(f"H  {_('Hide mine')}")
        self.mine_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mine_box.toggled.connect(lambda on: on != self.hide_mine and self.action_hide_mine())
        self.history_frame.head.addWidget(self.mine_box)
        self.table = self.history_frame.add(Table(0, 7), 1)
        self.table.pass_letters = True                 # [ ] C N M H are the page's
        self.table.setHorizontalHeaderLabels(history_columns())
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellClicked.connect(lambda row, _c: self._review_row(row))
        self.table.cellActivated.connect(lambda row, _c: self._review_row(row))

        panes = QSplitter(Qt.Orientation.Vertical)
        panes.addWidget(top)
        panes.addWidget(self.history_frame)
        panes.setChildrenCollapsible(False)
        panes.setSizes([330, 420])

        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.footer = Footer([HINT, SHORT_HINT])
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 6, 0)
        box.setSpacing(6)
        box.addWidget(self.status)
        box.addWidget(panes, 1)
        box.addWidget(self.message)
        box.addWidget(LoadLine())
        box.addWidget(self.footer)
        self._show()

    # ---- life ---------------------------------------------------------------------------

    def start(self) -> None:
        self.link.attach(self._on_event)
        self.run_worker(self._start())

    def restore_focus(self) -> None:
        self.table.setFocus()

    # The terminal's: starting, loading, and every key.
    _start = DStarApp._start
    _load_history = DStarApp._load_history

    async def _load_reflectors(self) -> None:
        await DStarApp._load_reflectors(self)
        self._fill_reflectors()
        self._show()
    shown_rows = DStarApp.shown_rows
    action_reflector = DStarApp.action_reflector
    action_change = DStarApp.action_change
    _change = DStarApp._change
    action_note = DStarApp.action_note
    _note = DStarApp._note
    action_message = DStarApp.action_message
    _tx_message = DStarApp._tx_message
    action_hide_mine = DStarApp.action_hide_mine
    action_escape = DStarApp.action_escape
    action_leave = DStarApp.action_leave
    _leave = DStarApp._leave

    @property
    def is_running(self) -> bool:
        return True

    # ---- events -------------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "rig":
            self.status.update_rig(event)
        elif kind == "dstar":
            self.state = event
            if event.get("history_version") != self.history_version:
                self.history_version = event.get("history_version")
                self.run_worker(self._load_history(), group="history", exclusive=True)
            self._show()
        elif kind == "reconnected":
            self.run_worker(self._start(), group="start", exclusive=True)
        elif kind in ("info", "error") and str(event.get("msgid", "")).startswith("D-STAR"):
            self._message(kind, render(event))

    # ---- drawing ------------------------------------------------------------------------

    def _show(self) -> None:
        state = self.state
        self.status.extra = extra(state)
        self.status.redraw()
        colours = {"on": ("#000000", theme.BG_GREEN), "bad": ("#ffffff", theme.RED)}
        self.lamps.setText("".join(
            badge(name, *colours[lamp]) if lamp in colours else
            span(f"\u00a0{name}\u00a0", theme.DIM) + "&nbsp;"
            for name, lamp in indicators(state)))
        info = reflector_info(state)
        lines = [span(info.get("name") or "—", theme.AMBER, True) + "&nbsp;&nbsp;"
                 + span(info.get("to") or "", theme.CYAN, True)]
        if info.get("description"):
            lines.append(esc(info["description"]))
        server = info["radio_server"]
        lines.append(span(_("Server") + " ", theme.DIM)
                     + (span(server, theme.TEXT, True) if server else
                        span(_("(set [dstar] server)"), theme.DIM))
                     + ("&nbsp;&nbsp;" + span(_("{to} is reached through {needed}").format(
                         to=info.get("to", ""), needed=info.get("server", "")), theme.RED, True)
                        if info["mismatch"] else ""))
        lines.append(" &nbsp; ".join(
            span(label, theme.DIM) + " " + span(state.get(key) or "—", theme.TEXT, key == "ur")
            for label, key in (("UR", "ur"), ("R1", "r1"), ("R2", "r2"))))
        if self.choice and self.choice != state.get("ur"):
            lines.append(span(_("C changes to {to}").format(to=self.choice), theme.YELLOW, True))
        problem = warning(state)
        if problem:
            lines.append(span("⚠ " + problem, theme.RED, True))
        self.reflector_label.setText("<br>".join(lines))
        if not self.note_edit.hasFocus():
            self.note_edit.setText(state.get("my_note", ""))
        if not self.message_edit.hasFocus():
            self.message_edit.setText(state.get("tx_message", ""))
        wanted = self.choice or state.get("ur", "")
        index = self.reflector_box.findData(wanted)
        if index >= 0 and not self.reflector_box.view().isVisible():
            self.reflector_box.setCurrentIndex(index)
        self._show_on_air()

    def _show_on_air(self) -> None:
        air = on_air(self.state, self.review)
        if not air["title"]:
            html = span(_("no activity"), theme.DIM)
        else:
            colour = {"RX": theme.GREEN, "TX": theme.RED, "HISTORY": theme.MAGENTA}.get(
                air["title"], theme.DIM)
            call_colour = theme.RED if air.get("tx") else theme.GREEN if air.get("live") \
                else theme.AMBER
            html = (span(_(air["title"]), colour, True) + "&nbsp;&nbsp;"
                    + span(air["time"], theme.DIM)
                    + f"<p style='margin:2px 0;font-family:\"{theme.mono().family()}\";"
                      f"font-size:30px;font-weight:bold;color:{call_colour}'>"
                      f"{esc(air['call'])}</p>"
                    + "".join(f"<p style='margin:0'>"
                              f"{span(line, theme.CYAN if line.startswith('«') else theme.TEXT)}"
                              "</p>" for line in air["lines"]))
        heard = heard_here(self.rows, self.state.get("ur", ""))
        if heard:
            html += "<p style='margin:14px 0 2px 0'>" + span(_("Last heard here"), theme.DIM,
                                                              True) + "</p>" + \
                "".join("<p style='margin:0'>" + span(call, theme.AMBER, True) + "&nbsp; "
                        + esc(name) + "&nbsp; " + span(hhmm, theme.DIM) + "</p>"
                        for call, name, hhmm in heard)
        self.air_label.setText(html)

    def retheme(self) -> None:
        self._show()
        self._fill_history()

    def _fill_history(self) -> None:
        self.shown = self.shown_rows()
        locator = self.state.get("locator", "")
        with self.table.filling():
            self.table.setRowCount(len(self.shown))
            for i, r in enumerate(self.shown):
                for column, text in enumerate(history_cells(r, locator)):
                    item = QTableWidgetItem(text)
                    if column == 1:
                        item.setForeground(QColor(theme.RED if r["direction"] == "TX"
                                                  else theme.CYAN))
                    self.table.setItem(i, column, item)
        self.table.resizeColumnsToContents()
        self.mine_box.blockSignals(True)
        self.mine_box.setChecked(self.hide_mine)
        self.mine_box.blockSignals(False)

    def _message(self, kind: str, text: str) -> None:
        self.message.setText(span(text, theme.RED if kind == "error" else theme.CYAN))

    # ---- the mouse ----------------------------------------------------------------------

    def _review_row(self, row: int) -> None:
        """A row of the history: that over in the on-air box (Esc, or live again)."""
        if 0 <= row < len(self.shown):
            self.review = self.shown[row]
            self._show_on_air()

    def _picked_reflector(self, index: int) -> None:
        self.choice = self.reflector_box.itemData(index) or ""
        self._show()

    def _change_clicked(self) -> None:
        self.choice = self.reflector_box.currentData() or ""
        self.action_change()

    def _fill_reflectors(self) -> None:
        self.reflector_box.clear()
        for r in self.reflectors:
            label = f"{r['to']}  {r.get('name') or ''}".strip()
            if r.get("server"):
                label += f"  ·  {r['server']}"
            self.reflector_box.addItem(label, r["to"])

    async def _typed_note(self) -> None:
        reply = await self.link.call("dstar.note", note=self.note_edit.text())
        self.table.setFocus()
        self._message("error" if reply.get("error") else "info",
                      error_text(reply) if reply.get("error") else
                      _("MY note /{note} written").format(note=reply["note"]))

    async def _typed_message(self) -> None:
        reply = await self.link.call("dstar.message", text=self.message_edit.text())
        self.table.setFocus()
        self._message("error" if reply.get("error") else "info",
                      error_text(reply) if reply.get("error") else _("TX message written"))
