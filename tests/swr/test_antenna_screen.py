"""Antennas from the keyboard: add one, make it active, save a position, switch back."""

import asyncio

from ic705suite.apps.swr import service as service_module
from ic705suite.config import Config
from ic705suite.station.links import LocalLink


def test_antennas_from_the_keyboard(tmp_path, monkeypatch):
    from ic705suite.apps.swr.antennas import AntennaScreen
    from ic705suite.apps.swr.app import SwrApp

    monkeypatch.setattr(service_module, "LEGACY_HISTORY", tmp_path / "none.json")

    def typed(text):
        names = {" ": "space", "-": "minus", ".": "full_stop"}
        return [names.get(c, c) for c in text]

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        app = SwrApp(link, owns_link=False)
        history = link.core.swr.history
        try:
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause(1.5)
                assert app.antenna["name"] == "Antenna 1"
                await pilot.press("a")
                await pilot.pause(0.3)
                assert isinstance(app.screen, AntennaScreen)
                await pilot.press("n", *typed("Komunica HF-PRO-2-PLUS-T"), "enter")
                await pilot.pause(0.2)
                await pilot.press(*typed("vertical"), "enter")
                await pilot.pause(0.2)
                await pilot.press("enter")          # first answer: higher number, lower freq
                await pilot.pause(0.3)
                komunica = history.antenna()
                assert komunica["name"] == "Komunica HF-PRO-2-PLUS-T"
                assert komunica["coil_sign"] == -1 and komunica["notes"] == "vertical"
                await pilot.press("escape")
                await pilot.pause(0.3)
                assert "Komunica" in app.query_one("#sweep").border_title
                await pilot.press("f2", "c", *typed("12.2"), "enter")   # 40m position
                await pilot.pause(0.3)
                assert history.settings()["40m"]["coil"] == 12.2
                # Back to the first antenna: its own (empty) settings.
                await pilot.press("a")
                await pilot.pause(0.3)
                screen = app.screen
                names = [a["name"] for a in screen.antennas]
                await pilot.press(*["up"] * 3, *["down"] * names.index("Antenna 1"), "enter")
                await pilot.pause(0.3)
                assert history.antenna()["name"] == "Antenna 1" and history.settings() == {}
        finally:
            await link.close()

    asyncio.run(run())
