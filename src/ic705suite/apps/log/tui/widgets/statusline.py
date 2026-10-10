"""The active configuration line at the top of the screen."""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ...core import bands, modes
from ...i18n import _


class StatusLine(Static):
    """One line summarising the configuration every new QSO inherits."""

    operator: reactive[str] = reactive("")
    repeater: reactive[str] = reactive("")
    band: reactive[str] = reactive("")
    freq_hz: reactive[int | None] = reactive(None)
    mode: reactive[str] = reactive("")
    digital_summary: reactive[str] = reactive("")
    station: reactive[str] = reactive("")
    profile: reactive[str] = reactive("")
    #: A demo run: nothing typed is kept, and the line says so first.
    demo: reactive[bool] = reactive(False)

    def render(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        if self.demo:
            text.append(f" {_('DEMO')} ", style="bold black on yellow")
            text.append(" ")
        # Where the chunks start: the separator goes between them only.
        start = len(text)

        def chunk(label: str, value: str, style: str = "bold white") -> None:
            if not value:
                return
            if len(text) > start:
                text.append(" · ", style="dim")
            if label:
                text.append(f"{label} ", style="dim")
            text.append(value, style=style)

        chunk("OP", self.operator or _("no operator"), "bold cyan")
        chunk(_("BAND"), self.band or "-", "bold yellow")
        chunk("QRG", bands.format_frequency(self.freq_hz), "bold yellow")
        # Working through a repeater changes where the signal goes, so it is
        # shown right next to the frequency it applies to.
        chunk(_("VIA"), self.repeater, "bold bright_red")

        mode = modes.get(self.mode)
        mode_style = "bold magenta" if mode and mode.is_digital else "bold green"
        chunk(_("MODE"), self.mode or "-", mode_style)
        chunk("", self.digital_summary, "magenta")
        chunk(_("RIG"), self.station, "white")
        chunk(_("PROFILE"), self.profile or _("(none)"), "bold blue")
        return text
