"""The logbook at a glance, as JSON: what the Plasma applet (packaging/plasma) shows.

    ic705suite --logbook-json              the latest QSOs and the counters
    ic705suite --logbook-json 20m FT8      only that band and mode (either one alone too)

The logbook is the one the clients open: the core's (asked once, as `ic705suite log` does)
when this machine is a client of another's core, this machine's own otherwise. Any failure
is JSON too ({"ok": false, "error": ...}), so the applet can say what went wrong.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

LIMIT = 50          # QSOs listed: the applet's list is not the logbook


def summary(words: list[str], limit: int = LIMIT) -> dict[str, Any]:
    """The counters, the bands and modes logged, and the latest QSOs of the filter."""
    from ic705suite.apps.log.api.schemas import qso_to_dict, stats_to_dict
    from ic705suite.apps.log.core.services import QsoService
    from ic705suite.apps.log.db.session import init_engine

    init_engine(None)
    band, mode = QsoService.parse_filter(" ".join(words))
    rows = QsoService.recent(limit, band=band or None, mode=mode or None)
    return {
        "ok": True,
        "filter": {"band": band, "mode": mode},
        "stats": stats_to_dict(QsoService.stats()),
        "bands": QsoService.logged_bands(),
        "modes": QsoService.logged_modes(),
        "qsos": [qso_to_dict(row) for row in reversed(rows)],     # newest first
    }


def open_logbook(config) -> str:
    """Point hamrlog at the clients' logbook; where it is, for the JSON."""
    from ic705suite import shared_logbook

    if config.connection == "server" and config.connect:
        if shared_logbook.ask_core(config.connect, config.client_token, timeout=5.0):
            return "core"
        return "local"
    url = shared_logbook.core_url(config)       # the core runs here: its [logbook]
    if url:
        shared_logbook.use(url)
        return "core"
    return "local"


def run(config, words: list[str]) -> int:
    try:
        source = open_logbook(config)
        answer = summary(words)
        answer["source"] = source
        answer["callsign"] = config.callsign
    except Exception as exc:  # noqa: BLE001 - the applet shows it
        log.debug("logbook summary failed", exc_info=True)
        answer = {"ok": False, "error": str(exc) or type(exc).__name__}
    print(json.dumps(answer, ensure_ascii=False))
    return 0
