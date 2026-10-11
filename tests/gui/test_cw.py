"""The CW page of the graphical client with the simulator: the terminal's keys."""

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tests.gui.test_pages import key, until, window


def test_cw_keys_as_in_the_terminal(tmp_path, run):
    async def scenario():
        win, link = await window(tmp_path, start_app="cw")
        page = win.page
        try:
            await link.command("/f 7.012")
            # The decoder reads the simulator's CQ; it shows in the receive pane.
            await until(lambda: any("EA4SIM" in text for kind, text in page.rx.lines
                                    if kind == "rx"), timeout=40)
            # A macro: receive only, so the core says it was not sent (as in the terminal).
            await key(Qt.Key.Key_F2)
            await until(lambda: any("receive-only" in text or "solo recepción" in text
                                    for _kind, text in page.rx.lines))
            # Lines typed come back with Ctrl+P (and ↑ when not skimming).
            await key(None, text="/wpm 22")
            await key(Qt.Key.Key_Return)
            await until(lambda: page.state.get("key_wpm") == 22)
            await key(Qt.Key.Key_Up)
            assert page.entry.text() == "/wpm 22"
            page.entry.clear()
            await key(Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
            assert page.entry.text() == "/wpm 22"
            page.entry.clear()
            # F10: the skimmer, its stations chosen with ↓, Tab; Enter on an empty line tunes.
            await key(Qt.Key.Key_F10)
            await until(lambda: page.state.get("skimming"))
            assert page.main_stack.currentIndex() == 1
            await until(lambda: len(page.channels) >= 2, timeout=40)
            await key(Qt.Key.Key_Down)
            assert page.selected == 1
            await key(Qt.Key.Key_Tab)
            assert page.selected == 2 % len(page.channels)
            assert page.scope.view.chosen == page.channels[page.selected]["frequency"]
            await key(Qt.Key.Key_F10)
            await until(lambda: not page.state.get("skimming"))
            assert page.main_stack.currentIndex() == 0
            # F11: the channels, over the page; Esc goes back.
            await key(Qt.Key.Key_F11)
            await until(lambda: page.overlay is not None and page.overlay.rows)
            assert win.stack.currentWidget() is page.overlay
            await key(Qt.Key.Key_Return)                      # show the transcript
            await until(lambda: "EA4SIM" in page.overlay.transcript.toPlainText())
            await key(Qt.Key.Key_Escape)
            assert page.overlay is None and win.stack.currentWidget() is page
            # F1: the commands; Esc closes them.
            await key(Qt.Key.Key_F1)
            await until(lambda: QApplication.activeModalWidget() is not None)
            QApplication.activeModalWidget().reject()
            await asyncio.sleep(0.1)
        finally:
            await link.close()

    run(scenario())
