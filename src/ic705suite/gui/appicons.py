"""The applications' icons on the dashboard, drawn with QPainter in one colour (the theme's):
no image files to bundle, the same on every platform, and drawn again when the theme
changes. Each one is drawn on a 48 x 48 canvas and scaled to the size asked for.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

CANVAS = 48.0


def _radio(p: QPainter) -> None:
    """A transceiver: the case, its display and the big knob."""
    p.drawRoundedRect(QRectF(4, 12, 40, 26), 4, 4)
    p.drawRect(QRectF(9, 17, 18, 8))
    p.drawEllipse(QPointF(36, 25), 5, 5)
    p.drawLine(QPointF(10, 31), QPointF(26, 31))
    p.drawLine(QPointF(14, 12), QPointF(10, 5))          # the antenna


def _cw(p: QPainter) -> None:
    """Morse: C (-.-.) on one line, a straight key under it."""
    y = 15
    x = 5.0
    for long in (True, False, True, False):
        width = 9.0 if long else 3.0
        p.drawLine(QPointF(x, y), QPointF(x + width, y))
        x += width + 4
    p.drawLine(QPointF(6, 39), QPointF(42, 39))           # the base
    p.drawLine(QPointF(10, 32), QPointF(38, 28))          # the lever
    p.drawEllipse(QPointF(38, 26), 3.5, 3.5)               # the knob
    p.drawLine(QPointF(16, 31), QPointF(16, 39))


def _dstar(p: QPainter) -> None:
    """A star (D-STAR) over a digital step."""
    points = []
    for i in range(10):
        r = 17 if i % 2 == 0 else 7
        a = -math.pi / 2 + i * math.pi / 5
        points.append(QPointF(24 + r * math.cos(a), 22 + r * math.sin(a)))
    p.drawPolygon(QPolygonF(points))
    step = QPainterPath(QPointF(6, 44))
    for x, y in ((14, 44), (14, 40), (24, 40), (24, 44), (34, 44), (34, 40), (42, 40)):
        step.lineTo(x, y)
    p.drawPath(step)


def _antenna(p: QPainter) -> None:
    """A vertical antenna with its waves."""
    p.drawLine(QPointF(24, 12), QPointF(24, 42))
    p.drawLine(QPointF(24, 42), QPointF(16, 44))
    p.drawLine(QPointF(24, 42), QPointF(32, 44))
    p.drawLine(QPointF(24, 30), QPointF(17, 36))
    p.drawLine(QPointF(24, 30), QPointF(31, 36))
    for r in (7.0, 13.0):
        box = QRectF(24 - r, 12 - r, 2 * r, 2 * r)
        p.drawArc(box, 30 * 16, 60 * 16)                   # right
        p.drawArc(box, 90 * 16, 60 * 16)                   # left


def _logbook(p: QPainter) -> None:
    """An open book with lines."""
    left = QPainterPath(QPointF(24, 12))
    left.cubicTo(18, 8, 10, 8, 5, 10)
    left.lineTo(5, 38)
    left.cubicTo(10, 36, 18, 36, 24, 40)
    left.cubicTo(30, 36, 38, 36, 43, 38)
    left.lineTo(43, 10)
    left.cubicTo(38, 8, 30, 8, 24, 12)
    p.drawPath(left)
    p.drawLine(QPointF(24, 12), QPointF(24, 40))
    for y in (17.0, 23.0, 29.0):
        p.drawLine(QPointF(10, y), QPointF(19, y))
        p.drawLine(QPointF(29, y), QPointF(38, y))


def _packet(p: QPainter) -> None:
    """A terminal: the screen, a prompt and the cursor."""
    p.drawRoundedRect(QRectF(4, 8, 40, 30), 3, 3)
    p.drawLine(QPointF(10, 17), QPointF(16, 22))
    p.drawLine(QPointF(16, 22), QPointF(10, 27))
    p.drawLine(QPointF(20, 28), QPointF(30, 28))
    p.drawLine(QPointF(18, 38), QPointF(16, 43))
    p.drawLine(QPointF(30, 38), QPointF(32, 43))
    p.drawLine(QPointF(12, 43), QPointF(36, 43))


def _ft8(p: QPainter) -> None:
    """The band's activity: signals as bars over the passband, as on a waterfall."""
    p.drawLine(QPointF(4, 40), QPointF(44, 40))
    for x, top in ((9, 30), (15, 18), (21, 34), (27, 12), (33, 26), (39, 22)):
        p.drawLine(QPointF(x, 40), QPointF(x, top))


def _speaker_box(p: QPainter) -> None:
    p.drawPolygon(QPolygonF([QPointF(6, 18), QPointF(14, 18), QPointF(25, 8), QPointF(25, 40),
                             QPointF(14, 30), QPointF(6, 30)]))


def _speaker(p: QPainter) -> None:
    """The receive audio on the speakers: a loudspeaker and its sound."""
    _speaker_box(p)
    for radius in (8.0, 15.0):
        p.drawArc(QRectF(25 - radius, 24 - radius, 2 * radius, 2 * radius), -50 * 16, 100 * 16)


def _speaker_off(p: QPainter) -> None:
    """The speakers silent: the loudspeaker, crossed out."""
    _speaker_box(p)
    p.drawLine(QPointF(31, 18), QPointF(42, 30))
    p.drawLine(QPointF(42, 18), QPointF(31, 30))


def _microphone(p: QPainter) -> None:
    """Talking through the radio: a microphone on its stand."""
    p.drawRoundedRect(QRectF(17, 5, 14, 24), 7, 7)
    p.drawArc(QRectF(11, 13, 26, 24), 180 * 16, 180 * 16)
    p.drawLine(QPointF(24, 37), QPointF(24, 43))
    p.drawLine(QPointF(16, 43), QPointF(32, 43))


DRAWINGS: dict[str, Callable[[QPainter], None]] = {
    "view": _radio, "cw": _cw, "dstar": _dstar, "swr": _antenna, "log": _logbook,
    "packet": _packet, "ft8": _ft8, "speaker": _speaker, "speaker_off": _speaker_off,
    "microphone": _microphone, "phone": _microphone,
}


def app_icon(app_id: str, colour: str, size: int = 48) -> QIcon:
    """The application's icon in `colour`, `size` pixels square (a plain circle for one
    without a drawing)."""
    ratio = 2.0                                            # sharp on HiDPI screens too
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / CANVAS, size / CANVAS)
    pen = QPen(QColor(colour), 2.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    DRAWINGS.get(app_id, lambda p: p.drawEllipse(QPointF(24, 24), 16, 16))(painter)
    painter.end()
    return QIcon(pixmap)
