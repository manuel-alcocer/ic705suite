"""The suite's packet BBS: personal messages and bulletins, with the commands of the BBSes
on the air (FBB, BPQ): L, LM, LB, LL, R, RM, S, SP, SB, K, KM, N, H, B.

A session is plain text both ways and knows nothing of radios: the packet service feeds it
what a station sends and passes on what it answers, over the air or, for the sysop, straight
from the packet terminal. What it says stays in English, as BBSes do on the air.

Messages and users live in the suite's database (tables bbs_messages and bbs_users).
"""

from __future__ import annotations

import datetime as dt
import re
import time
from collections.abc import Callable

import sqlalchemy as sa

from ic705suite import __version__, db

metadata = sa.MetaData()

messages_table = sa.Table(
    "bbs_messages", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("kind", sa.String(1), nullable=False),          # P personal, B bulletin
    sa.Column("to_call", sa.String(16), nullable=False),      # a callsign, or a bulletin area
    sa.Column("at", sa.String(64), nullable=False, default=""),   # @ BBS or distribution
    sa.Column("from_call", sa.String(16), nullable=False),
    sa.Column("title", sa.String(80), nullable=False, default=""),
    sa.Column("body", sa.Text, nullable=False, default=""),
    sa.Column("bid", sa.String(32), nullable=False, default=""),
    sa.Column("created", sa.Float, nullable=False),
    sa.Column("status", sa.String(1), nullable=False, default="N"),  # N new, Y read, K killed
)

users_table = sa.Table(
    "bbs_users", metadata,
    sa.Column("call", sa.String(16), primary_key=True),
    sa.Column("name", sa.String(40), nullable=False, default=""),
    sa.Column("first_seen", sa.Float, nullable=False),
    sa.Column("last_seen", sa.Float, nullable=False),
    sa.Column("last_listed", sa.Integer, nullable=False, default=0),
    sa.Column("connects", sa.Integer, nullable=False, default=0),
)

TITLE_MAX = 80
BODY_MAX = 99_999
END_OF_MESSAGE = re.compile(r"^\s*/ex\s*$", re.IGNORECASE)
CTRL_Z = "\x1a"
# A callsign has a digit and a letter (ALL, QST... are bulletin areas).
CALL = re.compile(r"^(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{3,6}(-\d{1,2})?$")
AREA = re.compile(r"^[A-Z0-9]{1,6}$")

HELP = """\
L         list the messages since your last L      LL n    the last n messages
LM        list your messages                       LB      list the bulletins
L< CALL   messages from CALL                       L> CALL messages to CALL
R n       read message n (R n m ... several)       RM      read your unread messages
S CALL    send a personal message (also SP CALL)   SB AREA send a bulletin (SB ALL@EU)
K n       kill message n                           KM      kill your read messages
N name    tell the BBS your name                   I       about this BBS
H         this help                                B       bye
Messages end with /EX on a line of its own (or Ctrl+Z).
"""


def base_call(call: str) -> str:
    return call.upper().split("-")[0]


def when(stamp: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(stamp, dt.UTC)


class BbsStore:
    def __init__(self, url: str | None, engine: sa.Engine | None = None) -> None:
        self.engine = engine or db.make_engine(url)
        metadata.create_all(self.engine)

    # ---- messages -----------------------------------------------------------------------------

    def add(self, kind: str, to_call: str, at: str, from_call: str, title: str, body: str,
            bbs: str) -> dict:
        with self.engine.begin() as conn:
            number = conn.execute(messages_table.insert().values(
                kind=kind, to_call=to_call, at=at, from_call=from_call, title=title,
                body=body, bid="", created=time.time(), status="N")).inserted_primary_key[0]
            bid = f"{number}_{base_call(bbs)}"
            conn.execute(messages_table.update().where(messages_table.c.id == number)
                         .values(bid=bid))
        return self.message(number)

    def message(self, number: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(messages_table)
                               .where(messages_table.c.id == number)).mappings().first()
        return dict(row) if row else None

    def messages(self, *, since: int = 0, kind: str | None = None, to_call: str | None = None,
                 from_call: str | None = None, killed: bool = False,
                 last: int | None = None) -> list[dict]:
        """Newest first."""
        m = messages_table.c
        query = sa.select(messages_table).where(m.id > since)
        if not killed:
            query = query.where(m.status != "K")
        if kind:
            query = query.where(m.kind == kind)
        if to_call:
            query = query.where(m.to_call == to_call)
        if from_call:
            query = query.where(m.from_call == from_call)
        query = query.order_by(m.id.desc())
        if last:
            query = query.limit(last)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query).mappings()]

    def set_status(self, number: int, status: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(messages_table.update().where(messages_table.c.id == number)
                         .values(status=status))

    def latest(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(sa.select(sa.func.max(messages_table.c.id))).scalar() or 0

    def count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(sa.select(sa.func.count()).select_from(messages_table)
                                .where(messages_table.c.status != "K")).scalar() or 0

    # ---- users --------------------------------------------------------------------------------

    def user(self, call: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(users_table)
                               .where(users_table.c.call == call)).mappings().first()
        return dict(row) if row else None

    def users(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(
                sa.select(users_table).order_by(users_table.c.last_seen.desc())).mappings()]

    def arrived(self, call: str) -> tuple[dict, bool]:
        """A user connects: their record (created the first time) and whether it is new."""
        now = time.time()
        user = self.user(call)
        with self.engine.begin() as conn:
            if user is None:
                conn.execute(users_table.insert().values(call=call, name="", first_seen=now,
                                                         last_seen=now, last_listed=0,
                                                         connects=1))
            else:
                conn.execute(users_table.update().where(users_table.c.call == call)
                             .values(last_seen=now, connects=user["connects"] + 1))
        return self.user(call), user is None

    def update_user(self, call: str, **values: object) -> None:
        with self.engine.begin() as conn:
            conn.execute(users_table.update().where(users_table.c.call == call)
                         .values(**values))


class BbsSession:
    """One station connected to the BBS: lines in, answers out (`send`); `hang_up` ends it."""

    def __init__(self, store: BbsStore, bbs_call: str, sysop: str, call: str,
                 send: Callable[[str], None], hang_up: Callable[[], None],
                 on_change: Callable[[], None] = lambda: None) -> None:
        self.store = store
        self.bbs_call = bbs_call.upper()
        self.sysop = base_call(sysop)
        self.call = base_call(call)            # users are by callsign, whatever their SSID
        self.send = send
        self.hang_up = hang_up
        self.on_change = on_change
        self.since = time.time()
        self.user: dict = {}
        self._buffer = ""
        self._compose: dict | None = None      # a message being written
        self.ended = False

    @property
    def is_sysop(self) -> bool:
        return self.call == self.sysop

    @property
    def prompt(self) -> str:
        return f"de {base_call(self.bbs_call)}>\r"

    def start(self) -> None:
        self.user, new = self.store.arrived(self.call)
        latest = self.store.latest()
        name = self.user["name"] or self.call
        lines = [f"[ic705suite-{__version__}-H$]",
                 f"Hello {name}. Latest message is {latest}, last listed is "
                 f"{self.user['last_listed']}"]
        mine = [m for m in self.store.messages(kind="P", to_call=self.call) if m["status"] == "N"]
        if mine:
            lines.append(f"You have {len(mine)} new message{'s' if len(mine) > 1 else ''}: "
                         "RM reads them")
        if new:
            lines.append("Welcome! Please tell me your name: N yourname   (H for help)")
        self.send("\r".join(lines) + "\r" + self.prompt)

    def feed(self, text: str) -> None:
        """What the station sent: whole lines are acted on, the rest waits for its end (a
        message being written also ends at Ctrl+Z)."""
        self._buffer += text.replace("\r\n", "\r").replace("\n", "\r")
        while not self.ended:
            stops = [self._buffer.find("\r")]
            if self._compose is not None:
                stops.append(self._buffer.find(CTRL_Z))
            stops = [stop for stop in stops if stop >= 0]
            if not stops:
                return
            cut = min(stops)
            end = self._buffer[cut] == CTRL_Z
            line, self._buffer = self._buffer[:cut], self._buffer[cut + 1:]
            if self._compose is not None:
                self._compose_line(line, end=end)
            else:
                self._command(line.strip())

    # ---- commands -----------------------------------------------------------------------------

    def _command(self, line: str) -> None:
        if not line:
            self.send(self.prompt)
            return
        words = line.split()
        name, args = words[0].upper(), words[1:]
        handler = {
            "L": self._list, "LM": self._list_mine, "LB": self._list_bulletins,
            "LL": self._list_last, "L<": self._list_from, "L>": self._list_to,
            "R": self._read, "RM": self._read_mine,
            "S": self._send, "SP": self._send, "SB": self._send,
            "K": self._kill, "KM": self._kill_mine,
            "N": self._name, "H": self._help, "?": self._help, "I": self._info,
            "B": self._bye, "BYE": self._bye, "Q": self._bye,
        }.get(name)
        if handler is None and len(name) > 1 and name[:2] in ("L<", "L>"):
            handler, args = (self._list_from if name[1] == "<" else self._list_to), \
                [name[2:], *args]
        if handler is None:
            self.send(f"Invalid command: {words[0]} (H for help)\r" + self.prompt)
            return
        if name in ("S", "SP", "SB"):
            handler(name, args)
        else:
            handler(args)

    def _visible(self, message: dict) -> bool:
        if message["kind"] == "B" or self.is_sysop:
            return True
        return self.call in (message["to_call"], message["from_call"])

    def _listing(self, found: list[dict], none: str = "No messages") -> None:
        found = [m for m in found if self._visible(m)]
        if not found:
            self.send(f"{none}\r" + self.prompt)
            return
        lines = []
        for m in found:
            at = f"@{m['at']}" if m["at"] else ""
            lines.append(f"{m['id']:<6} {when(m['created']):%d-%b} {m['kind']}{m['status']} "
                         f"{len(m['body']):>6} {m['to_call']:<7}{at:<8} {m['from_call']:<7} "
                         f"{m['title']}")
        self.send("\r".join(lines) + "\r" + self.prompt)

    def _list(self, args: list[str]) -> None:
        if args and args[0].isdigit():
            found = [m for m in self.store.messages() if m["id"] >= int(args[0])]
        else:
            found = self.store.messages(since=self.user["last_listed"])
        latest = self.store.latest()
        self.store.update_user(self.call, last_listed=latest)
        self.user["last_listed"] = latest
        self._listing(found, "No new messages")

    def _list_mine(self, _args: list[str]) -> None:
        self._listing(self.store.messages(kind="P", to_call=self.call), "No messages for you")

    def _list_bulletins(self, _args: list[str]) -> None:
        self._listing(self.store.messages(kind="B"), "No bulletins")

    def _list_last(self, args: list[str]) -> None:
        count = int(args[0]) if args and args[0].isdigit() else 10
        self._listing(self.store.messages(last=count))

    def _list_from(self, args: list[str]) -> None:
        self._listing(self.store.messages(from_call=args[0].upper()) if args and args[0]
                      else [])

    def _list_to(self, args: list[str]) -> None:
        self._listing(self.store.messages(to_call=args[0].upper()) if args and args[0]
                      else [])

    def _read(self, args: list[str]) -> None:
        numbers = [int(a) for a in args if a.isdigit()]
        if not numbers:
            self.send("Usage: R number\r" + self.prompt)
            return
        out = []
        for number in numbers:
            message = self.store.message(number)
            if message is None or message["status"] == "K" or not self._visible(message):
                out.append(f"Message {number} not found")
                continue
            out.append(self._text_of(message))
            if message["kind"] == "P" and message["to_call"] == self.call:
                self.store.set_status(number, "Y")
        self.send("\r".join(out) + "\r" + self.prompt)
        self.on_change()

    def _read_mine(self, _args: list[str]) -> None:
        unread = [m for m in self.store.messages(kind="P", to_call=self.call)
                  if m["status"] == "N"]
        if not unread:
            self.send("No unread messages\r" + self.prompt)
            return
        self._read([str(m["id"]) for m in reversed(unread)])

    @staticmethod
    def _text_of(message: dict) -> str:
        at = f"@{message['at']}" if message["at"] else ""
        kind = {"P": "Private", "B": "Bulletin"}[message["kind"]]
        body = message["body"].replace("\n", "\r").rstrip("\r")
        return (f"From: {message['from_call']}\rTo: {message['to_call']}{at}\r"
                f"Type/Status: {kind}/{message['status']}\r"
                f"Date/Time: {when(message['created']):%d-%b %H:%MZ}\r"
                f"Bid: {message['bid']}\rTitle: {message['title']}\r\r{body}\r"
                f"[End of Message #{message['id']} from {message['from_call']}]")

    def _send(self, command: str, args: list[str]) -> None:
        target = re.sub(r"\s*@\s*", "@", " ".join(args)).upper().split()
        to_call, _sep, at = target[0].partition("@") if target else ("", "", "")
        if command == "SB" or (command == "S" and to_call and not CALL.match(to_call)):
            kind = "B"
        else:
            kind = "P"
        valid = CALL.match(to_call) if kind == "P" else AREA.match(to_call)
        if not valid:
            usage = "SB AREA[@DIST]" if kind == "B" else "SP CALL[@BBS]"
            self.send(f"Usage: {usage}\r" + self.prompt)
            return
        self._compose = {"kind": kind, "to_call": base_call(to_call) if kind == "P"
                         else to_call, "at": at, "title": None, "lines": []}
        self.send("Enter Title (only):\r")

    def _compose_line(self, line: str, end: bool = False) -> None:
        compose = self._compose
        if compose["title"] is None:
            if not line.strip():
                self._compose = None
                self.send("Message cancelled (no title)\r" + self.prompt)
                return
            compose["title"] = line.strip()[:TITLE_MAX]
            self.send("Enter Message Text (end with /EX or Ctrl+Z)\r")
            if end:
                self._finish()
            return
        if END_OF_MESSAGE.match(line):
            self._finish()
            return
        compose["lines"].append(line)
        if end:
            self._finish()

    def _finish(self) -> None:
        compose, self._compose = self._compose, None
        body = "\n".join(compose["lines"])[:BODY_MAX]
        message = self.store.add(compose["kind"], compose["to_call"], compose["at"],
                                 self.call, compose["title"], body, self.bbs_call)
        self.send(f"Message: {message['id']} Bid: {message['bid']} Size: {len(body)}\r"
                  + self.prompt)
        self.on_change()

    def _kill(self, args: list[str]) -> None:
        out = []
        for number in [int(a) for a in args if a.isdigit()] or [0]:
            message = self.store.message(number)
            if message is None or message["status"] == "K":
                out.append(f"Message {number} not found" if number else "Usage: K number")
            elif self.is_sysop or self.call in (message["from_call"], message["to_call"]):
                self.store.set_status(number, "K")
                out.append(f"Message #{number} Killed")
            else:
                out.append(f"Message #{number} is not yours")
        self.send("\r".join(out) + "\r" + self.prompt)
        self.on_change()

    def _kill_mine(self, _args: list[str]) -> None:
        read = [m for m in self.store.messages(kind="P", to_call=self.call)
                if m["status"] == "Y"]
        for message in read:
            self.store.set_status(message["id"], "K")
        self.send(f"{len(read)} message{'s' if len(read) != 1 else ''} killed\r" + self.prompt)
        self.on_change()

    def _name(self, args: list[str]) -> None:
        if not args:
            self.send("Usage: N yourname\r" + self.prompt)
            return
        name = " ".join(args)[:40]
        self.store.update_user(self.call, name=name)
        self.user["name"] = name
        self.send(f"Thank you, {name}\r" + self.prompt)
        self.on_change()

    def _help(self, _args: list[str]) -> None:
        self.send(HELP.replace("\n", "\r") + self.prompt)

    def _info(self, _args: list[str]) -> None:
        self.send(f"{base_call(self.bbs_call)} BBS ({self.bbs_call}), sysop {self.sysop}: "
                  f"ic705suite {__version__}. {self.store.count()} messages.\r" + self.prompt)

    def _bye(self, _args: list[str]) -> None:
        self.ended = True
        self.send(f"73 de {base_call(self.bbs_call)}\r")
        self.hang_up()
