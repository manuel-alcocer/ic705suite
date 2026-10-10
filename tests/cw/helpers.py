"""A CW session on its own station, as the core builds it."""

from __future__ import annotations

from ic705suite.apps.cw.session import Session
from ic705suite.rig import open_rig
from ic705suite.station.station import Station


def make_session(transport, config, ui) -> Session:
    station = Station(open_rig(config, transport), config, ui)
    return Session(station, config, ui)


async def start(session: Session) -> None:
    await session.station.start()
    await session.start()
    await session.activate()


async def stop(session: Session) -> None:
    await session.stop()
    await session.station.stop()
