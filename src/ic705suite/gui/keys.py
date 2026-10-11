"""The keyboard of the graphical client is the terminal's: each page takes the bindings of
the Textual screen it mirrors (its BINDINGS, the same key names and actions) and runs the
action of the same name on itself.

As in Textual, priority bindings act whatever has the focus; the others reach the focused
widget first (a text field keeps its letters, a table its arrows) and the page only gets the
keys the widget leaves.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent

log = logging.getLogger(__name__)

NAMED = {
    Qt.Key.Key_PageUp: "pageup", Qt.Key.Key_PageDown: "pagedown",
    Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
    Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter", Qt.Key.Key_Space: "space",
    Qt.Key.Key_Escape: "escape", Qt.Key.Key_Tab: "tab", Qt.Key.Key_Backtab: "shift+tab",
    Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Insert: "insert",
    **{getattr(Qt.Key, f"Key_F{n}"): f"f{n}" for n in range(1, 25)},
}


def key_name(event: QKeyEvent) -> str:
    """The Textual name of a key press: "f2", "pageup", "shift+up", "ctrl+q", "minus"..."""
    from textual.keys import _character_to_key

    key, mods = event.key(), event.modifiers()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    alt = bool(mods & Qt.KeyboardModifier.AltModifier)
    name = NAMED.get(key)
    if name is None:
        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            name = chr(key).lower()
        elif ctrl and Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            name = chr(key)                          # ctrl+3: hamrlog's profiles
        else:
            text = event.text()
            if not text or not text.isprintable():
                return ""
            name = _character_to_key(text)
            shift = False                 # already in the character ("<", "A", "?")
    if key == Qt.Key.Key_Backtab:
        shift = False                     # "shift+tab" already
    prefix = ("ctrl+" if ctrl else "") + ("alt+" if alt else "") + ("shift+" if shift else "")
    return prefix + name


@dataclass(frozen=True)
class KeyBinding:
    key: str
    action: str
    priority: bool


def bindings_of(screen_class: type) -> list[KeyBinding]:
    """The suite's own bindings of a Textual App or Screen class (not Textual's defaults),
    the class's own winning over its bases', one entry per key."""
    found: dict[str, KeyBinding] = {}
    for cls in reversed(screen_class.__mro__):
        if not cls.__module__.startswith("ic705suite"):
            continue
        for binding in cls.__dict__.get("BINDINGS", []):
            if isinstance(binding, tuple):        # ("key", "action", "description")
                keys, action, priority = binding[0], binding[1], False
            else:
                keys, action, priority = binding.key, binding.action, binding.priority
            for key in keys.split(","):
                key = key.strip()
                found[key] = KeyBinding(key, action, priority)
    return list(found.values())


def parse_action(action: str) -> tuple[str, tuple]:
    """"arrow(0, -1, True)" -> ("arrow", (0, -1, True)); "app.pop_screen" -> ("pop_screen", ())."""
    name, _, rest = action.partition("(")
    name = name.rsplit(".", 1)[-1].strip()
    if not rest:
        return name, ()
    args = ast.literal_eval(f"({rest.rstrip()[:-1]},)") if rest.rstrip()[:-1].strip() else ()
    return name, tuple(args)


class KeyMap:
    """The bindings of one Textual screen, ready to run on a page."""

    def __init__(self, screen_class: type) -> None:
        self.screen_class = screen_class
        self.by_key = {b.key: b for b in bindings_of(screen_class)}

    def find(self, key: str, priority: bool) -> KeyBinding | None:
        binding = self.by_key.get(key)
        return binding if binding is not None and binding.priority == priority else None

    def actions(self) -> set[str]:
        return {parse_action(b.action)[0] for b in self.by_key.values()}


def run_action(target: object, action: str) -> bool:
    """Run `action` on `target` (its action_<name> method, async or not). False if the
    target has no such action."""
    name, args = parse_action(action)
    method = getattr(target, f"action_{name}", None)
    if method is None:
        log.warning("no action %s on %s", name, type(target).__name__)
        return False
    result = method(*args)
    if inspect.isawaitable(result):
        task = asyncio.ensure_future(result)
        task.add_done_callback(_report)
    return True


def _report(task: asyncio.Future) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("action failed", exc_info=task.exception())
