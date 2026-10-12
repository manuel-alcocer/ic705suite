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
