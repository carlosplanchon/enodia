"""The streets themselves, as OpenStreetMap draws them.

Placing a scan between two crossings means putting it some fraction of the way
along. Without the street that has to be a straight line from one corner to the
other, which is what `Position.coordinates` has always done and what the README
has always admitted: the two ends come out right and the middle drifts towards
the chord wherever the block bends.

The lookup already knows better. The one Overpass request `--geocode` sends
comes back with every way of every street the notebook names, node by node, and
that is the street drawn. Kept in a file beside the notebook it costs one more
flag and the middle of the block stops being a guess.

None of this is required. A reconciliation without a streets file behaves
exactly as it did, on the chord, and says so rather than pretending: a block
Enodia has no drawing of falls back, and so does one where the drawing cannot be
matched to the two crossings with any confidence.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from math import cos, hypot, radians
from pathlib import Path
from typing import Any

from enodia.netlog import number

EARTH_RADIUS_M = 6_371_000
NEAR_CROSSING_M = 25.0
# A drawing that runs three times the straight distance between two crossings is
# not the block between them: it is a way that loops, or the long way round a
# one-way pair. Better the chord than a confident detour.
LONGEST_DETOUR = 3.0

Place = tuple[float, float]
Line = tuple[Place, ...]


def distance_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points, flat-earth approximation: fine over a walk."""
    mean_lat = radians((lat1 + lat2) / 2)
    return EARTH_RADIUS_M * hypot(radians(lon2 - lon1) * cos(mean_lat), radians(lat2 - lat1))


def line_length_m(line: Sequence[Place]) -> float:
    """How far it is along a drawn line, corner to corner, the bends included."""
    return sum(distance_metres(*line[i], *line[i + 1]) for i in range(len(line) - 1))


def point_along(line: Sequence[Place], fraction: float) -> Place:
    """The place a given fraction of the way along a drawn line.

    By length walked and not by how many vertices there are, since a straight
    block is two vertices and a curved one is twenty, and the operator walked
    metres either way.
    """
    if len(line) < 2:
        return line[0]
    total = line_length_m(line)
    if total <= 0:
        return line[0]
    want = max(0.0, min(1.0, fraction)) * total
    walked = 0.0
    for index in range(len(line) - 1):
        here, there = line[index], line[index + 1]
        step = distance_metres(*here, *there)
        if walked + step >= want:
            share = (want - walked) / step if step > 0 else 0.0
            return (here[0] + (there[0] - here[0]) * share, here[1] + (there[1] - here[1]) * share)
        walked += step
    # Only reachable if the sum of the steps came out under the total it was
    # measured from, which is rounding and not geometry.
    return line[-1]  # pragma: no cover


def _project(place: Place, start: Place, end: Place) -> tuple[float, Place, float]:
    """Where a place falls along one segment: a fraction of it, the point there, and how far off.

    A flat frame around the segment, the same approximation `distance_metres`
    makes: fine over a block.
    """
    scale = cos(radians((start[0] + end[0]) / 2))
    run = ((end[1] - start[1]) * scale, end[0] - start[0])
    offset = ((place[1] - start[1]) * scale, place[0] - start[0])
    length2 = run[0] * run[0] + run[1] * run[1]
    along = 0.0
    if length2 > 0:
        along = max(0.0, min(1.0, (offset[0] * run[0] + offset[1] * run[1]) / length2))
    point = (start[0] + (end[0] - start[0]) * along, start[1] + (end[1] - start[1]) * along)
    return along, point, distance_metres(*place, *point)


def nearest_point(line: Sequence[Place], place: Place) -> tuple[tuple[int, float], Place, float]:
    """The point of a drawn line closest to a place: where along the line, the point, how far off.

    "Where along the line" is the segment and the fraction of it, which orders
    two such points the way the line runs. It is the nearest point and not the
    nearest vertex, because OpenStreetMap puts vertices where a way bends or
    meets another and nowhere along a straight run, so a mark halfway down a
    straight block is a hundred metres from the nearest vertex and right on
    the line. A place at a vertex is given as the start of the segment after
    it rather than the end of the one before, so that two places at one vertex
    compare equal.
    """
    best: tuple[tuple[int, float], Place, float] | None = None
    for index in range(len(line) - 1):
        along, point, off = _project(place, line[index], line[index + 1])
        if best is None or off < best[2]:
            best = ((index, along), point, off)
    assert best is not None  # a line has two points: `_chained` and `_places` see to it
    (index, along), point, off = best
    if along >= 1.0 and index < len(line) - 2:
        index, along = index + 1, 0.0
    return (index, along), point, off


@dataclass(frozen=True)
class Street:
    """One way of one street, drawn."""

    name: str
    line: Line

    @property
    def length_m(self) -> float:
        return line_length_m(self.line)


def _chained(streets: Sequence[Street]) -> tuple[Street, ...]:
    """The ways of each street that meet end to end, joined into one line apiece.

    OpenStreetMap starts a new way wherever a tag changes: the surface, the
    number of lanes, a bridge. A block whose two marks sit on different ways of
    one street is still one block, and looked for one way at a time it was
    never found and fell back to the chord without a word.
    """
    by_name: dict[str, list[Line]] = {}
    for street in streets:
        if len(street.line) >= 2:
            by_name.setdefault(street.name, []).append(tuple(street.line))
    chained = []
    for name, lines in by_name.items():
        joined = True
        while joined:
            joined = False
            for i, a in enumerate(lines):
                for j in range(i + 1, len(lines)):
                    b = lines[j]
                    if a[-1] == b[0]:
                        together = a + b[1:]
                    elif a[-1] == b[-1]:
                        together = a + b[::-1][1:]
                    elif a[0] == b[-1]:
                        together = b + a[1:]
                    elif a[0] == b[0]:
                        together = a[::-1] + b[1:]
                    else:
                        continue
                    lines[i] = together
                    del lines[j]
                    joined = True
                    break
                if joined:
                    break
        chained.extend(Street(name, line) for line in lines)
    return tuple(chained)


@dataclass(frozen=True)
class StreetMap:
    """What the lookup drew: the streets, and the buildings they run between."""

    streets: tuple[Street, ...] = ()
    buildings: tuple[Line, ...] = ()

    def __len__(self) -> int:
        return len(self.streets)

    @cached_property
    def runs(self) -> tuple[Street, ...]:
        """The streets with their ways chained end to end: what a block is looked for on."""
        return _chained(self.streets)

    def between(self, here: Place, there: Place, within_m: float = NEAR_CROSSING_M) -> Line | None:
        """The block from one mark to the next, as drawn, or None to use the chord.

        Matched on the geometry and never on the marks' names. A notebook
        writes "Agraciada y Freire" and the street is called "Avenida Agraciada",
        the same corner turns up spelled two ways, and a mark may have been
        typed in by hand rather than looked up. Two coordinates and a drawing
        need none of that: the block is the run of the way between the point
        nearest one mark and the point nearest the other, cut there, whether or
        not either is a vertex.
        """
        chord = distance_metres(*here, *there)
        best: tuple[float, Line] | None = None
        for street in self.runs:
            at_here, point_here, off_here = nearest_point(street.line, here)
            at_there, point_there, off_there = nearest_point(street.line, there)
            if at_here == at_there or off_here > within_m or off_there > within_m:
                continue
            forwards = at_here < at_there
            (first, start), (last, end) = sorted(((at_here, point_here), (at_there, point_there)))
            # The vertices strictly inside the run: the end points are the
            # projections themselves, so a vertex one of them sits on is not
            # written twice.
            stop = last[0] + (1 if last[1] > 0.0 else 0)
            run = (start, *street.line[first[0] + 1 : stop], end)
            drawn = (here, *(run if forwards else run[::-1]), there)
            if chord > 0 and line_length_m(drawn) > chord * LONGEST_DETOUR:
                continue
            if best is None or off_here + off_there < best[0]:
                best = (off_here + off_there, drawn)
        return None if best is None else best[1]


def _drawn(line: Line) -> list[list[float]]:
    return [[round(lat, 7), round(lon, 7)] for lat, lon in line]


def write_streets(
    path: str | Path, streets: Iterable[Street], buildings: Iterable[Line] = ()
) -> int:
    """Keep what OpenStreetMap drew beside the notebook, one shape per line.

    Coordinates inline rather than node references, so the file stands on its
    own: it is read back without asking OpenStreetMap anything, which is the
    whole point of having asked once.
    """
    written = 0
    with Path(path).open("w", encoding="utf-8") as out:
        for street in streets:
            row = {"street": street.name, "line": _drawn(street.line)}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
        for building in buildings:
            out.write(json.dumps({"building": _drawn(building)}) + "\n")
            written += 1
    return written


def read_streets(path: str | Path) -> StreetMap:
    """The drawn streets back out of the file, skipping anything that is not one.

    A file that is not there is no geometry, which is the ordinary case: it is
    what `--streets` names before `--geocode` has written it. Every other way a
    read can fail is raised, because a failure to read the streets is not
    evidence that there are no streets. Swallowing a permission error here hands
    back an empty map, and an empty map means every block is placed on the
    chord, which is a quietly worse answer wearing the same face as a good one.
    """
    found = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return StreetMap()
    shapes = []
    for row in text.splitlines():
        if not row.strip():
            continue
        try:
            fields = json.loads(row)
        except ValueError:
            continue
        if not isinstance(fields, dict):
            continue
        name, line = fields.get("street"), fields.get("line")
        building = fields.get("building")
        if isinstance(name, str) and isinstance(line, list):
            places = _places(line)
            if len(places) >= 2:
                found.append(Street(name, places))
        elif isinstance(building, list):
            places = _places(building)
            if len(places) >= 3:
                shapes.append(places)
    return StreetMap(tuple(found), tuple(shapes))


def _places(line: list[Any]) -> Line:
    """The points of a drawn line that are actually points.

    Two numbers, finite, and on the earth. This file is written by `--geocode`
    and then lives beside the notebook, where it gets copied about and opened in
    an editor, so a string where a latitude belongs is not a reason for a whole
    reconciliation to end in a ValueError out of `float()`.
    """
    found = []
    for point in line:
        if not isinstance(point, list) or len(point) != 2:
            continue
        lat, lon = number(point[0], -90.0, 90.0), number(point[1], -180.0, 180.0)
        if lat is not None and lon is not None:
            found.append((lat, lon))
    return tuple(found)
