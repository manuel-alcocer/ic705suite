"""FT8 in the terminal: the band's activity, cycle by cycle, as WSJT-X's Band Activity.

The core decodes (apps.ft8.service); this screen starts it when it opens, shows what each
cycle brings (a CQ in green, what is sent to us in red) and stops it when it closes.
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

HINT = N_("←→ Band · C CQ only · S Stop/Start · ↑↓ PgUp/PgDn · Esc Back")
SHORT_HINT = N_("←→ Band · C CQ only · S Stop/Start · Esc Back")
FT8_BANDS = list(DIALS)

# How each kind of line is drawn (Rich styles; the graphical client maps them too).
STYLE_CQ = "bold green"
STYLE_ME = "bold white on dark_red"
STYLE_DOUBT = "dim italic"


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


def extra(state: dict, now: float | None = None) -> list[tuple[str, str]]:
    """FT8's pieces of the status line."""
    now = time.time() if now is None else now
    if not state.get("on"):
        return [("FT8", _("stopped"))]
    pieces = [("FT8", state.get("band") or "?")]
    if state.get("dial") and not state.get("ft8_dial"):
        pieces.append(("", _("not on an FT8 frequency (←→)")))
    pieces.append((_("CYCLE"), f"{int(now % CYCLE):02d}/{CYCLE} s"))
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
        self.cycles: list[dict] = []         # oldest first, as decoded (deep passes merged)
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
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _add(self, cycle: dict) -> None:
        if cycle.get("deep"):
            known = next((c for c in self.cycles if c["start"] == cycle["start"]), None)
            if known is not None:
                known["decodes"] = known["decodes"] + cycle["decodes"]
        else:
            self.cycles.append(dict(cycle))
            del self.cycles[:-60]
        self._fill(keep_end=True)

    def _visible(self, decodes: list[dict]) -> list[dict]:
        shown = [d for d in decodes if not self.cq_only or d.get("kind") == "cq"
                 or kind_style(d, self.mycall) == STYLE_ME]
        return sorted(shown, key=lambda d: d["freq"])

    def _fill(self, keep_end: bool = False) -> None:
        table = self.query_one("#activity", DataTable)
        at_end = not table.row_count or table.cursor_row >= table.row_count - 2
        table.clear()
        for cycle in self.cycles:
            decodes = self._visible(cycle["decodes"])
            table.add_row(Text("────────", style="dim"), "", "", "",
                          Text(heading(cycle), style="dim"))
            for d in decodes:
                style = kind_style(d, self.mycall)
                table.add_row(f"{d['time'][:2]}:{d['time'][2:4]}:{d['time'][4:]}",
                              Text(str(d["snr"]), justify="right"),
                              Text(f"{d['dt']:.1f}", justify="right"),
                              Text(str(d["freq"]), justify="right"),
                              Text(d["message"], style=style))
        if table.row_count and (at_end or not keep_end):
            table.move_cursor(row=table.row_count - 1)

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
