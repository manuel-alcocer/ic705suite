"""The card of a logged QSO, opened with V while browsing the log.

Unlike the other panels it does not cover the whole log: it floats in the
middle of it, small, so the rows around stay in view and it reads as a
closer look at one of them. Esc, Enter, V or Q close it; the arrows scroll
it when it is longer than the log is high.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, Static

from ...core import bands, modes
from ...core.dto import PartRow, QsoCard
from ...i18n import N_, _
from .base import PanelScreen

#: Width of the labels on the left of each line.
LABEL_WIDTH = 13

#: Widest the card gets, and what it leaves free on each side of the log.
MAX_WIDTH = 92
MARGIN = 4


class QsoCardScreen(PanelScreen[None]):
    """Every detail of one QSO, centred over the log."""

    BINDINGS = [
        Binding("escape", "close", N_("Close")),
        Binding("enter", "close", N_("Close"), show=False),
        Binding("v", "close", N_("Close"), show=False),
        Binding("q", "close", N_("Close"), show=False),
    ]

    def __init__(self, card: QsoCard) -> None:
        super().__init__()
        self.card = card
        self.body = render_card(card)

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal qso-card"):
            yield Label(self.card.row.call, classes="modal-title")
            with VerticalScroll(classes="qso-card-body"):
                yield Static(self.body)

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one(".qso-card-body", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def _fit_to_panel(self) -> None:
        """Centre the card in the log, as tall as its lines, never taller."""
        try:
            frame = self.app.screen_stack[0].query_one("#log-frame")
            panel = self.query(".modal").first()
        except Exception:  # noqa: BLE001 - nothing to fit, e.g. during teardown
            return
        region = frame.region
        width = max(20, min(MAX_WIDTH, region.width - 2 * MARGIN))
        # Two rows of frame around the lines.
        height = max(5, min(region.height - 2, len(self.body.plain.splitlines()) + 2))
        panel.styles.offset = (
            region.x + (region.width - width) // 2,
            region.y + (region.height - height) // 2,
        )
        panel.styles.width = width
        panel.styles.height = height
        panel.styles.max_width = None
        panel.styles.max_height = None


def render_card(card: QsoCard) -> Text:
    """The lines of the card: the station worked, the QSO, ours, the setup."""
    row = card.row
    text = Text()

    def section(title: str) -> None:
        if text.plain:
            text.append("\n")
        text.append(f"{title}\n", style="bold cyan")

    def line(label: str, value: str, style: str = "") -> None:
        if not value:
            return
        text.append(f"{label:<{LABEL_WIDTH}}", style="dim")
        text.append(f"{value}\n", style=style)

    def join(*parts: str) -> str:
        return " · ".join(part for part in parts if part)

    # -- The station worked.
    section(_("CONTACT"))
    line(_("Callsign"), row.call, "bold")
    line(_("Name"), row.name)
    line(_("QTH"), join(row.qth, row.gridsquare))
    line(_("Country"), _(row.country) if row.country else "")
    contact = card.contact
    if contact is not None:
        place = contact.city if contact.city == contact.state else join(contact.city, contact.state)
        line(
            _("Address book"),
            join(
                contact.full_name,
                place,
                _(contact.country) if contact.country else "",
                contact.gridsquare,
            ),
        )
        line(_("DMR ID"), str(contact.dmr_id) if contact.dmr_id else "")
        line(_("Email"), contact.email)
        line(_("Book notes"), contact.notes, "italic")
    if row.name_drift:
        line("", _("d: the address book says «{name}»").format(name=row.book_name), "bold yellow")

    # -- The QSO itself.
    section(_("QSO"))
    kind = _("manual") if row.is_manual else _("automatic")
    line(_("Date"), f"{row.qso_utc:%Y-%m-%d %H:%M:%S} UTC  ({kind})", "bold")
    frequency = bands.format_frequency(row.freq_hz) if row.freq_hz else ""
    if row.freq_tx_hz and row.freq_tx_hz != row.freq_hz:
        frequency = join(frequency, f"TX {bands.format_frequency(row.freq_tx_hz)}")
    line(_("Frequency"), join(row.band, frequency), "yellow")
    line(_("Mode"), row.mode, "green")
    line(_("Reports"), join(
        _("sent {rst}").format(rst=row.rst_sent) if row.rst_sent else "",
        _("received {rst}").format(rst=row.rst_rcvd) if row.rst_rcvd else "",
    ))
    line(_("Repeater"), card.repeater or row.repeater_call, "bold bright_red")
    line(
        _("Digital"),
        modes.status_summary(row.digital_data or {}, has_repeater=bool(row.repeater_call)),
        "magenta",
    )
    line(_("Notes"), row.comment, "italic")

    # -- Our station.
    section(_("MY STATION"))
    line(_("Operator"), join(row.operator_callsign, card.operator_name), "bold")
    line(_("Location"), join(card.operator_qth, card.operator_gridsquare))
    line(_("Power"), f"{card.power_w} W" if card.power_w else "")

    # -- The setup, when the QSO has one; else the radio and antenna it kept.
    if card.setup_name:
        section(_("SETUP «{name}»").format(name=card.setup_name))
        if row.equipment_mismatch:
            line("", _("E: the frequency does not fit this setup"), "bold red")
        _parts(text, _("Radios"), card.radios)
        _parts(text, _("Antennas"), card.antennas)
        _parts(text, _("Supplies"), card.supplies)
        line(_("Notes"), card.setup_notes, "italic")
    elif card.station or card.antenna:
        section(_("RADIO"))
        _parts(text, _("Radio"), (card.station,) if card.station else ())
        _parts(text, _("Antenna"), (card.antenna,) if card.antenna else ())
    else:
        section(_("SETUP"))
        line("", _("This QSO has no setup."), "dim italic")

    text.rstrip()
    return text


def _parts(text: Text, label: str, parts: tuple[PartRow, ...]) -> None:
    """One line per part, the label on the first one only."""
    for index, part in enumerate(parts):
        text.append(f"{label if index == 0 else '':<{LABEL_WIDTH}}", style="dim")
        if part.code:
            text.append(f"{part.code} ", style="dim")
        text.append(part.name, style="bold")
        if part.details:
            text.append(f"  {part.details}", style="dim")
        text.append("\n")
