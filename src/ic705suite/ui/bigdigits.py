"""Large digits that every terminal can draw.

Textual's Digits are drawn with light and heavy line pieces (╺ ━ ┓ ╹ ...) that the Linux
console fonts lack. In console mode these digits are drawn instead from a 3x5 pixel font: with
the half blocks ▀ ▄ █ (two pixel rows per character cell, three rows high) when the font has
them, else with the full block alone (five rows high). Elsewhere they are Textual's own.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Digits

from ic705suite.ui import glyphs

FONT = {
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", "..#", "..#", "..#"),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
    "-": ("...", "...", "###", "...", "..."),
    "+": ("...", ".#.", "###", ".#.", "..."),
    ":": (".", "#", ".", "#", "."),
    ".": (".", ".", ".", ".", "#"),
    " ": ("..", "..", "..", "..", ".."),
}
HALVES = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}


def block_rows(text: str, halves: bool = True) -> list[str]:
    """The character rows of `text` in the block font (one blank column between): three
    rows of half blocks, or five rows of full blocks (`halves` False)."""
    full = "█" if glyphs.has("█") else "#"
    rows = ["", "", ""] if halves else [""] * 5
    for i, char in enumerate(text):
        pixels = FONT.get(char, FONT[" "])
        gap = " " if i < len(text) - 1 else ""
        if not halves:
            for r, line in enumerate(pixels):
                rows[r] += line.replace("#", full).replace(".", " ") + gap
            continue
        pixels = (*pixels, "." * len(pixels[0]))           # sixth pixel row: empty
        for r in range(3):
            top, bottom = pixels[2 * r], pixels[2 * r + 1]
            rows[r] += "".join(HALVES[(a == "#", b == "#")] for a, b in zip(top, bottom)) + gap
    return rows


def console_rows(text: str) -> list[str]:
    """`text` in the block font that suits the console font."""
    return block_rows(text, halves=glyphs.has("▀") and glyphs.has("▄"))


def height() -> int:
    """Rows taken by the digits on this terminal."""
    return len(console_rows("0")) if glyphs.CONSOLE else 3


class BigDigits(Digits):
    """Textual's Digits, or the block font on a plain console."""

    def render(self):
        if not glyphs.CONSOLE:
            return super().render()
        return Text("\n".join(console_rows(self.value)), no_wrap=True)

    def get_content_width(self, container, viewport) -> int:
        if not glyphs.CONSOLE:
            return super().get_content_width(container, viewport)
        return len(console_rows(self.value)[0])

    def get_content_height(self, container, viewport, width: int) -> int:
        return height()
