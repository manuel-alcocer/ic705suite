"""Telnet to the node console.

The fallback when AGWPE is not enabled, and the way to use the node's own
commands (``C``, ``NODES``, ``MHEARD``, ``BBS``…). Everything typed goes to
the node as is; connecting to a station is the node's ``C`` command.
"""

from __future__ import annotations

import asyncio
import re

from .base import Link, LinkEvent, decode_text

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240

#: How LinBPQ reports the outcome of a ``C`` command, e.g.
#: ``EA7WM-7} Connected to EA1ABC-1``.
CONNECTED = re.compile(r"\}\s*Connected to (\S+)", re.IGNORECASE)
DISCONNECTED = re.compile(r"\}\s*(?:Disconnected|Failure with|Busy from)", re.IGNORECASE)


def strip_telnet(data: bytes) -> tuple[bytes, bytes]:
    """Remove telnet negotiation; return the text and the refusals to send back.

    The node does not need any option, so every request is declined.
    """
    out = bytearray()
    replies = bytearray()
    index = 0
    while index < len(data):
        byte = data[index]
        if byte != IAC:
            out.append(byte)
            index += 1
            continue
        command = data[index + 1] if index + 1 < len(data) else None
        if command == IAC:
            out.append(IAC)
            index += 2
        elif command in (DO, DONT, WILL, WONT):
            option = data[index + 2] if index + 2 < len(data) else 0
            if command == DO:
                replies += bytes([IAC, WONT, option])
            elif command == WILL:
                replies += bytes([IAC, DONT, option])
            index += 3
        elif command == SB:
            end = data.find(bytes([IAC, SE]), index)
            index = len(data) if end < 0 else end + 2
        else:
            index += 2
    return bytes(out), bytes(replies)


class TelnetLink(Link):
    kind = "Telnet"
    supports_monitor = False

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._login_step = 0
        self._pending = ""
        self.session_call = ""
        #: Typed once the node has let us in, e.g. ["BBS"].
        self.after_login: list[str] = []

    async def _on_open(self) -> None:
        return None

    async def _pump(self, reader: asyncio.StreamReader) -> None:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                return
            self.rx_bytes += len(chunk)
            data, replies = strip_telnet(chunk)
            if replies:
                self._write(replies)
            if data:
                self._handle_text(decode_text(data))

    def _handle_text(self, text: str) -> None:
        self._answer_login(text)
        self.sink(LinkEvent("rx", text))
        # Watch complete lines for the node reporting a session.
        self._pending += text
        *lines, self._pending = re.split(r"\r\n|\r|\n", self._pending)
        for line in lines:
            match = CONNECTED.search(line)
            if match:
                self.session_call = match.group(1).upper()
                self.sink(LinkEvent("session_up", call=self.session_call))
            elif self.session_call and DISCONNECTED.search(line):
                self.sink(LinkEvent("session_down", call=self.session_call))
                self.session_call = ""

    def _answer_login(self, text: str) -> None:
        """Type the user and the password when the node asks for them."""
        link = self.config.link
        lowered = text.lower()
        if self._login_step == 0 and "user" in lowered and ":" in lowered and link.user:
            self._write((link.user + "\r").encode())
            self._login_step = 1
        elif self._login_step == 1 and "password" in lowered:
            self._write((link.password + "\r").encode())
            self._login_step = 2
        elif self._login_step == 2:
            # The first thing the node says after the password is its welcome.
            self._login_step = 3
            for command in self.after_login:
                self.send_line(command)

    def send_line(self, text: str, call: str = "", radio_port: int = 1) -> None:
        self._write((text + "\r").encode("utf-8", "replace"))

    def connect(self, call: str, via: list[str], radio_port: int) -> None:
        command = f"C {radio_port} {call}"
        if via:
            command += " V " + " ".join(via)
        self.send_line(command)

    def disconnect(self, call: str, radio_port: int = 1) -> None:
        # Whatever is typed goes to the far station once connected, so no
        # command can reach the node. Dropping the telnet connection makes
        # the node close the session it relays; the interface opens a new one.
        if self._writer is not None:
            self._writer.close()
