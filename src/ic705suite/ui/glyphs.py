"""Glyphs for the terminal at hand: full Unicode, or what a plain console font has.

A terminal emulator (in a desktop session, over SSH) draws any Unicode character. The Linux
virtual console does not: its fonts hold 256 or 512 glyphs, roughly the IBM PC set (CP437:
box drawing, the blocks █ ▀ ▄ ▌ ▐, the shades ░ ▒ ▓, arrows) plus Latin letters, and only 16
colours (8 as background). Anything else comes out as a diamond or a question mark, so the
eighth blocks of the spectrum, the rounded borders, Textual's digits and the half-step colours
of the waterfall are lost there.

In console mode the suite draws with that set instead: the waterfall uses shades between the
16 colours, the spectrum half blocks, the frequency a block font; borders become square, and
whatever Textual or an application still sends outside the set is replaced, one character for
one, just before it reaches the terminal.

``configure()`` chooses the mode once, before the first application starts. Code that draws
reads ``glyphs.CONSOLE`` (and the constants below) at drawing time.
"""

from __future__ import annotations

import os

CONSOLE = False

# Glyphs used by the suite's own widgets, set by configure().
BAR = "▮"          # a filled cell of a meter or level bar
MARK = "┃"         # the dial marker, the centre of an offset bar
CARET = "▏"        # end of a value being typed

UNICODE_GLYPHS = {"BAR": "▮", "MARK": "┃", "CARET": "▏"}
CONSOLE_GLYPHS = {"BAR": "■", "MARK": "│", "CARET": "_"}

MODES = ("auto", "unicode", "console")

# The console's 16 colours as RGB. Named ANSI colours would go through Textual's theme (its
# "blue" is violet); these exact values come back to the same console colour.
VGA = {"black": "rgb(0,0,0)", "red": "rgb(170,0,0)", "green": "rgb(0,170,0)",
       "brown": "rgb(170,85,0)", "blue": "rgb(0,0,170)", "magenta": "rgb(170,0,170)",
       "cyan": "rgb(0,170,170)", "grey": "rgb(170,170,170)", "dark_grey": "rgb(85,85,85)",
       "bright_red": "rgb(255,85,85)", "bright_green": "rgb(85,255,85)",
       "yellow": "rgb(255,255,85)", "bright_blue": "rgb(85,85,255)",
       "bright_magenta": "rgb(255,85,255)", "bright_cyan": "rgb(85,255,255)",
       "white": "rgb(255,255,255)"}

# One character for one, so nothing moves on the screen.
CONSOLE_TRANSLATION = str.maketrans({
    # rounded, heavy and dashed lines -> the single and double lines every console font has
    "╭": "┌", "╮": "┐", "╰": "└", "╯": "┘",
    "╴": "─", "╶": "─", "╵": "│", "╷": "│", "╼": "─", "╾": "─", "╽": "│", "╿": "│",
    "━": "═", "┃": "║", "┏": "╔", "┓": "╗", "┗": "╚", "┛": "╝",
    "┣": "╠", "┫": "╣", "┳": "╦", "┻": "╩", "╋": "╬",
    "╸": "═", "╺": "═", "╹": "║", "╻": "║",
    "┄": "─", "┅": "═", "┈": "─", "┉": "═", "╌": "─", "╍": "═",
    "┆": "│", "┇": "║", "┊": "│", "┋": "║", "╎": "│", "╏": "║",
    # partial blocks -> the half and full blocks
    "▁": "_", "▂": "▄", "▃": "▄", "▅": "▄", "▆": "█", "▇": "█",
    "▉": "█", "▊": "▌", "▋": "▌", "▍": "▌", "▎": "▌", "▏": "▌", "▕": "▐", "▔": "▀",
    "▖": "▄", "▗": "▄", "▘": "▀", "▝": "▀", "▙": "█", "▚": "▒", "▛": "█", "▜": "█",
    "▞": "▒", "▟": "█",
    # symbols
    "▮": "■", "▯": "□", "●": "*", "○": "o", "◆": "♦", "◇": "♦", "•": "·",
    "▶": "►", "▸": "►", "◀": "◄", "◂": "◄",
    "✓": "√", "✔": "√", "✗": "x", "✘": "x", "★": "*", "☆": "*", "⚠": "!",
    "…": ".", "–": "-", "—": "-", "‐": "-", "‑": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"', "×": "x",
})

# Border styles whose characters a console font lacks, and the one drawn instead.
CONSOLE_BORDERS = {"round": "solid", "dashed": "solid", "heavy": "double",
                   "tall": "solid", "panel": "solid", "wide": "solid", "tab": "solid",
                   "hkey": "solid", "vkey": "solid", "inner": "thick", "outer": "thick"}


def is_plain_console(environ=None) -> bool:
    """True on the Linux (or BSD) virtual console, or without a UTF-8 locale."""
    environ = os.environ if environ is None else environ
    term = environ.get("TERM", "")
    if term in ("linux", "cons25", "pcvt25", "wsvt25") or term.startswith(("vt1", "vt2")):
        return True
    locale = (environ.get("LC_ALL") or environ.get("LC_CTYPE") or environ.get("LANG") or "")
    return bool(locale) and locale not in ("C", "POSIX") \
        and "utf-8" not in locale.lower() and "utf8" not in locale.lower()


def configure(setting: str | None = "auto", environ=None) -> bool:
    """Choose the glyphs: "unicode", "console", or "auto" (console on a plain console).
    IC705SUITE_GLYPHS overrides the setting. Returns whether console mode is on."""
    global CONSOLE
    environ = os.environ if environ is None else environ
    setting = (environ.get("IC705SUITE_GLYPHS") or setting or "auto").strip().lower()
    if setting not in MODES:
        setting = "auto"
    console = setting == "console" or setting == "auto" and is_plain_console(environ)
    CONSOLE = console
    globals().update(CONSOLE_GLYPHS if console else UNICODE_GLYPHS)
    if console:
        _patch_textual()
    return console


def fit(text: str) -> str:
    """Text as the terminal can show it."""
    return text.translate(CONSOLE_TRANSLATION) if CONSOLE else text


_patched = False


def _patch_textual() -> None:
    """Square borders, plain scrollbars, and the last-moment translation of the output."""
    global _patched
    if _patched:
        return
    _patched = True
    from textual import _border
    from textual.drivers import linux_driver
    from textual.scrollbar import ScrollBarRender

    for name, instead in CONSOLE_BORDERS.items():
        if name in _border.BORDER_CHARS and instead in _border.BORDER_CHARS:
            _border.BORDER_CHARS[name] = _border.BORDER_CHARS[instead]
            _border.BORDER_LOCATIONS[name] = _border.BORDER_LOCATIONS[instead]
    _border.get_box.cache_clear()
    # The scrollbar thumb is drawn with its colour only.
    ScrollBarRender.VERTICAL_BARS = [" "] * len(ScrollBarRender.VERTICAL_BARS)
    ScrollBarRender.HORIZONTAL_BARS = [" "] * len(ScrollBarRender.HORIZONTAL_BARS)

    write = linux_driver.LinuxDriver.write

    def write_for_the_console(self, data: str) -> None:
        write(self, data.translate(CONSOLE_TRANSLATION))

    linux_driver.LinuxDriver.write = write_for_the_console
