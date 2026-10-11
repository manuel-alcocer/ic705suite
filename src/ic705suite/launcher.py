"""Runs the suite in the terminal: the dashboard, and each application it opens, in turn.

All of them share one link to the core, connected in the background, so the radio stays
connected (and the CW channels, the skimmer, a sweep... keep running) while the operator
moves between applications.
"""

from __future__ import annotations

import asyncio
import logging

from ic705suite.config import Config
from ic705suite.i18n import N_
from ic705suite.station.links import Link

log = logging.getLogger(__name__)


def start_link(link: Link) -> asyncio.Task:
    """Connect in the background; a failure becomes an error event for the screen."""

    async def connect() -> None:
        try:
            await link.connect()
        except Exception as exc:  # noqa: BLE001 - shown to the operator, who can retry (F9)
            log.warning("cannot connect: %s", exc)
            link._dispatch({"type": "error", "msgid": N_("cannot connect: {error}"),
                            "args": {"error": str(exc)}, "text": f"cannot connect: {exc}"})

    return asyncio.ensure_future(connect())


def packet_link(config: Config) -> Link | None:
    """The link to the core packet runs on, when it is not this one ([packet] core)."""
    if not config.packet_core:
        return None
    from ic705suite.station.links import RemoteLink

    return RemoteLink(config.packet_core, config.packet_core_token or config.client_token,
                      "ic705suite packet")


# Keyboard only, everywhere: the mouse is never captured, so the terminal keeps it for
# selecting and copying text.


async def run_app(app_id: str, link: Link | None, config: Config, scp: set[str] | None = None,
                  standalone: bool = False) -> int:
    """Open one application. `standalone`: started directly from the command line, so
    leaving it ends the program (and it owns the link)."""
    if app_id == "cw":
        from ic705suite.apps.cw.tui import CwApp

        return await CwApp(link, scp=scp, owns_link=standalone).run_async(mouse=False) or 0
    if app_id == "view":
        from ic705suite.apps.view.app import ViewApp

        return await ViewApp(link, owns_link=standalone).run_async(mouse=False) or 0
    if app_id == "swr":
        from ic705suite.apps.swr.app import SwrApp

        return await SwrApp(link, owns_link=standalone).run_async(mouse=False) or 0
    if app_id == "log":
        from ic705suite.apps.log.db.session import init_engine
        from ic705suite.apps.log.tui.app import HamrlogApp

        # hamrlog keeps its own database (~/.local/share/hamrlog) and settings (F9).
        init_engine(None)
        # With the simulator it is hamrlog's demo log (see cli.demo_logbook).
        await HamrlogApp(demo=config.connection == "sim").run_async(mouse=False)
        return 0
    if app_id == "packet":
        from ic705suite.apps.packet.app import PacketApp

        remote = packet_link(config)
        if remote is not None:       # packet on another machine's core
            return await PacketApp(remote, owns_link=True).run_async(mouse=False) or 0
        return await PacketApp(link, owns_link=standalone).run_async(mouse=False) or 0
    raise ValueError(f"unknown application {app_id}")


async def run_suite(link: Link | None, config: Config, scp: set[str] | None = None,
                    start_app: str | None = None, problem: str = "") -> int:
    from ic705suite.dashboard.app import DashboardApp

    if link is not None:
        start_link(link)
    messages: list[tuple[str, str]] = []
    code = 0
    try:
        if start_app is not None:
            return await run_app(start_app, link, config, scp, standalone=True)
        while True:
            choice = await DashboardApp(link, config, problem, messages).run_async(mouse=False)
            if choice in (None, "quit"):
                break
            code = await run_app(choice, link, config, scp)
    finally:
        if link is not None and start_app is None:
            await link.close()
    return code
