"""The CW application of the graphical client: the terminal's (apps.cw.tui), window for
window, and its channels view (apps.cw.screens).

Receive: the decoded text (a CQ green, a contest magenta) and its plain-language
translation; or, skimming, the skimmer's band scope with the stations marked and their table.
Below, the decoder (or the chosen station), the F2-F9 macros and the command line. The keys
are the terminal's; the mouse picks a station (click), tunes it (double click), tunes on the
scope, and sends a macro with its button.
"""

from __future__ import annotations

import base64
import re

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ic705suite.apps.cw.interpret import Interpretation, Interpreter, call_kind, interpret
from ic705suite.apps.cw.screens import SOURCES, ChannelsScreen
from ic705suite.apps.cw.session import HELP
from ic705suite.apps.cw.tui import (
    BADGES,
    CALL_RES,
    HELP_HINT,
    KEYS,
    LINE_HISTORY,
    MAX_LINES,
    SCOPE_KEEPALIVE,
    SHORT_HINT,
    TABLE_BADGES,
    CwApp,
    skim_window,
    station_marks,
)
from ic705suite.gui import theme
from ic705suite.gui.page import Overlay, Page, show_text
from ic705suite.gui.widgets import (
    Footer,
    Frame,
    Meter,
    Scope,
    StatusLine,
    Table,
    badge,
    esc,
    hint_html,
    span,
    with_load_html,
)
from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.rig.base import RigError, smeter_text
from ic705suite.rig.transport import TransportError
from ic705suite.station.links import Link, LinkError
from ic705suite.ui.channels import ChannelSaving


def line_style(kind: str) -> tuple[str, bool, bool]:
    """The terminal's style of a kind of line, as (colour, bold, italic) in the theme."""
    return {"rx": (theme.TEXT, False, False), "tx": (theme.YELLOW, True, False),
            "info": (theme.CYAN, False, False), "error": (theme.RED, True, False),
            "spot": (theme.GREEN, True, False), "summary": (theme.TEXT, True, False),
            "glossary": (theme.DIM, False, False),
            "history": (theme.DIM, False, True)}.get(kind, (theme.TEXT, False, False))


def call_colour(kind: str | None, background: bool = False) -> str:
    """Green for a CQ, magenta for a contest: as text, or vivid behind black text."""
    if background:
        return {"cq": theme.BG_GREEN, "contest": theme.BG_MAGENTA}.get(kind, theme.BG_YELLOW)
    return {"cq": theme.GREEN, "contest": theme.MAGENTA}.get(kind, theme.TEXT)
VISIBLE_LINES = 400            # lines drawn of the MAX_LINES kept


def highlight_calls_html(escaped: str) -> str:
    """A CQ (green) and a contest (magenta) marked in text already escaped for HTML."""
    for kind, pattern in CALL_RES.items():
        escaped = re.sub(pattern, lambda m, k=kind: (
            f'<span style="background:{call_colour(k, True)};color:#000;font-weight:bold">'
            f"{m.group()}</span>"), escaped)
    return escaped


def call_badge(kind: str | None) -> str:
    return badge(_(BADGES[kind]), "#000000", call_colour(kind, True)) if kind else ""


class TextPane(QTextEdit):
    """Decoded text and messages, the newest at the bottom (the terminal's TextPane):
    received text grows the last line; drawn a few times a second at most."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lines: list[tuple[str, str]] = []
        self.setReadOnly(True)
        self.setFont(theme.mono(10.5))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._timer = QTimer(self, singleShot=True, interval=80, timeout=self._draw)

    def add(self, kind: str, text: str, append: bool = False) -> None:
        if append and self.lines and self.lines[-1][0] == kind:
            self.lines[-1] = (kind, self.lines[-1][1] + text)
        else:
            self.lines.append((kind, text))
        del self.lines[:-MAX_LINES]
        if not self._timer.isActive():
            self._timer.start()

    def redraw(self) -> None:
        """Drawn again (the theme changed: the lines keep their kind, not their colour)."""
        self._draw()

    def clear_lines(self) -> None:
        self.lines.clear()
        self._draw()

    def _draw(self) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        rows = []
        for kind, line in self.lines[-VISIBLE_LINES:]:
            colour, bold, italic = line_style(kind)
            body = esc(line)
            if kind == "rx":
                body = highlight_calls_html(body)
            style = f"color:{colour};" + ("font-weight:bold;" if bold else "") + \
                ("font-style:italic;" if italic else "")
            rows.append(f'<div style="{style}white-space:pre-wrap">{body or "&nbsp;"}</div>')
        self.setHtml("".join(rows))
        if at_bottom:
            self.moveCursor(QTextCursor.MoveOperation.End)
            bar.setValue(bar.maximum())


class CwStatus(StatusLine):
    """The CW status line: badges, rig, frequency, mode, S-meter, keyer, power, link."""

    def redraw(self) -> None:
        st = self.rig
        parts = []
        if st and not st.get("connected"):
            parts.append(badge(_("DISCONNECTED"), "#ffffff", "#b3261e"))
        if st.get("transmitting"):
            parts.append(badge(_("TX"), "#ffffff", "#d32f2f"))
        elif st and not st.get("tx_enabled"):
            parts.append(badge(_("RX ONLY"), "#000000", theme.BG_YELLOW))
        play = st.get("playback")
        if play:
            elapsed, duration = int(play["elapsed"]), int(play["duration"])
            parts.append(badge(f"▶ {play['file']} {elapsed // 60}:{elapsed % 60:02d}"
                               f"/{duration // 60}:{duration % 60:02d}", "#000000",
                               theme.BG_MAGENTA))
        if st.get("scanning"):
            parts.append(badge(f"{_('SCAN')} {bands.format_frequency(st.get('scan_freq'))}",
                               "#000000", theme.BG_CYAN))
        if st.get("skimming"):
            parts.append(badge(_("SKIMMER"), "#000000", theme.BG_GREEN))
        chunks = [span(st.get("radio") or "IC-705", theme.CYAN, True),
                  span(f"{_('QRG')} ", theme.DIM)
                  + span(bands.format_frequency(st.get("frequency")), theme.YELLOW, True),
                  span(f"{_('MODE')} ", theme.DIM) + span(st.get("mode") or "-", theme.GREEN, True),
                  span(smeter_text(st.get("smeter", 0)), theme.TEXT, True),
                  span(f"{_('KEY')} ", theme.DIM)
                  + span(f"{st.get('key_wpm', '-')} {_('wpm')}", theme.TEXT)]
        if st.get("power") is not None:
            chunks.append(span(f"{_('PWR')} ", theme.DIM) + span(f"{st['power']}%", theme.TEXT))
        chunks.append(span(f"{_('LINK')} ", theme.DIM) + span(self.link_description, theme.BLUE,
                                                               True))
        separator = span(" · ", theme.DIM)
        self.setText(with_load_html("".join(parts) + separator.join(chunks)))


class CwPage(ChannelSaving, Page):
    TUI = CwApp

    def __init__(self, window, link: Link, config, scp: set[str] | None = None,
                 owns_link: bool = False, startup: list[str] | None = None) -> None:
        super().__init__(window, link, config)
        self.owns_link = owns_link
        self.scp = scp or set()
        self.startup = startup or []
        self.state: dict = {}
        self.welcome: dict = {}
        self.exit_code = 0
        self.interpreter = Interpreter(scp=self.scp)
        self.state_frequency: int | None = None
        self._history: list[str] = []
        self._history_index = 0
        self.channels: list[dict] = []
        self.selected = 0
        self.calling: str | None = None       # the single decoder's station: call_kind()
        self.meaning = ""                     # plain-language summary of the chosen station
        self._scope_wanted = False
        self.rx_chars = 0
        self._build()
        self.scope_timer = QTimer(self, interval=int(SCOPE_KEEPALIVE * 1000),
                                  timeout=lambda: self.run_worker(self._keep_scope()))
        self.tick_timer = QTimer(self, interval=1000, timeout=self._interpreter_tick)

    # ---- layout -----------------------------------------------------------------------------

    def _build(self) -> None:
        self.status = CwStatus(self.link.description)
        self.main_frame = Frame(_("Receive"))
        self.main_stack = QStackedWidget()
        self.rx = TextPane()
        skim = QWidget()
        skim_box = QVBoxLayout(skim)
        skim_box.setContentsMargins(0, 0, 0, 0)
        self.scope = Scope(spectrum_share=0.3)
        self.scope.mark_row = True
        self.scope.setMinimumHeight(150)
        self.scope.clicked.connect(lambda hz: self.run_worker(self.action_command(f"/f {hz}")))
        self.skim_table = Table(0, 6)
        self.skim_table.setHorizontalHeaderLabels(["#", _("QRG"), _("Call"), _("WPM"), _("SNR"),
                                                   _("Text")])
        self.skim_table.right_align(0, 1, 3, 4)
        self.skim_table.verticalHeader().hide()
        self.skim_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.skim_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.skim_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.skim_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.skim_table.setShowGrid(False)
        self.skim_table.setWordWrap(False)
        self.skim_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.skim_table.cellClicked.connect(lambda row, _col: self._choose(row))
        self.skim_table.cellDoubleClicked.connect(
            lambda row, _col: self.run_worker(self.action_command(f"/go {row + 1}")))
        skim_box.addWidget(self.scope, 2)
        skim_box.addWidget(self.skim_table, 3)
        self.main_stack.addWidget(self.rx)
        self.main_stack.addWidget(skim)
        self.main_frame.add(self.main_stack, 1)

        self.meaning_frame = Frame(_("Translation"))
        self.meaning_pane = self.meaning_frame.add(TextPane(), 1)
        self.meaning_pane.add("glossary",
                              _("The meaning of each transmission appears here once it ends."))

        self.detail_frame = Frame(_("Detail"))
        self.detail = self.detail_frame.add(QLabel())
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.level = self.detail_frame.add(Meter(_("LEVEL")))
        self.detail_keys = self.detail_frame.add(QLabel())
        self.detail_keys.setTextFormat(Qt.TextFormat.RichText)

        self.macro_bar = QHBoxLayout()
        self.macro_bar.setSpacing(4)
        self.macro_buttons: dict[str, QPushButton] = {}
        for n in range(2, 10):
            button = QPushButton(f"F{n}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _c=False, n=n: self.run_worker(
                self.action_command(f"/macro F{n}")))
            self.macro_buttons[f"F{n}"] = button
            self.macro_bar.addWidget(button, 1)
        # The keys only (a long macro would widen the window); ? lists what each one sends,
        # and the mouse over a key shows it too.
        self.macro_help = QPushButton("?")
        self.macro_help.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.macro_help.setToolTip(_("What each macro sends"))
        self.macro_help.clicked.connect(lambda: self.run_worker(self.show_macros()))
        self.macro_bar.addWidget(self.macro_help)

        self.entry_frame = Frame()
        row = QHBoxLayout()
        self.entry_label = QLabel()
        self.entry_label.setObjectName("heading")
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(_("type /help for the commands"))
        self.entry.returnPressed.connect(lambda: self.run_worker(self._submit()))
        row.addWidget(self.entry_label)
        row.addWidget(self.entry, 1)
        self.entry_frame.body.addLayout(self.macro_bar)
        self.entry_frame.body.addLayout(row)
        self.footer = Footer([HELP_HINT, SHORT_HINT])

        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 6, 0)
        box.setSpacing(6)
        box.addWidget(self.status)
        box.addWidget(self.main_frame, 5)
        box.addWidget(self.meaning_frame, 2)
        box.addWidget(self.detail_frame)
        box.addWidget(self.entry_frame)
        box.addWidget(self.footer)
        for widget in (self.status, self.footer, self.detail, self.level, self.detail_keys,
                       self.scope):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._update_entry_title()
        self._show_detail()
        self._show_macros()

    # ---- life -------------------------------------------------------------------------------

    def start(self) -> None:
        self.link.attach(self._on_event)
        self.entry.setFocus()
        self.scope_timer.start()
        self.tick_timer.start()
        self.run_worker(self._connect())

    def restore_focus(self) -> None:
        self.entry.setFocus()

    def stop(self) -> None:
        self.scope_timer.stop()
        self.tick_timer.stop()
        super().stop()

    async def _connect(self) -> None:
        try:
            await self.link.connect()
        except (LinkError, TransportError, RigError, OSError) as exc:
            self._message("error", _("cannot connect: {error}").format(error=exc))
            self._message("info", _("press Ctrl+Q to leave"))
            self.exit_code = 1
            return
        try:
            await self.link.call("cw.activate")  # the radio goes to CW while this is open
        except (LinkError, OSError) as exc:
            self._message("error", str(exc))
        for line in self.startup:
            await self.link.command(line)
        await self.link.closed.wait()
        self._message("error", _("the link to the core has ended; press Ctrl+Q to leave"))

    # ---- events from the core -------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "state":
            self._apply_state(event)
        elif kind == "skim":
            self._apply_skim(event["channels"])
        elif kind == "scope":
            self._apply_scope(event)
        elif kind == "rx":
            self.rx.add("rx", event["text"], append=True)
            last = self.rx.lines[-1]
            calling = call_kind(last[1][-300:]) if last[0] == "rx" else None
            if calling != self.calling:
                self.calling = calling
                self._show_detail()
            for result in self.interpreter.feed(event["text"]):
                self._show_meaning(result)
            self.rx_chars += len(event["text"].strip())
            self._counters()
        elif kind == "tx":
            self.rx.add("tx", f"TX> {event['text']}")
        elif kind in ("info", "error"):
            spot = str(event.get("msgid", "")).startswith("spot ")
            self._message("spot" if spot else kind, render(event))
        elif kind == "welcome":
            self.welcome = event
            self._update_entry_title()
            self._show_macros()
        elif kind == "history":
            self.rx.add("history", "── " + _("earlier on {title}").format(title=event["title"])
                        + " ──")
            self.rx.add("history", event["text"][-600:])

    def _interpreter_tick(self) -> None:
        result = self.interpreter.tick()
        if result is not None:
            self._show_meaning(result)

    def _show_meaning(self, result: Interpretation) -> None:
        if result.summary:
            self.meaning_pane.add("summary", "≈ " + ". ".join(result.summary) + ".")
        if result.glossary:
            self.meaning_pane.add("glossary", "   " + " · ".join(f"{w} = {m}"
                                                               for w, m in result.glossary))

    def _message(self, kind: str, text: str) -> None:
        self.rx.add(kind, text)
        self.entry_frame.note(text[:200], "error" if kind == "error" else
                              "ok" if kind == "spot" else "info")

    def _counters(self) -> None:
        self.footer.counters = [(_("RX chars"), self.rx_chars),
                                (_("spots"), self.state.get("spots", 0))]

    def _apply_state(self, state: dict) -> None:
        was_skimming = self.state.get("skimming", False)
        self.state = state
        self.status.update_rig(state)
        self._counters()
        if state.get("frequency") != self.state_frequency:
            self.state_frequency = state.get("frequency")
            self.interpreter = Interpreter(scp=self.scp)  # a new frequency, a new conversation
        if state.get("skimming") != was_skimming:
            skimming = state.get("skimming", False)
            self.main_stack.setCurrentIndex(1 if skimming else 0)
            self.meaning_frame.setVisible(not skimming)
            self.scope.clear()
            self.scope.setVisible(skimming)
            self._scope_wanted = skimming
            if skimming:
                self.run_worker(self._keep_scope())
            self.main_frame.set_title(_("Skimmer") if skimming else _("Receive"))
            if not skimming:
                self._apply_skim([])
        self._update_entry_title()
        self._show_detail()

    def _apply_skim(self, channels: list[dict]) -> None:
        self.channels = channels
        table = self.skim_table
        table.setRowCount(len(channels))
        for i, ch in enumerate(channels):
            calling = call_kind(ch["text"])
            colour = call_colour(calling)
            call = (ch["callsign"] or "-") + (" ✓" if ch.get("known") else "") + \
                (f"  {TABLE_BADGES[calling]}" if calling else "")
            cells = (str(ch["index"]), bands.format_frequency(ch["frequency"]), call,
                     f"{ch['wpm']:.0f}", f"{ch['snr_db']:.0f}", ch["text"][-160:])
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(theme.CYAN if column == 2 and ch["callsign"] and
                                          not calling else colour))
                if column == 0 and calling:
                    item.setBackground(QColor(call_colour(calling, True)))
                    item.setForeground(QColor("#000000"))
                if column == 5:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                          Qt.AlignmentFlag.AlignVCenter)
                table.setItem(i, column, item)
        for column in range(5):
            table.resizeColumnToContents(column)
        self.selected = min(self.selected, len(channels) - 1) if channels else 0
        if channels:
            table.selectRow(self.selected)
        self._update_selected_meaning()
        self._mark_stations()

    # ---- the skimmer's scope ---------------------------------------------------------------------

    def _mark_stations(self) -> None:
        for name, value in station_marks(self.channels, self.selected).items():
            setattr(self.scope.view, name, value)
        self.scope.update()

    def _apply_scope(self, event: dict) -> None:
        if not self._scope_wanted:
            return
        data = np.frombuffer(base64.b64decode(event["data"]), dtype=np.uint8) / 255.0
        span_range = self.state.get("skim_range")
        data, start, end = skim_window(data, int(event["start"]), int(event["end"]), span_range)
        self.scope.view.dial = self.state.get("frequency")
        if span_range:
            self.scope.view.passband = (span_range[0], span_range[1])
        self.scope.add_frame(data, start, end)

    async def _keep_scope(self) -> None:
        if not self._scope_wanted:
            return
        try:
            reply = await self.link.call("scope.watch", seconds=SCOPE_KEEPALIVE * 3)
        except (LinkError, OSError):
            return
        if reply.get("scope") is False:      # a radio without a scope (errors are retried)
            self._scope_wanted = False
            self.scope.setVisible(False)

    def _update_selected_meaning(self) -> None:
        self.meaning = ""
        if 0 <= self.selected < len(self.channels):
            words = self.channels[self.selected]["text"].split()[1:][-15:]  # first is cut
            self.meaning = ". ".join(interpret(words, self.scp).summary)
        self._show_detail()

    # ---- detail, macros, entry ----------------------------------------------------------------

    def retheme(self) -> None:
        self._show_detail()
        self._show_macros()

    def _show_detail(self) -> None:
        """Three lines, as the terminal's DetailPanel: the decoder (or, skimming, the range
        and the chosen station) and what to do next."""
        st = self.state

        def chunk(label: str, value: str, colour: str = theme.TEXT) -> str:
            return span(f"{label} ", theme.DIM) + span(value, colour, True)

        sep = span(" · ", theme.DIM)
        if st.get("skimming"):
            self.level.setVisible(False)
            first = []
            skim_range = st.get("skim_range")
            if skim_range:
                first.append(chunk(_("RANGE"), f"{bands.format_frequency(skim_range[0])} – "
                                               f"{bands.format_frequency(skim_range[1])}",
                                   theme.YELLOW))
                first.append(chunk(_("WIDTH"), f"{(skim_range[1] - skim_range[0]) / 1000:.2f} kHz"))
            first.append(chunk(_("STATIONS"), str(len(self.channels))))
            first.append(chunk(_("SPOTS"), str(st.get("spots", 0))))
            if self.channels and 0 <= self.selected < len(self.channels):
                ch = self.channels[self.selected]
                second = sep.join([chunk(_("QRG"), bands.format_frequency(ch["frequency"]),
                                         theme.YELLOW),
                                   chunk(_("CALL"), ch["callsign"] or "-", theme.CYAN),
                                   chunk(_("SPEED"), f"{ch['wpm']:.0f} {_('wpm')}"),
                                   chunk(_("SNR"), f"{ch['snr_db']:.0f} dB")]) \
                    + "&nbsp;&nbsp;" + call_badge(call_kind(ch["text"]))
            else:
                second = span(_("Looking for CW signals..."), theme.DIM)
            self.detail.setText(sep.join(first) + "<br>" + second)
            self.detail_keys.setText(
                span("≈ ", theme.GREEN, True) + span(self.meaning, theme.TEXT) if self.meaning
                else hint_html(_("↑↓ select a station · Enter tune it (or /go N) · "
                                 "F10 back to single decoding")))
            return
        self.level.setVisible(True)
        d = st.get("decoder", {})
        if not d:
            self.detail.setText("")
            self.level.set(None, "")
            self.detail_keys.setText("")
            return
        squelch = chunk(_("SQUELCH"), _("closed") if d["squelched"] else _("open"),
                        theme.DIM if d["squelched"] else theme.GREEN)
        self.detail.setText(sep.join([
            chunk(_("TONE"), f"{d['tone']:.0f} Hz", theme.YELLOW),
            chunk(_("SPEED"), f"{d['wpm']:.1f} {_('wpm')}"),
            chunk(_("SNR"), f"{d['snr_db']:.1f} dB"),
            chunk(_("AFC"), _("on") if d["afc"] else _("off")), squelch])
            + "&nbsp;&nbsp;" + call_badge(self.calling))
        self.level.set(d["level"], "", hot=False)
        self.detail_keys.setText(chunk(_("PITCH"), f"{st.get('pitch', '-')} Hz") + "&nbsp;" * 4
                                 + hint_html(_("PgUp/PgDn tune · /scan search · F10 skimmer")))

    def _show_macros(self) -> None:
        macros = self.welcome.get("macros") or self.config.macros
        call = self.welcome.get("callsign") or self.config.callsign or "N0CALL"
        for key, button in self.macro_buttons.items():
            text = macros.get(key, "").replace("{call}", call)
            button.setText(key)
            button.setToolTip(text)
            button.setEnabled(bool(text))

    def macro_lines(self) -> list[str]:
        macros = self.welcome.get("macros") or self.config.macros
        call = self.welcome.get("callsign") or self.config.callsign or "N0CALL"
        return [f"{key}  {macros.get(key, '').replace('{call}', call) or '—'}"
                for key in self.macro_buttons]

    async def show_macros(self) -> None:
        await show_text(self, _("Macros"), self.macro_lines())

    def _update_entry_title(self) -> None:
        tx = self.state.get("tx_enabled", self.welcome.get("tx_enabled", False))
        self.entry_frame.set_title(_("Transmit") if tx else _("Command (receive only)"))
        self.entry_label.setText(_("SEND") if tx else _("COMMAND"))

    def _choose(self, row: int) -> None:
        if 0 <= row < len(self.channels):
            self.selected = row
            self.skim_table.selectRow(row)
            self._update_selected_meaning()
            self._mark_stations()

    # ---- input --------------------------------------------------------------------------------

    async def _submit(self) -> None:
        line = self.entry.text().strip()
        self.entry.clear()
        if not line:
            if self.state.get("skimming") and self.channels:
                await self.action_command(f"/go {self.selected + 1}")
            return
        if not self._history or self._history[-1] != line:
            self._history = (self._history + [line])[-LINE_HISTORY:]
        self._history_index = len(self._history)
        if line.lower() in ("/quit", "/q", "/exit"):
            self.action_leave()
        elif line.lower() in ("/help", "/h", "/?"):
            self.action_help()
        elif line.lower() == "/clear":
            self.rx.clear_lines()
        else:
            await self.action_command(line)

    # ---- keys (the terminal's actions) ----------------------------------------------------------

    def action_help(self) -> None:
        self.show_text(_("CW commands"), [_(row) for row in HELP] + [""]
                       + [_(row) for row in KEYS])

    async def action_command(self, line: str) -> None:
        self.run_worker(self.link.command(line))

    def action_arrow(self, step: int) -> None:
        if self.state.get("skimming"):
            self.action_station(step)
        else:
            self.action_recall(step)

    def action_station(self, step: int) -> None:
        if self.state.get("skimming") and self.channels:
            self._choose((self.selected + step) % len(self.channels))

    def action_recall(self, step: int) -> None:
        if not self._history:
            return
        self._history_index = max(0, min(len(self._history), self._history_index + step))
        self.entry.setText(self._history[self._history_index]
                           if self._history_index < len(self._history) else "")
        self.entry.setCursorPosition(len(self.entry.text()))

    def action_channels(self) -> None:
        if not isinstance(self.overlay, ChannelsView):
            self.push_overlay(ChannelsView(self))

    def action_leave(self) -> None:
        self.run_worker(self._leave())

    async def _leave(self) -> None:
        """On its own: offer to archive the unsaved channels, then quit. Inside the suite:
        back to the dashboard (the channels stay in the core; leaving the suite asks)."""
        if self.owns_link:
            if not await self.offer_save_channels():
                return
            await self.link.close()
        self.exit(self.exit_code)


# ---- channels ----------------------------------------------------------------------------------

def stats_html(stats: dict) -> str:
    """The channel statistics line (screens.stats_text)."""
    def chunk(label: str, value: str, colour: str = theme.TEXT) -> str:
        return span(f"{label} ", theme.DIM) + span(value, colour, True)

    noise = stats.get("noise_pct", 0)
    parts = [chunk(_("words"), str(stats.get("words", 0))),
             chunk(_("recognized"), f"{stats.get('recognized_pct', 0):.0f}%", theme.GREEN),
             chunk(_("noise"), f"{noise:.0f}%", theme.RED if noise > 30 else theme.TEXT),
             chunk(_("unreadable"), str(stats.get("garbled", 0))),
             chunk(_("fragments"), str(stats.get("fragments", 0))),
             chunk(_("callsigns"), f"{stats.get('callsigns', 0)} "
                                   f"({stats.get('scp_callsigns', 0)} SCP)"),
             chunk(_("transmissions"), str(stats.get("transmissions", 0)))]
    if stats.get("wpm"):
        parts.append(chunk(_("speed"), f"{stats['wpm']:.0f} {_('wpm')}"))
    if stats.get("snr_db") is not None:
        parts.append(chunk("SNR", f"{stats['snr_db']:.0f} dB"))
    return span(" · ", theme.DIM).join(parts)


class ChannelsView(Overlay):
    """The channels of this session and the archive (the terminal's ChannelsScreen):
    statistics, transcript, save, tune, play; the same keys, plus the mouse and buttons."""

    TUI = ChannelsScreen

    def __init__(self, page: CwPage) -> None:
        super().__init__(page)
        self.rows: list[tuple[str, dict]] = []     # ("session"|"saved", channel)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.frame = Frame(_("Channels"))
        self.table = self.frame.add(Table(0, 10), 1)
        self.table.setHorizontalHeaderLabels(["", _("Name"), _("QRG"), _("Source"),
                                              _("Last (UTC)"), _("Words"), _("OK %"),
                                              _("Noise %"), _("WPM"), _("Audio")])
        self.table.right_align(2, 5, 6, 7, 8)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.action_show())
        self.detail_frame = Frame(_("Transcript"))
        self.transcript = self.detail_frame.add(QTextEdit(), 1)
        self.transcript.setReadOnly(True)
        self.transcript.setFont(theme.mono(10))
        self.transcript.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        buttons = QHBoxLayout()
        for key, label, action in (("Enter", N_("show"), self.action_show),
                                   ("S", N_("save"), self.action_save),
                                   ("T", N_("tune"), self.action_tune),
                                   ("P", N_("play audio"), self.action_play),
                                   ("R", N_("reload"), self.action_reload),
                                   ("Esc", N_("back"), self.action_pop_screen)):
            button = QPushButton(f"{key}  {_(label)}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _c=False, a=action: a())
            buttons.addWidget(button)
        buttons.addStretch(1)
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 6, 6, 6)
        box.addWidget(self.title)
        box.addWidget(self.frame, 3)
        box.addWidget(self.detail_frame, 2)
        box.addLayout(buttons)

    def start(self) -> None:
        self.run_worker(self.reload())

    async def reload(self) -> None:
        listing = await self.page.link.call("channels")
        self.rows = [("session", c) for c in reversed(listing.get("session", []))] + \
                    [("saved", c) for c in listing.get("saved", [])]
        title = span(_("Channels") + "  ", theme.CYAN, True) + span(
            _("{n} in this session, {m} archived").format(
                n=len(listing.get("session", [])), m=len(listing.get("saved", []))), theme.DIM)
        if listing.get("error"):
            title += "&nbsp;&nbsp;" + span(_("archive unavailable: {error}").format(
                error=listing["error"]), theme.RED, True)
        self.title.setText(title)
        row = max(0, self.table.currentRow())
        self.table.setRowCount(len(self.rows))
        for i, (kind, c) in enumerate(self.rows):
            s = c.get("stats", {})
            mark, mark_colour = ("●", theme.YELLOW) if kind == "session" and c.get("unsaved") \
                else ("✓", theme.GREEN) if kind == "saved" or c.get("archive_name") else ("", "")
            source = _(SOURCES.get(c.get("source", ""), N_("archive"))) if kind == "session" \
                else _("archive")
            last = (c.get("last") or c.get("updated") or "")[11:16]
            cells = (mark, c.get("label") or c.get("name") or "",
                     bands.format_frequency(c.get("frequency")), source, last,
                     str(s.get("words", 0)), f"{s.get('recognized_pct', 0):.0f}",
                     f"{s.get('noise_pct', 0):.0f}", f"{s['wpm']:.0f}" if s.get("wpm") else "-",
                     "♪" if c.get("audio") and kind == "saved" else "")
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0 and mark_colour:
                    item.setForeground(QColor(mark_colour))
                self.table.setItem(i, column, item)
        self.table.resizeColumnsToContents()
        if self.rows:
            self.table.selectRow(min(row, len(self.rows) - 1))

    def _selected(self) -> tuple[str, dict] | None:
        row = self.table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    # ---- keys (the terminal's ChannelsScreen actions) ----

    def action_select(self, step: int) -> None:
        if self.rows:
            self.table.selectRow((max(0, self.table.currentRow()) + step) % len(self.rows))

    def action_show(self) -> None:
        self.run_worker(self.show())

    async def show(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        kind, c = selected
        reply = await self.page.link.call("transcript", id=c["id"], saved=kind == "saved")
        t = reply.get("transcript")
        if t is None:
            self.transcript.setHtml(span(_("Not found"), theme.RED, True))
            return
        lines = [span(f"{t.get('name') or ''}  {bands.format_frequency(t.get('frequency'))} MHz",
                      theme.CYAN, True), stats_html(t.get("stats", {})), ""]
        colours = {"rx": theme.TEXT, "tx": theme.YELLOW, "summary": theme.GREEN}
        for e in t.get("entries", []):
            prefix = {"tx": "TX> ", "summary": "≈ "}.get(e["kind"], "")
            lines.append(span(f"{e['utc'][11:19]} ", theme.DIM)
                         + span(prefix + e["text"], colours.get(e["kind"], theme.TEXT)))
        for a in t.get("audio", []):
            lines.append(span(f"♪ {a['path']} ({a['seconds']:.0f} s)", theme.MAGENTA))
        self.transcript.setHtml("<br>".join(lines))

    def action_reload(self) -> None:
        self.run_worker(self.reload())

    def action_save(self) -> None:
        selected = self._selected()
        if selected and selected[0] == "session":
            self.run_worker(self._save(selected[1]))

    async def _save(self, channel: dict) -> None:
        await self.page.save_channel_flow(channel, ask_audio=True)
        await self.reload()

    def action_tune(self) -> None:
        selected = self._selected()
        if selected and selected[1].get("frequency"):
            self.run_worker(self.page.link.command(f"/f {selected[1]['frequency']}"))
            self.action_pop_screen()

    def action_play(self) -> None:
        selected = self._selected()
        if not selected or selected[0] != "saved":
            return

        async def play() -> None:
            reply = await self.page.link.call("transcript", id=selected[1]["id"], saved=True)
            audio = (reply.get("transcript") or {}).get("audio") or []
            if audio:
                await self.page.link.command(f"/play {audio[-1]['path']}")
                self.action_pop_screen()

        self.run_worker(play())
