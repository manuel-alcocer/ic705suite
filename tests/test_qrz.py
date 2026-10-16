"""QRZ.com's logbook API: answers read as QRZ sends them, the key never shown."""

import urllib.parse

from ic705suite import cli, qrz
from ic705suite.config import Config


class Answer:
    def __init__(self, body: str) -> None:
        self.body = body.encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass


def test_answers_and_the_adif_in_them():
    assert qrz.parse_answer("RESULT=OK&LOGIDS=130877825&COUNT=1") == \
        {"RESULT": "OK", "LOGIDS": "130877825", "COUNT": "1"}
    answer = qrz.parse_answer("RESULT=OK&COUNT=1&ADIF=<call:5>F5RRS <mode:3>FT8 <eor>")
    assert answer["COUNT"] == "1" and answer["ADIF"] == "<call:5>F5RRS <mode:3>FT8 <eor>"


def test_records_go_in_ascii():
    record = "<CALL:6>EA7LLT<QTH:6>Alcalá<COMMENT:9>73 <a:1>ñ!<EOR>"
    assert qrz.ascii_record(record) == "<CALL:6>EA7LLT<QTH:6>Alcala<COMMENT:9>73 <a:1>n!<EOR>"


def test_the_status_check(monkeypatch, capsys):
    sent: list = []

    def urlopen(request, timeout):
        sent.append(request)
        data = urllib.parse.quote("CALLSIGN=EA7KLX&TOTAL_QSOS=120&CONFIRMED=40")
        return Answer(f"RESULT=OK&DATA={data}")

    monkeypatch.setattr(qrz.urllib.request, "urlopen", urlopen)
    config = Config(callsign="EA7KLX", qrz_api_key="SECRET-KEY")
    assert cli.qrz_status(config) == 0
    shown = capsys.readouterr().out
    assert "logbook of: EA7KLX" in shown and "QSOs: 120" in shown and "SECRET" not in shown
    request = sent[0]
    assert request.get_header("User-agent").startswith("ic705suite/")
    assert urllib.parse.parse_qs(request.data.decode())["ACTION"] == ["STATUS"]

    monkeypatch.setattr(qrz.urllib.request, "urlopen",
                        lambda request, timeout: Answer("RESULT=AUTH&REASON=invalid api key"))
    assert cli.qrz_status(config) == 1
    assert "AUTH invalid api key" in capsys.readouterr().out
    assert cli.qrz_status(Config()) == 1
    assert "[qrz] api_key" in capsys.readouterr().err



def test_the_upload_of_the_new_qsos(tmp_path, monkeypatch, capsys):
    from ic705suite.apps.log.db.models import Qso
    from tests.test_lotw import logbook

    session = logbook(tmp_path, monkeypatch)
    monkeypatch.setattr("ic705suite.logbook_summary.open_logbook", lambda config: "local")
    config = Config(callsign="EA7KLX", qrz_api_key="SECRET-KEY")
    sent: list = []

    def urlopen(request, timeout):
        record = urllib.parse.parse_qs(request.data.decode())["ADIF"][0]
        sent.append(record)
        if "EA7EAY" in record:
            return Answer("RESULT=FAIL&REASON=Unable to add QSO to database: duplicate&COUNT=0")
        return Answer(f"RESULT=OK&LOGID={1000 + len(sent)}&COUNT=1")

    try:
        monkeypatch.setattr(qrz.urllib.request, "urlopen", lambda request, timeout: Answer(
            "RESULT=AUTH&REASON=invalid api key"))
        assert cli.qrz_upload(config) == 1                  # refused: all still pending
        assert "F5RRS: AUTH invalid api key" in capsys.readouterr().err
        assert len(qrz.pending("EA7KLX")) == 1                     # HF: no 2 m
        assert len(qrz.pending("EA7KLX", bands=("20m", "2m"))) == 2
        config.qrz_bands = ("20m", "2m")

        monkeypatch.setattr(qrz.urllib.request, "urlopen", urlopen)
        assert cli.qrz_upload(config) == 0
        assert len(sent) == 2 and not any("DL1ABC" in r for r in sent)  # another operator's
        assert "<CALL:5>F5RRS" in sent[0] and sent[0].endswith("<EOR>")
        shown = capsys.readouterr().out
        assert "1 QSOs uploaded, 1 already there" in shown and "SECRET" not in shown
        with session.session_scope() as s:
            f5rrs = next(q for q in s.query(Qso) if q.call == "F5RRS").extra
        assert f5rrs[qrz.SENT] == "Y" and f5rrs[qrz.LOGID] == "1001"
        assert cli.qrz_upload(config) == 0 and len(sent) == 2            # sent once
        assert "0 QSOs uploaded, 0 already there" in capsys.readouterr().out
    finally:
        session.dispose()
