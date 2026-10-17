"""Quick logbook entry on the dashboard: one line into hamrlog's log, and today's contacts.

The line is hamrlog's fast entry (its active profile's field order and separator, e.g.
"EA7XYZ, Pepe, 59, 57, Sevilla"); the contact goes into hamrlog's own database with the
operator and station of hamrlog's last session. While the radio is connected its frequency
and mode are the contact's (unless the line gives others), so it is right for the IC-705
whatever hamrlog's profile was set to.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Input

from ic705suite.i18n import _

log = logging.getLogger(__name__)

# The rig's modes as hamrlog names them.
LOG_MODES = {"CW": "CW", "CW-R": "CW", "USB": "USB", "LSB": "LSB", "AM": "AM", "FM": "FM",
             "WFM": "FM", "RTTY": "RTTY", "RTTY-R": "RTTY", "DV": "DSTAR"}
TODAY_LIMIT = 2000      # contacts looked at to find today's
IC705_TOP = 470_000_000     # Hz: the IC-705 goes up to 70 cm


class LogUnavailable(Exception):
    pass


def _logbook():
    """hamrlog's services, with its database open (init_engine reuses an open one)."""
    try:
        from ic705suite.apps.log.core import entry
        from ic705suite.apps.log.core import bands as log_bands
        from ic705suite.apps.log.core.services import QsoService, ServiceError, SettingsService
        from ic705suite.apps.log.db.session import init_engine
    except ImportError as exc:     # pragma: no cover - part of the suite
        raise LogUnavailable(str(exc)) from None
    init_engine(None)
    return entry, log_bands, QsoService, ServiceError, SettingsService


def log_line(line: str, rig: dict) -> tuple[object | None, str]:
    """Log one fast-entry line: (the new contact, "") or (None, why not)."""
    entry, log_bands, QsoService, ServiceError, SettingsService = _logbook()
    state = SettingsService.load_state()
    mode = LOG_MODES.get(rig.get("mode") or "") if rig.get("connected") else None
    parsed = entry.parse(line, field_order=tuple(state.field_order), separator=state.separator,
                         mode_name=mode or state.mode, validation=state.callsign_validation)
    if not parsed.ok:
        return None, parsed.error or _("not a contact")
    fields = dict(parsed.fields)
    if rig.get("connected") and rig.get("frequency") and "freq_hz" not in fields \
            and "band" not in fields:
        fields["freq_hz"] = rig["frequency"]
        band = log_bands.from_frequency(rig["frequency"])
        if band is not None:
            fields["band"] = band.name
        if mode and "mode" not in fields:
            fields["mode"] = mode
        if rig.get("power") is not None and "power_w" not in fields:
            fields["power_w"] = round(rig["power"] * rig.get("max_power_w", 10) / 100)
    try:
        row = QsoService.log(fields, state, digital=parsed.digital)
    except ServiceError as exc:
        return None, str(exc)
    return row, "; ".join(parsed.warnings)


def repeaters(text: str = "") -> list[dict]:
    """hamrlog's repeaters the IC-705 can work (FM and D-STAR), as memory channels: the
    output to listen on, the duplex and offset to the input, the tone that opens it."""
    _logbook()
    from ic705suite.apps.log.core.services import RepeaterService

    rows = RepeaterService.search(text, limit=500) if text.strip() else \
        RepeaterService.list_all()
    out = []
    for r in rows:
        mode = (r.mode or "FM").upper().replace("-", "")
        mode = "DV" if mode in ("DSTAR", "DV") else "FM" if mode in ("FM", "NFM", "") else ""
        if not mode or not r.output_hz or r.output_hz > IC705_TOP:
            continue                         # digital ones it cannot work, 23 cm
        tone = r.ctcss_tx.strip() if r.ctcss_tx else ""
        out.append({"name": " ".join(x for x in (r.callsign, r.channel) if x)[:80],
                    "frequency": r.output_hz, "mode": mode, "filter": 1,
                    "duplex": "-" if r.shift_hz < 0 else "+" if r.shift_hz > 0 else "",
                    "offset": abs(r.shift_hz or 0),
                    "tone_mode": "tone" if tone and mode == "FM" else "",
                    "tone": float(tone) if tone.replace(".", "", 1).isdigit() else 88.5,
                    "note": " · ".join(x for x in (r.qth, r.name) if x)})
    return out


def today_contacts() -> list:
    """Today's contacts (UTC day), oldest first."""
    _entry, _bands, QsoService, _error, SettingsService = _logbook()
    state = SettingsService.load_state()
    today = dt.datetime.now(dt.timezone.utc).date()
    return [r for r in QsoService.recent(limit=TODAY_LIMIT, operator_id=state.operator_id)
            if r.qso_utc.date() == today]


def entry_hint() -> str:
    """The fields of the fast entry line, in the profile's order."""
    try:
        _entry, _bands, _qso, _error, SettingsService = _logbook()
        state = SettingsService.load_state()
    except Exception:  # noqa: BLE001 - only a placeholder
        return ""
    names = {"call": _("call"), "name": _("name"), "rst_sent": _("RST sent"),
             "rst_rcvd": _("RST rcvd"), "qth": _("QTH"), "comment": _("comment"),
             "freq_hz": _("frequency"), "band": _("band"), "mode": _("mode"),
             "gridsquare": _("locator"), "power_w": _("power")}
    return f"{state.separator} ".join(names.get(f, f) for f in state.field_order)


class QuickLog(Vertical):
    """The entry line and today's contacts below it (newest at the bottom)."""

    def __init__(self, demo: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.demo = demo          # hamrlog's demo log (the simulator): said in the title

    def compose(self) -> ComposeResult:
        yield Input(id="qso-input")
        yield DataTable(id="qso-today", cursor_type="none", zebra_stripes=False)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(_("UTC"), _("Call"), _("Name"), _("Band"), _("Mode"), _("Sent"),
                          _("Rcvd"), _("QTH"))
        table.can_focus = False
        self.border_title = _("Quick log · today") + (" · " + _("demo") if self.demo else "")
        self.run_worker(self._start())

    async def _start(self) -> None:
        try:
            hint = await asyncio.to_thread(entry_hint)
        except Exception:  # noqa: BLE001
            hint = ""
        self.query_one(Input).placeholder = hint or _("call, name, RST sent, RST rcvd...")
        await self.reload()

    async def reload(self) -> None:
        try:
            rows = await asyncio.to_thread(today_contacts)
        except Exception as exc:  # noqa: BLE001 - shown in the box
            log.warning("logbook not available: %s", exc)
            self._note("error", _("logbook not available: {error}").format(error=exc))
            return
        table = self.query_one(DataTable)
        table.clear()
        for r in rows:
            table.add_row(f"{r.qso_utc:%H:%M}", Text(r.call, style="bold cyan"), r.name or "",
                          r.band or "", r.mode or "", r.rst_sent or "", r.rst_rcvd or "",
                          r.qth or "")
        if rows:
            table.scroll_end(animate=False)
        self.border_subtitle = _("{count} today").format(count=len(rows))

    async def submit(self, line: str, rig: dict) -> bool:
        try:
            row, note = await asyncio.to_thread(log_line, line, rig)
        except Exception as exc:  # noqa: BLE001 - shown in the box
            self._note("error", str(exc))
            return False
        if row is None:
            self._note("error", note)
            return False
        await self.reload()
        where = f"{row.band} {row.mode}".strip()
        self._note("info", _("logged {call} {where}").format(call=row.call, where=where)
                   + (f" · {note}" if note else ""))
        return True

    def _note(self, kind: str, text: str) -> None:
        self.border_subtitle = Text(text[:100], style="bold red" if kind == "error" else "green")
