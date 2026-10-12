"""The FT8 map, as GridTracker draws it: the world (Natural Earth's land, public domain) with
the day and night (the grey line), Maidenhead's fields and squares, the squares worked and
confirmed (the logbook: worked.py), our station, and every station heard lately where it is
(its grid, or its country's middle when no grid came: a hollow dot), coloured as the band
activity (a CQ green, a call to us red, a new DXCC ringed magenta); the great circle to the
station we work. The wheel zooms, a drag moves it, a click on a station chooses it in the
list and a double click answers it.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from importlib import resources

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QToolTip, QWidget

from ic705suite import geo
from ic705suite.gui import theme

HEARD_FOR = 180.0            # seconds a station heard stays on the map
OCEAN = QColor(18, 30, 46)
LAND = QColor(46, 62, 52)
COAST = QColor(86, 110, 92)
NIGHT = QColor(0, 0, 0, 95)
GRID = QColor(255, 255, 255, 38)
GRID_LABEL = QColor(255, 255, 255, 70)
WORKED = QColor(240, 160, 40, 120)
CONFIRMED = QColor(70, 210, 120, 140)


@dataclass
class Heard:
    call: str
    lat: float
    lon: float
    approx: bool
    decode: dict
    start: float
    seen: float              # time.time() when it was heard


def _land() -> list[QPolygonF]:
    """Natural Earth's land, as polygons in degrees (x: longitude, y: latitude)."""
    data = json.loads(resources.files("ic705suite.gui").joinpath("data/world_land.json")
                      .read_text(encoding="utf-8"))
    scale = data["scale"]
    out = []
    for ring in data["rings"]:
        out.append(QPolygonF([QPointF(ring[i] / scale, ring[i + 1] / scale)
                              for i in range(0, len(ring), 2)]))
    return out


def subsolar(now: float | None = None) -> tuple[float, float]:
    """Where the sun is overhead now (latitude, longitude): the day's declination and the
    hour, with the equation of time."""
    t = time.gmtime(now if now is not None else time.time())
    day = t.tm_yday + (t.tm_hour + t.tm_min / 60) / 24
    g = 2 * math.pi / 365 * (day - 1)
    decl = math.degrees(0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
                        - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
                        - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))   # minutes
    minutes = t.tm_hour * 60 + t.tm_min + t.tm_sec / 60
    lon = -(minutes + eot - 720) / 4
    return decl, ((lon + 180) % 360) - 180


def night_polygon(now: float | None = None, step: float = 2.0) -> QPolygonF:
    """The night side, in degrees: the terminator from west to east, closed over the pole
    that is in darkness."""
    decl, sun_lon = subsolar(now)
    decl = decl if abs(decl) > 0.01 else 0.01
    points = []
    lon = -180.0
    while lon <= 180.0:
        lat = math.degrees(math.atan(-math.cos(math.radians(lon - sun_lon))
                                     / math.tan(math.radians(decl))))
        points.append(QPointF(lon, lat))
        lon += step
    dark_pole = -90.0 if decl > 0 else 90.0
    points += [QPointF(180.0, dark_pole), QPointF(-180.0, dark_pole)]
    return QPolygonF(points)


class MapView(QWidget):
    station_clicked = Signal(float, dict)
    station_double_clicked = Signal(float, dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(140)
        self.land = _land()
        self.heard: dict[str, Heard] = {}
        self.grids: dict[str, str] = {}          # grid4 -> worked | confirmed
        self.home: tuple[float, float] | None = None
        self.home_label = ""
        self.them = ""                           # the station we work: the path to it
        self.mycall = ""                         # a station calling it: red
        self.center = (25.0, 0.0)                # latitude, longitude at the middle
        self.zoom = 1.0                          # 1: the whole world in the width
        self._drag: QPointF | None = None
        self._spots: list[tuple[QRectF, Heard]] = []

    # ---- what it shows ------------------------------------------------------------------------

    def set_home(self, lat: float | None, lon: float | None, label: str = "") -> None:
        if lat is None or lon is None:
            return
        first = self.home is None
        self.home, self.home_label = (lat, lon), label
        if first:
            self.center = (25.0, lon)            # our side of the world in the middle
        self.update()

    def set_worked(self, grids: dict[str, str]) -> None:
        self.grids = dict(grids)
        self.update()

    def set_them(self, call: str) -> None:
        if call != self.them:
            self.them = call
            self.update()

    def add_decodes(self, start: float, decodes: list[dict]) -> None:
        now = time.time()
        for d in decodes:
            call = (d.get("sender") or "").strip("<>")
            if not call or d.get("lat") is None or d.get("kind") == "tx":
                continue
            known = self.heard.get(call)
            if known is not None and not known.approx and d.get("approx"):
                known.decode, known.start, known.seen = d, start, now   # keep its grid place
                continue
            self.heard[call] = Heard(call, d["lat"], d["lon"], bool(d.get("approx")), d,
                                     start, now)
        for call in [c for c, h in self.heard.items() if now - h.seen > HEARD_FOR]:
            del self.heard[call]
        self.update()

    # ---- the projection: equirectangular, zoomed and moved --------------------------------------

    def _scale(self) -> float:
        """Pixels per degree: zoom 1 shows the whole width of the world, or 110 degrees of
        latitude when the map is low (the poles cut, as GridTracker)."""
        return min(self.width() / 360.0, self.height() / 110.0) * self.zoom

    def _xy(self, lat: float, lon: float) -> QPointF:
        s = self._scale()
        lon = (lon - self.center[1] + 540) % 360 - 180     # the shortest way round
        return QPointF(self.width() / 2 + lon * s, self.height() / 2 - (lat - self.center[0]) * s)

    def _latlon(self, point: QPointF) -> tuple[float, float]:
        s = self._scale()
        lon = self.center[1] + (point.x() - self.width() / 2) / s
        lat = self.center[0] - (point.y() - self.height() / 2) / s
        return lat, ((lon + 180) % 360) - 180

    def _transform(self, painter: QPainter, shift: float = 0.0) -> None:
        """Degrees to pixels, for the land and the night (drawn three times: the seams)."""
        s = self._scale()
        painter.translate(self.width() / 2 + (shift - self.center[1]) * s,
                          self.height() / 2 + self.center[0] * s)
        painter.scale(s, -s)

    # ---- drawing --------------------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), OCEAN)
        for shift in (-360.0, 0.0, 360.0):
            painter.save()
            self._transform(painter, shift)
            pen = QPen(COAST, 0)                    # a cosmetic pen: one pixel at any zoom
            painter.setPen(pen)
            painter.setBrush(QBrush(LAND))
            for ring in self.land:
                painter.drawPolygon(ring)
            self._draw_worked(painter)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(NIGHT))
            painter.drawPolygon(night_polygon())
            painter.restore()
        self._draw_grid(painter)
        self._draw_path(painter)
        self._draw_home(painter)
        self._draw_heard(painter)
        painter.end()

    def _draw_worked(self, painter: QPainter) -> None:
        for grid, state in self.grids.items():
            centre = geo.locator_to_latlon(grid[:4])
            if centre is None:
                continue
            colour = CONFIRMED if state == "confirmed" else WORKED
            edge = QColor(colour)
            edge.setAlpha(230)
            painter.setPen(QPen(edge, 0))
            painter.setBrush(QBrush(colour))
            painter.drawRect(QRectF(centre[1] - 1, centre[0] - 0.5, 2, 1))

    def _x(self, lon: float) -> float:
        """The x of a longitude as it is (not brought round): lines repeat with the world."""
        return self.width() / 2 + (lon - self.center[1]) * self._scale()

    def _draw_grid(self, painter: QPainter) -> None:
        """Fields (20 x 10 degrees) always; squares (2 x 1) when zoomed in enough; their
        names where there is room for them."""
        s = self._scale()
        squares = s * 2 >= 14
        font = QFont(painter.font())
        font.setPointSizeF(7.5)
        painter.setFont(font)
        step_lon, step_lat = (2, 1) if squares else (20, 10)
        top_left = self._latlon(QPointF(0, 0))
        bottom_right = self._latlon(QPointF(self.width(), self.height()))
        lat0 = max(-90, math.floor(bottom_right[0] / step_lat) * step_lat)
        lat1 = min(90, math.ceil(top_left[0] / step_lat) * step_lat)
        span = self.width() / s
        lon0 = math.floor((self.center[1] - span / 2) / step_lon) * step_lon
        lon1 = math.ceil((self.center[1] + span / 2) / step_lon) * step_lon
        lon = lon0
        while lon <= lon1:
            x = self._x(lon)
            painter.setPen(QPen(GRID, 1.5 if lon % 20 == 0 else 1))
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            lon += step_lon
        lat = lat0
        while lat <= lat1:
            y = self._xy(lat, self.center[1]).y()
            painter.setPen(QPen(GRID, 1.5 if lat % 10 == 0 else 1))
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
            lat += step_lat
        if step_lon * s < (48 if squares else 22) or step_lat * s < (22 if squares else 12):
            return                                       # no room for the names
        painter.setPen(GRID_LABEL)
        lat = lat0
        while lat < lat1:
            lon = lon0
            while lon < lon1:
                text = geo.latlon_to_locator(lat + 0.01, ((lon + 180) % 360) - 180 + 0.01, 4)
                y = self._xy(lat + step_lat, self.center[1]).y()
                painter.drawText(QPointF(self._x(lon) + 3, y + 11),
                                 text if squares else text[:2])
                lon += step_lon
            lat += step_lat

    def _draw_home(self, painter: QPainter) -> None:
        if self.home is None:
            return
        p = self._xy(*self.home)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QBrush(QColor(theme.YELLOW or "#ffc94d")))
        painter.drawRect(QRectF(p.x() - 5, p.y() - 5, 10, 10))
        painter.drawText(p + QPointF(8, -6), self.home_label)

    def _draw_path(self, painter: QPainter) -> None:
        them = self.heard.get(self.them)
        if self.home is None or them is None:
            return
        points = geo.great_circle(self.home, (them.lat, them.lon))
        pen = QPen(QColor(theme.RED or "#ff5c5c"), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        path = QPainterPath()
        previous = None
        for lat, lon in points:
            p = self._xy(lat, lon)
            if previous is None or abs(p.x() - previous.x()) > self.width() / 2:
                path.moveTo(p)                    # across the map's edge: a new piece
            else:
                path.lineTo(p)
            previous = p
        painter.drawPath(path)

    def _draw_heard(self, painter: QPainter) -> None:
        now = time.time()
        font = QFont(painter.font())
        font.setPointSizeF(8)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        self._spots = []
        for heard in sorted(self.heard.values(), key=lambda h: h.seen):
            d = heard.decode
            p = self._xy(heard.lat, heard.lon)
            fade = max(0.35, 1 - (now - heard.seen) / HEARD_FOR)
            colour = QColor(theme.GREEN or "#43d17a") if d.get("kind") == "cq" else \
                QColor(235, 235, 235)
            if self.mycall and d.get("to") and \
                    d["to"].split("/")[0] == self.mycall.split("/")[0]:
                colour = QColor(theme.RED or "#ff5c5c")
            colour.setAlphaF(fade)
            radius = 4.5
            if d.get("dxcc") in ("new", "new_band"):
                ring = QColor(theme.MAGENTA or "#e070ff")
                ring.setAlphaF(fade)
                painter.setPen(QPen(ring, 2.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(p, radius + 4, radius + 4)
            painter.setPen(QPen(colour, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush if heard.approx else QBrush(colour))
            painter.drawEllipse(p, radius, radius)
            show_label = self.zoom >= 2 or d.get("kind") == "cq" or heard.call == self.them \
                or d.get("dxcc") in ("new", "new_band")
            rect = QRectF(p.x() - radius - 2, p.y() - radius - 2, radius * 2 + 4, radius * 2 + 4)
            if show_label:
                painter.setPen(colour)
                label = QRectF(p.x() + 7, p.y() - metrics.ascent() / 2 - 2,
                               metrics.horizontalAdvance(heard.call) + 2, metrics.height())
                painter.drawText(label, Qt.AlignmentFlag.AlignLeft, heard.call)
                rect = rect.united(label)
            self._spots.append((rect, heard))

    # ---- the mouse ------------------------------------------------------------------------------

    def _heard_at(self, point: QPointF) -> Heard | None:
        for rect, heard in reversed(self._spots):
            if rect.contains(point):
                return heard
        return None

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        before = self._latlon(event.position())
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.zoom = min(40.0, max(1.0, self.zoom * factor))
        after = self._latlon(event.position())
        self.center = (max(-80.0, min(80.0, self.center[0] + before[0] - after[0])),
                       ((self.center[1] + before[1] - after[1] + 180) % 360) - 180)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        heard = self._heard_at(event.position())
        if heard is not None:
            self.station_clicked.emit(heard.start, heard.decode)
        else:
            self._drag = event.position()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:  # noqa: N802
        self._drag = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        heard = self._heard_at(event.position())
        if heard is not None:
            self.station_double_clicked.emit(heard.start, heard.decode)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is not None:
            s = self._scale()
            delta = event.position() - self._drag
            self._drag = event.position()
            self.center = (max(-80.0, min(80.0, self.center[0] + delta.y() / s)),
                           ((self.center[1] - delta.x() / s + 180) % 360) - 180)
            self.update()
            return
        heard = self._heard_at(event.position())
        if heard is None:
            lat, lon = self._latlon(event.position())
            text = geo.latlon_to_locator(lat, lon, 6)
            if self.home is not None:
                text += f" · {round(geo.distance_km(self.home, (lat, lon)))} km " \
                        f"{round(geo.bearing_deg(self.home, (lat, lon)))}°"
        else:
            d = heard.decode
            text = f"{d.get('message', '')}\n{d.get('country', '')}"
            if d.get("km") is not None:
                text += f" · {'~' if heard.approx else ''}{d['km']} km {d.get('az')}°"
            text += f" · {d.get('snr')} dB"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.center = (25.0, self.home[1] if self.home else 0.0)
        self.update()
