"""The whole application against a fake AGWPE node."""

from __future__ import annotations

import asyncio

from ic705suite.apps.packet.config import BpqConfig, Config, LinkConfig
from ic705suite.apps.packet.link.agw import HEADER, AgwLink, Frame, decode_header
from ic705suite.apps.packet.link.telnet import TelnetLink
from ic705suite.apps.packet.tui.app import HamrpktApp
from ic705suite.apps.packet.tui.widgets import HeardTable, StatusLine


class FakeNode:
    """Just enough of LinBPQ's AGWPE interface: register, connect, echo."""

    def __init__(self) -> None:
        self.received: list[Frame] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                frame, length = decode_header(await reader.readexactly(HEADER.size))
                frame.data = await reader.readexactly(length) if length else b""
                self.received.append(frame)
                for reply in self.reply(frame):
                    writer.write(reply.encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()

    def reply(self, frame: Frame) -> list[Frame]:
        if frame.kind == "X":
            return [Frame(0, "X", frame.call_from, data=b"\x01")]
        if frame.kind == "G":
            return [Frame(0, "G", data=b"1;Port1 VHF 1200;")]
        if frame.kind == "m":
            text = b" 1:Fm EA1ABC-7 To ID <UI pid=F0 Len=6 >[10:00:00]\rEA1ABC\r"
            return [Frame(0, "U", "EA1ABC-7", "ID", text)]
        if frame.kind == "C":
            data = f"*** CONNECTED With Station {frame.call_to}\r".encode()
            return [Frame(frame.port, "C", frame.call_to, frame.call_from, data)]
        if frame.kind == "D":
            return [Frame(frame.port, "D", frame.call_to, frame.call_from,
                          b"ECHO " + frame.data + b"EA1ABC BBS>")]
        if frame.kind == "d":
            return [Frame(frame.port, "d", frame.call_to, frame.call_from,
                          b"*** DISCONNECTED From Station\r")]
        return []


def _lines(view) -> str:  # type: ignore[no-untyped-def]
    return "\n".join(strip.text for strip in view.lines)


async def _wait(pilot, condition, timeout: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition never met")


async def _scenario(tmp_path) -> None:  # type: ignore[no-untyped-def]
    node = FakeNode()
    server = await asyncio.start_server(node.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    config = Config("EA7WM-1", LinkConfig(type="agw", host="127.0.0.1", port=port))
    app = HamrpktApp(config, tmp_path / "config.toml")
    async with app.run_test(size=(120, 30)) as pilot:
        status = app.query_one(StatusLine)
        await _wait(pilot, lambda: status.link_state == "up")
        await _wait(pilot, lambda: "EA1ABC-7" in app.heard)
        assert app.query_one(HeardTable).row_count == 1

        await pilot.press(*"/c ea1abc", "enter")
        await _wait(pilot, lambda: app.session == "EA1ABC")
        assert status.session == "EA1ABC"

        await pilot.press(*"hola", "enter")
        await _wait(pilot, lambda: "EA1ABC BBS>" in _lines(app.terminal))
        assert "ECHO hola" in _lines(app.terminal)
        sent = [f for f in node.received if f.kind == "D"]
        assert sent[0].data == b"hola\r" and sent[0].call_to == "EA1ABC"

        await pilot.press("up")
        assert app.entry.line.value == "hola"
        await pilot.press("escape", "f5")
        await _wait(pilot, lambda: app.session == "")

        await pilot.press("f3")
        assert app.view == "heard"
        await pilot.press("enter")
        await _wait(pilot, lambda: app.session == "EA1ABC-7")
        await pilot.press("ctrl+q")
        await pilot.press("y")
    server.close()
    await server.wait_closed()
    logs = list((tmp_path / "sessions").glob("*_EA1ABC.log"))
    assert logs and "ECHO hola" in logs[0].read_text()


def test_session_against_fake_node(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HAMRPKT_HOME", str(tmp_path))
    asyncio.run(_scenario(tmp_path))


class FakeBpqTelnet:
    """LinBPQ's console: user, password, welcome, and a BBS behind ``BBS``."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"\xff\xfb\x03\xff\xfb\x01user:")
        await writer.drain()
        try:
            while True:
                raw = await reader.readuntil(b"\r")
                line = raw.replace(b"\xff\xfe\x03\xff\xfe\x01", b"").decode().strip()
                self.lines.append(line)
                if len(self.lines) == 1:
                    writer.write(b"password:")
                elif len(self.lines) == 2:
                    writer.write(b"Welcome to KLXNOD Telnet Server\r\n")
                elif line == "BBS":
                    writer.write(b"KLXNOD:EA7KLX-7} Connected to BBS\r\nde EA7KLX>\r\n")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()


async def _bpq_scenario(tmp_path) -> None:  # type: ignore[no-untyped-def]
    node, bpq = FakeNode(), FakeBpqTelnet()
    agw_server = await asyncio.start_server(node.handle, "127.0.0.1", 0)
    bpq_server = await asyncio.start_server(bpq.handle, "127.0.0.1", 0)
    config = Config("EA7KLX-4", LinkConfig(host="127.0.0.1",
                                           port=agw_server.sockets[0].getsockname()[1]))
    config.bpq = BpqConfig(port=bpq_server.sockets[0].getsockname()[1], user="manuel",
                           password="secret", apps={"EA7KLX-1": "BBS"})
    app = HamrpktApp(config, tmp_path / "config.toml")
    async with app.run_test(size=(120, 30)) as pilot:
        await _wait(pilot, lambda: app.link_state == "up")
        await pilot.press(*"/c bpq:ea7klx-1", "enter")
        await _wait(pilot, lambda: "de EA7KLX>" in _lines(app.terminal))
        assert bpq.lines[:3] == ["manuel", "secret", "BBS"]
        assert app.session == "bpq:EA7KLX-1"
        assert isinstance(app.link, TelnetLink)

        await pilot.press("f5")
        await _wait(pilot, lambda: isinstance(app.link, AgwLink) and app.link_state == "up")
        assert app.session == ""
        await pilot.press("ctrl+q")
    for server in (agw_server, bpq_server):
        server.close()
        await server.wait_closed()


def test_bpq_detour_through_telnet(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HAMRPKT_HOME", str(tmp_path))
    asyncio.run(_bpq_scenario(tmp_path))
