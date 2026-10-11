"""What the packet terminal does, whatever draws it: the terminal (apps.packet.app) and the
graphical client (gui.packet) both hold a PacketClient and only paint what it says.

As in BayCom: channel 0 is the monitor, shown on top all the time; the big window below is
the chosen channel (1 to 8, F1-F8), one session each; the line at the bottom sends what is
typed to that channel's station. Lines starting with / are commands.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ic705suite.i18n import N_, _, render
from ic705suite.station.links import Link

CHANNELS = 8
LINE_HISTORY = 200

#: Listed by /help.
HELP = N_(
    "/c CALL [v DIGI …]   connect this channel to CALL (via digipeaters)\n"
    "/d                   disconnect this channel\n"
    "/ch N                go to channel N (also F1-F8; 0 is the monitor)\n"
    "/ui DEST text        send an unconnected UI frame\n"
    "/clear               clear this channel's window (also Ctrl+L)\n"
    "/retry               reach the TNC again now\n"
    "/tnc                 the TNC: settings and what Direwolf says (/tnc restart)\n"
    "/qrg 144.800         tune the IC-705 for packet (FM-D), when it is the radio\n"
    "/quit                back to the dashboard (also Ctrl+Q)\n"
    "//text               send a line starting with / (connected, /EX and any other\n"
    "                     line that is not a command here go to the station as typed)"
)

#: How each kind of text is drawn: Rich style names, which the graphical client maps too.
STYLES = {"rx": "", "tx": "bold cyan", "sys": "bold yellow", "mon": "", "own": "cyan"}

STATE_NAMES = {"free": N_("free"), "calling": N_("calling"), "connected": N_("connected"),
               "leaving": N_("disconnecting")}
TNC_NAMES = {"off": N_("not set up"), "connecting": N_("connecting"), "up": N_("up"),
             "down": N_("down")}
DIREWOLF_NAMES = {"stopped": N_("stopped"), "starting": N_("starting"),
                  "running": N_("running"), "waiting": N_("stopped, starting again"),
                  "missing": N_("not installed"), "failed": N_("keeps stopping")}


@dataclass
class Tab:
    """One entry of the channel bar."""
    number: int
    label: str
    state: str
    unread: int
    current: bool


def piece_text(piece: dict) -> str:
    """A piece of a channel's text in the client's language (the core's own lines carry a
    msgid; what stations send is shown as it came)."""
    if piece.get("kind") == "sys":
        return "*** " + render(piece).rstrip("\n") + "\n"
    return piece.get("text", "")


def parse_connect(args: list[str]) -> tuple[str, list[str]] | None:
    """``CALL [v|via] [DIGI …]`` -> the call and the digipeaters."""
    if not args:
        return None
    call = args[0].upper()
    rest = args[1:]
    if rest and rest[0].lower() in ("v", "via"):
        rest = rest[1:]
    return call, [digi.upper() for arg in rest for digi in arg.split(",") if digi]


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else \
        f"{rest // 60:02d}:{rest % 60:02d}"


class PacketClient:
    """The state a packet screen shows and what its keys and command line do.

    The screen gives callbacks: `on_text(channel, piece)` for text arriving, `on_reload
    (channel, pieces)` when a channel's whole text is to be drawn again, `on_state()` when
    the channels or the TNC change, `on_feedback(level, text)` for a short message and
    `on_show(title, lines)` for lines to read in a window of their own.
    """

    def __init__(self, link: Link, on_text: Callable[[int, dict], None],
                 on_reload: Callable[[int, list[dict]], None], on_state: Callable[[], None],
                 on_feedback: Callable[[str, str], None],
                 on_show: Callable[[str, list[str]], None] = lambda title, lines: None) -> None:
        self.link = link
        self.on_show = on_show
        self.on_text = on_text
        self.on_reload = on_reload
        self.on_state = on_state
        self.on_feedback = on_feedback
        self.state: dict = {}
        self.channel = 1
        self.history: list[str] = []
        self.history_index: int | None = None

    # ---- the core ------------------------------------------------------------------------

    async def start(self) -> None:
        await self.link.connect()
        self.state = await self.link.call("pkt.state")
        self.on_state()
        for number in (0, self.channel):
            await self.reload(number)

    async def reload(self, number: int) -> None:
        reply = await self.link.call("pkt.history", channel=number)
        self.on_reload(number, reply.get("text", []))
        if number:
            await self.link.call("pkt.read", channel=number)

    def handle(self, event: dict) -> None:
        """An event of the core (the screen passes them all)."""
        kind = event.get("type")
        if kind == "pkt":
            self.state = event
            self.on_state()
        elif kind == "pkt.text":
            number = int(event.get("channel", 0))
            self.on_text(number, event)
            if number and number == self.channel and event.get("kind") != "tx":
                self.link_task(self.link.call("pkt.read", channel=number))

    def link_task(self, awaitable) -> None:
        """Set by the screen: how it runs a call in the background."""
        import asyncio

        asyncio.ensure_future(awaitable)

    # ---- what is shown -------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return (self.state.get("tnc") or {}).get("state", "off") != "off"

    def channel_info(self, number: int | None = None) -> dict:
        number = self.channel if number is None else number
        channels = self.state.get("channels") or []
        return channels[number] if number < len(channels) else {"number": number,
                                                                 "state": "free", "call": ""}

    def tabs(self) -> list[Tab]:
        out = []
        for number in range(CHANNELS + 1):
            info = self.channel_info(number)
            label = _("Monitor") if number == 0 else (info.get("call") or "·")
            out.append(Tab(number, label, info.get("state", "free"),
                           0 if number == 0 else info.get("unread", 0), number == self.channel))
        return out

    def status(self) -> list[tuple[str, str, str]]:
        """(label, value, style) pieces of the status line."""
        tnc = self.state.get("tnc") or {}
        pieces = [("", "PACKET", "bold")]
        if self.state.get("mycall"):
            pieces.append((_("Call"), self.state["mycall"], "bold"))
        pieces.append((_("TNC"), *self._tnc_text(tnc)))
        bbs = self.state.get("bbs") or {}
        if bbs.get("on"):
            on_air = len([s for s in bbs.get("sessions") or [] if not s["local"]])
            pieces.append(("BBS", bbs["call"] + (f" · {on_air}" if on_air else ""),
                           "bold green" if bbs.get("registered") else "bold yellow"))
        info = self.channel_info()
        if self.channel == 0:
            pieces.append((_("Ch"), f"0 {_('Monitor')}", "bold"))
        else:
            text = f"{self.channel} " + (info.get("call") or _("free"))
            if info.get("state") == "connected" and info.get("since"):
                text += f" {duration(time.time() - info['since'])}"
            elif info.get("state") not in (None, "free"):
                text += f" ({_(STATE_NAMES[info['state']])})"
            pieces.append((_("Ch"), text, "bold cyan" if info.get("state") == "connected"
                           else "bold"))
        return pieces

    @staticmethod
    def _tnc_text(tnc: dict) -> tuple[str, str]:
        """What the TNC is doing, and its style, for the status line."""
        tnc_state = tnc.get("state", "off")
        style = {"up": "bold green", "connecting": "bold yellow"}.get(tnc_state, "bold red")
        direwolf, radio = tnc.get("direwolf"), tnc.get("radio")
        if radio and radio.get("problem"):
            return "IC-705 " + _("cannot work here (/tnc)"), "bold red"
        if direwolf and tnc_state != "up":
            # Ours: what Direwolf itself is doing says more than the connection.
            return "Direwolf " + _(DIREWOLF_NAMES.get(direwolf["state"], direwolf["state"])), \
                style
        if radio:
            where = "IC-705"
        elif direwolf:
            where = f"Direwolf :{tnc.get('port', '')}"
        else:
            where = f"{tnc.get('host', '')}:{tnc.get('port', '')}" if tnc.get("host") else ""
        text = " ".join(filter(None, [where, _(TNC_NAMES[tnc_state])]))
        if radio and radio.get("keyed"):
            return text + " · TX", "bold red"
        return text, style

    def counters(self) -> list[tuple[str, object]]:
        tnc = self.state.get("tnc") or {}
        return [(_("Frames"), self.state.get("frames", 0)),
                (_("Heard"), len(self.state.get("heard") or [])),
                ("RX", tnc.get("rx", 0)), ("TX", tnc.get("tx", 0))]

    def heard(self) -> list[dict]:
        return list(self.state.get("heard") or [])

    def target(self) -> str:
        """Who the command line talks to, for its prompt."""
        info = self.channel_info()
        if self.channel and info.get("state") == "connected":
            return info.get("call", "")
        return ""

    # ---- what the keys do ----------------------------------------------------------------

    async def select(self, number: int) -> None:
        if not 0 <= number <= CHANNELS:
            return
        self.channel = number
        self.on_state()
        await self.reload(number)

    def recall(self, delta: int) -> str | None:
        """The previous (-1) or next (+1) typed line, None when nothing changes."""
        if not self.history:
            return None
        if self.history_index is None:
            if delta > 0:
                return None
            index = len(self.history) - 1
        else:
            index = self.history_index + delta
        if index >= len(self.history):
            self.history_index = None
            return ""
        self.history_index = max(0, index)
        return self.history[self.history_index]

    async def submit(self, text: str) -> str | None:
        """A typed line: a command or text for the channel's station. Returns "quit" when
        the screen should close."""
        self.history_index = None
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
            del self.history[:-LINE_HISTORY]
        if text.startswith("/") and not text.startswith("//"):
            return await self.command(text[1:])
        if not text:
            return None
        await self._call("pkt.send", channel=self.channel, text=text[1:] if
                         text.startswith("//") else text)
        return None

    async def command(self, line: str) -> str | None:
        parts = line.split()
        if not parts:
            return None
        name, args = parts[0].lower(), parts[1:]
        if name in ("c", "connect"):
            parsed = parse_connect(args)
            if parsed is None:
                self.on_feedback("warning", _("Usage: {usage}").format(
                    usage="/c CALL [v DIGI …]"))
                return None
            if self.channel == 0:
                await self._free_channel()
            await self._call("pkt.connect", channel=self.channel, call=parsed[0],
                             via=parsed[1])
        elif name in ("d", "disconnect", "bye"):
            await self._call("pkt.disconnect", channel=self.channel)
        elif name in ("ch", "channel"):
            if not args or not args[0].isdigit() or int(args[0]) > CHANNELS:
                self.on_feedback("warning", _("Usage: {usage}").format(usage="/ch 0-8"))
                return None
            await self.select(int(args[0]))
        elif name == "ui":
            if len(args) < 2:
                self.on_feedback("warning", _("Usage: {usage}").format(usage="/ui DEST text"))
                return None
            if await self._call("pkt.unproto", dest=args[0], text=" ".join(args[1:]), via=[]):
                self.on_feedback("ok", _("UI frame sent to {call}").format(call=args[0].upper()))
        elif name in ("clear", "cls"):
            await self.clear()
        elif name == "retry":
            await self._call("pkt.retry")
        elif name == "qrg":
            from ic705suite.rig.bands import parse_frequency

            try:
                frequency = parse_frequency(args[0]) if args else 0
            except ValueError:
                frequency = 0
            if not frequency:
                self.on_feedback("warning", _("Usage: {usage}").format(usage="/qrg 144.800"))
                return None
            if await self._call("pkt.qrg", frequency=frequency):
                self.on_feedback("ok", _("IC-705 on {frequency} MHz FM-D for packet").format(
                    frequency=f"{frequency / 1e6:.4f}"))
        elif name == "tnc":
            if args and args[0].lower() == "restart":
                if await self._call("pkt.tnc.restart"):
                    self.on_feedback("ok", _("Direwolf is starting again"))
            else:
                await self.show_tnc()
        elif name in ("help", "h", "?"):
            lines = [{"kind": "sys", "text": _("Commands") + "\n"}]
            lines += [{"kind": "help", "text": "  " + line + "\n"} for line in _(HELP).splitlines()]
            for piece in lines:
                self.on_text(self.channel, piece)
        elif name in ("q", "quit", "exit"):
            return "quit"
        elif self.channel and self.channel_info().get("state") == "connected":
            # Not ours: the station's (a BBS's /EX ends a message).
            await self._call("pkt.send", channel=self.channel, text="/" + line)
        else:
            self.on_feedback("error", _("Unknown command: {cmd}. /help lists them").format(
                cmd="/" + name))
        return None

    async def show_tnc(self) -> None:
        """The TNC's settings and state; with Direwolf started here, its file and output."""
        tnc = self.state.get("tnc") or {}
        lines = [_("Callsign: {call}").format(call=self.state.get("mycall", "")),
                 _("TNC: {kind} at {host}:{port}, {state}").format(
                     kind=tnc.get("kind", ""), host=tnc.get("host", ""),
                     port=tnc.get("port", ""), state=_(TNC_NAMES.get(tnc.get("state", "off"),
                                                                     "off")))]
        if tnc.get("error"):
            lines.append(_("Last problem: {error}").format(error=tnc["error"]))
        if tnc.get("ports"):
            lines.append(_("Radio ports: {ports}").format(ports=" · ".join(tnc["ports"])))
        radio = tnc.get("radio")
        if radio:
            if radio.get("problem"):
                lines.append(_("The IC-705 cannot be the radio here: {problem}").format(
                    problem=radio["problem"]))
            else:
                lines.append(_("Radio: the IC-705 over WiFi, audio at {rate} Hz; sent while "
                               "keyed: {bytes} bytes").format(rate=radio.get("rate", ""),
                                                             bytes=radio.get("tx_bytes", 0)))
            if radio.get("refused"):
                lines.append(_("Last PTT refused: {reason}").format(reason=_(radio["refused"])))
        direwolf = tnc.get("direwolf")
        if direwolf:
            lines.append(_("Direwolf: {state}").format(
                state=_(DIREWOLF_NAMES.get(direwolf["state"], direwolf["state"]))))
            if direwolf.get("error"):
                lines.append(_("It said: {error}").format(error=direwolf["error"]))
            reply = await self.link.call("pkt.tnc.log")
            lines += ["", _("Its configuration (written by the suite):")]
            lines += ["  " + line for line in reply.get("conf", "").splitlines()]
            lines += ["", _("What it said last:")]
            lines += ["  " + line for line in reply.get("lines", [])[-60:]]
        self.on_show(_("TNC"), lines)

    async def clear(self) -> None:
        await self._call("pkt.clear", channel=self.channel)
        self.on_reload(self.channel, [])

    async def connect_to(self, call: str) -> None:
        """A heard station chosen from the list: on this channel, or the first free one."""
        if self.channel == 0 or self.channel_info().get("state") != "free":
            await self._free_channel()
        await self._call("pkt.connect", channel=self.channel, call=call, via=[])

    async def _free_channel(self) -> None:
        free = next((n for n in range(1, CHANNELS + 1)
                     if self.channel_info(n).get("state", "free") == "free"), None)
        if free is not None and free != self.channel:
            await self.select(free)

    async def _call(self, method: str, **params) -> bool:
        reply = await self.link.call(method, **params)
        if reply.get("error"):
            self.on_feedback("error", render({"msgid": reply.get("msgid"),
                                              "args": reply.get("args", {}),
                                              "text": reply["error"]}))
            return False
        return True
