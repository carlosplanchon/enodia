"""The Rivera sample: real corners and street geometry, synthetic radios.

    uv run python samples/make_rivera.py

writes the three logs and the two notebooks beside this file, from
`rivera-streets.jsonl`, which `--geocode --streets` wrote from OpenStreetMap.
The four marks are real corners of Avenida Rivera in Montevideo, with the
coordinates `--geocode` gave them. Everything a radio would have heard is
invented here, deterministically, so that running this twice gives the same
bytes and `tests/test_samples.py` can say so.

The invented world is not the estimator's model looking at itself. Enodia
weights sightings assuming a path loss exponent of 3; the world here falls off
with 2.5, every router has its own transmit power, and every sighting carries
noise. A scan hears the world from where the walker was three seconds before
the time it is stamped with, the way a real sweep ends after it began: that lag
is what `--check-passes` measures, and this is what puts it there.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path

from enodia.netlog import SCAN_EVENT, LogRecord, NetworkLog, SeenNetwork
from enodia.streets import Place, distance_metres, read_streets

HERE = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=-3))
SEED = 20260914

# The corners, as --geocode wrote them (six decimals, the middle of the junction).
MARKS: tuple[tuple[str, float, float], ...] = (
    ("Rivera y Avenida Doctor Francisco Soca", -34.903126, -56.156023),
    ("Rivera y Brito del Pino", -34.903131, -56.157581),
    ("Rivera y Simón Bolívar", -34.903101, -56.159047),
    ("Rivera y Obligado", -34.903011, -56.160359),
)

INTERVAL_S = 5  # one scan every five seconds, Enodia's default
LAG_S = 3.0  # a scan hears the world from three seconds before its stamp
HEARD_DBM = -90.0  # weaker than this is not heard at all
EXPONENT = 2.5  # the world's path loss, deliberately not the estimator's 3
ANTENNA_M = 3.0  # routers are up a wall, never at foot level
FREQUENCIES = (2412, 2437, 2462, 5180, 5200, 5220, 5240, 5745, 5765)

# Where the routers are, along the route in metres and off to the side (left
# is positive, walking west): a few per block within the buildings, two at the
# corners a little way up the cross streets, and six far enough into the blocks
# to be heard faintly and placed badly, which is what "barely pinned" is for.
ROUTERS: tuple[tuple[float, float], ...] = (
    (20.0, 12.0),
    (55.0, -18.0),
    (90.0, 9.0),
    (125.0, -22.0),
    (150.0, -10.0),
    (185.0, 20.0),
    (215.0, 8.0),
    (250.0, -15.0),
    (285.0, 14.0),
    (320.0, -9.0),
    (350.0, 24.0),
    (380.0, -11.0),
    (138.2, 30.0),
    (272.0, -30.0),
    (70.0, 80.0),
    (200.0, -90.0),
    (330.0, 70.0),
    (10.0, -65.0),
    (392.0, 95.0),
    (240.0, 60.0),
)
OPEN = {6, 9}  # two of them run open, so the report has something to count

EARTH_M = 6_371_000.0


class Frame:
    """A flat frame in metres around the first mark, and the route drawn in it."""

    def __init__(self, lines: list[tuple[Place, ...]]) -> None:
        self.lat0, self.lon0 = lines[0][0]
        self.scale = math.cos(math.radians(self.lat0))
        # The whole route as one polyline of (distance along, x, y).
        self.points: list[tuple[float, float, float]] = []
        along = 0.0
        for line in lines:
            for index, place in enumerate(line):
                if self.points and index == 0:
                    continue  # the block starts where the last one ended
                x, y = self.xy(place)
                if self.points:
                    along += math.hypot(x - self.points[-1][1], y - self.points[-1][2])
                self.points.append((along, x, y))
        self.length = along

    def xy(self, place: Place) -> tuple[float, float]:
        lat, lon = place
        return (
            (lon - self.lon0) * self.scale * math.radians(EARTH_M),
            (lat - self.lat0) * math.radians(EARTH_M),
        )

    def place(self, x: float, y: float) -> Place:
        return (
            self.lat0 + y / math.radians(EARTH_M),
            self.lon0 + x / (self.scale * math.radians(EARTH_M)),
        )

    def at(self, along: float) -> tuple[float, float]:
        """The x, y a given distance along the route."""
        along = max(0.0, min(self.length, along))
        for (a0, x0, y0), (a1, x1, y1) in pairwise(self.points):
            if along <= a1:
                share = (along - a0) / (a1 - a0) if a1 > a0 else 0.0
                return (x0 + (x1 - x0) * share, y0 + (y1 - y0) * share)
        return self.points[-1][1:]

    def beside(self, along: float, offset: float) -> tuple[float, float]:
        """A point `offset` metres to the left of the route at `along`."""
        x0, y0 = self.at(max(0.0, along - 1.0))
        x1, y1 = self.at(min(self.length, along + 1.0))
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy) or 1.0
        x, y = self.at(along)
        return (x - dy / norm * offset, y + dx / norm * offset)


def blocks() -> list[tuple[Place, ...]]:
    """The three blocks as OpenStreetMap draws them, from mark to mark."""
    streets = read_streets(HERE / "rivera-streets.jsonl")
    drawn = []
    for (_, lat, lon), (_, lat2, lon2) in pairwise(MARKS):
        line = streets.between((lat, lon), (lat2, lon2))
        if line is None:  # pragma: no cover - the file beside this one draws every block
            line = ((lat, lon), (lat2, lon2))
        drawn.append(line)
    return drawn


class Router:
    def __init__(self, number: int, x: float, y: float, power_dbm: float) -> None:
        self.number = number
        self.x, self.y = x, y
        self.power_dbm = power_dbm

    def heard(self, x: float, y: float, rng: random.Random, drift: float) -> SeenNetwork | None:
        distance = math.sqrt((x - self.x) ** 2 + (y - self.y) ** 2 + ANTENNA_M**2)
        dbm = (
            self.power_dbm
            + drift
            - 10 * EXPONENT * math.log10(max(distance, 1.0))
            + rng.gauss(0.0, 1.0)
        )
        if dbm < HEARD_DBM:
            return None
        return SeenNetwork(
            f"sample-ap-{self.number:02d}",
            f"02:00:00:00:00:{self.number:02x}",
            "open" if self.number in OPEN else "psk",
            FREQUENCIES[self.number % len(FREQUENCIES)],
            round(dbm),
            None,
        )


class Walk:
    """Where the walker is at each second: a route, a pace, and one stop."""

    def __init__(
        self,
        frame: Frame,
        legs: list[tuple[float, float]],
        speed: float,
        stop_at: float | None,
        stop_for: float,
    ) -> None:
        # `legs` are (from, to) distances along the frame's route, walked in order.
        self.frame = frame
        self.legs = legs
        self.speed = speed
        self.stop_at = stop_at  # distance walked when the stop begins
        self.stop_for = stop_for
        self.total = sum(abs(b - a) for a, b in legs)

    def seconds_to(self, walked: float) -> float:
        seconds = walked / self.speed
        if self.stop_at is not None and walked > self.stop_at:
            seconds += self.stop_for
        return seconds

    def walked_at(self, seconds: float) -> float:
        if self.stop_at is not None:
            stop_begins = self.stop_at / self.speed
            if seconds >= stop_begins:
                seconds = max(stop_begins, seconds - self.stop_for)
        return min(self.total, max(0.0, seconds * self.speed))

    def along(self, walked: float) -> float:
        """The distance along the route (not the distance walked) after `walked` metres."""
        for a, b in self.legs:
            leg = abs(b - a)
            if walked <= leg:
                return a + (b - a) * (walked / leg if leg else 0.0)
            walked -= leg
        return self.legs[-1][1]

    def mark_times(self) -> list[float]:
        """When each leg boundary is reached, in seconds from the start."""
        times, walked = [0.0], 0.0
        for a, b in self.legs:
            walked += abs(b - a)
            times.append(self.seconds_to(walked))
        return times


def scan(
    frame: Frame,
    routers: list[Router],
    x: float,
    y: float,
    rng: random.Random,
    drift: dict[int, float],
) -> list[SeenNetwork]:
    heard = []
    for router in routers:
        seen = router.heard(x, y, rng, drift[router.number])
        if seen is not None:
            heard.append(seen)
    return heard


def write_log(
    path: Path,
    token: str,
    start: datetime,
    walk: Walk,
    routers: list[Router],
    rng: random.Random,
    drift: dict[int, float],
) -> None:
    path.unlink(missing_ok=True)
    log = NetworkLog(path)
    end = walk.mark_times()[-1]
    cycle = 0
    seconds = 0
    while seconds <= end:
        cycle += 1
        heard_at = walk.along(walk.walked_at(max(0.0, seconds - LAG_S)))
        x, y = walk.frame.at(heard_at)
        log.write(
            LogRecord(
                SCAN_EVENT,
                time=start + timedelta(seconds=seconds),
                interface="sample0",
                cycle=cycle,
                outing=token,
                networks=scan(walk.frame, routers, x, y, rng, drift),
            )
        )
        seconds += INTERVAL_S


def write_notebook(path: Path, start: datetime, walk: Walk, marks: list[int]) -> None:
    lines = []
    for seconds, index in zip(walk.mark_times(), marks, strict=True):
        name, lat, lon = MARKS[index]
        when = start + timedelta(seconds=round(seconds))
        lines.append(f"{when:%H:%M:%S} {name} @ {lat:.6f}, {lon:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sample(directory: Path) -> list[Path]:
    """Write the three logs and the two notebooks into `directory`, and name them."""
    rng = random.Random(SEED)
    frame = Frame(blocks())
    corners = [0.0]
    for line in blocks():
        corners.append(corners[-1] + sum(distance_metres(*a, *b) for a, b in pairwise(line)))
    routers = [
        Router(number, *frame.beside(along, offset), rng.uniform(-45.0, -35.0))
        for number, (along, offset) in enumerate(ROUTERS, start=1)
    ]

    written = []
    # The first outing walks Soca to Obligado at 1.3 m/s and stops for half a
    # minute forty percent of the way down the middle block.
    first = Walk(
        frame,
        [(corners[0], corners[1]), (corners[1], corners[2]), (corners[2], corners[3])],
        1.3,
        corners[1] + 0.4 * (corners[2] - corners[1]),
        30.0,
    )
    drift = {router.number: rng.gauss(0.0, 1.0) for router in routers}
    start = datetime(2026, 9, 14, 17, 0, 0, tzinfo=TZ)
    write_log(
        directory / "rivera-2026-09-14.jsonl", "sample-r14", start, first, routers, rng, drift
    )
    write_notebook(directory / "rivera-2026-09-14.txt", start, first, [0, 1, 2, 3])
    written += [directory / "rivera-2026-09-14.jsonl", directory / "rivera-2026-09-14.txt"]

    # The second, three days later, walks Obligado to Soca at 1.4 m/s, turns
    # round and comes back to Brito del Pino: the middle block's neighbour is
    # walked twice, once each way, which is what --check-passes needs.
    second = Walk(
        frame,
        [
            (corners[3], corners[2]),
            (corners[2], corners[1]),
            (corners[1], corners[0]),
            (corners[0], corners[1]),
        ],
        1.4,
        None,
        0.0,
    )
    drift = {router.number: rng.gauss(0.0, 1.0) for router in routers}
    start = datetime(2026, 9, 17, 17, 0, 0, tzinfo=TZ)
    write_log(
        directory / "rivera-2026-09-17.jsonl", "sample-r17", start, second, routers, rng, drift
    )
    write_notebook(directory / "rivera-2026-09-17.txt", start, second, [3, 2, 1, 0, 1])
    written += [directory / "rivera-2026-09-17.jsonl", directory / "rivera-2026-09-17.txt"]

    # The query: one scan on the middle block, sixty percent of the way west,
    # on yet another day, to be located against the map of the two outings.
    query = directory / "rivera-query.jsonl"
    query.unlink(missing_ok=True)
    drift = {router.number: rng.gauss(0.0, 1.0) for router in routers}
    x, y = frame.at(corners[1] + 0.6 * (corners[2] - corners[1]))
    NetworkLog(query).write(
        LogRecord(
            SCAN_EVENT,
            time=datetime(2026, 9, 21, 17, 3, 0, tzinfo=TZ),
            interface="sample0",
            cycle=1,
            outing="sample-q21",
            networks=scan(frame, routers, x, y, rng, drift),
        )
    )
    written.append(query)
    return written


def main() -> None:
    for path in write_sample(HERE):
        print(path.name)


if __name__ == "__main__":
    main()
