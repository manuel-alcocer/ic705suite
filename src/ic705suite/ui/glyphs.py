"""Glyphs for the terminal at hand: full Unicode, or what the console font has.

A terminal emulator (in a desktop session, over SSH) draws any Unicode character. The Linux
virtual console does not: its font holds 256 or 512 glyphs and only 16 colours (8 as
background), and which glyphs depends on the font. Terminus ``ter-1*`` has the full block and
two shades (█ ░ ▒) but no half blocks; the IBM PC set (CP437) has ▀ ▄ ▌ ▐ ▓ and double lines
too. A missing character comes out as the font's replacement sign.

In console mode the suite asks the kernel which characters the loaded font has (GIO_UNIMAP)
and draws with those: the waterfall shades between the 16 colours with the shades the font
has, the spectrum and the frequency use half blocks when there are any and full blocks
otherwise, borders become square. Whatever Textual or an application still sends that the
font lacks is replaced, one character for one, just before it reaches the terminal. When the
font cannot be asked (``--glyphs console`` over SSH), a small set every console font has is
assumed.

``configure()`` chooses the mode once, before the first application starts. Code that draws
reads ``glyphs.CONSOLE``, ``glyphs.has()`` and the constants below at drawing time.
"""

from __future__ import annotations

import os
import sys

CONSOLE = False
CHARSET: frozenset[str] | None = None      # the console font's characters (None: any)
TRANSLATION: dict[int, str] = {}

# Glyphs used by the suite's own widgets, set by configure().
BAR = "▮"          # a filled cell of a meter or level bar
MARK = "┃"         # the dial marker, the centre of an offset bar
CARET = "▏"        # end of a value being typed

UNICODE_GLYPHS = {"BAR": "▮", "MARK": "┃", "CARET": "▏"}
CONSOLE_GLYPHS = {"BAR": "■█#", "MARK": "│|", "CARET": "_"}   # first one the font has

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

# What a font without Unicode extras has: Latin-1, the light lines, █ ░ ▒ ■ and a few symbols
# (Terminus ter-1*, the smallest of the usual console fonts).
MINIMAL_CHARSET = frozenset(
    [chr(c) for c in range(0x20, 0x7F)] + [chr(c) for c in range(0xA0, 0x100)]
    + list("–—‘’‚“”„†‡•…‰‹›€™←↑→↓≤≥─│┌┐└┘├┤┬┴┼█░▒■▲►▼◄♦"))

# For each character a console font may lack: what to draw instead, best first. The last
# candidate is ASCII. One character for one, so nothing moves on the screen.
FALLBACKS = {
    "╭": "┌+", "╮": "┐+", "╰": "└+", "╯": "┘+",
    "╴": "─-", "╶": "─-", "╼": "─-", "╾": "─-", "╵": "│|", "╷": "│|", "╽": "│|", "╿": "│|",
    "━": "═─-", "┃": "║│|", "┏": "╔┌+", "┓": "╗┐+", "┗": "╚└+", "┛": "╝┘+",
    "┣": "╠├+", "┫": "╣┤+", "┳": "╦┬+", "┻": "╩┴+", "╋": "╬┼+",
    "╸": "═─-", "╺": "═─-", "╹": "║│|", "╻": "║│|",
    "═": "─-", "║": "│|", "╔": "┌+", "╗": "┐+", "╚": "└+", "╝": "┘+",
    "╠": "├+", "╣": "┤+", "╦": "┬+", "╩": "┴+", "╬": "┼+",
    "┄": "─-", "┅": "═─-", "┈": "─-", "┉": "═─-", "╌": "─-", "╍": "═─-",
    "┆": "│|", "┇": "║│|", "┊": "│|", "┋": "║│|", "╎": "│|", "╏": "║│|",
    "─": "-", "│": "|", "┌": "+", "┐": "+", "└": "+", "┘": "+", "├": "+", "┤": "+",
    "┬": "+", "┴": "+", "┼": "+",
    "▁": "⎽_", "▂": "▄⎽_", "▃": "▄⎽_", "▄": "⎽_", "▅": "▄█#", "▆": "█#", "▇": "█#", "█": "#",
    "▔": "▀⎺-", "▀": "⎺-",
    "▉": "█#", "▊": "▌█#", "▋": "▌█#", "▌": "█#", "▍": "▌|", "▎": "▌|", "▏": "▌|",
    "▐": "█#", "▕": "▐|",
    "▖": "▄.", "▗": "▄.", "▘": "▀'", "▝": "▀'", "▙": "█#", "▛": "█#", "▜": "█#", "▟": "█#",
    "▚": "▒:", "▞": "▒:", "░": ".", "▒": "░:", "▓": "▒░#",
    "▮": "■█#", "▯": "□o", "■": "#", "●": "•*", "○": "o", "◆": "♦*", "◇": "♦o", "♦": "*",
    "▶": "►>", "▸": "►>", "◀": "◄<", "◂": "◄<", "►": ">", "◄": "<", "▲": "^", "▼": "v",
    "←": "<", "→": ">", "↑": "^", "↓": "v",
    "✓": "√v", "✔": "√v", "✗": "x", "✘": "x", "★": "*", "☆": "*", "⚠": "!", "♪": "♪*",
    "…": ".", "–": "-", "—": "-", "‐": "-", "‑": "-", "•": "·*", "·": ".", "±": "±+",
    "‘": "'", "’": "'", "“": '"', "”": '"', "×": "x", "Δ": "d",
}

# Border styles whose characters a console font may lack, and what to draw instead.
CONSOLE_BORDERS = {"round": "solid", "dashed": "solid", "heavy": "double", "tall": "solid",
                   "panel": "solid", "wide": "solid", "tab": "solid", "hkey": "solid",
                   "vkey": "solid", "inner": "thick", "outer": "thick"}


# Backgrounds a console cannot show (it has only the 8 dark colours as background), and the
# (text, background) drawn instead, chosen for contrast.
CONSOLE_BACKGROUNDS = {
    "yellow": ("white", "brown"), "bright_yellow": ("white", "brown"),
    "red": ("white", "red"), "dark_red": ("white", "red"), "bright_red": ("white", "red"),
    "green": ("black", "green"), "cyan": ("black", "cyan"), "magenta": ("white", "magenta"),
    "blue": ("white", "blue"), "grey35": ("black", "grey"), "grey": ("black", "grey"),
    "rgb(120,180,255)": ("black", "cyan"),
}


def style(text: str) -> str:
    """A "bold black on yellow" style as the console can show it (unchanged elsewhere)."""
    if not CONSOLE or " on " not in text:
        return text
    head, background = text.rsplit(" on ", 1)
    if background not in CONSOLE_BACKGROUNDS:
        return text
    foreground, background = CONSOLE_BACKGROUNDS[background]
    attributes = [word for word in head.split() if word in ("bold", "italic", "underline")]
    return " ".join(attributes + [VGA[foreground], "on", VGA[background]])


def has(char: str) -> bool:
    """Whether the terminal can draw `char`."""
    return CHARSET is None or char in CHARSET


def first(candidates: str) -> str:
    """The first of `candidates` the terminal can draw (else the last one)."""
    return next((c for c in candidates if has(c)), candidates[-1])


def is_plain_console(environ=None) -> bool:
    """True on the Linux (or BSD) virtual console, or without a UTF-8 locale."""
    environ = os.environ if environ is None else environ
    term = environ.get("TERM", "")
    if term in ("linux", "cons25", "pcvt25", "wsvt25") or term.startswith(("vt1", "vt2")):
        return True
    locale = (environ.get("LC_ALL") or environ.get("LC_CTYPE") or environ.get("LANG") or "")
    return bool(locale) and locale not in ("C", "POSIX") \
        and "utf-8" not in locale.lower() and "utf8" not in locale.lower()


def font_charset(fd: int | None = None) -> frozenset[str] | None:
    """The characters of the Linux console font on `fd` (default: the terminal), or None."""
    import ctypes
    import errno
    import fcntl

    class UniPair(ctypes.Structure):
        _fields_ = [("unicode", ctypes.c_ushort), ("fontpos", ctypes.c_ushort)]

    class UniMapDesc(ctypes.Structure):
        _fields_ = [("entry_ct", ctypes.c_ushort), ("entries", ctypes.POINTER(UniPair))]

    GIO_UNIMAP = 0x4B66
    if fd is None:
        try:
            fd = sys.__stdout__.fileno()
        except (AttributeError, OSError, ValueError):
            return None
    count = 4096
    for _ in range(2):
        pairs = (UniPair * count)()
        desc = UniMapDesc(count, ctypes.cast(pairs, ctypes.POINTER(UniPair)))
        try:
            fcntl.ioctl(fd, GIO_UNIMAP, desc)
        except OSError as exc:
            if exc.errno == errno.ENOMEM and desc.entry_ct > count:
                count = desc.entry_ct              # the kernel says how many there are
                continue
            return None
        chars = frozenset(chr(p.unicode) for p in pairs[:desc.entry_ct])
        return chars | {" "} if len(chars) > 32 else None
    return None


def translation(charset: frozenset[str] | None) -> dict[int, str]:
    """Replacements for the characters `charset` lacks."""
    if charset is None:
        return {}
    table = {}
    for char, candidates in FALLBACKS.items():
        if char not in charset:
            table[ord(char)] = next((c for c in candidates if c in charset), candidates[-1])
    return table


def configure(setting: str | None = "auto", environ=None,
              charset: frozenset[str] | None = None) -> bool:
    """Choose the glyphs: "unicode", "console", or "auto" (console on a plain console).
    IC705SUITE_GLYPHS overrides the setting; `charset` is the console font's characters
    (default: asked to the kernel, or a minimal set). Returns whether console mode is on."""
    global CONSOLE, CHARSET, TRANSLATION
    environ = os.environ if environ is None else environ
    setting = (environ.get("IC705SUITE_GLYPHS") or setting or "auto").strip().lower()
    if setting not in MODES:
        setting = "auto"
    console = setting == "console" or setting == "auto" and is_plain_console(environ)
    CONSOLE = console
    if not console:
        CHARSET, TRANSLATION = None, {}
        globals().update(UNICODE_GLYPHS)
        return False
    CHARSET = charset or font_charset() or MINIMAL_CHARSET
    TRANSLATION = translation(CHARSET)
    globals().update({name: first(candidates) for name, candidates in CONSOLE_GLYPHS.items()})
    _patch_textual()
    return True


def fit(text: str) -> str:
    """Text as the terminal can show it."""
    return text.translate(TRANSLATION) if TRANSLATION else text


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

    borders = dict(CONSOLE_BORDERS)
    if not all(has(c) for c in "═║╔╗╚╝"):
        borders.update({"heavy": "solid", "double": "solid"})
    if not all(has(c) for c in "▀▄█"):
        borders.update({"inner": "solid", "outer": "solid", "thick": "solid", "block": "solid"})
    chars, locations = dict(_border.BORDER_CHARS), dict(_border.BORDER_LOCATIONS)
    for name, instead in borders.items():
        if name in chars and instead in chars:
            _border.BORDER_CHARS[name] = chars[instead]
            _border.BORDER_LOCATIONS[name] = locations[instead]
    _border.get_box.cache_clear()
    # The scrollbar thumb is drawn with its colour only.
    ScrollBarRender.VERTICAL_BARS = [" "] * len(ScrollBarRender.VERTICAL_BARS)
    ScrollBarRender.HORIZONTAL_BARS = [" "] * len(ScrollBarRender.HORIZONTAL_BARS)

    write = linux_driver.LinuxDriver.write

    def write_for_the_console(self, data: str) -> None:
        write(self, data.translate(TRANSLATION) if TRANSLATION else data)

    linux_driver.LinuxDriver.write = write_for_the_console
