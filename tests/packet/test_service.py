"""The core's packet service against a fake Direwolf: channels, sessions both ways, the
monitor, and the TNC going away and coming back."""

from __future__ import annotations

import asyncio

from ic705suite.apps.packet import service as service_module
from ic705suite.apps.packet.agwpe import Frame, parse_ports
from ic705suite.apps.packet.service import CHANNELS, PacketService, parse_via, readable
from ic705suite.config import Config
from tests.packet.fake_tnc import FakeTnc


class Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict, keep: bool = False) -> None:
        self.events.append(event)

    def texts(self, channel: int) -> str:
        return "".join(e["text"] for e in self.events
                       if e["type"] == "pkt.text" and e["channel"] == channel)


async def until(condition, timeout: float = 3.0) -> None:
    for _ in range(int(timeout / 0.02)):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never met")


async def started(tnc: FakeTnc, **settings) -> tuple[PacketService, Hub]:
    config = Config(callsign="EA7KLX", packet_host="127.0.0.1", packet_tcp_port=tnc.port,
                    packet_mycall="EA7KLX-4", **settings)
    hub = Hub()
    service = PacketService(config, hub)
    await service.start()
    await until(lambda: service.registered)
    return service, hub


def test_a_session_on_the_chosen_channel():
    async def scenario():
        tnc = await FakeTnc().start()
        service, hub = await started(tnc)
        try:
            state = service.state()
            assert state["tnc"]["state"] == "up" and state["tnc"]["version"] == "2005.127"
            assert state["tnc"]["ports"] == ["1: Port1 first soundcard mono"]
            assert tnc.registered == {"EA7KLX-4"}
            assert (await service.call("connect", {"channel": 3, "call": "ea1abc-1"})) == \
                {"ok": True}
            await until(lambda: service.channels[3].state == "connected")
            assert tnc.sent("C")[0].call_from == "EA7KLX-4"
            assert tnc.sent("C")[0].call_to == "EA1ABC-1"
            await until(lambda: "BBS>" in hub.texts(3))
            assert "Welcome to EA1ABC-1\n" in hub.texts(3)
            assert service.channels[3].unread == 1

            await service.call("send", {"channel": 3, "text": "L\nR 12"})
            await until(lambda: tnc.sent("D"))
            assert tnc.sent("D")[-1].data == b"L\rR 12\r"
            await until(lambda: "ECHO L\nR 12\nBBS>" in hub.texts(3))
            history = (await service.call("history", {"channel": 3}))["text"]
            assert [p["kind"] for p in history] == ["sys", "sys", "rx", "tx", "rx"]
            assert history[0]["msgid"] == "Calling {call}…"
            await service.call("read", {"channel": 3})
            assert service.channels[3].unread == 0

            await service.call("disconnect", {"channel": 3})
            await until(lambda: service.channels[3].state == "free")
            assert service.channels[3].call == ""
            assert "Disconnected from EA1ABC-1" in hub.texts(3)
        finally:
            await service.stop()
            await tnc.stop()

    asyncio.run(scenario())


def test_digipeaters_and_what_is_refused():
    async def scenario():
        tnc = await FakeTnc().start()
        service, _hub = await started(tnc)
        try:
            await service.call("connect", {"channel": 1, "call": "EA1ABC",
                                           "via": "ea7urs-1, EA7RCM"})
            await until(lambda: tnc.sent("v"))
            frame = tnc.sent("v")[0]
            assert frame.data == bytes([2]) + b"EA7URS-1\0\0EA7RCM\0\0\0\0"
            await until(lambda: service.channels[1].state == "connected")
            assert service.channels[1].via == ["EA7URS-1", "EA7RCM"]

            busy = await service.call("connect", {"channel": 1, "call": "EA2XYZ"})
            assert busy["msgid"] == "Channel {channel} is busy with {call}"
            again = await service.call("connect", {"channel": 2, "call": "EA1ABC"})
            assert again["msgid"] == "Already connected to {call}"
            bad = await service.call("connect", {"channel": 2, "call": "NOT A CALL"})
            assert bad["error"] == "NOT A CALL is not a callsign"
            assert (await service.call("connect", {"channel": 0, "call": "EA2XYZ"}))["msgid"] \
                == "Sessions use channels 1 to {last}"
            idle = await service.call("send", {"channel": 2, "text": "hello"})
            assert idle["msgid"] == "Channel {channel} is not connected: /c CALL first"

            await service.call("unproto", {"dest": "cq", "text": "hello all", "via": []})
            await until(lambda: tnc.sent("M"))
            assert tnc.sent("M")[-1].data == b"hello all\r"
            assert tnc.sent("M")[-1].call_to == "CQ"
        finally:
            await service.stop()
            await tnc.stop()

    asyncio.run(scenario())


def test_a_call_that_is_not_answered_frees_the_channel():
    async def scenario():
        tnc = await FakeTnc(answers=False).start()
        service, hub = await started(tnc)
        try:
            await service.call("connect", {"channel": 2, "call": "EA1ABC"})
            await until(lambda: service.channels[2].state == "free")
            assert "Disconnected from EA1ABC" in hub.texts(2)
        finally:
            await service.stop()
            await tnc.stop()

    asyncio.run(scenario())


def test_stations_calling_us_take_free_channels():
    async def scenario():
        tnc = await FakeTnc().start()
        service, hub = await started(tnc)
        try:
            await service.call("connect", {"channel": 1, "call": "EA1ABC"})
            await until(lambda: service.channels[1].state == "connected")
            tnc.incoming("EA2XYZ", "EA7KLX-4")
            await until(lambda: service.channels[2].state == "connected")
            assert service.channels[2].call == "EA2XYZ" and service.channels[2].incoming
            assert "EA2XYZ connected to us" in hub.texts(2)
            tnc.hang_up("EA2XYZ", "EA7KLX-4")
            await until(lambda: service.channels[2].state == "free")

            # Every channel busy: the next caller is told so and hung up.
            for number in range(2, CHANNELS + 1):
                tnc.incoming(f"EA3AA{number}", "EA7KLX-4")
            await until(lambda: all(c.state == "connected" for c in service.channels[1:]))
            tnc.incoming("EA4ZZZ", "EA7KLX-4")
            await until(lambda: any(f.kind == "d" and f.call_to == "EA4ZZZ"
                                    for f in tnc.received))
            assert any(f.kind == "D" and b"busy" in f.data for f in tnc.received)
            assert "EA4ZZZ called but every channel is busy" in hub.texts(0)
        finally:
            await service.stop()
            await tnc.stop()

    asyncio.run(scenario())


def test_the_monitor_and_the_heard_stations():
    async def scenario():
        tnc = await FakeTnc().start()
        service, hub = await started(tnc)
        try:
            await until(lambda: "EA1ABC-7" in service.heard)
            assert "1:Fm EA1ABC-7 To ID <UI" in hub.texts(0)
            # XID parameters (binary) are not shown as text.
            tnc.push(FakeTnc.monitor_frame("EA2XYZ", "EA7KLX-4", "XID",
                                           data=b"\x82\x80\x00\x17\x02\x02!\x00\x03\r"))
            await until(lambda: "EA2XYZ" in service.heard)
            assert "[9 bytes of data]" in hub.texts(0)
            # Our own frames are shown but are not a station heard.
            tnc.push(Frame(0, "T", "EA7KLX-4", "ID",
                           b" 1:Fm EA7KLX-4 To ID <UI pid=F0 Len=3 >[10:00:01]\rhi\r\0"))
            await until(lambda: "EA7KLX-4 To ID" in hub.texts(0))
            assert "EA7KLX-4" not in service.heard
            heard = service.state()["heard"]
            assert [s["call"] for s in heard] == ["EA2XYZ", "EA1ABC-7"]
            assert service.state()["frames"] == 3
            await service.call("clear", {"channel": 0})
            assert service.state()["heard"] == []
        finally:
            await service.stop()
            await tnc.stop()

    asyncio.run(scenario())


def test_the_tnc_lost_and_found_again(monkeypatch):
    monkeypatch.setattr(service_module, "RETRY_DELAY", 0.2)

    async def scenario():
        tnc = await FakeTnc().start()
        service, hub = await started(tnc)
        try:
            await service.call("connect", {"channel": 1, "call": "EA1ABC"})
            await until(lambda: service.channels[1].state == "connected")
            port = tnc.port
            await tnc.stop()
            await until(lambda: service.tnc_state in ("down", "connecting")
                        and service.channels[1].state == "free")
            assert "Link to the TNC lost: EA1ABC is no longer connected" in hub.texts(1)
            assert "Cannot reach the TNC at 127.0.0.1" in hub.texts(0)
            refused = await service.call("connect", {"channel": 1, "call": "EA1ABC"})
            assert refused["msgid"] == "No TNC: {state}"
            tnc.port = port
            await tnc.start()
            await until(lambda: service.tnc_state == "up" and service.registered, 5)
        finally:
            await service.stop()
            await tnc.stop()

    asyncio.run(scenario())


def test_not_set_up():
    async def scenario():
        service = PacketService(Config(callsign="EA7KLX"), Hub())
        await service.start()
        assert service.state()["tnc"]["state"] == "off"
        reply = await service.call("retry", {})
        assert reply["msgid"].startswith("Packet is not set up")
        await service.stop()

    asyncio.run(scenario())


def test_small_helpers():
    assert parse_via("ea7urs-1, EA7RCM") == ["EA7URS-1", "EA7RCM"]
    assert parse_via(["a", " "]) == ["A"]
    assert readable("hola\rqué tal") and not readable("\x82\x80\x00\x17")
    assert parse_ports("2;Port1 VHF;Port2 INVALID CHANNEL;") == ["1: Port1 VHF"]
