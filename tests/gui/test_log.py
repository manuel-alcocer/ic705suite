"""The logbook in the graphical client: hamrlog's own code behind Qt widgets, its keys."""

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ic705suite.config import Config
from tests.gui.test_pages import key, until


async def logbook(tmp_path):
    from ic705suite.gui.app import MainWindow

    config = Config(connection="lan", host="", swr_web=False,
                    database_url=f"sqlite:///{tmp_path / 'suite.db'}")
    win = MainWindow(None, config, start_app="log")
    win.resize(1300, 850)
    win.show()
    win.activateWindow()
    await asyncio.sleep(0.2)
    return win


async def fill_dialog(*texts: str) -> None:
    """Type into the boxes of the dialog that opens, then Enter."""
    from PySide6.QtWidgets import QLineEdit

    await until(lambda: QApplication.activeModalWidget() is not None)
    boxes = QApplication.activeModalWidget().findChildren(QLineEdit)
    for box, text in zip(boxes, texts, strict=False):
        box.setText(text)
    boxes[0].setFocus()
    await key(Qt.Key.Key_Return)


def test_logbook_keys_as_in_the_terminal(tmp_path, run):
    async def scenario():
        win = await logbook(tmp_path)
        page = win.page
        from ic705suite.apps.log.core.services import QsoService

        # The first run asks for the operator, as hamrlog does.
        await fill_dialog("EA7KLX", "Manuel", "IM77")
        await until(lambda: page.state.operator_id is not None)
        page.entry.focus_input()
        # A command in the entry line.
        await key(None, text="/band 20m")
        await key(Qt.Key.Key_Return)
        assert page.state.band == "20m"
        # A contact: the call and Enter, from any box.
        await key(None, text="EA4ABC")
        await key(Qt.Key.Key_Tab)
        await key(None, text="Pepe")
        await key(Qt.Key.Key_Return)
        await until(lambda: len(QsoService.recent()) == 1)
        assert "EA4ABC" in page.entry.message
        assert page.history.qso_count == 1
        # ↑: on the logged QSO the line becomes the action bar; E edits it.
        await key(Qt.Key.Key_Up)
        assert page.entry.browsing and QApplication.focusWidget() is page.browse_bar
        await key(None, text="e")
        assert page.entry.editing
        name = next(b for b in page.entry.fields if b.field_name == "name")
        name.setText("José")
        await key(Qt.Key.Key_Return)
        await until(lambda: QsoService.recent()[0].name == "José")
        # D asks first; N keeps it.
        await key(None, text="d")
        await until(lambda: QApplication.activeModalWidget() is not None)
        await key(None, text="n")
        await until(lambda: QApplication.activeModalWidget() is None)
        assert len(QsoService.recent()) == 1
        # V: the QSO's card; Esc closes it.
        await key(None, text="v")
        await until(lambda: QApplication.activeModalWidget() is not None)
        await key(Qt.Key.Key_Escape)
        await until(lambda: QApplication.activeModalWidget() is None)
        # Esc: back to the insert row, typing again.
        await key(Qt.Key.Key_Escape)
        assert not page.entry.browsing
        # F2: the inventory, in place of the log; F1 back.
        await key(Qt.Key.Key_F2)
        assert page._view == "inventory" and page.inventory.isVisible()
        await key(Qt.Key.Key_F1)
        assert page._view == "log" and page.history.isVisible()
        # Ctrl+D on the insert row: the last QSO, after asking; Y deletes it.
        await key(Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        await until(lambda: QApplication.activeModalWidget() is not None)
        await key(None, text="y")
        await until(lambda: not QsoService.recent())
        # Ctrl+Q: out of the logbook (on its own, the window is done).
        await key(Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
        await until(lambda: win.finished.done())

    run(scenario())
