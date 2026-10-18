"""The suite's own D-STAR gateway (EXT Terminal Mode): the IC-705 on USB to the core's
machine, its voice routed to one reflector link and back. QDStar's LocalGateway on the
core's loop.

The radio is set to MENU > DV GW > Gateway Select = External Gateway USB, its Terminal/AP
call sign "<call> B" (not MY), then Terminal Mode; it transmits to CQCQCQ and the gateway
sends the over to the reflector linked. Typed as UR, the radio's own commands work as on
ircDDBGateway: "XLX214DL" (or "XLX214 D") links, "       U" unlinks, "       I" tells the
link, "       E" plays the over back (an echo test); the answers come as a short stream
from "<call> G" with the text in its message. Half duplex: the radio transmitting wins
over the reflector's stream.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import random
import time
from collections.abc import Callable

from ic705suite.apps.dstar import links as protocols
from ic705suite.apps.dstar.hosts import HostDirectory
from ic705suite.apps.dstar.protocol import (
    FRAMES_PER_SUPERFRAME,
    Header,
    is_end_frame,
    message_frames,
    pad,
    ur_command,
)
from ic705suite.apps.dstar.terminal import IcomTerminal

log = logging.getLogger(__name__)

NET_STREAM_TIMEOUT = 1.0    # s without a frame from the reflector: its stream has ended
FRAME = 0.02                # s: a DV frame, for the streams the gateway plays itself
ECHO_MAX_FRAMES = 60 * 50   # a minute
ECHO_DELAY = 1.0            # s
ANNOUNCE_DELAY = 0.5        # s: the radio back on receive
LINK_ANSWER = 10.0          # s for the reflector to answer a link request
DPLUS_AUTH_AGE = 6 * 3600   # s: the DPlus authentication, renewed after
COMMAND_RECENT = 3.0        # s: an over that carried a command, for the TX the CI-V reports


class ExtGateway:
    """Calls on_change() when what state() tells changes, and on_command(command,
    reflector) when the radio has given one."""

    def __init__(self, login: str, terminal_call: str, gateway_call: str, port: str = "", *,
                 on_change: Callable[[], None],
                 on_command: Callable[[str, str], None] | None = None,
                 hosts: HostDirectory | None = None) -> None:
        self.login = login.upper().strip()
        self.terminal_call = pad(terminal_call)
        self.gateway_call = pad(gateway_call)
        self.on_change = on_change
        self.on_command = on_command or (lambda command, reflector: None)
        self.hosts = hosts or HostDirectory()
        self.terminal = IcomTerminal(port, on_connected=self._radio_connected,
                                     on_header=self._radio_header, on_data=self._radio_data)
        self.conn: protocols.Link | None = None
        self.reflector = ""
        self.protocol = ""
        self.dplus_hosts: dict[str, str] = {}
        self.dplus_at = 0.0
        self.tx_id = 0              # radio -> reflector
        self.tx_seq = 0
        self.radio_tx = False
        self.rx_id: int | None = None   # reflector -> radio
        self.command: tuple[str, str] | None = None
        self.command_at = 0.0
        self.echo_frames: list[bytes] = []
        self.announce_link = ""     # the reflector whose outcome the radio waits for
        self.play_queue: collections.deque = collections.deque()
        self._tasks: set[asyncio.Task] = set()
        self._player: asyncio.Task | None = None
        self._watchdog: asyncio.TimerHandle | None = None
        self._answered = asyncio.Event()

    # ---- life ---------------------------------------------------------------------------

    def start(self) -> None:
        self.terminal.start()

    def stop(self) -> None:
        self._stop_playing(end=False)
        for task in list(self._tasks):
            task.cancel()
        if self._watchdog:
            self._watchdog.cancel()
        if self.conn:
            self.conn.close()
            self.conn = None
        self.terminal.close()

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- what the clients see -----------------------------------------------------------

    def linked_reflector(self) -> str:
        return self.reflector if self.conn and self.conn.state == "linked" else ""

    def command_recent(self) -> bool:
        return time.monotonic() - self.command_at < COMMAND_RECENT

    def state(self) -> dict:
        return {"usb": self.terminal.connected, "port": self.terminal.path,
                "reflector": self.reflector,
                "protocol": self.protocol, "link": self.conn.state if self.conn else "unlinked",
                "login": self.login, "terminal": self.terminal_call.strip(),
                "error": self.terminal.last_error}

    # ---- linking ------------------------------------------------------------------------

    async def link(self, reflector: str) -> tuple[bool, str]:
        """Link a reflector ('XLX214 D'); (True, "linked") or (False, why: "not_found",
        "auth", "refused", "no_answer": it goes on trying)."""
        reflector = pad(reflector)
        name, _ = protocols.split_reflector(reflector)
        protocol = protocols.protocol_for(name)
        address = None
        if protocol == "DPlus":
            if time.time() - self.dplus_at > DPLUS_AUTH_AGE:
                try:
                    self.dplus_hosts = await asyncio.to_thread(protocols.authenticate,
                                                               self.login)
                    self.dplus_at = time.time()
                    log.info("dstar DPlus: authenticated as %s (%d REF reflectors)",
                             self.login, len(self.dplus_hosts))
                except OSError as exc:
                    log.info("dstar DPlus: authentication failed: %s", exc)
                    self._announce_outcome(reflector, "Auth failed")
                    return False, "auth"
            address = self.dplus_hosts.get(name)
        if not address:
            address = await asyncio.to_thread(self.hosts.resolve, name, protocol)
        if not address:
            self._announce_outcome(reflector, "Not found")
            return False, "not_found"
        await self._start_link(reflector, protocol, address)
        self._answered.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._answered.wait(), LINK_ANSWER)
        if self.reflector != reflector or not self.conn:
            return False, "no_answer"
        state = self.conn.state
        if state == "linked":
            return True, "linked"
        if state == "linking":
            self._announce_outcome(reflector, "No answer")
        return False, "refused" if state == "refused" else "no_answer"

    async def _start_link(self, reflector: str, protocol: str, address: str) -> None:
        if self.conn:
            self.conn.close()
        self.reflector, self.protocol = reflector, protocol
        conn = protocols.LINKS[protocol](reflector, address, self.login, self.terminal_call,
                                         on_state=self._link_state,
                                         on_header=self._net_header, on_data=self._net_data)
        self.conn = conn
        await conn.open()
        log.info("dstar %s: linking %s (%s)", protocol, reflector.strip(), address)
        conn.link()
        self.on_change()

    def unlink(self) -> str:
        """The reflector that was linked, "" if none."""
        old = self.linked_reflector()
        if self.conn:
            self.conn.close()
            self.conn = None
        self.reflector = self.protocol = ""
        self.on_change()
        return old

    def _link_state(self, state: str) -> None:
        if state in ("linked", "refused"):
            self._answered.set()
            self._announce_outcome(self.reflector, "Linked to" if state == "linked"
                                   else "Refused")
        self.on_change()

    # ---- radio -> reflector -------------------------------------------------------------

    def _radio_connected(self, ok: bool) -> None:
        if not ok:
            self.radio_tx = False
            self._stop_playing(end=False)
        self.on_change()

    def _radio_header(self, header: Header) -> None:
        self.radio_tx = True
        self._end_net_stream()              # half duplex: the radio first
        self._stop_playing()
        log.info("dstar USB: TX %s", header.describe())
        self.command = ur_command(header.your)
        if self.command:
            # For the gateway, not an over for the reflector
            self.command_at = time.monotonic()
            self.echo_frames = []
            self.tx_id = 0
            return
        if self.conn and self.conn.state == "linked":
            self.tx_id = random.randint(1, 0xFFFF)
            self.tx_seq = 0
            self.conn.send_header(self.tx_id, header.with_(your=pad("CQCQCQ")))

    def _radio_data(self, frame: bytes, end: bool) -> None:
        if self.command:
            if self.command[0] == "echo" and not end and \
                    len(self.echo_frames) < ECHO_MAX_FRAMES and not is_end_frame(frame):
                self.echo_frames.append(bytes(frame))
            if end:
                self.radio_tx = False
                command, self.command = self.command, None
                self._run_command(*command)
            return
        if self.conn and self.conn.state == "linked" and self.tx_id:
            if end:
                self.conn.send_data(self.tx_id, self.tx_seq, b"", end=True)
                self.tx_id = 0
            else:
                self.conn.send_data(self.tx_id, self.tx_seq, frame)
                self.tx_seq = (self.tx_seq + 1) % FRAMES_PER_SUPERFRAME
        if end:
            self.radio_tx = False

    # ---- reflector -> radio -------------------------------------------------------------

    def _net_header(self, stream_id: int, header: Header) -> None:
        if self._playing() or self.radio_tx:
            return                  # the gateway talks to the radio, or the radio transmits
        if self.rx_id is not None:
            return                  # its header repeated, or another stream on top
        self.rx_id = stream_id
        log.info("dstar RX %s: %s", self.reflector.strip(), header.describe())
        self.terminal.write_header(Header(0, 0, 0, rpt2=self.terminal_call,
                                          rpt1=self.gateway_call, your=pad("CQCQCQ"),
                                          my=header.my, my2=header.my2))
        self._arm_watchdog()

    def _net_data(self, stream_id: int, seq: int, frame: bytes, end: bool) -> None:
        if stream_id != self.rx_id:
            return
        if end:
            self._end_net_stream()
            return
        self._arm_watchdog()
        self.terminal.write_data(frame)

    def _arm_watchdog(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
        self._watchdog = asyncio.get_running_loop().call_later(NET_STREAM_TIMEOUT,
                                                               self._net_stream_lost)

    def _net_stream_lost(self) -> None:
        if self.rx_id is not None:
            log.info("dstar RX: the reflector's stream was cut, closed")
            self._end_net_stream()

    def _end_net_stream(self) -> None:
        if self.rx_id is not None:
            self.terminal.write_data(b"", end=True)
            self.rx_id = None
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None

    # ---- the radio's commands -----------------------------------------------------------

    def _run_command(self, command: str, reflector: str) -> None:
        log.info("dstar USB: command %s %s", command, reflector.strip())
        self.on_command(command, reflector)
        if command == "link":
            if pad(reflector) == self.linked_reflector():
                self._announce(f"Linked to {reflector.strip()}")
                return
            self.announce_link = pad(reflector)
            self._spawn(self.link(reflector))
        elif command == "unlink":
            self._announce("Unlinked" if self.unlink() else "Not linked")
        elif command == "info":
            linked = self.linked_reflector()
            self._announce(f"Linked to {linked.strip()}" if linked else "Not linked")
        elif command == "echo":
            frames, self.echo_frames = self.echo_frames, []
            if frames:
                self._spawn(self._play(frames, "ECHO", ECHO_DELAY))

    def _announce_outcome(self, reflector: str, text: str) -> None:
        """How the link the radio asked for ended (those asked from a client are not
        told)."""
        if self.announce_link and pad(reflector) == self.announce_link:
            self.announce_link = ""
            self._announce(f"{text} {reflector.strip()}")

    def _announce(self, text: str) -> None:
        self._spawn(self._play(message_frames(text), "INFO", ANNOUNCE_DELAY))

    # ---- the streams the gateway plays --------------------------------------------------

    def _playing(self) -> bool:
        return self._player is not None and not self._player.done()

    async def _play(self, frames: list[bytes], suffix: str, delay: float) -> None:
        """A stream of our own to the radio, from the gateway's call sign, paced like live
        voice. UR CQCQCQ like the reflector's streams: to its own call sign the IC-705
        buffers the stream without playing it, then refuses every frame (and stays busy)."""
        await asyncio.sleep(delay)
        if self.radio_tx or not self.terminal.connected:
            return
        self._end_net_stream()
        self._stop_playing()
        self.terminal.write_header(Header(0, 0, 0, rpt2=self.terminal_call,
                                          rpt1=self.gateway_call, your=pad("CQCQCQ"),
                                          my=self.gateway_call, my2=pad(suffix, 4)))
        self.play_queue.extend(frames)
        self._player = asyncio.current_task()
        try:
            start = time.monotonic()
            n = 0
            while self.play_queue:
                self.terminal.write_data(self.play_queue.popleft())
                n += 1
                await asyncio.sleep(max(0.0, start + n * FRAME - time.monotonic()))
            self.terminal.write_data(b"", end=True)
        finally:
            self._player = None

    def _stop_playing(self, end: bool = True) -> None:
        if self._playing():
            self._player.cancel()
            self._player = None
            self.play_queue.clear()
            if end:
                self.terminal.write_data(b"", end=True)
