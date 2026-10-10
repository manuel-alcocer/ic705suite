"""What the interface expects from any way of reaching the node.

A link is a TCP connection to the machine with the radio (the Raspberry Pi
running LinBPQ). The radio itself, and whatever modem or TNC drives it, stay
on that machine: changing the IC-705 for the TM-241E, or LinBPQ for Direwolf,
does not change anything here as long as the node offers the same interface.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Config


@dataclass(slots=True)
class LinkEvent:
    """Something the link wants the interface to show.

    Kinds:
        link_up, link_down: the TCP connection to the node.
        session_up, session_down: a connected AX.25 session with ``call``.
        rx: text received from ``call`` (or from the node console).
        monitor: a monitored frame, ``own`` when we sent it.
        info, error: a line for the operator.
    """

    kind: str
    text: str = ""
    call: str = ""
    own: bool = False
    extra: dict[str, object] = field(default_factory=dict)


EventSink = Callable[[LinkEvent], None]


def decode_text(data: bytes) -> str:
    """Packet text arrives in whatever the other station uses: try UTF-8 first."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


class Link(ABC):
    """A connection to the node, driven by the interface."""

    #: Short name shown on the status line.
    kind: str = ""
    #: Whether this link can show monitored frames and heard stations.
    supports_monitor: bool = False

    def __init__(self, config: Config, sink: EventSink) -> None:
        self.config = config
        self.sink = sink
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.rx_bytes = 0
        self.tx_bytes = 0

    @property
    def is_open(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def run(self) -> None:
        """Open the link and pump it until it closes. Never raises."""
        host, port = self.config.link.host, self.config.link.port
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
        except (OSError, TimeoutError) as error:
            self.sink(LinkEvent("link_down", text=str(error) or type(error).__name__,
                                extra={"failed_open": True}))
            return
        self.sink(LinkEvent("link_up"))
        reason = ""
        try:
            await self._on_open()
            await self._pump(self._reader)
        except asyncio.CancelledError:
            reason = ""
        except (OSError, asyncio.IncompleteReadError, ConnectionError) as error:
            reason = str(error) or type(error).__name__
        finally:
            await self.close()
            self.sink(LinkEvent("link_down", text=reason))

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionError):
            pass

    def _write(self, data: bytes) -> None:
        if not self.is_open:
            raise ConnectionError("link is not open")
        assert self._writer is not None
        self._writer.write(data)
        self.tx_bytes += len(data)

    @abstractmethod
    async def _on_open(self) -> None:
        """Say hello to the node once the TCP connection is up."""

    @abstractmethod
    async def _pump(self, reader: asyncio.StreamReader) -> None:
        """Read from the node until the connection ends."""

    @abstractmethod
    def send_line(self, text: str, call: str = "", radio_port: int = 1) -> None:
        """Send one line of text to the connected station (or the node)."""

    @abstractmethod
    def connect(self, call: str, via: list[str], radio_port: int) -> None:
        """Open an AX.25 session with ``call``."""

    @abstractmethod
    def disconnect(self, call: str, radio_port: int = 1) -> None:
        """Close the AX.25 session with ``call``."""

    def unproto(self, dest: str, text: str, radio_port: int, via: list[str]) -> None:
        """Send an unconnected UI frame. Only some links can."""
        raise NotImplementedError

    def set_monitor(self, enabled: bool) -> None:
        """Start or stop receiving monitored frames. Only some links can."""
        raise NotImplementedError
