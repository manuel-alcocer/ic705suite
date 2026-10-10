"""Console mode: what the rig view draws must fit a Linux console font."""

import asyncio

import pytest

from ic705suite.config import Config
from ic705suite.station.links import LocalLink
from ic705suite.ui import glyphs
from ic705suite.ui.bigdigits import block_rows


def fits_a_console_font(char: str) -> bool:
    """CP437 (box drawing, blocks, shades), its arrows, and Latin-1 letters."""
    if ord(char) < 128 or char in "←↑→↓" or 0xC0 <= ord(char) <= 0xFF and char.isalpha():
        return True
    try:
        char.encode("cp437")
    except UnicodeEncodeError:
        return False
    return True


@pytest.fixture
def console():
    glyphs.configure("console", environ={})
    yield
    glyphs.configure("unicode", environ={})


@pytest.mark.parametrize("environ, console", [
    ({"TERM": "linux", "LANG": "es_ES.UTF-8"}, True),
    ({"TERM": "xterm-256color", "LANG": "es_ES.UTF-8"}, False),
    ({"TERM": "tmux-256color", "LANG": "es_ES.ISO-8859-1"}, True),
    ({"TERM": "xterm-256color", "LANG": "es_ES.UTF-8", "IC705SUITE_GLYPHS": "console"}, True),
    ({"TERM": "linux", "IC705SUITE_GLYPHS": "unicode"}, False),
])
def test_console_detection(environ, console):
    try:
        assert glyphs.configure("auto", environ=environ) is console
        assert (glyphs.BAR == "■") is console
    finally:
        glyphs.configure("unicode", environ={})


def test_block_font():
    rows = block_rows("14.074.00")
    assert len(rows) == 3 and len({len(r) for r in rows}) == 1
    assert all(fits_a_console_font(c) for c in "".join(rows))
    assert rows[0].startswith("▄█  █ █")


def test_rig_view_on_the_console(tmp_path, console):
    from ic705suite.apps.view.app import ViewApp

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        await link.connect()
        app = ViewApp(link)
        try:
            async with app.run_test(size=(140, 46)) as pilot:
                await pilot.pause(1.5)
                strips = app.screen._compositor.render_strips()
                screen = "\n".join(strip.text for strip in strips)
                assert "█" in screen and "░" in screen          # block digits, shaded waterfall
                for char in "▁▂▃▅▆▇▮┃╭╮":
                    assert char not in screen, char
                # Whatever is left (from Textual or the logbook) the driver replaces.
                shown = screen.translate(glyphs.CONSOLE_TRANSLATION)
                assert sorted({c for c in shown if not fits_a_console_font(c)}) == []
        finally:
            await link.close()

    asyncio.run(run())
