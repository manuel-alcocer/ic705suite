"""The rig's front panel as a grid of knobs and switches, one column per group.

Keyboard only, in two modes:

* navigation: the cursor keys move between the options (↑↓ within a column, ←→ between
  columns); Space or Enter starts editing the option under the cursor;
* editing: the cursor keys change the value at once (Shift: ten steps), or the value is
  typed ("40", "-120", "slow", "on"); Enter confirms and goes back to navigation, Esc
  cancels and puts the old value back.

The panel only draws; its state and the changes the keys make live in PanelState
(ui.panelmodel), which the graphical client shares, and the application sends the values to
the rig.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual import events
from textual.widget import Widget

from ic705suite.i18n import _
from ic705suite.rig.controls import GROUPS, LEVEL, OFFSET, TOGGLE
from ic705suite.ui import glyphs
from ic705suite.ui.panelmodel import PanelState, format_value, parse_value  # noqa: F401

BAR_CELLS = 6


def bar(control: dict, value) -> Text:
    text = Text()
    if value is None or control["kind"] not in (LEVEL, OFFSET):
        return text
    lo, hi = control["minimum"], control["maximum"]
    fraction = (value - lo) / (hi - lo) if hi > lo else 0
    if control["kind"] == OFFSET:
        # Centre mark, filled towards the side of the offset.
        half = BAR_CELLS // 2
        cells = round((fraction - 0.5) * 2 * half)
        left = glyphs.BAR * max(0, -cells)
        right = glyphs.BAR * max(0, cells)
        text.append("·" * (half - len(left)), style="dim")
        text.append(left, style="yellow")
        text.append(glyphs.MARK, style="white")
        text.append(right, style="yellow")
        text.append("·" * (half - len(right)), style="dim")
        return text
    filled = round(fraction * BAR_CELLS)
    text.append(glyphs.BAR * filled, style="green")
    text.append("·" * (BAR_CELLS - filled), style="dim")
    return text


class ControlPanel(PanelState, Widget, can_focus=True):
    DEFAULT_CSS = """
    ControlPanel {
        height: auto;
        min-height: 3;
        padding: 0 1;
    }
    ControlPanel:focus {
        background: $boost;
    }
    """

    def __init__(self, groups: tuple[tuple[str, str], ...] = GROUPS, bars: bool = True,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._init_panel(groups)
        self.bars = bars          # level bars beside the values (off: compact)

    def on_key(self, event: events.Key) -> None:
        """While editing, printable keys write the value instead of acting as shortcuts."""
        if self.type_key(event.key, event.character):
            event.prevent_default()
            event.stop()

    # ---- drawing ----------------------------------------------------------------------------

    def get_content_height(self, container, viewport, width: int) -> int:
        per_group = {}
        for c in self.controls:
            per_group[c["group"]] = per_group.get(c["group"], 0) + 1
        return 1 + max(per_group.values(), default=1)

    def render(self) -> Table:
        groups = [(g, label) for g, label in self.groups
                  if any(c["group"] == g for c in self.controls)]
        table = Table.grid(expand=True, padding=(0, 2))
        for _group in groups:
            table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        columns = []
        for group, label in groups:
            cells = [Text(_(label).upper(), style="bold cyan")]
            for c in (c for c in self.controls if c["group"] == group):
                value = self.values.get(c["id"])
                selected = c["id"] == self.selected
                editing = selected and self.editing
                cell = Text(no_wrap=True, overflow="ellipsis")
                name_style = glyphs.style("bold black on yellow") if editing \
                    else glyphs.style("bold black on rgb(120,180,255)") if selected and self.has_focus \
                    else "bold reverse" if selected else "dim"
                cell.append(f"{_(c['label']):<6}", style=name_style)
                cell.append(" ")
                if editing and self.buffer:
                    cell.append(self.buffer + glyphs.CARET, style=glyphs.style("bold black on yellow"))
                    cells.append(cell)
                    continue
                if c["kind"] in (LEVEL, OFFSET) and self.bars and c.get("bar", True):
                    cell.append_text(bar(c, value))
                    cell.append(" ")
                on = c["kind"] == TOGGLE and value
                value_style = glyphs.style("bold black on yellow") if editing else "bold green" if on \
                    else "bold white" if value is not None else "dim"
                cell.append(format_value(c, value), style=value_style)
                cells.append(cell)
            columns.append(cells)
        rows = max((len(c) for c in columns), default=0)
        for i in range(rows):
            table.add_row(*(col[i] if i < len(col) else Text("") for col in columns))
        return table
