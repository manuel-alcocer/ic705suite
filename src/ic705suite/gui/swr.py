"""The Antenna application of the graphical client: the terminal's (apps.swr.app), with the
sweep drawn as a chart, and its antennas window (apps.swr.antennas).

The keys are the terminal's (F2-F9 band, N noise sweep, S SWR sweep, C save position, A
antennas, R remote control, Esc stop/back); the mouse picks a band and has a button for each.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ic705suite.apps.swr.antennas import SIGNS, AntennaScreen, settings_text
from ic705suite.apps.swr.app import HINT, SHORT_HINT, SwrApp, advice, noise_request, swr_request
from ic705suite.gui import theme
from ic705suite.gui.page import Overlay, Page, ask_text, choose
from ic705suite.gui.widgets import Footer, Frame, StatusLine, Table, badge, esc, span
from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.station.links import Link
from ic705suite.ui.widgets import ChoiceScreen, NameScreen

STYLE_COLOURS = {"bold red": theme.RED, "bold yellow": theme.YELLOW, "bold green": theme.GREEN}


class SweepChart(QWidget):
    """The sweep as a curve: SWR (green to red as it rises) or noise, with the target and
    the best point marked."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sweep: dict = {}
        self.target: int | None = None
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_sweep(self, sweep: dict, target: int | None) -> None:
        self.sweep, self.target = sweep, target
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        points = self.sweep.get("points") or []
        if not points:
            p.setPen(QColor(theme.DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       _("Choose a band and start a sweep."))
            return
        is_swr = self.sweep.get("kind") == "swr"
        values = [pt["v"] for pt in points]
        f_lo = min(self.sweep.get("start") or points[0]["f"], points[0]["f"])
        f_hi = max(self.sweep.get("stop") or points[-1]["f"], points[-1]["f"])
        lo, hi = (1.0, max(3.0, max(values))) if is_swr else (min(values), max(values) or 1)
        span_v, span_f = (hi - lo) or 1, (f_hi - f_lo) or 1
        left, right, top, bottom = 52, self.width() - 12, 10, self.height() - 26
        width, height = max(10, right - left), max(10, bottom - top)

        def x_of(f: float) -> float:
            return left + (f - f_lo) / span_f * width

        def y_of(v: float) -> float:
            return bottom - (v - lo) / span_v * height

        p.setFont(theme.mono(8))
        grid = QPen(QColor(theme.BORDER), 1)
        if is_swr:                      # the zones: good, fair, poor
            zones = ((1.0, 1.5, "#123d24"), (1.5, 2.0, "#3d3712"), (2.0, hi, "#3d1616"))
            for a, b, colour in zones:
                if b > a:
                    p.fillRect(QRectF(left, y_of(min(b, hi)), width, y_of(a) - y_of(min(b, hi))),
                               QColor(colour))
        for i in range(5):
            v = lo + span_v * i / 4
            y = y_of(v)
            p.setPen(grid)
            p.drawLine(QPointF(left, y), QPointF(right, y))
            p.setPen(QColor(theme.DIM))
            p.drawText(QRectF(0, y - 8, left - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{v:.1f}" if is_swr else f"{v:.0f}")
        for i in range(5):
            f = f_lo + span_f * i / 4
            x = x_of(f)
            p.setPen(grid)
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
            p.setPen(QColor(theme.DIM))
            p.drawText(QRectF(x - 40, bottom + 4, 80, 16), Qt.AlignmentFlag.AlignHCenter,
                       bands.format_frequency(round(f)))
        if self.target:
            x = x_of(self.target)
            p.setPen(QPen(QColor(theme.CYAN), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
        path = QPainterPath()
        for i, pt in enumerate(points):
            point = QPointF(x_of(pt["f"]), y_of(pt["v"]))
            path.moveTo(point) if i == 0 else path.lineTo(point)
        p.setPen(QPen(QColor(theme.CYAN if not is_swr else theme.TEXT), 2))
        p.drawPath(path)
        best = min(values) if is_swr else max(values)
        for pt in points:
            v = pt["v"]
            colour = theme.CYAN if not is_swr else theme.GREEN if v <= 1.5 else \
                theme.YELLOW if v <= 2 else theme.RED
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colour))
            radius = 5 if v == best else 3
            p.drawEllipse(QPointF(x_of(pt["f"]), y_of(v)), radius, radius)


class SwrPage(Page):
    TUI = SwrApp

    def __init__(self, window, link: Link, config, owns_link: bool = False) -> None:
        super().__init__(window, link, config)
        self.owns_link = owns_link
        self.bands: list[dict] = []
        self.state: dict = {}
        self.antenna: dict = {}
        self._build()

    def _build(self) -> None:
        self.status = StatusLine(self.link.description)
        self.bands_frame = Frame(_("Band"))
        self.table = self.bands_frame.add(Table(0, 6), 1)
        self.table.setHorizontalHeaderLabels([_("Key"), _("Band"), _("Target"), _("Position"),
                                              _("Resonance"), _("SWR")])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.currentCellChanged.connect(lambda *_a: self._show_sweep())
        actions = QHBoxLayout()
        for key, label, action in (("N", N_("Noise sweep (RX)"),
                                    lambda: self.action_sweep("noise")),
                                   ("S", N_("SWR sweep (TX)"), lambda: self.action_sweep("swr")),
                                   ("C", N_("Save position"), self.action_final),
                                   ("A", N_("Antennas"), self.action_antennas),
                                   ("R", N_("Remote control"), self.action_remote),
                                   ("Esc", N_("Stop/Back"), self.action_back)):
            button = QPushButton(f"{key}  {_(label)}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _c=False, a=action: self._run(a))
            actions.addWidget(button)
        self.bands_frame.body.addLayout(actions)

        self.sweep_frame = Frame(_("Antenna"))
        self.remote = self.sweep_frame.add(QLabel())
        self.remote.setTextFormat(Qt.TextFormat.RichText)
        self.notes = self.sweep_frame.add(QLabel())
        self.notes.setTextFormat(Qt.TextFormat.RichText)
        self.notes.setOpenExternalLinks(True)
        self.verdict = self.sweep_frame.add(QLabel())
        self.verdict.setTextFormat(Qt.TextFormat.RichText)
        self.verdict.setWordWrap(True)
        self.chart = self.sweep_frame.add(SweepChart(), 1)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.footer = Footer([HINT, SHORT_HINT])

        top = QHBoxLayout()
        top.addWidget(self.bands_frame, 2)
        top.addWidget(self.sweep_frame, 3)
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 6, 0)
        box.setSpacing(6)
        box.addWidget(self.status)
        box.addLayout(top, 1)
        box.addWidget(self.message)
        box.addWidget(self.footer)
        self._show_state()

    def _run(self, action) -> None:
        result = action()
        if asyncio.iscoroutine(result):
            self.run_worker(result)

    # ---- life ---------------------------------------------------------------------------

    def start(self) -> None:
        self.link.attach(self._on_event)
        self.setFocus()
        self.run_worker(self._start())

    async def _start(self) -> None:
        try:
            await self.link.connect()
            self.bands = (await self.link.call("swr.bands")).get("bands", [])
            self._apply_state(await self.link.call("swr.state"))
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._message("error", _("cannot connect: {error}").format(error=exc))
            return
        await self._fill_bands()
        rig_band = (self.link.latest.get("rig") or {}).get("band")
        names = [b["name"] for b in self.bands]
        if rig_band in names:
            self.table.selectRow(names.index(rig_band))

    async def _fill_bands(self) -> None:
        reply = await self.link.call("swr.antennas")
        self.antenna = next((a for a in reply.get("antennas", []) if a["active"]), {})
        settings = self.antenna.get("settings", {})
        row = max(0, self.table.currentRow())
        self.table.setRowCount(len(self.bands))
        for i, band in enumerate(self.bands):
            saved = settings.get(band["name"], {})
            cells = (f"F{i + 2}", band["name"], bands.format_frequency(band["target"]),
                     f"{saved['coil']:g}" if saved else "",
                     bands.format_frequency(saved["resonance"]) if saved.get("resonance") else "",
                     f"{saved['best']:.2f}" if saved.get("best") is not None else "")
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(theme.CYAN if column == 0 else theme.TEXT))
                self.table.setItem(i, column, item)
        if self.bands:
            self.table.selectRow(min(row, len(self.bands) - 1))
        self.sweep_frame.set_title(f"{_('Antenna')} · {self.antenna.get('name', '')}")

    # ---- events -------------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "swr":
            self._apply_state(event)
        elif kind == "rig":
            self.status.update_rig(event)
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _apply_state(self, state: dict) -> None:
        if "sweep" not in state:
            return
        self.state = state
        self._show_state()

    def _show_state(self) -> None:
        """The terminal's SweepPanel: remote control, receive only, phone page, the sweep's
        verdict and advice; the points as a chart."""
        state = self.state
        if not state:
            self.remote.setText(span(_("waiting for the core..."), theme.DIM))
            return
        if state.get("remote"):
            remote = badge(_("ON"), "#ffffff", theme.RED) + span(
                _("the phone can sweep; SWR sweeps allowed") + "  " + _("(R switches it off)"),
                theme.TEXT)
        else:
            remote = badge(_("OFF"), "#ffffff", "#2e5aa8") + span(
                _("the phone only watches; no SWR sweeps") + "  " + _("(R switches it on)"),
                theme.TEXT)
        self.remote.setText(span(_("Remote control") + ": ", theme.DIM) + remote)
        notes = []
        if not state.get("allow_tx"):
            notes.append(span(_("Receive only: noise sweeps only (start with --tx for SWR "
                                "sweeps)"), theme.YELLOW, True))
        if state.get("port"):
            from ic705suite.apps.swr.web import lan_address

            url = f"http://{lan_address()}:{state['port']}"
            notes.append(span(_("Phone page") + ": ", theme.DIM)
                         + f'<a style="color:{theme.CYAN}" href="{esc(url)}">{esc(url)}</a>')
        self.notes.setText("<br>".join(notes))
        self._show_sweep()

    def _show_sweep(self) -> None:
        sweep = (self.state or {}).get("sweep") or {}
        band = self._band()
        if not sweep.get("kind"):
            self.verdict.setText(span(_("Choose a band and start a sweep."), theme.DIM))
            self.chart.set_sweep({}, band["target"] if band else None)
            return
        points = sweep.get("points") or []
        is_swr = sweep["kind"] == "swr"
        title = _("SWR sweep") if is_swr else _("Noise sweep")
        lines = [span(f"{title} {sweep.get('band', '')}", theme.CYAN, True)
                 + span(f"  {len(points)}/{sweep.get('total', 0)}", theme.TEXT)
                 + (span("  " + _("running..."), theme.YELLOW, True) if sweep.get("running")
                    else "")]
        if not sweep.get("running"):
            verdict, style, words = advice(sweep)
            lines.append(span(verdict, STYLE_COLOURS.get(style, theme.TEXT), True))
            if words:
                lines.append(span(words, theme.TEXT))
            a = sweep.get("analysis")
            if a:
                best = f"{a['best_value']:.2f}" if is_swr else f"{a['best_value']:.0f}"
                lines.append(span(f"{_('Resonance')} ", theme.DIM)
                             + span(f"{bands.format_frequency(a['resonance'])} MHz", theme.TEXT,
                                    True)
                             + span(f"   {_('Lowest SWR') if is_swr else _('Noise peak')} ",
                                    theme.DIM) + span(best, theme.TEXT, True)
                             + span(f"   {_('Off target')} ", theme.DIM)
                             + span(f"{a['delta_khz']:+.0f} kHz", theme.TEXT, True))
        self.verdict.setText("<br>".join(lines))
        target = next((b["target"] for b in self.bands if b["name"] == sweep.get("band")),
                      band["target"] if band else None)
        self.chart.set_sweep(sweep, target)

    def _message(self, kind: str, text: str) -> None:
        self.message.setText(span(" " + text[:200], theme.RED if kind == "error" else theme.CYAN,
                                  kind == "error"))

    def _band(self) -> dict | None:
        row = self.table.currentRow()
        return self.bands[row] if self.bands and 0 <= row < len(self.bands) else None

    # ---- keys (the terminal's actions) ---------------------------------------------------------

    def action_band(self, index: int) -> None:
        if 0 <= index < len(self.bands):
            self.table.selectRow(index)

    async def action_sweep(self, kind: str) -> None:
        band = self._band()
        if band is None:
            return
        if kind == "swr" and not self.state.get("remote"):
            self._message("error", _("SWR sweeps need the remote control on: press R"))
            return
        request = noise_request(band) if kind == "noise" else swr_request(band)
        result = await self.link.call("swr.sweep", **request)
        if result.get("error"):
            self._message("error", result["error"])
        else:
            self._apply_state(result)

    def action_final(self) -> None:
        self.run_worker(self._final())

    async def _final(self) -> None:
        band = self._band()
        if band is None:
            return
        saved = self.antenna.get("settings", {}).get(band["name"], {})
        coil = await self.push_screen_wait(NameScreen(
            _("Position on {band}").format(band=band["name"]),
            _("Which number did the coil (or adjustment) of {antenna} end on?").format(
                antenna=self.antenna.get("name", _("the antenna"))),
            value=f"{saved['coil']:g}" if saved else "",
            help_text=_("Enter saves (with the last SWR sweep, if it was on this band) · "
                        "Esc cancels")))
        if not coil:
            return
        result = await self.link.call("swr.final", coil=coil, band=band["name"])
        if result.get("error"):
            self._message("error", result["error"])
            return
        self._message("info", _("position {coil} saved for {band}").format(
            coil=result["entry"]["coil"], band=result["entry"]["band"]))
        await self._fill_bands()

    def action_remote(self) -> None:
        self.run_worker(self._remote())

    async def _remote(self) -> None:
        if self.state.get("remote"):
            self._apply_state(await self.link.call("swr.remote", on=False))
            return
        warning = _("While it is on, the phone page can start sweeps and SWR sweeps can run "
                    "(they transmit a carrier at low power). It stays on until you switch it "
                    "off here or the suite ends.")
        if not self.state.get("allow_tx"):
            warning += " " + _("Transmission is disabled (no --tx): SWR sweeps will still be "
                               "refused.")
        answer = await self.push_screen_wait(ChoiceScreen(
            _("Remote control"), warning, [("on", _("Switch it on")), ("no", _("Leave it off"))]))
        if answer == "on":
            self._apply_state(await self.link.call("swr.remote", on=True))

    def action_antennas(self) -> None:
        self.run_worker(self._antennas())

    async def _antennas(self) -> None:
        view = AntennasView(self)
        self.push_overlay(view)
        await view.closed
        await self._fill_bands()
        self._apply_state(await self.link.call("swr.state"))

    async def action_back(self) -> None:
        if (self.state.get("sweep") or {}).get("running"):
            await self.link.call("swr.stop")
            return
        self.action_leave()

    def action_leave(self) -> None:
        self.run_worker(self._leave())

    async def _leave(self) -> None:
        # A sweep keeps running in the core; the phone page and the dashboard still see it.
        if self.owns_link:
            await self.link.close()
        self.exit(0)


class AntennasView(Overlay):
    """The saved antennas (the terminal's AntennaScreen): choose the active one, add, edit or
    delete; the same keys, plus the mouse and buttons."""

    TUI = AntennaScreen

    def __init__(self, page: SwrPage) -> None:
        super().__init__(page)
        self.link = page.link
        self.antennas: list[dict] = []
        self.closed = asyncio.get_event_loop().create_future()
        self.frame = Frame(_("Antennas"))
        self.table = self.frame.add(Table(0, 3), 1)
        self.table.setHorizontalHeaderLabels(["", _("Antenna"), _("Saved settings")])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.currentCellChanged.connect(lambda *_a: self._show_notes())
        self.table.cellDoubleClicked.connect(lambda *_a: self.run_worker(self.action_select()))
        self.notes = self.frame.add(QLabel())
        self.notes.setTextFormat(Qt.TextFormat.RichText)
        self.notes.setWordWrap(True)
        buttons = QHBoxLayout()
        for key, label, action in (("Enter", N_("use it"), self.action_select),
                                   ("N", N_("new"), self.action_new),
                                   ("E", N_("edit"), self.action_edit),
                                   ("D", N_("delete"), self.action_delete),
                                   ("Esc", N_("back"), self.action_dismiss)):
            button = QPushButton(f"{key}  {_(label)}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _c=False, a=action: self._run(a))
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.frame.body.addLayout(buttons)
        box = QVBoxLayout(self)
        box.setContentsMargins(60, 40, 60, 40)
        box.addWidget(self.frame)

    def _run(self, action) -> None:
        result = action()
        if asyncio.iscoroutine(result):
            self.run_worker(result)

    def start(self) -> None:
        self.table.setFocus()
        self.run_worker(self._load())

    async def _load(self, keep: int | None = None) -> None:
        reply = await self.link.call("swr.antennas")
        self.antennas = reply.get("antennas", [])
        self.table.setRowCount(len(self.antennas))
        row = 0
        for i, antenna in enumerate(self.antennas):
            cells = ("●" if antenna["active"] else "", antenna["name"],
                     settings_text(antenna.get("settings", {})) or "–")
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setForeground(QColor(theme.GREEN))
                if column == 1 and antenna["active"]:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(i, column, item)
            if antenna["id"] == keep or keep is None and antenna["active"]:
                row = i
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        if self.antennas:
            self.table.selectRow(row)
        self._show_notes()

    def _chosen(self) -> dict | None:
        row = self.table.currentRow()
        return self.antennas[row] if 0 <= row < len(self.antennas) else None

    def _show_notes(self) -> None:
        antenna = self._chosen()
        if antenna is None:
            self.notes.setText("")
            return
        text = (span(antenna["notes"], theme.TEXT) + "<br>") if antenna["notes"] else ""
        self.notes.setText(text + span(_("Adjustment") + ": ", theme.DIM)
                           + span(_(SIGNS.get(antenna["coil_sign"], SIGNS[0])), theme.TEXT))

    def _message(self, text: str) -> None:
        self.notes.setText(span(text, theme.RED, True))

    # ---- keys (the terminal's AntennaScreen actions) ----

    def action_dismiss(self) -> None:
        self.page.pop_overlay()
        if not self.closed.done():
            self.closed.set_result(None)

    def action_pop_screen(self) -> None:
        self.action_dismiss()

    async def action_select(self) -> None:
        antenna = self._chosen()
        if antenna is None:
            return
        await self.link.call("swr.antenna.select", id=antenna["id"])
        self.action_dismiss()

    def action_new(self) -> None:
        self.run_worker(self._edit(None))

    def action_edit(self) -> None:
        antenna = self._chosen()
        if antenna is not None:
            self.run_worker(self._edit(antenna))

    async def _edit(self, antenna: dict | None) -> None:
        title = _("Edit antenna") if antenna else _("New antenna")
        name = await ask_text(self, title, _("Name (make and model, or how you call it)"),
                              "Komunica HF-PRO-2-PLUS-T", antenna["name"] if antenna else "")
        if not name or not name.strip():
            return
        notes = await ask_text(self, title,
                               _("Notes (optional): where it is, radials, what the numbers "
                                 "mean..."), "", antenna["notes"] if antenna else "")
        if notes is None:
            return
        sign = await choose(self, title,
                            _("How does its adjustment (coil, tap, length) move the resonance?"),
                            [("-1", _("Higher number, lower frequency")),
                             ("1", _("Higher number, higher frequency")),
                             ("0", _("Not known yet"))])
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
            self.run_worker(self._delete(antenna))

    async def _delete(self, antenna: dict) -> None:
        answer = await choose(self, _("Delete antenna"),
                              _("Delete {name} and all its sweeps and saved settings?").format(
                                  name=antenna["name"]),
                              [("no", _("Keep it")), ("yes", _("Delete"))])
        if answer != "yes":
            return
        reply = await self.link.call("swr.antenna.delete", id=antenna["id"])
        if reply.get("error"):
            self._message(reply["error"])
            return
        await self._load()

