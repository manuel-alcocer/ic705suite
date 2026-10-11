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


def test_the_sysop_page_in_the_window(tmp_path, run, monkeypatch):
    async def scenario():
        from ic705suite.gui import packet as gui_packet
        from ic705suite.gui.app import MainWindow
        from ic705suite.gui.packet import BbsView

        async def kill(parent, title, message, options):
            assert "Kill message 1 from EA7KLX" in message
            return "yes"

        shown: list = []
        monkeypatch.setattr(gui_packet, "choose", kill)
        tnc = await FakeTnc(echo=False).start()
        config = packet_config(tmp_path, tnc.port)
        config.packet_bbs = True
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="packet")
        win.resize(1200, 800)
        win.show()
        win.activateWindow()
        page = win.page
        page.show_text = lambda title, lines: shown.append((title, lines))
        client = page.client
        try:
            await until(lambda: (client.state.get("bbs") or {}).get("registered"))
            for line in ("/c EA7KLX-1", "SP EA1ABC", "Hola", "Texto", "/EX"):
                await key(None, text=line)
                await key(Qt.Key.Key_Return)
            await until(lambda: "Message: 1 Bid: 1_EA7KLX" in page.channel_view.text())
            await key(Qt.Key.Key_F12)
            assert isinstance(page.overlay, BbsView)
            view = page.overlay
            await until(lambda: view.rows)
            assert view.table.item(0, 6).text() == "Hola"
            assert "connected EA7KLX (here)" in view.who.text()
            await key(Qt.Key.Key_Return)
            await until(lambda: shown)
            assert shown[0][0] == "Message 1" and "Texto" in shown[0][1]
            await key(Qt.Key.Key_U)
            await until(lambda: view.showing == "users" and view.table.columnCount() == 4)
            await key(Qt.Key.Key_U)
            await until(lambda: view.showing == "messages" and view.rows)
            await key(Qt.Key.Key_K)
            await until(lambda: not view.rows)
            await key(Qt.Key.Key_Escape)
            assert page.overlay is None
        finally:
            await link.close()
            await tnc.stop()
            win.close()

    run(scenario())


def test_the_keys_of_the_footer_can_be_clicked(tmp_path, run):
    """A click on a key of the footer does what the key does; ranges are not links."""
    from ic705suite.gui.widgets import hint_html

    html = hint_html("F1-F8 Channel · F9 Monitor · Esc Back · Ctrl+Q Quit · ↑↓", links=True)
    assert 'href="key:F9"' in html and 'href="key:Esc"' in html
    assert 'href="key:Ctrl+Q"' in html
    assert "key:F1" not in html and "key:↑↓" not in html
    assert "href" not in hint_html("F9 Monitor")

    async def scenario():
        from ic705suite.gui.app import MainWindow
        from ic705suite.gui.packet import BbsView

        tnc = await FakeTnc(echo=False).start()
        config = packet_config(tmp_path, tnc.port)
        config.packet_bbs = True
        link = LocalLink(None, config)
        win = MainWindow(link, config, start_app="packet")
        win.resize(1300, 800)
        win.show()
        win.activateWindow()
        page = win.page
        try:
            await until(lambda: (page.client.state.get("bbs") or {}).get("on"))
            assert 'href="key:F12"' in page.footer.text()
            page.footer.linkActivated.emit("key:F9")
            assert not page.monitor_frame.isVisible()
            page.footer.linkActivated.emit("key:F12")
            await until(lambda: isinstance(page.overlay, BbsView))
            page.footer.linkActivated.emit("key:Esc")
            await until(lambda: page.overlay is None)
        finally:
            await link.close()
            await tnc.stop()
            win.close()

    run(scenario())


def test_opened_from_the_dashboard(tmp_path, run):
    """The dashboard stops after the packet page is made: the page still gets the events."""
    async def scenario():
        from ic705suite.gui.app import MainWindow
        from ic705suite.launcher import start_link

        tnc = await FakeTnc(echo=False).start()
        config = packet_config(tmp_path, tnc.port)
        config.packet_bbs = True
        link = LocalLink(None, config)
        win = MainWindow(link, config)
        win.resize(1200, 800)
        win.show()
        start_link(link)
        try:
            await until(lambda: link.connected)
            win.open_app("packet")
            page = win.page
            await until(lambda: (page.client.state.get("bbs") or {}).get("registered"))
            await key(None, text="/c EA7KLX-1")
            await key(Qt.Key.Key_Return)
            await key(None, text="H")
            await key(Qt.Key.Key_Return)
            await until(lambda: "LM        list your messages" in page.channel_view.text())
        finally:
            await link.close()
            await tnc.stop()
            win.close()

    run(scenario())
