"""The BBS as stations see it: what they type, what it answers, what it keeps."""

from __future__ import annotations

from ic705suite.apps.packet.bbs import BbsSession, BbsStore


class Station:
    """A station connected to the BBS: types lines, reads the answers."""

    def __init__(self, store: BbsStore, call: str) -> None:
        self.out: list[str] = []
        self.gone = False
        self.session = BbsSession(store, "EA7KLX-1", "EA7KLX", call, self.out.append,
                                  self._hang_up)
        self.session.start()

    def _hang_up(self) -> None:
        self.gone = True

    def type(self, *lines: str) -> str:
        """Answers to these lines, as one text with line breaks."""
        self.out.clear()
        for line in lines:
            self.session.feed(line + "\r")
        return "".join(self.out).replace("\r", "\n")

    def welcome(self) -> str:
        return "".join(self.out).replace("\r", "\n")


def store(tmp_path) -> BbsStore:
    return BbsStore(f"sqlite:///{tmp_path / 'bbs.db'}")


def test_a_new_user_is_welcomed_and_named(tmp_path):
    bbs = store(tmp_path)
    station = Station(bbs, "EA1ABC-7")
    welcome = station.welcome()
    assert welcome.startswith("[ic705suite-")
    assert "Hello EA1ABC. Latest message is 0, last listed is 0" in welcome
    assert "Please tell me your name: N yourname" in welcome
    assert welcome.endswith("de EA7KLX>\n")
    assert station.type("N Juan") == "Thank you, Juan\nde EA7KLX>\n"
    again = Station(bbs, "EA1ABC")                     # users are by call, any SSID
    assert "Hello Juan." in again.welcome() and "your name" not in again.welcome()
    assert bbs.user("EA1ABC")["connects"] == 2


def test_personal_messages(tmp_path):
    bbs = store(tmp_path)
    juan = Station(bbs, "EA1ABC")
    assert juan.type("SP EA2XYZ @ EA2BBS") == "Enter Title (only):\n"
    assert juan.type("Meeting") == "Enter Message Text (end with /EX or Ctrl+Z)\n"
    answer = juan.type("See you at 20h", "on 144.850", "/ex")
    assert answer == "Message: 1 Bid: 1_EA7KLX Size: 25\nde EA7KLX>\n"
    message = bbs.message(1)
    assert (message["kind"], message["to_call"], message["at"], message["from_call"]) == \
        ("P", "EA2XYZ", "EA2BBS", "EA1ABC")
    assert message["body"] == "See you at 20h\non 144.850"

    pepe = Station(bbs, "EA2XYZ-1")
    assert "You have 1 new message: RM reads them" in pepe.welcome()
    listing = pepe.type("LM")
    assert "1      " in listing and "PN" in listing and "EA2XYZ @EA2BBS" in listing.replace(
        "EA2XYZ @", "EA2XYZ @") and "Meeting" in listing
    text = pepe.type("RM")
    assert "From: EA1ABC\nTo: EA2XYZ@EA2BBS\nType/Status: Private/N" in text
    assert "Title: Meeting\n\nSee you at 20h\non 144.850\n[End of Message #1 from EA1ABC]" in text
    assert bbs.message(1)["status"] == "Y"
    assert pepe.type("RM") == "No unread messages\nde EA7KLX>\n"

    # A third station neither lists nor reads it; the sysop does.
    other = Station(bbs, "EA3AAA")
    assert other.type("R 1") == "Message 1 not found\nde EA7KLX>\n"
    assert other.type("L") == "No new messages\nde EA7KLX>\n"
    sysop = Station(bbs, "EA7KLX-4")
    assert "Meeting" in sysop.type("R 1")
    assert other.type("K 1") == "Message #1 is not yours\nde EA7KLX>\n"
    assert pepe.type("KM") == "1 message killed\nde EA7KLX>\n"
    assert bbs.message(1)["status"] == "K"
    assert sysop.type("R 1") == "Message 1 not found\nde EA7KLX>\n"


def test_bulletins_and_listing_since_last_time(tmp_path):
    bbs = store(tmp_path)
    juan = Station(bbs, "EA1ABC")
    juan.type("SB ALL@EU", "Net tonight", "21h on 144.650\x1a")
    assert bbs.message(1)["kind"] == "B" and bbs.message(1)["at"] == "EU"
    juan.type("S QST", "Second", "text", "/EX")      # not a callsign: a bulletin
    assert bbs.message(2)["kind"] == "B" and bbs.message(2)["to_call"] == "QST"

    pepe = Station(bbs, "EA2XYZ")
    first = pepe.type("L")
    assert first.index("2 ") < first.index("1 ")         # newest first
    assert "BN" in first and "ALL    @EU" in first and "Net tonight" in first
    assert pepe.type("L") == "No new messages\nde EA7KLX>\n"
    assert "Second" in pepe.type("LB") and "Second" in pepe.type("LL 1")
    assert "Net tonight" not in pepe.type("LL 1")
    assert "Second" in pepe.type("L< EA1ABC") and "Second" in pepe.type("L> QST")
    assert "Bulletin/N" in pepe.type("R 2")
    assert bbs.message(2)["status"] == "N"              # bulletins stay new for everyone


def test_what_is_refused_and_bye(tmp_path):
    bbs = store(tmp_path)
    juan = Station(bbs, "EA1ABC")
    assert juan.type("XYZ") == "Invalid command: XYZ (H for help)\nde EA7KLX>\n"
    assert juan.type("SP") == "Usage: SP CALL[@BBS]\nde EA7KLX>\n"
    assert juan.type("SB THIS-IS-NOT") == "Usage: SB AREA[@DIST]\nde EA7KLX>\n"
    assert juan.type("SP EA2XYZ", "") == "Enter Title (only):\nMessage cancelled (no title)\n" \
        "de EA7KLX>\n"
    assert juan.type("R") == "Usage: R number\nde EA7KLX>\n"
    assert "LM        list your messages" in juan.type("H")
    assert juan.type("") == "de EA7KLX>\n"
    assert "EA7KLX BBS (EA7KLX-1), sysop EA7KLX" in juan.type("I")
    assert juan.type("B") == "73 de EA7KLX\n" and juan.gone
    assert juan.type("L") == ""                         # nothing more once gone


def test_lines_arriving_in_pieces(tmp_path):
    bbs = store(tmp_path)
    juan = Station(bbs, "EA1ABC")
    juan.out.clear()
    juan.session.feed("N Ju")
    assert juan.out == []
    juan.session.feed("an\r\nL")
    assert "Thank you, Juan" in "".join(juan.out)
    juan.session.feed("\n")
    assert "No new messages" in "".join(juan.out)
