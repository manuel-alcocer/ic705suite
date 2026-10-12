"""FT8 in the terminal: the band's activity, cycle by cycle, as WSJT-X's Band Activity.

The core decodes (apps.ft8.service); this screen starts it when it opens, shows what each
cycle brings (a CQ in green, what is sent to us in red, what we send in yellow) and stops
it when it closes. Under it, the QSO: what we send, what is sent to us and what the station
we work says. Enter answers the chosen station, Q calls CQ, H halts, F puts our transmit
frequency on the chosen line's ([ ] move it), - + the transmit audio (the ALC must stay
at 0), , . the radio's power.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Static

from ic705suite.apps.ft8.service import CYCLE, DIALS
from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.station.links import Link
from ic705suite.ui.rigline import RigStatusLine
from ic705suite.ui.widgets import clock_footer, css

HINT = N_("Enter Answer · Q CQ · H Halt · T Tone · F [ ] TX freq · - + TX level · , . Power · "
          "←→ Band · C CQ only · S Stop/Start · Esc Back")
SHORT_HINT = N_("Enter Answer · Q CQ · H Halt · ←→ Band · Esc Back")
FT8_BANDS = list(DIALS)
FREQ_STEP = 25                 # Hz: [ ] move the transmit frequency
LEVEL_STEP = 0.05              # - + change the transmit audio
ALC_GOOD = 0.5                 # the ALC up to this: green (well within its zone)
ALC_HOT = 1.0                  # over this, past the radio's ALC zone: red, lower the level

# How each kind of line is drawn (Rich styles; the graphical client maps them too).
STYLE_CQ = "bold green"
STYLE_ME = "bold white on dark_red"
STYLE_DOUBT = "dim italic"
STYLE_TX = "bold yellow"
STYLE_HEADING = "dim"
# The marks of what is heard against the logbook (worked.py), as GridTracker and JTAlert.
STYLE_NEW_DXCC = "bold magenta"
STYLE_NEW_BAND = "magenta"
STYLE_NEW_GRID = "bold cyan"
STYLE_B4 = "dim"
STYLE_CONFIRMED = "green"


def kind_style(decode: dict, mycall: str) -> str:
    if mycall and decode.get("to") and \
            decode["to"].split("/")[0] in (mycall, mycall.split("/")[0]):
        return STYLE_ME
    if decode.get("kind") == "cq":
        return STYLE_CQ
    if decode.get("doubtful"):
        return STYLE_DOUBT
    return ""


def cycle_label(start: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(start))


def heading(cycle: dict) -> str:
    """The line that opens each cycle in the list."""
    dial = cycle.get("dial")
    return f"── {cycle_label(cycle['start'])}" + \
        (f" · {bands.format_frequency(dial)}" if dial else "") + " " + "─" * 24


def merge(cycles: list[dict], event: dict) -> None:
    """A pass of a cycle (or a message we sent in it) into the list: a cycle seen before
    gets the new lines."""
    known = next((c for c in cycles if c["start"] == event["start"]), None)
    if known is not None:
        seen = {(d.get("kind") == "tx", d["message"]) for d in known["decodes"]}
        known["decodes"] = known["decodes"] + [
            d for d in event["decodes"] if (d.get("kind") == "tx", d["message"]) not in seen]
        if event.get("dial"):
            known["dial"] = event["dial"]
    else:
        cycles.append({**event, "decodes": list(event["decodes"])})
        cycles.sort(key=lambda c: c["start"])
        del cycles[:-60]


def sent_line(event: dict) -> dict:
    """An ft8.tx event as a line of its cycle."""
    return {"start": event["start"], "dial": None,
            "decodes": [{"kind": "tx", "time": time.strftime("%H%M%S", time.gmtime(
                event["start"])), "snr": None, "dt": None, "freq": event["freq"],
                "message": event["message"]}]}


def where(d: dict) -> str:
    """Country, distance and bearing: "Wales 1814 km 6°" (~ from the country's middle)."""
    parts = []
    if d.get("country"):
        parts.append(d["country"])
    if d.get("km") is not None:
        parts.append(f"{'~' if d.get('approx') else ''}{d['km']} km {d.get('az', 0)}°")
    return " ".join(parts)


def mark(d: dict) -> tuple[str, str]:
    """(text, style) of what the logbook says of the sender: the newest thing first."""
    if d.get("dxcc") == "new":
        return _("NEW DXCC"), STYLE_NEW_DXCC
    if d.get("dxcc") == "new_band":
        return _("NEW BAND"), STYLE_NEW_BAND
    if d.get("grid_state") in ("new", "new_band"):
        return _("NEW GRID") if d["grid_state"] == "new" else _("GRID BAND"), STYLE_NEW_GRID
    if d.get("b4"):
        return "B4", STYLE_B4
    if d.get("dxcc") == "confirmed":
        return "✓", STYLE_CONFIRMED
    return "", ""


def rows(cycles: list[dict], cq_only: bool, mycall: str) -> list[tuple]:
    """(cycle start, decode or None, cells, style) for each line of the list: time, dB, DT,
    audio frequency, message, where the sender is, and its mark (mark())."""
    out = []
    for cycle in cycles:
        shown = [d for d in cycle["decodes"] if not cq_only or d.get("kind") in ("cq", "tx")
                 or kind_style(d, mycall) == STYLE_ME]
        if not shown:
            continue                     # a quiet cycle: no heading over nothing
        out.append((cycle["start"], None, ["────────", "", "", "", heading(cycle), "", ""],
                    STYLE_HEADING))
        for d in sorted(shown, key=lambda d: (d.get("kind") != "tx", d["freq"])):
            stamp = f"{d['time'][:2]}:{d['time'][2:4]}:{d['time'][4:]}"
            if d.get("kind") == "tx":
                out.append((cycle["start"], d, [stamp, "TX", "", str(d["freq"]),
                                                d["message"], "", ""], STYLE_TX))
            else:
                out.append((cycle["start"], d, [stamp, str(d["snr"]), f"{d['dt']:.1f}",
                                                str(d["freq"]), d["message"], where(d),
                                                mark(d)[0]],
                            kind_style(d, mycall)))
    return out


def conversation(cycles: list[dict], mycall: str, them: str) -> list[tuple]:
    """The QSO's lines, as rows(): what we sent, what was sent to us, and what the station
    we work said (to anyone)."""
    out = []
    for cycle in cycles:
        for d in sorted(cycle["decodes"], key=lambda d: (d.get("kind") != "tx", d["freq"])):
            style = STYLE_TX if d.get("kind") == "tx" else kind_style(d, mycall)
            if style not in (STYLE_TX, STYLE_ME) and not (them and d.get("sender") == them):
                continue
            stamp = f"{d['time'][:2]}:{d['time'][2:4]}:{d['time'][4:]}"
            if style == STYLE_TX:
                cells = [stamp, "TX", "", str(d["freq"]), d["message"]]
            else:
                cells = [stamp, str(d["snr"]), f"{d['dt']:.1f}", str(d["freq"]), d["message"]]
            out.append((cycle["start"], d, cells, style))
    return out


# A QSO is five messages, both ways: answering a CQ, our grid, their report, our R-report,
# their RR73, our 73; calling CQ, our CQ, their grid, our report, their R-report, our RR73.
# Each step (sequence.Qso) is how many have gone by when we start sending its message.
QSO_MESSAGES = 5
STEP_DONE = {"cq": 0, "tx1": 0, "tx2": 2, "tx3": 2, "tx4": 4, "tx5": 4, "done": 5}
PHASES = {
    ("cq", 0): N_("Calling CQ"),
    ("cq", 1): N_("CQ sent: waiting for an answer"),
    ("tx1", 0): N_("Answering with our grid"),
    ("tx1", 1): N_("Grid sent: waiting for their report"),
    ("tx2", 2): N_("They answered: sending their report"),
    ("tx2", 3): N_("Report sent: waiting for their R-report"),
    ("tx3", 2): N_("Report received: sending ours (R)"),
    ("tx3", 3): N_("R-report sent: waiting for their RR73"),
    ("tx4", 4): N_("Reports exchanged: sending RR73"),
    ("tx4", 5): N_("RR73 sent: the QSO counts"),
    ("tx5", 4): N_("RR73 received: sending 73"),
    ("tx5", 5): N_("73 sent: the QSO counts"),
    ("done", 5): N_("QSO complete"),
}


def qso_progress(tx: dict) -> tuple[int, int, str] | None:
    """(messages gone by, of how many, what is going on) of the QSO under way; None with no
    QSO (or CQ) going."""
    step = tx.get("step") or "idle"
    if step not in STEP_DONE or (not tx.get("on") and step != "done"):
        return None
    done = STEP_DONE[step] + (1 if step != "done" and tx.get("tries") else 0)
    done = min(done, QSO_MESSAGES)
    return done, QSO_MESSAGES, _(PHASES.get((step, done), ""))


def qso_status(tx: dict, completed: str = "") -> tuple[int, int, str] | None:
    """What the QSO area shows of the QSO: under way (qso_progress, with whom), or the last
    one completed (`completed`: its call) until another starts."""
    going = qso_progress(tx)
    if going is not None:
        done, total, text = going
        them = tx.get("them") or ""
        return done, total, f"{them} · {text}" if them else text
    if completed:
        return QSO_MESSAGES, QSO_MESSAGES, _("QSO with {call} complete").format(call=completed)
    return None


def progress_bar(done: int, total: int, cells: int = 10) -> str:
    full = round(done / total * cells) if total else 0
    return "█" * full + "░" * (cells - full)


def power_percent(rig: dict) -> float | None:
    power = (rig.get("controls") or {}).get("power")
    return power if power is not None else rig.get("power")


def watts(rig: dict, percent: float | None) -> str:
    if percent is None:
        return "?"
    return f"{percent * float(rig.get('max_power_w') or 10) / 100:.1f} W"


def power_step(rig: dict, percent: float | None, step: int) -> float:
    """The power one watt up or down (in percent, as the radio takes it)."""
    one = 100 / float(rig.get("max_power_w") or 10)
    return max(0.0, min(100.0, round((percent or 0) + step * one)))


def tx_pieces(state: dict, rig: dict) -> list[tuple[str, str]]:
    """The transmit settings, for the status line: audio, power, the ALC (live while
    transmitting, else the last message's peak)."""
    tx = state.get("tx") or {}
    pieces = [] if tx.get("on") else [("TX", f"{tx.get('freq', '?')} Hz")]   # else tx_piece's
    pieces += [(_("LEVEL"), f"{round(float(tx.get('level', 0.5)) * 100)} %"),
               (_("PWR"), watts(rig, power_percent(rig)))]
    meters = rig.get("meters") or {}
    if rig.get("transmitting"):
        pieces.append(("ALC", f"{float(meters.get('alc') or 0):.2f}"))
    elif tx.get("last"):
        pieces.append(("ALC", _("{alc} (last)").format(alc=f"{tx['last']['alc']:.2f}")))
    return pieces


def tx_piece(state: dict) -> tuple[str, str] | None:
    """What we are sending, for the status line."""
    tx = state.get("tx") or {}
    if not tx.get("on"):
        return None
    turn = _("even") if tx.get("parity") == 0 else _("odd")
    return ("TX", f"{tx.get('message', '')} · {tx.get('freq')} Hz · {turn}")


def extra(state: dict, now: float | None = None) -> list[tuple[str, str]]:
    """FT8's pieces of the status line."""
    now = time.time() if now is None else now
    if not state.get("on"):
        return [("FT8", _("stopped"))]
    pieces = [("FT8", state.get("band") or "?")]
    if state.get("dial") and not state.get("ft8_dial"):
        pieces.append(("", _("not on an FT8 frequency (←→)")))
    pieces.append((_("CYCLE"), f"{int(now % CYCLE):02d}/{CYCLE} s"))
    sending = tx_piece(state)
    if sending:
        pieces.append(sending)
    last = state.get("last") or {}
    if last:
        pieces.append((_("LAST"), _("{count} decoded in {seconds} s").format(
            count=last.get("count", 0), seconds=last.get("seconds", 0))))
    if state.get("decoding"):
        pieces.append(("", _("decoding…")))
    return pieces


class Ft8App(App[int]):
    CSS = css()
    TITLE = "ic705suite FT8"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("enter", "reply", show=False, priority=True),
        Binding("q", "cq", show=False),
        Binding("h", "halt", show=False),
        Binding("t", "tone", show=False),
        Binding("f", "tx_freq", show=False),
        Binding("left_square_bracket", "move_tx(-1)", show=False),
        Binding("right_square_bracket", "move_tx(1)", show=False),
        Binding("minus", "level(-1)", show=False),
        Binding("plus", "level(1)", show=False),
        Binding("comma", "power(-1)", show=False),
        Binding("full_stop", "power(1)", show=False),
        Binding("left", "band(-1)", show=False, priority=True),
        Binding("right", "band(1)", show=False, priority=True),
        Binding("c", "cq_only", show=False),
        Binding("s", "toggle", show=False),
        Binding("escape", "leave", show=False),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link, owns_link: bool = False, config=None) -> None:
        super().__init__()
        self.link = link
        self.config = config
        self.owns_link = owns_link
        self.state: dict = {}
        self.rig: dict = {}
        self.cycles: list[dict] = []         # oldest first, as decoded (passes merged)
        self.lines: list[tuple] = []         # what each row of the table shows
        self.cq_only = False
        self.started_here = False
        self.mycall = ""
        self.completed = ""                  # the call of the last QSO completed

    def compose(self) -> ComposeResult:
        yield RigStatusLine(self.link.description, id="statusline")
        yield DataTable(id="activity", cursor_type="row", zebra_stripes=False,
                        classes="frame")
        yield DataTable(id="qso", cursor_type="none", zebra_stripes=False, classes="frame")
        yield Static(id="message")
        yield Static(id="stats")

    def on_mount(self) -> None:
        table = self.query_one("#activity", DataTable)
        table.border_title = _("Band activity")
        table.add_columns(_("UTC"), Text("dB", justify="right"), Text("DT", justify="right"),
                          Text(_("Freq"), justify="right"), _("Message"), _("Where"), "")
        table.styles.height = "1fr"
        qso = self.query_one("#qso", DataTable)
        qso.border_title = _("QSO")
        qso.add_columns(_("UTC"), Text("dB", justify="right"), Text("DT", justify="right"),
                        Text(_("Freq"), justify="right"), _("Message"))
        qso.styles.height = 9
        qso.can_focus = False
        self.set_interval(1.0, self._tick)
        self._tick()
        self.link.attach(self._on_event)
        self.run_worker(self._start(), exclusive=True)

    async def _start(self) -> None:
        try:
            await self.link.connect()
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._message("error", _("cannot connect: {error}").format(error=exc))
            return
        self.mycall = ((self.link.welcome or {}).get("callsign") or "").upper()
        for cycle in (await self.link.call("ft8.history")).get("cycles", []):
            merge(self.cycles, cycle)       # what the core kept (after a reconnection: more)
        self._fill()
        state = await self.link.call("ft8.state")
        if not state.get("on"):
            reply = await self.link.call("ft8.start")
            if reply.get("error"):
                self._message("error", render({"msgid": reply.get("msgid"),
                                               "args": reply.get("args", {}),
                                               "text": reply["error"]}))
                return
            self.started_here = True
            self._message("info", _("Decoding: the first cycle comes when the next 15 s "
                                    "are heard whole"))

    # ---- events -------------------------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if not self.is_running:              # closing: the link may still talk
            return
        kind = event.get("type")
        if kind == "rig":
            status = self.query_one("#statusline", RigStatusLine)
            status.rig = event
            status.refresh()
            self.rig = event
        elif kind == "ft8":
            them = (self.state.get("tx") or {}).get("them")
            self.state = event
            if (event.get("tx") or {}).get("on"):
                self.completed = ""          # a new QSO (or CQ) under way
            if (event.get("tx") or {}).get("them") != them:
                self._fill_qso()
            self._show_progress()
            self._tick()
        elif kind == "ft8.cycle":
            self._add(event)
            self._message("info", "")
        elif kind == "ft8.qso":
            self.completed = event["qso"].get("CALL", "")
            self._show_progress()
            if not event.get("logbook"):         # the core's shared logbook has it
                self.run_worker(self._logbook(event["qso"]))
        elif kind == "ft8.tx":
            self._add(sent_line(event))
        elif kind == "reconnected":          # the core is back (perhaps restarted): again
            self.run_worker(self._start(), group="start", exclusive=True)
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _add(self, cycle: dict) -> None:
        merge(self.cycles, cycle)
        self._fill(keep_end=True)

    def _fill(self, keep_end: bool = False) -> None:
        table = self.query_one("#activity", DataTable)
        at_end = not table.row_count or table.cursor_row >= table.row_count - 2
        row = table.cursor_row
        table.clear()
        self.lines = rows(self.cycles, self.cq_only, self.mycall)
        for _start, decode, cells, style in self.lines:
            stamp, snr, dt, freq, message, place, tag = cells
            table.add_row(Text(stamp, style=STYLE_HEADING if style == STYLE_HEADING else ""),
                          Text(snr, justify="right"), Text(dt, justify="right"),
                          Text(freq, justify="right"), Text(message, style=style),
                          Text(place, style="dim"),
                          Text(tag, style=mark(decode)[1] if decode else ""))
        if table.row_count:
            table.move_cursor(row=table.row_count - 1 if at_end or not keep_end
                              else min(row, table.row_count - 1))
        self._fill_qso()

    def _show_progress(self) -> None:
        """Under the QSO area: how far the QSO has gone, five messages in all."""
        status = qso_status(self.state.get("tx") or {}, self.completed)
        table = self.query_one("#qso", DataTable)
        if status is None:
            table.border_subtitle = ""
            return
        done, total, text = status
        table.border_subtitle = f"{progress_bar(done, total)} {done * 100 // total}% · {text}"

    def _fill_qso(self) -> None:
        table = self.query_one("#qso", DataTable)
        table.clear()
        them = (self.state.get("tx") or {}).get("them") or ""
        for _start, _decode, cells, style in conversation(self.cycles, self.mycall, them):
            stamp, snr, dt, freq, message = cells
            table.add_row(stamp, Text(snr, justify="right"), Text(dt, justify="right"),
                          Text(freq, justify="right"), Text(message, style=style))
        if table.row_count:
            table.scroll_end(animate=False)

    def _chosen(self) -> tuple[float, dict] | None:
        table = self.query_one("#activity", DataTable)
        if 0 <= table.cursor_row < len(self.lines):
            start, decode, _cells, _style = self.lines[table.cursor_row]
            if decode is not None and decode.get("kind") != "tx":
                return start, decode
        return None

    def _tick(self) -> None:
        if not self.is_running:
            return
        status = self.query_one("#statusline", RigStatusLine)
        status.extra = extra(self.state) + (tx_pieces(self.state, self.rig)
                                            if self.state.get("on") else [])
        status.refresh()
        stats = self.query_one("#stats", Static)
        count = sum(len(c["decodes"]) for c in self.cycles[-1:])
        stats.update(clock_footer(stats.size.width, [(_("Decoded"), count)],
                                  [_(HINT), _(SHORT_HINT)]))

    def _message(self, kind: str, text: str) -> None:
        self.query_one("#message", Static).update(
            Text(" " + text[:200], style="bold red" if kind == "error" else "cyan"))

    # ---- keys ---------------------------------------------------------------------------------

    def action_band(self, step: int) -> None:
        current = self.state.get("band") or "20m"
        index = FT8_BANDS.index(current) if current in FT8_BANDS else FT8_BANDS.index("20m")
        band = FT8_BANDS[(index + step) % len(FT8_BANDS)] if self.state.get("ft8_dial") \
            else FT8_BANDS[index]
        self.run_worker(self._band(band), group="band", exclusive=True)

    async def _band(self, band: str) -> None:
        reply = await self.link.call("ft8.band", band=band)
        if reply.get("error"):
            self._message("error", render({"msgid": reply.get("msgid"),
                                           "args": reply.get("args", {}),
                                           "text": reply["error"]}))
        else:
            self._message("info", _("FT8 on {band}: {frequency} USB-D").format(
                band=band, frequency=bands.format_frequency(reply["dial"])))

    async def _logbook(self, fields: dict) -> None:
        """The QSO completed into this machine's logbook (hamrlog), besides the core's
        ft8_log.adi."""
        import asyncio

        from ic705suite.apps.ft8.logbook import log_qso

        try:
            row, why = await asyncio.to_thread(
                log_qso, fields, self.config.ft8_setup if self.config else "",
                ((self.link.welcome or {}).get("rig") or {}).get("name") or "")
        except Exception as exc:  # noqa: BLE001 - shown on screen
            row, why = None, str(exc)
        if row is not None:
            self._message("info", _("{call} logged in the logbook (FT8 {band}, sent {sent}, "
                                    "received {rcvd})").format(
                call=fields["CALL"], band=fields.get("BAND", ""),
                sent=fields.get("RST_SENT", ""), rcvd=fields.get("RST_RCVD", "")))
        elif why != "already logged":
            self._message("error", _("Not in the logbook: {error} (it is in ft8_log.adi)")
                          .format(error=why))

    def action_reply(self) -> None:
        chosen = self._chosen()
        if chosen is None:
            self._message("error", _("Choose a station's line first (↑↓)"))
            return
        self.run_worker(self._call(_("Answering: {message}"), "ft8.reply",
                                   decode=chosen[1], start=chosen[0]), group="tx")

    def action_cq(self) -> None:
        self.run_worker(self._call(_("Calling: {message}"), "ft8.cq"), group="tx")

    def action_halt(self) -> None:
        self.run_worker(self._call(_("Transmission halted"), "ft8.halt"), group="tx")

    def action_tx_freq(self) -> None:
        chosen = self._chosen()
        if chosen is None:
            self._message("error", _("Choose a line first (↑↓): its frequency"))
            return
        self.run_worker(self._call(_("Transmitting on {freq} Hz"), "ft8.txfreq",
                                   freq=chosen[1]["freq"]), group="tx")

    def action_tone(self) -> None:
        if (self.state.get("tx") or {}).get("tone"):
            self.run_worker(self._call(_("Tone stopped"), "ft8.tone", on=False), group="tx")
        else:
            self.run_worker(self._call(_("Tone on {freq} Hz for 10 s: watch the ALC (T or H "
                                         "stops it)").replace("{freq}", str(
                                             (self.state.get("tx") or {}).get("freq", ""))),
                                       "ft8.tone", on=True), group="tx")

    def action_move_tx(self, step: int) -> None:
        freq = int((self.state.get("tx") or {}).get("freq") or 1500) + step * FREQ_STEP
        self.run_worker(self._call(_("Transmitting on {freq} Hz"), "ft8.txfreq", freq=freq),
                        group="tx")

    def action_level(self, step: int) -> None:
        level = float((self.state.get("tx") or {}).get("level") or 0.5) + step * LEVEL_STEP
        self.run_worker(self._call(_("TX level {percent} %"), "ft8.level", level=level),
                        group="tx")

    def action_power(self, step: int) -> None:
        percent = power_step(self.rig, power_percent(self.rig), step)
        self.run_worker(self._power(percent), group="power", exclusive=True)

    async def _power(self, percent: float) -> None:
        reply = await self.link.call("rig.set", id="power", value=percent)
        if reply.get("error"):
            self._message("error", reply["error"])
        else:
            value = reply.get("value", percent)
            self.rig.setdefault("controls", {})["power"] = value   # before the next rig event
            self._message("info", _("Power {watts}").format(watts=watts(self.rig, value)))
            self._tick()

    async def _call(self, done: str, method: str, **params) -> None:
        reply = await self.link.call(method, **params)
        if reply.get("error"):
            self._message("error", render({"msgid": reply.get("msgid"),
                                           "args": reply.get("args", {}),
                                           "text": reply["error"]}))
        else:
            self._message("info", done.format(**{k: v for k, v in reply.items()
                                                 if isinstance(v, (str, int))}))

    def action_cq_only(self) -> None:
        self.cq_only = not self.cq_only
        self._fill()
        self._message("info", _("Only the CQs (and what is sent to you)") if self.cq_only
                      else _("Every message"))

    def action_toggle(self) -> None:
        self.run_worker(self._toggle(), group="toggle", exclusive=True)

    async def _toggle(self) -> None:
        if self.state.get("on"):
            await self.link.call("ft8.stop")
            self.started_here = False
            self._message("info", _("Decoding stopped (S starts it again)"))
        else:
            reply = await self.link.call("ft8.start")
            if reply.get("error"):
                self._message("error", reply["error"])
            else:
                self.started_here = True
                self._message("info", _("Decoding"))

    def action_leave(self) -> None:
        self.run_worker(self._leave(), exclusive=True, group="leave")

    async def _leave(self) -> None:
        if self.started_here:
            # jt9 takes a good share of a small machine: decode only while someone looks.
            await self.link.call("ft8.stop")
        if self.owns_link:
            await self.link.close()
        else:
            self.link.detach()
        self.exit(0)
