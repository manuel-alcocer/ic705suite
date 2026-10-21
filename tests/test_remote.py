"""Remote control from the phone: Opus both ways, the page and the WebSocket on one HTTPS
port, the messages in the phone's language, a renewed certificate taken without a restart."""

import asyncio
import json
import shutil
import ssl
import subprocess

import numpy as np
import pytest
from websockets.asyncio.client import connect

from ic705suite import opus
from ic705suite.config import Config
from ic705suite.rig.icom.sim import SimTransport
from ic705suite.station.core import OPUS_RX, OPUS_TX, Core
from ic705suite.station.remote import RemoteServer

pytestmark = pytest.mark.skipif(not opus.available() or shutil.which("openssl") is None,
                                reason="needs libopus and openssl")
USB = 0x01


def tone(rate: int, seconds: float, hz: float = 700.0) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * hz * t) * 12000).astype("<i2").tobytes()


def make_cert(folder, name="cert"):
    cert, key = folder / f"{name}.pem", folder / f"{name}.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
                    "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1", "-subj",
                    "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)
    return cert, key


def trusting():
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def test_opus_encodes_at_the_radios_rate_and_decodes_at_the_browsers():
    encoder, decoder = opus.Encoder(8000), opus.Decoder(48000)
    pcm = tone(8000, 1.0)
    packets = encoder.encode(pcm[:1001]) + encoder.encode(pcm[1001:])
    assert len(packets) == 50 and all(len(p) < 120 for p in packets)    # 20 ms each, small
    out = np.frombuffer(b"".join(decoder.decode(p) for p in packets), "<i2")
    assert len(out) == 48000
    spectrum = np.abs(np.fft.rfft(out[4800:]))
    assert abs(np.argmax(spectrum) * 48000 / len(out[4800:]) - 700) < 5
    assert decoder.decode(b"\xff\xff\xff") == b""                  # not Opus: nothing
    with pytest.raises(opus.OpusError):
        opus.Encoder(44100)


async def start(tmp_path, tx=True):
    sim = SimTransport(stations=[], seed=3)
    sim.mode = USB
    core = Core(sim, Config(tx_enabled=tx, swr_web=False))
    await core.start()
    cert, key = make_cert(tmp_path)
    server = RemoteServer(core, "127.0.0.1", 0, "secret", str(cert), str(key))
    stop = asyncio.Event()
    task = asyncio.create_task(server.run(stop))
    port = None
    while port is None:
        await asyncio.sleep(0.05)
        for event in core.hub.backlog:
            if event.get("msgid", "").startswith("remote control for the phone"):
                port = event["args"]["port"]
    return core, sim, server, stop, task, port


async def get(port: int, path: str) -> tuple[str, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=trusting())
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    data = await reader.read()
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return head.decode().split("\r\n")[0], body


async def hello(port: int, language: str = "es"):
    ws = await connect(f"wss://127.0.0.1:{port}/ws", ssl=trusting())
    await ws.send(json.dumps({"type": "hello", "token": "secret", "client": "phone",
                              "language": language}))
    return ws


async def reply(ws, wanted_id: int, seen: list | None = None) -> dict:
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(raw, bytes):
            continue
        event = json.loads(raw)
        if seen is not None:
            seen.append(event)
        if event["type"] == "reply" and event["id"] == wanted_id:
            return event["result"]


def test_the_page_and_the_socket_share_the_https_port(tmp_path):
    async def scenario():
        core, _sim, _server, stop, task, port = await start(tmp_path)
        try:
            status, body = await get(port, "/")
            assert "200" in status and b"<title>IC-705 remoto</title>" in body
            status, body = await get(port, "/manifest.webmanifest")
            assert json.loads(body)["display"] == "standalone"
            assert "404" in (await get(port, "/../config.toml"))[0]
            ws = await connect(f"wss://127.0.0.1:{port}/ws", ssl=trusting())
            await ws.send(json.dumps({"type": "hello", "token": "wrong"}))
            assert json.loads(await ws.recv())["fatal"]
            await ws.close()
        finally:
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_the_phone_hears_and_talks_in_opus(tmp_path):
    async def scenario():
        core, sim, _server, stop, task, port = await start(tmp_path)
        ws = await hello(port)
        try:
            await ws.send(json.dumps({"type": "call", "id": 1, "method": "audio.listen",
                                      "params": {"on": True, "codec": "opus"}}))
            assert (await reply(ws, 1))["codec"] == "opus"
            packets = 0
            while packets < 5:                            # the receive audio, as Opus
                raw = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(raw, bytes):
                    assert raw[0] == OPUS_RX and len(raw) < 200
                    packets += 1
            encoder = opus.Encoder(48000)                 # the browser's rate
            for packet in encoder.encode(tone(48000, 0.1)):   # not keyed yet: dropped
                await ws.send(bytes([OPUS_TX]) + packet)
            await ws.send(json.dumps({"type": "call", "id": 2, "method": "voice.ptt",
                                      "params": {"on": True}}))
            assert await reply(ws, 2) == {"on": True} and sim.ptt
            assert sim.tx_audio_bytes == 0
            for packet in encoder.encode(tone(48000, 0.2)):
                await ws.send(bytes([OPUS_TX]) + packet)
            await asyncio.sleep(0.3)
            assert sim.tx_audio_bytes == 10 * 160 * 2     # 10 packets at the radio's 8 kHz
            await ws.close()                              # gone while talking
            await asyncio.sleep(0.3)
            assert not sim.ptt and not core.voice.keyed
        finally:
            await ws.close()
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_messages_come_in_the_phones_language(tmp_path):
    async def scenario():
        core, sim, _server, stop, task, port = await start(tmp_path, tx=False)
        ws = await hello(port, "es-ES")
        try:
            await ws.send(json.dumps({"type": "call", "id": 1, "method": "voice.ptt",
                                      "params": {"on": True}}))
            seen: list[dict] = []
            assert "error" in await reply(ws, 1, seen) and not sim.ptt
            errors = [e["text"] for e in seen if e["type"] == "error"]
            assert any("la transmisión está desactivada" in text for text in errors)
        finally:
            await ws.close()
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_a_renewed_certificate_is_taken_without_a_restart(tmp_path):
    async def scenario():
        core, _sim, server, stop, task, port = await start(tmp_path)
        try:
            first = server._loaded
            new_cert, new_key = make_cert(tmp_path, "renewed")
            shutil.copy(new_cert, server.cert)
            shutil.copy(new_key, server.key)
            await get(port, "/")                          # noticed on a request...
            assert server._loaded != first
            _reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=trusting())
            served = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
            writer.close()                                # ...and served on the next one
            assert served == ssl.PEM_cert_to_DER_cert(new_cert.read_text())
        finally:
            stop.set()
            await task
            await core.stop()

    asyncio.run(scenario())


def test_without_a_certificate_the_core_says_so(tmp_path):
    async def scenario():
        core = Core(SimTransport(stations=[], seed=1), Config(swr_web=False))
        with pytest.raises(ValueError, match="certificate"):
            RemoteServer(core, "127.0.0.1", 0, "secret", str(tmp_path / "none.pem"), "")
        with pytest.raises(ValueError, match="token"):
            RemoteServer(core, "127.0.0.1", 0, "", str(tmp_path / "none.pem"), "")

    asyncio.run(scenario())
