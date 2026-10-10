"""The rig's front panel as a grid of knobs and switches, one column per group.

Keyboard only: ↑↓ or Tab / Shift+Tab move between controls, ←→ or - + turn a knob or change a
selector (Shift+←→ or < > ten steps), Enter or Space toggles a switch. The panel only draws and computes the new value;
the application sends it to the rig.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.widget import Widget

from ic705suite.i18n import _
from ic705suite.rig.controls import CHOICE, GROUPS, LEVEL, OFFSET, TOGGLE

BAR_CELLS = 6


def format_value(control: dict, value) -> str:
    if value is None:
        return "–"
    kind = control["kind"]
    if kind == TOGGLE:
        return _("on") if value else _("off")
    if kind == CHOICE:
        for key, label in control["choices"]:
            if key == value:
                return _(label)
        return str(value)
    unit = control.get("unit", "")
    if kind == OFFSET:
        return f"{value:+.0f}{unit if unit != '%' else ''}"
    if unit == "%":
        return f"{value:.0f}%"
    return f"{value:g} {unit}".strip()


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
        left = "▮" * max(0, -cells)
        right = "▮" * max(0, cells)
        text.append("·" * (half - len(left)), style="dim")
        text.append(left, style="yellow")
        text.append("┃", style="white")
        text.append(right, style="yellow")
        text.append("·" * (half - len(right)), style="dim")
        return text
    filled = round(fraction * BAR_CELLS)
    text.append("▮" * filled, style="green")
    text.append("·" * (BAR_CELLS - filled), style="dim")
    return text


class ControlPanel(Widget, can_focus=True):
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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.controls: list[dict] = []
        self.values: dict = {}
        self.selected: str | None = None

    def set_controls(self, controls: list[dict]) -> None:
        order = [g for g, _ in GROUPS]
        self.controls = sorted(controls, key=lambda c: order.index(c["group"])
                               if c["group"] in order else len(order))
        if self.controls and self.selected not in {c["id"] for c in self.controls}:
            self.selected = self.controls[0]["id"]
        self.refresh(layout=True)

    def set_values(self, values: dict) -> None:
        self.values = dict(values)
        self.refresh()

    def control(self, control_id: str | None) -> dict | None:
        return next((c for c in self.controls if c["id"] == control_id), None)

    # ---- navigation -------------------------------------------------------------------------

    def move(self, step: int) -> None:
        if not self.controls:
            return
        ids = [c["id"] for c in self.controls]
        index = ids.index(self.selected) if self.selected in ids else 0
        self.selected = ids[(index + step) % len(ids)]
        self.refresh()

    def move_group(self, step: int) -> None:
        """Jump to the first control of the next (or previous) group."""
        groups = [g for g, _ in GROUPS if any(c["group"] == g for c in self.controls)]
        current = self.control(self.selected)
        if not groups or current is None:
            return
        group = groups[(groups.index(current["group"]) + step) % len(groups)]
        self.selected = next(c["id"] for c in self.controls if c["group"] == group)
        self.refresh()

    def adjusted(self, direction: int, big: bool = False) -> tuple[str, object] | None:
        """(control id, new value) for turning the selected control one step."""
        control = self.control(self.selected)
        if control is None:
            return None
        value = self.values.get(control["id"])
        kind = control["kind"]
        if kind == TOGGLE:
            return control["id"], not bool(value)
        if kind == CHOICE:
            keys = [k for k, _ in control["choices"]]
            index = keys.index(value) if value in keys else -direction
            return control["id"], keys[(index + direction) % len(keys)]
        step = control["step"] * (10 if big else 1)
        base = value if value is not None else control["minimum"]
        new = max(control["minimum"], min(control["maximum"], base + direction * step))
        return control["id"], new

    # ---- drawing ----------------------------------------------------------------------------

    def get_content_height(self, container, viewport, width: int) -> int:
        per_group = {}
        for c in self.controls:
            per_group[c["group"]] = per_group.get(c["group"], 0) + 1
        return 1 + max(per_group.values(), default=1)

    def render(self) -> Table:
        groups = [(g, label) for g, label in GROUPS if any(c["group"] == g for c in self.controls)]
        table = Table.grid(expand=True, padding=(0, 2))
        for _group in groups:
            table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        columns = []
        for group, label in groups:
            cells = [Text(_(label).upper(), style="bold cyan")]
            for c in (c for c in self.controls if c["group"] == group):
                value = self.values.get(c["id"])
                selected = c["id"] == self.selected
                cell = Text(no_wrap=True, overflow="ellipsis")
                name_style = "bold black on rgb(120,180,255)" if selected and self.has_focus \
                    else "bold reverse" if selected else "dim"
                cell.append(f"{_(c['label']):<6}", style=name_style)
                cell.append(" ")
                if c["kind"] in (LEVEL, OFFSET):
                    cell.append_text(bar(c, value))
                    cell.append(" ")
                on = c["kind"] == TOGGLE and value
                cell.append(format_value(c, value),
                            style="bold green" if on else "bold white" if value is not None else "dim")
                cells.append(cell)
            columns.append(cells)
        rows = max((len(c) for c in columns), default=0)
        for i in range(rows):
            table.add_row(*(col[i] if i < len(col) else Text("") for col in columns))
        return table

    def help_line(self) -> str:
        control = self.control(self.selected)
        if control is None:
            return ""
        return _(control["help"]) if control.get("help") else _(control["label"])
