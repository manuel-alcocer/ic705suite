"""FT8 in the terminal: the band's activity, cycle by cycle, as WSJT-X's Band Activity.

The core decodes (apps.ft8.service); this screen starts it when it opens, shows what each
cycle brings (a CQ in green, what is sent to us in red, what we send in yellow) and stops
it when it closes. Enter answers the chosen station, Q calls CQ, H halts, F puts our
transmit frequency on the chosen line's.
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

HINT = N_("Enter Answer · Q CQ · H Halt · F TX freq · ←→ Band · C CQ only · S Stop/Start · "
          "Esc Back")
SHORT_HINT = N_("Enter Answer · Q CQ · H Halt · ←→ Band · Esc Back")
FT8_BANDS = list(DIALS)

# How each kind of line is drawn (Rich styles; the graphical client maps them too).
STYLE_CQ = "bold green"
STYLE_ME = "bold white on dark_red"
STYLE_DOUBT = "dim italic"
STYLE_TX = "bold yellow"
STYLE_HEADING = "dim"


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
        known["decodes"] = known["decodes"] + event["decodes"]
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


def rows(cycles: list[dict], cq_only: bool, mycall: str) -> list[tuple]:
    """(cycle start, decode or None, cells, style) for each line of the list."""
    out = []
    for cycle in cycles:
        out.append((cycle["start"], None, ["────────", "", "", "", heading(cycle)],
                    STYLE_HEADING))
        shown = [d for d in cycle["decodes"] if not cq_only or d.get("kind") in ("cq", "tx")
                 or kind_style(d, mycall) == STYLE_ME]
        for d in sorted(shown, key=lambda d: (d.get("kind") != "tx", d["freq"])):
            stamp = f"{d['time'][:2]}:{d['time'][2:4]}:{d['time'][4:]}"
            if d.get("kind") == "tx":
                out.append((cycle["start"], d, [stamp, "TX", "", str(d["freq"]),
                                                d["message"]], STYLE_TX))
            else:
                out.append((cycle["start"], d, [stamp, str(d["snr"]), f"{d['dt']:.1f}",
                                                str(d["freq"]), d["message"]],
                            kind_style(d, mycall)))
    return out


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
        Binding("f", "tx_freq", show=False),
        Binding("left", "band(-1)", show=False, priority=True),
        Binding("right", "band(1)", show=False, priority=True),
        Binding("c", "cq_only", show=False),
        Binding("s", "toggle", show=False),
        Binding("escape", "leave", show=False),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link, owns_link: bool = False) -> None:
        super().__init__()
        self.link = link
        self.owns_link = owns_link
        self.state: dict = {}
        self.cycles: list[dict] = []         # oldest first, as decoded (passes merged)
        self.lines: list[tuple] = []         # what each row of the table shows
        self.cq_only = False
        self.started_here = False
        self.mycall = ""

    def compose(self) -> ComposeResult:
        yield RigStatusLine(self.link.description, id="statusline")
        yield DataTable(id="activity", cursor_type="row", zebra_stripes=False,
                        classes="frame")
        yield Static(id="message")
        yield Static(id="stats")

    def on_mount(self) -> None:
        table = self.query_one("#activity", DataTable)
        table.border_title = _("Band activity")
        table.add_columns(_("UTC"), Text("dB", justify="right"), Text("DT", justify="right"),
                          Text(_("Freq"), justify="right"), _("Message"))
        table.styles.height = "1fr"
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
        self.cycles = (await self.link.call("ft8.history")).get("cycles", [])
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
        elif kind == "ft8":
            self.state = event
            self._tick()
        elif kind == "ft8.cycle":
            self._add(event)
            self._message("info", "")
        elif kind == "ft8.qso":
            self.run_worker(self._logbook(event["qso"]))
        elif kind == "ft8.tx":
            self._add(sent_line(event))
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
        for _start, _decode, cells, style in self.lines:
            stamp, snr, dt, freq, message = cells
            table.add_row(Text(stamp, style=STYLE_HEADING if style == STYLE_HEADING else ""),
                          Text(snr, justify="right"), Text(dt, justify="right"),
                          Text(freq, justify="right"), Text(message, style=style))
        if table.row_count:
            table.move_cursor(row=table.row_count - 1 if at_end or not keep_end
                              else min(row, table.row_count - 1))

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
        status.extra = extra(self.state)
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
            row, why = await asyncio.to_thread(log_qso, fields)
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
