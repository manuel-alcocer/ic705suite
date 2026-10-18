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
FIT_MAX_ZOOM = 12.0        # the view fitted to the stations: no nearer than this


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
        self.groups: dict[str, list] = {}     # a group's call -> its stations (Heard)
        self._fit_pending = True              # the first stations: the view fitted to them

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

    def _draw_home(self, painter: QPainter) -> None:
        """Our square only: its call is written with the stations', where there is room."""
        if self.home is None:
            return
        p = self._xy(*self.home)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QBrush(QColor(theme.YELLOW or "#ffc94d")))
        painter.drawRect(QRectF(p.x() - 5, p.y() - 5, 10, 10))

    @staticmethod
    def _label_places(p: QPointF, width: float, height: float, ascent: float) -> list[QRectF]:
        """Where a call may go around its dot: right, left, above, below."""
        return [QRectF(p.x() + 8, p.y() - ascent / 2 - 2, width, height),
                QRectF(p.x() - 8 - width, p.y() - ascent / 2 - 2, width, height),
                QRectF(p.x() - width / 2, p.y() - 8 - height, width, height),
                QRectF(p.x() - width / 2, p.y() + 7, width, height)]

    def _draw_heard(self, painter: QPainter) -> None:
        """The dots, oldest first (the newest on top). Stations on the same spot (too near
        at this zoom) make a group, called by its newest (or the one on air) with "+n";
        each call goes beside its dot where it covers no other, else is left out (zoom in,
        or the mouse, tells). Our own call last, if there is room."""
        if self._fit_pending and self.heard and self.width() > 50:
            self.fit()
        now = time.time()
        font = QFont(painter.font())
        font.setPointSizeF(8)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        red = QColor(theme.RED or "#ff5c5c")
        labels = self.zoom >= 2 or len(self.heard) <= LABELS_UP_TO
        drawn = []
        for heard in sorted(self.heard.values(), key=lambda h: h.seen):
            p = self._xy(heard.lat, heard.lon)
            colour = kind_colour(heard.decode.get("kind", ""))
            colour.setAlphaF(max(0.35, 1 - (now - heard.seen) / (MAP_DAYS * 86400)))
            air = bool(self.on_air) and base_call(heard.call) == self.on_air
            if air:
                painter.setPen(QPen(red, 2.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(p, 8.5, 8.5)
            painter.setPen(QPen(colour, 1.5))
            painter.setBrush(QBrush(colour))
            painter.drawEllipse(p, 4.5, 4.5)
            drawn.append((heard, p, colour, air))
        # the groups: each station joins the first (by priority) whose dot it touches
        leaders: list[list] = []
        for heard, p, colour, air in sorted(drawn, key=lambda d: (not d[3], -d[0].seen)):
            for group in leaders:
                lp = group[1]
                if abs(lp.x() - p.x()) < 9 and abs(lp.y() - p.y()) < 9:
                    group[4].append(heard)
                    break
            else:
                leaders.append([heard, p, colour, air, [heard]])
        taken: list[QRectF] = [QRectF(lp.x() - 6, lp.y() - 6, 12, 12) for _h, lp, *_r in leaders]
        self._spots = []
        self.groups = {}
        for heard, p, colour, air, members in leaders:
            text = heard.call + (f" +{len(members) - 1}" if len(members) > 1 else "")
            width, height = metrics.horizontalAdvance(text) + 2, metrics.height()
            rect = QRectF(p.x() - 7, p.y() - 7, 14, 14)
            if labels or air:
                for place in self._label_places(p, width, height, metrics.ascent()):
                    if air or not any(place.intersects(t) and not t.contains(p)
                                      for t in taken):
                        painter.setPen(red if air else colour)
                        painter.drawText(place, Qt.AlignmentFlag.AlignLeft, text)
                        taken.append(place)
                        rect = rect.united(place)
                        break
            self.groups[heard.call] = members
            self._spots.append((rect, heard))
        if self.home is not None and self.home_label:
            h = self._xy(*self.home)
            width = metrics.horizontalAdvance(self.home_label) + 2
            for place in self._label_places(h, width, metrics.height(), metrics.ascent()):
                if not any(place.intersects(t) for t in taken):
                    painter.setPen(QColor(255, 255, 255))
                    painter.drawText(place, Qt.AlignmentFlag.AlignLeft, self.home_label)
                    break

    def fit(self) -> None:
        """The view that holds every station and us, as near as it can (a whole world
        when they are spread over it)."""
        self._fit_pending = False
        points = [(h.lat, h.lon) for h in self.heard.values()]
        if self.home is not None:
            points.append(self.home)
        if not points:
            return
        lats, lons = [p[0] for p in points], [p[1] for p in points]
        span_lat = max(max(lats) - min(lats), 2.0)
        span_lon = max(max(lons) - min(lons), 3.0)
        base = min(self.width() / 360.0, self.height() / 110.0)
        scale = min(self.width() * 0.8 / span_lon, self.height() * 0.7 / span_lat)
        self.zoom = min(FIT_MAX_ZOOM, max(1.0, scale / base))
        self.center = (max(-80.0, min(80.0, (max(lats) + min(lats)) / 2)),
                       (max(lons) + min(lons)) / 2)
        self._background = None
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self._fit_pending = False
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        self._fit_pending = False
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """On a station: its report; elsewhere: every station in view again."""
        if self._heard_at(event.position()) is None:
            self.fit()
            return
        super().mouseDoubleClickEvent(event)

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
        members = self.groups.get(heard.call, [heard])
        parts = [", ".join(m.call for m in members)] if len(members) > 1 else [heard.call]
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
