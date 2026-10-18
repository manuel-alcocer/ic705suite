"""D-STAR (F4): the radio in DV followed by the core (apps.dstar.service), as the suite's
other applications show things: the rig's status line with D-STAR's own fields, the
reflector in use (INT Terminal Mode: the TO written to the radio), the station on air (or
the last one heard) with its name, place, message and distance, and the history of the
overs. The keys are the same in the terminal and in the window (gui.dstar):

  [ ]  choose another reflector   C  change to it (writes the TO; a short PTT registers)
  N    the MY note (/xxxx)        M  the TX message
  H    hide our own overs         Enter  an over of the history, in the on-air box
  V    the history, the D-PRS reports heard, the weather stations
  B    our D-PRS beacon (the radio's settings: position, symbol, SSID, comment)
  R    the reflectors' status (online, nodes, our terminal linked) checked again now
  Esc  back to live, or leave

The functions here (what each part shows) serve both clients; the window draws the same
with pixels.
"""

from __future__ import annotations

import re
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from ic705suite import geo
from ic705suite.apps.dstar import dprs
from ic705suite.apps.dstar.civ import compass_point
from ic705suite.i18n import N_, _, render
from ic705suite.station.links import Link
from ic705suite.ui.rigline import RigStatusLine
from ic705suite.ui.widgets import NameScreen, clock_footer, css

HINT = N_("[ ] Reflector · C Change · N MY note · M TX message · V View · B D-PRS beacon · "
          "R Status · H Hide mine · Enter Show over · Esc Live / Back")
SHORT_HINT = N_("[ ] Reflector · C Change · Esc Back")
INDICATORS = (("civ", "CI-V"), ("dv", "DV"), ("int", "INT"), ("net", "NET"), ("rx", "RX"),
              ("tx", "TX"))
VIEWS = (("history", N_("History")), ("dprs", N_("D-PRS")), ("weather", N_("Weather")))


# ---- what each part shows (both clients) -------------------------------------------------

def indicators(state: dict) -> list[tuple[str, str]]:
    """(name, "on" | "off" | "bad") of D-STAR's lamps: CI-V answering, the radio in DV, in
    INT Terminal Mode, receiving, transmitting."""
    on_dv = state.get("mode") == "DV"
    lamps = {
        "civ": "on" if state.get("civ") else "bad" if state.get("on") else "off",
        "dv": "on" if on_dv else "bad" if state.get("mode") else "off",
        "int": "on" if state.get("radio_mode") == "int" else "off",
        "net": {True: "on", False: "bad"}.get(state.get("internet"), "off"),
        "rx": "on" if state.get("rx_active") else "off",
        "tx": "bad" if state.get("transmitting") else "off",
    }
    return [(name, lamps[key]) for key, name in INDICATORS]


def warning(state: dict) -> str:
    """What is wrong for D-STAR, or ""."""
    if not state.get("on"):
        return ""
    if state.get("mode") and state.get("mode") != "DV":
        return _("the radio is in {mode}, not DV").format(mode=state["mode"])
    if state.get("my_call") and state.get("radio_mode") != "int":
        return _("the radio is not in INT Terminal Mode (R1 {r1})").format(
            r1=state.get("r1") or _("empty"))
    return ""


def reflector_info(state: dict) -> dict:
    """The reflector in use: its entry (or just the TO), the server the radio should use,
    and whether it is reached through another one."""
    reflector = dict(state.get("reflector") or {"to": state.get("ur", "")})
    server = state.get("server") or ""
    needed = reflector.get("server") or ""
    reflector["mismatch"] = bool(needed and server and needed.lower() != server.lower())
    reflector["radio_server"] = server
    return reflector


def reflector_status(state: dict) -> list[tuple[str, str]]:
    """The reflector in use as its pages tell it: (text, "good" | "bad" | "warn" | "dim" |
    "text") pieces: online or down, the nodes on our module, its name, our terminal linked
    or not, the country."""
    found = state.get("status")
    if not found:
        return [("● " + _("NO DATA"), "dim")] if state.get("ur", "").startswith("/") else []
    online = found.get("online")
    pieces = [("● " + _("ONLINE"), "good") if online else
              ("● " + _("NO DATA"), "dim") if online is None else ("● " + _("DOWN"), "bad")]
    if found.get("users") is not None:
        pieces.append((_("{n} nodes").format(n=found["users"]), "text"))
    if found.get("module_name"):
        pieces.append((found["module_name"], "dim"))
    if found.get("linked") is True:
        pieces.append((_("linked"), "good"))
    elif found.get("linked") is False:
        pieces.append((_("not linked"), "warn"))
    if found.get("country"):
        pieces.append((found["country"], "dim"))
    return pieces


def short_time(text: str) -> str:
    """A reflector's time ("2026-10-18 10:21:33", "18.10.2026 10:21") as HH:MM."""
    match = re.search(r"(\d{1,2}:\d{2})", text or "")
    return match.group(1) if match else (text or "")


def heard_list(state: dict, rows: list[dict], count: int = 3) -> tuple[str, list]:
    """The last heard: the reflector's own list when its page gives one, else ours on it
    (title, [(call sign, name, HH:MM)])."""
    reflector = (state.get("status") or {}).get("heard") or []
    if reflector:
        rows, seen = [], set()
        for h in reflector:
            call = h.get("callsign", "").strip()
            if call and call not in seen and len(rows) < count:
                seen.add(call)
                rows.append((call, h.get("name", "").strip(), short_time(h.get("time", ""))))
        return _("Last heard on the reflector"), rows
    return _("Last heard here"), heard_here(rows, state.get("ur", ""), count)


def ago(ts: float, now: float | None = None) -> str:
    secs = int((now or time.time()) - ts)
    if secs < 60:
        return _("{n}s ago").format(n=secs)
    if secs < 3600:
        return _("{n} min ago").format(n=secs // 60)
    if secs < 86400:
        return _("{n} h ago").format(n=secs // 3600)
    return _("{n} d ago").format(n=secs // 86400)


def clock(since: float | None, now: float | None = None) -> str:
    if not since:
        return ""
    secs = int((now or time.time()) - since)
    return f"{secs // 60:02d}:{secs % 60:02d}"


def where(over: dict, home=None) -> str:
    """How far and which way from us ("612 km NE 47°"; home: our (lat, lon), or a locator),
    or ""."""
    if over.get("distance") is not None:
        bearing = over.get("bearing") or 0
        return f"{over['distance']:.0f} km {_(compass_point(bearing))} {bearing:.0f}°"
    lat, lon = over.get("lat"), over.get("lon")
    if lat is None or lon is None:
        return ""
    if isinstance(home, str):
        home = geo.locator_to_latlon(home) if home else None
    if not home:
        return (f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} "
                f"{abs(lon):.2f}°{'E' if lon >= 0 else 'W'}")
    bearing = geo.bearing_deg(home, (lat, lon))
    return f"{geo.distance_km(home, (lat, lon)):.0f} km {_(compass_point(bearing))} {bearing:.0f}°"


def on_air(state: dict, review: dict | None = None, now: float | None = None) -> dict:
    """The on-air box: title ("RX", "TX", "LAST", "HISTORY" or ""), the call sign and the
    lines under it, and the time (elapsed, or how long ago)."""
    now = now or time.time()
    if review is not None:
        when = time.strftime("%d/%m %H:%M:%S", time.localtime(review["started"]))
        if review.get("ended"):
            secs = int(review["ended"] - review["started"])
            when += f" · {secs // 60}:{secs % 60:02d}" if secs >= 60 else f" · {secs}s"
        over, title, time_text = review, "HISTORY", when
        tx = review.get("direction") == "TX"
    elif state.get("transmitting"):
        my = state.get("my_call") or state.get("callsign") or ""
        return {"title": "TX", "call": my, "lines": [f"→ {state.get('ur', '')}"],
                "time": clock(state.get("tx_since"), now), "tx": True}
    elif state.get("rx"):
        over = state["rx"]
        live = over.get("live")
        title = "RX" if live else "LAST"
        time_text = clock(over.get("started"), now) if live else \
            ago(over["ended"], now) if over.get("ended") else ""
        tx = False
    else:
        return {"title": "", "call": "", "lines": [], "time": ""}
    call = over.get("callsign", "") + (f" /{over['suffix']}" if over.get("suffix") else "")
    lines = [text for text in (over.get("name"), over.get("location"),
                               where(over, home_of(state))) if text]
    if over.get("message"):
        lines.append(f"« {over['message']} »")
    return {"title": title, "call": call, "lines": lines, "time": time_text, "tx": tx,
            "live": title == "RX"}


def heard_here(rows: list[dict], to: str, count: int = 3) -> list[tuple[str, str, str]]:
    """The last overs on this reflector, from the history: (call sign, name, HH:MM)."""
    return [(r["callsign"], r.get("name") or "",
             time.strftime("%H:%M", time.localtime(r["started"])))
            for r in rows if to and r.get("reflector") == to][:count]


def distance_to(home):
    """A function giving a report's distance from home (km), None when unknown."""
    if isinstance(home, str):
        home = geo.locator_to_latlon(home) if home else None

    def km(row: dict) -> float | None:
        if not home or row.get("lat") is None or row.get("lon") is None:
            return None
        return geo.distance_km(home, (row["lat"], row["lon"]))
    return km


def home_of(state: dict):
    """Where we are for distances: the core's (the radio's position), else the locator."""
    return tuple(state["home"]) if state.get("home") else state.get("locator", "")


def history_cells(row: dict, locator="") -> list[str]:
    started = time.strftime("%d/%m %H:%M", time.localtime(row["started"]))
    if row.get("ended"):
        secs = int(row["ended"] - row["started"])
        started += f" ({secs // 60}:{secs % 60:02d})" if secs >= 60 else f" ({secs}s)"
    call = row["callsign"] + (f" /{row['suffix']}" if row.get("suffix") else "")
    return [started, call, row.get("name") or "", where(row, locator),
            row.get("reflector") or "", row.get("location") or "", row.get("message") or ""]


def history_columns() -> list[str]:
    return [_("Time"), _("Call sign"), _("Name"), _("Dist."), _("Reflector"), _("Location"),
            _("Message")]


def dprs_columns() -> list[str]:
    return [_("Time"), _("Station"), _("Type"), _("Dist."), _("Details"), _("Via"),
            _("Reflector")]


def dprs_cells(row: dict, home="") -> list[str]:
    import json

    details = dprs.symbol_name(row.get("symbol") or "")
    if row.get("kind") == "weather" and row.get("weather"):
        details = dprs.weather_text(json.loads(row["weather"]))
    return [time.strftime("%d/%m %H:%M", time.localtime(row["received"])), row["name"],
            _(dprs.KINDS.get(row["kind"], row["kind"])), where(row, home), details,
            row.get("via") or "", row.get("reflector") or ""]


MAP_DAYS = 7          # the map shows where each station was last reported this lately


def map_stations(rows: list[dict], now: float | None = None,
                 days: float = MAP_DAYS) -> list[dict]:
    """The stations that gave their position (D-PRS reports, newest first as the core
    sends them): the latest of each, of the last `days` days, oldest first (the newest
    drawn on top)."""
    now = time.time() if now is None else now
    latest: dict[str, dict] = {}
    for row in rows:
        if row.get("lat") is None or row.get("lon") is None or not row.get("name"):
            continue
        if now - row["received"] > days * 86400:
            continue
        known = latest.get(row["name"])
        if known is None or row["received"] > known["received"]:
            latest[row["name"]] = row
    return sorted(latest.values(), key=lambda r: r["received"])


def base_call(call: str) -> str:
    """A call sign without its SSID or suffix ("EA7ABC-9", "EA7ABC /705": EA7ABC)."""
    return (call or "").split("-")[0].split("/")[0].split(" ")[0].strip().upper()


def weather_columns() -> list[str]:
    return [_("Station"), _("Weather"), _("Dist."), _("Via"), _("When")]


def weather_cells(row: dict, home="") -> list[str]:
    import json

    report = json.loads(row["weather"]) if row.get("weather") else {}
    return [row["name"], dprs.weather_text(report), where(row, home), row.get("via") or "",
            ago(row["received"])]


def extra(state: dict) -> list[tuple[str, str]]:
    """D-STAR's fields in the rig's status line."""
    pieces = []
    if state.get("ur"):
        pieces.append(("TO", state["ur"]))
    if state.get("my_call"):
        pieces.append(("MY", state["my_call"] + (f" /{state['my_note']}"
                                                  if state.get("my_note") else "")))
    if state.get("radio_mode") == "int":
        pieces.append(("", "INT"))
    if state.get("dprs"):
        pieces.append(("D-PRS", "ON" if state["dprs"]["send"] else "OFF"))
    return pieces


class BeaconScreen(ModalScreen[dict | None]):
    """Our D-PRS beacon, as the radio has it: sent with every over or not, the position
    (the radio's GPS, or one typed: degrees, or a locator's centre), the symbol (one of the
    four the radio keeps), the SSID and the comment. Returns the form, None on Esc."""

    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        s = self.settings
        symbols = [(_("No. {n}: {name} ({code})").format(n=i + 1, name=dprs.symbol_name(code),
                                                         code=code), i)
                   for i, code in enumerate(s["symbols"])]
        with Vertical(classes="modal"):
            yield Label(_("D-PRS beacon"), classes="modal-subtitle")
            yield Checkbox(_("Send my position with every over"), s["send"], id="send")
            yield Select([(_("The radio's GPS"), "gps"), (_("A position typed here"), "manual")],
                         value=s["source"], allow_blank=False, id="source")
            yield Input("" if s["lat"] is None else f"{s['lat']:.5f}", placeholder=_("Latitude"),
                        id="lat")
            yield Input("" if s["lon"] is None else f"{s['lon']:.5f}",
                        placeholder=_("Longitude (west negative)"), id="lon")
            yield Input("" if s["alt"] is None else f"{s['alt']:g}",
                        placeholder=_("Altitude (m, optional)"), id="alt")
            yield Input("", placeholder=_("Or a locator (its centre)"), id="locator")
            yield Select(symbols, value=s["symbol"], allow_blank=False, id="symbol")
            yield Select([(name, i) for i, name in enumerate(dprs.SSID_CHOICES)],
                         value=s["ssid"], allow_blank=False, id="ssid")
            yield Input(s["comment"], placeholder=_("Comment"), max_length=dprs.COMMENT_LENGTH,
                        id="comment")
            with Horizontal(classes="modal-buttons"):
                yield Button(_("Save"), variant="primary", id="save")
                yield Button(_("Cancel"), id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            self.dismiss(None)
            return
        form = beacon_form(
            send=self.query_one("#send", Checkbox).value,
            source=self.query_one("#source", Select).value,
            lat=self.query_one("#lat", Input).value, lon=self.query_one("#lon", Input).value,
            alt=self.query_one("#alt", Input).value,
            locator=self.query_one("#locator", Input).value,
            symbol=self.query_one("#symbol", Select).value,
            ssid=self.query_one("#ssid", Select).value,
            comment=self.query_one("#comment", Input).value)
        if isinstance(form, str):
            self.notify(form, severity="error")
            return
        self.dismiss(form)


def beacon_form(send, source, lat, lon, alt, locator, symbol, ssid, comment) -> dict | str:
    """The beacon form as the core takes it, or why not (a message)."""
    form = {"send": bool(send), "source": source, "symbol": int(symbol), "ssid": int(ssid),
            "comment": comment}
    if source != "manual":
        return form
    try:
        if locator.strip():
            from ic705suite.apps.dstar.civ import locator_to_latlon

            form["lat"], form["lon"] = locator_to_latlon(locator)
        else:
            form["lat"] = float(lat.replace(",", "."))
            form["lon"] = float(lon.replace(",", "."))
        form["alt"] = float(alt.replace(",", ".")) if alt.strip() else None
    except ValueError as exc:
        return str(exc) if "locator" in str(exc).lower() else \
            _("Check the latitude, longitude and altitude")
    if not (-90 <= form["lat"] <= 90 and -180 <= form["lon"] <= 180):
        return _("Coordinates out of range")
    return form


def remote(link) -> bool:
    """The core runs elsewhere (a server), not in this program."""
    return getattr(link, "core", None) is None


def error_text(reply: dict) -> str:
    return render({"msgid": reply.get("msgid"), "args": reply.get("args", {}),
                   "text": reply.get("error", "")})


# ---- the terminal --------------------------------------------------------------------------

class DStarApp(App[int]):
    CSS = css()
    TITLE = "ic705suite D-STAR"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("left_square_bracket", "reflector(-1)", show=False),
        Binding("right_square_bracket", "reflector(1)", show=False),
        Binding("c", "change", show=False),
        Binding("n", "note", show=False),
        Binding("m", "message", show=False),
        Binding("h", "hide_mine", show=False),
        Binding("v", "view", show=False),
        Binding("b", "beacon", show=False),
        Binding("r", "refresh", show=False),
        Binding("escape", "escape", show=False),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link, owns_link: bool = False, config=None) -> None:
        super().__init__()
        self.link = link
        self.config = config
        self.owns_link = owns_link
        self.state: dict = {}
        self.rows: list[dict] = []
        self.shown: list[dict] = []
        self.reflectors: list[dict] = []
        self.choice = ""                     # the reflector [ ] chose, C changes to
        self.review: dict | None = None
        self.hide_mine = False
        self.history_version = -1
        self.dprs_version = -1
        self.dprs_rows: list[dict] = []
        self.weather_rows: list[dict] = []
        self.view = "history"
        self.started_here = False

    def compose(self) -> ComposeResult:
        yield RigStatusLine(self.link.description, id="statusline")
        with Horizontal(id="top"):
            yield Static(id="reflector", classes="frame")
            yield Static(id="onair", classes="frame")
        yield DataTable(id="history", cursor_type="row", zebra_stripes=False, classes="frame")
        yield Static(id="message")
        yield Static(id="stats")

    def on_mount(self) -> None:
        self.query_one("#top").styles.height = 11
        for name, title in (("#reflector", _("Reflector")), ("#onair", _("On air"))):
            box = self.query_one(name, Static)
            box.border_title = title
            box.styles.width = "1fr"
        table = self.query_one("#history", DataTable)
        table.styles.height = "1fr"
        self._fill_view()
        self.set_interval(1.0, self._tick)
        self.link.attach(self._on_event)
        self.run_worker(self._start(), exclusive=True)

    async def _start(self) -> None:
        try:
            await self.link.connect()
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._message("error", _("cannot connect: {error}").format(error=exc))
            return
        state = await self.link.call("dstar.state")
        if not state.get("error") and not state.get("on"):
            reply = await self.link.call("dstar.start")
            if reply.get("error"):
                self._message("error", error_text(reply))
                return
            self.started_here = True
        await self._load_reflectors()
        await self._load_history()

    # ---- events -------------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if not self.is_running:
            return
        kind = event.get("type")
        if kind == "rig":
            status = self.query_one("#statusline", RigStatusLine)
            status.rig = event
            status.refresh()
        elif kind == "dstar":
            self.state = event
            if event.get("history_version") != self.history_version:
                self.history_version = event.get("history_version")
                self.run_worker(self._load_history(), group="history", exclusive=True)
            if event.get("dprs_version") != self.dprs_version:
                self.dprs_version = event.get("dprs_version")
                self.run_worker(self._load_dprs(), group="dprs", exclusive=True)
            self._show()
        elif kind == "reconnected":
            self.run_worker(self._start(), exclusive=True)
        elif kind in ("info", "error") and str(event.get("msgid", "")).startswith("D-STAR"):
            self._message(kind, render(event))

    async def _load_history(self) -> None:
        reply = await self.link.call("dstar.history")
        if not reply.get("error"):
            self.rows = reply.get("history", [])
            self._fill_history()
            self._show()

    async def _load_dprs(self) -> None:
        reply = await self.link.call("dstar.dprs")
        weather = await self.link.call("dstar.weather")
        if not reply.get("error"):
            self.dprs_rows = reply.get("dprs", [])
        if not weather.get("error"):
            rows = weather.get("weather", [])
            km = distance_to(home_of(self.state))
            rows.sort(key=lambda r: km(r) if km(r) is not None else 1e9)   # the nearest first
            self.weather_rows = rows
        if self.view != "history":
            self._fill_view()

    async def _load_reflectors(self) -> None:
        reply = await self.link.call("dstar.reflectors")
        if reply.get("error"):
            self._message("error", reply["error"])
            return
        self.reflectors = [r for r in reply.get("reflectors", []) if r["via"] == "int"]
        self._show()

    def shown_rows(self) -> list[dict]:
        mine = (self.state.get("my_call") or (self.config.callsign if self.config else "")
                or "").split()[:1]
        if self.hide_mine and mine:
            return [r for r in self.rows if r["callsign"] != mine[0]]
        return self.rows

    def _fill_history(self) -> None:
        self._fill_view()

    def _fill_view(self) -> None:
        """The lower table: the history, the D-PRS reports or the weather stations."""
        table = self.query_one("#history", DataTable)
        row = table.cursor_row
        table.clear(columns=True)
        table.border_title = _(dict(VIEWS)[self.view])
        home = home_of(self.state)
        if self.view == "history":
            table.add_columns(*history_columns())
            self.shown = self.shown_rows()
            for r in self.shown:
                cells = history_cells(r, home)
                cells[1] = Text(cells[1], style="bold red" if r["direction"] == "TX"
                                else "bold cyan")
                table.add_row(*cells)
            count = len(self.shown)
        elif self.view == "dprs":
            table.add_columns(*dprs_columns())
            for r in self.dprs_rows:
                cells = dprs_cells(r, home)
                cells[1] = Text(cells[1], style="bold yellow")
                table.add_row(*cells)
            count = len(self.dprs_rows)
        else:
            table.add_columns(*weather_columns())
            for r in self.weather_rows:
                cells = weather_cells(r, home)
                cells[0] = Text(cells[0], style="bold yellow")
                table.add_row(*cells)
            count = len(self.weather_rows)
        if count:
            table.move_cursor(row=min(row, count - 1))

    def _show(self) -> None:
        if not self.is_running:
            return
        state = self.state
        box = self.query_one("#reflector", Static)
        info = reflector_info(state)
        text = Text()
        text.append(f"{info.get('name') or '—'}\u00a0\u00a0", style="bold yellow")
        text.append(info.get("to") or "", style="bold cyan")
        text.append(f"\n{info.get('description') or ''}\n", style="white")
        styles = {"good": "bold green", "bad": "bold red", "warn": "bold yellow", "dim": "dim",
                  "text": "white"}
        for piece, kind in reflector_status(state):
            text.append(piece + "\u00a0\u00a0", style=styles[kind])
        if reflector_status(state):
            text.append("\n")
        server = info["radio_server"] or _("(set [dstar] server)")
        text.append(f"{_('Server')} ", style="dim")
        text.append(server, style="bold")
        if info["mismatch"]:
            text.append("\u00a0\u00a0" + _("{to} is reached through {needed}").format(
                to=info.get("to", ""), needed=info.get("server", "")), style="bold red")
        text.append("\nUR\u00a0", style="dim")
        text.append(state.get("ur") or "—", style="bold")
        text.append("\u00a0\u00a0R1\u00a0", style="dim")
        text.append(state.get("r1") or "—")
        text.append("\u00a0\u00a0R2\u00a0", style="dim")
        text.append(state.get("r2") or "—")
        if self.choice and self.choice != state.get("ur"):
            text.append("\n" + _("C changes to {to}").format(to=self.choice), style="bold yellow")
        problem = warning(state)
        if problem:
            text.append(f"\n⚠ {problem}", style="bold red")
        lamps = Text("\n")
        for name, lamp in indicators(state):
            lamps.append(f" {name} ", style={"on": "bold black on green",
                                             "bad": "bold white on red"}.get(lamp, "dim"))
            lamps.append(" ")
        box.update(text + lamps)
        self._show_on_air()

    def _show_on_air(self) -> None:
        box = self.query_one("#onair", Static)
        air = on_air(self.state, self.review)
        text = Text()
        if not air["title"]:
            text.append(_("no activity"), style="dim")
        else:
            style = {"RX": "bold green", "TX": "bold red", "HISTORY": "bold magenta"}.get(
                air["title"], "dim")
            text.append(_(air["title"]), style=style)
            text.append(f"\u00a0\u00a0\u00a0{air['time']}\n", style="dim")
            text.append(air["call"] + "\n", style="bold red" if air.get("tx") else
                        "bold green" if air.get("live") else "bold yellow")
            for line in air["lines"]:
                text.append(line + "\n", style="cyan" if line.startswith("«") else "white")
        title, heard = heard_list(self.state, self.rows)
        if heard:
            text.append("\n" + title + "\n", style="dim")
            for call, name, hhmm in heard:
                text.append(f"{call:<9}", style="bold yellow")
                text.append(f" {name[:22]:<22} {hhmm}\n", style="white")
        box.update(text)

    def _tick(self) -> None:
        if not self.is_running:
            return
        status = self.query_one("#statusline", RigStatusLine)
        status.extra = extra(self.state)
        status.refresh()
        stats = self.query_one("#stats", Static)
        stats.update(clock_footer(stats.size.width, [(_("Overs"), len(self.rows))],
                                  [_(HINT), _(SHORT_HINT)]))
        if self.state.get("transmitting") or (self.state.get("rx") or {}).get("live"):
            self._show_on_air()

    def _message(self, kind: str, text: str) -> None:
        self.query_one("#message", Static).update(
            Text(" " + text[:200], style="bold red" if kind == "error" else "cyan"))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self.view == "history" and 0 <= event.cursor_row < len(self.shown):
            self.review = self.shown[event.cursor_row]
            self._show_on_air()

    # ---- the keys (the window runs these too) -------------------------------------------

    def action_reflector(self, step: int) -> None:
        """[ ]: the reflector to change to, round the list (from the one in use)."""
        if not self.reflectors:
            return
        tos = [r["to"] for r in self.reflectors]
        current = self.choice or self.state.get("ur", "")
        index = tos.index(current) if current in tos else -1
        self.choice = tos[(index + step) % len(tos)]
        chosen = next(r for r in self.reflectors if r["to"] == self.choice)
        self._message("info", f"{chosen['to']}  {chosen.get('name') or ''}"
                      + (f" · {chosen['server']}" if chosen.get("server") else "")
                      + " — " + _("C changes to it"))
        self._show()

    def action_change(self) -> None:
        self.run_worker(self._change(), group="tx")

    async def _change(self) -> None:
        to = self.choice or ""
        if not to:
            self._message("info", _("Choose a reflector first ([ ])"))
            return
        if to == self.state.get("ur"):
            self._message("info", _("{to} is already the current TO").format(to=to))
            return
        self._message("info", _("Changing the TO to {to}…").format(to=to))
        reply = await self.link.call("dstar.change", to=to)
        if reply.get("error"):
            self._message("error", error_text(reply))
            return
        self.choice = ""
        self.state["ur"] = reply["to"]               # confirmed: before the next state
        self._message("info", _("TO {to}: press the PTT briefly to register on the "
                                "reflector").format(to=reply["to"]))
        self._show()

    def action_note(self) -> None:
        self.run_worker(self._note(), group="tx")

    async def _note(self) -> None:
        note = await self.push_screen_wait(NameScreen(
            _("MY note"), _("The 4 characters after your call sign (/xxxx):"),
            placeholder="705", value=self.state.get("my_note", "")))
        if note is None:
            return
        reply = await self.link.call("dstar.note", note=note)
        self._message("error" if reply.get("error") else "info",
                      error_text(reply) if reply.get("error") else
                      _("MY note /{note} written").format(note=reply["note"]))

    def action_message(self) -> None:
        self.run_worker(self._tx_message(), group="tx")

    async def _tx_message(self) -> None:
        text = await self.push_screen_wait(NameScreen(
            _("TX message"), _("The 20-character message your overs carry:"),
            value=self.state.get("tx_message", "")))
        if text is None:
            return
        reply = await self.link.call("dstar.message", text=text)
        self._message("error" if reply.get("error") else "info",
                      error_text(reply) if reply.get("error") else _("TX message written"))

    def action_hide_mine(self) -> None:
        self.hide_mine = not self.hide_mine
        self._fill_history()
        self._message("info", _("Our own overs hidden") if self.hide_mine else
                      _("Our own overs shown"))

    def action_view(self) -> None:
        """V: the history, the D-PRS reports heard, the weather stations, in turn."""
        names = [name for name, _label in VIEWS]
        self.view = names[(names.index(self.view) + 1) % len(names)]
        self._fill_view()

    def action_beacon(self) -> None:
        self.run_worker(self._beacon(), group="tx")

    async def _beacon(self) -> None:
        """B: our D-PRS beacon, read from the radio, edited, written and confirmed."""
        reply = await self.link.call("dstar.dprs_settings")
        settings = reply.get("settings")
        if not settings:
            self._message("error", error_text(reply) if reply.get("error") else
                          _("The radio's D-PRS settings could not be read"))
            return
        form = await self._beacon_form(settings)
        if form is None:
            return
        reply = await self.link.call("dstar.dprs_save", **form)
        self._message("error" if reply.get("error") else "info",
                      error_text(reply) if reply.get("error") else
                      _("D-PRS beacon saved in the radio and confirmed"))

    async def _beacon_form(self, settings: dict) -> dict | None:
        return await self.push_screen_wait(BeaconScreen(settings))

    def action_refresh(self) -> None:
        """R: the reflectors' status checked again now."""
        self.run_worker(self.link.call("dstar.status_refresh"))
        self._message("info", _("Checking the reflector's status…"))

    def action_escape(self) -> None:
        if self.review is not None:
            self.review = None
            self._show_on_air()
            return
        self.action_leave()

    def action_leave(self) -> None:
        self.run_worker(self._leave(), exclusive=True)

    async def _leave(self) -> None:
        # A core of its own (the server's) goes on following D-STAR: the history fills.
        if self.started_here and not remote(self.link):
            await self.link.call("dstar.stop")
        if self.owns_link:
            await self.link.close()
        else:
            self.link.detach()
        self.exit(0)
