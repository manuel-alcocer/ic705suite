"""Where a station is (locators, cty.dat), and what the logbook has worked of it."""

import pytest

from ic705suite import geo, worked

CTY = """Wales:                    14:  27:  EU:   52.28:     3.73:     0.0:  GW:
    2W,GC,GW,MW;
Spain:                    14:  37:  EU:   40.32:     3.43:    -1.0:  EA:
    AM,AN,AO,EA,EB,EC,ED,EE,EF,EG,EH,=EA7KLX(14)<37.3/5.9>;
France:                   14:  27:  EU:   46.00:    -2.00:    -1.0:  F:
    F,HW,HX,HY,TH,TM,TO,TP,TQ,TV,TW,TX;
Canary Islands:           33:  36:  AF:   28.32:    15.85:     0.0:  EA8:
    AM8,AN8,AO8,EA8,EB8,EC8,ED8,EE8,EF8,EG8,EH8;
Argentina:                13:  14:  SA:  -34.80:    65.92:     3.0:  LU:
    AY,AZ,L1,L2,L3,L4,L5,L6,L7,L8,L9,LO,LP,LQ,LR,LS,LT,LU,LV,LW;
"""


@pytest.fixture
def cty(monkeypatch):
    table = geo.Cty(CTY)
    monkeypatch.setattr(geo, "cty", lambda: table)
    return table


def test_locators():
    lat, lon = geo.locator_to_latlon("IM77ah")
    assert (round(lat, 3), round(lon, 3)) == (37.312, -5.958)
    assert geo.latlon_to_locator(lat, lon) == "IM77AH"
    assert geo.locator_to_latlon("IM77") == (37.5, -5.0)
    assert geo.locator_to_latlon("RR73") is None and not geo.is_locator("RR73")
    assert geo.is_locator("io83") and not geo.is_locator("IM7")


def test_distance_and_bearing():
    home, wales = geo.locator_to_latlon("IM77AH"), geo.locator_to_latlon("IO83")
    assert round(geo.distance_km(home, wales)) == 1814
    assert round(geo.bearing_deg(home, wales)) == 6
    assert round(geo.bearing_deg((0, 0), (0, 10))) == 90
    path = geo.great_circle(home, wales, steps=8)
    assert len(path) == 9 and path[0] == pytest.approx(home) and path[-1] == pytest.approx(wales)


def test_cty_lookup(cty):
    assert cty.lookup("MW3FLI").name == "Wales"
    assert cty.lookup("EA8AT").name == "Canary Islands"            # the longest prefix
    assert cty.lookup("F/G0LRH").name == "France"                  # operating from France
    assert cty.lookup("LW2EDM").continent == "SA"
    assert cty.lookup("LW2EDM").lon == pytest.approx(-65.92)      # west positive in the file
    special = cty.lookup("EA7KLX")                                 # a whole call, overridden
    assert (special.lat, special.lon) == (37.3, -5.9)
    assert cty.lookup("EA7KLX/P").name == "Spain"
    assert cty.lookup("XX9XX") is None


def test_the_worked_index(cty):
    index = worked.WorkedIndex()
    index.add("MW3FLI", "20m", "IO83", "Wales", confirmed=True)
    index.add("F5RRS", "20m", "JN36", "France", confirmed=False)
    assert index.status("MW3FLI", "20m", "IO83") == {
        "b4": True, "b4_any": True, "dxcc": "confirmed", "dxcc_count": 1,
        "grid_state": "confirmed"}
    assert index.status("MW3FLI", "40m")["dxcc"] == "new_band"
    other = index.status("F6ABC", "20m", "JN18")
    assert other["dxcc"] == "worked" and not other["b4"] and other["grid_state"] == "new"
    assert index.status("LW2EDM", "20m")["dxcc"] == "new"
    index.add("F5ABC", "40m", "JN12", "France", confirmed=False)
    assert index.status("F6ABC", "20m")["dxcc_count"] == 2           # QSOs with France
    assert index.summary() == {"grids": {"IO83": "confirmed", "JN36": "worked",
                                         "JN12": "worked"},
                               "entities": {"Wales": "confirmed", "France": "worked"},
                               "qsos": 3}
