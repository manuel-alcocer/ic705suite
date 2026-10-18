"""The reflectors' status, as their own pages tell it (QDStar's StatusPoller): online or
not, the nodes on our module, whether our terminal is linked, and the last heard.

Sources, in this order: the XLX directory (xlxapi.rlx.lu: every XLX reflector, its
dashboard and when it was last seen), the reflector's xlxd JSON API (?api=users,
?api=heard), and failing that its dashboard pages (modules, repeaters). Our terminal (INT:
R1, "EA7KLX Z") linked is looked for among the nodes; on a dashboard only guessed from the
XLX directory not finding it means nothing (a G3 server links through DCS214, never shown
on XLX214's). Every call here blocks (HTTP): the core runs them in threads.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request

XLX_API_URL = "http://xlxapi.rlx.lu/api.php?do=GetReflectorList"
ONLINE_MAX_AGE = 3600          # s: an XLX reflector seen in the directory this lately is up
MODULE_ROW_RE = re.compile(r"\|\s*([A-Z])\s*\|\s*([^|]*?)\s*\|\s*(\d+)\s*\|\s*REF\d{3}\1L",
                           re.I)
HEARD = 8
USER_AGENT = "ic705suite"


def http_get(url: str, timeout: float = 10, limit: int = 2_000_000) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply.read(limit).decode("utf-8", "replace")


def page_text(raw: str) -> str:
    """A dashboard page as text, its cells between | (for MODULE_ROW_RE)."""
    raw = re.sub(r"(?s)<(script|style).*?</\1>", "", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " | ", raw))
    return re.sub(r"\s+", " ", re.sub(r"(\s*\|\s*)+", " | ", text))


def parse_xlx(xml: str) -> dict[str, dict]:
    """The XLX directory: name -> dashboard, lastcontact, comment, country."""
    found = {}
    for block in re.findall(r"<reflector>(.*?)</reflector>", xml, re.S):
        fields = dict(re.findall(r"<(\w+)>(.*?)</\1>", block, re.S))
        name = fields.get("name", "").strip().upper()
        if name:
            found[name] = {
                "dashboard": html.unescape(fields.get("dashboardurl", "").strip()),
                "lastcontact": int(fields.get("lastcontact", "0") or 0),
                "comment": html.unescape(fields.get("comment", "").strip()),
                "country": html.unescape(fields.get("country", "").strip()),
            }
    return found


def fetch_xlx() -> dict[str, dict] | None:
    """The XLX directory, None when it cannot be read (no internet, for one)."""
    try:
        return parse_xlx(http_get(XLX_API_URL))
    except OSError:
        return None


def terminal_regex(call: str):
    """Our terminal as it may be written on a dashboard: 'EA7KLX Z', 'EA7KLX-Z', 'EA7KLXZ'."""
    call = (call or "").strip()
    if not call:
        return None
    base, _, suffix = call.partition(" ")
    suffix = suffix.strip()
    pattern = re.escape(base) + (r"[\s\-]*" + re.escape(suffix) if suffix else "")
    return re.compile(r"\b" + pattern + r"\b", re.I)


CALL_RE = re.compile(r"^[A-Z0-9]{1,3}\d[A-Z]{1,4}(?:[ -][A-Z])?$")
TABBED_TIME_RE = re.compile(r"(\d{1,2}:\d{2}:\d{2})")


def cells(text: str) -> list[str]:
    return [c.strip() for c in text.split("|") if c.strip()]


def tabbed_nodes(text: str) -> list[tuple[str, str, str]]:
    """The nodes on a tabbed dashboard (?tab=nodes, DCS214's): (call, module, module name)
    of each row 'EA7IYR G | GPS | ***.***.***.125 | D | D-STAR SPAIN | DPlus'."""
    row = cells(text)
    found = []
    for i in range(1, len(row) - 2):
        if row[i] == "GPS" and row[i + 1].startswith("***") and len(row[i + 2]) == 1:
            name = row[i + 3] if i + 3 < len(row) and not CALL_RE.match(row[i + 3]) else ""
            found.append((row[i - 1], row[i + 2].upper(), name))
    return found


def tabbed_heard(text: str) -> list[dict]:
    """The last heard on a tabbed dashboard (?tab=heard): 'Spain | EA7KLX | Manuel Alcocer |
    IC-705 | Portable / base | GPS | EA7KLX Z | D | D-STAR SPAIN | XLX214 | Saturday Sat Oct
    17 22:08:58 2026' (the name and the radio may be missing, or the radio a '—')."""
    row = cells(text)
    found = []
    for i in range(len(row) - 5):
        if row[i] != "GPS" or len(row[i + 2]) != 1 or not re.match(r"^[A-Z]{3}\d{3}$",
                                                                     row[i + 4]):
            continue
        start = next((j for j in range(max(0, i - 5), i) if CALL_RE.match(row[j])), None)
        if start is None:
            continue
        between = row[start + 1:i]
        named = len(between) == 3 or (len(between) == 2 and between[1] == "—")
        stamp = TABBED_TIME_RE.search(row[i + 5]) if i + 5 < len(row) else None
        found.append({"callsign": row[start], "name": between[0] if named else "",
                      "via": row[i + 1], "time": stamp.group(1) if stamp else ""})
    return found


def check(item: dict, xlx: dict[str, dict], terminal: str = "", get=None) -> dict:
    """One reflector's status: online (True, False, None unknown), users (nodes on our
    module), module_name, linked (True, False, None unknown), heard [{callsign, name, via,
    time}], nodes, comment, country, dashboard, checked."""
    get = get or http_get
    to = item["to"]
    reflector, module = to.lstrip("/")[:-1].strip(), to[-1]
    number = reflector[3:]
    # DCS/XRF reflectors usually share their number with an XLX; REF numbers do not
    entry = xlx.get(reflector) or ({} if reflector.startswith("REF")
                                   else xlx.get("XLX" + number, {}))
    dashboard = item.get("dashboard") or entry.get("dashboard", "")
    guessed = not item.get("dashboard")
    result = {"online": None, "users": None, "module_name": "", "linked": None, "heard": [],
              "nodes": [], "comment": entry.get("comment", ""),
              "country": entry.get("country", ""), "dashboard": dashboard,
              "checked": time.time()}
    if entry:
        result["online"] = time.time() - entry["lastcontact"] < ONLINE_MAX_AGE
    term_re = terminal_regex(terminal) if item.get("via") == "int" else None
    api = item.get("api")
    if api:
        try:
            users = json.loads(get(api + "?api=users"))
            nodes = [u for u in users if u.get("module") == module]
            result.update(online=True, users=len(nodes),
                          nodes=[u.get("callsign", "").strip() for u in nodes])
            if term_re:
                result["linked"] = any(term_re.search(u.get("callsign", "")) for u in users)
            heard = json.loads(get(api + "?api=heard"))
            result["heard"] = [{"callsign": h.get("callsign", ""), "name": h.get("name", ""),
                                "via": h.get("via", ""), "time": h.get("time", "")}
                               for h in heard[:HEARD]]
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    if dashboard and result["users"] is None:
        base = dashboard.rstrip("/") + "/"
        for page in ("index.php?show=modules", "modules.php"):
            try:
                raw = get(urllib.parse.urljoin(base, page))
            except OSError:
                continue
            if "tab=nodes" in raw:
                _check_tabbed(result, base, module, term_re, guessed, get)
                break
            rows = MODULE_ROW_RE.findall(page_text(raw))
            if rows:
                result["online"] = True
                for mod, name, users in rows:
                    if mod.upper() == module:
                        result["users"] = int(users)
                        result["module_name"] = name.strip(" -")
                break
        if term_re and result["linked"] is None:
            try:
                text = page_text(get(urllib.parse.urljoin(base, "index.php?show=repeaters")))
                found = bool(term_re.search(text))
                result["linked"] = True if found else (None if guessed else False)
            except OSError:
                pass
    return result


def _check_tabbed(result: dict, base: str, module: str, term_re, guessed: bool, get) -> None:
    """A tabbed dashboard (DCS214's own): the nodes and the last heard from its tabs."""
    try:
        nodes = tabbed_nodes(page_text(get(urllib.parse.urljoin(base, "?tab=nodes"))))
    except OSError:
        return
    if not nodes:
        return
    ours = [n for n in nodes if n[1] == module]
    result.update(online=True, users=len(ours), nodes=[n[0] for n in ours])
    result["module_name"] = next((n[2] for n in ours if n[2]), "").title()
    if term_re:
        found = any(term_re.search(n[0]) for n in nodes)
        result["linked"] = True if found else (None if guessed else False)
    try:
        heard = tabbed_heard(page_text(get(urllib.parse.urljoin(base, "?tab=heard"))))
        result["heard"] = heard[:HEARD]
    except OSError:
        pass
