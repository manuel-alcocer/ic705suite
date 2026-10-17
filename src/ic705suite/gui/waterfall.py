"""The FT8 waterfall: the band's audio, newest row on top, as WSJT-X's Wide Graph, and more:
the call of each station decoded is written on its trace, a click on a call chooses that
station (a double click answers it) and a click elsewhere puts our transmit frequency there.
Our transmit frequency is marked in red, the station we work in green, and each 15-second
cycle starts at a dashed line with its time.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from ic705suite.apps.ft8 import waterfall
from ic705suite.gui import theme

HISTORY = 480                  # rows kept (two minutes)
SCALE_HEIGHT = 20              # pixels of the frequency scale on top
SIGNAL_WIDTH = 50              # Hz an FT8 signal takes from its frequency (8 tones)
CYCLE = 15
LABEL_AT = 7.0                 # seconds into its cycle where a call is written

# WSJT-X's default colours: black, blue, cyan, green, yellow, red.
GRADIENT = [(0.0, (0, 0, 0)), (0.2, (10, 20, 140)), (0.4, (20, 140, 220)),
            (0.6, (40, 200, 90)), (0.8, (240, 220, 40)), (1.0, (240, 40, 30))]


def colour_table(gain: float, zero: float) -> list[int]:
    """The 256 colours of the levels: `zero` dB over the noise floor is black, `30 / gain`
    dB more the top of the scale."""
    table = []
    for level in range(256):
        x = (waterfall.db_over_floor(level) - zero) * gain / 30
        x = min(1.0, max(0.0, x))
        for (x0, c0), (x1, c1) in zip(GRADIENT, GRADIENT[1:], strict=False):
            if x <= x1:
                f = (x - x0) / (x1 - x0) if x1 > x0 else 0
                r, g, b = (round(a + (b_ - a) * f) for a, b_ in zip(c0, c1, strict=True))
                break
        table.append(QColor(r, g, b).rgb())
    return table


@dataclass
class Label:
    row: int              # the absolute row it is written on
    freq: int
    text: str
    start: float          # its cycle
    decode: dict


class WaterfallView(QWidget):
    freq_clicked = Signal(int)                       # Hz: put our transmit frequency there
    station_clicked = Signal(float, dict)            # a call on the waterfall: chosen
    station_double_clicked = Signal(float, dict)     # and answered
    station_menu = Signal(float, dict, QPoint)       # right-clicked: its menu, there

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(120)
        self.columns = waterfall.columns()
        self.step = waterfall.STEP
        self.low_hz, self.high_hz = 200, 3000         # what is shown
        self.levels = np.zeros((HISTORY, self.columns), np.uint8)   # row 0: the newest
        self.total = 0                                 # rows added so far
        self.times: deque[tuple[int, float]] = deque(maxlen=HISTORY)   # (row, time)
        self.cycles: deque[tuple[int, str]] = deque(maxlen=16)        # (row, "HH:MM:SS")
        self.labels: deque[Label] = deque(maxlen=400)
        self.tx_freq = 1500
        self.rx_freq = 0
        self.gain, self.zero = 1.0, 0.0
        self._table = colour_table(self.gain, self.zero)
        self._label_rects: list[tuple[QRect, Label]] = []

    # ---- what it shows ------------------------------------------------------------------------

    def set_colours(self, gain: float | None = None, zero: float | None = None) -> None:
        self.gain = self.gain if gain is None else gain
        self.zero = self.zero if zero is None else zero
        self._table = colour_table(self.gain, self.zero)
        self.update()

    def set_markers(self, tx_freq: int, rx_freq: int) -> None:
        if (tx_freq, rx_freq) != (self.tx_freq, self.rx_freq):
            self.tx_freq, self.rx_freq = tx_freq, rx_freq
            self.update()

    def add_row(self, levels: bytes, when: float, low: int = 0, step: int = waterfall.STEP) \
            -> None:
        row = np.frombuffer(levels, np.uint8)
        if low != 0 or step != self.step or len(row) != self.columns:
            return                                     # another layout: not ours
        self.levels[1:] = self.levels[:-1]
        self.levels[0] = row
        previous = self.times[-1][1] if self.times else None
        self.times.append((self.total, when))
        if previous is not None and math.floor(when / CYCLE) != math.floor(previous / CYCLE):
            start = math.floor(when / CYCLE) * CYCLE
            self.cycles.append((self.total, time.strftime("%H:%M:%S", time.gmtime(start))))
        self.total += 1
        self.update()

    def add_labels(self, start: float, decodes: list[dict]) -> None:
        """The calls decoded in a cycle, on their traces (halfway through the cycle)."""
        if not self.times:
            return
        moment = start + LABEL_AT
        row, when = min(self.times, key=lambda rt: abs(rt[1] - moment))
        if abs(when - moment) > CYCLE:
            return                                     # that cycle is not on the waterfall
        known = {(label.start, label.text) for label in self.labels}
        for d in decodes:
            text = d.get("sender") or d.get("message", "").split(" ")[-1]
            if d.get("kind") == "tx" or not text or (start, text) in known:
                continue
            self.labels.append(Label(row, int(d["freq"]), text, start, d))
        self.update()

    # ---- coordinates --------------------------------------------------------------------------

    def _x(self, hz: float) -> float:
        return (hz - self.low_hz) / (self.high_hz - self.low_hz) * self.width()

    def _hz(self, x: float) -> int:
        return round(self.low_hz + x / max(1, self.width()) * (self.high_hz - self.low_hz))

    def _y(self, row: int) -> int:
        return SCALE_HEIGHT + (self.total - 1 - row)

    # ---- drawing ------------------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        rows = min(HISTORY, max(0, height - SCALE_HEIGHT))
        first, last = self.low_hz // self.step, self.high_hz // self.step
        visible = np.ascontiguousarray(self.levels[:rows, first:last])
        if rows and visible.size:
            image = QImage(visible.data, visible.shape[1], rows, visible.shape[1],
                           QImage.Format.Format_Indexed8)
            image.setColorTable(self._table)
            painter.drawImage(QRectF(0, SCALE_HEIGHT, width, rows), image)
        self._draw_cycles(painter, width)
        self._draw_labels(painter, height)
        self._draw_scale(painter, width)
        self._draw_marker(painter, self.tx_freq, QColor(theme.RED or "#ff5c5c"), height)
        if self.rx_freq:
            self._draw_marker(painter, self.rx_freq, QColor(theme.GREEN or "#43d17a"), height)
        painter.end()

    def _draw_scale(self, painter: QPainter, width: int) -> None:
        painter.fillRect(0, 0, width, SCALE_HEIGHT, QColor(24, 28, 36))
        painter.setPen(QColor(200, 205, 215))
        font = QFont(painter.font())
        font.setPointSizeF(8)
        painter.setFont(font)
        for hz in range(self.low_hz - self.low_hz % 100, self.high_hz + 1, 100):
            x = round(self._x(hz))
            major = hz % 500 == 0
            painter.drawLine(x, SCALE_HEIGHT - (8 if major else 4), x, SCALE_HEIGHT - 1)
            if major:
                painter.drawText(x + 3, SCALE_HEIGHT - 8, str(hz))

    def _draw_marker(self, painter: QPainter, hz: int, colour: QColor, height: int) -> None:
        left, right = self._x(hz), self._x(hz + SIGNAL_WIDTH)
        band = QColor(colour)
        band.setAlpha(40)
        painter.fillRect(QRectF(left, SCALE_HEIGHT, right - left, height), band)
        pen = QPen(colour, 2)
        painter.setPen(pen)
        painter.drawLine(round(left), 2, round(left), SCALE_HEIGHT - 1)
        painter.drawLine(round(left), 2, round(right), 2)
        painter.drawLine(round(right), 2, round(right), SCALE_HEIGHT - 1)

    def _draw_cycles(self, painter: QPainter, width: int) -> None:
        pen = QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine)
        font = QFont(painter.font())
        font.setPointSizeF(7.5)
        painter.setFont(font)
        for row, label in self.cycles:
            y = self._y(row)
            if y <= SCALE_HEIGHT or y >= self.height():
                continue
            painter.setPen(pen)
            painter.drawLine(0, y, width, y)
            painter.setPen(QColor(255, 255, 255, 170))
            painter.drawText(4, y - 2, label)

    def _draw_labels(self, painter: QPainter, height: int) -> None:
        font = QFont(painter.font())
        font.setPointSizeF(8)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        self._label_rects = []
        for label in self.labels:
            y = self._y(label.row)
            if y <= SCALE_HEIGHT + metrics.height() or y >= height:
                continue
            x = round(self._x(label.freq))
            rect = QRect(x, y - metrics.ascent(), metrics.horizontalAdvance(label.text) + 6,
                         metrics.height())
            painter.fillRect(rect, QColor(0, 0, 0, 150))
            painter.setPen(QColor(theme.GREEN or "#43d17a") if label.decode.get("kind") == "cq"
                           else QColor(235, 235, 235))
            painter.drawText(rect.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                             label.text)
            self._label_rects.append((rect, label))

    # ---- the mouse ----------------------------------------------------------------------------

    def _label_at(self, point: QPoint) -> Label | None:
        for rect, label in reversed(self._label_rects):
            if rect.contains(point):
                return label
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        label = self._label_at(event.position().toPoint())
        if label is not None:
            self.station_clicked.emit(label.start, label.decode)
        else:
            self.freq_clicked.emit(max(200, min(3000, self._hz(event.position().x()))))

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        label = self._label_at(event.position().toPoint())
        if label is not None:
            self.station_double_clicked.emit(label.start, label.decode)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt's name
        label = self._label_at(event.pos())
        if label is not None:
            self.station_menu.emit(label.start, label.decode, event.globalPos())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = event.position().toPoint()
        label = self._label_at(point)
        hz = self._hz(event.position().x())
        text = f"{hz} Hz"
        if label is not None:
            d = label.decode
            text = f"{d.get('message', '')}\n{d.get('snr')} dB · {d.get('freq')} Hz"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)
