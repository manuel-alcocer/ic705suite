"""QRZ.com's logbook, through its API (https://logbook.qrz.com/api): a POST of name=value
pairs with the logbook's access key ([qrz] api_key: on QRZ.com, the logbook's settings) and
an action; the answer is name=value pairs too (RESULT OK, FAIL or AUTH, REASON, COUNT,
LOGIDS, DATA, ADIF). QRZ asks every program for a user agent of its own.

The key is never printed nor logged.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from ic705suite import __version__

API_URL = "https://logbook.qrz.com/api"
TIMEOUT = 30


class QrzError(Exception):
    pass


def user_agent(callsign: str = "") -> str:
    return f"ic705suite/{__version__}" + (f" ({callsign})" if callsign else "")


def parse_answer(body: str) -> dict[str, str]:
    """The answer's pairs; an ADIF= one runs to the end (the records hold & and =)."""
    head, sep, adif = body.partition("ADIF=")
    out = {k: v[0] for k, v in urllib.parse.parse_qs(head.rstrip("&"),
                                                       keep_blank_values=True).items()}
    if sep:
        out["ADIF"] = urllib.parse.unquote(adif)
    return out


def call(key: str, action: str, callsign: str = "", **params: str) -> dict[str, str]:
    if not key:
        raise QrzError("no QRZ logbook key: [qrz] api_key")
    data = urllib.parse.urlencode({"KEY": key, "ACTION": action, **params}).encode()
    request = urllib.request.Request(API_URL, data=data,
                                     headers={"User-Agent": user_agent(callsign)})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            body = answer.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QrzError(f"cannot reach QRZ.com: {exc}") from None
    return parse_answer(body)


def status(key: str, callsign: str = "") -> dict[str, str]:
    """The logbook's summary (owner, QSOs, confirmed, dates), or RESULT and REASON."""
    answer = call(key, "STATUS", callsign)
    if answer.get("RESULT") == "OK" and answer.get("DATA"):
        data = urllib.parse.parse_qs(answer["DATA"].replace("&amp;", "&"))
        answer.update({k: v[0] for k, v in data.items()})
    return answer


# ---- uploading the logbook --------------------------------------------------------------------
# Each QSO goes in an INSERT of its own, and is marked in its `extra` with ADIF's own fields
# (and the QRZ LOGID), so it is sent once. One QRZ already has is a duplicate: marked too.

SENT, SENT_DATE, LOGID = "QRZCOM_QSO_UPLOAD_STATUS", "QRZCOM_QSO_UPLOAD_DATE", "QRZCOM_LOGID"
# The bands uploaded unless [qrz] bands says otherwise: HF (160 to 10 m), no VHF/UHF.
HF_BANDS = ("160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m")


def ascii_record(record: str) -> str:
    """The record with its values in ASCII (Alcalá: Alcala), as ADI wants them: hamrlog's
    lengths count characters, QRZ counts bytes, and an accent throws its reading off."""
    out, at = [], 0
    for tag in re.finditer(r"<([A-Za-z0-9_]+):(\d+)>", record):
        if tag.start() < at:                    # a "<x:1>" inside a value already read
            continue
        out.append(record[at:tag.start()])
        value = record[tag.end():tag.end() + int(tag.group(2))]
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
        out.append(f"<{tag.group(1)}:{len(value)}>{value}")
        at = tag.end() + int(tag.group(2))
    return "".join(out) + record[at:]


def insert(key: str, record: str, callsign: str = "") -> str:
    """One ADI record into the logbook: its LOGID, or "" when QRZ had it already."""
    answer = call(key, "INSERT", callsign, ADIF=record)
    if answer.get("RESULT") == "OK":
        return answer.get("LOGID") or answer.get("LOGIDS", "")
    reason = answer.get("REASON", "")
    if answer.get("RESULT") == "FAIL" and "duplicate" in reason.lower():
        return ""
    raise QrzError(f"{answer.get('RESULT', '?')} {reason}".rstrip())


def pending(own_call: str, bands=HF_BANDS) -> list[tuple[int, str, str]]:
    """(id, call, ADI record) of the operator's QSOs on `bands` not on QRZ.com yet, oldest
    first."""
    from sqlalchemy import select

    from ic705suite.apps.log.adif.writer import qso_to_adif
    from ic705suite.apps.log.core import callsign
    from ic705suite.apps.log.core.services import _to_rows
    from ic705suite.apps.log.core.transfer import _station_lookup
    from ic705suite.apps.log.db.models import Operator, Qso
    from ic705suite.apps.log.db.session import session_scope

    stations = _station_lookup()
    mine = callsign.base_call(own_call or "")
    allowed = {b.lower() for b in bands}
    with session_scope() as session:
        rows = session.execute(
            select(Qso, Operator.callsign).join(Operator, Qso.operator_id == Operator.id)
            .order_by(Qso.qso_utc)).all()
        wanted = [q for q, op_call in rows
                  if callsign.base_call(op_call or "") == mine
                  and (q.band or "").lower() in allowed
                  and (q.extra or {}).get(SENT) != "Y"]
        return [(row.id, row.call,
                 ascii_record(qso_to_adif(row, station=stations.get(row.station_name))))
                for row in _to_rows(session, wanted)]


def mark(qso_id: int, logid: str) -> None:
    import datetime as dt

    from ic705suite.apps.log.db.models import Qso
    from ic705suite.apps.log.db.session import session_scope

    fields = {SENT: "Y", SENT_DATE: dt.date.today().strftime("%Y%m%d")}
    if logid:
        fields[LOGID] = logid
    with session_scope() as session:
        qso = session.get(Qso, qso_id)
        if qso is not None:
            qso.extra = {**(qso.extra or {}), **fields}      # a new dict: saved as changed


def upload_pending(key: str, own_call: str, bands=HF_BANDS,
                   report=print) -> tuple[int, int]:
    """Every pending QSO up, one by one: (uploaded, duplicates). Stops at the first refusal
    that is not a duplicate (QrzError), leaving it and the rest pending."""
    uploaded = duplicates = 0
    for qso_id, call_, record in pending(own_call, bands):
        try:
            logid = insert(key, record, own_call)
        except QrzError as exc:
            raise QrzError(f"{call_}: {exc}") from None
        mark(qso_id, logid)
        if logid:
            uploaded += 1
            report(f"{call_}: uploaded (LOGID {logid})")
        else:
            duplicates += 1
            report(f"{call_}: already on QRZ.com")
    return uploaded, duplicates
