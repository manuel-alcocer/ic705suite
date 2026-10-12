"""FT8 against the logbook: each decode placed (country, km, bearing) and marked (new
DXCC, new grid, worked before), as the band activity shows it."""

import asyncio

from ic705suite import geo, shared_logbook
from ic705suite.apps.ft8.app import mark, rows, where
from ic705suite.config import Config
from ic705suite.station.links import LocalLink
from tests.ft8.test_ft8 import fake_jt9, until

CTY = """Spain:   14:  37:  EU:   40.32:     3.43:    -1.0:  EA:
    EA,EB;
France:  14:  27:  EU:   46.00:    -2.00:    -1.0:  F:
    F;
England: 14:  27:  EU:   52.77:     1.47:     0.0:  G:
    G,M;
Fed. Rep. of Germany: 14: 28: EU: 51.00: -10.00: -1.0: DL:
    DA,DB,DC,DD,DF,DG,DJ,DK,DL;
Japan:   25:  45:  AS:   36.40:  -138.38:    -9.0:  JA:
    JA;
"""


def test_heard_against_the_logbook(tmp_path, monkeypatch):
    from ic705suite.apps.log.core.services import OperatorService, QsoService, SettingsService
    from ic705suite.apps.log.db import session

    fake_jt9(monkeypatch)
    table = geo.Cty(CTY)
    monkeypatch.setattr(geo, "cty", lambda: table)
    shared = f"sqlite:///{tmp_path / 'shared.sqlite3'}"
    monkeypatch.setenv(shared_logbook.VARIABLE, shared)
    session.dispose()
    session.init_engine(shared)
    operator = OperatorService.create("EA7KLX", "Manuel")
    state = SettingsService.load_state()
    state.operator_id = operator.id
    SettingsService.save_state(state)
    QsoService.log({"call": "DL1ABC", "mode": "FT8", "band": "20m", "gridsquare": "JO62"},
                   state)                                       # worked before on 20 m
    session.dispose()
    config = Config(connection="sim", swr_web=False, callsign="EA7KLX", locator="IM77AH",
                    database_url=f"sqlite:///{tmp_path / 'suite.db'}",
                    logbook_database=shared)

    async def scenario():
        link = LocalLink(None, config)
        await link.connect()
        cycles: list[dict] = []
        link.attach(lambda e: e.get("type") == "ft8.cycle" and e["decodes"] and cycles.append(e))
        try:
            await link.call("ft8.band", band="20m")
            await link.call("ft8.start")
            await until(lambda: link.core.ft8.worked is not None)
            await until(lambda: any(len(c["decodes"]) >= 3 for c in cycles))
            got = {d["sender"]: d for c in cycles for d in c["decodes"]}
            assert got["DL1ABC"]["b4"] and got["DL1ABC"]["dxcc"] == "worked"
            assert got["DL1ABC"]["country"] == "Fed. Rep. of Germany"
            assert got["DL1ABC"]["approx"]                 # no grid in "EA7KLX DL1ABC -12"
            ea1 = got["EA1ABC"]                            # CQ EA1ABC IN73
            assert ea1["country"] == "Spain" and not ea1.get("approx")
            assert (ea1["km"], ea1["az"]) == (round(geo.distance_km(
                geo.locator_to_latlon("IM77AH"), geo.locator_to_latlon("IN73"))), round(
                geo.bearing_deg(geo.locator_to_latlon("IM77AH"),
                                geo.locator_to_latlon("IN73"))))
            assert ea1["dxcc"] == "new" and ea1["grid_state"] == "new"
            assert mark(ea1)[0] == "NEW DXCC" and mark(got["DL1ABC"])[0] == "B4"
            assert where(ea1).startswith("Spain ") and where(got["DL1ABC"]).count("~") == 1
            line = next(cells for _s, d, cells, _st in rows(cycles, False, "EA7KLX")
                        if d and d.get("sender") == "EA1ABC")
            assert line[4] == "CQ EA1ABC IN73" and line[6] == "NEW DXCC"
            summary = await link.call("ft8.worked")
            assert summary["grids"] == {"JO62": "worked"} and summary["locator"] == "IM77AH"
            assert summary["lat"] == geo.locator_to_latlon("IM77AH")[0] and summary["logbook"]
        finally:
            await link.close()

    asyncio.run(scenario())
    session.dispose()
