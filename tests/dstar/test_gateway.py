"""The EXT gateway: the USB Terminal Mode frames (as the IC-705 sends them, idle bytes and
all), the reflectors' link packets and answers, and the voice routed both ways between a
simulated radio and a simulated reflector, with the radio's UR commands."""

import asyncio

from ic705suite.apps.dstar import gateway as gateway_module
from ic705suite.apps.dstar import links, terminal
from ic705suite.apps.dstar.protocol import Header, message_frames, pad


def test_frames_split_from_the_idle_bytes():
    pong = bytes([0x03, 0x03, 0xFF])
    ack = bytes([0x04, 0x23, 0x07, 0x00])
    buffer = bytearray(b"\xff" * 40 + pong + b"\x99" + ack + bytes([0x10, 0x12, 0x00]))
    assert terminal.split_frames(buffer) == [pong, ack]
    assert buffer == bytearray([0x10, 0x12, 0x00])        # the data frame, not whole yet


def test_the_data_port_is_interface_02(tmp_path):
    for name in ("usb-Icom_Inc._IC-705_IC-705_43002594-if00",
                 "usb-Icom_Inc._IC-705_IC-705_43002594-if02", "usb-Silicon_Labs_CP2102N-if00"):
        (tmp_path / name).touch()
    assert terminal.find_data_port(tmp_path).endswith("43002594-if02")
    assert terminal.find_data_port(tmp_path / "missing") == ""


def test_protocols_by_reflector():
    assert links.protocol_for("REF001") == "DPlus"
    assert links.protocol_for("XRF988") == "DExtra"
    assert links.protocol_for("XLX214") == links.protocol_for("DCS214") == "DCS"
    assert links.split_reflector("XLX214 D") == ("XLX214", "D")


def _link(cls, **calls):
    callbacks = {"on_state": lambda s: None, "on_header": lambda i, h: None,
                 "on_data": lambda i, s, f, e: None, **calls}
    return cls("DCS214 D", "192.0.2.1", "ea7klx", "EA7KLX B", **callbacks)


def test_the_link_packets():
    dcs = _link(links.DCSLink)
    packet = dcs.link_packet()
    assert len(packet) == 519 and packet[:9] == b"EA7KLX  B" and packet[9:10] == b"D"
    assert packet[11:19] == b"DCS214  "
    assert dcs.poll_packet() == b"EA7KLX B\x00DCS214 D"
    dextra = _link(links.DExtraLink)
    assert dextra.link_packet() == b"EA7KLX  BD\x00"
    assert dextra.unlink_packet() == b"EA7KLX  B \x00"


async def test_a_dcs_link_answered_and_its_voice():
    states, headers, frames = [], [], []
    dcs = _link(links.DCSLink, on_state=states.append,
                on_header=lambda i, h: headers.append((i, h)),
                on_data=lambda i, s, f, e: frames.append((i, s, f, e)))
    sent = []
    dcs._send = sent.append
    dcs.link()
    assert states == ["linking"] and len(sent[0]) == 519
    dcs.handle(b"EA7KLX BD" + b"\x00" + b"ACK\x00")
    assert states == ["linking", "linked"]
    packet = bytearray(100)
    packet[0:4] = b"0001"
    packet[7:43] = Header(rpt2="DCS214 D", rpt1="DCS214 G", your="CQCQCQ", my="EA1ABC",
                          my2="705").callsigns()
    packet[43:45] = (0x1234).to_bytes(2, "little")
    packet[45] = 3
    packet[46:58] = bytes(range(12))
    dcs.handle(bytes(packet))
    dcs.handle(bytes(packet))
    assert len(headers) == 1 and headers[0][1].my.strip() == "EA1ABC"
    assert frames[0] == (0x1234, 3, bytes(range(12)), False)
    dcs._cancel()


async def test_dplus_refused_and_linked():
    states = []
    ref = links.DPlusLink("REF001 C", "192.0.2.1", "EA7KLX", "EA7KLX B",
                          on_state=states.append, on_header=lambda i, h: None,
                          on_data=lambda i, s, f, e: None)
    sent = []
    ref._send = sent.append
    ref.link()
    ref.handle(b"\x05\x00\x18\x00\x01")             # LINK1 echoed: LINK2 goes out
    assert len(sent[-1]) == 28 and sent[-1][4:10] == b"EA7KLX"
    ref.handle(b"\x08\xc0\x04\x00OKRO")
    assert states[-1] == "refused"
    ref.link()
    ref.handle(b"\x08\xc0\x04\x00OKRW")
    assert states[-1] == "linked"
    ref._cancel()


class FakeTerminal:
    def __init__(self, *args, on_connected, on_header, on_data) -> None:
        self.connected = True
        self.path = "/dev/fake"
        self.last_error = ""
        self.on_header, self.on_data = on_header, on_data
        self.written: list = []

    def start(self) -> None: ...

    def close(self) -> None: ...

    def write_header(self, header) -> None:
        self.written.append(("header", header))

    def write_data(self, frame, end=False) -> None:
        self.written.append(("end",) if end else ("data", bytes(frame)))


class FakeLink:
    instances: list = []

    def __init__(self, reflector, address, login, terminal_call, *, on_state, on_header,
                 on_data) -> None:
        self.reflector, self.address = pad(reflector), address
        self.on_state, self.on_header, self.on_data = on_state, on_header, on_data
        self.state = "idle"
        self.sent: list = []
        FakeLink.instances.append(self)

    async def open(self) -> None: ...

    def link(self) -> None:
        self.state = "linking"
        self.on_state("linking")
        asyncio.get_running_loop().call_soon(self._answer)

    def _answer(self) -> None:
        self.state = "linked"
        self.on_state("linked")

    def close(self) -> None:
        self.state = "unlinked"

    def send_header(self, stream_id, header) -> None:
        self.sent.append(("header", header))

    def send_data(self, stream_id, seq, frame, end=False) -> None:
        self.sent.append(("end",) if end else ("data", seq, bytes(frame)))


class FakeHosts:
    def resolve(self, name, protocol):
        return "192.0.2.7" if name != "XLX999" else None


def _gateway(monkeypatch, commands=None):
    commands = [] if commands is None else commands
    FakeLink.instances = []
    monkeypatch.setattr(gateway_module, "IcomTerminal", FakeTerminal)
    monkeypatch.setitem(links.LINKS, "DCS", FakeLink)
    monkeypatch.setattr(gateway_module, "ANNOUNCE_DELAY", 0)
    monkeypatch.setattr(gateway_module, "ECHO_DELAY", 0)
    monkeypatch.setattr(gateway_module, "FRAME", 0)
    changes = []
    gw = gateway_module.ExtGateway("EA7KLX", "EA7KLX B", "EA7KLX G", hosts=FakeHosts(),
                                   on_change=lambda: changes.append(1),
                                   on_command=lambda c, r: commands.append((c, r)))
    return gw, gw.terminal


async def test_linked_and_the_voice_both_ways(monkeypatch):
    gw, radio = _gateway(monkeypatch)
    assert await gw.link("XLX214 D") == (True, "linked")
    assert gw.linked_reflector() == "XLX214 D"
    reflector = FakeLink.instances[0]
    # Our over goes to the reflector, to CQCQCQ whatever UR the radio had
    radio.on_header(Header(rpt2="", rpt1="", your="CQCQCQ", my="EA7KLX", my2="705"))
    radio.on_data(bytes(12), False)
    radio.on_data(bytes(12), True)
    assert reflector.sent[0][0] == "header" and reflector.sent[0][1].your == pad("CQCQCQ")
    assert reflector.sent[1] == ("data", 0, bytes(12)) and reflector.sent[2] == ("end",)
    # A station on the reflector reaches the radio from the gateway
    reflector.on_header(77, Header(my="EA1ABC", my2="705"))
    reflector.on_header(77, Header(my="EA1ABC", my2="705"))       # repeated
    reflector.on_data(77, 0, bytes(range(12)), False)
    reflector.on_data(77, 1, b"", True)
    headers = [w for w in radio.written if w[0] == "header"]
    assert len(headers) == 1 and headers[0][1].rpt1 == "EA7KLX G" and \
        headers[0][1].rpt2 == "EA7KLX B" and headers[0][1].my.strip() == "EA1ABC"
    assert radio.written[-1] == ("end",)
    gw.stop()


async def test_the_radios_commands(monkeypatch):
    commands = []
    gw, radio = _gateway(monkeypatch, commands)
    radio.on_header(Header(your="XLX214DL", my="EA7KLX"))
    radio.on_data(bytes(12), True)
    for _ in range(5):
        await asyncio.sleep(0)
    assert commands == [("link", "XLX214 D")] and gw.linked_reflector() == "XLX214 D"
    assert gw.command_recent()
    await asyncio.sleep(0.05)
    told = [w for w in radio.written if w[0] == "header"]
    assert told and told[-1][1].my == "EA7KLX G" and told[-1][1].my2 == "INFO"
    assert ("data", message_frames("Linked to XLX214 D")[1]) in radio.written
    # Echo: the over played back
    radio.written.clear()
    radio.on_header(Header(your="       E", my="EA7KLX"))
    radio.on_data(bytes([1] * 12), False)
    radio.on_data(bytes(12), True)
    await asyncio.sleep(0.05)
    assert ("data", bytes([1] * 12)) in radio.written and radio.written[-1] == ("end",)
    # Unlink
    radio.on_header(Header(your="U", my="EA7KLX"))
    radio.on_data(bytes(12), True)
    assert gw.linked_reflector() == "" and commands[-1] == ("unlink", "")
    gw.stop()


async def test_a_reflector_not_found(monkeypatch):
    gw, _radio = _gateway(monkeypatch)
    assert await gw.link("XLX999 A") == (False, "not_found")
    assert gw.conn is None
    gw.stop()


async def test_the_terminal_on_a_serial_port():
    import os
    import pty

    master, slave = pty.openpty()
    headers, states = [], []
    term = terminal.IcomTerminal(os.ttyname(slave), on_connected=states.append,
                                 on_header=headers.append, on_data=lambda f, e: None)
    try:
        term.start()
        await asyncio.sleep(0.15)
        assert os.read(master, 100).startswith(terminal.POLL)        # polled
        os.write(master, b"\xff\xff" + bytes([0x03, 0x03, 0xFF]))
        await asyncio.sleep(0.05)
        assert states == [True] and term.connected
        header = Header(rpt2="EA7KLX B", rpt1="EA7KLX G", your="CQCQCQ", my="EA1ABC")
        os.write(master, bytes([0x2C, 0x10]) + header.to_radio() + b"\xff")
        await asyncio.sleep(0.05)
        assert headers and headers[0].my.strip() == "EA1ABC"
        term.write_header(header)
        await asyncio.sleep(0.02)
        sent = os.read(master, 200)
        assert bytes([0x29, 0x20]) in sent                           # in flight, retried
        os.write(master, bytes([0x03, 0x21, 0x00]))
        await asyncio.sleep(0.02)
        assert term.in_flight is None
    finally:
        term.close()
        os.close(master)
        os.close(slave)


def test_ext_on_the_simulated_radio(tmp_path, monkeypatch):
    """The core in EXT: the radio answering the gateway over USB, a reflector linked from a
    client (UR set back to CQCQCQ), kept for next time, and unlinked."""
    from ic705suite.apps.dstar import service as service_module
    from ic705suite.config import Config
    from ic705suite.station.links import LocalLink

    from .test_dstar import until

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(service_module, "FAST_POLL", 0.1)
    monkeypatch.setattr(service_module, "lookup_name", lambda call: ("", ""))
    FakeLink.instances = []
    monkeypatch.setattr(gateway_module, "IcomTerminal", FakeTerminal)
    monkeypatch.setattr(gateway_module, "HostDirectory", FakeHosts)
    monkeypatch.setitem(links.LINKS, "DCS", FakeLink)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA4SIM", locator="IM77",
                        tx_enabled=True, database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        await link.connect()
        sim, dstar = link.core.station.transport, link.core.dstar
        sim.mode = 0x17
        sim.dv_tx_calls = b"/XLX214D" + b" " * 16                      # EXT: R1 / R2 blank
        try:
            await link.call("dstar.start")
            await until(lambda: dstar.my_call == "EA4SIM" and dstar.gateway is not None)
            state = await link.call("dstar.state")
            assert state["via"] == "ext" and state["gateway"]["usb"]
            assert state["gateway"]["terminal"] == "EA4SIM B" and state["to"] == ""
            assert await link.call("dstar.change", to="dcs214d") == {"to": "DCS214 D"}
            assert sim.dv_tx_calls == b"CQCQCQ  " + b" " * 16
            await until(lambda: (dstar.reflector_info or {}).get("name") == "D-STAR Spain")
            state = await link.call("dstar.state")
            assert state["to"] == "DCS214 D" and state["gateway"]["link"] == "linked"
            assert dstar._last_link() == "DCS214 D"
            assert await link.call("dstar.unlink") == {"unlinked": "DCS214 D"}
            assert (await link.call("dstar.state"))["to"] == "" and dstar._last_link() == ""
        finally:
            await link.close()

    asyncio.run(scenario())


def test_the_gateway_lives_with_the_core(tmp_path, monkeypatch):
    """No client needed: the gateway starts with the core, links the reflector of last time
    when the radio answers, keeps it when D-STAR is closed, and stops with the core."""
    from ic705suite.config import Config
    from ic705suite.station.links import LocalLink

    from .test_dstar import until

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    FakeLink.instances = []
    monkeypatch.setattr(gateway_module, "IcomTerminal", FakeTerminal)
    monkeypatch.setattr(gateway_module, "HostDirectory", FakeHosts)
    monkeypatch.setitem(links.LINKS, "DCS", FakeLink)

    async def scenario():
        config = Config(connection="sim", swr_web=False, callsign="EA4SIM", locator="IM77",
                        database_url=f"sqlite:///{tmp_path / 'suite.db'}")
        link = LocalLink(None, config)
        dstar = link.core.dstar
        dstar._save_last_link("DCS214 D")
        await link.connect()
        gateway = dstar.gateway
        try:
            assert gateway is not None and not dstar.on
            gateway._radio_connected(True)
            await until(lambda: gateway.linked_reflector() == "DCS214 D")
            await link.call("dstar.start")
            assert (await link.call("dstar.state"))["to"] == "DCS214 D"
            await link.call("dstar.stop")
            assert dstar.gateway is gateway and gateway.linked_reflector() == "DCS214 D"
        finally:
            await link.close()
        assert dstar.gateway is None and FakeLink.instances[-1].state == "unlinked"

    asyncio.run(scenario())
