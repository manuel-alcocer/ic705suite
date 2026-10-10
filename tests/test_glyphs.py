"""Console mode: what the rig view draws must fit a Linux console font."""

import asyncio

import pytest

from ic705suite.config import Config
from ic705suite.station.links import LocalLink
from ic705suite.ui import glyphs


def cp437() -> frozenset[str]:
    chars = set(bytes(range(32, 256)).decode("cp437")) | set("←↑→↓·")
    return frozenset(chars | {chr(c) for c in range(0xA0, 0x100)})


# A Terminus ter-1* font (full block and two shades, no half blocks), and the IBM PC set.
FONTS = {"terminus": glyphs.MINIMAL_CHARSET, "cp437": cp437()}


@pytest.fixture(params=sorted(FONTS))
def font(request):
    charset = FONTS[request.param]
    glyphs.configure("console", environ={}, charset=charset)
    yield charset
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


def test_fallbacks_follow_the_font():
    table = glyphs.translation(glyphs.MINIMAL_CHARSET)
    assert "▀".translate(table) == "-" and "▓".translate(table) == "▒" and "┃".translate(table) == "│"
    assert "█░▒■".translate(table) == "█░▒■"
    table = glyphs.translation(cp437())
    assert "▀▓".translate(table) == "▀▓" and "┃".translate(table) == "║"
    assert all(len(c) == 1 for fallback in glyphs.FALLBACKS.values() for c in fallback)


def test_block_font():
    from ic705suite.ui.bigdigits import block_rows

    rows = block_rows("14.074.00")
    assert len(rows) == 3 and len({len(r) for r in rows}) == 1
    assert rows[0].startswith("▄█  █ █")
    rows = block_rows("14.074.00", halves=False)
    assert len(rows) == 5 and set("".join(rows)) == {" ", "█"}


def test_rig_view_on_the_console(tmp_path, font):
    from ic705suite.apps.view.app import ViewApp
    from ic705suite.ui.bigdigits import BigDigits

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
                digits = app.query_one(BigDigits)
                assert digits.size.height == (3 if "▀" in font else 5)
                # Whatever is left (from Textual or the logbook) the driver replaces.
                shown = glyphs.fit(screen)
                assert sorted({c for c in shown if c not in font and c != "\n"}) == []
        finally:
            await link.close()

    asyncio.run(run())
