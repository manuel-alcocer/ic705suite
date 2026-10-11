"""The cursor keys of the CW application: skimmer stations while skimming, earlier lines
otherwise."""

import asyncio

from textual.widgets import DataTable, Input

from ic705suite.config import Config
from ic705suite.station.links import LocalLink

STATIONS = [
    {"index": n, "frequency": 7_020_000 + 500 * n, "callsign": call, "wpm": 22.0,
     "snr_db": 12.0, "text": f"CQ CQ DE {call} {call} K"}
    for n, call in enumerate(("EA7XYZ", "F5ABC", "DL1QRP"), start=1)
]


async def _skimming(app, pilot) -> DataTable:
    """Skimming with three stations; the simulator's own (often none yet) are kept out."""
    await pilot.press("f10")
    for _ in range(30):
        await pilot.pause(0.1)
        if app.state.get("skimming"):
            break
    assert app.state.get("skimming")
    app._apply_skim(STATIONS)
    app._apply_skim = lambda channels: None
    return app.query_one("#skim", DataTable)


def test_up_and_down_choose_a_skimmer_station(tmp_path):
    from ic705suite.apps.cw.tui import CwApp, DetailPanel

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        app = CwApp(link, owns_link=False)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(1.5)
                table = await _skimming(app, pilot)
                detail = app.query_one("#detail", DetailPanel)
                start = table.cursor_row
                await pilot.press("down")
                assert table.cursor_row == (start + 1) % table.row_count
                assert detail.selected == table.cursor_row
                await pilot.press("up")
                assert table.cursor_row == start
                await pilot.press("up")                 # wraps round, like Shift+Tab
                assert table.cursor_row == (start - 1) % table.row_count
        finally:
            await link.close()

    asyncio.run(run())


def test_up_and_down_recall_earlier_lines_when_not_skimming(tmp_path):
    from ic705suite.apps.cw.tui import CwApp

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        app = CwApp(link, owns_link=False)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(1.5)
                field = app.query_one("#entry-input", Input)
                await pilot.press("slash", "c", "l", "e", "a", "r", "enter")
                await pilot.press("up")
                assert field.value == "/clear"
                await pilot.press("down")
                assert field.value == ""
        finally:
            await link.close()

    asyncio.run(run())


def test_the_skimmer_shows_its_range_of_the_waterfall_with_the_stations(tmp_path):
    from ic705suite.apps.cw.tui import CwApp
    from ic705suite.ui.waterfall import Waterfall

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        app = CwApp(link, owns_link=False)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(1.5)
                scope = app.query_one("#skim-scope", Waterfall)
                assert not scope.display                # only while skimming
                await _skimming(app, pilot)
                assert scope.display
                # Shown, it is laid out on a later refresh (slow on a busy CI runner).
                for _ in range(50):
                    if scope.size.height:
                        break
                    await pilot.pause(0.1)
                assert scope.size.height == 8
                for _ in range(50):
                    await pilot.pause(0.1)
                    if scope._raw:
                        break
                assert scope._raw, "no sweep reached the skimmer's waterfall"
                lo, hi = app.state["skim_range"]
                # Cut down to the skimmer's range and as much again around it.
                assert scope.view.start <= lo and hi <= scope.view.end
                assert scope.view.end - scope.view.start <= 3 * max(hi - lo, 200)
                assert [label for _, label in scope.view.marks] == ["1", "2", "3"]
                assert scope.view.chosen == STATIONS[0]["frequency"]
                await pilot.press("down")
                assert scope.view.chosen == STATIONS[1]["frequency"]
                await pilot.press("f10")                # back to one decoder: no waterfall
                for _ in range(30):
                    await pilot.pause(0.1)
                    if not app.state.get("skimming"):
                        break
                assert not scope.display
        finally:
            await link.close()

    asyncio.run(run())
