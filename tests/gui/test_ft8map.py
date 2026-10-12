"""The FT8 map: the sun and the night, stations placed and kept a while, the mouse."""

import calendar
import time

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from ic705suite.gui import ft8map


def test_the_sun_and_the_night():
    equinox_noon = calendar.timegm((2026, 3, 20, 12, 0, 0))
    decl, lon = ft8map.subsolar(equinox_noon)
    assert abs(decl) < 1 and abs(lon) < 5                    # over Greenwich, at noon
    solstice = calendar.timegm((2026, 6, 21, 18, 0, 0))
    decl, lon = ft8map.subsolar(solstice)
    assert 23 < decl < 23.6 and -95 < lon < -85                # the tropic, over America
    night = ft8map.night_polygon(solstice)
    assert night.last().y() == -90                             # the south pole in the dark


def decode(call, lat, lon, kind="cq", approx=False, **more):
    return {"sender": call, "message": f"CQ {call}", "kind": kind, "lat": lat, "lon": lon,
            "approx": approx, "snr": -10, **more}


def test_stations_on_the_map(qapp):
    view = ft8map.MapView()
    view.resize(900, 450)
    view.set_home(37.31, -5.96, "IM77AH")
    view.add_decodes(1000.0, [decode("MW3FLI", 53.5, -3.0),
                              decode("ZS6ABC", -29.0, 25.0, approx=True),
                              decode("EA7KLX", 37.3, -5.9, kind="tx")])
    assert set(view.heard) == {"MW3FLI", "ZS6ABC"}             # not our own messages
    view.add_decodes(1015.0, [decode("MW3FLI", 52.0, -1.0, approx=True)])
    assert view.heard["MW3FLI"].lat == 53.5                    # its grid's place kept
    view.heard["ZS6ABC"].seen = time.time() - ft8map.HEARD_FOR - 1
    view.add_decodes(1030.0, [])
    assert "ZS6ABC" not in view.heard                          # gone after a while


def test_a_click_chooses_and_a_double_click_answers(qapp):
    view = ft8map.MapView()
    view.resize(900, 450)
    view.show()
    view.set_home(37.31, -5.96, "IM77AH")
    view.add_decodes(1000.0, [decode("MW3FLI", 53.5, -3.0)])
    qapp.processEvents()
    view.repaint()                                  # where the stations are drawn: clickable
    chosen, answered = [], []
    view.station_clicked.connect(lambda start, d: chosen.append((start, d["sender"])))
    view.station_double_clicked.connect(lambda start, d: answered.append(d["sender"]))
    spot = view._xy(53.5, -3.0).toPoint()
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=spot)
    QTest.mouseDClick(view, Qt.MouseButton.LeftButton, pos=spot)
    assert chosen[0] == (1000.0, "MW3FLI") and answered == ["MW3FLI"]
    lat, lon = view._latlon(QPointF(spot))
    assert abs(lat - 53.5) < 1 and abs(lon + 3.0) < 1
    zoom = view.zoom
    view.zoom *= 2
    assert view._scale() > 0 and view.zoom == zoom * 2
    view.close()


def test_arrows_the_way_each_call_goes(qapp):
    """Our call goes out to the station we call; a station calling us comes in: each has
    its path, and the frames run only while there are arrows and the map is shown."""
    view = ft8map.MapView()
    view.resize(900, 450)
    view.mycall = "EA7KLX"
    view.set_home(37.31, -5.96, "IM77AH")
    view.add_decodes(1000.0, [decode("MW3FLI", 53.5, -3.0),
                              decode("DL1ABC", 51.0, 10.0, kind="call", to="EA7KLX",
                                     message="EA7KLX DL1ABC JO61"),
                              decode("F5ABC", 46.0, 2.0, kind="call", to="G4XYZ")])
    assert view.paths() == [("DL1ABC", False)]               # calling us: towards us
    view.set_them("MW3FLI")
    assert sorted(view.paths()) == [("DL1ABC", False), ("MW3FLI", True)]
    assert not view._frames.isActive()                        # not shown: no frames
    view.show()
    qapp.processEvents()
    assert view._frames.isActive()
    view.grab()                                               # draws (arrows and key)
    still = view._background
    view.grab()
    assert view._background is still                          # the world drawn once
    view.zoom = 2.0
    view.grab()
    assert view._background is not still                      # a new view: drawn again
    view.heard["DL1ABC"].seen -= ft8map.CALLING_FOR + 1       # its call went by
    view.set_them("")
    assert view.paths() == []
    view.grab()
    assert not view._frames.isActive()
    view.hide()


def test_the_key_says_what_is_under_the_mouse(qapp):
    view = ft8map.MapView()
    view.resize(900, 450)
    view.set_home(37.31, -5.96, "IM77AH")
    view.add_decodes(1000.0, [decode("MW3FLI", 53.5, -3.0, country="Wales", km=1814, az=6)])
    view.grab()
    spot = view._spots[0][0].center()
    assert view.under(spot) == "MW3FLI · Wales · 1814 km 6° · -10 dB"
    elsewhere = view.under(view._xy(37.31, -5.96))
    assert elsewhere.startswith("IM77AH · 0 km ")                 # our own square


def test_the_lines_go_when_the_qso_is_completed(qapp):
    view = ft8map.MapView()
    view.resize(900, 450)
    view.mycall = "EA7KLX"
    view.set_home(37.31, -5.96, "IM77AH")
    view.add_decodes(1000.0, [decode("DL1ABC", 51.0, 10.0, kind="call", to="EA7KLX",
                                     report="R-07")])
    view.set_them("DL1ABC")
    assert view.paths() == [("DL1ABC", True)]
    view.add_decodes(1030.0, [decode("DL1ABC", 51.0, 10.0, kind="call", to="EA7KLX",
                                     end="RR73")])
    view.set_them("")                                   # our QSO over
    assert view.paths() == []                           # their RR73: no arrows to us
    view.add_decodes(1045.0, [decode("G4XYZ", 52.0, -1.0, kind="call", to="EA7KLX",
                                     report="-05")])
    view.set_them("G4XYZ")
    view.qso_done("G4XYZ")
    assert view.paths() == []                           # completed: its lines go at once
    time.sleep(0.01)
    view.add_decodes(1060.0, [decode("G4XYZ", 52.0, -1.0, kind="call", to="EA7KLX",
                                     grid="IO91")])
    assert view.paths() == [("G4XYZ", False)]           # calling again: shown again
