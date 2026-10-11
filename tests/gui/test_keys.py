"""The graphical client's keys are the terminal's: same names, same actions."""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from ic705suite.gui.keys import KeyMap, key_name, parse_action


def press(key, text="", modifiers=Qt.KeyboardModifier.NoModifier) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text)


@pytest.mark.parametrize("event, name", [
    (press(Qt.Key.Key_F2), "f2"),
    (press(Qt.Key.Key_PageUp), "pageup"),
    (press(Qt.Key.Key_Up, modifiers=Qt.KeyboardModifier.ShiftModifier), "shift+up"),
    (press(Qt.Key.Key_Backtab, modifiers=Qt.KeyboardModifier.ShiftModifier), "shift+tab"),
    (press(Qt.Key.Key_Q, "\x11", Qt.KeyboardModifier.ControlModifier), "ctrl+q"),
    (press(Qt.Key.Key_Minus, "-"), "minus"),
    (press(Qt.Key.Key_Less, "<", Qt.KeyboardModifier.ShiftModifier), "less_than_sign"),
    (press(Qt.Key.Key_BracketLeft, "["), "left_square_bracket"),
    (press(Qt.Key.Key_Slash, "/"), "slash"),
    (press(Qt.Key.Key_Return, "\r"), "enter"),
    (press(Qt.Key.Key_A, "a"), "a"),
])
def test_qt_keys_get_textual_names(event, name):
    assert key_name(event) == name


def test_actions_are_parsed_as_textual_does():
    assert parse_action("arrow(0, -1, True)") == ("arrow", (0, -1, True))
    assert parse_action("open('view')") == ("open", ("view",))
    assert parse_action("command('/macro F2')") == ("command", ("/macro F2",))
    assert parse_action("app.pop_screen") == ("pop_screen", ())
    assert parse_action("leave") == ("leave", ())


PAGES = {"dashboard": ("ic705suite.gui.dashboard", "DashboardPage"),
         "view": ("ic705suite.gui.view", "ViewPage"),
         "cw": ("ic705suite.gui.cw", "CwPage"),
         "cw channels": ("ic705suite.gui.cw", "ChannelsView"),
         "antenna": ("ic705suite.gui.swr", "SwrPage"),
         "antennas": ("ic705suite.gui.swr", "AntennasView"),
         "presets": ("ic705suite.gui.swr", "PresetsView"),
         "logbook": ("ic705suite.gui.log", "LogPage"),
         "packet": ("ic705suite.gui.packet", "PacketPage")}


@pytest.mark.parametrize("page", sorted(PAGES))
def test_every_terminal_key_has_its_action(page):
    """Each binding of the terminal screen runs an action the page has: no key of the
    console is missing from the graphical client."""
    import importlib

    module, name = PAGES[page]
    Page = getattr(importlib.import_module(module), name)
    keymap = KeyMap(Page.TUI)
    assert keymap.by_key, "no bindings found"
    missing = sorted(a for a in keymap.actions() if not hasattr(Page, f"action_{a}"))
    assert not missing, f"{Page.__name__} lacks {missing}"


def test_the_terminal_bindings_are_read_with_their_priority():
    from ic705suite.apps.view.app import ViewApp

    keymap = KeyMap(ViewApp)
    assert keymap.find("pageup", priority=True).action == "tune(1)"
    assert keymap.find("up", priority=False).action == "arrow(0, -1)"
    assert keymap.find("up", priority=True) is None
