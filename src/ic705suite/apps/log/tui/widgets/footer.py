"""Bottom line with aggregate counters and the UTC clock."""

from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ...core.dto import LogStats
from ...i18n import N_, _

#: Shown at the right edge, so the views and the way out stay discoverable.
#: Translated where it is drawn.
HELP_HINT = N_(
    "F1 Log · F2 Inventory · F3 Address book · F4 Profiles · F5 Repeaters · "
    "F8 Tools · F9 Settings · Ctrl+Q Quit"
)

#: What is left of it when the terminal cannot hold the whole hint. Every
#: key keeps at least a short name of where it leads.
SHORT_HINTS = (
    N_(
        "F1 Log · F2 Inventory · F3 Address book · F4 Profiles · F5 Repeaters · "
        "F8 Tools · F9 Settings"
    ),
    N_("F1 Log F2 Inv F3 Book F4 Prof F5 Rptr F8 Tools F9 Set"),
)

#: The keys in a hint, drawn brighter than the names they lead to.
_KEY = re.compile(r"F\d+|Ctrl\+\w")


class StatsFooter(Static):
    """Totals for the current log plus a live UTC clock.

    UTC is what goes into the log and into every ADIF export, so showing local
    time here would invite mistakes; with a time zone set (F9) the local time
    follows the UTC one, smaller.
    """

    stats: reactive[LogStats | None] = reactive(None)
    #: IANA zone of the local time shown next to UTC; empty shows UTC only.
    timezone: reactive[str] = reactive("")

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Text:
        # The hint must keep its names: on a narrow terminal the counters
        # close up, drop the bands and then the date before it loses them.
        width = self.size.width
        for gap, bands, date in (("   ", True, True), ("  ", False, True), ("  ", False, False)):
            text = self._counters(gap, bands, date)
            for hint in (_(HELP_HINT), *(_(short) for short in SHORT_HINTS)):
                padding = width - text.cell_len - len(hint) - 1
                if width and padding >= 2:
                    text.append(" " * padding)
                    _append_hint(text, hint)
                    text.append(" ")
                    return text
        # Not even that fits: the shortest hint, cut at the right edge.
        text.append("  ")
        _append_hint(text, _(SHORT_HINTS[-1]))
        text.truncate(width)
        return text

    def _counters(self, gap: str, bands: bool, date: bool) -> Text:
        now = dt.datetime.now(dt.timezone.utc)
        text = Text(no_wrap=True)
        clock = f"{now:%Y-%m-%d %H:%M:%S}" if date else f"{now:%H:%M:%S}"
        text.append(f" {clock} UTC ", style="bold black on rgb(120,180,255)")
        local = self._local(now)
        if local is not None:
            text.append(f" {local:%H:%M} {local:%Z}", style="dim")

        stats = self.stats
        if stats is not None:

            def chunk(label: str, value: object) -> None:
                text.append(gap)
                text.append(f"{label} ", style="dim")
                text.append(str(value), style="bold")

            chunk("QSO", stats.total)
            chunk(_("today"), stats.today)
            chunk(_("unique"), stats.unique_calls)
            chunk(_("countries"), stats.countries)
            if bands and stats.by_band:
                top = sorted(stats.by_band.items(), key=lambda item: -item[1])[:3]
                text.append(gap)
                text.append(
                    " ".join(f"{band}:{count}" for band, count in top), style="dim yellow"
                )
        return text

    def _local(self, now: dt.datetime) -> dt.datetime | None:
        if not self.timezone:
            return None
        try:
            return now.astimezone(ZoneInfo(self.timezone))
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def on_resize(self) -> None:
        self.refresh()


def _append_hint(text: Text, hint: str) -> None:
    """Append the hint with its keys bold and the rest dim."""
    position = 0
    for match in _KEY.finditer(hint):
        text.append(hint[position : match.start()], style="dim")
        text.append(match.group(), style="bold")
        position = match.end()
    text.append(hint[position:], style="dim")
