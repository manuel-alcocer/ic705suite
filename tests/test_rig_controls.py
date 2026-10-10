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


def test_rig_view_sets_controls_and_tunes_from_the_waterfall(tmp_path):
    from ic705suite.apps.view.app import ViewApp
    from ic705suite.ui.controlpanel import ControlPanel
    from ic705suite.ui.waterfall import Waterfall

    async def run():
        link = sim_link(tmp_path)
        await link.connect()
        app = ViewApp(link)
        try:
            async with app.run_test(size=(140, 46)) as pilot:
                await pilot.pause(1.0)
                panel = app.query_one(ControlPanel)
                assert panel.selected == panel.controls[0]["id"] == "af"
                before = link.core.station.state.controls["af"]
                await pilot.press("right", "right")
                await pilot.pause(0.3)
                assert link.core.station.state.controls["af"] == before + 2
                await pilot.press("down")              # RF
                await pilot.press("shift+left")        # -10
                await pilot.pause(0.3)
                assert link.core.station.state.controls["rf"] == 40
                assert app.query_one(Waterfall)._raw   # scope frames arrived
                scope = app.query_one(Waterfall)
                expected = scope.column_frequency(10 - 1)     # the frame's border is column 0
                await pilot.click(Waterfall, offset=(10, 12))
                await pilot.pause(0.5)
                assert abs(link.core.station.state.frequency - expected) <= 100
                await pilot.press("slash")             # the command line
                await pilot.press(*["space" if c == " " else c for c in "set agc fast"], "enter")
                await pilot.pause(0.3)
                assert link.core.station.state.controls["agc"] == "fast"
        finally:
            await link.close()

    asyncio.run(run())

