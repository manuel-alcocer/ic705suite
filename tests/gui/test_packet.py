"""The packet terminal in the graphical client against hamrpkt's fake AGWPE node: the same
session as the terminal test (tests/packet/test_app.py), with the same keys."""

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ic705suite.apps.packet.config import Config, LinkConfig
from ic705suite.config import Config as SuiteConfig
from tests.gui.test_pages import key, until
from tests.packet.test_app import FakeNode


def test_packet_session_as_in_the_terminal(tmp_path, run, monkeypatch):
    monkeypatch.setenv("HAMRPKT_HOME", str(tmp_path))

    async def scenario():
        from ic705suite.gui.app import MainWindow
        from ic705suite.gui.packet import PacketPage

        node = FakeNode()
        server = await asyncio.start_server(node.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        config = Config("EA7WM-1", LinkConfig(type="agw", host="127.0.0.1", port=port))
        win = MainWindow(None, SuiteConfig(connection="lan", host="", swr_web=False))
        win.resize(1200, 800)
        win.show()
        win.activateWindow()
        page = PacketPage(win, config, tmp_path / "config.toml")
        win.stack.addWidget(page)
        win.show_page(page)
        try:
            await until(lambda: page.status.link_state == "up")
            await until(lambda: "EA1ABC-7" in page.heard)
            assert page.heard_table.row_count == 1

            await key(None, text="/c ea1abc")
            await key(Qt.Key.Key_Return)
            await until(lambda: page.session == "EA1ABC")
            assert page.status.session == "EA1ABC"

            await key(None, text="hola")
            await key(Qt.Key.Key_Return)
            await until(lambda: "EA1ABC BBS>" in page.terminal_view.toPlainText())
            assert "ECHO hola" in page.terminal_view.toPlainText()

            await key(Qt.Key.Key_Up)                               # the line typed before
            assert page.entry_panel.line.text() == "hola"
            await key(Qt.Key.Key_Escape)
            await key(Qt.Key.Key_F5)
            await until(lambda: page.session == "")

            await key(Qt.Key.Key_F3)                               # the heard stations
            assert page.view == "heard" and page.heard_table.isVisible()
            await key(Qt.Key.Key_Return)                           # Enter connects
            await until(lambda: page.session == "EA1ABC-7")
            await key(Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
            await until(lambda: QApplication.activeModalWidget() is not None)
            await key(None, text="y")                              # still connected: quit?
            await until(lambda: win.page is win.dashboard)
        finally:
            server.close()
            await server.wait_closed()
        logs = list((tmp_path / "sessions").glob("*_EA1ABC.log"))
        assert logs and "ECHO hola" in logs[0].read_text()

    run(scenario())
