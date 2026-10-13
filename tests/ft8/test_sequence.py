"""The QSO's sequence, as WSJT-X runs it: both ways, repeats and giving up."""

from __future__ import annotations

from ic705suite.apps.ft8.sequence import TRIES, Qso, parse_decode, report


def heard(qso: Qso, text: str, snr: int = -10) -> bool:
    return qso.heard(parse_decode(text, snr))


def test_reports():
    assert (report(-5), report(3), report(-40), report(60), report(0)) == \
        ("-05", "+03", "-30", "+49", "+00")


def test_answering_a_cq():
    qso = Qso("EA7KLX", "IM77")
    assert qso.answer(parse_decode("CQ DL1ABC JO62", -14)) == "DL1ABC EA7KLX IM77"
    qso.sent()
    assert not heard(qso, "CQ DL1ABC JO62")                  # not answered yet: again
    assert qso.message == "DL1ABC EA7KLX IM77"
    assert heard(qso, "EA7KLX DL1ABC -07", snr=-12)
    assert qso.message == "DL1ABC EA7KLX R-12" and qso.got_report == "-07"
    qso.sent()
    assert heard(qso, "EA7KLX DL1ABC RR73")
    assert qso.message == "DL1ABC EA7KLX 73"
    qso.sent()
    assert qso.step == "done" and qso.complete
    assert qso.log == ["DL1ABC EA7KLX IM77", "DL1ABC EA7KLX R-12", "DL1ABC EA7KLX 73"]


def test_calling_cq():
    qso = Qso("EA7KLX", "IM77")
    assert qso.call_cq() == "CQ EA7KLX IM77"
    qso.sent()
    assert not heard(qso, "CQ F5XYZ JN12")                   # not to us
    assert heard(qso, "EA7KLX DL1ABC JO62", snr=-8)
    assert (qso.message, qso.them, qso.their_grid) == ("DL1ABC EA7KLX -08", "DL1ABC", "JO62")
    qso.sent()
    assert heard(qso, "EA7KLX DL1ABC R-15")
    assert qso.message == "DL1ABC EA7KLX RR73" and qso.got_report == "-15"
    qso.sent()
    assert qso.complete and qso.step == "tx4"
    assert heard(qso, "EA7KLX DL1ABC 73")
    assert qso.step == "done"


def test_they_were_calling_us_already():
    """Answering a station that called our CQ with its grid: straight to the report."""
    qso = Qso("EA7KLX", "IM77")
    assert qso.answer(parse_decode("EA7KLX DL1ABC JO62", -3)) == "DL1ABC EA7KLX -03"
    assert qso.step == "tx2"


def test_repeats_others_and_giving_up():
    qso = Qso("EA7KLX/P", "IM77")
    qso.answer(parse_decode("CQ DL1ABC JO62", -14))
    assert qso.message == "DL1ABC EA7KLX/P IM77"
    assert not heard(qso, "EA7KLX G4XYZ -10")                 # someone else: later
    assert heard(qso, "EA7KLX DL1ABC -07")
    qso.sent()
    assert not heard(qso, "EA7KLX DL1ABC -07")                # they repeat: ours again
    assert qso.message == "DL1ABC EA7KLX/P R-10"
    for _ in range(TRIES - 1):
        qso.sent()
    assert qso.gave_up
    qso.stop()
    assert qso.step == "idle" and not heard(qso, "EA7KLX DL1ABC RR73")


def test_skipped_steps():
    qso = Qso("EA7KLX", "IM77")
    qso.answer(parse_decode("CQ DL1ABC JO62", -14))
    assert heard(qso, "EA7KLX DL1ABC R-07")                   # their R-report at once
    assert qso.message == "DL1ABC EA7KLX RR73"
    cq = Qso("EA7KLX", "IM77")
    cq.call_cq()
    assert heard(cq, "EA7KLX DL1ABC R-09")
    assert cq.message == "DL1ABC EA7KLX RR73" and cq.got_report == "-09"


def test_heard_while_our_message_goes_out():
    """Their RR73 decoded while our R-report is still going out (a slow decoder): that
    message is not taken for the 73, which goes in the next turn."""
    qso = Qso("EA7KLX", "IM77")
    qso.answer(parse_decode("CQ DL1ABC JO62", -14))
    qso.sent()
    heard(qso, "EA7KLX DL1ABC -07", snr=-12)
    going = qso.message                                   # DL1ABC EA7KLX R-12, keyed
    assert heard(qso, "EA7KLX DL1ABC RR73")               # decoded meanwhile
    qso.sent(going)
    assert qso.step == "tx5" and qso.message == "DL1ABC EA7KLX 73" and qso.tries == 0
    qso.sent("DL1ABC EA7KLX 73")
    assert qso.step == "done" and qso.complete
    assert qso.log == ["DL1ABC EA7KLX IM77", "DL1ABC EA7KLX R-12", "DL1ABC EA7KLX 73"]


def test_how_far_the_qso_has_gone():
    """Five messages both ways: the QSO area's bar counts those gone by."""
    from ic705suite.apps.ft8.app import progress_bar, qso_progress, qso_status

    qso = Qso("EA7KLX", "IM77")

    def now() -> tuple[int, int, str] | None:
        return qso_progress({"on": True, "step": qso.step, "tries": qso.tries})

    qso.answer(parse_decode("CQ DL1ABC JO62", -14))
    assert now()[:2] == (0, 5)
    qso.sent()
    assert now()[0] == 1 and "waiting for their report" in now()[2]
    heard(qso, "EA7KLX DL1ABC -07", snr=-12)
    assert now()[0] == 2
    qso.sent()
    assert now()[0] == 3 and "RR73" in now()[2]
    heard(qso, "EA7KLX DL1ABC RR73")
    assert now()[0] == 4
    qso.sent()
    assert qso.step == "done"
    assert qso_progress({"on": False, "step": "idle"}) is None
    assert qso_status({"on": False, "step": "idle"}, "DL1ABC") == \
        (5, 5, "QSO with DL1ABC complete")
    assert qso_status({"on": True, "step": "tx3", "tries": 1, "them": "DL1ABC"})[2] \
        .startswith("DL1ABC · ")
    assert progress_bar(3, 5) == "██████░░░░"

    cq = Qso("EA7KLX", "IM77")
    cq.call_cq()
    assert qso_progress({"on": True, "step": cq.step, "tries": 0})[0] == 0
    cq.sent()
    assert qso_progress({"on": True, "step": cq.step, "tries": cq.tries})[0] == 1
    heard(cq, "EA7KLX DL1ABC JO62")
    assert qso_progress({"on": True, "step": cq.step, "tries": cq.tries})[0] == 2


def test_their_grid_sent_later():
    """Answered from a message without a grid (their RR73 to another): the grid they send
    during the QSO is theirs; one already known is kept."""
    qso = Qso("EA7KLX", "IM77")
    qso.answer(parse_decode("G4XYZ DL1ABC RR73", -14))
    assert qso.their_grid == ""
    assert not heard(qso, "EA7KLX DL1ABC JO62")              # their call, to us
    assert qso.their_grid == "JO62"
    assert heard(qso, "EA7KLX DL1ABC -07")
    assert not heard(qso, "EA7KLX DL1ABC JO50")
    assert qso.their_grid == "JO62"
