"""Antenna sweeps on the shared station (ported from swr-remote) with a fake IC-705."""

import asyncio
import json

import pytest

from ic705suite.apps.swr.analysis import analyze, khz_per_unit
from ic705suite.apps.swr.service import HistoryStore, SweepError, SwrService
from ic705suite.config import Config
from ic705suite.rig import open_rig
from ic705suite.rig.icom import civ
from ic705suite.rig.icom.rig import swr_from_raw, watts_from_raw
from ic705suite.rig.transport import Transport
from ic705suite.station.station import Station


class _Ui:
    def __init__(self):
        self.messages = []

    def info(self, msgid, **args): self.messages.append(msgid.format(**args))
    def error(self, msgid, **args): self.messages.append(msgid.format(**args))
    def status_changed(self): ...


class FakeRadio(Transport):
    """Answers the CI-V commands used by the sweeps; SWR is a parabola around `resonance`."""

    name = "fake"

    def __init__(self, resonance: int) -> None:
        super().__init__(8000)
        self.resonance = resonance
        self.freq, self.mode, self.filter, self.data, self.power = 7_074_000, 0x01, 1, 1, 255
        self.ptt = False
        self.keyed = 0
        self.max_keyed_points = 0
        self.radio_name = "IC-705"
        self.civ_address = 0xA4

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    def _reply(self, body: bytes) -> None:
        loop = asyncio.get_running_loop()
        loop.call_soon(self._emit_civ, civ.build_frame(civ.CONTROLLER_ADDR, body, src=0xA4))

    def send_civ(self, frame: bytes) -> None:
        body = frame[4:-1]
        ok = b"\xfb"
        if body == b"\x03":
            self._reply(b"\x03" + civ.encode_frequency(self.freq))
        elif body[0] == 0x05:
            self.freq = civ.decode_frequency(body[1:6]); self._reply(ok)
        elif body == b"\x04":
            self._reply(bytes([0x04, self.mode, self.filter]))
        elif body[0] == 0x06:
            self.mode = body[1]; self.filter = body[2] if len(body) > 2 else self.filter; self._reply(ok)
        elif body == b"\x1a\x06":
            self._reply(bytes([0x1A, 0x06, self.data, 1 if self.data else 0]))
        elif body[:2] == b"\x1a\x06":
            self.data = body[2]; self._reply(ok)
        elif body == b"\x14\x0a":
            self._reply(b"\x14\x0a" + civ.encode_level(self.power))
        elif body[:2] == b"\x14\x0a":
            self.power = civ.decode_level(body[2:4]); self._reply(ok)
        elif body[:2] == b"\x1c\x00":
            self.ptt = body[2] == 1; self.keyed += self.ptt; self._reply(ok)
        elif body == b"\x15\x12":
            khz = abs(self.freq - self.resonance) / 1000
            raw = min(200, round(khz * 2)) if self.ptt else 0
            self._reply(b"\x15\x12" + civ.encode_level(raw))
        elif body == b"\x15\x11":
            self._reply(b"\x15\x11" + civ.encode_level(80 if self.ptt else 0))
        elif body == b"\x15\x02":
            khz = abs(self.freq - self.resonance) / 1000
            self._reply(b"\x15\x02" + civ.encode_level(max(0, 60 - round(khz / 5))))
        else:
            self._reply(b"\xfa")


def test_meter_conversions():
    assert swr_from_raw(0) == 1.0
    assert swr_from_raw(48) == 1.5
    assert swr_from_raw(100) == 2.5
    assert watts_from_raw(213) == 10.0


def test_analysis_direction():
    freqs = list(range(7_000_000, 7_200_001, 10_000))
    swr = [1 + abs(f - 7_030_000) / 40_000 for f in freqs]
    a = analyze("swr", freqs, swr, 7_074_000, "40m", [])
    assert abs(a.resonance - 7_030_000) < 3000
    assert a.move == "lower" and not a.centered   # resonance too low -> smaller coil number
    assert a.units == 0.25                          # 44 kHz at the default 150 kHz/unit
    swr = [1 + abs(f - 7_150_000) / 40_000 for f in freqs]
    assert analyze("swr", freqs, swr, 7_074_000, "40m", []).move == "higher"


def test_analysis_edge_and_tuned():
    freqs = list(range(14_000_000, 14_350_001, 25_000))
    swr = [1 + abs(f - 13_900_000) / 50_000 for f in freqs]
    assert analyze("swr", freqs, swr, 14_074_000, "20m", []).at_edge == "low"
    swr = [1.05 + abs(f - 14_080_000) / 80_000 for f in freqs]
    a = analyze("swr", freqs, swr, 14_074_000, "20m", [])
    assert a.tuned and a.move == "" and a.bw_15[0] < 14_074_000 < a.bw_15[1]


def test_flat_noise_is_not_conclusive():
    # Real 40 m daytime sweep: flat floor plus broadcast carriers at 7.200 and 7.260.
    values = [110, 111.2, 106.5, 106.2, 103.8, 101.5, 100.2, 101.5, 103.8, 96.2, 99.5, 100.5,
              97.2, 93.8, 100.8, 125.2, 107.2, 99, 127.8, 108.8, 101.2]
    freqs = list(range(6_900_000, 7_300_001, 20_000))
    a = analyze("noise", freqs, values, 7_074_000, "40m", [])
    assert not a.confident and a.move == "" and a.units is None
    peak = [100 + max(0, 20 - abs(f - 7_100_000) / 5_000) for f in freqs]
    a = analyze("noise", freqs, peak, 7_074_000, "40m", [])
    assert a.confident and abs(a.resonance - 7_100_000) < 5_000


def test_real_40m_off_resonance():
    # Real sweep 2026-10-10: SWR > 3 everywhere, rising with frequency.
    values = [5.22, 5.28, 5.33, 5.38, 5.4, 5.45, 5.45, 5.45, 5.25, 5.55, 5.58, 5.6, 5.6, 5.6,
              5.42, 5.67, 5.65, 5.67, 5.72, 5.65, 5.7]
    freqs = list(range(7_000_000, 7_200_001, 10_000))
    a = analyze("swr", freqs, values, 7_074_000, "40m", [])
    assert a.far and a.at_edge == "low" and a.move == "lower" and a.units is None


def test_tuned_when_swr_low_on_target():
    # Real 2026-10-10 result: dip 1.17 at 7.095, 21 kHz above the FT8 target.
    freqs = list(range(7_000_000, 7_200_001, 10_000))
    swr = [1.17 + ((f - 7_095_000) / 60_000) ** 2 for f in freqs]
    a = analyze("swr", freqs, swr, 7_074_000, "40m", [])
    assert a.tuned and a.move == "" and a.swr_at_target < 1.5


def test_slope_from_history():
    hist = [{"kind": "final", "band": "20m", "coil": 4, "resonance": 14_100_000},
            {"kind": "final", "band": "20m", "coil": 5, "resonance": 13_900_000},
            {"kind": "swr", "band": "20m", "resonance": 15_000_000}]
    assert khz_per_unit(hist, "20m") == -200.0
    assert khz_per_unit([], "20m") is None


def make_service(tmp_path, fake, tx_enabled=True):
    config = Config(tx_enabled=tx_enabled, swr_web=False,
                    database_url=f"sqlite:///{tmp_path / 'suite.db'}")
    station = Station(open_rig(config, fake), config, _Ui())
    service = SwrService(station, config, station.ui)
    service._history = HistoryStore(config.database_url, legacy=None)
    return station, service


@pytest.mark.parametrize("kind", ["swr", "noise"])
def test_full_sweep_restores_radio(tmp_path, kind):
    fake = FakeRadio(resonance=7_120_000)

    async def run():
        station, service = make_service(tmp_path, fake)
        # A higher coil number lowers the frequency, as on the Komunica.
        service.history.save_antenna("Komunica", coil_sign=-1)
        service.set_remote(True)          # switched on by hand in the antenna application
        await station.start()
        try:
            service.start_sweep({"kind": kind, "band": "40m", "start": 7_000_000,
                                 "stop": 7_200_000, "step": 20_000, "target": 7_074_000})
            await service._task
            assert not station.paused   # polling resumes after the sweep
        finally:
            await station.stop()
        return service

    service = asyncio.run(run())
    sw = service.sweep
    assert not sw.error and len(sw.points) == 11
    assert abs(sw.analysis["resonance"] - 7_120_000) <= 10_000
    assert sw.analysis["move"] == "higher"
    assert not fake.ptt
    # Operating settings back as they were (USB-D on 7.074, full power).
    assert (fake.freq, fake.mode, fake.data, fake.power) == (7_074_000, 0x01, 1, 255)
    recorded = service.history.entries()[0]
    assert recorded["kind"] == kind and recorded["points"] == sw.points   # kept whole
    assert sw.entry_id == recorded["id"]
    if kind == "swr":
        final = service.save_final("12,75")
        assert final["coil"] == 12.75 and final["points"] == sw.points
        assert service.history.entries("40m")[-1]["kind"] == "final"
        # The sweep is given the position it was made at.
        assert service.history.entry(sw.entry_id)["coil"] == 12.75
    else:
        assert fake.keyed == 0    # a noise sweep never transmits
        # The position alone can be kept: no SWR sweep on this band to go with it.
        entry = service.save_final("12", note="by ear")
        assert entry["coil"] == 12 and "resonance" not in entry and entry["note"] == "by ear"


def test_swr_sweep_refused_outside_band_or_without_tx(tmp_path):
    async def run():
        station, service = make_service(tmp_path, FakeRadio(7_100_000))
        station.connected = True
        # --tx alone is not enough: the remote control starts off.
        assert not service.remote
        with pytest.raises(SweepError, match="remote control"):
            service.start_sweep({"kind": "swr", "start": 7_000_000, "stop": 7_100_000,
                                 "step": 10_000})
        service.set_remote(True)
        with pytest.raises(SweepError, match="one amateur band"):
            service.start_sweep({"kind": "swr", "start": 6_950_000, "stop": 7_100_000,
                                 "step": 10_000})
        station, service = make_service(tmp_path, FakeRadio(7_100_000), tx_enabled=False)
        station.connected = True
        with pytest.raises(SweepError, match="--tx"):
            service.start_sweep({"kind": "swr", "start": 7_000_000, "stop": 7_100_000,
                                 "step": 10_000})
        # The noise sweep is still allowed in receive-only mode.
        service.start_sweep({"kind": "noise", "start": 7_000_000, "stop": 7_100_000,
                             "step": 10_000})
        service.stop_sweep()

    asyncio.run(run())


def test_antenna_page_listens_on_the_configured_host(tmp_path):
    async def run():
        config = Config(swr_host="127.0.0.1", swr_port=0,
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        station = Station(open_rig(config, FakeRadio(7_100_000)), config, _Ui())
        service = SwrService(station, config, station.ui)
        await service.start()
        try:
            return [s.getsockname()[0] for s in service._web.sockets]
        finally:
            await service.stop()

    assert asyncio.run(run()) == ["127.0.0.1"]


def test_ptt_blocked_by_the_rig_in_receive_only_mode(tmp_path):
    from ic705suite.rig.base import TxDisabledError

    async def run():
        fake = FakeRadio(7_100_000)
        station, _ = make_service(tmp_path, fake, tx_enabled=False)
        await station.start()
        try:
            with pytest.raises(TxDisabledError):
                await station.rig.ptt(True)
            await station.rig.ptt(False)   # releasing is always allowed
        finally:
            await station.stop()
        assert fake.keyed == 0

    asyncio.run(run())


def test_legacy_history_is_imported_once(tmp_path):
    legacy = tmp_path / "history.json"
    legacy.write_text(json.dumps([{"ts": "2026-10-10 12:04:53", "band": "40m", "kind": "final",
                                   "coil": 12.2, "resonance": 7_095_000}]))
    url = f"sqlite:///{tmp_path / 'suite.db'}"
    HistoryStore(url, legacy).close()
    store = HistoryStore(url, legacy)
    assert [e["coil"] for e in store.entries("40m")] == [12.2]
    store.close()


def test_unknown_adjustment_learns_from_history():
    freqs = list(range(7_000_000, 7_200_001, 20_000))
    swr = [1.0 + abs(f - 7_120_000) / 40_000 for f in freqs]
    a = analyze("swr", freqs, swr, 7_074_000, "40m", [], coil_sign=0, default_slopes={})
    assert (a.move, a.shift, a.units) == ("", "down", None)   # only where the resonance goes
    # Two saved positions: a higher number gave a higher frequency on this antenna.
    hist = [{"kind": "final", "band": "40m", "coil": 3, "resonance": 7_000_000},
            {"kind": "final", "band": "40m", "coil": 5, "resonance": 7_200_000}]
    a = analyze("swr", freqs, swr, 7_074_000, "40m", hist, coil_sign=0, default_slopes={})
    assert (a.move, a.shift, a.khz_per_unit) == ("lower", "down", 100.0)
    assert a.units == 0.5


def test_antennas_keep_their_own_history(tmp_path):
    store = HistoryStore(f"sqlite:///{tmp_path / 'suite.db'}", legacy=None)
    first = store.antenna()
    assert first["name"] == "Antenna 1" and first["active"]
    store.add({"ts": "1", "band": "40m", "kind": "final", "coil": 12.2, "resonance": 7_095_000})
    komunica = store.save_antenna("Komunica HF-PRO-2-PLUS-T", "vertical", coil_sign=-1)
    assert store.antenna()["id"] == komunica["id"] and store.entries() == []
    store.add({"ts": "2", "band": "20m", "kind": "final", "coil": 4.5})
    assert set(store.settings()) == {"20m"}
    store.select_antenna(first["id"])
    assert store.settings()["40m"]["coil"] == 12.2
    with pytest.raises(ValueError):
        store.save_antenna("Komunica HF-PRO-2-PLUS-T")        # the name is taken
    store.delete_antenna(komunica["id"])
    assert [a["name"] for a in store.antennas()] == ["Antenna 1"]
    with pytest.raises(ValueError):
        store.delete_antenna(first["id"])                    # the last one stays


def test_history_from_before_the_antennas(tmp_path):
    """The old single history becomes the Komunica's (as swr-remote was made for it)."""
    import sqlalchemy as sa

    url = f"sqlite:///{tmp_path / 'suite.db'}"
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE swr_history (id INTEGER PRIMARY KEY, ts VARCHAR(32) "
                             "NOT NULL, band VARCHAR(10) NOT NULL, kind VARCHAR(10) NOT NULL, "
                             "data TEXT NOT NULL)"))
        conn.execute(sa.text("INSERT INTO swr_history (ts, band, kind, data) VALUES "
                             "('2026-10-09', '40m', 'final', "
                             "'{\"band\": \"40m\", \"kind\": \"final\", \"coil\": 12.2, "
                             "\"resonance\": 7095000}')"))
    engine.dispose()
    store = HistoryStore(url, legacy=None)
    antenna = store.antenna()
    assert antenna["name"] == "Komunica HF-PRO-2-PLUS-T" and antenna["coil_sign"] == -1
    assert store.settings()["40m"]["coil"] == 12.2 and len(store.antennas()) == 1


def test_phone_page_needs_the_remote_control(tmp_path):
    """With the remote control off the page only watches; stopping is always allowed, and
    the page has no way to switch the remote control on."""
    import json

    from ic705suite.apps.swr.web import route

    async def run():
        station, service = make_service(tmp_path, FakeRadio(7_100_000))
        station.connected = True
        sweep = json.dumps({"kind": "noise", "start": 7_000_000, "stop": 7_100_000,
                            "step": 10_000}).encode()
        reply = await route(service, "POST", "/api/sweep", sweep)
        assert b"400" in reply.split(b"\r\n")[0] and b"remote control is off" in reply
        assert b"400" in (await route(service, "POST", "/api/final", b'{"coil": 3}'))[:20]
        assert b"200" in (await route(service, "POST", "/api/stop", b"{}"))[:20]
        assert b"404" in (await route(service, "POST", "/api/remote", b'{"on": true}'))[:20] \
            or not service.remote
        assert not service.remote
        state = json.loads((await route(service, "GET", "/api/state", b"")).split(b"\r\n\r\n")[1])
        assert state["remote"] is False
        service.set_remote(True)
        assert b"200" in (await route(service, "POST", "/api/sweep", sweep))[:20]
        service.stop_sweep()

    asyncio.run(run())


def test_history_entries_photos_from_the_phone_and_the_link(tmp_path):
    """An entry's position and note change, a photo arrives (whole from the phone, in pieces
    over the link), is replaced and removed with its file, and the entry is deleted."""
    import base64

    from ic705suite.apps.swr.service import PHOTO_CHUNK
    from ic705suite.apps.swr.web import route

    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 300          # bigger than a piece
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 50

    def body(reply):
        return json.loads(reply.split(b"\r\n\r\n", 1)[1])

    async def run():
        station, service = make_service(tmp_path, FakeRadio(7_100_000))
        service.photos_dir = tmp_path / "photos"
        entry_id = service.history.add({"ts": "2026-10-11 10:00:00", "band": "40m",
                                        "kind": "swr", "points": [{"f": 7_000_000, "v": 1.5}]})
        service.set_remote(True)
        # The phone: position and note, then a photo as the request's body.
        reply = await route(service, "POST", "/api/entry",
                            json.dumps({"id": entry_id, "coil": "11,5", "note": " rain "}).encode())
        assert body(reply)["coil"] == 11.5 and body(reply)["note"] == "rain"
        assert b"400" in (await route(service, "POST", f"/api/photo?id={entry_id}", b"hello"))[:20]
        name = body(await route(service, "POST", f"/api/photo?id={entry_id}", png))["photo"]
        assert (service.photos_dir / name).read_bytes() == png
        got = await route(service, "GET", f"/api/photo?id={entry_id}", b"")
        assert b"image/png" in got.split(b"\r\n\r\n")[0] and got.endswith(png)
        listed = body(await route(service, "GET", "/api/history?band=40m", b""))
        assert listed[0]["photo"] == name and "points" not in listed[0]
        # The link: in pieces; it replaces the phone's photo (and its file).
        assert len(jpeg) > PHOTO_CHUNK
        offset = 0
        while offset < len(jpeg):
            piece = jpeg[offset:offset + PHOTO_CHUNK]
            reply = await service.call("photo.put", {
                "id": entry_id, "offset": offset, "data": base64.b64encode(piece).decode(),
                "done": offset + len(piece) >= len(jpeg)})
            offset += len(piece)
        new = reply["entry"]["photo"]
        assert new != name and not (service.photos_dir / name).exists()
        back, offset = b"", 0
        while True:
            reply = await service.call("photo.get", {"id": entry_id, "offset": offset})
            back += base64.b64decode(reply["data"])
            offset = len(back)
            if offset >= reply["size"]:
                break
        assert back == jpeg and reply["type"] == "image/jpeg"
        # Out of step: refused, and the next upload starts again.
        assert "error" in await service.call("photo.put", {"id": entry_id, "offset": 99,
                                                           "data": ""})
        # The phone deletes the entry; its photo goes with it.
        await route(service, "POST", "/api/entry/delete", json.dumps({"id": entry_id}).encode())
        assert not (service.photos_dir / new).exists()
        assert "error" in await service.call("entry", {"id": entry_id})
        # Without the remote control the phone cannot change the history.
        service.set_remote(False)
        assert b"400" in (await route(service, "POST", "/api/entry/delete", b'{"id": 1}'))[:20]

    asyncio.run(run())


def test_phone_page_refuses_too_large_bodies(tmp_path):
    from ic705suite.apps.swr.service import MAX_PHOTO
    from ic705suite.apps.swr.web import handle

    async def run():
        station, service = make_service(tmp_path, FakeRadio(7_100_000))
        server = await asyncio.start_server(lambda r, w: handle(service, r, w), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"POST /api/photo?id=1 HTTP/1.1\r\nContent-Length: {MAX_PHOTO + 1}"
                         "\r\n\r\n".encode())
            await writer.drain()
            reply = await reader.read()
            writer.close()
            assert reply.startswith(b"HTTP/1.1 413")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())
