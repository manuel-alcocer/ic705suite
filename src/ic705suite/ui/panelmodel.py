"""What a rig front panel knows, without drawing it: the options, their values, the one under
the cursor and the edit in progress. The terminal's ControlPanel and the graphical client's
draw it; PanelKeys turns the keys into changes. Both clients therefore move, edit and adjust
exactly alike.

Two modes: navigation (the cursor keys move between the options: ↑↓ within a column, ←→
between columns) and editing (the cursor keys change the value at once, Shift ten steps, or
the value is typed: "40", "-120", "slow", "on"; Enter confirms, Esc puts the old value back).

``PanelState`` is a mixin: it calls ``self.refresh()`` after each change, which the class it
is mixed into provides (a Textual widget, or ``PanelModel`` below with a callback).
"""

from __future__ import annotations

from collections.abc import Callable

from ic705suite.i18n import _
from ic705suite.rig.controls import CHOICE, GROUPS, OFFSET, TOGGLE


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
    if control.get("decimals") is not None:
        return f"{value:.{control['decimals']}f} {unit}".strip()
    if kind == OFFSET:
        return f"{value:+.0f}{unit if unit != '%' else ''}"
    if unit == "%":
        return f"{value:.0f}%"
    return f"{value:g} {unit}".strip()


def parse_value(control: dict, text: str):
    """A typed value for a control: a number, on/off, or a choice by key or label."""
    text = text.strip().lower()
    kind = control["kind"]
    if kind == TOGGLE:
        if text in ("on", "1", "si", "sí", "yes", "true", _("on").lower()):
            return True
        if text in ("off", "0", "no", "false", _("off").lower()):
            return False
        raise ValueError(text)
    if kind == CHOICE:
        for key, label in control["choices"]:
            if text in (key.lower(), label.lower(), _(label).lower()):
                return key
        for key, label in control["choices"]:      # an unambiguous beginning is enough
            if _(label).lower().startswith(text) or key.lower().startswith(text):
                return key
        raise ValueError(text)
    value = float(text.rstrip("%").replace(",", ".").rstrip("hz").rstrip("db").strip())
    return max(control["minimum"], min(control["maximum"], value))


def fraction(control: dict, value) -> float | None:
    """Where a level or offset stands between its minimum and maximum (0..1)."""
    if value is None:
        return None
    lo, hi = control["minimum"], control["maximum"]
    return (value - lo) / (hi - lo) if hi > lo else 0.0


class PanelState:
    """The panel's state and every change the keys make to it."""

    def _init_panel(self, groups: tuple[tuple[str, str], ...] = GROUPS) -> None:
        self.groups = groups      # (id, label) of each column, in order
        self.controls: list[dict] = []
        self.values: dict = {}
        self.selected: str | None = None
        self.editing = False
        self.buffer = ""          # value being typed while editing
        self.original = None      # value before editing, put back on Esc

    def set_controls(self, controls: list[dict]) -> None:
        order = [g for g, _ in self.groups]
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

    def visible_groups(self) -> list[tuple[str, str]]:
        """(id, label) of the groups that have options, in order: the columns."""
        return [(g, label) for g, label in self.groups
                if any(c["group"] == g for c in self.controls)]

    def _columns(self) -> list[list[str]]:
        return [[c["id"] for c in self.controls if c["group"] == g]
                for g, _ in self.visible_groups()]

    def _position(self) -> tuple[int, int]:
        for x, column in enumerate(self._columns()):
            if self.selected in column:
                return x, column.index(self.selected)
        return 0, 0

    def move_vertical(self, step: int) -> None:
        """↑↓: the option above or below, in the same column."""
        columns = self._columns()
        if not columns:
            return
        x, y = self._position()
        self.selected = columns[x][max(0, min(len(columns[x]) - 1, y + step))]
        self.refresh()

    def move_horizontal(self, step: int) -> None:
        """←→: the option at the same height in the next column (or its last one)."""
        columns = self._columns()
        if not columns:
            return
        x, y = self._position()
        x = max(0, min(len(columns) - 1, x + step))
        self.selected = columns[x][min(y, len(columns[x]) - 1)]
        self.refresh()

    def move_group(self, step: int) -> None:
        """Jump to the first control of the next (or previous) group."""
        groups = [g for g, _ in self.visible_groups()]
        current = self.control(self.selected)
        if not groups or current is None:
            return
        group = groups[(groups.index(current["group"]) + step) % len(groups)]
        self.selected = next(c["id"] for c in self.controls if c["group"] == group)
        self.refresh()

    # ---- editing ----------------------------------------------------------------------------

    def start_edit(self) -> None:
        if self.control(self.selected) is None:
            return
        self.editing, self.buffer = True, ""
        self.original = self.values.get(self.selected)
        self.refresh()

    def end_edit(self) -> None:
        self.editing, self.buffer, self.original = False, "", None
        self.refresh()

    def typed_value(self):
        """The typed text as a value of the selected control (ValueError if it is not one)."""
        control = self.control(self.selected)
        if control is None or not self.buffer.strip():
            raise ValueError(self.buffer)
        return parse_value(control, self.buffer)

    def type_key(self, key: str, character: str | None) -> bool:
        """While editing, printable keys write the value instead of acting as shortcuts.
        True when the key was taken."""
        if not self.editing:
            return False
        if key == "backspace":
            self.buffer = self.buffer[:-1]
        elif character and len(character) == 1 and character.isprintable() and character != " ":
            self.buffer += character
        else:
            return False
        self.refresh()
        return True

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

    def help_line(self) -> str:
        control = self.control(self.selected)
        if control is None:
            return ""
        return _(control["help"]) if control.get("help") else _(control["label"])


class PanelModel(PanelState):
    """A panel on its own (the graphical client): changes are reported to `on_change`."""

    def __init__(self, groups: tuple[tuple[str, str], ...] = GROUPS,
                 on_change: Callable[[], None] | None = None) -> None:
        self._init_panel(groups)
        self.on_change = on_change

    def refresh(self, layout: bool = False) -> None:
        if self.on_change is not None:
            self.on_change()
