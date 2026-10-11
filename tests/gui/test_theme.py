"""The light theme: every colour drawn follows the palette in use; the waterfall stays dark."""

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
    """Help > About says the version; every status line ends with it."""
    from ic705suite import __version__
    from tests.gui.test_pages import window

    async def scenario():
        win, link = await window(tmp_path)
        try:
            menus = [a.text() for a in win.menuBar().actions()]
            assert menus == ["Help"]
            items = [a.text() for a in win.menuBar().actions()[0].menu().actions() if a.text()]
            assert items == ["Project page", "About ic705suite"]
            box = win.show_about()
            assert f"ic705suite {__version__}" in box.text()
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
