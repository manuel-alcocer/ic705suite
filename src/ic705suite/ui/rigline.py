"""The status line of the rig, shared by the dashboard and the rig applications."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ic705suite.i18n import _
from ic705suite.rig import bands
from ic705suite.rig.base import smeter_text


def smeter_bar(raw: int, cells: int = 15) -> tuple[str, str]:
    """(filled, empty) parts of an S-meter bar: 9 cells up to S9, one per 10 dB above."""
    filled = min(cells, round(raw / 120 * 9) if raw <= 120 else 9 + round((raw - 120) / 20))
    return "▮" * filled, "·" * (cells - filled)


class RigStatusLine(Static):
    """One line: state badges, rig, frequency, mode, S-meter, power and the link."""

    def __init__(self, link_description: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rig: dict = {}
        self.link_description = link_description
        self.extra: list[tuple[str, str]] = []

    def render(self) -> Text:
        rig = self.rig
        text = Text(no_wrap=True, overflow="ellipsis")

        def badge(label: str, style: str) -> None:
            text.append(f" {label} ", style=style)
            text.append(" ")

        if not rig:
            badge(_("NO RIG"), "bold white on grey35")
        elif not rig.get("connected"):
            badge(_("DISCONNECTED"), "bold white on red")
        if rig.get("transmitting"):
            badge(_("TX"), "bold white on red")
        elif rig and not rig.get("tx_enabled"):
            badge(_("RX ONLY"), "bold black on yellow")
        if rig.get("recording"):
            badge("● REC", "bold white on dark_red")
        start = len(text)

        def chunk(label: str, value: str, style: str = "bold white") -> None:
            if not value:
                return
            if len(text) > start:
                text.append(" · ", style="dim")
            if label:
                text.append(f"{label} ", style="dim")
            text.append(value, style=style)

        if rig:
            chunk("", rig.get("radio") or rig.get("model_name") or "", "bold cyan")
            chunk(_("QRG"), bands.format_frequency(rig.get("frequency")), "bold yellow")
            chunk(_("MODE"), rig.get("mode") or "-", "bold green")
            smeter = rig.get("smeter", 0)
            if len(text) > start:
                text.append(" · ", style="dim")
            filled, empty = smeter_bar(smeter)
            text.append(f"{smeter_text(smeter):<6}", style="bold white")
            text.append(filled, style="green")
            text.append(empty, style="dim")
            if rig.get("power") is not None:
                chunk(_("PWR"), f"{rig['power']}%", "white")
        for label, value in self.extra:
            chunk(label, value, "white")
        chunk(_("LINK"), self.link_description, "bold blue")
        return text
