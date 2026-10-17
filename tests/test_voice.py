"""Voice from a client's microphone, with the simulated radio: who may key, the MOD input set
to WLAN only while keyed, the audio passed on, and every way it is released."""

import asyncio

from ic705suite.config import Config
from ic705suite.rig.icom.sim import SimTransport
from ic705suite.station import voice
from ic705suite.station.core import Core
from ic705suite.station.links import RemoteLink
from tests.cw.test_client_server import start_server

USB, CW = 0x01, 0x03
MOD = b"\x1a\x05\x01\x18"
TONE = b"\x00\x10" * 160                 # 20 ms at 8 kHz


async def started(tx=True, mode=USB, **options):
    sim = SimTransport(stations=[], seed=7)
    sim.mode = mode
    core = Core(sim, Config(tx_enabled=tx, swr_web=False, **options))
    await core.start()
    return core, sim


def test_voice_keys_only_with_transmission_on_and_in_a_voice_mode():
    async def scenario():
        core, sim = await started(tx=False)
        try:
            result = await core.voice.ptt(True, "me")
            assert "transmission is off" in result["error"] and not sim.ptt
        finally:
            await core.stop()
        core, sim = await started(mode=CW)
        try:
            result = await core.voice.ptt(True, "me")
            assert "in CW" in result["error"] and not sim.ptt
            assert sim.settings[MOD] == b"\x00"
        finally:
            await core.stop()

    asyncio.run(scenario())


def test_the_talker_keys_and_its_audio_reaches_the_radio_with_wlan_modulation():
    async def scenario():
        core, sim = await started()
        try:
            assert await core.voice.ptt(True, "me") == {"on": True}
            assert sim.ptt and sim.settings[MOD] == b"\x03"
            assert "another client" in (await core.voice.ptt(True, "other"))["error"]
            core.voice.audio("other", TONE)                # not the talker's: dropped
            assert sim.tx_audio_bytes == 0
            core.voice.audio("me", TONE)
            assert sim.tx_audio_bytes == len(TONE)
            assert core.station.keyed_by == "voice"
            msgid, _args = await core.ft8._cannot_key()   # FT8 waits for the voice
            assert msgid.endswith("a microphone is talking")
            assert await core.voice.ptt(False, "me") == {"on": False}
            assert not sim.ptt and sim.settings[MOD] == b"\x00"     # the radio's microphone
            assert core.station.keyed_by is None
            core.voice.audio("me", TONE)                   # released: dropped
            assert sim.tx_audio_bytes == len(TONE)
        finally:
            await core.stop()

    asyncio.run(scenario())


def test_released_when_the_microphone_goes_quiet_or_keyed_too_long(monkeypatch):
    async def scenario():
        core, sim = await started()
        try:
            await core.voice.ptt(True, "me")
            await asyncio.sleep(voice.SILENT + 0.5)        # no audio: the connection is gone
            assert not sim.ptt and not core.voice.keyed
            monkeypatch.setattr(voice, "LONGEST", 0.6)
            await core.voice.ptt(True, "me")
            for _ in range(10):
                core.voice.audio("me", TONE)
                await asyncio.sleep(0.1)
            assert not sim.ptt and sim.settings[MOD] == b"\x00"
            errors = [e["msgid"] for e in core.hub.backlog if e["type"] == "error"]
            assert any("no audio" in m for m in errors) and any("longest" in m for m in errors)
        finally:
            await core.stop()

    asyncio.run(scenario())


def test_a_remote_client_talks_and_leaving_releases():
    async def scenario():
        sim = SimTransport(stations=[], seed=8)
        sim.mode = USB
        core, stop, task, port = await start_server(Config(tx_enabled=True), sim)
        link = RemoteLink(f"127.0.0.1:{port}", "secret")
        try:
            await link.connect()
            assert (await link.ptt(True)) == {"on": True} and sim.ptt
            for _ in range(5):
                link.send_voice(TONE)
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.2)
            assert sim.tx_audio_bytes == 5 * len(TONE)
            await link.close()                             # gone while talking
            await asyncio.sleep(0.3)
            assert not sim.ptt and not core.voice.keyed and sim.settings[MOD] == b"\x00"
        finally:
            await link.close()
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_the_radios_own_microphone():
    """Source "radio": no audio needed, MIC as the voice modes' input while keyed (put back
    after), and the PTT said again keeps it keyed; quiet, it is released."""
    async def scenario():
        core, sim = await started()
        sim.settings[MOD] = b"\x03"                        # left on WLAN by someone
        try:
            assert await core.voice.ptt(True, "me", "radio") == {"on": True}
            assert sim.ptt and sim.settings[MOD] == b"\x00"     # the radio's microphone
            for _ in range(8):                                  # held: said again
                await asyncio.sleep(0.25)
                await core.voice.ptt(True, "me", "radio")
            assert sim.ptt and sim.tx_audio_bytes == 0
            await asyncio.sleep(voice.SILENT + 0.5)              # the client went quiet
            assert not sim.ptt and sim.settings[MOD] == b"\x03"
        finally:
            await core.stop()

    asyncio.run(scenario())
