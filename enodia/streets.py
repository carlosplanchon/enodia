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


def nearest_vertex(line: Sequence[Place], place: Place) -> tuple[int, float]:
    """Which vertex of a line is closest to a place, and how far off it is."""
    gaps = [distance_metres(*place, *vertex) for vertex in line]
    best = min(range(len(gaps)), key=gaps.__getitem__)
    return best, gaps[best]


@dataclass(frozen=True)
class Street:
    """One way of one street, drawn."""

    name: str
    line: Line

    @property
    def length_m(self) -> float:
        return line_length_m(self.line)


@dataclass(frozen=True)
class StreetMap:
    """What the lookup drew: the streets, and the buildings they run between."""

    streets: tuple[Street, ...] = ()
    buildings: tuple[Line, ...] = ()

    def __len__(self) -> int:
        return len(self.streets)

    def between(self, here: Place, there: Place, within_m: float = NEAR_CROSSING_M) -> Line | None:
        """The block from one crossing to the next, as drawn, or None to use the chord.

        Matched on the geometry and never on the crossings' names. A notebook
        writes "Agraciada y Freire" and the street is called "Avenida Agraciada",
        the same corner turns up spelled two ways, and a crossing may have been
        typed in by hand rather than looked up. Two coordinates and a drawing
        need none of that: the block is the run of the way that starts nearest
        one and ends nearest the other.
        """
        chord = distance_metres(*here, *there)
        best: tuple[float, Line] | None = None
        for street in self.streets:
            if len(street.line) < 2:
                continue
            first, off_first = nearest_vertex(street.line, here)
            last, off_last = nearest_vertex(street.line, there)
            if first == last or off_first > within_m or off_last > within_m:
                continue
            run = street.line[min(first, last) : max(first, last) + 1]
            drawn = (here, *(run if first < last else run[::-1]), there)
            if chord > 0 and line_length_m(drawn) > chord * LONGEST_DETOUR:
                continue
            if best is None or off_first + off_last < best[0]:
                best = (off_first + off_last, drawn)
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
