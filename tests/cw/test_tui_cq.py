"""Stations calling CQ stand out in the CW application."""

import asyncio

from rich.text import Text
from textual.widgets import DataTable

from ic705suite.config import Config
from ic705suite.station.links import LocalLink
from tests.cw.test_tui_cursor_keys import _skimming

CALLING = {"index": 1, "frequency": 7_030_500, "callsign": "EA7XYZ", "wpm": 22.0,
           "snr_db": 14.0, "text": "CQ CQ CQ DE EA7XYZ EA7XYZ K"}
CONTEST = {"index": 3, "frequency": 7_031_900, "callsign": "DL1QRP", "wpm": 30.0,
           "snr_db": 12.0, "text": "CQ TEST DL1QRP F5ABC 5NN 14 TU DL1QRP"}
TALKING = {"index": 2, "frequency": 7_031_200, "callsign": "F5ABC", "wpm": 18.0,
           "snr_db": 9.0, "text": "DL1QRP DE F5ABC UR RST 599 NAME JEAN HW? K"}


def test_stations_calling_cq_are_highlighted(tmp_path):
    from ic705suite.apps.cw.tui import CALL_STYLES, CwApp, DetailPanel
    from ic705suite.ui.waterfall import Waterfall

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        app = CwApp(link, owns_link=False)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(1.5)
                await _skimming(app, pilot)
                apply = type(app)._apply_skim
                apply(app, [CALLING, TALKING, CONTEST])
                table = app.query_one("#skim", DataTable)
                first, second, third = (table.get_row_at(n) for n in range(3))
                assert isinstance(first[0], Text) and first[0].style == CALL_STYLES["cq"]
                assert "CQ" in first[2].plain
                assert second[0].style == "" and "CQ" not in second[2].plain
                assert third[0].style == CALL_STYLES["contest"] and "TEST" in third[2].plain

                scope = app.query_one("#skim-scope", Waterfall)
                assert scope.view.calling == {CALLING["frequency"]: "cq",
                                              CONTEST["frequency"]: "contest"}

                detail = app.query_one("#detail", DetailPanel)
                assert "CALLING CQ" in detail.render().plain     # the chosen one, calling
                await pilot.press("down")
                assert "CALLING CQ" not in detail.render().plain
                await pilot.press("down")
                assert "CONTEST" in detail.render().plain
        finally:
            await link.close()

    asyncio.run(run())


def test_cq_in_the_receive_pane_is_highlighted_and_marks_the_detail(tmp_path):
    from ic705suite.apps.cw.tui import CALL_STYLES, CwApp, DetailPanel, TextPane

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        app = CwApp(link, owns_link=False)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(1.5)
                detail = app.query_one("#detail", DetailPanel)
                app._on_event({"type": "rx", "text": "CQ CQ DE EA7XYZ EA7XYZ K "})
                assert detail.calling == "cq"
                rendered = app.query_one("#rx", TextPane).render()
                cq = [span for span in rendered.spans if span.style == CALL_STYLES["cq"]]
                assert len(cq) == 2
                app._on_event({"type": "rx", "text": "EA7XYZ DE F5ABC F5ABC UR 599 NAME JEAN "
                                                      "QTH SEVILLA RIG IC705 ANT DIPOLE "
                                                      "WX SUNNY TEMP 25C HW? K "})
                assert detail.calling is None
                app._on_event({"type": "rx", "text": "CQ TEST DL1QRP "})
                assert detail.calling == "contest"
                rendered = app.query_one("#rx", TextPane).render()
                assert any(span.style == CALL_STYLES["contest"] for span in rendered.spans)
        finally:
            await link.close()

    asyncio.run(run())
