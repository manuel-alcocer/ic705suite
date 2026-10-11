"""The light theme: every colour drawn follows the palette in use; the waterfall stays dark."""

import asyncio

import pytest
from rich.text import Text

from ic705suite.gui import theme


@pytest.fixture
def light():
    theme.use("light")
    yield
    theme.use("dark")


def test_the_palette_in_use_is_what_is_drawn(light):
    from ic705suite.gui.cw import line_style
    from ic705suite.gui.widgets import rich_html

    assert theme.NAME == "light" and theme.TEXT == theme.PALETTES["light"]["TEXT"]
    assert theme.PALETTES["light"]["BACKGROUND"] in theme.stylesheet()
    assert line_style("tx")[0] == theme.PALETTES["light"]["YELLOW"]
    assert theme.PALETTES["light"]["GREEN"] in rich_html(Text("ok", style="green"))
    # Behind black text, the vivid colours, whatever the theme.
    assert theme.BG_YELLOW in rich_html(Text(" DEMO ", style="bold black on yellow"))


def test_auto_follows_the_system_and_unknown_names_are_dark(qapp):
    assert theme.use("auto") in ("dark", "light")
    assert theme.use("purple") == "dark"


def test_the_waterfall_is_dark_in_the_light_theme(light, qapp):
    from PySide6.QtGui import QColor

    from ic705suite.gui.widgets import Scope

    scope = Scope()
    scope.resize(200, 120)
    image = scope.grab().toImage()
    assert image.pixelColor(100, 110) == QColor("#000000")


def test_help_menu_and_the_version_always_shown(tmp_path, run):
    """? opens About: the version and the project's and the help's links; every status
    line ends with the version."""
    from PySide6.QtWidgets import QMessageBox

    from ic705suite import __version__
    from ic705suite.gui.app import HELP_URL, PROJECT_URL
    from tests.gui.test_pages import window

    async def scenario():
        win, link = await window(tmp_path)
        try:
            assert win.help_button.text() == "?"
            win.help_button.click()
            box = win.findChild(QMessageBox)
            assert f"ic705suite {__version__}" in box.text()
            assert PROJECT_URL in box.text() and HELP_URL in box.text()
            box.close()
            assert f"v{__version__}" in win.dashboard.status.text()
        finally:
            await link.close()

    run(scenario())


def test_terminal_status_lines_keep_the_version():
    from rich.text import Text

    from ic705suite import __version__
    from ic705suite.ui.sysload import with_load

    for width in (200, 60, 20):
        line = with_load(Text("IC-705 · QRG 7.030.00 · MODE CW · LINK local"), width)
        assert line.plain.rstrip().endswith(f"v{__version__}") and line.cell_len <= width


def test_theme_button_switches_the_theme_at_once_and_keeps_it(tmp_path, run):
    """The theme button goes light, dark, auto showing the one in use; the palette changes
    on the open window (the status line's colours too) and [ui] theme is written in the
    configuration file, the rest of it unchanged."""
    from ic705suite.gui import theme
    from tests.gui.test_pages import window

    config_file = tmp_path / "config.toml"
    config_file.write_text('# mine\n[station]\ncallsign = "EA7KLX"\n', encoding="utf-8")

    async def scenario():
        win, link = await window(tmp_path)
        try:
            win.config.source = config_file
            win.set_theme("dark")
            dark_cyan = theme.CYAN
            assert dark_cyan in win.dashboard.status.text()
            win.set_theme("light")
            assert theme.NAME == "light" and theme.CYAN != dark_cyan
            assert win.theme_button.text() == "\u2600"        # the sun
            win.theme_button.click()
            assert win.config.theme == "dark" and win.theme_button.text() == "\u263e"
            win.theme_button.click()
            assert win.config.theme == "auto" and win.theme_button.text() == "\u25d0"
            win.theme_button.click()
            assert win.config.theme == "light"
            assert theme.CYAN in win.dashboard.status.text()
            assert dark_cyan not in win.dashboard.status.text()
            saved = config_file.read_text(encoding="utf-8")
            assert 'theme = "light"' in saved and 'callsign = "EA7KLX"' in saved
            assert saved.startswith("# mine")
        finally:
            theme.switch("dark")
            await link.close()

    run(scenario())


def test_cw_macro_keys_are_short_and_question_lists_them(tmp_path, run):
    """F2-F9 show the key only (the window stays narrow); ? lists what each sends."""
    from PySide6.QtWidgets import QApplication, QPlainTextEdit

    from tests.gui.test_pages import window

    async def scenario():
        win, link = await window(tmp_path, start_app="cw")
        page = win.page
        try:
            await asyncio.sleep(1.0)
            assert [b.text() for b in page.macro_buttons.values()] == \
                [f"F{n}" for n in range(2, 10)]
            assert "CQ" in page.macro_buttons["F2"].toolTip()
            page.macro_help.click()
            for _ in range(50):
                if QApplication.activeModalWidget() is not None:
                    break
                await asyncio.sleep(0.05)
            dialog = QApplication.activeModalWidget()
            shown = dialog.findChild(QPlainTextEdit).toPlainText()
            assert shown.startswith("F2  CQ") and "F9" in shown
            dialog.reject()
        finally:
            await link.close()

    run(scenario())
