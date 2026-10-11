"""A fake Direwolf AGWPE server: what a real one answered in the lab (two Direwolf joined by
audio), so the tests need neither a radio nor Direwolf.

In session frames Direwolf puts the other station in call_from and ours in call_to; a call
we make is answered "*** CONNECTED With Station X", one made to us "*** CONNECTED To
Station X".
"""

from __future__ import annotations

import asyncio
import struct

from ic705suite.apps.packet.agwpe import HEADER, Frame, decode_header


class FakeTnc:
    def __init__(self, answers: bool = True, echo: bool = True) -> None:
        self.answers = answers           # the stations we call answer (else: no answer)
        self.echo = echo                 # the far end echoes what it gets
        self.received: list[Frame] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.registered: set[str] = set()
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> FakeTnc:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        for writer in self.writers:
            writer.close()
        self.writers.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        try:
            while True:
                frame, length = decode_header(await reader.readexactly(HEADER.size))
                frame.data = await reader.readexactly(length) if length else b""
                self.received.append(frame)
                for reply in self.reply(frame):
                    writer.write(reply.encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    def reply(self, frame: Frame) -> list[Frame]:
        kind, mine, other = frame.kind, frame.call_from, frame.call_to
        if kind == "R":
            return [Frame(0, "R", data=struct.pack("<II", 2005, 127))]
        if kind == "G":
            return [Frame(0, "G", data=b"1;Port1 first soundcard mono;Port2 INVALID CHANNEL;\0")]
        if kind == "X":
            self.registered.add(mine)
            return [Frame(0, "X", mine, data=b"\x01")]
        if kind == "m":
            return [self.monitor_frame("EA1ABC-7", "ID", "UI", "EA1ABC")]
        if kind in "Cv":
            if not self.answers:
                return [Frame(0, "d", other, mine,
                              f"*** DISCONNECTED From Station {other}\r\0".encode())]
            return [Frame(0, "C", other, mine,
                          f"*** CONNECTED With Station {other}\r\0".encode()),
                    Frame(0, "D", other, mine, f"Welcome to {other}\rBBS>".encode(),
                          pid=0xF0)]
        if kind == "D":
            if not self.echo:
                return []
            return [Frame(0, "D", other, mine, b"ECHO " + frame.data + b"BBS>", pid=0xF0)]
        if kind == "Y":                  # nothing waits to be acknowledged
            return [Frame(0, "Y", mine, other, struct.pack("<I", 0))]
        if kind == "d":
            return [Frame(0, "d", other, mine,
                          f"*** DISCONNECTED From Station {other}\r\0".encode())]
        return []

    @staticmethod
    def monitor_frame(src: str, dest: str, ftype: str, text: str = "",
                      data: bytes | None = None) -> Frame:
        head = f" 1:Fm {src} To {dest} <{ftype} pid=F0 Len={len(text)} >[10:00:00]\r"
        body = data if data is not None else text.encode() + b"\r"
        return Frame(0, "U", src, dest, head.encode() + body + b"\0")

    def push(self, frame: Frame) -> None:
        """Something the TNC sends on its own (a station calling us, a frame heard...)."""
        for writer in self.writers:
            writer.write(frame.encode())

    def incoming(self, call: str, mycall: str) -> None:
        self.push(Frame(0, "C", call, mycall, f"*** CONNECTED To Station {call}\r\0".encode()))

    def hang_up(self, call: str, mycall: str) -> None:
        self.push(Frame(0, "d", call, mycall,
                        f"*** DISCONNECTED From Station {call}\r\0".encode()))

    @staticmethod
    def reply_from(call: str, mycall: str, data: bytes) -> Frame:
        """Text a connected station sends us."""
        return Frame(0, "D", call, mycall, data, pid=0xF0)

    def sent(self, kind: str) -> list[Frame]:
        return [f for f in self.received if f.kind == kind]
