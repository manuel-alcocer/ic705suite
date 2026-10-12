"""Antenna tuning in the terminal: the same sweeps as the phone page, on the shared station.

Several antennas can be kept (A): the bands show where the active one was left on each (its
saved settings), and the sweeps, the advice and the saved positions are that antenna's.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from ic705suite.apps.swr.web import antenna_url
from ic705suite.i18n import N_, _, render
from ic705suite.rig import bands
from ic705suite.station.links import Link
from ic705suite.ui import glyphs
from ic705suite.ui.rigline import RigStatusLine
from ic705suite.ui.widgets import ChoiceScreen, NameScreen, clock_footer, css

HINT = N_("F2-F9 Band · ↑↓ Band or preset · P Presets · H History · A Antennas · "
          "R Remote control · N Noise sweep (RX) · S SWR sweep (TX) · C Save position · "
          "Esc Stop/Back")
SHORT_HINT = N_("F2-F9 Band · ↑↓ · P Presets · H History · N Noise · S SWR · Esc Back")
BAR = 40


def noise_request(band: dict) -> dict:
    """Off-air listening may go beyond the band edges: widen by half the range each side.
    `band` is a row of the list: a band, or a preset (its band, its own target)."""
    half = (band["end"] - band["start"]) // 2
    start, stop = max(band["start"] - half, 1_000_000), band["end"] + half
    step = max(band["step"], round((stop - start) / 40 / 1000) * 1000)
    return {"kind": "noise", "band": band.get("band", band["name"]), "target": band["target"],
            "start": start, "stop": stop, "step": step, "preset": band.get("preset")}


def swr_request(band: dict) -> dict:
    return {"kind": "swr", "band": band.get("band", band["name"]), "target": band["target"],
            "start": band["start"], "stop": band["end"], "step": band["step"],
            "preset": band.get("preset")}


def row_label(row: dict) -> str:
    """A band by its name; a preset by its name and its band."""
    return f"{row['name']} ({row['band']})" if row.get("preset") else row["name"]


def saved_setting(settings: dict, row: dict) -> dict:
    from ic705suite.apps.swr.service import setting_key

    return settings.get(setting_key(row.get("band", row["name"]), row.get("preset")), {})


def advice(sweep: dict) -> tuple[str, str, str]:
    """(verdict, style, advice) for a finished sweep, as the phone page words it."""
    a = sweep.get("analysis")
    if sweep.get("error") and sweep["error"] != "stopped":
        return _("Sweep failed"), "bold red", sweep["error"]
    if not a:
        return (_("Sweep stopped") if sweep.get("error") == "stopped" else _("Not enough data"),
                "bold yellow", "")
    is_swr = sweep.get("kind") == "swr"
    if not a["confident"]:
        verdict, style = _("Noise without a clear peak"), "bold yellow"
        text = _("The noise is almost flat or stations dominate it: the resonance cannot be "
                 "told. Try at night, on a quieter band, or make an SWR sweep.")
    elif a["tuned"]:
        verdict, style, text = _("Tuned! Leave the coil there"), "bold green", ""
    elif a["centered"]:
        verdict, style = _("Resonance in place"), "bold yellow"
        text = _("The frequency is right but the lowest SWR is {swr:.2f}: it is not the coil "
                 "(ground, radials, connections).").format(swr=a["best_value"])
    elif not a["move"]:
        # This antenna's adjustment direction is not known yet: say where the resonance goes.
        verdict = _("Raise the resonance") if a.get("shift") == "up" else _("Lower the resonance")
        style = "bold red"
        where = _("below") if a["delta_khz"] < 0 else _("above")
        text = _("The resonance is {khz:.0f} kHz {where} the target. Which way to turn the "
                 "adjustment is not known yet for this antenna: save its position after each "
                 "change (C) and the advice will learn it.").format(
            khz=abs(a["delta_khz"]), where=where)
    else:
        verdict = _("Lower the coil") if a["move"] == "lower" else _("Raise the coil")
        style = "bold red"
        where = _("below") if a["delta_khz"] < 0 else _("above")
        units = (_("quite a lot") if a["at_edge"] else
                 _("about {units} divisions").format(units=a["units"]) if a["units"]
                 else _("a little"))
        number = _("a lower number") if a["move"] == "lower" else _("a higher number")
        text = _("The resonance is {khz:.0f} kHz {where} the target: move to {number} "
                 "({units}) and sweep again.").format(khz=abs(a["delta_khz"]), where=where,
                                                     number=number, units=units)
        if a["far"]:
            text = _("SWR above 3 over the whole sweep: the antenna does not resonate on this "
                     "band. Move the coil to {number} (check it is on this band's position)."
                     ).format(number=number)
        elif a["at_edge"]:
            text += " " + _("The extreme is at the edge of the sweep: the real resonance is "
                            "further out.")
    if sweep.get("warning") == "no_power":
        text += " " + _("The radio was hardly putting out power: the SWR read is not reliable.")
    if not is_swr and a["confident"]:
        text += " " + _("(Noise sweep: a hint only; confirm with an SWR sweep.)")
    return verdict, style, text


class SweepPanel(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.state: dict = {}

    def render(self) -> Text:
        text = Text()
        state = self.state
        sweep = state.get("sweep") or {}
        if not state:
            text.append(_("waiting for the core..."), style="dim")
            return text
        # SWR sweeps transmit: they need --tx and the remote control switched on here.
        text.append(_("Remote control") + ": ", style="dim")
        if state.get("remote"):
            text.append(" " + _("ON") + " ", style=glyphs.style("bold white on red"))
            text.append("  " + _("the phone can sweep; SWR sweeps allowed") + "  "
                        + _("(R switches it off)") + "\n", style="white")
        else:
            text.append(" " + _("OFF") + " ", style=glyphs.style("bold white on blue"))
            text.append("  " + _("the phone only watches; no SWR sweeps") + "  "
                        + _("(R switches it on)") + "\n", style="white")
            if state.get("remote_password"):
                text.append(_("The phone page can switch it on with the password.") + "\n",
                            style="dim")
        if not state.get("allow_tx"):
            text.append(_("Receive only: noise sweeps only (start with --tx for SWR sweeps)") + "\n",
                        style="bold yellow")
        url = antenna_url(state.get("url"), state.get("port"))
        if url:
            text.append(_("Phone page") + ": ", style="dim")
            text.append(f"{url}\n", style="white")
        if not sweep.get("kind"):
            text.append("\n" + _("Choose a band and start a sweep."), style="dim")
            return text
        points = sweep.get("points") or []
        is_swr = sweep["kind"] == "swr"
        title = _("SWR sweep") if is_swr else _("Noise sweep")
        text.append(f"\n{title} {sweep.get('band', '')}", style="bold cyan")
        text.append(f"  {len(points)}/{sweep.get('total', 0)}", style="white")
        if sweep.get("running"):
            text.append("  " + _("running..."), style="bold yellow")
        text.append("\n")
        if not sweep.get("running"):
            verdict, style, words = advice(sweep)
            text.append(f"{verdict}\n", style=style)
            if words:
                text.append(words + "\n", style="white")
            a = sweep.get("analysis")
            if a:
                text.append(f"{_('Resonance')} ", style="dim")
                text.append(f"{bands.format_frequency(a['resonance'])} MHz", style="bold white")
                text.append(f"   {_('Lowest SWR') if is_swr else _('Noise peak')} ", style="dim")
                text.append(f"{a['best_value']:.2f}" if is_swr else f"{a['best_value']:.0f}",
                            style="bold white")
                text.append(f"   {_('Off target')} ", style="dim")
                text.append(f"{a['delta_khz']:+.0f} kHz", style="bold white")
            text.append("\n")
        text.append_text(sweep_bars(sweep["kind"], points))
        return text


def sweep_bars(kind: str, points: list[dict], target: int | None = None) -> Text:
    """A sweep as one bar per frequency: the SWR (green, yellow, red) or the noise; the
    best point bold, and the target marked when given."""
    text = Text()
    is_swr = kind == "swr"
    values = [p["v"] for p in points]
    if not values:
        return text
    lo, hi = (1.0, max(3.0, max(values))) if is_swr else (min(values), max(values) or 1)
    span = (hi - lo) or 1
    best = min(values) if is_swr else max(values)
    nearest = min(points, key=lambda p: abs(p["f"] - target))["f"] if target else None
    for p in points:
        cells = round((p["v"] - lo) / span * BAR)
        style = "bold green" if p["v"] == best else "green" if is_swr and p["v"] <= 1.5 \
            else "yellow" if is_swr and p["v"] <= 2 else "red" if is_swr else "cyan"
        text.append(f"{bands.format_frequency(p['f'])} ",
                    style="bold cyan" if p["f"] == nearest else "dim")
        text.append("█" * max(1, cells), style=style)
        text.append(f" {p['v']:.2f}" if is_swr else f" {p['v']:.0f}", style="white")
        text.append(" ◀\n" if p["f"] == nearest else "\n", style="bold cyan")
    return text


class SwrApp(App[int]):
    CSS = css("ic705suite.apps.swr")
    TITLE = "ic705suite Antenna"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("n", "sweep('noise')", show=False),
        Binding("s", "sweep('swr')", show=False),
        Binding("c", "final", show=False),
        Binding("a", "antennas", show=False),
        Binding("r", "remote", show=False),
        # One function key per band (the list order); ↑↓ go through the bands and presets.
        *(Binding(f"f{n}", f"band({n - 2})", show=False, priority=True) for n in range(2, 10)),
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        Binding("p", "presets", show=False),
        Binding("h", "history", show=False),
        Binding("escape", "back", show=False),
        Binding("ctrl+q", "leave", show=False, priority=True),
    ]

    def __init__(self, link: Link, owns_link: bool = False) -> None:
        super().__init__()
        self.link = link
        self.owns_link = owns_link
        self.bands: list[dict] = []        # the bands, then the presets: the list's rows
        self.state: dict = {}
        self.antenna: dict = {}

    def compose(self) -> ComposeResult:
        yield RigStatusLine(self.link.description, id="statusline")
        with Horizontal(id="top"):
            with Vertical(id="bands-frame", classes="frame"):
                yield DataTable(id="bands", cursor_type="row")
            yield SweepPanel(id="sweep", classes="frame")
        yield Static(id="message")
        yield Static(id="stats")

    def on_mount(self) -> None:
        self.query_one("#bands-frame").border_title = _("Band")
        self.query_one("#sweep").border_title = _("Antenna")
        table = self.query_one("#bands", DataTable)
        table.add_columns(_("Key"), _("Band"), _("Target"), _("Position"), _("Resonance"),
                          _("SWR"))
        table.can_focus = False
        self.set_interval(1.0, self._footer)
        self._footer()
        self.link.attach(self._on_event)
        self.run_worker(self._start(), exclusive=True)

    async def _start(self) -> None:
        try:
            await self.link.connect()
            await self._load_rows()
            self._apply_state(await self.link.call("swr.state"))
        except Exception as exc:  # noqa: BLE001 - shown on screen
            self._message("error", _("cannot connect: {error}").format(error=exc))
            return
        await self._fill_bands()
        table = self.query_one("#bands", DataTable)
        rig_band = (self.link.latest.get("rig") or {}).get("band")
        names = [b["name"] for b in self.bands]
        if rig_band in names:
            table.move_cursor(row=names.index(rig_band))

    async def _load_rows(self) -> None:
        self.bands = (await self.link.call("swr.bands")).get("bands", []) + \
            (await self.link.call("swr.presets")).get("presets", [])

    async def _fill_bands(self) -> None:
        """The bands and presets with the active antenna's saved setting on each."""
        reply = await self.link.call("swr.antennas")
        self.antenna = next((a for a in reply.get("antennas", []) if a["active"]), {})
        settings = self.antenna.get("settings", {})
        table = self.query_one("#bands", DataTable)
        row = table.cursor_row if table.row_count else 0
        table.clear()
        for i, band in enumerate(self.bands):
            saved = saved_setting(settings, band)
            key = f"F{i + 2}" if i < 8 and not band.get("preset") else ""
            table.add_row(key, row_label(band), bands.format_frequency(band["target"]),
                          f"{saved['coil']:g}" if saved else "",
                          bands.format_frequency(saved["resonance"]) if saved.get("resonance")
                          else "",
                          f"{saved['best']:.2f}" if saved.get("best") is not None else "")
        if self.bands:
            table.move_cursor(row=min(row, len(self.bands) - 1))
        self.query_one("#sweep").border_title = \
            f"{_('Antenna')} · {self.antenna.get('name', '')}"

    def _footer(self) -> None:
        stats = self.query_one("#stats", Static)
        stats.update(clock_footer(stats.size.width, [], [_(HINT), _(SHORT_HINT)]))

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "swr":
            self._apply_state(event)
        elif kind == "rig":
            status = self.query_one("#statusline", RigStatusLine)
            status.rig = event
            status.refresh()
        elif kind in ("info", "error"):
            self._message(kind, render(event))

    def _apply_state(self, state: dict) -> None:
        if "sweep" not in state:
            return
        self.state = state
        panel = self.query_one("#sweep", SweepPanel)
        panel.state = state
        panel.refresh()

    def _message(self, kind: str, text: str) -> None:
        self.query_one("#message", Static).update(
            Text(" " + text[:200], style="bold red" if kind == "error" else "cyan"))

    def action_band(self, index: int) -> None:
        if 0 <= index < len(self.bands):
            self.query_one("#bands", DataTable).move_cursor(row=index)

    def action_move(self, step: int) -> None:
        table = self.query_one("#bands", DataTable)
        if self.bands:
            table.move_cursor(row=max(0, min(len(self.bands) - 1, table.cursor_row + step)))

    def _band(self) -> dict | None:
        table = self.query_one("#bands", DataTable)
        if not self.bands or not 0 <= table.cursor_row < len(self.bands):
            return None
        return self.bands[table.cursor_row]

    async def action_sweep(self, kind: str) -> None:
        band = self._band()
        if band is None:
            return
        if kind == "swr" and not self.state.get("remote"):
            self._message("error", _("SWR sweeps need the remote control on: press R"))
            return
        request = noise_request(band) if kind == "noise" else swr_request(band)
        result = await self.link.call("swr.sweep", **request)
        if result.get("error"):
            self._message("error", result["error"])
        else:
            self._apply_state(result)

    def action_final(self) -> None:
        self.run_worker(self._final(), exclusive=True, group="final")

    async def _final(self) -> None:
        band = self._band()
        if band is None:
            return
        saved = saved_setting(self.antenna.get("settings", {}), band)
        coil = await self.push_screen_wait(NameScreen(
            _("Position on {band}").format(band=row_label(band)),
            _("Which number did the coil (or adjustment) of {antenna} end on?").format(
                antenna=self.antenna.get("name", _("the antenna"))),
            value=f"{saved['coil']:g}" if saved else "",
            help_text=_("Enter saves (with the last SWR sweep, if it was on this band) · "
                        "Esc cancels")))
        if not coil:
            return
        result = await self.link.call("swr.final", coil=coil, band=band.get("band", band["name"]),
                                      preset=band.get("preset"))
        if result.get("error"):
            self._message("error", result["error"])
            return
        self._message("info", _("position {coil} saved for {band}").format(
            coil=result["entry"]["coil"], band=row_label(band)))
        await self._fill_bands()

    def action_remote(self) -> None:
        self.run_worker(self._remote(), exclusive=True, group="remote")

    async def _remote(self) -> None:
        """Switch the remote control on (after asking) or off (at once)."""
        if self.state.get("remote"):
            self._apply_state(await self.link.call("swr.remote", on=False))
            return
        warning = _("While it is on, the phone page can start sweeps and SWR sweeps can run "
                    "(they transmit a carrier at low power). It stays on until you switch it "
                    "off here or the suite ends.")
        if not self.state.get("allow_tx"):
            warning += " " + _("Transmission is disabled (no --tx): SWR sweeps will still be "
                               "refused.")
        answer = await self.push_screen_wait(ChoiceScreen(
            _("Remote control"), warning, [("on", _("Switch it on")), ("no", _("Leave it off"))]))
        if answer == "on":
            self._apply_state(await self.link.call("swr.remote", on=True))

    def action_presets(self) -> None:
        self.run_worker(self._presets(), exclusive=True, group="presets")

    async def _presets(self) -> None:
        from ic705suite.apps.swr.antennas import PresetScreen

        await self.push_screen_wait(PresetScreen(self.link))
        await self._load_rows()
        await self._fill_bands()

    def action_history(self) -> None:
        self.run_worker(self._history(), exclusive=True, group="history")

    async def _history(self) -> None:
        from ic705suite.apps.swr.history import HistoryScreen

        band = self._band()
        if band is None:
            return
        await self.push_screen_wait(HistoryScreen(self.link, band, row_label(band)))
        await self._fill_bands()          # a position changed or deleted moves the saved one

    def action_antennas(self) -> None:
        self.run_worker(self._antennas(), exclusive=True, group="antennas")

    async def _antennas(self) -> None:
        from ic705suite.apps.swr.antennas import AntennaScreen

        await self.push_screen_wait(AntennaScreen(self.link))
        await self._fill_bands()
        self._apply_state(await self.link.call("swr.state"))

    async def action_back(self) -> None:
        if (self.state.get("sweep") or {}).get("running"):
            await self.link.call("swr.stop")
            return
        self.action_leave()

    def action_leave(self) -> None:
        self.run_worker(self._leave(), exclusive=True, group="leave")

    async def _leave(self) -> None:
        # A sweep keeps running in the core; the phone page and the dashboard still see it.
        if self.owns_link:
            await self.link.close()
        else:
            self.link.detach()
        self.exit(0)
