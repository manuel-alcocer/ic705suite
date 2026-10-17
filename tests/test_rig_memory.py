"""The IC-705's memory channels: their 115-byte layout, and reading, writing, clearing and
tuning to them on the simulated radio."""

import asyncio

from ic705suite.config import Config
from ic705suite.rig import open_rig
from ic705suite.rig.icom import memory
from ic705suite.rig.icom.sim import SimTransport

# A channel as the radio sends it (after 1A 00): group 01, channel 23, 145.650 MHz FM FIL1,
# DUP-, TONE 88.5, offset 600 kHz, "ED7ZAB".
REPLY = (bytes.fromhex("0001 0023 00") +
         bytes.fromhex("0000654501 05 01 00 11 00 000885 000885 00 0023 00 006000") +
         b"CQCQCQ  " + b" " * 16 +
         bytes.fromhex("0000654501 05 01 00 11 00 000885 000885 00 0023 00 006000") +
         b"CQCQCQ  " + b" " * 16 + b"ED7ZAB".ljust(16))


def test_the_layout_both_ways():
    assert len(REPLY) == memory.SIZE
    group, channel, found = memory.decode(REPLY)
    assert (group, channel) == (1, 23)
    assert found == memory.Memory(frequency=145_650_000, mode=0x05, filter=1, data=0,
                                  duplex="-", offset=600_000, tone_mode="tone", tone=88.5,
                                  tsql=88.5, name="ED7ZAB")
    assert memory.encode(1, 23, found) == REPLY
    assert memory.decode(bytes.fromhex("0000 0001 ff")) == (0, 1, None)
    assert memory.encode(99, 99, None) == bytes.fromhex("0099 0099 ff")


def test_memories_on_the_simulator():
    async def scenario():
        sim = SimTransport(stations=[], seed=1)
        rig = open_rig(Config(connection="sim"), sim)
        await sim.connect()
        try:
            assert await rig.read_memory(0, 1) is None
            await rig.write_memory(0, 1, {"frequency": 7_090_000, "mode": "LSB", "filter": 2,
                                          "name": "40m net"})
            found = await rig.read_memory(0, 1)
            assert found["frequency"] == 7_090_000 and found["mode"] == "LSB"
            assert found["filter"] == 2 and found["name"] == "40m net"
            await rig.write_memory(0, 1, None)
            assert await rig.read_memory(0, 1) is None
            # Tuning to a repeater: frequency, FM, the duplex, its offset and the tone.
            problems = await rig.apply_memory({"frequency": 145_650_000, "mode": "FM",
                                               "filter": 1, "duplex": "-", "offset": 600_000,
                                               "tone_mode": "tone", "tone": 88.5})
            assert problems == []
            assert sim.frequency == 145_650_000 and sim.mode == 0x05
            assert sim.settings[b"\x0f"] == b"\x11"                # DUP-
            assert sim.settings[b"\x0d"] == bytes.fromhex("006000")  # 600.0 kHz
            assert sim.settings[b"\x1b\x00"] == bytes.fromhex("000885")
            assert sim.settings[b"\x16\x42"] == b"\x01"
            assert sim.settings[b"\x16\x43"] == b"\x00"
        finally:
            await sim.close()

    asyncio.run(scenario())
