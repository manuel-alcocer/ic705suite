"""The packet terminal in the graphical client against the fake Direwolf: the same session
as the terminal test (tests/packet/test_app.py), with the same keys, plus the mouse."""

from PySide6.QtCore import Qt

from ic705suite.station.links import LocalLink
from tests.gui.test_pages import key, until
from tests.packet.fake_tnc import FakeTnc
from tests.packet.test_app import packet_config


def test_packet_session_as_in_the_terminal(tmp_path, run):
    async def scenario():
        from ic705suite.gui.app import MainWindow
        from ic705suite.gui.packet import HeardView, PacketPage

        tnc = await FakeTnc().start()
        config = packet_config(tmp_path, tnc.port)
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="packet")
        win.resize(1200, 800)
        win.show()
        win.activateWindow()
        page = win.page
        assert isinstance(page, PacketPage)
        client = page.client
        try:
            await until(lambda: (client.state.get("tnc") or {}).get("state") == "up")
            await until(lambda: "Fm EA1ABC-7 To ID" in page.monitor.text())
            assert page.tabs[0].text() == "0: Monitor"

            await key(Qt.Key.Key_F2)
            await until(lambda: client.channel == 2)
            assert page.tabs[2].isChecked()
            await key(None, text="/c ea1abc")
            await key(Qt.Key.Key_Return)
            await until(lambda: client.channel_info(2).get("state") == "connected")
            await until(lambda: "BBS>" in page.channel_view.text())
            assert page.tabs[2].text() == "2: EA1ABC"
            assert page.line.placeholderText() == "to EA1ABC"

            await key(None, text="hola")
            await key(Qt.Key.Key_Return)
            await until(lambda: "ECHO hola" in page.channel_view.text())
            await key(Qt.Key.Key_Up)                               # the line typed before
            assert page.line.text() == "hola"
            await key(Qt.Key.Key_Escape)
            assert page.line.text() == ""

            page.tabs[5].click()                                   # the mouse picks a channel
            await until(lambda: client.channel == 5)
            assert "ECHO" not in page.channel_view.text()
            page.tabs[2].click()
            await until(lambda: "ECHO hola" in page.channel_view.text())

            await key(None, text="/d")
            await key(Qt.Key.Key_Return)
            await until(lambda: client.channel_info(2).get("state") == "free")

            await key(Qt.Key.Key_F10)                              # the heard stations
            assert isinstance(page.overlay, HeardView)
            assert page.overlay.table.rowCount() == 1
            await key(Qt.Key.Key_Return)                           # Enter connects
            await until(lambda: client.channel_info(2).get("call") == "EA1ABC-7")
            assert page.overlay is None

            await key(Qt.Key.Key_F9)
            assert not page.monitor_frame.isVisible()
            await key(Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
            await until(lambda: link.closed.is_set())
        finally:
            await link.close()
            await tnc.stop()
            win.close()

    run(scenario())
