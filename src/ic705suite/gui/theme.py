"""The graphical client's look: a radio front panel, dark or light, the same on Linux and
Windows.

The colours keep the terminal's meanings (cyan the rig, yellow the frequency, green the mode
and good values, red errors and transmission, dim the labels). ``use()`` picks the palette
before any window is made ([ui] theme, --theme: dark, light or auto, which follows the
system). The band scope's waterfall stays dark in both, as on any radio.
"""

from __future__ import annotations

import contextlib
import re

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QTableWidget, QWidget

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
QListWidget {{
    background: {PANEL};
    border: none;
    outline: none;
}}
QListWidget::item {{
    border-radius: 6px;
    padding: 4px;
}}
QListWidget::item:hover {{
    background: {RAISED};
}}
QListWidget::item:selected {{
    background: {SELECTED};
    color: {TEXT};
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
QPushButton:checked {{
    background: {SELECTED};
    border: 1px solid {FOCUS};
    color: {TEXT};
    font-weight: bold;
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
QLabel#statusbar {{
    background: {PANEL};
    border-bottom: 1px solid {BORDER};
}}
QLabel#loadbar {{
    background: {PANEL};
    border-top: 1px solid {BORDER};
}}
QLabel#footerbar {{
    background: {PANEL};
    border-top: 1px solid {BORDER};
}}
QLabel#heading {{
    color: {CYAN};
    font-weight: bold;
}}
QLabel#browsebar {{
    background: {SELECTED};
    padding: 4px 8px;
    font-weight: bold;
}}
QLabel#toast {{
    background: {RAISED};
    border: 1px solid {FOCUS};
    border-radius: 6px;
    padding: 8px 12px;
}}
QMenuBar {{
    background: {PANEL};
    border-bottom: 1px solid {BORDER};
    min-height: 26px;
}}
QWidget#menucorner {{
    background: transparent;
}}
QToolButton#cornerbutton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    color: {TEXT};
    font-size: 17px;
    font-weight: bold;
    min-width: 26px;
    padding: 0px 4px;
}}
QToolButton#cornerbutton:hover {{
    background: {RAISED};
    border-color: {BORDER};
}}
QToolButton#cornerbutton[talking="true"] {{
    background: {RED};
    border-color: {RED};
}}
QMenuBar::item:selected, QMenu::item:selected {{
    background: {SELECTED};
    color: {TEXT};
}}
QMenu {{
    background: {PANEL};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 4px 18px;
}}
"""


def quiet() -> str:
    """Halfway between the text and DIM: there and easy to read, but not in the way (the
    FT8 messages between other stations)."""
    a, b = QColor(TEXT), QColor(DIM)
    return QColor((a.red() + b.red()) // 2, (a.green() + b.green()) // 2,
                  (a.blue() + b.blue()) // 2).name()


def mono(size: float = 10, bold: bool = False) -> QFont:
    """The system's fixed-width font (numbers line up, as in the terminal)."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSizeF(size)
    font.setBold(bold)
    return font


def switch(name: str) -> str:
    """Change the theme of the running client: the palette, the stylesheet, and everything
    drawn again. Returns the one in use ("auto" resolved)."""
    before = dict(PALETTES[NAME])
    chosen = use(name)
    app = QApplication.instance()
    if app is None:
        return chosen
    apply(app)
    # A colour of the old palette becomes the same colour of the new one.
    mapping = {QColor(before[key]).name(): QColor(PALETTES[chosen][key])
               for key in before if before[key] != PALETTES[chosen][key]}
    for widget in app.allWidgets():
        if isinstance(widget, QTableWidget):
            _recolour_items(widget, mapping)
        elif isinstance(widget, QLabel) and "#" in widget.text():
            # Rich text written with the old colours (spans, rich_html): the same, in the new.
            widget.setText(re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: mapping.get(
                m.group(0).lower(), QColor(m.group(0))).name(), widget.text()))
        # Status lines, footers, text panes: drawn again with the new colours.
        for method in ("redraw", "refresh"):
            draw = getattr(type(widget), method, None)
            if draw is not None and not hasattr(QWidget, method):
                with contextlib.suppress(TypeError, RuntimeError):
                    draw(widget)
                break
        widget.update()
    return chosen


def _recolour_items(table: QTableWidget, mapping: dict[str, QColor]) -> None:
    from PySide6.QtCore import Qt

    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is None:
                continue
            for role in (Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.BackgroundRole):
                brush = item.data(role)
                if brush is None:
                    continue
                colour = brush.color() if hasattr(brush, "color") else QColor(brush)
                new = mapping.get(colour.name())
                if new is not None:
                    item.setData(role, new)


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
