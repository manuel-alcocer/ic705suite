"""The graphical client's look: a dark radio front panel, the same on Linux and Windows.

The colours keep the terminal's meanings (cyan the rig, yellow the frequency, green the mode
and good values, red errors and transmission, dim the labels), on a near-black panel.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

BACKGROUND = "#0b0e13"
PANEL = "#12171f"
RAISED = "#1a212c"
BORDER = "#26303d"
FOCUS = "#3fa7d6"
TEXT = "#d6dde6"
DIM = "#7b8796"
CYAN = "#4fc3f7"
YELLOW = "#ffc94d"
AMBER = "#ffb000"
GREEN = "#43d17a"
RED = "#ff5a5a"
MAGENTA = "#e070ff"
BLUE = "#78b4ff"
SELECTED = "#1f3b57"

# Rich style names of the terminal screens, for the messages they share.
STYLE_COLOURS = {"cyan": CYAN, "yellow": YELLOW, "green": GREEN, "red": RED, "magenta": MAGENTA,
                 "white": TEXT, "dim": DIM, "blue": BLUE}

STYLESHEET = f"""
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
    app.setStyleSheet(STYLESHEET)
