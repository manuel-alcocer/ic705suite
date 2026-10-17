"""The FT8 map, as GridTracker draws it: the world (Natural Earth's land, public domain) with
the day and night (the grey line), Maidenhead's fields and squares, the squares worked and
confirmed (the logbook: worked.py), our station, and every station heard lately where it is
(its grid, or its country's middle when no grid came: a hollow dot), coloured as the band
activity (a CQ green, a call to us red, a new DXCC ringed magenta); the great circle to the
station we call, and from each station calling us, with arrows running the way the call
goes; under them, quiet, the calls between other stations (traffic, a switch). A key in
the bottom right corner says what each mark is, and what is under the mouse (no tooltips:
the help is in the documentation). The wheel zooms, a drag moves it, a click on a station
chooses it in the list and a double click answers it.

The still layers (land, night, grid, the squares worked) are drawn once into a picture and
kept until the view changes, so the arrows move without redrawing the world each frame.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from importlib import resources

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from ic705suite import geo
from ic705suite.gui import theme
from ic705suite.i18n import _

HEARD_FOR = 180.0            # seconds a station heard stays on the map
CALLING_FOR = 35.0           # seconds a call to us keeps its arrows (two cycles and a bit)
ARROW_EVERY = 34.0           # pixels between arrows along a path
ARROW_SPEED = 40.0           # pixels a second the arrows run
FRAME_MS = 50                # the arrows' frames (20 a second), only while there are some
OCEAN = QColor(18, 30, 46)
LAND = QColor(46, 62, 52)
COAST = QColor(86, 110, 92)
NIGHT = QColor(0, 0, 0, 95)
GRID = QColor(255, 255, 255, 38)
GRID_LABEL = QColor(255, 255, 255, 70)
TRAFFIC = QColor(170, 190, 215, 105)  # calls between other stations: there, not in the way
TRAFFIC_DASH = (4.0, 4.0)    # their dashes and gaps, in pen widths, running the call's way
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
    station_menu = Signal(float, dict, QPoint)        # right-clicked: its menu, there

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
        self._background: QPixmap | None = None   # the still layers, as last drawn
        self._background_key: tuple = ()
        self._under = ""                          # what is under the mouse, for the key
        self._done: dict[str, float] = {}         # call -> when its QSO was completed
        self.traffic = True                       # the calls between other stations
        self.grid_on = True                       # Maidenhead's fields and squares
        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self.update)

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
        self._background = None
        self.update()

    def set_them(self, call: str) -> None:
        if call != self.them:
            self.them = call
            self._animate()
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
        self._animate()
        self.update()

    # ---- the arrows ---------------------------------------------------------------------------

    def qso_done(self, call: str) -> None:
        """A QSO completed: its lines go (until that station calls again)."""
        call = call.strip("<>")
        self._done[call] = time.time()
        if call == self.them:
            self.them = ""
        self._animate()
        self.update()

    def paths(self, now: float | None = None) -> list[tuple[str, bool]]:
        """(call, outward) of each path drawn: ours to the station we call (outward), and
        each station that called us lately (towards us); not the last words of a QSO (RR73,
        RRR, 73), nor a station whose QSO was just completed."""
        now = time.time() if now is None else now
        out = [(self.them, True)] if self.them in self.heard and self.home \
            and not self._ended(self.heard[self.them]) else []
        if self.home is None or not self.mycall:
            return out
        mine = self.mycall.split("/")[0]
        for heard in self.heard.values():
            d = heard.decode
            to = (d.get("to") or "").split("/")[0]
            if to == mine and now - heard.seen <= CALLING_FOR and heard.call != self.them \
                    and not d.get("end") and not self._ended(heard):
                out.append((heard.call, False))
        return out

    def set_grid(self, on: bool) -> None:
        """Maidenhead's lines and names, or a cleaner map without them (the squares worked
        stay)."""
        self.grid_on = on
        self._background = None
        self.update()

    def set_traffic(self, on: bool) -> None:
        self.traffic = on
        self._animate()
        self.update()

    def traffic_paths(self, now: float | None = None) -> list[tuple[tuple, tuple, str]]:
        """(from, to, sender) of each call between other stations heard lately: from the
        sender to the station it calls, where that one was heard, else its country's
        middle; not ours (they have paths of their own) nor those to us."""
        now = time.time() if now is None else now
        mine = self.mycall.split("/")[0] if self.mycall else ""
        out = []
        for heard in self.heard.values():
            d = heard.decode
            to = (d.get("to") or "").strip("<>")
            if d.get("kind") != "call" or not to or now - heard.seen > CALLING_FOR:
                continue
            if mine and mine in (to.split("/")[0], heard.call.split("/")[0]):
                continue
            if heard.call == self.them:
                continue
            target = self.heard.get(to)
            if target is not None:
                where = (target.lat, target.lon)
            else:
                entity = geo.entity_for(to)
                if entity is None:
                    continue
                where = (entity.lat, entity.lon)
            out.append(((heard.lat, heard.lon), where, heard.call))
        return out

    def _ended(self, heard: Heard) -> bool:
        done = self._done.get(heard.call)
        return done is not None and heard.seen <= done

    def moving(self, now: float | None = None) -> bool:
        """Something to animate: our paths' arrows, the others' calls' dashes."""
        return bool(self.paths(now)) or (self.traffic and bool(self.traffic_paths(now)))

    def _animate(self) -> None:
        """Frames only while there is something moving and the map is shown."""
        wanted = self.moving() and self.isVisible()
        if wanted and not self._frames.isActive():
            self._frames.start()
        elif not wanted and self._frames.isActive():
            self._frames.stop()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self._animate()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().hideEvent(event)
        self._frames.stop()

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
        painter.drawPixmap(0, 0, self._still())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = time.time()
        if self.traffic:
            self._draw_traffic(painter, now)
        for call, outward in self.paths(now):
            self._draw_path(painter, call, outward, now)
        self._draw_home(painter)
        self._draw_heard(painter)
        self._draw_key(painter)
        painter.end()
        if not self.moving(now) and self._frames.isActive():
            self._frames.stop()                  # the calls went by

    def _still(self) -> QPixmap:
        """The land, the squares worked, the night and the grid: drawn again only when the
        view, the squares or the minute (the grey line) change."""
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), ratio, self.zoom, self.center, self.grid_on,
               int(time.time() // 60))
        if self._background is not None and key == self._background_key:
            return self._background
        picture = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        picture.setDevicePixelRatio(ratio)
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(QRectF(0, 0, self.width(), self.height()), OCEAN)
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
        if self.grid_on:
            self._draw_grid(painter)
        painter.end()
        self._background, self._background_key = picture, key
        return picture

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

    def _pieces(self, points: list[tuple[float, float]]) -> list[list[QPointF]]:
        """A path on the screen, cut where it crosses the map's edge."""
        pieces: list[list[QPointF]] = []
        previous = None
        for lat, lon in points:
            p = self._xy(lat, lon)
            if previous is None or abs(p.x() - previous.x()) > self.width() / 2:
                pieces.append([p])
            else:
                pieces[-1].append(p)
            previous = p
        return pieces

    def _draw_traffic(self, painter: QPainter, now: float) -> None:
        """The calls between other stations: thin, faint, dashes running from the caller
        to the station called, an arrow halfway."""
        pen = QPen(TRAFFIC, 1.2, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern(list(TRAFFIC_DASH))
        period = sum(TRAFFIC_DASH)
        # The offset in pen widths; lower values move the dashes along the path.
        pen.setDashOffset(period - (now * ARROW_SPEED / pen.widthF()) % period)
        for start, end, _call in self.traffic_paths(now):
            pieces = self._pieces(geo.great_circle(start, end, steps=32))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for piece in pieces:
                path = QPainterPath(piece[0])
                for p in piece[1:]:
                    path.lineTo(p)
                painter.drawPath(path)
            longest = max(pieces, key=len)
            if len(longest) >= 2:
                middle = len(longest) // 2
                a, b = longest[max(0, middle - 1)], longest[middle]
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(TRAFFIC))
                self._arrow(painter, b, math.atan2(b.y() - a.y(), b.x() - a.x()), size=5.0)

    def _draw_path(self, painter: QPainter, call: str, outward: bool, now: float) -> None:
        """The great circle between us and a station, and arrows running along it: from us
        when we call it (outward), towards us when it calls us."""
        them = self.heard.get(call)
        if self.home is None or them is None:
            return
        points = geo.great_circle(self.home, (them.lat, them.lon))
        if not outward:
            points = points[::-1]                 # from them to us: the arrows' way
        colour = QColor(theme.RED or "#ff5c5c")
        pieces = self._pieces(points)
        faint = QColor(colour)
        faint.setAlpha(150)
        painter.setPen(QPen(faint, 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for piece in pieces:
            path = QPainterPath(piece[0])
            for p in piece[1:]:
                path.lineTo(p)
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(colour))
        travelled = 0.0
        phase = (now * ARROW_SPEED) % ARROW_EVERY
        for piece in pieces:
            for a, b in zip(piece, piece[1:], strict=False):
                length = math.hypot(b.x() - a.x(), b.y() - a.y())
                if length <= 0:
                    continue
                # Arrows at phase, phase + ARROW_EVERY... along the whole path.
                first = math.ceil((travelled - phase) / ARROW_EVERY)
                at = phase + first * ARROW_EVERY - travelled
                while at <= length:
                    f = at / length
                    self._arrow(painter, QPointF(a.x() + (b.x() - a.x()) * f,
                                                 a.y() + (b.y() - a.y()) * f),
                                math.atan2(b.y() - a.y(), b.x() - a.x()))
                    at += ARROW_EVERY
                travelled += length

    @staticmethod
    def _arrow(painter: QPainter, at: QPointF, angle: float, size: float = 7.5) -> None:
        cos, sin = math.cos(angle), math.sin(angle)

        def point(x: float, y: float) -> QPointF:
            return QPointF(at.x() + x * cos - y * sin, at.y() + x * sin + y * cos)

        painter.drawPolygon(QPolygonF([point(size, 0), point(-size * 0.7, size * 0.6),
                                       point(-size * 0.35, 0), point(-size * 0.7, -size * 0.6)]))

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

    def _draw_key(self, painter: QPainter) -> None:
        """What each mark is, in the bottom right corner, and what is under the mouse."""
        font = QFont(painter.font())
        font.setPointSizeF(8)
        font.setBold(False)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        red, green = QColor(theme.RED or "#ff5c5c"), QColor(theme.GREEN or "#43d17a")
        rows = [("dot", green, _("calling CQ")), ("dot", red, _("calling you")),
                ("ring", QColor(theme.MAGENTA or "#e070ff"), _("new DXCC or band")),
                ("hollow", QColor(235, 235, 235), _("placed by its country")),
                ("square", WORKED, _("square worked")), ("square", CONFIRMED, _("confirmed")),
                ("home", QColor(theme.YELLOW or "#ffc94d"), _("your station")),
                ("arrow", red, _("the way a call goes"))]
        if self.traffic:
            rows.append(("arrow", TRAFFIC, _("calls between others")))
        line = metrics.height() + 2
        width = max(metrics.horizontalAdvance(text) for _kind, _c, text in rows) + 30
        under = self._under
        if under:
            width = max(width, metrics.horizontalAdvance(under) + 16)
        height = line * (len(rows) + (1 if under else 0)) + 10
        box = QRectF(self.width() - width - 8, self.height() - height - 8, width, height)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        painter.setBrush(QBrush(QColor(10, 16, 26, 200)))
        painter.drawRoundedRect(box, 5, 5)
        y = box.top() + 5
        for kind, colour, text in rows:
            mark = QPointF(box.left() + 13, y + line / 2)
            painter.setPen(QPen(colour, 1.5))
            painter.setBrush(QBrush(colour) if kind in ("dot", "square", "home") else
                             Qt.BrushStyle.NoBrush)
            if kind in ("dot", "hollow"):
                painter.drawEllipse(mark, 4, 4)
            elif kind == "ring":
                painter.setPen(QPen(colour, 2))
                painter.drawEllipse(mark, 6, 6)
            elif kind in ("square", "home"):
                painter.drawRect(QRectF(mark.x() - 5, mark.y() - 4, 10, 8))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(colour))
                self._arrow(painter, mark, 0.0)
            painter.setPen(QColor(220, 225, 232))
            painter.drawText(QRectF(box.left() + 26, y, width - 28, line),
                             Qt.AlignmentFlag.AlignVCenter, text)
            y += line
        if under:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(QRectF(box.left() + 8, y, width - 12, line),
                             Qt.AlignmentFlag.AlignVCenter, under)

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

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt's name
        heard = self._heard_at(QPointF(event.pos()))
        if heard is not None:
            self.station_menu.emit(heard.start, heard.decode, event.globalPos())

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
        self._under = self.under(event.position())
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().leaveEvent(event)
        self._under = ""
        self.update()

    def under(self, point: QPointF) -> str:
        """What is under the mouse, for the key's last line: a station heard, else the
        locator there and how far and which way from us."""
        heard = self._heard_at(point)
        if heard is None:
            lat, lon = self._latlon(point)
            text = geo.latlon_to_locator(lat, lon, 6)
            if self.home is not None:
                text += f" · {round(geo.distance_km(self.home, (lat, lon)))} km " \
                        f"{round(geo.bearing_deg(self.home, (lat, lon)))}°"
            return text
        d = heard.decode
        parts = [heard.call]
        if d.get("country"):
            parts.append(_(d["country"]))
        if d.get("km") is not None:
            parts.append(f"{'~' if heard.approx else ''}{d['km']} km {d.get('az')}°")
        if d.get("snr") is not None:
            parts.append(f"{d['snr']} dB")
        return " · ".join(parts)

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.center = (25.0, self.home[1] if self.home else 0.0)
        self.update()
