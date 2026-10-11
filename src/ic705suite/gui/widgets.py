"""The pieces every page of the graphical client shares: framed windows, the status line, the
footer with the clock and the keys, seven-segment digits, bar meters, the rig's front panel
and the band scope. They draw what the terminal's widgets draw, with pixels and the mouse.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections import deque

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ic705suite.gui import theme
from ic705suite.gui.keys import key_name
from ic705suite.i18n import _
from ic705suite.rig import bands
from ic705suite.rig.base import smeter_text
from ic705suite.rig.controls import LEVEL, OFFSET, TOGGLE
from ic705suite.ui.panelmodel import PanelModel, format_value, fraction
from ic705suite.ui.sysload import LOAD
from ic705suite.ui.waterfall import PALETTE_STOPS, ScopeView, find_signals

KEY = re.compile(r"F\d+(?:-F\d+)?|Esc|PgUp/PgDn|Ctrl\+\w(?:/Ctrl\+\w)?|Enter|Shift\+Tab|Tab|"
                 r"Space|↑↓|←→|\[ \]|(?<!\w)/(?!\w)")


def esc(text: object) -> str:
    return html.escape(str(text))


def span(text: object, colour: str, bold: bool = False) -> str:
    weight = "font-weight:bold;" if bold else ""
    return f'<span style="color:{colour};{weight}">{esc(text)}</span>'


def badge(text: str, fg: str, bg: str) -> str:
    return (f'<span style="background:{bg};color:{fg};font-weight:bold;">'
            f"&nbsp;{esc(text)}&nbsp;</span>&nbsp;")


def hint_html(hint: str) -> str:
    """A key hint with its keys bold and the rest dim, as the terminal's footer."""
    out, position = [], 0
    for match in KEY.finditer(hint):
        out.append(span(hint[position:match.start()], theme.DIM))
        out.append(span(match.group(), theme.TEXT, bold=True))
        position = match.end()
    out.append(span(hint[position:], theme.DIM))
    return "".join(out)


# ---- rich text -----------------------------------------------------------------------------

# The terminal's colour names, as the theme draws them.
def rich_colours() -> dict[str, str]:
    """The terminal's colour names, as the theme in use draws them."""
    return {"green": theme.GREEN, "bright_green": theme.GREEN, "yellow": theme.YELLOW,
            "bright_yellow": theme.YELLOW, "red": theme.RED, "bright_red": theme.RED,
            "cyan": theme.CYAN, "bright_cyan": theme.CYAN, "magenta": theme.MAGENTA,
            "bright_magenta": theme.MAGENTA, "blue": theme.BLUE, "bright_blue": theme.BLUE,
            "white": theme.TEXT, "bright_white": theme.TEXT, "black": "#000000"}


def _css_colour(colour, background: bool = False) -> str | None:
    if colour is None or colour.is_default:
        return None
    if background:              # black text sits on it: the vivid ones, in either theme
        vivid = {"yellow": theme.BG_YELLOW, "green": theme.BG_GREEN,
                 "magenta": theme.BG_MAGENTA, "cyan": theme.BG_CYAN, "blue": theme.BG_BLUE}
        name = colour.name.removeprefix("bright_")
        if name in vivid:
            return vivid[name]
    colours = rich_colours()
    if colour.name in colours:
        return colours[colour.name]
    return colour.get_truecolor().hex


def rich_html(text) -> str:
    """A rich Text (what the terminal's widgets draw) as Qt rich text: the same colours,
    bold, italic and dim, on the theme's palette."""
    from rich.console import Console

    if isinstance(text, str):
        return esc(text)
    if text.style:
        # The style given to the whole text, under its own stretches (rich keeps it apart).
        from rich.text import Span

        text = text.copy()
        text.spans.insert(0, Span(0, len(text), text.style))
        text.style = ""
    console = Console(width=100_000, color_system="truecolor", force_terminal=True,
                      legacy_windows=False)
    out = []
    for segment in text.render(console):
        if not segment.text:
            continue
        body = esc(segment.text).replace("\n", "<br>")
        style = segment.style
        if style is None or style == "":
            out.append(body)
            continue
        css = []
        fg, bg = _css_colour(style.color), _css_colour(style.bgcolor, background=True)
        if style.reverse:
            fg, bg = bg or theme.PANEL, fg or theme.TEXT
        if style.dim and not fg:
            fg = theme.DIM
        if fg:
            css.append(f"color:{fg}")
        if bg:
            css.append(f"background:{bg}")
        if style.bold:
            css.append("font-weight:bold")
        if style.italic:
            css.append("font-style:italic")
        out.append(f'<span style="{";".join(css)}">{body}</span>' if css else body)
    return "".join(out)


def rich_colour(text) -> str | None:
    """The colour of the first styled stretch of a rich Text (a table cell's colour)."""
    for span_ in getattr(text, "spans", []):
        style = span_.style
        if isinstance(style, str):
            from rich.style import Style

            style = Style.parse(style)
        colour = _css_colour(style.color)
        if colour:
            return colour
    style = getattr(text, "style", "")
    if style:
        from rich.style import Style

        parsed = Style.parse(style) if isinstance(style, str) else style
        return _css_colour(parsed.color) or (theme.DIM if parsed.dim else None)
    return None


# ---- frames --------------------------------------------------------------------------------

class Frame(QFrame):
    """A window with a title and a subtitle, lit while the keys are its own."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("active", False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 6)
        outer.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel(title, objectName="frame-title")
        self.subtitle = QLabel("", objectName="frame-subtitle")
        self.subtitle.setTextFormat(Qt.TextFormat.RichText)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(self.title)
        head.addWidget(self.subtitle, 1)
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(4)
        outer.addLayout(self.body, 1)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def note(self, text: str, kind: str = "info") -> None:
        colour = theme.RED if kind == "error" else theme.CYAN if kind == "info" else theme.GREEN
        self.subtitle.setText(span(text, colour, bold=kind == "error") if text else "")
        self.subtitle.setToolTip(text)

    def set_active(self, active: bool) -> None:
        if self.property("active") != active:
            self.setProperty("active", active)
            self.style().unpolish(self)
            self.style().polish(self)


# ---- status line and footer ------------------------------------------------------------------

class StatusLine(QLabel):
    """Badges, rig, frequency, mode, S-meter, power, the link, and the suite's CPU and RAM."""

    def __init__(self, link_description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rig: dict = {}
        self.link_description = link_description
        self.extra: list[tuple[str, str]] = []
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setContentsMargins(6, 3, 6, 3)
        self.setStyleSheet(f"background:{theme.PANEL};border-bottom:1px solid {theme.BORDER};")
        self.timer = QTimer(self, interval=2000, timeout=self.redraw)
        self.timer.start()
        self.redraw()

    def update_rig(self, rig: dict) -> None:
        self.rig = rig
        self.redraw()

    def redraw(self) -> None:
        rig, parts, chunks = self.rig, [], []
        if not rig:
            parts.append(badge(_("NO RIG"), theme.TEXT, "#555d68"))
        elif not rig.get("connected"):
            parts.append(badge(_("DISCONNECTED"), "#ffffff", "#b3261e"))
        if rig.get("transmitting"):
            parts.append(badge(_("TX"), "#ffffff", "#d32f2f"))
        elif rig and not rig.get("tx_enabled"):
            parts.append(badge(_("RX ONLY"), "#000000", theme.BG_YELLOW))
        if rig.get("recording"):
            parts.append(badge("● REC", "#ffffff", "#8b0000"))

        def chunk(label: str, value: str, colour: str = theme.TEXT, bold: bool = True) -> None:
            if value:
                chunks.append((span(f"{label} ", theme.DIM) if label else "")
                              + span(value, colour, bold))

        if rig:
            chunk("", rig.get("radio") or rig.get("model_name") or "", theme.CYAN)
            chunk(_("QRG"), bands.format_frequency(rig.get("frequency")), theme.YELLOW)
            chunk(_("MODE"), rig.get("mode") or "-", theme.GREEN)
            chunk("", f"{smeter_text(rig.get('smeter', 0)):<6}", theme.TEXT)
            if rig.get("power") is not None:
                chunk(_("PWR"), f"{rig['power']}%", theme.TEXT, False)
        for label, value in self.extra:
            chunk(label, value, theme.TEXT, False)
        chunk(_("LINK"), self.link_description, theme.BLUE)
        load = LOAD.sample()
        cpu_colour = theme.RED if load.cpu >= 80 else theme.YELLOW if load.cpu >= 50 else theme.TEXT
        right = span("CPU ", theme.DIM) + span(f"{load.cpu:.0f}%", cpu_colour, True)
        if load.ram is not None:
            right += span(" · RAM ", theme.DIM) + span(f"{load.ram:.1f}%", theme.TEXT, True) \
                + span(f" {load.rss / 2**20:.0f}M", theme.DIM)
        separator = span(" · ", theme.DIM)
        self.setText(f'<table width="100%"><tr><td>{"".join(parts)}{separator.join(chunks)}'
                     f'</td><td align="right">{right}</td></tr></table>')


class Footer(QLabel):
    """UTC clock, counters and the key hint (the long one, or the short one if narrow)."""

    def __init__(self, hints: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hints = hints
        self.counters: list[tuple[str, object]] = []
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setContentsMargins(0, 2, 6, 2)
        self.setStyleSheet(f"background:{theme.PANEL};border-top:1px solid {theme.BORDER};")
        self.timer = QTimer(self, interval=1000, timeout=self.redraw)
        self.timer.start()
        self.redraw()

    def redraw(self) -> None:
        now = dt.datetime.now(dt.UTC)
        clock = (f'<span style="background:{theme.BG_BLUE};color:#000;font-weight:bold;">'
                 f"&nbsp;{now:%Y-%m-%d %H:%M:%S} UTC&nbsp;</span>")
        counters = "".join(f"&nbsp;&nbsp;&nbsp;{span(label, theme.DIM)} "
                           f"{span(value, theme.TEXT, True)}" for label, value in self.counters)
        hints = [_(h) for h in self.hints]
        chars = max(1, self.width() // max(1, self.fontMetrics().averageCharWidth()))
        hint = next((h for h in hints if len(h) + 40 < chars), hints[-1] if hints else "")
        self.setText(f'<table width="100%"><tr><td>{clock}{counters}</td>'
                     f'<td align="right">{hint_html(hint)}</td></tr></table>')

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self.redraw()


# ---- seven-segment digits ------------------------------------------------------------------

SEGMENTS = {   # a b c d e f g
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
    "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g", " ": "",
}


class SevenSegment(QWidget):
    """The frequency as on the radio's display: lit segments over faint unlit ones."""

    wheel = Signal(int)

    def __init__(self, text: str = "", colour: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text = text
        self.colour = QColor(colour or theme.AMBER)
        self.setMinimumSize(QSize(260, 64))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(420, 80)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self.wheel.emit(1 if event.angleDelta().y() > 0 else -1)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        chars = [c for c in self.text if c != "."]
        positions, count = set(), 0             # the digits a dot follows
        for c in self.text:
            if c == ".":
                positions.add(count - 1)
            else:
                count += 1
        n = max(1, len(chars))
        height = self.height() - 8
        width = min(self.width() / (n + 0.4), height * 0.62)
        thick = max(3.0, width * 0.16)
        x = (self.width() - width * n) / 2
        dim = QColor(self.colour)
        dim.setAlpha(28)
        for i, ch in enumerate(chars):
            lit = SEGMENTS.get(ch, "")
            for seg in "abcdefg":
                self._segment(painter, x + i * width, 4, width * 0.82, height, thick, seg,
                              self.colour if seg in lit else dim)
            if i in positions:
                painter.setBrush(self.colour)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(x + i * width + width * 0.9, 4 + height - thick / 2),
                                    thick * 0.55, thick * 0.55)

    @staticmethod
    def _segment(p: QPainter, x: float, y: float, w: float, h: float, t: float, seg: str,
                 colour: QColor) -> None:
        half = h / 2
        horizontal = {"a": (x + t / 2, y), "g": (x + t / 2, y + half - t / 2),
                      "d": (x + t / 2, y + h - t)}
        vertical = {"f": (x, y + t / 2, half - t), "b": (x + w - t, y + t / 2, half - t),
                    "e": (x, y + half + t / 2 - t / 2, half - t),
                    "c": (x + w - t, y + half + t / 2 - t / 2, half - t)}
        path = QPainterPath()
        if seg in horizontal:
            sx, sy = horizontal[seg]
            length = w - t
            path.moveTo(sx, sy + t / 2)
            path.lineTo(sx + t / 2, sy)
            path.lineTo(sx + length - t / 2, sy)
            path.lineTo(sx + length, sy + t / 2)
            path.lineTo(sx + length - t / 2, sy + t)
            path.lineTo(sx + t / 2, sy + t)
        else:
            sx, sy, length = vertical[seg]
            path.moveTo(sx + t / 2, sy)
            path.lineTo(sx + t, sy + t / 2)
            path.lineTo(sx + t, sy + length - t / 2)
            path.lineTo(sx + t / 2, sy + length)
            path.lineTo(sx, sy + length - t / 2)
            path.lineTo(sx, sy + t / 2)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawPath(path)


# ---- meters ---------------------------------------------------------------------------------

def smeter_fraction(raw: float) -> float:
    """S0..S9 over the left 60 %, S9..S9+60 over the rest (as the radio's own scale)."""
    if raw <= 120:
        return 0.6 * raw / 120
    return min(1.0, 0.6 + 0.4 * (raw - 120) / 121)


class Meter(QWidget):
    """One bar meter: a label, LED-like segments with a scale, and the reading."""

    SEGMENTS = 30

    def __init__(self, label: str, scale: list[tuple[float, str]] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = label
        self.scale = scale or []
        self.value: float | None = None       # 0..1
        self.reading = "–"
        self.hot = False                      # red (SWR too high, ALC over)
        self.warm_from = 1.1                  # fraction where the segments turn yellow
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: float | None, reading: str, hot: bool = False) -> None:
        self.value, self.reading, self.hot = value, reading, hot
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(300, 34)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_w, reading_w = 54, 90
        p.setFont(theme.mono(9, bold=True))
        p.setPen(QColor(theme.DIM))
        p.drawText(QRectF(0, 0, label_w, 20), Qt.AlignmentFlag.AlignVCenter, self.label)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(self.width() - reading_w, 0, reading_w, 20),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.reading)
        x0, x1 = label_w, self.width() - reading_w - 8
        width = max(10.0, x1 - x0)
        cell = width / self.SEGMENTS
        lit = 0 if self.value is None else round(self.value * self.SEGMENTS)
        for i in range(self.SEGMENTS):
            on = i < lit
            share = (i + 0.5) / self.SEGMENTS
            colour = QColor(theme.RED if self.hot and on else
                            theme.YELLOW if on and share >= self.warm_from else
                            theme.GREEN if on else theme.RAISED)
            p.fillRect(QRectF(x0 + i * cell + 1, 4, cell - 2, 12), colour)
        p.setFont(theme.mono(7))
        p.setPen(QColor(theme.DIM))
        for share, text in self.scale:
            x = x0 + share * width
            p.drawLine(QPointF(x, 18), QPointF(x, 21))
            p.drawText(QRectF(x - 20, 21, 40, 12), Qt.AlignmentFlag.AlignHCenter, text)


SMETER_SCALE = [(smeter_fraction(v), t) for v, t in
                ((13, "1"), (40, "3"), (67, "5"), (93, "7"), (120, "9"), (161, "+20"),
                 (201, "+40"), (241, "+60"))]


def smeter(meter: Meter, raw: int | None) -> None:
    if raw is None:
        meter.set(None, "–")
    else:
        meter.set(smeter_fraction(raw), smeter_text(int(raw)))


# ---- the rig's front panel ------------------------------------------------------------------

class ControlGrid(QWidget):
    """The front panel's options in columns, as the terminal draws them: the one under the
    cursor lit, the one being edited yellow. The keys are the page's (PanelKeys); the mouse
    selects (click), edits (double click) and turns (wheel)."""

    # Asked of the page: ("enter",) on a double click, ("adjust", direction) on the wheel.
    request = Signal(str, int)

    ROW = 24

    def __init__(self, model: PanelModel, bars: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        model.on_change = self.changed
        self.bars = bars
        self._cells: list[tuple[QRectF, str]] = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def changed(self) -> None:
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        rows = max((sum(1 for c in self.model.controls if c["group"] == g)
                    for g, _l in self.model.visible_groups()), default=1)
        return QSize(500, (rows + 1) * self.ROW + 6)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, self.sizeHint().height())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.model.type_key(key_name(event), event.text()):
            return
        event.ignore()                         # the page's keys

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def _at(self, point: QPointF) -> str | None:
        return next((cid for rect, cid in self._cells if rect.contains(point)), None)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus()
        control_id = self._at(event.position())
        if control_id is not None and not self.model.editing:
            self.model.selected = control_id
            self.model.refresh()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._at(event.position()) is not None:
            self.request.emit("enter", 0)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        control_id = self._at(event.position())
        if control_id is None:
            return
        if not self.model.editing:
            self.model.selected = control_id
        self.setFocus()
        self.request.emit("adjust", 1 if event.angleDelta().y() > 0 else -1)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        model, groups = self.model, self.model.visible_groups()
        self._cells = []
        if not groups:
            p.setPen(QColor(theme.DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "…")
            return
        gap = 14
        column_w = (self.width() - gap * (len(groups) - 1)) / len(groups)
        for x_index, (group, label) in enumerate(groups):
            x = x_index * (column_w + gap)
            p.setFont(theme.mono(8.5, bold=True))
            p.setPen(QColor(theme.CYAN))
            p.drawText(QRectF(x, 0, column_w, self.ROW), Qt.AlignmentFlag.AlignVCenter,
                       _(label).upper())
            for row, c in enumerate(c for c in model.controls if c["group"] == group):
                self._draw_control(p, c, QRectF(x, (row + 1) * self.ROW, column_w, self.ROW - 2))

    def _draw_control(self, p: QPainter, c: dict, rect: QRectF) -> None:
        model = self.model
        self._cells.append((rect, c["id"]))
        selected = c["id"] == model.selected
        editing = selected and model.editing
        value = model.values.get(c["id"])
        name_w = min(70.0, rect.width() * 0.38)
        name_rect = QRectF(rect.left(), rect.top() + 1, name_w, rect.height() - 2)
        if editing:
            p.setBrush(QColor(theme.BG_YELLOW))
        elif selected and self.hasFocus():
            p.setBrush(QColor(theme.BG_BLUE))
        elif selected:
            p.setBrush(QColor(theme.BORDER))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(name_rect, 3, 3)
        p.setFont(theme.mono(8.5, bold=selected))
        p.setPen(QColor("#000000") if editing or (selected and self.hasFocus()) else
                 QColor(theme.TEXT) if selected else QColor(theme.DIM))
        p.drawText(name_rect.adjusted(5, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, _(c["label"]))
        x = name_rect.right() + 8
        right = rect.right()
        if editing and model.buffer:
            p.setPen(QColor(theme.YELLOW))
            p.setFont(theme.mono(9, bold=True))
            p.drawText(QRectF(x, rect.top(), right - x, rect.height()),
                       Qt.AlignmentFlag.AlignVCenter, model.buffer + "▏")
            return
        share = fraction(c, value) if c["kind"] in (LEVEL, OFFSET) else None
        if self.bars and share is not None and c.get("bar", True) and right - x > 120:
            bar = QRectF(x, rect.center().y() - 3, min(80.0, (right - x) * 0.45), 6)
            p.setBrush(QColor(theme.RAISED))
            p.drawRoundedRect(bar, 3, 3)
            p.setBrush(QColor(theme.YELLOW if c["kind"] == OFFSET else theme.GREEN))
            if c["kind"] == OFFSET:
                mid = bar.left() + bar.width() / 2
                end = bar.left() + bar.width() * share
                p.drawRect(QRectF(min(mid, end), bar.top(), abs(end - mid), bar.height()))
                p.setPen(QPen(QColor(theme.TEXT), 1))
                p.drawLine(QPointF(mid, bar.top() - 1), QPointF(mid, bar.bottom() + 1))
                p.setPen(Qt.PenStyle.NoPen)
            else:
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * share,
                                         bar.height()), 3, 3)
            x = bar.right() + 8
        on = c["kind"] == TOGGLE and value
        p.setFont(theme.mono(9, bold=value is not None))
        p.setPen(QColor(theme.YELLOW if editing else theme.GREEN if on else
                        theme.TEXT if value is not None else theme.DIM))
        p.drawText(QRectF(x, rect.top(), right - x, rect.height()),
                   Qt.AlignmentFlag.AlignVCenter, format_value(c, value))


# ---- band scope -------------------------------------------------------------------------------

def _lookup() -> np.ndarray:
    """The waterfall palette as 256 RGBA rows (black, blue, cyan, green, yellow, red, white)."""
    table = np.zeros((256, 4), dtype=np.uint8)
    for i in range(256):
        x = i / 255
        for (x0, c0), (x1, c1) in zip(PALETTE_STOPS, PALETTE_STOPS[1:], strict=False):
            if x <= x1:
                t = (x - x0) / (x1 - x0) if x1 > x0 else 0
                table[i, :3] = [round(a + (b - a) * t) for a, b in zip(c0, c1, strict=True)]
                break
        table[i, 3] = 255
    return table


LOOKUP = _lookup()
MAX_ROWS = 300
ROW_PIXELS = 3           # each sweep a few pixels high, so the waterfall fills at a glance


class Scope(QWidget):
    """Spectrum over a waterfall with the frequency scale, the dial and the passband; the
    skimmer's stations as marks above. Click tunes there, the wheel tunes by the step."""

    clicked = Signal(int)          # Hz
    wheel = Signal(int)            # +1 / -1

    def __init__(self, parent: QWidget | None = None, spectrum_share: float = 0.38) -> None:
        super().__init__(parent)
        self.view = ScopeView()
        self.spectrum_share = spectrum_share
        self.mark_row = False
        self.floor, self.span = 0.10, 0.75
        self._rows: deque[np.ndarray] = deque(maxlen=MAX_ROWS)
        self._edges = (0, 0)
        self._image: QImage | None = None
        self.setMinimumHeight(80)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- data ----

    def add_frame(self, amplitudes: np.ndarray, start: int, end: int) -> None:
        if (start, end) != self._edges:
            self._rows.clear()
            self._edges = (start, end)
        self.view.start, self.view.end = start, end
        self._rows.appendleft(np.asarray(amplitudes, dtype=float))
        self._image = None
        self.update()

    def clear(self) -> None:
        self._rows.clear()
        self._image = None
        self.update()

    def recolour(self) -> None:
        """Draw the waterfall again (the contrast changed)."""
        self._image = None
        self.update()

    def signals(self, threshold: float = 0.12) -> list[int]:
        if not self._rows:
            return []
        return find_signals(self._rows[0], self.view.start, self.view.end, threshold)

    def _level(self, values: np.ndarray) -> np.ndarray:
        return np.clip((values - self.floor) / self.span, 0.0, 1.0)

    def _waterfall(self) -> QImage | None:
        if self._image is None and self._rows:
            width = max(len(r) for r in self._rows)
            data = np.zeros((len(self._rows), width), dtype=np.uint8)
            for i, row in enumerate(self._rows):
                data[i, :len(row)] = (self._level(row) * 255).astype(np.uint8)
            rgba = np.ascontiguousarray(LOOKUP[data])
            self._image = QImage(rgba.data, width, len(self._rows), width * 4,
                                 QImage.Format.Format_RGBA8888).copy()
        return self._image

    # ---- geometry ----

    def x_of(self, hz: int | None) -> float | None:
        start, end = self.view.start, self.view.end
        if hz is None or end <= start or not start <= hz <= end:
            return None
        return (hz - start) * self.width() / (end - start)

    def hz_at(self, x: float) -> int | None:
        start, end = self.view.start, self.view.end
        if end <= start or self.width() <= 0:
            return None
        return round(start + x * (end - start) / self.width())

    # ---- mouse ----

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        hz = self.hz_at(event.position().x())
        if hz is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(round(hz / 10) * 10)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        hz = self.hz_at(event.position().x())
        self.setToolTip(f"{hz / 1e6:.4f} MHz" if hz else "")

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self.wheel.emit(1 if event.angleDelta().y() > 0 else -1)

    # ---- drawing ----

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#000000"))
        top = 16 if self.mark_row else 0
        scale_h = 16
        spectrum_h = max(20, int((h - top - scale_h) * self.spectrum_share))
        fall_top = top + spectrum_h + scale_h
        dial_x = self.x_of(self.view.dial)
        # passband
        if self.view.passband:
            a, b = (self.x_of(f) for f in self.view.passband)
            if a is not None and b is not None:
                p.fillRect(QRectF(min(a, b), top, abs(b - a), spectrum_h), QColor(40, 50, 110))
        # spectrum
        if self._rows:
            row = self._level(self._rows[0])
            n = len(row)
            path = QPainterPath()
            path.moveTo(0, top + spectrum_h)
            for i, v in enumerate(row):
                path.lineTo(i * w / max(1, n - 1), top + spectrum_h - v * (spectrum_h - 2))
            path.lineTo(w, top + spectrum_h)
            path.closeSubpath()
            gradient = QLinearGradient(0, top, 0, top + spectrum_h)
            gradient.setColorAt(0, QColor(120, 220, 255, 200))
            gradient.setColorAt(1, QColor(20, 60, 120, 120))
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillPath(path, QBrush(gradient))
            p.setPen(QPen(QColor(150, 230, 255), 1.2))
            p.drawPath(path)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # waterfall
        image = self._waterfall()
        if image is not None and h > fall_top:
            rows_shown = min(image.height(), -(-(h - fall_top) // ROW_PIXELS))
            p.drawImage(QRectF(0, fall_top, w, rows_shown * ROW_PIXELS), image,
                        QRectF(0, 0, image.width(), rows_shown))
        self._draw_scale(p, top + spectrum_h, scale_h, w)
        if dial_x is not None:
            p.setPen(QPen(QColor(theme.RED), 1.5))
            p.drawLine(QPointF(dial_x, top), QPointF(dial_x, h))
        if self.mark_row:
            self._draw_marks(p, w)

    def _draw_scale(self, p: QPainter, y: float, height: float, w: int) -> None:
        start, end = self.view.start, self.view.end
        p.fillRect(QRectF(0, y, w, height), QColor(20, 20, 20))
        if end <= start or w <= 0:
            return
        from ic705suite.ui.waterfall import _nice_step

        step = _nice_step((end - start) * 90 / w)
        # As many decimals as the step needs, so no two labels read the same.
        decimals = 3 if step >= 1000 else 4 if step >= 100 else 5
        p.setFont(theme.mono(7.5))
        p.setPen(QColor(170, 170, 170))
        for hz in range(-(-start // step) * step, end + 1, step):
            x = self.x_of(hz)
            if x is None:
                continue
            p.drawLine(QPointF(x, y), QPointF(x, y + 4))
            p.drawText(QPointF(x + 3, y + height - 3), f"{hz / 1e6:.{decimals}f}")

    def _draw_marks(self, p: QPainter, w: int) -> None:
        view = self.view
        # On the scope's black, the vivid colours whatever the theme.
        colours = {None: theme.BG_YELLOW, "cq": theme.BG_GREEN, "contest": theme.BG_MAGENTA}
        p.setFont(theme.mono(7.5, bold=True))
        taken: list[tuple[float, float]] = []
        ranked = sorted(view.marks, key=lambda m: (0 if m[0] == view.chosen else
                                                   1 if m[0] in view.calling else
                                                   2 if m[0] in view.named else 3))
        for hz, label in ranked:
            x = self.x_of(hz)
            if x is None:
                continue
            colour = QColor(colours[view.calling.get(hz)])
            chosen = hz == view.chosen
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            triangle = QPainterPath()
            triangle.moveTo(x - 4, 3)
            triangle.lineTo(x + 4, 3)
            triangle.lineTo(x, 11)
            triangle.closeSubpath()
            p.drawPath(triangle)
            text_w = p.fontMetrics().horizontalAdvance(label) + 6
            left = x + 6 if x + 6 + text_w < w else x - 6 - text_w
            if any(a < left + text_w and left < b for a, b in taken) and not chosen:
                continue
            taken.append((left, left + text_w))
            if chosen:
                p.fillRect(QRectF(left, 0, text_w, 14), colour)
                p.setPen(QColor("#000000"))
            else:
                p.setPen(colour)
            p.drawText(QRectF(left + 3, 0, text_w, 14), Qt.AlignmentFlag.AlignVCenter, label)


# ---- tables -----------------------------------------------------------------------------------

class Table(QTableWidget):
    """A table the page's keys reach: Space goes to the page (it opens, as Enter), and
    the focus is drawn by the frame around it. Headings sit where their values do: on the
    left, or on the right over the columns of numbers (right_align)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                                                    | Qt.AlignmentFlag.AlignVCenter)
        self.right_columns: set[int] = set()

    def right_align(self, *columns: int) -> None:
        """Numbers: these columns' values and headings on the right, decimals in a column."""
        self.right_columns = set(columns)
        for column in columns:
            header = self.horizontalHeaderItem(column)
            if header is not None:
                header.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)

    def setItem(self, row: int, column: int, item) -> None:  # noqa: N802 - Qt's name
        if column in self.right_columns:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        super().setItem(row, column, item)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            event.ignore()
            return
        super().keyPressEvent(event)


def mono_label(text: str = "", size: float = 10, bold: bool = False) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.mono(size, bold))
    label.setTextFormat(Qt.TextFormat.RichText)
    return label

