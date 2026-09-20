"""Tests for the streets as OpenStreetMap draws them, and for walking along one."""

import json

import pytest

from enodia.reconcile import Position, Waypoint, block_line, reconcile
from enodia.streets import (
    Street,
    StreetMap,
    distance_metres,
    line_length_m,
    nearest_vertex,
    point_along,
    read_streets,
    write_streets,
)

# A block that bends north and comes back: the chord misses its middle by 78 m.
BEND = (
    (-34.9000, -56.2000),
    (-34.8995, -56.1990),
    (-34.8993, -56.1975),
    (-34.8995, -56.1960),
    (-34.9000, -56.1950),
)
CURVE = StreetMap((Street("Curva", BEND),))


def test_a_drawn_line_is_measured_along_its_bends_and_not_across_them():
    assert line_length_m(BEND) > distance_metres(*BEND[0], *BEND[-1])
    assert line_length_m(BEND) == pytest.approx(491, abs=5)
    assert line_length_m(BEND[:1]) == 0.0


def test_a_point_along_a_line_is_found_by_distance_walked_not_by_vertex():
    assert point_along(BEND, 0.0) == BEND[0]
    assert point_along(BEND, 1.0) == BEND[-1]
    middle = point_along(BEND, 0.5)
    assert middle[0] == pytest.approx(-34.8993, abs=0.0002)  # sobre la curva, no en la cuerda
    assert point_along(BEND, -1.0) == BEND[0] and point_along(BEND, 2.0) == BEND[-1]


def test_a_line_of_one_point_or_no_length_is_that_point():
    assert point_along([(-34.9, -56.2)], 0.5) == (-34.9, -56.2)
    assert point_along([(-34.9, -56.2), (-34.9, -56.2)], 0.5) == (-34.9, -56.2)


def test_the_nearest_vertex_is_found_with_how_far_off_it_was():
    where, gap = nearest_vertex(BEND, (-34.8993, -56.1975))
    assert where == 2 and gap == pytest.approx(0, abs=0.5)


def test_the_block_between_two_crossings_is_the_run_of_the_way_between_them():
    drawn = CURVE.between(BEND[0], BEND[-1])
    assert drawn is not None
    assert drawn[0] == BEND[0] and drawn[-1] == BEND[-1]
    assert line_length_m(drawn) == pytest.approx(491, abs=5)


def test_a_block_walked_the_other_way_round_comes_out_the_other_way_round():
    drawn = CURVE.between(BEND[-1], BEND[0])
    assert drawn is not None and drawn[0] == BEND[-1] and drawn[-1] == BEND[0]


def test_a_crossing_nowhere_near_the_drawing_falls_back_to_the_chord():
    assert CURVE.between((-34.95, -56.30), BEND[-1]) is None
    assert CURVE.between(BEND[0], (-34.95, -56.30)) is None


def test_two_crossings_at_the_same_vertex_have_no_block_between_them():
    assert CURVE.between(BEND[0], BEND[0]) is None


def test_a_street_drawn_as_one_point_is_no_help():
    assert StreetMap((Street("Punto", ((-34.9, -56.2),)),)).between(BEND[0], BEND[-1]) is None


def test_a_way_that_loops_the_long_way_round_is_refused_rather_than_followed():
    # The two crossings are 20 m apart but the way between them runs a kilometre.
    detour = (
        (-34.90000, -56.20000),
        (-34.89500, -56.20000),
        (-34.89500, -56.19500),
        (-34.90018, -56.20000),
    )
    assert StreetMap((Street("Vuelta", detour),)).between(detour[0], detour[-1]) is None


def test_the_closest_drawing_wins_when_more_than_one_could_serve():
    near = Street("Cerca", (BEND[0], BEND[-1]))
    far = Street("Lejos", ((-34.90015, -56.20000), (-34.90015, -56.19500)))
    chosen = StreetMap((far, near)).between(BEND[0], BEND[-1])
    assert chosen is not None and len(chosen) == 4  # los dos cruces más los dos vértices


# --- the file -----------------------------------------------------------------


def test_the_drawn_streets_survive_a_round_trip_through_the_file(tmp_path):
    path = tmp_path / "calles.jsonl"
    assert write_streets(path, [Street("Curva", BEND)]) == 1
    back = read_streets(path)
    assert len(back) == 1
    assert back.streets[0].name == "Curva"
    assert back.streets[0].length_m == pytest.approx(line_length_m(BEND), abs=1)
    assert json.loads(path.read_text(encoding="utf-8"))["street"] == "Curva"


def test_a_missing_streets_file_reads_as_no_streets_at_all(tmp_path):
    assert len(read_streets(tmp_path / "todavia-no.jsonl")) == 0


def test_a_line_of_the_streets_file_that_is_not_a_street_is_skipped(tmp_path):
    path = tmp_path / "calles.jsonl"
    path.write_text(
        "\n"
        "no es json\n"
        "[1, 2]\n"
        '{"street": 7, "line": []}\n'
        '{"street": "Sin trazo", "line": "no es una lista"}\n'
        '{"street": "Vacia", "line": []}\n'
        '{"street": "Buena", "line": [[-34.9, -56.2], "torcido", [-34.9, -56.19]]}\n'
    )
    back = read_streets(path)
    assert len(back) == 1 and back.streets[0].name == "Buena"
    assert len(back.streets[0].line) == 2  # el punto torcido no entró


# --- what it changes about a reconciliation -----------------------------------


def wp(minute, name, place=None):
    from datetime import datetime, timedelta, timezone

    tz = timezone(timedelta(hours=-3))
    lat, lon = place if place else (None, None)
    return Waypoint(datetime(2026, 9, 5, 17, minute, tzinfo=tz), name, lat, lon)


def test_a_position_without_a_drawing_stays_on_the_straight_line():
    here, there = wp(0, "A", BEND[0]), wp(4, "B", BEND[-1])
    plain = Position(here, there, 0.5)
    assert plain.coordinates == pytest.approx((-34.9000, -56.1975))
    assert plain.length_m == pytest.approx(distance_metres(*BEND[0], *BEND[-1]), abs=1)


def test_a_position_with_a_drawing_follows_the_street():
    here, there = wp(0, "A", BEND[0]), wp(4, "B", BEND[-1])
    drawn = Position(here, there, 0.5, block_line(here, there, CURVE))
    assert drawn.coordinates is not None
    assert drawn.coordinates[0] == pytest.approx(-34.8993, abs=0.0002)
    assert drawn.length_m == pytest.approx(491, abs=5)
    # And that is 78 m from where the chord would have put the same scan.
    plain = Position(here, there, 0.5).coordinates
    assert plain is not None
    assert distance_metres(*drawn.coordinates, *plain) == pytest.approx(78, abs=5)


def test_a_crossing_without_coordinates_has_no_block_to_draw():
    assert block_line(wp(0, "A"), wp(4, "B", BEND[-1]), CURVE) is None
    assert block_line(wp(0, "A", BEND[0]), wp(4, "B", BEND[-1]), None) is None


def test_a_whole_reconciliation_places_its_scans_along_the_street(tmp_path, monkeypatch):
    from enodia import netlog
    from enodia.netlog import NetworkLog

    stamps = iter(f"2026-09-05T17:0{minute}:00-03:00" for minute in (1, 2, 3))
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "paseo.jsonl")
    for _ in range(3):
        log.record_scan([])
    nb = tmp_path / "libreta.txt"
    nb.write_text(f"17:00 A @ {BEND[0][0]}, {BEND[0][1]}\n17:04 B @ {BEND[-1][0]}, {BEND[-1][1]}\n")
    plain = reconcile(log.path, nb, by_movement=False)
    drawn = reconcile(log.path, nb, by_movement=False, streets=CURVE)
    assert plain.streets is None and drawn.streets is CURVE
    middle_plain = plain.placed[1].position.coordinates
    middle_drawn = drawn.placed[1].position.coordinates
    assert middle_plain is not None and middle_drawn is not None
    assert middle_drawn[0] > middle_plain[0]  # sobre la curva, al norte de la cuerda


def test_the_buildings_go_into_the_file_and_come_back_out(tmp_path):
    path = tmp_path / "calles.jsonl"
    block = ((-34.9, -56.2), (-34.9, -56.199), (-34.8995, -56.199), (-34.9, -56.2))
    assert write_streets(path, [Street("Curva", BEND)], [block]) == 2
    back = read_streets(path)
    assert len(back) == 1 and len(back.buildings) == 1
    assert back.buildings[0] == block


def test_a_building_of_fewer_than_three_corners_is_not_a_building(tmp_path):
    path = tmp_path / "calles.jsonl"
    path.write_text('{"building": [[-34.9, -56.2], [-34.9, -56.19]]}\n{"building": "no"}\n')
    assert read_streets(path).buildings == ()


def test_a_coordinate_that_is_not_a_coordinate_is_dropped_and_not_crashed_on(tmp_path):
    # This file is written once and then lives beside the notebook, where it
    # gets copied about and opened in an editor. A string where a latitude
    # belongs used to end a whole reconciliation in a ValueError out of float().
    path = tmp_path / "calles.jsonl"
    path.write_text(
        '{"street": "Buena", "line": [[-34.9, -56.2], ["oops", -56.19], [-34.89, -56.19]]}\n'
        '{"street": "Fuera del mundo", "line": [[-34.9, -56.2], [-91.0, -56.19]]}\n'
        '{"street": "No numero", "line": [[NaN, -56.2], [-34.9, -56.19]]}\n'
        '{"street": "Media", "line": [[-34.9, -56.2], [-34.89]]}\n'
    )
    back = read_streets(path)
    # Only the one that still has two real points left is a line at all.
    assert [street.name for street in back.streets] == ["Buena"]
    assert back.streets[0].line == ((-34.9, -56.2), (-34.89, -56.19))


def test_a_streets_file_that_cannot_be_read_is_not_a_map_with_no_streets_in_it(tmp_path):
    # A file that is not there is no geometry, which is the ordinary case: it is
    # what --streets names before --geocode has written it. Every other failure
    # is raised, because failing to read the streets is not evidence that there
    # are none, and an empty map places every block on the chord and looks
    # exactly like a good answer.
    assert len(read_streets(tmp_path / "todavia-no.jsonl")) == 0
    folder = tmp_path / "calles.jsonl"
    folder.mkdir()
    with pytest.raises(OSError):
        read_streets(folder)
