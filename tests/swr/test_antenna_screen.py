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


def test_remote_control_switch(tmp_path, monkeypatch):
    """Off at start; R asks before switching it on, switches it off at once; S refuses
    an SWR sweep while it is off."""
    from ic705suite.apps.swr.app import SwrApp
    from ic705suite.ui.widgets import ChoiceScreen

    monkeypatch.setattr(service_module, "LEGACY_HISTORY", tmp_path / "none.json")

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False, tx_enabled=True,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        app = SwrApp(link, owns_link=False)
        swr = link.core.swr
        try:
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause(1.5)
                assert not swr.remote and app.state.get("remote") is False
                await pilot.press("f2", "s")                  # SWR sweep refused
                await pilot.pause(0.2)
                assert not swr.sweep.running and "R" in str(app.query_one("#message").render())
                await pilot.press("r")
                await pilot.pause(0.2)
                assert isinstance(app.screen, ChoiceScreen) and not swr.remote
                await pilot.press("escape")                   # left off
                await pilot.pause(0.2)
                assert not swr.remote
                await pilot.press("r", "enter")               # first answer: switch it on
                await pilot.pause(0.3)
                assert swr.remote and app.state["remote"]
                await pilot.press("r")                        # off without asking
                await pilot.pause(0.3)
                assert not swr.remote and not isinstance(app.screen, ChoiceScreen)
        finally:
            await link.close()

    asyncio.run(run())


def test_presets_from_the_keyboard(tmp_path, monkeypatch):
    """P: a preset (name, band, target) joins the list after the bands, ↓ reaches it, its
    position is its own, and a noise sweep aims at its target."""
    from ic705suite.apps.swr.antennas import PresetScreen
    from ic705suite.apps.swr.app import SwrApp
    from ic705suite.ui.widgets import ChoiceScreen

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
                bands = len(app.bands)
                await pilot.press("p")
                await pilot.pause(0.3)
                assert isinstance(app.screen, PresetScreen)
                await pilot.press("n", *typed("SSB casa"), "enter")
                await pilot.pause(0.3)
                assert isinstance(app.screen, ChoiceScreen)
                await pilot.press("enter")                          # the first band: 40m
                await pilot.pause(0.3)
                await pilot.press(*["backspace"] * 12, *typed("7.150"), "enter")
                await pilot.pause(0.3)
                assert [(p["name"], p["band"], p["target"]) for p in history.presets()] == \
                    [("SSB casa", "40m", 7_150_000)]
                await pilot.press("escape")
                await pilot.pause(0.3)
                assert len(app.bands) == bands + 1 and app.bands[-1]["name"] == "SSB casa"
                # ↓ past the bands: the preset; C saves its own position.
                await pilot.press(*["down"] * bands, "c", *typed("14.5"), "enter")
                await pilot.pause(0.3)
                settings = history.settings()
                assert settings["preset:1"]["coil"] == 14.5 and "40m" not in settings
                await pilot.press("n")                              # a noise sweep, at 7.150
                await pilot.pause(0.5)
                sweep = link.core.swr.sweep
                assert sweep.preset == 1 and sweep.target == 7_150_000 and sweep.band == "40m"
                await pilot.press("escape")
        finally:
            await link.close()

    asyncio.run(run())


def test_history_from_the_keyboard(tmp_path, monkeypatch):
    """H: the band's sweeps and positions, newest first; Enter shows one with its curve; there
    C changes the position, O the note, F adds a photo from a file, V opens it, X removes it
    and D deletes the entry."""
    from ic705suite.apps.swr import history as history_module
    from ic705suite.apps.swr.app import SwrApp
    from ic705suite.apps.swr.history import EntryScreen, HistoryScreen

    monkeypatch.setattr(service_module, "LEGACY_HISTORY", tmp_path / "none.json")
    opened = []
    monkeypatch.setattr(history_module, "open_with_viewer", opened.append)
    photo = tmp_path / "antenna.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 100)
    points = [{"f": 7_000_000 + i * 10_000, "v": 1.1 + abs(i - 10) / 10} for i in range(21)]

    def typed(text):
        names = {" ": "space", "-": "minus", ".": "full_stop", "/": "slash", "_": "underscore"}
        return [names.get(c, c) for c in text]

    async def run():
        link = LocalLink(None, Config(connection="sim", swr_web=False,
                                      database_url=f"sqlite:///{tmp_path / 'suite.db'}"))
        swr = link.core.swr
        swr.photos_dir = tmp_path / "photos"
        swr.history.add({"ts": "2026-10-11 10:00:00", "band": "40m", "kind": "swr",
                         "target": 7_100_000, "points": points, "confident": True,
                         "resonance": 7_100_000, "best": 1.1, "at_edge": ""})
        swr.history.add({"ts": "2026-10-11 10:05:00", "band": "40m", "kind": "final",
                         "coil": 12.0})
        swr.history.add({"ts": "2026-10-11 10:06:00", "band": "20m", "kind": "final",
                         "coil": 3.0})
        app = SwrApp(link, owns_link=False)
        try:
            async with app.run_test(size=(140, 50)) as pilot:
                await pilot.pause(1.5)
                await pilot.press("f2", "h")
                await pilot.pause(0.5)
                screen = app.screen
                assert isinstance(screen, HistoryScreen)
                assert [e["kind"] for e in screen.entries] == ["final", "swr"]  # 40m, newest first
                await pilot.press("down", "enter")
                await pilot.pause(0.5)
                assert isinstance(app.screen, EntryScreen)
                shown = str(app.screen.query_one("#entry-details").render())
                assert "7.100.00" in shown and "█" in shown          # the curve
                await pilot.press("c", *typed("11.5"), "enter")
                await pilot.pause(0.3)
                await pilot.press("o", *typed("after rain"), "enter")
                await pilot.pause(0.3)
                entry = swr.history.entry(1)
                assert entry["coil"] == 11.5 and entry["note"] == "after rain"
                await pilot.press("f", *typed(str(photo)), "enter")
                await pilot.pause(0.5)
                name = swr.history.entry(1)["photo"]
                assert (swr.photos_dir / name).read_bytes() == photo.read_bytes()
                await pilot.press("v")
                await pilot.pause(0.5)
                assert opened and opened[0].read_bytes() == photo.read_bytes()
                await pilot.press("x")
                await pilot.pause(0.3)
                assert "photo" not in swr.history.entry(1)
                assert not (swr.photos_dir / name).exists()
                await pilot.press("d")
                await pilot.pause(0.3)
                await pilot.press("tab", "enter")                  # Delete
                await pilot.pause(0.5)
                assert isinstance(app.screen, HistoryScreen)
                assert [e["kind"] for e in app.screen.entries] == ["final"]
                await pilot.press("escape")
        finally:
            await link.close()

    asyncio.run(run())
