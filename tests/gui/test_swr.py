"""The Antenna page of the graphical client with the simulator: the terminal's keys."""

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from tests.gui.test_pages import key, until, window


async def answer(text: str) -> None:
    """Click the button `text` of the dialog that opens."""
    await until(lambda: QApplication.activeModalWidget() is not None)
    dialog = QApplication.activeModalWidget()
    next(b for b in dialog.findChildren(QPushButton) if b.text() == text).click()
    await asyncio.sleep(0.05)


def test_antenna_keys_as_in_the_terminal(tmp_path, run, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))     # no swr-remote history

    async def scenario():
        win, link = await window(tmp_path, start_app="swr")
        page = win.page
        try:
            await until(lambda: page.bands and page.state)
            await key(Qt.Key.Key_F3)                                  # the second band
            assert page._band()["name"] == page.bands[1]["name"]
            await key(Qt.Key.Key_F2)
            # S without the remote control: refused, as in the terminal.
            await key(Qt.Key.Key_S)
            await until(lambda: "R" in page.message.text())
            # R asks first; switched on, then off at once.
            await key(Qt.Key.Key_R)
            await answer("Switch it on")
            await until(lambda: page.state.get("remote"))
            await key(Qt.Key.Key_R)
            await until(lambda: not page.state.get("remote"))
            # N: a noise sweep (receive only); Esc stops it while it runs.
            await key(Qt.Key.Key_N)
            await until(lambda: (page.state.get("sweep") or {}).get("running"))
            await key(Qt.Key.Key_Escape)
            await until(lambda: not (page.state.get("sweep") or {}).get("running"))
            assert win.page is page                                  # stopping is not leaving
            # A: the antennas, over the page; Esc back.
            await key(Qt.Key.Key_A)
            await until(lambda: page.overlay is not None and page.overlay.antennas)
            await key(Qt.Key.Key_Escape)
            await until(lambda: page.overlay is None)
            # Esc with nothing running leaves (on its own: the window is done).
            await key(Qt.Key.Key_Escape)
            await until(lambda: win.finished.done())
        finally:
            await link.close()

    run(scenario())


def test_presets_as_in_the_terminal(tmp_path, run, monkeypatch):
    """P: a preset (name, band, target) is made in the dialogs and joins the list; ↓ reaches
    it; a noise sweep aims at its target."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    async def scenario():
        from PySide6.QtWidgets import QLineEdit

        win, link = await window(tmp_path, start_app="swr")
        page = win.page
        try:
            await until(lambda: page.bands and page.state)
            bands = len(page.bands)
            await key(Qt.Key.Key_P)
            await until(lambda: page.overlay is not None)
            await key(Qt.Key.Key_N)
            await until(lambda: QApplication.activeModalWidget() is not None)
            QApplication.activeModalWidget().findChild(QLineEdit).setText("SSB casa")
            await key(Qt.Key.Key_Return)
            await answer("40m")
            await until(lambda: QApplication.activeModalWidget() is not None)
            QApplication.activeModalWidget().findChild(QLineEdit).setText("7.150")
            await key(Qt.Key.Key_Return)
            await until(lambda: page.overlay.presets)
            await key(Qt.Key.Key_Escape)
            await until(lambda: page.overlay is None and len(page.bands) == bands + 1)
            for _ in range(bands):
                await key(Qt.Key.Key_Down)
            assert page._band()["name"] == "SSB casa"
            await key(Qt.Key.Key_N)
            await until(lambda: (page.state.get("sweep") or {}).get("preset") == 1)
            assert page.state["sweep"]["target"] == 7_150_000
            await key(Qt.Key.Key_Escape)
        finally:
            await link.close()

    run(scenario())


def test_history_as_in_the_terminal(tmp_path, run, monkeypatch):
    """H: the band's history; Enter shows an entry with its curve and photo; C, O, F, X and
    D as in the terminal; Esc goes back to the list, and from there to the page."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from ic705suite.apps.swr import service as service_module
    from ic705suite.gui import swr as swr_page

    # Not the history of before the database (its path is fixed when the module loads).
    monkeypatch.setattr(service_module, "LEGACY_HISTORY", tmp_path / "none.json")

    photo = tmp_path / "antenna.png"
    from PySide6.QtGui import QImage

    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkGreen)
    image.save(str(photo))

    async def picked(_parent):
        return str(photo)

    monkeypatch.setattr(swr_page, "pick_photo", picked)
    points = [{"f": 7_000_000 + i * 10_000, "v": 1.1 + abs(i - 10) / 10} for i in range(21)]

    async def scenario():
        from PySide6.QtWidgets import QLineEdit

        win, link = await window(tmp_path, start_app="swr")
        page = win.page
        history = link.core.swr.history
        history.add({"ts": "2026-10-11 10:00:00", "band": "40m", "kind": "swr",
                     "target": 7_100_000, "points": points, "confident": True,
                     "resonance": 7_100_000, "best": 1.1, "at_edge": ""})
        try:
            await until(lambda: page.bands and page.state)
            await key(Qt.Key.Key_F2)
            await key(Qt.Key.Key_H)
            await until(lambda: isinstance(page.overlay, swr_page.HistoryView)
                        and page.overlay.entries)
            listing = page.overlay
            # The chosen row's curve beside the list.
            await until(lambda: listing.preview.sweep.get("points") == points)
            await key(Qt.Key.Key_Return)
            await until(lambda: isinstance(page.overlay, swr_page.EntryView)
                        and page.overlay.entry)
            view = page.overlay
            assert view.chart.isVisibleTo(view) and view.chart.sweep["points"] == points
            await key(Qt.Key.Key_C)
            await until(lambda: QApplication.activeModalWidget() is not None)
            QApplication.activeModalWidget().findChild(QLineEdit).setText("11,5")
            await key(Qt.Key.Key_Return)
            await until(lambda: view.entry.get("coil") == 11.5)
            await key(Qt.Key.Key_O)
            await until(lambda: QApplication.activeModalWidget() is not None)
            QApplication.activeModalWidget().findChild(QLineEdit).setText("after rain")
            await key(Qt.Key.Key_Return)
            await until(lambda: view.entry.get("note") == "after rain")
            await key(Qt.Key.Key_F)
            await until(lambda: view._pixmap is not None)
            assert view._pixmap.width() == 40 and history.entry(1).get("photo")
            await key(Qt.Key.Key_X)
            await until(lambda: view._pixmap is None and "photo" not in view.entry)
            await key(Qt.Key.Key_Escape)                     # back to the list
            await until(lambda: page.overlay is listing)
            await until(lambda: "11.5" in listing.table.item(0, 2).text())
            await key(Qt.Key.Key_D)
            await answer("Delete")
            await until(lambda: not listing.entries)
            await key(Qt.Key.Key_Escape)
            await until(lambda: page.overlay is None)
        finally:
            await link.close()

    run(scenario())


def test_the_chosen_bands_history_and_its_latest_curve(tmp_path, run, monkeypatch):
    """Below the bands, the chosen one's history; with no sweep of its own in the core the
    chart shows its latest saved curve, and a click on a row shows that one's."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from ic705suite.apps.swr import service as service_module

    monkeypatch.setattr(service_module, "LEGACY_HISTORY", tmp_path / "none.json")
    older = [{"f": 7_000_000 + i * 20_000, "v": 2.0 - i / 20} for i in range(11)]
    newer = [{"f": 7_000_000 + i * 10_000, "v": 1.1 + abs(i - 10) / 10} for i in range(21)]

    async def scenario():
        win, link = await window(tmp_path, start_app="swr")
        page = win.page
        history = link.core.swr.history
        history.add({"ts": "2026-10-10 10:00:00", "band": "40m", "kind": "swr",
                     "target": 7_074_000, "points": older})
        history.add({"ts": "2026-10-11 10:00:00", "band": "40m", "kind": "swr",
                     "target": 7_074_000, "points": newer})
        history.add({"ts": "2026-10-11 10:00:00", "band": "20m", "kind": "final", "coil": 3.0})
        try:
            await until(lambda: page.bands and page.state)
            await key(Qt.Key.Key_F3)
            await key(Qt.Key.Key_F2)                       # 40m: its history is loaded
            await until(lambda: len(page.recent) == 2)
            await until(lambda: page.chart.sweep.get("points") == newer)
            page.recent_table.cellClicked.emit(1, 0)       # the older one
            await until(lambda: page.chart.sweep.get("points") == older)
            await key(Qt.Key.Key_F4)                       # 20m: a position, no curve
            await until(lambda: len(page.recent) == 1 and not page.chart.sweep)
        finally:
            await link.close()

    run(scenario())
