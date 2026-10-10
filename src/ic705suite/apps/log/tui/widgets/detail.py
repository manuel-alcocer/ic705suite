"""Detail pane: everything the history row has no room for.

The table shows what fits on one line; this shows the rest of whatever the
cursor is on. On the insert row it shows what a new QSO is about to inherit
instead, so the pane is useful in both states rather than blank half the time.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ...core import bands, modes
from ...core.dto import QsoRow
from ...core.state import SessionState
from ...i18n import _


class DetailPanel(Static):
    """Three lines describing the selected QSO, or the one being written."""

    def on_mount(self) -> None:
        self.show_nothing()

    # ----------------------------------------------------------- selected --
    def show_qso(self, row: QsoRow) -> None:
        """Expand a logged QSO: everything the table had to leave out."""
        text = Text(no_wrap=True, overflow="ellipsis")

        kind = ("MANUAL", "bold green") if row.is_manual else ("AUTO", "bold yellow")
        text.append(row.call, style="bold white")
        _chunk(text, row.name)
        _chunk(text, _place(row))
        _chunk(text, _(row.country) if row.country else "", "dim")
        text.append("   ")
        text.append(f" {kind[0]} ", style=kind[1])
        text.append("\n")

        text.append(f"{row.qso_utc:%Y-%m-%d %H:%M:%S} UTC", style="bold")
        _chunk(text, row.band, "yellow")
        _chunk(text, _frequencies(row), "yellow")
        _chunk(text, row.mode, "green")
        _chunk(text, f"{row.rst_sent}/{row.rst_rcvd}".strip("/"))
        if row.repeater_call:
            _chunk(text, _("via {call}").format(call=row.repeater_call), "bold bright_red")
        text.append("\n")

        third = Text(no_wrap=True, overflow="ellipsis")
        if row.operator_callsign:
            _chunk(third, f"Op {row.operator_callsign}", "cyan")
        if row.equipment_name:
            _chunk(third, _("Setup {name}").format(name=row.equipment_name))
            if row.equipment_mismatch:
                _chunk(third, _("E: the frequency does not fit this setup"), "bold red")
        elif row.station_name:
            _chunk(third, row.station_name)
        if row.name_drift:
            _chunk(
                third,
                _("d: the address book says «{name}»").format(name=row.book_name),
                "bold yellow",
            )
        digital = modes.status_summary(row.digital_data or {}, has_repeater=bool(row.repeater_call))
        if digital:
            _chunk(third, digital, "magenta")
        if row.comment:
            _chunk(third, f"«{row.comment}»", "italic")
        if not third.plain:
            third.append("—", style="dim")
        text.append(third)

        self.update(text)

    # -------------------------------------------------------------- insert --
    def show_session(
        self,
        state: SessionState,
        *,
        operator: str = "",
        station: str = "",
        stats_line: str = "",
    ) -> None:
        """Show what a QSO written now would inherit."""
        text = Text(no_wrap=True, overflow="ellipsis")

        text.append(_("NEW CONTACT"), style="bold green")
        text.append("   ")
        text.append(
            _("the UTC date and time are set when you press Enter"), style="dim italic"
        )
        text.append("\n")

        second = Text(no_wrap=True, overflow="ellipsis")
        _chunk(second, operator or _("no operator"), "bold cyan")
        _chunk(second, state.band or _("no band"), "bold yellow")
        _chunk(second, bands.format_frequency(state.freq_hz), "yellow")
        if state.via_repeater:
            _chunk(second, _("via {call}").format(call=state.repeater_call), "bold bright_red")
            if state.freq_tx_hz:
                _chunk(second, f"TX {bands.format_frequency(state.freq_tx_hz)}", "dim")
        _chunk(second, state.mode or _("no mode"), "bold green")
        digital = modes.status_summary(state.digital_data, has_repeater=state.via_repeater)
        if digital:
            _chunk(second, digital, "magenta")
        text.append(second)
        text.append("\n")

        third = Text(no_wrap=True, overflow="ellipsis")
        if station:
            _chunk(third, station)
        _chunk(
            third,
            _("default report {rst}").format(rst=modes.default_rst(state.mode)),
            "dim",
        )
        if state.autofill_from_book:
            _chunk(third, _("name and QTH are filled in from the address book"), "dim")
        if stats_line:
            _chunk(third, stats_line, "dim")
        text.append(third)

        self.update(text)

    def show_lines(self, lines: tuple[str, ...], *, locked: bool = False) -> None:
        """Up to three plain lines, the first one bold: used by the inventory view."""
        text = Text(no_wrap=True, overflow="ellipsis")
        for index, line in enumerate(lines[:3]):
            if index:
                text.append("\n")
            style = "bold white" if index == 0 else "white"
            if locked and index == 2:
                style = "italic dim"
            text.append(line, style=style)
        self.update(text)

    def show_nothing(self) -> None:
        self.update(Text("—", style="dim"))


def _chunk(text: Text, value: str, style: str = "white") -> None:
    """Append a value preceded by a separator, skipping empties."""
    if not value:
        return
    if text.plain:
        text.append(" · ", style="dim")
    text.append(value, style=style)


def _place(row: QsoRow) -> str:
    """QTH with the locator in brackets, when both are known."""
    if row.qth and row.gridsquare:
        return f"{row.qth} ({row.gridsquare})"
    return row.qth or row.gridsquare


def _frequencies(row: QsoRow) -> str:
    """Working frequency, plus the transmit one when they differ."""
    if not row.freq_hz:
        return ""
    rendered = bands.format_frequency(row.freq_hz)
    if row.freq_tx_hz and row.freq_tx_hz != row.freq_hz:
        rendered += f" (TX {bands.format_frequency(row.freq_tx_hz)})"
    return rendered
