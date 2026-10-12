"""LoTW from the core: the logbook's QSOs signed and uploaded by TQSL (a fake one here),
the confirmations marked; repeaters and other operators left out."""

import asyncio
import datetime as dt
import stat

import pytest

from ic705suite import lotw
from ic705suite.config import Config


def fake_tqsl(tmp_path, code: int = 0):
    """A tqsl that keeps its arguments and the log it was given, and exits with `code`."""
    seen = tmp_path / "seen"
    script = tmp_path / "tqsl"
    script.write_text(f"#!/bin/sh\necho \"$@\" > {seen}.args\ncp \"$(eval echo \\${{$#}})\" "
                      f"{seen}.adi\necho 'Final Status: Success'\nexit {code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), seen


def logbook(tmp_path, monkeypatch):
    from ic705suite.apps.log.core.services import OperatorService, QsoService, SettingsService
    from ic705suite.apps.log.db import session

    url = f"sqlite:///{tmp_path / 'logbook.sqlite3'}"
    monkeypatch.setenv("HAMRLOG_DATABASE_URL", url)
    session.dispose()
    session.init_engine(url)
    me = OperatorService.create("EA7KLX", "Manuel")
    other = OperatorService.create("EA7XXX", "Otro")
    state = SettingsService.load_state()
    state.operator_id = me.id
    when = dt.datetime(2026, 10, 12, 7, 4, 30)
    QsoService.log({"call": "F5RRS", "mode": "FT8", "band": "20m", "freq_hz": 14_075_686,
                    "rst_sent": "-16", "rst_rcvd": "-08"}, state, qso_utc=when)
    QsoService.log({"call": "EA7EAY", "mode": "FM", "band": "2m", "freq_hz": 145_300_000},
                   state, qso_utc=when)
    state.repeater_call = "ED7ZAB"                                 # through a repeater
    QsoService.log({"call": "EA7LLT", "mode": "FM"}, state, qso_utc=when)
    state.repeater_call, state.operator_id = "", other.id         # another operator's
    QsoService.log({"call": "DL1ABC", "mode": "SSB", "band": "20m"}, state, qso_utc=when)
    return session


def test_the_command(monkeypatch, tmp_path):
    path = tmp_path / "x.adi"
    monkeypatch.setenv("DISPLAY", ":0")
    assert lotw.tqsl_command("tqsl", "Bellavista", path) == \
        ["tqsl", "-x", "-d", "-u", "-a", "compliant", "-l", "Bellavista", str(path)]
    monkeypatch.delenv("DISPLAY")
    monkeypatch.setattr(lotw.shutil, "which", lambda name: "/usr/bin/" + name)
    assert lotw.tqsl_command("tqsl", "Bellavista", path, "k")[:3] == ["xvfb-run", "-a", "tqsl"]
    assert "-p" in lotw.tqsl_command("tqsl", "Bellavista", path, "k")


def test_upload_and_its_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    tqsl, seen = fake_tqsl(tmp_path)
    assert lotw.upload("<eoh>\n<call:5>F5RRS<eor>\n", "Bellavista", tqsl) == \
        "Final Status: Success"
    assert "-l Bellavista" in (tmp_path / "seen.args").read_text()
    for code in (8, 9):                                     # duplicates: uploaded before
        assert lotw.upload("x", "Bellavista", fake_tqsl(tmp_path, code)[0])
    with pytest.raises(lotw.LotwError, match="rejected by LoTW"):
        lotw.upload("x", "Bellavista", fake_tqsl(tmp_path, 2)[0])
    with pytest.raises(lotw.LotwError, match="location"):
        lotw.upload("x", "", tqsl)


def test_pending_sent_and_confirmed(tmp_path, monkeypatch):
    session = logbook(tmp_path, monkeypatch)
    try:
        ids, adif = lotw.pending("EA7KLX")
        assert len(ids) == 2 and "<CALL:5>F5RRS" in adif.upper() and "EA7EAY" in adif
        assert "EA7LLT" not in adif and "DL1ABC" not in adif      # repeater, other operator
        lotw.mark(ids, {lotw.SENT: "Y", lotw.SENT_DATE: "20261012"})
        assert lotw.pending("EA7KLX") == ([], "")
        report = ("ARRL Logbook of the World Status Report\n<eoh>\n"
                  "<CALL:5>F5RRS <BAND:3>20M <MODE:3>FT8 <QSO_DATE:8>20261012 "
                  "<TIME_ON:6>070500 <QSL_RCVD:1>Y <QSLRDATE:8>20261013 <eor>\n")
        assert lotw.confirm(report) == ["F5RRS"]
        assert lotw.confirm(report) == []                          # once
        from ic705suite.apps.log.core.services import QsoService

        f5rrs = next(q for q in QsoService.recent() if q.call == "F5RRS")
        from ic705suite.apps.log.db.models import Qso

        with session.session_scope() as s:
            extra = s.get(Qso, f5rrs.id).extra
        assert extra[lotw.RCVD] == "Y" and extra[lotw.RCVD_DATE] == "20261013"
    finally:
        session.dispose()


def test_the_service_in_the_core(tmp_path, monkeypatch):
    session = logbook(tmp_path, monkeypatch)
    monkeypatch.setenv("DISPLAY", ":0")
    tqsl, _seen = fake_tqsl(tmp_path)
    events: list[tuple] = []

    class Hub:
        def info(self, msgid, **args):
            events.append(("info", msgid.format(**args)))

        def error(self, msgid, **args):
            events.append(("error", msgid.format(**args)))

    monkeypatch.setattr(lotw, "report", lambda *a, **k: "<eoh>\n")
    config = Config(callsign="EA7KLX", logbook_database="x", lotw_location="Bellavista",
                    lotw_tqsl=tqsl, lotw_username="ea7klx", lotw_password="pw")
    service = lotw.LotwService(config, Hub())
    try:
        assert service.enabled
        result = asyncio.run(service.sync())
        assert result["uploaded"] == 2 and result["confirm_ok"]
        assert ("info", "LoTW: 2 QSOs signed and uploaded") in events
        assert asyncio.run(service.sync())["uploaded"] == 0         # nothing new
    finally:
        session.dispose()
