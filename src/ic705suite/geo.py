"""Where a station is: Maidenhead locators, distance and bearing over the Earth, and the
DXCC entity of a callsign from AD1C's cty.dat (the copy WSJT-X installs: the suite does not
carry it).

cty.dat, an entity per record:

    Spain:   14:  37:  EU:   40.32:     3.43:    -1.0:  EA:
        AM,AN,AO,EA,EB,EC,ED,EE,EF,EG,EH,=EA7KLX(14);

name, CQ zone, ITU zone, continent, latitude, longitude (west positive), UTC offset, primary
prefix; then its prefixes and whole calls (=CALL), any of them with overrides: (CQ zone)
[ITU zone] <lat/lon> {continent} ~UTC offset~.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

EARTH_KM = 6371.0
LOCATOR_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?$")


# ---- Maidenhead ---------------------------------------------------------------------------------

def is_locator(text: str) -> bool:
    """A Maidenhead locator; not RR73, which in FT8 says "received, 73" (as WSJT-X)."""
    text = (text or "").upper()
    return bool(LOCATOR_RE.match(text)) and text != "RR73"


def locator_to_latlon(locator: str) -> tuple[float, float] | None:
    """The centre of a 4, 6 or 8 character locator (latitude, longitude east positive)."""
    loc = (locator or "").upper()
    if not is_locator(loc):
        return None
    lon = (ord(loc[0]) - 65) * 20 - 180
    lat = (ord(loc[1]) - 65) * 10 - 90
    lon += int(loc[2]) * 2
    lat += int(loc[3])
    size_lon, size_lat = 2.0, 1.0
    if len(loc) >= 6:
        lon += (ord(loc[4]) - 65) * 5 / 60
        lat += (ord(loc[5]) - 65) * 2.5 / 60
        size_lon, size_lat = 5 / 60, 2.5 / 60
    if len(loc) >= 8:
        lon += int(loc[6]) * 0.5 / 60
        lat += int(loc[7]) * 0.25 / 60
        size_lon, size_lat = 0.5 / 60, 0.25 / 60
    return lat + size_lat / 2, lon + size_lon / 2


def latlon_to_locator(lat: float, lon: float, chars: int = 6) -> str:
    lon = min(179.9999, max(-180.0, lon)) + 180
    lat = min(89.9999, max(-90.0, lat)) + 90
    out = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10))
    out += str(int(lon % 20 // 2)) + str(int(lat % 10))
    if chars >= 6:
        out += chr(65 + int(lon % 2 * 12)) + chr(65 + int(lat % 1 * 24))
    return out


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + \
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """The initial great-circle bearing from a to b, 0-360 (north 0, east 90)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return math.degrees(math.atan2(y, x)) % 360


def great_circle(a: tuple[float, float], b: tuple[float, float], steps: int = 64) \
        -> list[tuple[float, float]]:
    """Points along the short great circle from a to b (for a path on a map)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    d = 2 * math.asin(math.sqrt(math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1)
                                * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2))
    if d == 0:
        return [a, b]
    out = []
    for i in range(steps + 1):
        f = i / steps
        k1, k2 = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = k1 * math.cos(lat1) * math.cos(lon1) + k2 * math.cos(lat2) * math.cos(lon2)
        y = k1 * math.cos(lat1) * math.sin(lon1) + k2 * math.cos(lat2) * math.sin(lon2)
        z = k1 * math.sin(lat1) + k2 * math.sin(lat2)
        out.append((math.degrees(math.atan2(z, math.hypot(x, y))),
                    math.degrees(math.atan2(y, x))))
    return out


# ---- cty.dat ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Entity:
    name: str
    cq: int
    itu: int
    continent: str
    lat: float
    lon: float               # east positive (cty.dat has west positive)
    prefix: str               # the primary one (its DXCC "name" in short)


OVERRIDE_RE = re.compile(r"\((\d+)\)|\[(\d+)\]|<([-\d.]+)/([-\d.]+)>|\{(\w+)\}|~[-\d.]+~")


def _with_overrides(entity: Entity, text: str) -> Entity:
    changes: dict = {}
    for cq, itu, lat, lon, continent in OVERRIDE_RE.findall(text):
        if cq:
            changes["cq"] = int(cq)
        if itu:
            changes["itu"] = int(itu)
        if lat:
            changes["lat"], changes["lon"] = float(lat), -float(lon)
        if continent:
            changes["continent"] = continent
    return replace(entity, **changes) if changes else entity


class Cty:
    """Callsign -> entity, as loggers do: a whole call (=CALL) first, else the longest
    prefix the call starts with."""

    def __init__(self, text: str) -> None:
        self.calls: dict[str, Entity] = {}
        self.prefixes: dict[str, Entity] = {}
        for record in text.split(";"):
            record = record.strip()
            if not record:
                continue
            head, _, rest = record.partition("\n")
            fields = [f.strip() for f in head.split(":")]
            if len(fields) < 8:
                continue
            entity = Entity(fields[0], int(fields[1]), int(fields[2]), fields[3],
                            float(fields[4]), -float(fields[5]), fields[7].lstrip("*"))
            for item in re.split(r"[,\s]+", rest):
                if not item:
                    continue
                base = OVERRIDE_RE.sub("", item)
                target = _with_overrides(entity, item)
                if base.startswith("="):
                    self.calls[base[1:].upper()] = target
                else:
                    self.prefixes[base.upper()] = target
        self._longest = max((len(p) for p in self.prefixes), default=0)

    def lookup(self, call: str) -> Entity | None:
        call = (call or "").upper().strip("<>")
        if not call:
            return None
        if call in self.calls:
            return self.calls[call]
        for part in self._parts(call):
            if part in self.calls:
                return self.calls[part]
            for size in range(min(len(part), self._longest), 0, -1):
                found = self.prefixes.get(part[:size])
                if found is not None:
                    return found
        return None

    @staticmethod
    def _parts(call: str) -> list[str]:
        """The part that says the country: F/EA7KLX -> F; EA7KLX/P -> EA7KLX."""
        parts = [p for p in call.split("/") if p]
        if len(parts) == 1:
            return parts
        suffixes = {"P", "M", "MM", "AM", "QRP", "A", "B", "R", "LH", "J"}
        rest = [p for p in parts if p not in suffixes and not p.isdigit()]
        if not rest:
            return parts[:1]
        # The shorter part is the prefix of the country operated from (F/EA7KLX, EA7KLX/F).
        return sorted(rest, key=len)[:1] + sorted(rest, key=len)[1:]


def cty_paths() -> list[Path]:
    """Where WSJT-X (or Debian's hamradio-files) puts cty.dat."""
    paths = [Path("/usr/share/wsjtx/cty.dat"), Path("/usr/share/hamradio-files/cty.dat"),
             Path("/usr/local/share/wsjtx/cty.dat"),
             Path.home() / ".local/share/WSJT-X/cty.dat"]
    if sys.platform == "win32":
        import os

        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            paths.append(Path(base) / "wsjtx" / "bin" / "cty.dat")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.append(Path(local) / "WSJT-X" / "cty.dat")
    if sys.platform == "darwin":
        paths.append(Path("/Applications/wsjtx.app/Contents/Resources/cty.dat"))
    return paths


@lru_cache(maxsize=1)
def cty() -> Cty | None:
    """The newest cty.dat found here, read once; None without one."""
    found = [p for p in cty_paths() if p.is_file()]
    if not found:
        return None
    newest = max(found, key=lambda p: p.stat().st_mtime)
    try:
        return Cty(newest.read_text(encoding="latin-1"))
    except (OSError, ValueError):
        return None


def entity_for(call: str) -> Entity | None:
    table = cty()
    return table.lookup(call) if table is not None else None
