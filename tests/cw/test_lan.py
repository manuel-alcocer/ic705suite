"""LAN transport against a minimal fake of the radio's network server.

The fake implements the protocol as documented by wfview/kappanhang, so this
checks our packet layouts and state machine, not the real radio.
"""

import asyncio
import struct

import numpy as np
import pytest

from ic705suite.apps.cw import civ
from ic705suite.apps.cw.transport import TransportError
from ic705suite.apps.cw.transport.lan import LanTransport, encode_credential

SERVER_ID = b"\x11\x22\x33\x44"
TOKEN = b"\xaa\xbb\xcc\xdd"


def header(length, ptype, seq, rcvd_id):
    p = bytearray(length)
    struct.pack_into("<IHH", p, 0, length, ptype, seq)
    p[8:12] = SERVER_ID
    p[12:16] = rcvd_id
    return p


class FakeStream(asyncio.DatagramProtocol):
    def __init__(self, server, name):
        self.server, self.name = server, name
        self.seq = 1
        self.client_addr = None
        self.client_id = b"\0" * 4
        self.pinged_back = False

    def connection_made(self, transport):
        self.transport = transport

    def send(self, p):
        struct.pack_into("<H", p, 6, self.seq)
        self.seq += 1
        self.transport.sendto(bytes(p), self.client_addr)

    def datagram_received(self, data, addr):
        self.client_addr = addr
        length, ptype, seq = struct.unpack_from("<IHH", data)
        assert length == len(data)
        self.client_id = data[8:12]
        if len(data) == 0x10 and ptype in (0x03, 0x06):
            p = header(0x10, 0x04 if ptype == 0x03 else 0x06, 0, self.client_id)
            self.transport.sendto(bytes(p), addr)
            if ptype == 0x06:  # ask the client for a ping reply
                ping = header(0x15, 0x07, 77, self.client_id)
                ping[0x11:0x15] = b"\x01\x02\x03\x04"
                self.transport.sendto(bytes(ping), addr)
            return
        if ptype == 0x07 and len(data) == 0x15:
            if data[0x10] == 0x01 and seq == 77:
                assert data[0x11:0x15] == b"\x01\x02\x03\x04"
                self.pinged_back = True
            return
        if len(data) == 0x10:
            return
        self.server.handle(self, data)


class FakeRadio:
    def __init__(self, username="user", password="secret"):
        self.username, self.password = username, password
        self.frequency = 14_060_000
        self.client_audio_port = None
        self.stream_request = None

    async def start(self):
        loop = asyncio.get_running_loop()
        self.ctl_t, self.ctl = await loop.create_datagram_endpoint(
            lambda: FakeStream(self, "control"), local_addr=("127.0.0.1", 0))
        self.civ_t, self.civ = await loop.create_datagram_endpoint(
            lambda: FakeStream(self, "civ"), local_addr=("127.0.0.1", 0))
        self.aud_t, self.aud = await loop.create_datagram_endpoint(
            lambda: FakeStream(self, "audio"), local_addr=("127.0.0.1", 0))
        return self.ctl_t.get_extra_info("sockname")[1]

    def close(self):
        for t in (self.ctl_t, self.civ_t, self.aud_t):
            t.close()

    def handle(self, stream, data):
        if stream.name == "control":
            self.handle_control(stream, data)
        elif stream.name == "civ":
            self.handle_civ(stream, data)

    def handle_control(self, s, data):
        size = len(data)
        if size == 0x80:  # login
            assert struct.unpack_from(">H", data, 0x12)[0] == 0x70
            ok = (data[0x40:0x50] == encode_credential(self.username)
                  and data[0x50:0x60] == encode_credential(self.password))
            p = header(0x60, 0, 0, s.client_id)
            p[0x1A:0x1C] = data[0x1A:0x1C]
            p[0x1C:0x20] = TOKEN
            p[0x30:0x34] = b"\x00" * 4 if ok else b"\xff\xff\xff\xfe"
            p[0x40:0x44] = b"FTTH"
            s.send(p)
        elif size == 0x40:  # token
            assert data[0x1C:0x20] == TOKEN
            p = header(0x40, 0, 0, s.client_id)
            p[0x15] = data[0x15]
            s.send(p)
            if data[0x15] == 0x02:
                caps = header(0x42 + 0x66, 0, 0, s.client_id)
                struct.pack_into(">H", caps, 0x40, 1)
                radio = 0x42
                caps[radio + 7:radio + 9] = b"\x80\x10"
                caps[radio + 0x0A:radio + 0x10] = b"\x00\x90\xc7\x01\x02\x03"
                caps[radio + 0x10:radio + 0x16] = b"IC-705"
                caps[radio + 0x52] = 0xA4
                s.send(caps)
        elif size == 0x90:  # stream request
            self.stream_request = bytes(data)
            assert data[0x20 + 0x0A:0x30] == b"\x00\x90\xc7\x01\x02\x03"
            self.client_audio_port = struct.unpack_from(">I", data, 0x80)[0]
            p = header(0x50, 0, 0, s.client_id)
            struct.pack_into(">H", p, 0x42, self.civ_t.get_extra_info("sockname")[1])
            struct.pack_into(">H", p, 0x46, self.aud_t.get_extra_info("sockname")[1])
            s.send(p)

    def handle_civ(self, s, data):
        if len(data) == 0x16:
            assert data[0x15] in (0x04, 0x00)
            return
        assert data[0x10] == 0xC1
        length = struct.unpack_from("<H", data, 0x11)[0]
        for frame in civ.CivParser().feed(data[0x15:0x15 + length]):
            if frame.body == b"\x03":
                reply = civ.build_frame(0xE0, b"\x03" + civ.encode_frequency(self.frequency), src=0xA4)
                p = header(0x15 + len(reply), 0, 0, s.client_id)
                p[0x10] = 0xC1
                struct.pack_into("<H", p, 0x11, len(reply))
                p[0x15:] = reply
                s.send(p)

    def send_audio(self, samples: np.ndarray):
        payload = (samples * 32767).astype("<i2").tobytes()
        p = header(0x18 + len(payload), 0, 0, self.aud.client_id)
        p[0x10:0x12] = b"\x97\x81"
        struct.pack_into(">H", p, 0x16, len(payload))
        p[0x18:] = payload
        self.aud.send(p)


def test_full_connection_civ_and_audio():
    async def scenario():
        radio = FakeRadio()
        port = await radio.start()
        lan = LanTransport("127.0.0.1", "user", "secret", 8000, control_port=port)
        civ_rx, audio_rx = [], []
        lan.on_civ = civ_rx.append
        lan.on_audio = audio_rx.append
        try:
            await lan.connect()
            assert lan.civ_address == 0xA4 and lan.radio_name == "IC-705"
            req = radio.stream_request
            assert req[0x72] == 0x04 and struct.unpack_from(">I", req, 0x74)[0] == 8000
            lan.send_civ(civ.build_frame(0xA4, b"\x03"))
            radio.send_audio(np.full(160, 0.5, np.float32))
            await asyncio.sleep(0.3)
            frames = civ.CivParser().feed(b"".join(civ_rx))
            assert civ.decode_frequency(frames[0].body[1:6]) == 14_060_000
            assert len(audio_rx) == 1 and abs(audio_rx[0][0] - 0.5) < 1e-3
            assert radio.ctl.pinged_back and radio.civ.pinged_back
        finally:
            await lan.close()
            radio.close()

    asyncio.run(scenario())


def test_bad_password_is_reported():
    async def scenario():
        radio = FakeRadio()
        port = await radio.start()
        lan = LanTransport("127.0.0.1", "user", "wrong", 8000, control_port=port)
        try:
            with pytest.raises(TransportError, match="invalid username or password"):
                await lan.connect()
        finally:
            radio.close()

    asyncio.run(scenario())


def test_credential_encoding_known_values():
    # 'A' (0x41) at position 0 -> table[0x41]; 'A' at position 1 -> table[0x42]
    assert encode_credential("AA")[:2] == bytes([0x5E, 0x4A])
    assert len(encode_credential("x" * 40)) == 16
