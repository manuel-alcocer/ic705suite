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
