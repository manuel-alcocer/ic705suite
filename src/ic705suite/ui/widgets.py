"""Pieces every screen of the suite shares (HAMRLOG look): styles, dialogs, footer hints."""

from __future__ import annotations

import datetime as dt
import re
from importlib import resources

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ic705suite.i18n import _
from ic705suite.ui import glyphs

_KEY = re.compile(r"F\d+(?:-F\d+)?|Esc|PgUp/PgDn|Ctrl\+\w(?:/Ctrl\+\w)?|Enter|Shift\+Tab|Tab|Space|↑↓|←→")


def css(package: str | None = None, name: str = "styles.tcss") -> str:
    """The shared stylesheet followed by an application's own."""
    text = resources.files("ic705suite.ui").joinpath("common.tcss").read_text(encoding="utf-8")
    if package:
        text += "\n" + resources.files(package).joinpath(name).read_text(encoding="utf-8")
    return text


def append_keys(text: Text, hint: str) -> None:
    """Append a hint with its keys bold and the rest dim (as HAMRLOG's footer)."""
    position = 0
    for match in _KEY.finditer(hint):
        text.append(hint[position:match.start()], style="dim")
        text.append(match.group(), style="bold")
        position = match.end()
    text.append(hint[position:], style="dim")


def clock_footer(width: int, counters: list[tuple[str, object]], hints: list[str]) -> Text:
    """UTC clock, counters and the longest key hint that fits."""
    now = dt.datetime.now(dt.timezone.utc)
    text = Text(no_wrap=True)
    text.append(f" {now:%Y-%m-%d %H:%M:%S} UTC ", style=glyphs.style("bold black on rgb(120,180,255)"))
    for label, value in counters:
        text.append("   ")
        text.append(f"{label} ", style="dim")
        text.append(str(value), style="bold")
    for hint in hints:
        padding = width - text.cell_len - len(hint) - 1
        if padding >= 2:
            text.append(" " * padding)
            append_keys(text, hint)
            return text
    return text


class ChoiceScreen(ModalScreen[str]):
    """A question with a few answers; returns the chosen key ("" on Esc)."""

    BINDINGS = [Binding("escape", "dismiss('')", show=False)]

    def __init__(self, title: str, message: str, options: list[tuple[str, str]]) -> None:
        super().__init__()
        self.title_text, self.message, self.options = title, message, options

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal") as box:
            box.border_title = self.title_text
            yield Static(self.message, classes="modal-subtitle")
            with Horizontal(classes="modal-buttons"):
                for i, (key, label) in enumerate(self.options):
                    yield Button(label, id=f"opt-{key}", variant="primary" if i == 0 else "default")

    def on_mount(self) -> None:
        self.query(Button).first().focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id.removeprefix("opt-"))


class TextScreen(ModalScreen[None]):
    """Lines to read (a help page); Esc, Enter or F1 closes it."""

    BINDINGS = [Binding("escape", "dismiss", show=False), Binding("enter", "dismiss", show=False),
                Binding("f1", "dismiss", show=False)]

    def __init__(self, title: str, lines: list[str]) -> None:
        super().__init__()
        self.title_text, self.lines = title, lines

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal wide") as box:
            box.border_title = self.title_text
            yield Static(Text("\n".join(self.lines)), classes="modal-subtitle")
            yield Static(_("Esc, Enter or F1 closes"), classes="modal-help")


class NameScreen(ModalScreen[str | None]):
    """Ask for a name; returns the text (possibly empty) or None on Esc."""

    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, title: str, message: str, placeholder: str = "",
                 help_text: str | None = None, value: str = "") -> None:
        super().__init__()
        self.title_text, self.message, self.placeholder = title, message, placeholder
        self.help_text = help_text
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal") as box:
            box.border_title = self.title_text
            yield Static(self.message, classes="modal-subtitle")
            yield Input(self.value, placeholder=self.placeholder, id="name-input")
            yield Static(self.help_text if self.help_text is not None else
                         _("Enter accepts (empty: automatic name) · Esc skips this channel"),
                         classes="modal-help")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)
