"""QRZ.com's logbook, through its API (https://logbook.qrz.com/api): a POST of name=value
pairs with the logbook's access key ([qrz] api_key: on QRZ.com, the logbook's settings) and
an action; the answer is name=value pairs too (RESULT OK, FAIL or AUTH, REASON, COUNT,
LOGIDS, DATA, ADIF). QRZ asks every program for a user agent of its own.

The key is never printed nor logged.
"""

from __future__ import annotations

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
