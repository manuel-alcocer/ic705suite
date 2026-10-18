"""D-STAR's map: the FT8 map's world (land, night, zoom and drag) with the stations that
gave their position by D-PRS, each where it was last reported (the last week): a station
green, a weather station cyan, an object or item magenta, fading with age; the station on
air ringed red, our own station the yellow square. A click on a station says what it is in
the key; a double click finds it among the D-PRS reports."""

from __future__ import annotations

import json
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen

from ic705suite import geo
from ic705suite.apps.dstar import dprs
from ic705suite.apps.dstar.app import MAP_DAYS, ago, base_call
from ic705suite.gui import theme
from ic705suite.gui.ft8map import Heard, MapView
from ic705suite.i18n import _

LABELS_UP_TO = 40          # stations: below this many every one has its call written


def kind_colour(kind: str) -> QColor:
    if kind == "weather":
        return QColor(theme.CYAN or "#4dd0e1")
    if kind in ("object", "item"):
        return QColor(theme.MAGENTA or "#e070ff")
    return QColor(theme.GREEN or "#43d17a")


class DStarMap(MapView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid_on = False                  # a cleaner map: the stations matter here
        self.traffic = False
        self.on_air = ""                      # the station on air's call

    def set_stations(self, rows: list[dict]) -> None:
        """The stations to show (apps.dstar.app.map_stations)."""
        self.heard = {r["name"]: Heard(r["name"], r["lat"], r["lon"], False, r,
                                       r["received"], r["received"]) for r in rows}
        self.update()

    def set_on_air(self, call: str) -> None:
        call = base_call(call)
        if call != self.on_air:
            self.on_air = call
            self.update()

    # nothing moves on this map
    def paths(self, now: float | None = None) -> list:
        return []

    def traffic_paths(self, now: float | None = None) -> list:
        return []

    def _draw_heard(self, painter: QPainter) -> None:
        """The dots, oldest first (the newest on top); then the calls, newest first, each
        only where it does not cover one already written (zoom in for the rest)."""
        now = time.time()
        font = QFont(painter.font())
        font.setPointSizeF(8)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        red = QColor(theme.RED or "#ff5c5c")
        self._spots = []
        labels = self.zoom >= 2 or len(self.heard) <= LABELS_UP_TO
        drawn = []
        for heard in sorted(self.heard.values(), key=lambda h: h.seen):
            p = self._xy(heard.lat, heard.lon)
            age = (now - heard.seen) / (MAP_DAYS * 86400)
            colour = kind_colour(heard.decode.get("kind", ""))
            colour.setAlphaF(max(0.35, 1 - age))
            radius = 4.5
            air = bool(self.on_air) and base_call(heard.call) == self.on_air
            if air:
                painter.setPen(QPen(red, 2.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(p, radius + 4, radius + 4)
            painter.setPen(QPen(colour, 1.5))
            painter.setBrush(QBrush(colour))
            painter.drawEllipse(p, radius, radius)
            drawn.append((heard, p, colour, air,
                          QRectF(p.x() - radius - 2, p.y() - radius - 2, radius * 2 + 4,
                                 radius * 2 + 4)))
        taken = []
        if self.home is not None:
            h = self._xy(*self.home)
            taken.append(QRectF(h.x() + 6, h.y() - 6 - metrics.ascent(),
                                metrics.horizontalAdvance(self.home_label) + 4,
                                metrics.height()))
        spots = {}
        for heard, p, colour, air, rect in sorted(drawn, key=lambda d: (not d[3], -d[0].seen)):
            label = QRectF(p.x() + 7, p.y() - metrics.ascent() / 2 - 2,
                           metrics.horizontalAdvance(heard.call) + 2, metrics.height())
            if (labels or air) and (air or not any(label.intersects(t) for t in taken)):
                painter.setPen(red if air else colour)
                painter.drawText(label, Qt.AlignmentFlag.AlignLeft, heard.call)
                taken.append(label)
                rect = rect.united(label)
            spots[heard.call] = (rect, heard)
        self._spots = [spots[h.call] for h, *_rest in drawn]

    def _draw_key(self, painter: QPainter) -> None:
        font = QFont(painter.font())
        font.setPointSizeF(8)
        font.setBold(False)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        rows = [("dot", kind_colour("position"), _("station")),
                ("dot", kind_colour("weather"), _("weather station")),
                ("dot", kind_colour("object"), _("object or item")),
                ("ring", QColor(theme.RED or "#ff5c5c"), _("on air now")),
                ("home", QColor(theme.YELLOW or "#ffc94d"), _("your station"))]
        title = _("{n} stations, last {days} days").format(n=len(self.heard), days=MAP_DAYS)
        line = metrics.height() + 2
        width = max(max(metrics.horizontalAdvance(t) for _k, _c, t in rows) + 30,
                    metrics.horizontalAdvance(title) + 16)
        under = self._under
        if under:
            width = max(width, metrics.horizontalAdvance(under) + 16)
        height = line * (len(rows) + 1 + (1 if under else 0)) + 10
        box = QRectF(self.width() - width - 8, self.height() - height - 8, width, height)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        painter.setBrush(QBrush(QColor(10, 16, 26, 200)))
        painter.drawRoundedRect(box, 5, 5)
        y = box.top() + 5
        painter.setPen(QColor(220, 225, 232))
        painter.drawText(QRectF(box.left() + 8, y, width - 12, line),
                         Qt.AlignmentFlag.AlignVCenter, title)
        y += line
        for kind, colour, text in rows:
            mark = QPointF(box.left() + 13, y + line / 2)
            if kind == "ring":
                painter.setPen(QPen(colour, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(mark, 6, 6)
            elif kind == "home":
                painter.setPen(QPen(QColor(255, 255, 255), 1.5))
                painter.setBrush(QBrush(colour))
                painter.drawRect(QRectF(mark.x() - 5, mark.y() - 4, 10, 8))
            else:
                painter.setPen(QPen(colour, 1.5))
                painter.setBrush(QBrush(colour))
                painter.drawEllipse(mark, 4, 4)
            painter.setPen(QColor(220, 225, 232))
            painter.drawText(QRectF(box.left() + 26, y, width - 28, line),
                             Qt.AlignmentFlag.AlignVCenter, text)
            y += line
        if under:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(QRectF(box.left() + 8, y, width - 12, line),
                             Qt.AlignmentFlag.AlignVCenter, under)

    def under(self, point: QPointF) -> str:
        heard = self._heard_at(point)
        if heard is None:
            return super().under(point)
        row = heard.decode
        parts = [heard.call]
        if row.get("kind") == "weather" and row.get("weather"):
            parts.append(dprs.weather_text(json.loads(row["weather"])))
        elif row.get("symbol"):
            parts.append(dprs.symbol_name(row["symbol"]))
        if self.home is not None:
            there = (heard.lat, heard.lon)
            parts.append(f"{round(geo.distance_km(self.home, there))} km "
                         f"{round(geo.bearing_deg(self.home, there))}°")
        parts.append(ago(heard.seen))
        if row.get("via"):
            parts.append(_("via {via}").format(via=row["via"]))
        return " · ".join(parts)
