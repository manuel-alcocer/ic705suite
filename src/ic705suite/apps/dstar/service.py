"""D-STAR in the core: the IC-705 in DV, followed over CI-V (QDStar's radio.py and the
orchestration of its main window, on the suite's rig instead of a session of its own).

Every 700 ms the transmit state and the last DV call signs, message and status received
are read (over the network the radio does not push them reliably), every 5 s the mode and
MY / UR / R1 / R2. A new header (or the voice again after a pause) starts an over; 1.5 s
without voice ends it; the same header within 8 s, with no transmission of ours between,
is the same over resumed (the voice bit drops in reflector and WiFi gaps). Overs go to the
history with the operator's name and place from radioid.net, the message when it comes,
and the D-PRS position read after it. Replies of our own gateway or terminal are no
conversation. In Terminal Mode with the radio's internal gateway (INT) the reflector is
changed by writing the TO (UR), read back to confirm it.

Events: dstar {...state()} when it changes. Calls (dstar.*): start, stop, state,
history {limit}, reflectors, reflector.save {item, old_to}, reflector.delete {to},
change {to}, note {note}, message {text}, clear_history.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import urllib.parse
import urllib.request

from ic705suite import geo
from ic705suite.apps.dstar import civ
from ic705suite.apps.dstar.protocol import ur_command
from ic705suite.i18n import N_
from ic705suite.rig.base import RigError
from ic705suite.rig.transport import TransportError

log = logging.getLogger(__name__)

FAST_POLL = 0.7        # s: the transmit state and the last DV call signs, message, status
SLOW_POLL = 5.0        # s: mode, MY, UR / R1 / R2 (they can be changed on the radio)
CIV_ALIVE = 3.0        # s: answers within this time light the CI-V LED
RX_END_GRACE = 1.5     # s without voice: the over has ended
RX_RESUME = 8.0        # s: the same header again within this, the same over
DPRS_AFTER = 2.5       # s into an over: its D-PRS position has arrived in the slow data
DPRS_REPEAT = 300.0    # s: the same D-PRS data again is the radio's last one, not a new one
TO_CONFIRM = 3.0       # s: the TO written must read back by then
RADIOID_URL = "https://radioid.net/api/dmr/user/?callsign={}"
HISTORY = 300          # rows a client gets at most


def lookup_name(callsign: str) -> tuple[str, str] | None:
    """radioid.net: (name, place) of an operator, ("", "") when unknown, None when it could
    not be asked (blocks: a thread)."""
    request = urllib.request.Request(RADIOID_URL.format(urllib.parse.quote(callsign)),
                                     headers={"User-Agent": "ic705suite"})
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            data = json.loads(reply.read(200_000))
    except (OSError, ValueError):
        return None
    for r in data.get("results", []):
        if str(r.get("callsign", "")).upper() == callsign:
            name = " ".join(p for p in (r.get("fname") or r.get("name"), r.get("surname"))
                            if p).strip()
            return name, ", ".join(p for p in (r.get("city"), r.get("country")) if p)
    return "", ""


def base_call(call: str) -> str:
    return call.split()[0] if call and call.split() else ""


class DStarError(Exception):
    def __init__(self, msgid: str, **args) -> None:
        super().__init__(msgid.format(**args) if args else msgid)
        self.msgid, self.args_ = msgid, args


class DStarService:
    def __init__(self, station, config, hub) -> None:
        self.station = station
        self.config = config
        self.hub = hub
        self.on = False
        self._task: asyncio.Task | None = None
        self._changed = False
        self._storage = None
        self._registry = None
        self._pending: set[asyncio.Task] = set()
        self._lookups: set[str] = set()
        self.reset()

    def reset(self) -> None:
        self.civ_ok = False
        self.last_civ = 0.0
        self.my_call = self.my_note = ""
        self.ur = self.r1 = self.r2 = ""
        self.tx_message = ""
        self.mode = ""
        self.transmitting = False
        self.tx_since: float | None = None
        self.tx_entry: int | None = None
        self.rx_active = False
        self.rx_caller = ""
        self.rx_last_voice = 0.0
        self.last_header: civ.RxCalls | None = None
        self.last_message = None
        self.rx_message_sent = False
        self.last_dprs: bytes | None = None
        self.last_dprs_time = 0.0
        self.rx_entry: int | None = None
        self.rx_info: dict | None = None        # the over on air, or the last one
        self.last_rx: dict | None = None        # for resuming it
        self.history_version = 0
        self.reflector_info: dict | None = None   # the registry's entry of the TO in use

    # ---- the database (blocking: threads) ------------------------------------------------

    @property
    def storage(self):
        if self._storage is None:
            from ic705suite.apps.dstar.storage import Storage

            self._storage = Storage(self.config.database_url or None)
        return self._storage

    @property
    def registry(self):
        if self._registry is None:
            from ic705suite.apps.dstar.reflectors import Registry

            self._registry = Registry(self.config.database_url or None)
        return self._registry

    async def _db(self, method, *args, **kwargs):
        return await asyncio.to_thread(method, *args, **kwargs)

    def _background(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    # ---- life ---------------------------------------------------------------------------

    async def start(self) -> None:
        if self.on:
            return
        self.reset()
        self.on = True
        self._task = asyncio.create_task(self._run())
        self._touch()

    async def stop(self) -> None:
        if not self.on:
            return
        self.on = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None
        if self.rx_info and self.rx_info.get("live"):
            await self._rx_ended()
        if self.tx_entry is not None:
            await self._transmit(False)
        for task in list(self._pending):
            task.cancel()
        self._touch()

    def changed(self) -> bool:
        changed, self._changed = self._changed, False
        return changed

    def _touch(self) -> None:
        self._changed = True

    async def _run(self) -> None:
        next_slow = 0.0
        while True:
            try:
                if self.station.connected:
                    now = time.monotonic()
                    if now >= next_slow:
                        next_slow = now + SLOW_POLL
                        for body in (b"\x04", b"\x1f\x00", b"\x1f\x01"):
                            await self._ask(body)
                    for body in (b"\x1c\x00", b"\x20\x02\x02", b"\x20\x00\x02", b"\x20\x01\x02"):
                        await self._ask(body)
                self._set_civ(time.monotonic() - self.last_civ < CIV_ALIVE)
                if self.rx_active and time.monotonic() - self.rx_last_voice > RX_END_GRACE:
                    await self._end_rx()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - logged; the polling goes on
                log.exception("dstar: poll failed")
            await asyncio.sleep(FAST_POLL)

    async def _ask(self, body: bytes) -> None:
        """One read: its reply handled; refused or unanswered (a frequency read in Terminal
        Mode is refused, for one) only leaves the CI-V LED to tell."""
        try:
            frame = await self.station.rig.transact(body, body, timeout=0.6, retries=0)
        except (RigError, TransportError):
            return
        self.last_civ = time.monotonic()
        parsed = civ.parse_reply(frame.body)
        if parsed:
            await self._handle(*parsed)

    def _set_civ(self, ok: bool) -> None:
        if ok != self.civ_ok:
            self.civ_ok = ok
            self._touch()

    # ---- the replies --------------------------------------------------------------------

    async def _handle(self, kind: str, value) -> None:
        if kind == "my_call":
            if value != (self.my_call, self.my_note):
                self.my_call, self.my_note = value
                self._touch()
        elif kind == "tx_calls":
            if value != (self.ur, self.r1, self.r2):
                if value[0] != self.ur:
                    self._background(self._find_reflector(value[0]))
                self.ur, self.r1, self.r2 = value
                self._touch()
        elif kind == "tx_message":
            self.tx_message = value
            self._touch()
        elif kind == "mode":
            if value != self.mode:
                self.mode = value
                self._touch()
        elif kind == "tx":
            if value != self.transmitting:
                await self._transmit(value)
        elif kind == "rx_calls":
            await self._on_header(value)
        elif kind == "rx_message":
            await self._on_message(value)
        elif kind == "rx_status":
            if value.voice:
                self.rx_last_voice = time.monotonic()
                if not self.rx_active and self.last_header is not None and \
                        self.last_header.caller:
                    await self._start_rx(self.last_header)     # the same header, a new over
        elif kind == "dprs" and value is not None:
            await self._on_dprs(value)

    async def _on_header(self, header: civ.RxCalls | None) -> None:
        if header is None:
            return
        first = self.last_header is None
        changed = header != self.last_header
        self.last_header = header
        if first:
            return                     # whatever was heard before we started
        if changed and header.caller:
            await self._start_rx(header)

    async def _start_rx(self, header: civ.RxCalls) -> None:
        self.rx_message_sent = False
        if self.rx_active:
            await self._end_rx()
        self.rx_active = True
        self.rx_caller = header.caller
        self.rx_last_voice = time.monotonic()
        await self._rx_started(header)
        self._background(self._read_dprs_later())

    async def _end_rx(self) -> None:
        self.rx_active = False
        await self._rx_ended()
        await self._ask(b"\x20\x03\x02")

    async def _read_dprs_later(self) -> None:
        await asyncio.sleep(DPRS_AFTER)
        if self.on:
            await self._ask(b"\x20\x03\x02")

    # ---- overs: the history -------------------------------------------------------------

    def _is_system_call(self, call: str) -> bool:
        """Our own gateway, repeater or terminal answering after a TX: no conversation (in
        EXT the radio reports no R1/R2: any call sign of our own base counts)."""
        if not call or call in (self.r1, self.r2):
            return True
        mine = base_call(self.my_call or self.config.callsign)
        return bool(mine) and base_call(call) == mine

    @property
    def reflector(self) -> str:
        """The reflector in use: the TO (INT)."""
        return self.ur

    async def _rx_started(self, calls: civ.RxCalls) -> None:
        if self._is_system_call(calls.caller):
            self._say(N_("D-STAR: system reply {caller} to {called}"), caller=calls.caller,
                      called=calls.called)
            return
        header = (calls.caller, calls.called, calls.rpt1, calls.rpt2)
        last, self.last_rx = self.last_rx, None
        if last and last["header"] == header and \
                time.time() - last["info"]["ended"] < RX_RESUME and \
                (self.tx_since or 0) < last["info"]["ended"]:
            # The same over again after a gap: its entry and start go on
            self.rx_entry, self.rx_info = last["entry"], last["info"]
            self.rx_info["live"] = True
            self.rx_info.pop("ended", None)
            await self._db(self.storage.update_entry, self.rx_entry, ended=None)
            self._history_changed()
            return
        base = base_call(calls.caller)
        log.info("dstar: RX %s /%s UR=%s R1=%s R2=%s", calls.caller, calls.note, calls.called,
                 calls.rpt1, calls.rpt2)
        self.rx_entry = await self._db(self.storage.start_entry, "RX", base, calls.note,
                                       self.reflector, calls.rpt1, calls.rpt2)
        self.rx_info = {"callsign": base, "suffix": calls.note, "called": calls.called,
                        "rpt1": calls.rpt1, "rpt2": calls.rpt2, "started": time.time(),
                        "live": True, "header": header, "name": "", "location": "",
                        "message": "", "reflector": self.reflector}
        known = await self._db(self.storage.last_position, base)
        if known:
            self._place(self.rx_info, known)
        self._lookup(base)
        self._history_changed()

    async def _rx_ended(self) -> None:
        if self.rx_info and self.rx_info.get("live"):
            self.rx_info["live"] = False
            self.rx_info["ended"] = time.time()
            await self._db(self.storage.update_entry, self.rx_entry,
                           ended=self.rx_info["ended"])
            self.last_rx = {"header": self.rx_info["header"], "entry": self.rx_entry,
                            "info": self.rx_info}
            self._history_changed()

    async def _on_message(self, value) -> None:
        if value is None:
            return
        message, caller, _note = value
        same = base_call(caller) == base_call(self.rx_caller)
        if self.rx_active and not self.rx_message_sent and same:
            self.rx_message_sent = True        # once per over, even if equal to the last
        elif value == self.last_message:
            return
        first = self.last_message is None
        self.last_message = value
        if first and not same:
            return                             # the radio's last one, from before
        base = base_call(caller)
        if not base or not message or self._is_system_call(caller):
            return
        if self.rx_info and self.rx_info["callsign"] == base:
            if self.rx_info.get("message") == message:
                return
            self.rx_info["message"] = message
        # It may come after the over ended: the station's latest entry gets it
        await self._db(self.storage.fill_recent, base, "message", message, 120)
        self._history_changed()

    async def _transmit(self, on: bool) -> None:
        self.transmitting = on
        base = base_call(self.my_call or self.config.callsign) or "?"
        if on:
            self.tx_since = time.time()
            if ur_command(self.ur or ""):
                self.tx_entry = None           # a command to a gateway, not a conversation
            else:
                self.tx_entry = await self._db(self.storage.start_entry, "TX", base,
                                               self.my_note, self.reflector, self.r1, self.r2)
                self._lookup(base)
        elif self.tx_entry is not None:
            own = await self._db(self.storage.cached_name, base) or {}
            await self._db(self.storage.update_entry, self.tx_entry, ended=time.time(),
                           name=own.get("name") or "", location=own.get("location") or "")
            self.tx_entry = None
        self._history_changed()

    async def _find_reflector(self, to: str) -> None:
        found = await self._db(self.registry.get, to) if to.startswith("/") else None
        if to == self.ur or self.reflector_info is None or found != self.reflector_info:
            self.reflector_info = found
            self._touch()

    def _history_changed(self) -> None:
        self.history_version += 1
        self._touch()

    # ---- names and places ---------------------------------------------------------------

    def _lookup(self, callsign: str) -> None:
        if callsign and callsign not in self._lookups:
            self._lookups.add(callsign)
            self._background(self._resolve(callsign))

    async def _resolve(self, callsign: str) -> None:
        try:
            cached = await self._db(self.storage.cached_name, callsign)
            if cached:
                found = (cached["name"] or "", cached["location"] or "")
            else:
                found = await asyncio.to_thread(lookup_name, callsign)
                if found is None:
                    return
                await self._db(self.storage.store_name, callsign, *found)
            name, location = found
            changed = await self._db(self.storage.fill_recent, callsign, "name", name,
                                     10 * 365 * 86400)
            changed += await self._db(self.storage.fill_recent, callsign, "location",
                                      location, 10 * 365 * 86400)
            if self.rx_info and self.rx_info["callsign"] == callsign:
                self.rx_info["name"] = self.rx_info.get("name") or name
                self.rx_info["location"] = self.rx_info.get("location") or location
                changed = True
            if changed:
                self._history_changed()
        finally:
            self._lookups.discard(callsign)

    def _home(self) -> tuple[float, float] | None:
        return geo.locator_to_latlon(self.config.locator) if self.config.locator else None

    def _place(self, info: dict, latlon: tuple[float, float]) -> None:
        """Where the station is, and (with [station] locator) how far and which way."""
        info["lat"], info["lon"] = latlon
        info["pos"] = list(latlon)
        home = self._home()
        if home:
            info["distance"] = round(geo.distance_km(home, latlon), 1)
            info["bearing"] = round(geo.bearing_deg(home, latlon))

    async def _on_dprs(self, pos) -> None:
        """A D-PRS report: new data, or from the station on air (the radio keeps the last one
        it heard, and a read may give an earlier station's)."""
        raw = json.dumps([pos.callsign, pos.kind, pos.lat, pos.lon]).encode()
        caller = base_call(self.rx_caller)
        own = base_call(pos.callsign.split("-")[0]) == caller
        repeated = raw == self.last_dprs and time.monotonic() - self.last_dprs_time < DPRS_REPEAT
        if repeated and not own:
            return
        self.last_dprs, self.last_dprs_time = raw, time.monotonic()
        on_air = self.rx_info and (self.rx_info.get("live") or
                                   time.time() - self.rx_info.get("ended", 0) < 10)
        via = self.rx_info["callsign"] if on_air and not self._is_system_call(
            self.rx_info["callsign"]) else ""
        await self._db(self.storage.add_dprs, pos, via, self.reflector)
        if pos.kind == "position" and own and self.rx_info and self.rx_entry is not None:
            self._place(self.rx_info, (pos.lat, pos.lon))
            await self._db(self.storage.update_entry, self.rx_entry, lat=pos.lat, lon=pos.lon)
        self._history_changed()

    # ---- what the operator does -----------------------------------------------------------

    def radio_mode(self) -> str | None:
        """The Terminal Mode the radio is in, by its R1: INT fills it with the terminal call
        sign (<call> Z); EXT, and plain DV, leave it empty."""
        call = base_call(self.my_call or self.config.callsign)
        if self.r1 and self.r1.split()[:1] == [call] and self.r1.endswith("Z"):
            return "int"
        if not self.r1 or self.r1 == f"{call:<7}B":
            return "ext"
        return None

    async def change_to(self, to: str) -> dict:
        """INT: write the TO (UR), R1 / R2 as the radio has them, and read it back."""
        from ic705suite.apps.dstar.reflectors import normalize_to

        to = normalize_to(to, "int")
        if not self.r1:
            raise DStarError(N_("R1 / R2 have not been read from the radio yet (is it in DV, "
                                "in Terminal Mode?)"))
        await self.station.rig.transact(b"\x1f\x01" + civ.text8(to) + civ.text8(self.r1)
                                        + civ.text8(self.r2))
        deadline = time.monotonic() + TO_CONFIRM
        while time.monotonic() < deadline:
            await asyncio.sleep(0.4)
            await self._ask(b"\x1f\x01")
            if self.ur == to:
                self._say(N_("D-STAR: TO {to}; press the PTT briefly to register on the "
                             "reflector"), to=to)
                return {"to": to}
        raise DStarError(N_("The radio did not confirm the TO {to}"), to=to)

    async def set_note(self, note: str) -> dict:
        call = self.my_call
        if not call:
            raise DStarError(N_("The MY call sign has not been read from the radio yet"))
        note = note.strip()[:4]
        await self.station.rig.transact(b"\x1f\x00" + civ.text8(call)
                                        + note.ljust(4).encode("ascii", "replace"))
        await asyncio.sleep(0.4)
        await self._ask(b"\x1f\x00")
        if self.my_note != note:
            raise DStarError(N_("The radio did not confirm the note /{note}"), note=note)
        return {"note": note}

    async def set_message(self, text: str) -> dict:
        text = text[:20]
        await self.station.rig.transact(b"\x1f\x02" + text.ljust(20).encode("ascii", "replace"))
        await asyncio.sleep(0.4)
        await self._ask(b"\x1f\x02")
        return {"message": self.tx_message}

    def _say(self, msgid: str, **args) -> None:
        self.hub.info(msgid, **args)

    # ---- for the clients ----------------------------------------------------------------

    def state(self) -> dict:
        info = None
        if self.rx_info:
            info = {k: v for k, v in self.rx_info.items() if k != "header"}
        return {"on": self.on, "civ": self.civ_ok, "connected": self.station.connected,
                "my_call": self.my_call, "my_note": self.my_note, "ur": self.ur, "r1": self.r1,
                "r2": self.r2, "tx_message": self.tx_message, "mode": self.mode,
                "radio_mode": self.radio_mode() if self.my_call else None,
                "transmitting": self.transmitting, "tx_since": self.tx_since,
                "rx": info, "rx_active": self.rx_active, "server": self.config.dstar_server,
                "reflector": self.reflector_info, "history_version": self.history_version,
                "locator": self.config.locator, "callsign": self.config.callsign}

    async def call(self, method: str, params: dict) -> dict:
        try:
            if method == "start":
                await self.start()
            elif method == "stop":
                await self.stop()
            elif method == "state":
                return self.state()
            elif method == "history":
                rows = await self._db(self.storage.recent,
                                      min(HISTORY, int(params.get("limit", HISTORY))))
                return {"history": rows}
            elif method == "reflectors":
                return {"reflectors": await self._db(self.registry.list)}
            elif method == "reflector.save":
                await self._db(self.registry.save, params["item"], params.get("old_to"))
                return {"reflectors": await self._db(self.registry.list)}
            elif method == "reflector.delete":
                await self._db(self.registry.delete, str(params["to"]))
                return {"reflectors": await self._db(self.registry.list)}
            elif method == "change":
                return await self.change_to(str(params["to"]))
            elif method == "note":
                return await self.set_note(str(params.get("note", "")))
            elif method == "message":
                return await self.set_message(str(params.get("text", "")))
            elif method == "clear_history":
                await self._db(self.storage.clear_history)
                self._history_changed()
            else:
                return {"error": f"unknown method dstar.{method}"}
        except DStarError as exc:
            return {"error": str(exc), "msgid": exc.msgid,
                    "args": {k: str(v) for k, v in exc.args_.items()}}
        except (RigError, TransportError, ValueError, KeyError) as exc:
            return {"error": str(exc)}
        return {"ok": True}
