"""Band scope for the terminal: a spectrum trace over a waterfall.

The terminal has no pixels, so each character cell carries two waterfall rows: the upper
half block "▀" painted with the newer row as foreground and the older one as background.
The spectrum above uses eighth blocks (▁▂▃▄▅▆▇█) for an 8-level trace per row. Columns are
the scope points resampled to the widget width (peak of the points each column covers, so a
narrow CW signal never disappears between columns).

The widget only draws; frames come from outside (``add_frame``) as amplitudes 0..1 with the
frequency of the first and last point. Keyboard only: ``signals()`` lists the signals of the
latest sweep so the application can jump from one to the next.

On a plain console (``glyphs.CONSOLE``: 16 colours, 8 of them as background, no eighth
blocks) each cell is one waterfall row shaded with ░ ▒ ▓ between two of those colours, about
25 levels instead of 7, and the spectrum uses half blocks.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

from ic705suite.ui import glyphs

BLOCKS = " ▁▂▃▄▅▆▇█"
MAX_ROWS = 400

# Black -> blue -> cyan -> green -> yellow -> red -> white, as most SDR programs.
PALETTE_STOPS = [(0.0, (0, 0, 0)), (0.15, (0, 0, 120)), (0.35, (0, 140, 220)),
                 (0.5, (0, 200, 80)), (0.65, (230, 230, 0)), (0.82, (240, 60, 0)),
                 (1.0, (255, 255, 255))]


def _palette(n: int = 64) -> list[Color]:
    colors = []
    for i in range(n):
        x = i / (n - 1)
        for (x0, c0), (x1, c1) in zip(PALETTE_STOPS, PALETTE_STOPS[1:]):
            if x <= x1:
                t = (x - x0) / (x1 - x0) if x1 > x0 else 0
                colors.append(Color.from_rgb(*(round(a + (b - a) * t) for a, b in zip(c0, c1))))
                break
    return colors


PALETTE = _palette()

CONSOLE_BLOCKS = " ▄█"
CONSOLE_GAMMA = 1.5        # the ramp brightens quickly: keep the noise floor dark


def _console_ramp() -> list[tuple[str, Style]]:
    """Levels for the 16-colour console, from black to white: each colour shaded into the next
    with ░ ▒ ▓ (only the 8 dark colours can be a background)."""
    ramp: list[tuple[str, str, str]] = []
    for low, high in (("black", "blue"), ("blue", "cyan"), ("cyan", "green"),
                      ("green", "yellow")):
        ramp.append((" ", low, low))
        ramp += [(shade, high, low) for shade in "░▒▓"]
    ramp.append(("█", "yellow", "black"))
    ramp += [(shade, "yellow", "red") for shade in "▓▒░"]
    ramp.append((" ", "red", "red"))
    ramp += [(shade, "white", "red") for shade in "░▒▓"]
    ramp.append(("█", "white", "black"))
    return [(char, Style(color=glyphs.VGA[fg], bgcolor=glyphs.VGA[bg])) for char, fg, bg in ramp]


CONSOLE_RAMP = _console_ramp()


@dataclass
class ScopeView:
    """What the scope shows: frequency edges (Hz) and the markers."""
    start: int = 0
    end: int = 0
    dial: int | None = None          # receive frequency marker
    passband: tuple[int, int] | None = None


def resample_peak(values: np.ndarray, width: int) -> np.ndarray:
    """Values to `width` columns, each the maximum of the points it covers."""
    n = len(values)
    if n == 0 or width <= 0:
        return np.zeros(max(width, 0))
    edges = np.linspace(0, n, width + 1)
    lo = np.floor(edges[:-1]).astype(int)
    hi = np.maximum(lo + 1, np.ceil(edges[1:]).astype(int))
    return np.array([values[a:min(b, n)].max() for a, b in zip(lo, hi)])


class Waterfall(Widget, can_focus=True):
    """Spectrum (top `spectrum_rows` rows) and waterfall (the rest), plus a frequency scale."""

    DEFAULT_CSS = """
    Waterfall {
        height: 1fr;
        min-height: 8;
        background: black;
    }
    """

    spectrum_rows: reactive[int] = reactive(6)
    # Contrast: amplitudes below `floor` are black, above `floor + span` saturate.
    floor: reactive[float] = reactive(0.10)
    span: reactive[float] = reactive(0.75)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.view = ScopeView()
        self._raw: deque[np.ndarray] = deque(maxlen=MAX_ROWS)   # newest first, scope points
        self._edges: tuple[int, int] = (0, 0)
        self._cache_width = -1
        self._columns: list[np.ndarray] = []                   # rows resampled to the width

    # ---- data ------------------------------------------------------------------------------

    def add_frame(self, amplitudes: np.ndarray, start: int, end: int) -> None:
        """A new sweep (values 0..1). A change of edges starts a new picture."""
        if (start, end) != self._edges:
            self._raw.clear()
            self._columns.clear()
            self._edges = (start, end)
        self.view.start, self.view.end = start, end
        self._raw.appendleft(np.asarray(amplitudes, dtype=float))
        if self._cache_width == self.size.width and self._cache_width > 0:
            self._columns.insert(0, resample_peak(self._raw[0], self._cache_width))
            del self._columns[MAX_ROWS:]
        self.refresh()

    def clear(self) -> None:
        self._raw.clear()
        self._columns.clear()
        self.refresh()

    def _rows(self) -> list[np.ndarray]:
        width = self.size.width
        if width != self._cache_width:
            self._cache_width = width
            self._columns = [resample_peak(r, width) for r in self._raw]
        return self._columns

    # ---- geometry ----------------------------------------------------------------------------

    def column_frequency(self, x: int) -> int | None:
        width, (start, end) = self.size.width, (self.view.start, self.view.end)
        if width <= 0 or end <= start:
            return None
        return round(start + (x + 0.5) * (end - start) / width)

    def frequency_column(self, hz: int | None) -> int | None:
        width, (start, end) = self.size.width, (self.view.start, self.view.end)
        if hz is None or width <= 0 or end <= start or not start <= hz <= end:
            return None
        return min(width - 1, int((hz - start) * width / (end - start)))

    def _level(self, value: float) -> float:
        return min(1.0, max(0.0, (value - self.floor) / self.span))

    # ---- drawing -----------------------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        rows = self._rows()
        marker = self.frequency_column(self.view.dial)
        band = None
        if self.view.passband:
            a, b = (self.frequency_column(f) for f in self.view.passband)
            if a is not None and b is not None:
                band = (min(a, b), max(a, b))
        if y == self.spectrum_rows:
            return self._scale_line(width, marker)
        if y < self.spectrum_rows:
            return self._spectrum_line(y, width, rows[0] if rows else None, marker, band)
        if glyphs.CONSOLE:
            return self._console_waterfall_line(y - self.spectrum_rows - 1, width, rows, marker)
        index = 2 * (y - self.spectrum_rows - 1)
        newer = rows[index] if index < len(rows) else None
        older = rows[index + 1] if index + 1 < len(rows) else None
        segments = []
        for x in range(width):
            fg = PALETTE[int(self._level(newer[x]) * (len(PALETTE) - 1))] if newer is not None \
                else Color.from_rgb(0, 0, 0)
            bg = PALETTE[int(self._level(older[x]) * (len(PALETTE) - 1))] if older is not None \
                else Color.from_rgb(0, 0, 0)
            if x == marker:
                segments.append(Segment("▀", Style(color=fg, bgcolor=bg, bold=True)))
            else:
                segments.append(Segment("▀", Style(color=fg, bgcolor=bg)))
        return Strip(segments, width)

    def _console_waterfall_line(self, index: int, width: int, rows: list[np.ndarray],
                                marker: int | None) -> Strip:
        row = rows[index] if index < len(rows) else None
        last = len(CONSOLE_RAMP) - 1
        segments = []
        for x in range(width):
            char, style = CONSOLE_RAMP[int(self._level(row[x]) ** CONSOLE_GAMMA * last)] \
                if row is not None else CONSOLE_RAMP[0]
            if x == marker:
                char, style = glyphs.MARK, Style(color=glyphs.VGA["bright_red"],
                                                 bgcolor=style.bgcolor)
            segments.append(Segment(char, style))
        return Strip(segments, width)

    def _spectrum_line(self, y: int, width: int, row: np.ndarray | None,
                       marker: int | None, band: tuple[int, int] | None) -> Strip:
        rows = self.spectrum_rows
        console = glyphs.CONSOLE
        segments = []
        for x in range(width):
            in_band = band is not None and band[0] <= x <= band[1]
            bg = (glyphs.VGA["blue"] if console else "rgb(30,30,60)") if in_band else "black"
            if row is None:
                char = " "
            elif console:
                halves = int(self._level(row[x]) * rows * 2)
                char = CONSOLE_BLOCKS[max(0, min(2, halves - (rows - 1 - y) * 2))]
            else:
                eighths = int(self._level(row[x]) * rows * 8)
                filled = eighths - (rows - 1 - y) * 8     # eighths reaching this row
                char = BLOCKS[max(0, min(8, filled))]
            if x == marker:
                segments.append(Segment(char if char.strip() else "│",
                                        Style(color="red", bgcolor=bg, bold=True)))
            else:
                segments.append(Segment(char, Style(color=glyphs.VGA["bright_cyan"] if console
                                                else "rgb(120,220,255)", bgcolor=bg)))
        return Strip(segments, width)

    def _scale_line(self, width: int, marker: int | None) -> Strip:
        """Frequency labels every ~16 columns, with the hovered frequency on the right."""
        start, end = self.view.start, self.view.end
        line = [" "] * width
        if end > start and width > 0:
            step = _nice_step((end - start) * 16 / width)
            first = -(-start // step) * step
            for hz in range(first, end + 1, step):
                x = self.frequency_column(hz)
                if x is None:
                    continue
                label = f"{hz / 1e6:.3f}"
                line[x] = "┴"
                for i, ch in enumerate(label, 1):
                    if x + i < width and line[x + i] == " ":
                        line[x + i] = ch
        segments = [Segment("".join(line), Style(color="rgb(170,170,170)", bgcolor="rgb(20,20,20)"))]
        if marker is not None:
            text = "".join(line)
            segments = [Segment(text[:marker], Style(color="rgb(170,170,170)", bgcolor="rgb(20,20,20)")),
                        Segment(text[marker] if text[marker] != " " else glyphs.MARK, Style(color="red", bold=True,
                                bgcolor="rgb(20,20,20)")),
                        Segment(text[marker + 1:], Style(color="rgb(170,170,170)", bgcolor="rgb(20,20,20)"))]
        return Strip(segments, width)

    # ---- signals ----------------------------------------------------------------------

    def signals(self, threshold: float = 0.12) -> list[int]:
        """Frequencies of the signals in the latest sweep: local peaks standing `threshold`
        above the median (the noise floor), strongest point of each."""
        if not self._raw or self.view.end <= self.view.start:
            return []
        row = self._raw[0]
        floor = float(np.median(row))
        above = row > floor + threshold
        peaks, i, n = [], 0, len(row)
        while i < n:
            if above[i]:
                j = i
                while j < n and above[j]:
                    j += 1
                k = i + int(np.argmax(row[i:j]))
                peaks.append(round(self.view.start + k * (self.view.end - self.view.start) / (n - 1)))
                i = j
            else:
                i += 1
        return peaks


def _nice_step(raw: float) -> int:
    """1, 2, 5 x 10^n Hz, at least `raw`."""
    if raw <= 0:
        return 1000
    exp = 10 ** int(np.floor(np.log10(raw)))
    for m in (1, 2, 5, 10):
        if m * exp >= raw:
            return int(m * exp)
    return int(10 * exp)
