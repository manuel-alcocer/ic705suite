"""D-PRS: the radio's own beacon settings (menu items 1A 05 0281-0297, read and written over
CI-V) to a form and back, the symbols and SSIDs it offers, positions and weather reports
in words. Pure functions, for the core and both clients.
"""

from __future__ import annotations

from ic705suite.apps.dstar import civ
from ic705suite.i18n import N_, _

# GPS select (01 internal GPS, 02 manual), manual position, GPS TX mode (01 D-PRS), the
# symbol chosen (0-3) and the four symbols, SSID (index of SSID_CHOICES), comment
OWN_SETTINGS = ("0281", "0286", "0287", "0290", "0291", "0292", "0293", "0294", "0295",
                "0297")
SYMBOL_NAMES = {
    "/-": N_("House"), "\\-": N_("House (HF)"), "/>": N_("Car"), "/[": N_("Person"),
    "/b": N_("Bicycle"), "/k": N_("Pick-up"), "/v": N_("Van"), "/u": N_("Truck"),
    "/s": N_("Ship"), "/Y": N_("Sailboat"), "/r": N_("Antenna"), "/R": N_("Motorhome"),
    "/j": N_("Jeep"), "/<": N_("Motorbike"), "/'": N_("Small aircraft"), "/_": N_("Weather"),
}
SSID_CHOICES = ["---", "-0"] + [f"-{n}" for n in range(1, 16)] + \
    [f"-{chr(c)}" for c in range(65, 91)]
COMMENT_LENGTH = 43
KINDS = {"position": N_("position"), "object": N_("object"), "item": N_("item"),
         "weather": N_("weather")}


def symbol_name(code: str) -> str:
    return _(SYMBOL_NAMES[code]) if code in SYMBOL_NAMES else code


def format_position(lat: float, lon: float) -> str:
    def part(value: float, positive: str, negative: str, width: int) -> str:
        deg = int(abs(value))
        minutes = (abs(value) - deg) * 60
        return f"{deg:0{width}d}° {minutes:06.3f}' {positive if value >= 0 else negative}"
    return f"{part(lat, 'N', 'S', 2)}, {part(lon, 'E', 'W', 3)}"


def weather_text(w: dict) -> str:
    parts = []
    if w.get("temperature") is not None:
        parts.append(f"{w['temperature']:.1f} °C")
    if w.get("humidity") is not None:
        parts.append(f"{w['humidity']:.0f} %")
    if w.get("pressure"):
        parts.append(f"{w['pressure']:.1f} hPa")
    if w.get("wind") is not None:
        if w["wind"] == 0:
            parts.append(_("calm"))
        else:
            wind = f"{w['wind']:.1f} m/s"
            if w.get("wind_dir") is not None:
                wind += " " + _(civ.compass_point(w["wind_dir"]))
            parts.append(wind)
    if w.get("rain_24h"):
        parts.append(_("rain {mm} mm/24h").format(mm=f"{w['rain_24h']:.1f}"))
    return " · ".join(parts)


def decode_settings(values: dict[str, bytes]) -> dict:
    """The radio's items (number -> data) as the beacon form: send (on/off), source
    ("gps" or "manual"), lat, lon, alt (manual), symbol (0-3), symbols (the four codes),
    ssid (index), comment."""
    manual = values.get("0286", b"")
    symbols = [values.get(n, b"")[:2].decode("latin-1") for n in ("0291", "0292", "0293",
                                                                    "0294")]
    chosen = values.get("0290", b"\x00")
    ssid = values.get("0295", b"\x00")
    return {
        "send": values.get("0287", b"")[:1] == b"\x01",
        "source": "manual" if values.get("0281", b"")[:1] == b"\x02" else "gps",
        "lat": civ.decode_latitude(manual[:5]), "lon": civ.decode_longitude(manual[5:11]),
        "alt": civ.decode_altitude(manual[11:15]),
        "symbol": min(chosen[0], 3) if chosen else 0, "symbols": symbols,
        "ssid": min(ssid[0], len(SSID_CHOICES) - 1) if ssid else 0,
        "comment": values.get("0297", b"").decode("latin-1").rstrip(),
    }


def encode_settings(form: dict) -> dict[str, bytes]:
    """The beacon form as the items to write, in order (the GPS TX mode last: the beacon
    goes on only once the rest is in place). ValueError on a position out of range."""
    writes = {"0289": b"\x00"}                    # TX format: position (our own station)
    manual = form.get("source") == "manual"
    if manual:
        lat, lon = float(form["lat"]), float(form["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(_("Coordinates out of range"))
        alt = form.get("alt")
        writes["0286"] = civ.encode_position(lat, lon, None if alt in (None, "") else float(alt))
    writes["0281"] = b"\x02" if manual else b"\x01"
    writes["0290"] = bytes([max(0, min(3, int(form.get("symbol") or 0)))])
    writes["0295"] = bytes([max(0, min(len(SSID_CHOICES) - 1, int(form.get("ssid") or 0)))])
    writes["0296"] = b"\x00"
    writes["0297"] = str(form.get("comment") or "").ljust(COMMENT_LENGTH)[:COMMENT_LENGTH] \
        .encode("latin-1", "replace")
    writes["0287"] = b"\x01" if form.get("send") else b"\x00"
    return writes


def own_position(settings: dict, gps_fix: tuple | None) -> tuple[float, float] | None:
    """Our position: the radio's manual one, or its GPS fix."""
    if settings.get("source") == "manual":
        if settings.get("lat") is None or settings.get("lon") is None:
            return None
        return settings["lat"], settings["lon"]
    return gps_fix


def source_call(callsign: str, ssid_index: int) -> str:
    ssid = SSID_CHOICES[ssid_index] if 0 <= ssid_index < len(SSID_CHOICES) else "---"
    return callsign.upper() + ("" if ssid in ("---", "-0") else ssid)
