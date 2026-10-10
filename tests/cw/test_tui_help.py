"""The CW application's help, also while the skimmer hides the message pane."""

import asyncio

from ic705suite.config import Config
from ic705suite.station.links import LocalLink


def test_help_while_skimming(tmp_path):
    from ic705suite.apps.cw.tui import CwApp
    from ic705suite.ui.widgets import TextScreen

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        app = CwApp(link, owns_link=False)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(1.5)
                await pilot.press("f10")
                for _ in range(30):
                    await pilot.pause(0.1)
                    if app.state.get("skimming"):
                        break
                assert app.state.get("skimming") and not app.query_one("#rx").display
                await pilot.press("slash", "h", "e", "l", "p", "enter")
                await pilot.pause(0.2)
                assert isinstance(app.screen, TextScreen)
                assert any("/skim" in line for line in app.screen.lines)
                await pilot.press("escape")
                await pilot.pause(0.2)
                assert not isinstance(app.screen, TextScreen) and app.state.get("skimming")
                await pilot.press("f1")                 # F1 opens it too
                await pilot.pause(0.2)
                assert isinstance(app.screen, TextScreen)
                await pilot.press("f1")
        finally:
            await link.close()

    asyncio.run(run())
