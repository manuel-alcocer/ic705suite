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
