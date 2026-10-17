"""The dashboard of the graphical client: the terminal's (dashboard.app), window for window.

Left, the applications and the quick logbook entry with today's contacts; right, the rig at a
glance, a small band scope, the basic settings and the station; below, the core's messages.
The keys are the terminal dashboard's; the mouse opens an application (a click on its icon), picks
and turns a setting (click, wheel) and tunes on the scope (click, wheel).
"""

from __future__ import annotations

import asyncio
import base64

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ic705suite import __version__
from ic705suite.apps import BY_ID, catalog
from ic705suite.apps.swr.web import antenna_url
from ic705suite.dashboard.app import (
    DASH_GROUPS,
    FEATURE_NAMES,
    HINT,
    MAX_MESSAGES,
    SCOPE_KEEPALIVE,
    SHORT_HINT,
    TUNE_STEP,
    DashboardApp,
    dashboard_controls,
)
from ic705suite.gui import theme
from ic705suite.gui.appicons import app_icon
from ic705suite.gui.page import Page
from ic705suite.gui.widgets import (
    ControlGrid,
    Footer,
    Frame,
    LoadLine,
    Meter,
    Scope,
    StatusLine,
    Table,
    esc,
    smeter,
    span,
)
from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.station.links import Link, LinkError, LocalLink
from ic705suite.ui.channels import ChannelSaving
from ic705suite.ui.panelkeys import PanelKeys
from ic705suite.ui.panelmodel import PanelModel
from ic705suite.ui.rigkeys import tune_target

# The applications the graphical client has so far; the others still open in the terminal.
GUI_APPS = {"view", "cw", "swr", "log", "packet", "ft8", "phone"}
NOT_YET = N_("{name}: not in the graphical client yet; in the terminal: ic705suite {app}")
ICON_SIZE = 48                 # the applications' icons, in pixels
CELL = QSize(116, 100)         # each application: its icon, its name and its key
ITEM = QSize(110, 94)          # the item in its cell (the rest, room between them)
COLUMNS = 4                    # applications to a row: always 4 + 3, whatever the width


class AppGrid(QListWidget):
    """The applications as big icons, each with its name and key, COLUMNS to a row: always
    the same size and the same rows (4 + 3), never a scroll bar. A click opens one; the
    arrows choose and Enter opens, as on the terminal's list."""

    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setGridSize(CELL)
        self.setSpacing(0)
        self.setWordWrap(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedWidth(COLUMNS * CELL.width() + 2 * self.frameWidth() + 4)

    def fit(self) -> None:
        """As tall as the rows the applications need."""
        rows = max(1, -(-self.count() // COLUMNS))
        self.setFixedHeight(rows * CELL.height() + 2 * self.frameWidth() + 4)


class DashboardPage(PanelKeys, ChannelSaving, Page):
    TUI = DashboardApp

    def __init__(self, window, link: Link | None, config, problem: str = "") -> None:
        super().__init__(window, link, config)
        self.problem = problem
        self.messages: list[tuple[str, str]] = []
        self.apps = catalog(None)
        self.rig: dict = {}
        self.welcome: dict = {}
        self.chosen: str | None = None
        self._scope_asked = False
        self.model = PanelModel(DASH_GROUPS)
        self._build()
        self.scope_timer = QTimer(self, interval=int(SCOPE_KEEPALIVE * 1000),
                                  timeout=lambda: self.run_worker(self._keep_scope()))

    # ---- layout -------------------------------------------------------------------------

    def _build(self) -> None:
        self.status = StatusLine(self.link.description if self.link else _("no core"))
        self.apps_frame = Frame(_("Applications"))
        self.table = self.apps_frame.add(AppGrid())
        # A click opens ([ui] open_apps: a tab or a window); Enter comes through the page's
        # keys (action_press); a right click offers both.
        self.table.clicked.connect(lambda _index: self.action_open_chosen())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._app_menu)

        self.quick_frame = Frame(_("Quick log · today")
                                 + (" · " + _("demo") if self.config.connection == "sim" else ""))
        self.qso_input = self.quick_frame.add(QLineEdit())
        self.qso_input.returnPressed.connect(lambda: self.run_worker(self._log_line()))
        self.qso_table = self.quick_frame.add(Table(0, 8), 1)
        self.qso_table.setHorizontalHeaderLabels([_("UTC"), _("Call"), _("Name"), _("Band"),
                                                  _("Mode"), _("Sent"), _("Rcvd"), _("QTH")])
        self.qso_table.verticalHeader().hide()
        self.qso_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.qso_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.qso_table.setShowGrid(False)
        self.qso_table.horizontalHeader().setStretchLastSection(True)

        self.rig_frame = Frame(_("Rig"))
        self.rig_label = self.rig_frame.add(QLabel())
        self.rig_label.setTextFormat(Qt.TextFormat.RichText)
        self.rig_meter = self.rig_frame.add(Meter("S"))
        self.rig_tx = self.rig_frame.add(QLabel())
        self.rig_tx.setTextFormat(Qt.TextFormat.RichText)

        self.scope_frame = Frame(_("Scope"))
        self.scope = self.scope_frame.add(Scope(spectrum_share=0.45), 1)
        self.scope.clicked.connect(lambda hz: self.run_worker(
            self._set_special("frequency", hz / 1e6)))
        self.scope.wheel.connect(lambda d: self.run_worker(self.action_tune(d)))

        self.controls_frame = Frame(_("Settings"))
        self.grid = self.controls_frame.add(ControlGrid(self.model, bars=False))
        self.grid.request.connect(self._grid_request)

        self.station_frame = Frame(_("Station"))
        self.station_label = self.station_frame.add(QLabel())
        self.station_label.setTextFormat(Qt.TextFormat.RichText)

        self.log_frame = Frame(_("Messages"))
        self.log_label = self.log_frame.add(QLabel(), 1)
        self.log_label.setTextFormat(Qt.TextFormat.RichText)
        self.log_label.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)

        self.footer = Footer([HINT, SHORT_HINT])

        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.addWidget(self.apps_frame)
        left_box.addWidget(self.quick_frame, 1)
        side = QWidget()
        side_box = QVBoxLayout(side)
        side_box.setContentsMargins(0, 0, 0, 0)
        for frame, stretch in ((self.rig_frame, 0), (self.scope_frame, 1),
                               (self.controls_frame, 0), (self.station_frame, 0)):
            side_box.addWidget(frame, stretch)
        # The left column as wide as the applications (it never changes); the right one
        # takes whatever width the window has besides.
        top = QWidget()
        top_box = QHBoxLayout(top)
        top_box.setContentsMargins(0, 0, 0, 0)
        top_box.addWidget(left)
        top_box.addWidget(side, 1)
        left.setFixedWidth(self.apps_frame.sizeHint().width())
        middle = QSplitter(Qt.Orientation.Vertical)
        middle.addWidget(top)
        middle.addWidget(self.log_frame)
        middle.setSizes([700, 150])
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 6, 0)
        box.setSpacing(6)
        box.addWidget(self.status)
        box.addWidget(middle, 1)
        box.addWidget(LoadLine())
        box.addWidget(self.footer)
        for widget in (self.rig_label, self.station_label, self.log_label, self.scope,
                       self.rig_meter, self.status, self.footer):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        QApplication.instance().focusChanged.connect(self._focus_changed)
        self._fill_apps()
        self._show_rig()
        self._show_station()

    def _focus_changed(self, _old, new) -> None:
        for frame in (self.apps_frame, self.quick_frame, self.controls_frame):
            frame.set_active(new is not None and frame.isAncestorOf(new))

    # ---- life ---------------------------------------------------------------------------

    def start(self) -> None:
        self.rig_label.setText(span(self.problem or _("connecting to the core..."), theme.DIM))
        if self.link is not None:
            self.messages.clear()          # the link replays the core's messages
            self.link.attach(self._on_event)
        elif self.problem:
            self._message("error", self.problem)
        self.scope_timer.start()
        self._fill_apps()
        self.table.setFocus()
        self.run_worker(self._quicklog_start())

    def restore_focus(self) -> None:
        self.table.setFocus()

    def stop(self) -> None:
        self.scope_timer.stop()
        super().stop()

    # ---- events -------------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "rig":
            self.rig = event
            self.status.update_rig(event)
            self._show_rig()
            self._apply_rig(event)
        elif kind == "scope":
            data = np.frombuffer(base64.b64decode(event["data"]), dtype=np.uint8) / 255.0
            self.scope.add_frame(data, int(event["start"]), int(event["end"]))
            span_khz = (event["end"] - event["start"]) / 2000
            self.scope_frame.set_title(f"{_('Scope')} ±{span_khz:g} kHz")
        elif kind == "welcome":
            self.welcome = event
            self.apps = event.get("apps") or self.apps
            self.model.set_controls(dashboard_controls(event.get("rig", {})))
            self._show_station()
            self._show_rig()
            self._fill_apps()
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _apply_rig(self, event: dict) -> None:
        from ic705suite.apps.view.app import passband

        if event.get("connected") and not self._scope_asked:
            self._scope_asked = True
            self.run_worker(self._keep_scope())
        elif not event.get("connected"):
            self._scope_asked = False
        values = dict(event.get("controls", {}))
        frequency = event.get("frequency")
        values["frequency"] = frequency / 1e6 if frequency else None
        values["band"] = event.get("band")
        values["mode"] = event.get("mode")
        if not self.model.editing:
            self.model.set_values(values)
        self.scope.view.dial = frequency
        self.scope.view.passband = passband(event.get("mode"), frequency,
                                            values.get("filter_width"))
        self.scope.update()

    async def _keep_scope(self) -> None:
        if self.link is None or not self.rig.get("connected"):
            return
        try:
            await self.link.call("scope.watch", seconds=SCOPE_KEEPALIVE * 3)
        except (LinkError, OSError):
            pass

    def retheme(self) -> None:
        self._fill_apps()                   # the icons, in the new colours
        self._show_rig()
        self._show_station()

    def _show_rig(self) -> None:
        rig, info = self.rig, self.welcome.get("rig", {})
        if not rig and not self.welcome:
            return
        name = f"{info.get('manufacturer', '')} " \
               f"{info.get('name', rig.get('model_name', ''))}".strip() or "-"
        lines = [span(name, theme.CYAN, True)
                 + (span(f"  ({rig['radio']})", theme.DIM)
                    if rig.get("radio") and rig["radio"] != info.get("name") else "")]
        state = _("connected") if rig.get("connected") else _("not connected")
        lines.append(span(f"{_('Link')}: ", theme.DIM) + span(f"{rig.get('transport', '-')} · ",
                                                              theme.TEXT)
                     + span(state, theme.GREEN if rig.get("connected") else theme.RED, True))
        freq = bands.format_frequency(rig.get("frequency")) if rig.get("frequency") else "-"
        lines.append(span(freq, theme.YELLOW, True) + span(" MHz", theme.DIM)
                     + (span(f"   {rig['band']}", theme.TEXT, True) if rig.get("band") else "")
                     + span(f"   {rig.get('mode') or '-'}", theme.GREEN, True))
        if rig.get("power") is not None:
            watts = rig["power"] * rig.get("max_power_w", 10) / 100
            lines.append(span(f"{_('Power')} ", theme.DIM)
                         + span(f"{rig['power']}% ({watts:.1f} W)", theme.TEXT))
        self.rig_label.setText("<br>".join(lines))
        smeter(self.rig_meter, rig.get("smeter") if rig else None)
        if rig.get("tx_enabled"):
            self.rig_tx.setText(span(_("Transmission enabled"), theme.RED, True))
        else:
            self.rig_tx.setText(span(_("Receive only (start with --tx to transmit)"),
                                     theme.YELLOW, True))

    def _show_station(self) -> None:
        from ic705suite import db

        call = self.welcome.get("callsign") or self.config.callsign or "-"
        lines = [span(f"{_('Callsign')}: ", theme.DIM) + span(call, theme.TEXT, True),
                 span(f"{_('Database')}: ", theme.DIM)
                 + span(db.describe(self.config.database_url or None), theme.TEXT)]
        features = [_(FEATURE_NAMES.get(f, f))
                    for f in self.welcome.get("rig", {}).get("features", [])]
        if features:
            lines.append(span(f"{_('Can do')}: ", theme.DIM)
                         + span(", ".join(features), theme.TEXT))
        url = antenna_url(self.welcome.get("swr_url"), self.welcome.get("swr_port"))
        if url:
            lines.append(span(f"{_('Antenna page')}: ", theme.DIM)
                         + f'<a style="color:{theme.CYAN}" href="{esc(url)}">{esc(url)}</a>')
        lines.append(span(f"ic705suite {__version__}", theme.DIM))
        self.station_label.setText("<br>".join(lines))
        self.station_label.setOpenExternalLinks(True)

    # ---- messages -------------------------------------------------------------------------

    def _message(self, kind: str, text: str) -> None:
        if self.messages and self.messages[-1] == (kind, text):
            return
        self.messages.append((kind, text))
        del self.messages[:-MAX_MESSAGES]
        rows = max(1, self.log_label.height() // max(1, self.log_label.fontMetrics().height()))
        self.log_label.setText("<br>".join(
            span(line, theme.RED if k == "error" else theme.CYAN, k == "error")
            for k, line in self.messages[-rows:]))

    def _note(self, kind: str, text: str) -> None:
        """Help while moving goes on the settings' edge, not into the messages."""
        self.controls_frame.note(text[:120], kind)

    # ---- the applications ---------------------------------------------------------------------

    def _usable(self, app: dict) -> bool:
        return app["ready"] and app["available"] and not self._lacks_core(app)

    def _lacks_core(self, app: dict) -> bool:
        """It runs on a core and there is none here (packet may use another machine's)."""
        if app["id"] == "packet" and self.config.packet_core:
            return False
        return app["needs_rig"] and self.link is None

    def _fill_apps(self) -> None:
        grid = self.table
        row = grid.currentRow() if grid.count() else 0
        model = self.welcome.get("rig", {}).get("name", "IC-705")
        grid.clear()
        for app in self.apps:
            usable = self._usable(app)
            name = _(app["name"]).format(model=model)
            description = _(app["description"])
            if not app["ready"]:
                description = f"{_('coming soon')} · {description}"
            elif not app["available"]:
                description = f"{_('this rig cannot')} · {description}"
            elif self._lacks_core(app):
                description = f"{_('needs the radio')} · {description}"
            item = QListWidgetItem(app_icon(app["id"], theme.CYAN if usable else theme.DIM,
                                            ICON_SIZE), f"{name}\n{app['key']}")
            item.setToolTip(f"{app['key']}  {name}\n{description}")
            item.setForeground(QColor(theme.TEXT if usable else theme.DIM))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setSizeHint(ITEM)          # its whole name, not elided to the icon's width
            if usable:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            grid.addItem(item)
        grid.fit()
        ids = [a["id"] for a in self.apps]
        if self.chosen in ids:
            row = ids.index(self.chosen)
        if self.apps:
            grid.setCurrentRow(min(max(row, 0), len(self.apps) - 1))

    def action_open_chosen(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.apps):
            self.action_open(self.apps[row]["id"])

    def _app_menu(self, point) -> None:
        """A right click on an application: open it in a tab or in a window of its own."""
        item = self.table.itemAt(point)
        if item is None:
            return
        app_id = self.apps[self.table.row(item)]["id"]
        self.table.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(_("Open in a tab"), lambda: self.action_open(app_id, "tab"))
        menu.addAction(_("Open in a new window"), lambda: self.action_open(app_id, "window"))
        menu.popup(self.table.viewport().mapToGlobal(point))

    def action_open(self, app_id: str, mode: str | None = None) -> None:
        """Open an application: in a tab or a window of its own (`mode`, else as [ui]
        open_apps says)."""
        app = next((a for a in self.apps if a["id"] == app_id), None)
        if app is None:
            return
        name = _(BY_ID[app_id].name).format(model=self.rig.get("model_name", "IC-705"))
        if not self._usable(app):
            reason = (_("coming soon") if not app["ready"] else _("this rig cannot")
                      if not app["available"] else _("needs the radio"))
            self._message("error", f"{name}: {reason}")
            return
        if app_id not in GUI_APPS:
            self._message("error", _(NOT_YET).format(name=name, app=app_id))
            return
        self.chosen = app_id
        if mode is None:
            self.exit(app_id)
        else:
            self.window_.open_app(app_id, mode)

    # ---- the settings (front panel keys) --------------------------------------------------------

    def _panel(self) -> PanelModel:
        return self.model

    def _panel_active(self) -> bool:
        return self.focused is self.grid and len(self.screen_stack) == 1

    def _typed_value(self, panel: PanelModel):
        if panel.selected == "frequency":
            return bands.parse_frequency(panel.buffer) / 1e6
        return panel.typed_value()

    async def _set_special(self, control_id: str, value) -> bool:
        if control_id == "frequency":
            line = f"/f {round(value * 1e6 / 10) * 10}"
        elif control_id == "band":
            line = f"/b {value}"
        elif control_id == "mode":
            line = f"/mode {value}"
        else:
            return False
        if self.link is None:
            self._message("error", _("no radio"))
            return True
        self.model.values[control_id] = value
        self.model.refresh()
        try:
            await self.link.command(line, app="rig")
        except LinkError as exc:
            self._message("error", str(exc))
        return True

    def _grid_request(self, what: str, direction: int) -> None:
        if what == "enter":
            self.run_worker(self.action_enter())
        else:
            self.run_worker(self.action_adjust(direction))

    # ---- keys ---------------------------------------------------------------------------

    async def action_press(self) -> None:
        """Enter or Space: the settings edit; on the applications, open the chosen one."""
        if self._panel_active():
            await self.action_enter()
        elif self.focused is self.table:
            self.action_open_chosen()

    async def action_escape(self) -> None:
        if self.focused is self.qso_input:
            if self.qso_input.text():
                self.qso_input.clear()           # first Esc clears the line
            else:
                self.table.setFocus()
        elif self._panel_active() and self.model.editing:
            await self._cancel_edit()
        elif self._panel_active():
            self.table.setFocus()

    async def action_tune(self, direction: int) -> None:
        if len(self.screen_stack) > 1:
            return
        freq = self.rig.get("frequency")
        if freq and self.link is not None:
            await self._set_special("frequency", tune_target(freq, TUNE_STEP, direction) / 1e6)

    def action_reconnect(self) -> None:
        if self.link is not None and not self.link.connected:
            from ic705suite.launcher import start_link

            start_link(self.link)

    def action_leave(self) -> None:
        self.run_worker(self._leave())

    async def _leave(self) -> None:
        if isinstance(getattr(self.link, "target", None), LocalLink) and self.link.connected \
                and not await self.offer_save_channels():
            return
        self.exit("quit")

    # ---- quick log ------------------------------------------------------------------------------

    async def _quicklog_start(self) -> None:
        from ic705suite.dashboard.quicklog import entry_hint

        try:
            hint = await asyncio.to_thread(entry_hint)
        except Exception:  # noqa: BLE001 - only a placeholder
            hint = ""
        self.qso_input.setPlaceholderText(hint or _("call, name, RST sent, RST rcvd..."))
        await self._quicklog_reload()

    async def _quicklog_reload(self) -> None:
        from ic705suite.dashboard.quicklog import today_contacts

        try:
            rows = await asyncio.to_thread(today_contacts)
        except Exception as exc:  # noqa: BLE001 - shown in the box
            self.quick_frame.note(_("logbook not available: {error}").format(error=exc), "error")
            return
        self.qso_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            cells = (f"{r.qso_utc:%H:%M}", r.call, r.name or "", r.band or "", r.mode or "",
                     r.rst_sent or "", r.rst_rcvd or "", r.qth or "")
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 1:
                    item.setForeground(QColor(theme.CYAN))
                self.qso_table.setItem(i, column, item)
        self.qso_table.resizeColumnsToContents()
        self.qso_table.scrollToBottom()
        self.quick_frame.note(_("{count} today").format(count=len(rows)), "ok")

    async def _log_line(self) -> None:
        from ic705suite.dashboard.quicklog import log_line

        line = self.qso_input.text().strip()
        if not line:
            return
        try:
            row, note = await asyncio.to_thread(log_line, line, self.rig)
        except Exception as exc:  # noqa: BLE001 - shown in the box
            self.quick_frame.note(str(exc), "error")
            return
        if row is None:
            self.quick_frame.note(note, "error")
            return
        self.qso_input.clear()
        await self._quicklog_reload()
        where = f"{row.band} {row.mode}".strip()
        self.quick_frame.note(_("logged {call} {where}").format(call=row.call, where=where)
                              + (f" · {note}" if note else ""), "ok")

