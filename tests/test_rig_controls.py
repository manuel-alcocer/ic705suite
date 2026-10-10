"""IC-705 front panel, meters and band scope (simulator and hand-made CI-V frames)."""

import asyncio
import base64

import pytest

from ic705suite.config import Config
from ic705suite.rig import open_rig
from ic705suite.rig.base import TxDisabledError
from ic705suite.rig.icom import civ
from ic705suite.rig.icom.controls import _decode_ref, _decode_rit, _encode_ref, _encode_rit
from ic705suite.rig.icom.rig import keys_transmitter
from ic705suite.station.links import LocalLink


def sim_link(tmp_path) -> LocalLink:
    return LocalLink(None, Config(connection="sim", swr_web=False,
                                  database_url=f"sqlite:///{tmp_path / 'suite.db'}"))


@pytest.mark.parametrize("body, keys", [
    (b"\x17CQ", True), (b"\x17\xff", False),
    (b"\x1c\x00\x01", True), (b"\x1c\x00\x00", False), (b"\x1c\x00", False),
    (b"\x1c\x01\x02", True), (b"\x1c\x01\x01", False), (b"\x1c\x01", False),
    (b"\x28\x00\x03", True), (b"\x28\x00\x00", False),
    (b"\x16\x46\x01", True), (b"\x16\x46\x00", False), (b"\x16\x46", False),
    (b"\x16\x47\x02", True), (b"\x16\x47\x00", False),
    (b"\x14\x0a\x02\x55", False), (b"\x05" + civ.encode_frequency(7_030_000), False),
])
def test_which_commands_can_transmit(body, keys):
    assert keys_transmitter(body) is keys


def test_rit_and_reference_level_formats():
    # Examples from the IC-705 CI-V reference guide.
    assert _encode_rit(-1250) == bytes([0x50, 0x12, 0x01]) and _decode_rit(b"\x50\x12\x01") == -1250
    assert _encode_ref(-10.5) == bytes([0x10, 0x50, 0x01]) and _decode_ref(b"\x10\x50\x01") == -10.5
    assert _decode_ref(_encode_ref(7)) == 7.0


def _rig_with_frames():
    rig = open_rig(Config(connection="sim"))
    frames = []
    rig.on_scope = frames.append
    return rig, frames


def _feed(rig, body: bytes) -> None:
    rig._on_civ(civ.build_frame(civ.CONTROLLER_ADDR, body, src=0xA4))


def test_scope_frame_over_the_network():
    rig, frames = _rig_with_frames()
    points = bytes(range(0, 160, 2))[:80] * 6
    body = (b"\x27\x00\x00\x01\x01\x00" + civ.encode_frequency(14_074_000)
            + civ.encode_frequency(25_000) + b"\x00" + points[:475])
    _feed(rig, body)
    frame = frames[-1]
    assert (frame.start, frame.end, frame.center_mode) == (14_049_000, 14_099_000, True)
    assert len(frame.amplitudes) == 475 and max(frame.amplitudes) <= 1.0


def test_scope_frames_over_usb_in_eleven_parts():
    rig, frames = _rig_with_frames()
    # The guide's example header: fixed mode, 14.000-14.350 MHz.
    _feed(rig, bytes.fromhex("27 00 00 01 11 01 00 00 00 14 00 00 00 35 14 00 00"))
    for seq in range(2, 12):
        count = 25 if seq == 11 else 50
        _feed(rig, b"\x27\x00\x00" + bytes([civ.encode_bcd_byte(seq), 0x11]) + b"\x50" * count)
    frame = frames[-1]
    assert len(frames) == 1 and (frame.start, frame.end, frame.center_mode) == \
        (14_000_000, 14_350_000, False)
    assert len(frame.amplitudes) == 475 and frame.amplitudes[0] == 0x50 / 160


def test_out_of_range_frame_has_no_points():
    rig, frames = _rig_with_frames()
    _feed(rig, b"\x27\x00\x00\x01\x01\x01" + civ.encode_frequency(1_000_000)
          + civ.encode_frequency(1_100_000) + b"\x01")
    assert frames[-1].out_of_range and not any(frames[-1].amplitudes)


def test_front_panel_on_the_simulator(tmp_path):
    async def run():
        link = sim_link(tmp_path)
        events = []
        link.attach(events.append)
        await link.connect()
        try:
            rig = link.welcome["rig"]
            ids = {c["id"] for c in rig["controls"]}
            assert {"af", "rf", "agc", "preamp", "nr", "pbt_in", "scope_span", "rit_offset",
                    "filter", "data_mode"} <= ids
            assert {m["id"] for m in rig["meters"]} >= {"s", "po", "swr", "alc", "vd", "id"}
            assert (await link.call("rig.set", id="agc", value="slow"))["value"] == "slow"
            assert (await link.call("rig.set", id="af", value="35"))["value"] == 35
            assert (await link.call("rig.set", id="rit_offset", value=-120))["value"] == -120
            assert (await link.call("rig.set", id="scope_ref", value=-10.5))["value"] == -10.5
            # Receive only: nothing that would let the radio transmit by itself.
            assert "error" in await link.call("rig.set", id="vox", value=True)
            assert "error" in await link.call("rig.set", id="break_in", value="full")
            with pytest.raises(TxDisabledError):
                await link.core.station.rig.raw(b"\x1c\x01\x02")
            await link.command("/set nr on", app="rig")
            assert link.core.station.state.controls["nr"] is True
            # Scope frames only while someone watches.
            assert (await link.call("scope.watch", seconds=1))["scope"]
            await asyncio.sleep(0.8)
            scope = [e for e in events if e["type"] == "scope"]
            assert scope and len(base64.b64decode(scope[-1]["data"])) == 475
            assert scope[-1]["start"] < link.latest["rig"]["frequency"] < scope[-1]["end"]
            await asyncio.sleep(3.5)          # no more keepalives: output switched off
            assert not link.core.station.scope_on
            await asyncio.sleep(1.2)
            assert link.latest["rig"]["meters"]["vd"] > 6
        finally:
            await link.close()

    asyncio.run(run())


def test_rig_view_from_the_keyboard(tmp_path):
    from ic705suite.apps.view.app import ViewApp
    from ic705suite.rig.icom.sim import DEFAULT_STATIONS
    from ic705suite.ui.controlpanel import ControlPanel
    from ic705suite.ui.waterfall import Waterfall

    def typed(text):
        return ["space" if c == " " else "minus" if c == "-" else c for c in text]

    async def run():
        link = sim_link(tmp_path)
        await link.connect()
        controls = link.core.station.state.controls
        app = ViewApp(link)
        try:
            async with app.run_test(size=(140, 46)) as pilot:
                await pilot.pause(1.0)
                panel = app.query_one(ControlPanel)
                assert panel.selected == "af" and not panel.editing
                # Navigation: the cursor keys only move.
                af = controls["af"]
                await pilot.press("down", "right", "left")
                assert panel.selected == "rf" and controls["af"] == af
                await pilot.press("up")
                # Editing with the cursor keys: each press reaches the radio at once.
                await pilot.press("enter", "right", "right", "up")
                await pilot.pause(0.3)
                assert panel.editing and controls["af"] == af + 3
                await pilot.press("shift+down")         # ten steps down
                await pilot.pause(0.3)
                assert controls["af"] == af - 7
                await pilot.press("enter")              # back to moving
                assert not panel.editing
                await pilot.press("right")              # DSP column, same height: NB
                assert panel.selected == "nb"
                # Editing by typing the value.
                await pilot.press("down", "space", *typed("80"), "enter")
                await pilot.pause(0.3)
                assert panel.selected == "nb_level" and controls["nb_level"] == 80
                # Esc puts the old value back.
                await pilot.press("enter", "right", "right")
                await pilot.pause(0.3)
                assert controls["nb_level"] == 82
                await pilot.press("escape")
                await pilot.pause(0.3)
                assert controls["nb_level"] == 80 and not panel.editing and app.is_running
                # A choice typed by its name, and a signed value.
                await pilot.press("left", "down", "down", "down", "down")   # RF, then down to AGC
                assert panel.selected == "agc"
                await pilot.press("enter", *typed("slow"), "enter")
                await pilot.pause(0.3)
                assert controls["agc"] == "slow"
                await pilot.press("enter", *typed("nonsense"), "enter")
                assert panel.editing                    # not a value: keep editing
                await pilot.press("escape")
                for _ in range(4):
                    await pilot.press("right")          # to the VFO column
                await pilot.press("up")                 # from ΔTX (row of DELAY in CW) to RIT Hz
                assert panel.selected == "rit_offset"
                await pilot.press("enter", *typed("-120"), "enter")
                await pilot.pause(0.3)
                assert controls["rit_offset"] == -120
                # Function keys work too.
                assert app.query_one(Waterfall)._raw   # scope frames arrived
                await pilot.press("f7")                # previous signal on the scope
                await pilot.pause(0.5)
                tuned = link.core.station.state.frequency
                assert tuned < 7_030_000
                assert min(abs(tuned - st.freq) for st in DEFAULT_STATIONS) <= 300
                await pilot.press("slash")             # the command line
                await pilot.press(*typed("set agc fast"), "enter")
                await pilot.pause(0.3)
                assert controls["agc"] == "fast"
        finally:
            await link.close()

    asyncio.run(run())
