"""Optional read-only REST API.

A deliberately small FastAPI application over the same service layer the TUI
uses, so a web frontend can be built later without touching the core. Run it
with::

    pip install hamrlog[api]
    uvicorn hamrlog.api.server:app

Write endpoints are intentionally absent: exposing them needs an
authentication decision that belongs to whoever deploys this, not to the
logbook itself.
"""

from __future__ import annotations

from typing import Any

from .. import __version__
from ..core.services import OperatorService, QsoService
from ..db.session import init_engine
from ..i18n import _
from .schemas import qso_to_dict, stats_to_dict


def create_app(database_url: str | None = None) -> Any:
    """Build the FastAPI application.

    Raises:
        ImportError: When the ``api`` extra is not installed.
    """
    try:
        from fastapi import FastAPI, HTTPException, Query
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            _("The API needs the extra: pip install hamrlog[api]")
        ) from exc

    init_engine(database_url)
    application = FastAPI(title="hamrlog", version=__version__)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/operators")
    def operators() -> list[dict[str, Any]]:
        return [
            {"id": op.id, "callsign": op.callsign, "name": op.name, "qth": op.qth}
            for op in OperatorService.list_all()
        ]

    @application.get("/qsos")
    def qsos(
        search: str = "",
        band: str | None = None,
        mode: str | None = None,
        operator_id: int | None = None,
        limit: int = Query(default=200, le=5000),
    ) -> list[dict[str, Any]]:
        rows = QsoService.search(
            search, band=band, mode=mode, operator_id=operator_id, limit=limit
        )
        return [qso_to_dict(row) for row in rows]

    @application.get("/qsos/{qso_id}")
    def qso(qso_id: int) -> dict[str, Any]:
        row = QsoService.get(qso_id)
        if row is None:
            raise HTTPException(status_code=404, detail=_("Contact not found"))
        return qso_to_dict(row)

    @application.get("/stats")
    def stats(operator_id: int | None = None) -> dict[str, Any]:
        return stats_to_dict(QsoService.stats(operator_id))

    return application


#: Module level application for ``uvicorn hamrlog.api.server:app``.
#: Building it here touches the database, so any failure leaves ``app`` as
#: None rather than breaking the import for callers that only want
#: ``create_app``.
try:  # pragma: no cover - depends on the installed extras
    app = create_app()
except Exception:  # noqa: BLE001
    app = None
