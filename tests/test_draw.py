"""Tests for drawing the walk as a plan."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from enodia.draw import HERE, LOST, UNSURE, WATER, Frame, live_map, svg_map
from enodia.fingerprint import Fingerprint, Location, Place
from enodia.netlog import LogRecord, SeenNetwork
from enodia.reconcile import Estimate, PlacedNetwork, PlacedScan, Position, Reconciliation, Waypoint
from enodia.streets import Street, StreetMap, distance_metres

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


def test_the_names_of_the_pinned_networks_are_written_only_when_asked():
    # Small type beside the dot, in the dot's colour. A ring gets no name,
    # since it marks where a network was heard from and not where it is, and
    # a hidden network has none to write. Markup in a name cannot break the
    # file, and neither can a control character, which XML refuses outright.
    middle = (-34.9060, -56.1894)
    drawn = [
        network("Casa", middle),
        network("<b>&", (-34.9060, -56.1896)),
        network("", (-34.9060, -56.1892)),
        network("Lejos", (-34.9060, -56.1890), spread=35.0, plain=40.0),
        network("Libre\x01Bar", (-34.9060, -56.1898), security="open"),
    ]
    quiet = svg_map(walked(drawn))
    assert quiet is not None and 'font-size="6"' not in quiet
    named = svg_map(walked(drawn), names=True)
    assert named is not None and named.count('font-size="6"') == 3
    assert '#b8442a">Casa</text>' in named
    assert "&lt;b&gt;&amp;</text>" in named
    assert '#1f7a4d">Libre Bar</text>' in named
    assert "Lejos" not in named


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


# --- the neighbourhood --------------------------------------------------------

AROUND = (
    (-34.9068, -56.1908),
    (-34.9068, -56.1880),
    (-34.9052, -56.1880),
    (-34.9052, -56.1908),
    (-34.9068, -56.1908),
)


def angles(picture):
    return [float(one) for one in re.findall(r"rotate\((-?[0-9.]+) ", picture)]


def test_the_neighbourhood_is_drawn_from_the_water_up():
    streets = StreetMap(
        streets=(Street("Rivera", CORNERS),),
        water=((AROUND, ((-34.9062, -56.1898), (-34.9062, -56.1896), (-34.9061, -56.1896))),),
        rivers=(((-34.9066, -56.1905), (-34.9066, -56.1885)),),
        parks=((((-34.9058, -56.1898), (-34.9058, -56.1896), (-34.9057, -56.1896)),),),
        roads=(Street("Rivera", CORNERS), Street("Otra", ((-34.9064, -56.19), (-34.9056, -56.19)))),
    )
    picture = svg_map(walked(streets=streets))
    assert picture is not None
    water = picture.index(f'fill="{WATER}" fill-rule="evenodd"')
    park = picture.index('fill="#cdebb0"')
    road = picture.index('stroke="#fdfcfa"')
    assert water < park < road  # agua, parques, calles, de abajo hacia arriba
    assert picture.count(" Z") >= 3  # the shore and its island in one even-odd path
    assert f'stroke="{WATER}" stroke-width="4"' in picture  # the river as a line
    assert "\u00a9 OpenStreetMap contributors" in picture


def test_an_area_that_fills_the_frame_is_drawn_though_no_corner_of_it_is_inside():
    lake = (((-35.0, -56.3), (-35.0, -56.1), (-34.8, -56.1), (-34.8, -56.3)),)
    picture = svg_map(walked(streets=StreetMap(water=(lake,))))
    assert picture is not None and f'fill="{WATER}"' in picture
    away = (((-35.0, -56.3), (-35.0, -56.29), (-34.99, -56.29)),)
    elsewhere = svg_map(walked(streets=StreetMap(water=(away,))))
    assert elsewhere is not None and f'fill="{WATER}"' not in elsewhere


def test_the_credit_is_there_only_when_something_of_openstreetmap_is():
    assert "OpenStreetMap" not in (svg_map(walked()) or "")
    assert "OpenStreetMap" not in (svg_map(walked(streets=StreetMap())) or "")
    assert "OpenStreetMap" in (svg_map(walked(streets=StreetMap((Street("R", CORNERS),)))) or "")


def test_a_street_name_never_reads_upside_down():
    # Drawn east to west, and steeply north to south: the text turns with the
    # street, and then by half a turn whenever it would have read backwards.
    west = Street("Hacia el oeste", ((-34.9060, -56.1880), (-34.9060, -56.1908)))
    south = Street("Hacia el sur", ((-34.9050, -56.1899), (-34.9070, -56.1895)))
    north = Street("Hacia el norte", ((-34.9070, -56.1895), (-34.9050, -56.1899)))
    picture = svg_map(walked(streets=StreetMap(roads=(west, south, north))))
    assert picture is not None
    turned = angles(picture)
    assert len(turned) == 6  # each name twice: its halo, then the name
    assert all(-90 < angle <= 90 for angle in turned)
    assert turned[0] == pytest.approx(0.0, abs=0.1)
    assert turned[2] == pytest.approx(turned[4], abs=0.1)  # one street, either way round


def test_a_long_street_is_named_every_so_often_and_not_on_every_block():
    # A kilometre in straight blocks of a hundred metres: a name per block
    # would be ten, and one every 250 m is four.
    blocks = tuple((-34.9060, -56.1900 + step * 0.0011) for step in range(11))
    long_one = Street("Larga", blocks)
    frame_wide = walked(streets=StreetMap(roads=(long_one,)), crossings=(blocks[0], blocks[-1]))
    picture = svg_map(frame_wide)
    assert picture is not None
    assert picture.count(">Larga</text>") == 2 * 4


def test_a_name_too_long_for_its_block_is_left_off_it():
    short = Street("Un nombre larguísimo para una cuadra", CORNERS)
    tiny = ((-34.9060, -56.1900), (-34.9060, -56.18995))
    picture = svg_map(walked(streets=StreetMap(roads=(Street("Corta", tiny), short))))
    assert picture is not None
    assert ">Corta</text>" not in picture


def test_without_the_neighbourhood_the_walked_streets_are_the_ones_named():
    picture = svg_map(walked(streets=StreetMap((Street("Rivera", CORNERS),))))
    assert picture is not None and ">Rivera</text>" in picture


def test_the_frame_shows_the_neighbourhood_around_even_a_short_walk():
    frame = Frame.around(CORNERS)
    assert distance_metres(frame.south, frame.west, CORNERS[0][0], frame.west) >= 199
    assert Frame(0.0, 0.0, 0.0, 0.0, 100.0, 100.0, 10.0).pixels(50) == 0.0


# --- the live map ---------------------------------------------------------------


def fingerprints():
    return [
        Fingerprint(Place("C0", "C1", step / 4, lat, lon), ())
        for step, (lat, lon) in enumerate((-34.9060, -56.1900 + step * 0.0003) for step in range(5))
    ] + [Fingerprint(Place("C0", "C1", 0.5), ())]


HERE_NOW = (-34.9060, -56.1894)


def found(fraction=0.5, alternative=None, scattered_m=None):
    here = Place("C0", "C1", fraction, -34.9060, -56.1894)
    return Location(here, 0.8, 2, 0.1, alternative=alternative, scattered_m=scattered_m)


def dot(page):
    """The colour of the large dot: where the page says you are."""
    (colour,) = re.findall(r'r="7" fill="(#[0-9a-f]+)"', page)
    return colour


def test_the_live_map_reloads_itself_and_says_where_you_are():
    page = live_map(fingerprints(), found(), [HERE_NOW], "17:02:03  C0 to <C1>", None, 5)
    assert page.startswith("<!DOCTYPE html>")
    assert 'content="5"' in page
    assert "17:02:03  C0 to &lt;C1&gt;" in page
    assert dot(page) == HERE
    assert page.count('r="1.6"') == 5  # the fingerprints that have a place, as the grid walked
    assert "OpenStreetMap" not in page
    assert 'content="1"' in live_map(fingerprints(), found(), [], "x", None, 0)


def test_an_unsettled_answer_is_drawn_apart_with_the_spread_of_its_evidence():
    other = Place("C1", "C2", 0.5)
    page = live_map(fingerprints(), found(alternative=other, scattered_m=30.0), [HERE_NOW], "x")
    assert dot(page) == UNSURE
    assert 'fill-opacity="0.12"' in page  # the ring of how far apart the evidence lay


def test_lost_the_page_keeps_the_last_place_it_knew_greyed_and_a_trail_behind():
    trail = [(-34.9060, -56.1900 + step * 0.0002) for step in range(4)]
    page = live_map(fingerprints(), None, trail, "not on the map")
    assert dot(page) == LOST
    assert page.count("<line") - 1 == 3  # three steps of trail, and the scale bar
    empty = live_map(fingerprints(), None, [], "not on the map")
    assert 'r="7"' not in empty


def test_the_live_map_draws_the_neighbourhood_and_credits_it():
    streets = StreetMap(roads=(Street("Rivera", CORNERS),))
    page = live_map(fingerprints(), found(), [], "x", streets)
    assert ">Rivera</text>" in page and "OpenStreetMap" in page


def test_the_live_map_zooms_and_keeps_its_zoom_across_the_reload():
    # The page is written again and reloaded every cycle, so the zoom lives in
    # the address, which a reload keeps. Without script it still reloads.
    page = live_map(fingerprints(), found(), [HERE_NOW], "x", None, 5)
    assert '<noscript><meta http-equiv="refresh" content="5"></noscript>' in page
    assert "location.reload()" in page and "}, 5000);" in page
    assert "location.hash" in page and "history.replaceState" in page
    for button in ('id="in"', 'id="out"', 'id="all"', 'id="follow"'):
        assert button in page, button
    # Where you are, for the view to follow you to.
    (here,) = re.findall(r'data-here="([0-9.]+),([0-9.]+)"', page)
    assert f'cx="{here[0]}" cy="{here[1]}" r="7"' in page
    assert 'data-here="' not in live_map(fingerprints(), None, [], "not on the map")


def test_the_live_map_credits_openstreetmap_outside_the_picture():
    # Inside the SVG a zoom would carry it off the screen.
    streets = StreetMap(roads=(Street("Rivera", CORNERS),))
    page = live_map(fingerprints(), found(), [], "x", streets)
    assert '<div class="credit">\u00a9 OpenStreetMap contributors</div>' in page
    assert page.index("</svg>") < page.index('class="credit"')
