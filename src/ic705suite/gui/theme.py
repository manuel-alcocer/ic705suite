"""The graphical client's look: a radio front panel, dark or light, the same on Linux and
Windows.

The colours keep the terminal's meanings (cyan the rig, yellow the frequency, green the mode
and good values, red errors and transmission, dim the labels). ``use()`` picks the palette
before any window is made ([ui] theme, --theme: dark, light or auto, which follows the
system). The band scope's waterfall stays dark in both, as on any radio.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

THEMES = ("dark", "light", "auto")

PALETTES = {
    "dark": {
        "BACKGROUND": "#0b0e13", "PANEL": "#12171f", "RAISED": "#1a212c", "BORDER": "#26303d",
        "FOCUS": "#3fa7d6", "TEXT": "#d6dde6", "DIM": "#7b8796", "CYAN": "#4fc3f7",
        "YELLOW": "#ffc94d", "AMBER": "#ffb000", "GREEN": "#43d17a", "RED": "#ff5a5a",
        "MAGENTA": "#e070ff", "BLUE": "#78b4ff", "SELECTED": "#1f3b57",
    },
    "light": {
        "BACKGROUND": "#e9edf2", "PANEL": "#ffffff", "RAISED": "#eef1f5", "BORDER": "#c6ced8",
        "FOCUS": "#1f78c1", "TEXT": "#1b2430", "DIM": "#5f6b7a", "CYAN": "#0a6f99",
        "YELLOW": "#8a5a00", "AMBER": "#b34d00", "GREEN": "#16793d", "RED": "#c62828",
        "MAGENTA": "#8e24aa", "BLUE": "#1f5fbf", "SELECTED": "#cfe2f6",
    },
}

# Backgrounds with black text on them (badges, a CQ marked, the option under the cursor):
# vivid, and the same in both themes so they always read.
BG_YELLOW = "#ffc94d"
BG_GREEN = "#43d17a"
BG_MAGENTA = "#e070ff"
BG_CYAN = "#4fc3f7"
BG_BLUE = "#78b4ff"

NAME = "dark"
BACKGROUND = PANEL = RAISED = BORDER = FOCUS = TEXT = DIM = ""
CYAN = YELLOW = AMBER = GREEN = RED = MAGENTA = BLUE = SELECTED = ""


def use(name: str) -> str:
    """Make `name` ("dark" or "light"; "auto" asks the system) the palette of what is drawn
    from now on. Returns the one chosen."""
    global NAME
    if name == "auto":
        name = system_theme()
    if name not in PALETTES:
        name = "dark"
    NAME = name
    globals().update(PALETTES[name])
    return name


def system_theme() -> str:
    """The desktop's choice (Qt 6.5+ knows it once the application exists); dark if unknown."""
    from PySide6.QtCore import Qt

    app = QApplication.instance()
    try:
        scheme = app.styleHints().colorScheme() if app is not None else None
    except AttributeError:
        scheme = None
    return "light" if scheme == Qt.ColorScheme.Light else "dark"


use("dark")


def stylesheet() -> str:
    return f"""
QWidget {{
    background: {BACKGROUND};
    color: {TEXT};
    font-size: 10pt;
}}
QToolTip {{
    background: {RAISED};
    color: {TEXT};
    border: 1px solid {BORDER};
}}
Frame {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
Frame[active="true"] {{
    border: 1px solid {FOCUS};
}}
Frame QLabel#frame-title {{
    background: transparent;
    color: {CYAN};
    font-weight: bold;
    letter-spacing: 1px;
}}
Frame QLabel#frame-subtitle {{
    background: transparent;
    color: {DIM};
}}
Frame > QWidget {{
    background: transparent;
}}
QLabel {{
    background: transparent;
}}
QTableWidget {{
    background: {PANEL};
    alternate-background-color: {PANEL};
    border: none;
    gridline-color: {BORDER};
    selection-background-color: {SELECTED};
    selection-color: {TEXT};
    outline: none;
}}
QTableWidget::item:selected {{
    background: {SELECTED};
}}
QHeaderView::section {{
    background: {RAISED};
    color: {DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 3px 6px;
    font-weight: bold;
}}
QLineEdit {{
    background: {RAISED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {SELECTED};
}}
QLineEdit:focus {{
    border: 1px solid {FOCUS};
}}
QPlainTextEdit, QTextEdit {{
    background: {PANEL};
    border: none;
}}
QPushButton {{
    background: {RAISED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 14px;
}}
QPushButton:hover {{
    border: 1px solid {CYAN};
}}
QPushButton:default, QPushButton:focus {{
    border: 1px solid {FOCUS};
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {PANEL};
    width: 10px;
    height: 10px;
}}
QScrollBar::handle {{
    background: {BORDER};
    border-radius: 4px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QSplitter::handle {{
    background: {BACKGROUND};
}}
QDialog {{
    background: {PANEL};
}}
"""


def mono(size: float = 10, bold: bool = False) -> QFont:
    """The system's fixed-width font (numbers line up, as in the terminal)."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSizeF(size)
    font.setBold(bold)
    return font


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, colour in ((QPalette.ColorRole.Window, BACKGROUND),
                         (QPalette.ColorRole.Base, PANEL),
                         (QPalette.ColorRole.AlternateBase, RAISED),
                         (QPalette.ColorRole.Text, TEXT),
                         (QPalette.ColorRole.WindowText, TEXT),
                         (QPalette.ColorRole.Button, RAISED),
                         (QPalette.ColorRole.ButtonText, TEXT),
                         (QPalette.ColorRole.Highlight, SELECTED),
                         (QPalette.ColorRole.HighlightedText, TEXT),
                         (QPalette.ColorRole.PlaceholderText, DIM),
                         (QPalette.ColorRole.ToolTipBase, RAISED),
                         (QPalette.ColorRole.ToolTipText, TEXT)):
        palette.setColor(role, QColor(colour))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())
