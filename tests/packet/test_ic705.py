"""The IC-705 over WiFi as the packet radio, with the simulator: the PTT Direwolf asks for
(through the stand-in rigctld), the checks before keying, the audio sent while keyed, and
the transmit audio packets of the network protocol."""

from __future__ import annotations

import asyncio
import struct

import pytest

from ic705suite.apps.packet import ic705
from ic705suite.apps.packet.ic705 import DUMP_STATE, Ic705Port, Rigctld
from ic705suite.config import Config
from ic705suite.rig.icom import lan
from ic705suite.station.links import LocalLink


def test_rigctld_answers_what_direwolf_asks():
    """The dialogue recorded between Direwolf 1.8 (Hamlib 4.7) and a real rigctld."""
    keyed = []

    async def ptt(on: bool) -> bool:
        keyed.append(on)
        return on is False or len(keyed) < 3

    async def scenario():
        server = Rigctld(0, ptt, lambda: 144_850_000, lambda: bool(keyed and keyed[-1]))
        answers = [await server.answer(command) for command in (
            "\\get_powerstat", "\\chk_vfo", "\\dump_state", "v", "l KEYSPD", "f", "T 1", "t",
            "T 0", "T 1", "\\get_split_vfo")]
        return answers

    answers = asyncio.run(scenario())
    assert answers[:3] == ["1\n", "0\n", DUMP_STATE]
    assert DUMP_STATE.startswith("1\n1\n0\n") and DUMP_STATE.endswith("done\n")
    assert answers[3:6] == ["VFOA\n", "0\n", "144850000\n"]
    assert answers[6:9] == ["RPRT 0\n", "1\n", "RPRT 0\n"]
    assert answers[9] == "RPRT -9\n"            # refused: the rig rejected it
    assert answers[10] == "RPRT -11\n"          # anything else: not available
    assert keyed == [True, False, True]


def test_rigctld_over_tcp():
    async def scenario():
        async def ptt(on: bool) -> bool:
            return True

        server = Rigctld(0, ptt, lambda: 7_030_000, lambda: False)
        server.port = 0
        server.server = await asyncio.start_server(server._client, "127.0.0.1", 0)
        port = server.server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"f\nT 1\n")
        await writer.drain()
        assert await reader.readline() == b"7030000\n"
        assert await reader.readline() == b"RPRT 0\n"
        writer.close()
        await server.stop()

    asyncio.run(scenario())


async def radio(tmp_path, tx: bool = True) -> tuple[LocalLink, Ic705Port, list]:
    config = Config(connection="sim", swr_web=False, callsign="EA7KLX", tx_enabled=tx,
                    database_url=f"sqlite:///{tmp_path / 'suite.db'}")
    link = LocalLink(None, config)
    await link.connect()
    said: list[str] = []
    port = Ic705Port(link.core.station, config, lambda msgid, **args: said.append(
        msgid.format(**args)), lambda: None)
    return link, port, said


def test_the_ptt_and_the_audio_sent_while_keyed(tmp_path, monkeypatch):
    monkeypatch.setattr(ic705, "TAIL", 0.15)

    async def scenario():
        link, port, said = await radio(tmp_path)
        sim = link.core.station.transport
        try:
            assert port.audio == "udp:7355 plughw:Loopback,0,0"
            assert port.ptt_line == "RIG 2 127.0.0.1:4575"
            assert not await port.ptt(True)               # not in a data mode yet
            assert "not in a data mode (FM-D)" in said[-1] and not sim.ptt
            assert not await port.ptt(True)
            assert len(said) == 1                           # said once, not at every retry

            await port.qrg(144_850_000)
            assert sim.frequency == 144_850_000 and sim.data_mode == (1, 1)
            assert sim.settings[b"\x1a\x05\x01\x19"] == b"\x03"     # DATA1 MOD: WLAN

            port._to_radio(b"\x01\x00" * 160)               # not keyed: nothing goes out
            assert sim.tx_audio_bytes == 0
            assert await port.ptt(True)
            assert sim.ptt and port.keyed and port.refused == ""
            assert link.core.station.rig.longest_key == ic705.LONGEST_KEY
            port._to_radio(b"\x01\x00" * 160)
            assert sim.tx_audio_bytes == 320
            assert await port.ptt(False)
            assert not port.keyed and sim.ptt               # the tail still on its way
            port._to_radio(b"\x01\x00" * 160)
            assert sim.tx_audio_bytes == 640
            await asyncio.sleep(0.4)
            assert not sim.ptt                              # released after the tail
            port._to_radio(b"\x01\x00" * 160)
            assert sim.tx_audio_bytes == 640
        finally:
            await link.close()

    asyncio.run(scenario())


def test_no_transmitting_with_transmission_off(tmp_path):
    async def scenario():
        link, port, said = await radio(tmp_path, tx=False)
        try:
            await port.qrg(144_800_000)
            assert not await port.ptt(True)
            assert "transmission is off" in said[-1]
            assert not link.core.station.transport.ptt
        finally:
            await link.close()

    asyncio.run(scenario())


def test_what_it_needs_here(tmp_path, monkeypatch):
    async def scenario():
        link, port, _said = await radio(tmp_path)
        try:
            monkeypatch.setattr(ic705, "loopback_present", lambda cards=None: False)
            monkeypatch.setattr(ic705.sys, "platform", "linux")
            msgid, args = port.problem()
            assert "snd-aloop" in msgid and "modprobe snd-aloop" in args["command"]
            monkeypatch.setattr(ic705, "loopback_present", lambda cards=None: True)
            assert port.problem() is None
            monkeypatch.setattr(ic705.sys, "platform", "win32")
            assert "needs Linux" in port.problem()[0]
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_core_says_why_the_ic705_cannot_be_the_radio(tmp_path, monkeypatch):
    monkeypatch.setattr(ic705, "loopback_present", lambda cards=None: False)
    monkeypatch.setattr(ic705.sys, "platform", "linux")

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA7KLX",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}", packet_tnc="ic705",
                        packet_mycall="EA7KLX-4")
        link = LocalLink(None, config)
        texts: list[str] = []
        link.attach(lambda e: e.get("type") == "pkt.text" and texts.append(e["text"]))
        await link.connect()
        try:
            state = (await link.call("pkt.state"))["tnc"]
            assert "snd-aloop" in state["radio"]["problem"]
            assert state["direwolf"]["state"] == "stopped"
            assert any("snd-aloop" in text for text in texts)
            refused = await link.call("pkt.qrg", frequency=144_800_000)
            assert refused["msgid"].startswith("/qrg tunes the IC-705")
        finally:
            await link.close()

    asyncio.run(scenario())


def test_transmit_audio_packets():
    """Laid out as received ones: ident, own sequence and length (big-endian), samples."""
    stream = lan.AudioStream("192.0.2.1", lambda reason: None, lambda samples: None)
    stream.my_id, stream.remote_id = b"\x01\x02\x03\x04", b"\x05\x06\x07\x08"
    sent: list[bytes] = []
    stream._send = lambda packet: sent.append(bytes(packet))
    stream.send_audio(bytes(range(256)) * 6)            # 1536 bytes: two packets
    assert [len(p) for p in sent] == [0x18 + 1364, 0x18 + 172]
    first, second = sent
    assert struct.unpack_from("<I", first, 0)[0] == len(first)
    assert first[8:12] == b"\x01\x02\x03\x04" and first[12:16] == b"\x05\x06\x07\x08"
    assert struct.unpack_from("<H", first, 0x10)[0] == lan.TX_AUDIO_IDENT
    assert struct.unpack_from(">H", first, 0x12)[0] == 0
    assert struct.unpack_from(">H", second, 0x12)[0] == 1
    assert struct.unpack_from(">H", first, 0x16)[0] == 1364
    assert first[0x18:] == (bytes(range(256)) * 6)[:1364]
    # Tracked: sequence numbers in the header, kept for retransmission.
    assert struct.unpack_from("<H", second, 6)[0] == struct.unpack_from("<H", first, 6)[0] + 1


@pytest.mark.parametrize("text, expected", [
    (" 3 [Loopback       ]: Loopback - Loopback\n", True),
    (" 2 [Device         ]: USB-Audio - USB Audio Device\n", False)])
def test_loopback_present(tmp_path, text, expected):
    cards = tmp_path / "cards"
    cards.write_text(text)
    assert ic705.loopback_present(cards) is expected
    assert not ic705.loopback_present(tmp_path / "missing")
