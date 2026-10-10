"""Large digits that every terminal can draw.

Textual's Digits are drawn with light and heavy line pieces (╺ ━ ┓ ╹ ...) that the Linux
console fonts lack. In console mode these digits are drawn instead from a 3x5 pixel font made
of the half blocks ▀ ▄ █ (two pixel rows per character cell), which every console font has.
Elsewhere they are Textual's own.
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


def block_rows(text: str) -> list[str]:
    """The three character rows of `text` in the block font (one blank column between)."""
    rows = ["", "", ""]
    for i, char in enumerate(text):
        pixels = FONT.get(char, FONT[" "])
        pixels = (*pixels, "." * len(pixels[0]))           # sixth pixel row: empty
        gap = " " if i < len(text) - 1 else ""
        for r in range(3):
            top, bottom = pixels[2 * r], pixels[2 * r + 1]
            rows[r] += "".join(HALVES[(a == "#", b == "#")] for a, b in zip(top, bottom)) + gap
    return rows


class BigDigits(Digits):
    """Textual's Digits, or the block font on a plain console."""

    def render(self):
        if not glyphs.CONSOLE:
            return super().render()
        return Text("\n".join(block_rows(self.value)), no_wrap=True)

    def get_content_width(self, container, viewport) -> int:
        if not glyphs.CONSOLE:
            return super().get_content_width(container, viewport)
        return len(block_rows(self.value)[0])
