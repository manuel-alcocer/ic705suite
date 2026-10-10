"""AGWPE framing, monitor parsing and telnet negotiation."""

from __future__ import annotations

from ic705suite.apps.packet.link.agw import HEADER, Frame, _parse_ports, decode_header, parse_monitor
from ic705suite.apps.packet.link.telnet import DO, IAC, WILL, WONT, strip_telnet
from ic705suite.apps.packet.tui.app import parse_connect


def test_frame_round_trip() -> None:
    frame = Frame(1, "D", "ea7wm-1", "EA1ABC", b"hello\r", 0xF0)
    raw = frame.encode()
    assert len(raw) == HEADER.size + 6
    decoded, length = decode_header(raw[: HEADER.size])
    assert (decoded.port, decoded.kind, decoded.pid) == (1, "D", 0xF0)
    assert (decoded.call_from, decoded.call_to, length) == ("EA7WM-1", "EA1ABC", 6)


def test_parse_monitor() -> None:
    heard = parse_monitor(" 1:Fm EA1ABC-7 To ID Via EA7WM-7* <UI pid=F0 Len=12 >[18:42:10]")
    assert heard is not None
    assert (heard.port, heard.src, heard.dest, heard.via, heard.ftype) == (
        1, "EA1ABC-7", "ID", "EA7WM-7*", "UI"
    )
    assert parse_monitor("garbage") is None


def test_strip_telnet_declines_options() -> None:
    text, replies = strip_telnet(bytes([IAC, DO, 1]) + b"user:" + bytes([IAC, WILL, 3]))
    assert text == b"user:"
    assert replies == bytes([IAC, WONT, 1, IAC, 254, 3])


def test_parse_connect() -> None:
    assert parse_connect(["ea1abc"], 1) == ("EA1ABC", [], 1)
    assert parse_connect(["2", "ea1abc", "v", "ea7wm-7,ea4x"], 1) == (
        "EA1ABC", ["EA7WM-7", "EA4X"], 2
    )
    assert parse_connect([], 1) is None


def test_parse_ports_skips_direwolf_unused_channels() -> None:
    text = "1;Port1 first soundcard mono;Port2 INVALID CHANNEL;Port3 INVALID CHANNEL;\0"
    assert _parse_ports(text) == ["1: Port1 first soundcard mono"]


def test_config_round_trip_keeps_bpq_apps(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from ic705suite.apps.packet import config as cfg

    original = cfg.Config("EA7KLX-4")
    original.bpq.user = "manuel"
    original.bpq.apps = {"EA7KLX-1": "BBS", "EA7KLX-7": ""}
    path = cfg.save(original, tmp_path / "c.toml")
    loaded = cfg.load(path)
    assert loaded.bpq.apps == original.bpq.apps and loaded.bpq.user == "manuel"
    assert loaded.bpq.command_for("ea7klx-7") == ""
    assert loaded.bpq.command_for("EA7KLX-2") == "C EA7KLX-2"
