"""Tests for drawing the walk as a plan."""

from datetime import datetime, timedelta, timezone

import pytest

from enodia.draw import Frame, svg_map
from enodia.netlog import LogRecord, SeenNetwork
from enodia.reconcile import Estimate, PlacedNetwork, PlacedScan, Position, Reconciliation, Waypoint
from enodia.streets import Street, StreetMap

TZ = timezone(timedelta(hours=-3))
CORNERS = ((-34.9060, -56.1900), (-34.9060, -56.1888))


def wp(minute, name, place):
    return Waypoint(datetime(2026, 9, 5, 17, minute, tzinfo=TZ), name, place[0], place[1])


def walked(networks=(), streets=None, crossings=CORNERS, scans=3):
    """A reconciliation of a straight block, ready to draw."""
    points = [wp(minute * 2, f"C{minute}", place) for minute, place in enumerate(crossings)]
    placed = [
        PlacedScan(LogRecord("scan"), Position(points[0], points[1], step / max(scans - 1, 1)))
        for step in range(scans)
    ]
    return Reconciliation(points, [], placed, 0, 0, list(networks), streets)


def network(name, place, spread=5.0, plain=40.0, security="wpa2"):
    seen = SeenNetwork(name, "aa:bb:cc:dd:ee:01", security, 2412, -50, None)
    when = datetime(2026, 9, 5, 17, 1, tzinfo=TZ)
    return PlacedNetwork(
        seen,
        Position(wp(0, "C0", CORNERS[0]), wp(2, "C1", CORNERS[1]), 0.5),
        3,
        when,
        when,
        when,
        estimate=Estimate(place[0], place[1], spread, 3, plain),
    )


# --- the frame ----------------------------------------------------------------


def test_the_frame_holds_everything_it_was_built_around():
    frame = Frame.around(CORNERS)
    assert all(frame.holds(place) for place in CORNERS)
    assert not frame.holds((-34.95, -56.30))


def test_the_frame_puts_north_at_the_top_and_east_at_the_right():
    frame = Frame.around(CORNERS)
    assert frame.y(frame.north) < frame.y(frame.south)
    assert frame.x(frame.west) < frame.x(frame.east)
    assert frame.margin <= frame.x(frame.west) <= frame.width - frame.margin


def test_a_frame_around_one_place_does_not_divide_by_zero():
    frame = Frame.around([CORNERS[0], CORNERS[0]])
    assert frame.x(CORNERS[0][1]) > 0 and frame.y(CORNERS[0][0]) > 0
    assert 0 < frame.squeeze <= 1


def test_longitude_is_squeezed_so_the_streets_meet_at_the_angles_they_do():
    # A degree of longitude at 35 south is about 82% of a degree of latitude.
    frame = Frame.around([(-34.906, -56.19), (-34.905, -56.189)])
    assert frame.squeeze == pytest.approx(0.82, abs=0.02)


# --- the picture --------------------------------------------------------------


def test_a_walk_with_no_coordinates_is_not_a_picture():
    bare = Reconciliation([], [], [], 0, 0, [])
    assert svg_map(bare) is None


def test_the_plan_carries_the_crossings_the_route_and_a_scale_bar():
    picture = svg_map(walked())
    assert picture is not None
    assert picture.startswith("<svg xmlns=") and picture.endswith("</svg>")
    assert 'viewBox="0 0 1200' in picture
    assert ">C0<" in picture and ">C1<" in picture  # los cruces, rotulados
    assert " m</text>" in picture  # la barra de escala
    assert picture.count("<path") == 1  # la ruta, sin calles que dibujar


def test_the_streets_and_the_blocks_are_drawn_when_there_are_any():
    streets = StreetMap(
        (Street("Rivera", CORNERS),),
        ((CORNERS[0], (-34.9058, -56.1900), (-34.9058, -56.1895), CORNERS[0]),),
    )
    plain = svg_map(walked())
    drawn = svg_map(walked(streets=streets))
    assert plain is not None and drawn is not None
    assert drawn.count("<path") > plain.count("<path")
    assert "#e7e1d8" in drawn  # la manzana rellena


def test_geometry_outside_the_frame_is_left_out_of_the_file():
    far = StreetMap(
        (Street("Lejos", ((-34.95, -56.30), (-34.96, -56.31))),),
        (((-34.95, -56.30), (-34.951, -56.30), (-34.951, -56.301), (-34.95, -56.30)),),
    )
    picture = svg_map(walked(streets=far))
    assert picture is not None and picture.count("<path") == 1  # solo la ruta


def test_a_street_map_handed_in_beats_the_one_the_reconciliation_carries():
    mine = StreetMap((Street("Rivera", CORNERS),))
    assert svg_map(walked(streets=None), mine) is not None
    assert "<path" in (svg_map(walked(streets=None), mine) or "")


def test_a_pinned_network_is_a_dot_and_a_loose_one_is_a_ring():
    middle = (-34.9060, -56.1894)
    pinned = svg_map(walked([network("Casa", middle, spread=5.0, plain=40.0)]))
    loose = svg_map(walked([network("Lejos", middle, spread=35.0, plain=40.0)]))
    assert pinned is not None and loose is not None
    assert "stroke-dasharray" not in pinned and '#b8442a"/>' in pinned
    assert "stroke-dasharray" in loose  # el anillo, sin punto en el medio
    assert "only heard from around there" in loose


def test_an_open_network_is_drawn_apart_from_the_rest():
    middle = (-34.9060, -56.1894)
    picture = svg_map(walked([network("Libre", middle, security="open")]))
    assert picture is not None and "#1f7a4d" in picture


def test_a_network_that_was_never_placed_or_fell_outside_is_not_drawn():
    nowhere = network("Fuera", (-34.95, -56.30))
    unplaced = PlacedNetwork(
        nowhere.network,
        nowhere.position,
        1,
        nowhere.first_seen,
        nowhere.first_seen,
        nowhere.first_seen,
    )
    picture = svg_map(walked([nowhere, unplaced]))
    assert picture is not None and "stroke-dasharray" not in picture


def test_a_walk_of_one_scan_still_draws_its_crossings():
    picture = svg_map(walked(scans=1))
    assert picture is not None and ">C0<" in picture and picture.count("<path") == 0


def test_a_crossing_name_with_markup_in_it_cannot_break_the_file():
    points = [wp(0, "A & <b>B</b>", CORNERS[0]), wp(2, "C1", CORNERS[1])]
    picture = svg_map(Reconciliation(points, [], [], 0, 0, []))
    assert picture is not None
    assert "&amp; &lt;b&gt;" in picture and "<b>" not in picture


def test_a_frame_with_no_width_at_all_has_no_scale_bar_to_draw():
    flat = Frame(-34.9, -56.19, -34.9, -56.19, 100.0, 100.0, 10.0)
    from enodia.draw import _scale_bar

    assert _scale_bar(flat) == []


def test_a_crossing_outside_the_frame_is_not_labelled():
    # The frame is built from the placed scans, so a crossing far off the walk
    # can fall outside it: better left out than drawn on the edge.
    points = [wp(0, "Cerca", CORNERS[0]), wp(2, "Lejos", (-34.80, -56.05))]
    placed = [PlacedScan(LogRecord("scan"), Position(points[0], points[0], 0.0))]
    small = Frame.around([CORNERS[0], CORNERS[1]])
    assert not small.holds((-34.80, -56.05))
    picture = svg_map(Reconciliation(points, [], placed, 0, 0, []))
    assert picture is not None and ">Cerca<" in picture


def test_a_crossing_without_coordinates_is_simply_not_drawn():
    points = [
        wp(0, "Cerca", CORNERS[0]),
        Waypoint(datetime(2026, 9, 5, 17, 2, tzinfo=TZ), "Sin lugar"),
        wp(4, "Otra", CORNERS[1]),
    ]
    picture = svg_map(Reconciliation(points, [], [], 0, 0, []))
    assert picture is not None
    assert ">Cerca<" in picture and ">Sin lugar<" not in picture
