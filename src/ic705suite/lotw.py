"""ARRL's Logbook of the World from the core: the logbook's QSOs signed with the operator's
certificate and uploaded by TQSL (the same program as by hand: `tqsl -x -d -u`, under a
virtual display on a machine without one), and the confirmations brought back from the
LoTW report, each QSO marked with ADIF's own fields in its `extra`:

    LOTW_QSL_SENT Y, LOTW_QSLSDATE   uploaded
    LOTW_QSL_RCVD Y, LOTW_QSLRDATE   confirmed by the other station

[lotw] location names TQSL's station location (the certificate's callsign and grid);
username and password, the LoTW website's, are only for the confirmations. Only the bands
in [lotw] bands are sent (HF unless told otherwise); never QSOs made through a repeater
(LoTW does not count them), nor other operators'.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

REPORT_URL = "https://lotw.arrl.org/lotwuser/lotwreport.adi"
TIMEOUT = 120
# tqsl's exit codes: 0 all signed and uploaded, 8 nothing new (all duplicates or out of
# the location's dates), 9 some were duplicates. Anything else: not uploaded.
UPLOADED = {0, 8, 9}
TQSL_ERRORS = {1: "cancelled", 2: "rejected by LoTW", 3: "unexpected answer from LoTW",
               4: "TQSL error", 5: "TQSL library error", 6: "cannot open the log",
               7: "cannot write the signed log", 10: "bad command line",
               11: "cannot connect to LoTW"}
# The bands sent unless [lotw] bands says otherwise: HF (160 to 10 m).
HF_BANDS = ("160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m")
FIRST_DAY = dt.date(1945, 1, 1)               # "since" for every confirmation there is
SENT, SENT_DATE = "LOTW_QSL_SENT", "LOTW_QSLSDATE"
RCVD, RCVD_DATE = "LOTW_QSL_RCVD", "LOTW_QSLRDATE"


class LotwError(Exception):
    pass


def tqsl_command(tqsl: str, location: str, path: Path, password: str = "") -> list[str]:
    """The upload, under xvfb-run when there is no display (TQSL is a GTK program)."""
    command = [tqsl, "-x", "-d", "-u", "-a", "compliant", "-l", location]
    if password:
        command += ["-p", password]
    command.append(str(path))
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a", *command]
    return command


def upload(adif: str, location: str, tqsl: str = "tqsl", password: str = "") -> str:
    """Sign and upload an ADI log; LotwError with TQSL's reason when it did not go."""
    if not location:
        raise LotwError("no TQSL station location: [lotw] location")
    if shutil.which(tqsl) is None and not Path(tqsl).is_file():
        raise LotwError("TQSL is not installed here (apt install trustedqsl xvfb)")
    with tempfile.TemporaryDirectory(prefix="ic705suite-lotw-") as folder:
        path = Path(folder) / "upload.adi"
        path.write_text(adif, encoding="utf-8")
        try:
            done = subprocess.run(tqsl_command(tqsl, location, path, password),
                                  capture_output=True, text=True, timeout=TIMEOUT,
                                  check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LotwError(f"TQSL did not run: {exc}") from None
    said = (done.stdout + done.stderr).strip().splitlines()
    if done.returncode not in UPLOADED:
        reason = TQSL_ERRORS.get(done.returncode, f"exit {done.returncode}")
        raise LotwError(f"{reason}: {said[-1] if said else ''}".rstrip(": "))
    return said[-1] if said else "uploaded"


def report(username: str, password: str, since: dt.date | None, own_call: str = "") -> str:
    """The confirmations (QSL received) since a date, as ADIF."""
    if not username or not password:
        raise LotwError("no LoTW account: [lotw] username and password")
    query = {"login": username, "password": password, "qso_query": "1", "qso_qsl": "yes",
             "qso_qsldetail": "yes"}
    if since is not None:
        query["qso_qslsince"] = since.isoformat()
    if own_call:
        query["qso_owncall"] = own_call
    url = f"{REPORT_URL}?{urllib.parse.urlencode(query)}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as answer:
            text = answer.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LotwError(f"cannot reach LoTW: {exc}") from None
    if "<eoh>" not in text.lower():
        raise LotwError("LoTW refused the report (username or password?)")
    return text


# ---- the logbook ------------------------------------------------------------------------------

def _base(call: str) -> str:
    from ic705suite.apps.log.core import callsign

    return callsign.base_call(call or "")


def pending(own_call: str, bands=HF_BANDS, limit: int = 500) -> tuple[list[int], str]:
    """(ids, ADI text) of the operator's QSOs on `bands` not sent to LoTW yet, repeaters
    left out."""
    from sqlalchemy import select

    from ic705suite.apps.log.adif.writer import adif_header, qso_to_adif
    from ic705suite.apps.log.core.services import _to_rows
    from ic705suite.apps.log.db.models import Operator, Qso
    from ic705suite.apps.log.db.session import session_scope

    with session_scope() as session:
        rows = session.execute(
            select(Qso, Operator.callsign).join(Operator, Qso.operator_id == Operator.id)
            .where(Qso.repeater_id.is_(None)).order_by(Qso.qso_utc)).all()
        allowed = {b.lower() for b in bands}
        wanted = [q for q, op_call in rows
                  if _base(op_call) == _base(own_call) and not q.repeater_call
                  and (q.band or "").lower() in allowed
                  and (q.extra or {}).get(SENT) != "Y"][:limit]
        records = [qso_to_adif(row) for row in _to_rows(session, wanted)]
        ids = [q.id for q in wanted]
    return ids, (adif_header(len(records)) + "\n".join(records) + "\n") if records else ""


def mark(ids: list[int], fields: dict[str, str]) -> None:
    from sqlalchemy import select

    from ic705suite.apps.log.db.models import Qso
    from ic705suite.apps.log.db.session import session_scope

    with session_scope() as session:
        for qso in session.scalars(select(Qso).where(Qso.id.in_(ids))):
            qso.extra = {**(qso.extra or {}), **fields}      # a new dict: saved as changed


def confirm(adif: str) -> list[str]:
    """The confirmations of a LoTW report onto the logbook's QSOs (same call, band and
    mode, within ten minutes): the calls confirmed now."""
    from sqlalchemy import select

    from ic705suite.apps.log.adif.reader import parse_adif
    from ic705suite.apps.log.db.models import Qso
    from ic705suite.apps.log.db.session import session_scope

    confirmed = []
    with session_scope() as session:
        for record in parse_adif(adif):
            if record.qso_utc is None or not record.call:
                continue
            when = record.qso_utc
            near = session.scalars(select(Qso).where(
                Qso.base_call == _base(record.call),
                Qso.qso_utc >= when - dt.timedelta(minutes=10),
                Qso.qso_utc <= when + dt.timedelta(minutes=10))).all()
            band = str(record.fields.get("band") or "").lower()
            for qso in near:
                if band and qso.band.lower() != band:
                    continue
                if (qso.extra or {}).get(RCVD) == "Y":
                    break
                qsl_date = str(record.extra.get("QSLRDATE") or record.extra.get("qslrdate")
                               or dt.date.today().strftime("%Y%m%d"))
                qso.extra = {**(qso.extra or {}), SENT: "Y", RCVD: "Y", RCVD_DATE: qsl_date}
                confirmed.append(qso.call)
                break
    return confirmed


# ---- in the core ------------------------------------------------------------------------------

class LotwService:
    """Uploads what is pending every UPLOAD_EVERY seconds and asks LoTW for confirmations
    every CONFIRM_EVERY; `lotw.sync` (a call) does both now."""

    UPLOAD_EVERY = 300.0
    CONFIRM_EVERY = 6 * 3600.0

    def __init__(self, config, hub) -> None:
        self.config = config
        self.hub = hub
        self.task = None
        self.last: dict = {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.lotw_location and self.config.logbook_database)

    async def start(self) -> None:
        import asyncio

        if self.enabled and self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        import asyncio
        import contextlib

        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def _run(self) -> None:
        import asyncio

        confirmed_at = 0.0
        await asyncio.sleep(30)                   # the station first
        while True:
            await self.sync(confirmations=asyncio.get_running_loop().time() - confirmed_at
                            >= self.CONFIRM_EVERY)
            if self.last.get("confirm_ok"):
                confirmed_at = asyncio.get_running_loop().time()
            await asyncio.sleep(self.UPLOAD_EVERY)

    async def sync(self, confirmations: bool = True) -> dict:
        import asyncio

        from ic705suite.i18n import N_

        result: dict = {}
        try:
            ids, adif = await asyncio.to_thread(pending, self.config.callsign,
                                                self.config.lotw_bands)
            if ids:
                said = await asyncio.to_thread(upload, adif, self.config.lotw_location,
                                               self.config.lotw_tqsl, self.config.lotw_key_password)
                await asyncio.to_thread(mark, ids, {SENT: "Y", SENT_DATE:
                                                    dt.date.today().strftime("%Y%m%d")})
                self.hub.info(N_("LoTW: {count} QSOs signed and uploaded"), count=len(ids))
                log.info("lotw: %d uploaded (%s)", len(ids), said)
            result["uploaded"] = len(ids)
        except LotwError as exc:
            self.hub.error(N_("LoTW: not uploaded: {error}"), error=str(exc))
            result["error"] = str(exc)
        if confirmations and self.config.lotw_username:
            try:
                since = self._since()
                text = await asyncio.to_thread(report, self.config.lotw_username,
                                               self.config.lotw_password, since,
                                               self.config.callsign)
                calls = await asyncio.to_thread(confirm, text)
                self._save_since()
                if calls:
                    self.hub.info(N_("LoTW: {count} QSOs confirmed ({calls})"),
                                  count=len(calls), calls=", ".join(calls[:8]))
                result["confirmed"] = len(calls)
                result["confirm_ok"] = True
            except LotwError as exc:
                self.hub.error(N_("LoTW: no confirmations: {error}"), error=str(exc))
                result["confirm_error"] = str(exc)
        self.last = result
        return result

    def _state_path(self) -> Path:
        from ic705suite.paths import data_dir

        return data_dir() / "lotw-since"

    def _since(self) -> dt.date | None:
        try:
            return dt.date.fromisoformat(self._state_path().read_text().strip())
        except (OSError, ValueError):
            # The first time, every confirmation: with no date LoTW gives only those new
            # since the account's last report, whoever asked for it.
            return FIRST_DAY

    def _save_since(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # A day back: confirmations of the same day are asked again (marked once).
        path.write_text((dt.date.today() - dt.timedelta(days=1)).isoformat())

    async def call(self, method: str, params: dict) -> dict:
        if method == "sync":
            return await self.sync()
        if method == "state":
            return {"enabled": self.enabled, "location": self.config.lotw_location,
                    "bands": list(self.config.lotw_bands), "last": self.last}
        return {"error": f"unknown method lotw.{method}"}
