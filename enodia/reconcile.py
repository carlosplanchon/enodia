"""Offline reconciliation of a scan log with a notebook of timed street crossings.

In the field, Enodia runs inside a backpack and says the time through headphones;
the operator writes in a paper notebook the time at which each street crossing
is passed. Afterwards, this module joins the transcribed notebook with the log:
every scan gets a position between the two crossings it fell between, sharing
out each stretch by how much the networks in view turned over rather than by
the clock; every network gets the position of the scan where its signal was
strongest; and every access point gets an estimate of where it stands, from
every place it was heard, on the map when the notebook has coordinates and
along the route itself when it has not. No GPS, nothing online.

Notebook format, one crossing per line::

    17:52:10 Agraciada y Freire
    17:58 Agraciada y San Fructuoso          # seconds are optional
    18:05:30 Plaza Vidiella @ -34.8612, -56.2072   # coordinates, if you add them later
    date 2026-09-06                          # switch the day for the lines below
    03:25:00 McDonald's Paso Molino

Lines starting with '#' are ignored. Times without a date belong to the day of
the first scan in the log; a time earlier than the previous one rolls over to
the next day.

With a headset button (see `enodia.button`) the times come from the log's
`mark` records and the notebook needs none: a line is just the crossing's
name, and takes the next mark in order, or `#7 Plaza Vidiella` to name mark 7
outright when one was skipped. Timed and untimed lines can be mixed.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from itertools import pairwise
from pathlib import Path
from typing import Any

from enodia.netlog import MARK_EVENT, LogRecord, SeenNetwork, read_log, records_for_outing
from enodia.streets import Line, StreetMap, distance_metres, line_length_m, point_along

NOTEBOOK_LINE = re.compile(
    r"^(?:(?P<date>\d{4}-\d{2}-\d{2})[ T])?(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?"
    r"\s+(?P<name>.+?)"
    r"(?:\s*@\s*(?P<lat>-?\d+(?:\.\d+)?)\s*,\s*(?P<lon>-?\d+(?:\.\d+)?))?\s*$"
)
DATE_DIRECTIVE = re.compile(r"^date\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE)
# A line that begins like a time or a date meant to be one. `MARKED_LINE` takes
# almost any line as a crossing's name, so without this a typo in `17:00 A`
# stopped being an error and quietly became a notebook of a different kind.
MEANT_A_TIME = re.compile(r"^\s*(\d{1,2}\s*:|\d{4}\s*-)")
# And the same for a date directive. `date 2026-9-18` is one month short of
# the shape above, and taken for a crossing's name it consumed a button mark
# and shifted every crossing after it onto the wrong press.
MEANT_A_DATE = re.compile(r"^\s*date\b", re.IGNORECASE)
MARKED_LINE = re.compile(
    r"^(?:#(?P<number>\d+)\s+)?(?P<name>.+?)"
    r"(?:\s*@\s*(?P<lat>-?\d+(?:\.\d+)?)\s*,\s*(?P<lon>-?\d+(?:\.\d+)?))?\s*$"
)


CORNER_SPLIT = re.compile(r"\s+(?:y|e|esq\.?|esquina|&)\s+|\s*/\s*")


def folded(name: str) -> str:
    """A crossing's name with the differences that are not differences taken out.

    Case, accents and runs of spaces do not make two streets. A notebook is
    written by hand, one outing at a time, weeks apart, and `Yaguaron` one week
    against `Yaguarón` the next would split one stretch into two that never
    match each other, silently. Names are kept as they were written and compared
    folded.
    """
    plain = unicodedata.normalize("NFD", " ".join(name.split()).casefold())
    return "".join(letter for letter in plain if not unicodedata.combining(letter))


def _corner_halves(name: str) -> tuple[str, ...] | None:
    """The two streets a corner is named after, sorted, or None if it is not one."""
    halves = [half.strip() for half in CORNER_SPLIT.split(folded(name))]
    if len(halves) != 2 or not all(halves):
        return None
    return tuple(sorted(halves))


def confusable_crossings(names: Iterable[str]) -> list[tuple[str, ...]]:
    """Crossings written both ways round, which Enodia is otherwise taking for two.

    "Agraciada y Freire" and "Freire y Agraciada" are one corner, and a notebook
    written by hand weeks apart writes it both ways sooner or later. To Enodia
    they are two corners, so the block between one of them and the next splits
    into two stretches that never match each other, the map holds each half
    alone, and nothing in the output says any of this happened.

    They are reported and deliberately not merged. "Treinta y Tres" is one
    street, not the corner of Treinta and Tres, and no rule can tell those two
    shapes apart from a name alone. What can be told, without guessing, is that
    both orders of the same two halves are actually present, and that is what
    this looks for. The fix belongs in the notebook, which is the ground truth.
    """
    corners: dict[tuple[str, ...], dict[str, str]] = {}
    for name in names:
        halves = _corner_halves(name)
        if halves is not None:
            corners.setdefault(halves, {}).setdefault(folded(name), name)
    return [tuple(sorted(written.values())) for written in corners.values() if len(written) > 1]


class NotebookError(ValueError):
    """The notebook file has a line that cannot be read."""


class UntimedNotebook(NotebookError):
    """The notebook's crossings have no times, and no marks can give them any.

    Kept apart from every other `NotebookError` because to a caller that only
    wanted the times the two mean opposite things. This one says the notebook is
    fine and its times are not known, which is the ordinary state of a notebook
    written with the headset button and read without its log. A line that cannot
    be parsed says the notebook is wrong, and reading that as "no times known"
    is how `--geocode` went on to send a network request over a notebook it had
    already failed to read.
    """


@dataclass(frozen=True)
class Waypoint:
    """A street crossing passed at a known time."""

    time: datetime
    name: str
    lat: float | None = None
    lon: float | None = None

    @property
    def coordinates(self) -> tuple[float, float] | None:
        """(lat, lon) when the notebook gave both."""
        if self.lat is None or self.lon is None:
            return None
        return (self.lat, self.lon)

    @property
    def has_coordinates(self) -> bool:
        return self.coordinates is not None


def strip_comment(raw: str) -> str:
    """Drop a '# comment', except a leading '#7 ' that names a mark.

    The byte order mark a Windows editor writes at the head of a file is taken
    off too. It is not whitespace, so `strip()` leaves it, and it would push the
    first line of the notebook out of `NOTEBOOK_LINE` and into `MARKED_LINE`
    with the mark itself glued to the front of the crossing's name.
    """
    line = raw.lstrip("\ufeff").strip()
    if re.match(r"^#\d+\s", line):
        head, _, tail = line.partition(" ")
        return f"{head} {tail.split('#', 1)[0]}".strip()
    return line.split("#", 1)[0].strip()


def _coordinate(raw: str | None, limit: float, what: str, where: str) -> float | None:
    """A coordinate off a notebook line, refused rather than believed when it cannot exist.

    The notebook is the ground truth every other check is measured against, so a
    latitude of 999 fails nowhere: it produces distances and geometry that are
    absurd and that look exactly as legitimate as the rest of the report. And
    somebody who typed `@` meant to give a coordinate, so this says the line is
    wrong instead of quietly reading it as a crossing with no coordinates, which
    would take the whole notebook down to the answers it can give without any.
    """
    if raw is None:
        return None
    # The pattern already matched digits, so this is a number. It can still be
    # an infinity: a four-hundred-digit run of them is a number to the pattern.
    found = float(raw)
    if not -limit <= found <= limit:
        raise NotebookError(f"{where}: {raw} is not a {what}")
    return found


def read_notebook(
    path: str | Path,
    day: date,
    tz: tzinfo | None,
    marks: Sequence[tuple[int, datetime]] = (),
) -> list[Waypoint]:
    """Parse the transcribed notebook. `day` and `tz` complete the times, which
    carry neither unless the line or a `date` directive says otherwise.

    `marks` are the log's button presses, as (number, time): a line without a
    time takes the next mark in order, or the one it names with `#7`.
    """
    waypoints: list[Waypoint] = []
    current_day = day
    previous: datetime | None = None
    by_number = dict(marks)
    if len(by_number) != len(marks):
        # `dict` keeps the last of a repeated number without a word, and the
        # crossing that answers to it would silently take the wrong time.
        twice = sorted({n for n, _ in marks if [m for m, _ in marks].count(n) > 1})
        raise NotebookError(
            f"{path}: the log has mark {', '.join(f'#{n}' for n in twice)} more than once"
        )
    used: set[int] = set()
    last_used = 0
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = strip_comment(raw)
        if not line:
            continue
        directive = DATE_DIRECTIVE.match(line)
        if not directive and MEANT_A_DATE.match(line):
            raise NotebookError(
                f"{path}:{number}: {line!r} begins like a date directive and is not one "
                "(it wants 'date YYYY-MM-DD')"
            )
        if directive:
            try:
                current_day = date.fromisoformat(directive.group("date"))
            # The 31st of February matches the shape of a date and is not one.
            except ValueError as exc:
                raise NotebookError(f"{path}:{number}: {line!r} is not a date ({exc})") from exc
            # The day moves and the walk does not restart. Forgetting the last
            # crossing here let a directive take the route back a day, and the
            # rollover below would then quietly undo the directive instead,
            # which is an instruction ignored without a word.
            if previous is not None and current_day < previous.date():
                raise NotebookError(
                    f"{path}:{number}: {line!r} is before the crossing above it, "
                    f"on {previous.date().isoformat()}"
                )
            continue
        m = NOTEBOOK_LINE.match(line)
        if m:
            try:
                # Inside the handler with the time: the 31st of February matches
                # the shape of a date wherever it is written, and the line that
                # carries its own date is the other place it can be written.
                line_day = date.fromisoformat(m.group("date")) if m.group("date") else current_day
                stamp = datetime(
                    line_day.year,
                    line_day.month,
                    line_day.day,
                    int(m.group("h")),
                    int(m.group("m")),
                    int(m.group("s") or 0),
                    tzinfo=tz,
                )
            except ValueError as exc:  # 25:59, or the 31st of February
                raise NotebookError(f"{path}:{number}: {line!r} is not a time ({exc})") from exc
            if previous is not None and stamp < previous and not m.group("date"):
                stamp += timedelta(days=1)
        else:
            if MEANT_A_TIME.match(line):
                # A line that begins like a time or a date and did not parse as
                # one is a typo, and `17:0 A` must not quietly become a crossing
                # named "17:0 A" taking the next button mark. Every other shape
                # of line can be a crossing's name, and this one cannot.
                raise NotebookError(f"{path}:{number}: {line!r} begins like a time and is not one")
            m = MARKED_LINE.match(line)
            if not m:  # pragma: no cover - the comment above is already non-empty
                raise NotebookError(
                    f"{path}:{number}: expected 'HH:MM[:SS] crossing name', got {line!r}"
                )
            if not by_number:
                raise UntimedNotebook(
                    f"{path}:{number}: {line!r} has no time "
                    "(and the log has no button marks to take a time from)"
                )
            if m.group("number"):
                wanted = int(m.group("number"))
                if wanted not in by_number:
                    raise NotebookError(
                        f"{path}:{number}: the log has no mark #{wanted}. Its marks run "
                        f"{min(by_number)} to {max(by_number)}"
                    )
                if wanted in used:
                    # One press of the button is one crossing. The set was
                    # already here for the unnumbered lines, and the numbered
                    # ones went round it: `#1 A` and `#1 B` put two crossings at
                    # one moment, and a block of no length is not a block.
                    raise NotebookError(f"{path}:{number}: mark #{wanted} is used twice")
            else:
                # Marks are chronological: the next crossing takes the first unused
                # mark after the last one consumed. Earlier unused marks were skipped
                # on purpose, presumably a press by mistake.
                later = [n for n in sorted(by_number) if n > last_used and n not in used]
                if not later:
                    # Marks were given and they do not cover this notebook, which
                    # is a mismatch and not a notebook whose times are unknown.
                    # It is often the wrong walk of the log: see --outing.
                    raise NotebookError(
                        f"{path}:{number}: {line!r} has no time "
                        f"and every mark in the log is used up ({len(by_number)} of them)"
                    )
                wanted = later[0]
            used.add(wanted)
            last_used = max(last_used, wanted)
            stamp = by_number[wanted]
        if previous is not None and stamp < previous:
            # Only reachable when a line carried its own date, since a bare time
            # earlier than the last one has just rolled over to the next day. A
            # walk goes forwards, and everything downstream reads the crossings
            # in the order they are written, so a route that goes back in time
            # is not a route it can place anything along.
            raise NotebookError(
                f"{path}:{number}: {stamp.isoformat()} is earlier than "
                f"the crossing before it, {previous.isoformat()}"
            )
        previous = stamp
        # The day moves with the walk. A line that carried its own date used to
        # leave this behind, so `2026-09-18 23:50` followed by a bare `00:10`
        # put the second crossing twenty-three hours before the first.
        current_day = stamp.date()
        where = f"{path}:{number}"
        lat = _coordinate(m.group("lat"), 90.0, "latitude", where)
        lon = _coordinate(m.group("lon"), 180.0, "longitude", where)
        waypoints.append(Waypoint(stamp, m.group("name").strip(), lat, lon))
    if len(waypoints) < 2:
        raise NotebookError(f"{path}: at least two crossings are needed, found {len(waypoints)}")
    return waypoints


# --- Positions along the route -----------------------------------------------


def button_marks(records: Sequence[LogRecord]) -> list[tuple[int, datetime]]:
    """The headset button presses in a log, as (number, time)."""
    return [
        (r.number, r.time)
        for r in records
        if r.event == MARK_EVENT and r.number is not None and r.time is not None
    ]


def describe_fraction(name_from: str, name_to: str, fraction: float) -> str:
    """How a place between two crossings reads: `at "A"`, or so far along from one to the other.

    Shared so that a place from a map file and a place from a reconciliation are
    phrased alike, since to the operator holding the notebook they are the same
    kind of thing.
    """
    if fraction <= 0.0 or name_from == name_to:
        return f'at "{name_from}"'
    if fraction >= 1.0:
        return f'at "{name_to}"'
    return f'between "{name_from}" and "{name_to}", {fraction:.0%} of the way'


@dataclass(frozen=True)
class Position:
    """A point on the route: some fraction of the way from one crossing to the next."""

    start: Waypoint
    end: Waypoint
    fraction: float
    line: Line | None = None

    @property
    def coordinates(self) -> tuple[float, float] | None:
        """(lat, lon) when both crossings have them, along the street if it is drawn.

        Without a drawing this is the straight line from one corner to the next,
        which is right at the two ends and drifts towards the chord wherever the
        block bends. With one (see `--streets`) it follows what the block
        actually does, by distance walked rather than by vertices.
        """
        start, end = self.start.coordinates, self.end.coordinates
        if start is None or end is None:
            return None
        if self.line is not None:
            return point_along(self.line, self.fraction)
        return (
            start[0] + (end[0] - start[0]) * self.fraction,
            start[1] + (end[1] - start[1]) * self.fraction,
        )

    @property
    def length_m(self) -> float | None:
        """How long this block is: as drawn when it is drawn, straight when not."""
        start, end = self.start.coordinates, self.end.coordinates
        if start is None or end is None:
            return None
        if self.line is not None:
            return line_length_m(self.line)
        return distance_metres(*start, *end)

    @property
    def lat(self) -> float | None:
        place = self.coordinates
        return None if place is None else place[0]

    @property
    def lon(self) -> float | None:
        place = self.coordinates
        return None if place is None else place[1]

    def describe(self) -> str:
        return describe_fraction(self.start.name, self.end.name, self.fraction)


def _when(scan: LogRecord) -> datetime:
    """The time of a scan that `reconcile` let through: it filtered on `time is not None`."""
    assert scan.time is not None
    return scan.time


def segment_of(moment: datetime, waypoints: Sequence[Waypoint]) -> int | None:
    """Index of the stretch between crossings that `moment` falls in, or None."""
    last = len(waypoints) - 2
    for index, (start, end) in enumerate(pairwise(waypoints)):
        # A crossing's own instant belongs to the segment that starts there,
        # except the final crossing, which closes the last segment.
        if start.time <= moment < end.time or (index == last and moment == end.time):
            return index
    return None


def _time_fraction(moment: datetime, start: Waypoint, end: Waypoint) -> float:
    span = (end.time - start.time).total_seconds()
    return (moment - start.time).total_seconds() / span if span else 0.0


def block_line(start: Waypoint, end: Waypoint, streets: StreetMap | None) -> Line | None:
    """How the block between two crossings is drawn, when anything drew it."""
    here, there = start.coordinates, end.coordinates
    if streets is None or here is None or there is None:
        return None
    return streets.between(here, there)


def locate(
    moment: datetime, waypoints: Sequence[Waypoint], streets: StreetMap | None = None
) -> Position | None:
    """Where the operator was at `moment`, sharing out the stretch on the clock.

    Assumes a steady pace from one crossing to the next. `place_by_movement`
    does not.
    """
    index = segment_of(moment, waypoints)
    if index is None:
        return None
    start, end = waypoints[index], waypoints[index + 1]
    fraction = _time_fraction(moment, start, end)
    return Position(start, end, fraction, block_line(start, end, streets))


# --- Pace from the networks themselves ---------------------------------------


def network_turnover(before: set[str], after: set[str]) -> float:
    """How much the set of networks in view changed between two scans.

    The Jaccard distance: 0 when exactly the same networks are in view, 1 when
    nothing is shared. Standing still, the set barely changes; walking, access
    points enter and leave continuously. It is an odometer made of other
    people's routers.
    """
    union = before | after
    if not union:
        return 0.0
    return 1.0 - len(before & after) / len(union)


def _in_view(scan: LogRecord) -> set[str]:
    return {network.key for network in scan.networks if network.identified}


def _step(before: LogRecord, after: LogRecord) -> float:
    """Movement between two consecutive scans, as far as the networks show it.

    Every scan record is evidence. A cycle whose scan could not be trusted (the
    daemon refused, the radio was switched off) is a `scan_failed` record, not a
    scan, and is skipped, so an empty list here is an empty view, and the step
    from a full view to it is a step.

    Two different radios are never a step. Watching two interfaces writes a
    record each, and they see different sets: one is a 5 GHz card hearing eight
    networks and the other a 2.4 GHz one hearing forty. Comparing them reads as
    a complete change of view, which is a whole block walked, over and over,
    while the operator stands still. `merged_scans` folds each cycle into one
    record before it gets here, and this is what catches a cycle whose two scans
    landed either side of a second boundary.
    """
    if before.interface and after.interface and before.interface != after.interface:
        return 0.0
    return network_turnover(_in_view(before), _in_view(after))


def merged_scans(scans: Sequence[LogRecord]) -> list[LogRecord]:
    """One scan per cycle, however many interfaces were watched.

    A cycle asks every Wi-Fi interface in turn and writes a record for each.
    They are one look at one place from one pair of feet, and leaving them
    separate makes the pace estimate read the difference between two radios as
    ground covered. Their networks are put together and the interface is
    dropped, since a merged look belongs to no single card.

    Grouped on the `cycle` the loop wrote down, and only on the timestamp when
    there is none. The clock cannot answer this: it has one second of
    resolution, so a cycle whose two scans land either side of a second comes
    apart, and then the pair either side of the split is two different radios
    and worth nothing as evidence, which loses the movement instead of
    inventing it. Logs written before the field existed fall back to the
    timestamp, which is what they have.
    """
    together: dict[object, list[LogRecord]] = {}
    for scan in scans:
        together.setdefault(scan.cycle if scan.cycle is not None else scan.time, []).append(scan)
    folded = []
    for cycle in together.values():
        if len(cycle) == 1:
            folded.append(cycle[0])
            continue
        networks: dict[str, SeenNetwork] = {}
        anonymous: list[SeenNetwork] = []
        for scan in cycle:
            for network in scan.networks:
                if not network.identified:
                    # Nothing to fold it onto, and nothing it can be folded onto
                    # either: every anonymous network answers to the same empty
                    # key, so keyed folding would keep one of them and lose the
                    # rest. They go through whole, in the order they were heard.
                    anonymous.append(network)
                    continue
                # The strongest reading of the two radios is the one to keep.
                if network.key not in networks or network.strength > networks[network.key].strength:
                    networks[network.key] = network
        folded.append(
            LogRecord(
                cycle[0].event,
                min(scan.time for scan in cycle if scan.time is not None),
                cycle=cycle[0].cycle,
                outing=cycle[0].outing,
                networks=[*networks.values(), *anonymous],
            )
        )
    # Back in the order the walk happened in: the cycles come out of a dict, and
    # everything downstream reads consecutive scans as consecutive moments.
    return sorted(folded, key=_when)


def _share_out(scans: Sequence[LogRecord], start: Waypoint, end: Waypoint) -> list[float]:
    """Where each scan of one stretch falls, by movement rather than by the clock.

    The scans measure movement only between themselves, so the stretches from
    the first crossing to the first scan, and from the last scan to the second
    crossing, are extrapolated at the average turnover per second of the ones
    in between. With a steady pace this lands on exactly what the clock would
    have said; the two only diverge where the pace was not steady, which is the
    whole point.
    """
    times = [_when(scan) for scan in scans]
    steps = [_step(a, b) for a, b in pairwise(scans)]
    walked = sum(steps)
    measured_seconds = (times[-1] - times[0]).total_seconds() if len(times) > 1 else 0.0
    if walked <= 0 or measured_seconds <= 0:
        return [_time_fraction(moment, start, end) for moment in times]

    rate = walked / measured_seconds
    head = rate * (times[0] - start.time).total_seconds()
    tail = rate * (end.time - times[-1]).total_seconds()
    total = head + walked + tail  # > 0: walked ya lo es, y head y tail no son negativos

    fractions = []
    travelled = head
    for index in range(len(scans)):
        fractions.append(min(1.0, max(0.0, travelled / total)))
        if index < len(steps):
            travelled += steps[index]
    return fractions


def place_by_movement(
    scans: Sequence[LogRecord],
    waypoints: Sequence[Waypoint],
    streets: StreetMap | None = None,
) -> list[Position | None]:
    """Where each scan was, sharing out every stretch by observed movement.

    Interpolating on the clock assumes a steady pace, and the README has to ask
    the operator to write a place down twice when they stop. The log already
    holds the evidence of that stop -- the same networks, scan after scan -- so
    this reads it instead of asking. Where there is no evidence (a single scan
    in the stretch, or no turnover at all) it falls back to the clock.
    """
    members: dict[int, list[int]] = {}
    for index, scan in enumerate(scans):
        segment = segment_of(_when(scan), waypoints)
        if segment is not None:
            members.setdefault(segment, []).append(index)

    positions: list[Position | None] = [None] * len(scans)
    for segment, indexes in members.items():
        start, end = waypoints[segment], waypoints[segment + 1]
        line = block_line(start, end, streets)
        fractions = _share_out([scans[i] for i in indexes], start, end)
        for index, fraction in zip(indexes, fractions, strict=True):
            positions[index] = Position(start, end, fraction, line)
    return positions


# --- Where the access point itself probably stands ---------------------------


PATH_LOSS_EXPONENT = 3.0
PINNED = 0.75


def signal_weight(dbm: float, exponent: float = PATH_LOSS_EXPONENT) -> float:
    """How much a sighting should count towards where an access point stands.

    Under the log-distance model a signal falls off as `RSSI = A - 10 n log d`,
    so distance goes as `10 ** (-RSSI / 10n)` and a sighting's weight -- one
    over that distance -- goes as `10 ** (RSSI / 10n)`. The `A` term is the
    same for every sighting of one access point, so it cancels when the weights
    are normalised, and the transmit power never has to be known.

    `exponent` is the path loss exponent: 2 in free space, 3 or so in a street
    with buildings on both sides. It sets how sharply the nearest sighting
    dominates -- at n=1 a sighting 30 dB stronger counts a thousand times more,
    at n=3 it counts ten times more -- which is why the default is not free
    space: through walls, a naive weighting collapses onto the strongest
    sighting and tells you nothing the strongest sighting did not.
    """
    return 10 ** (dbm / (10 * exponent))


@dataclass(frozen=True)
class Estimate:
    """Where an access point probably stands, from every place it was seen."""

    lat: float
    lon: float
    spread_m: float
    sightings: int
    plain_spread_m: float = 0.0

    @property
    def barely_pinned(self) -> bool:
        """True when the signal barely narrowed this down past where you walked.

        The weighted centroid is compared with the plain middle of the same
        sightings. Walk past an access point and its signal peaks sharply,
        pulling the centroid well clear of that middle. Hear one faintly from a
        block away and every sighting weighs about the same, so the centroid
        settles on the middle of your own route and says nothing whatever about
        where the thing is. Most of what a walk hears is of the second kind, and
        without this the two come out of the report looking alike.
        """
        return self.plain_spread_m <= 0 or self.spread_m > self.plain_spread_m * PINNED

    @property
    def few_sightings(self) -> bool:
        """Fewer than three sightings: too few to say much about where it stands.

        Many sightings are no guarantee either -- a straight street sees the
        access point from a line, which pins it down along the street and not
        across it (see `_estimate_from`) -- but under three is not worth a map.
        """
        return self.sightings < 3


def _estimate_from(points: Sequence[tuple[float, float, float]]) -> Estimate | None:
    """Weighted centroid of the places a network was seen from, and their spread.

    The spread is the weighted mean distance of the sightings from the centroid:
    small when they cluster around one point, large when the network was heard
    from all along the route. It is a measure of how much the route pinned the
    access point down, not of how accurate the centroid is.

    Two biases are worth knowing about. Sightings strung out along a straight
    street pin down the position along it and nothing at all across it: the
    estimate will sit on the street you walked whatever side the access point
    is on. And a centroid is pulled towards the middle of the sightings, so an
    access point beyond the end of the route comes out nearer than it is --
    where the strongest sighting is at the edge of the walk, that sighting is
    a floor on how far along the route the access point can be, not a guess to
    average away from.
    """
    total = sum(weight for _, _, weight in points)
    if not points or total <= 0:
        return None
    lat = sum(point_lat * weight for point_lat, _, weight in points) / total
    lon = sum(point_lon * weight for _, point_lon, weight in points) / total
    spread = (
        sum(
            weight * distance_metres(lat, lon, point_lat, point_lon)
            for point_lat, point_lon, weight in points
        )
        / total
    )
    # The same sightings with the signal ignored: the middle of where you walked
    # while it was in view. How far the weighted centroid sits from this is the
    # whole of what the signal contributed. See `Estimate.barely_pinned`.
    plain_lat = sum(point_lat for point_lat, _, _ in points) / len(points)
    plain_lon = sum(point_lon for _, point_lon, _ in points) / len(points)
    plain = sum(
        distance_metres(plain_lat, plain_lon, point_lat, point_lon)
        for point_lat, point_lon, _ in points
    ) / len(points)
    return Estimate(lat, lon, spread, len(points), plain)


@dataclass(frozen=True)
class Stretch:
    """One stretch of street between two named crossings, and every walk along it.

    Two segments of the route are the same stretch when the notebook gave them
    the same pair of crossing names, whichever way round: a block walked and
    then walked back is one stretch covered twice. The first of them sets the
    direction both are measured in, so "60% of the way" means one place however
    many passes produced it, and a pass that ran the other way is read backwards
    to get there.
    """

    name_from: str
    name_to: str
    start: Waypoint
    end: Waypoint
    segments: tuple[int, ...]
    forwards: tuple[bool, ...]
    length_m: float | None

    def metres(self, fraction: float) -> float | None:
        """A fraction of this stretch in metres, when the crossings have coordinates."""
        return None if self.length_m is None else fraction * self.length_m


def route_stretches(
    waypoints: Sequence[Waypoint], streets: StreetMap | None = None
) -> list[Stretch]:
    """The stretches of street the route covers, each with every segment that walked it."""
    order: list[tuple[str, ...]] = []
    segments: dict[tuple[str, ...], list[int]] = {}
    for segment, (start, end) in enumerate(pairwise(waypoints)):
        key = tuple(sorted((folded(start.name), folded(end.name))))
        if key not in segments:
            order.append(key)
        segments.setdefault(key, []).append(segment)

    stretches = []
    for key in order:
        walked = segments[key]
        first = walked[0]
        start, end = waypoints[first], waypoints[first + 1]
        length = None
        for segment in walked:
            here, there = waypoints[segment], waypoints[segment + 1]
            length = Position(here, there, 0.0, block_line(here, there, streets)).length_m
            if length is not None:
                break
        stretches.append(
            Stretch(
                start.name,
                end.name,
                start,
                end,
                tuple(walked),
                tuple(folded(waypoints[segment].name) == folded(start.name) for segment in walked),
                length,
            )
        )
    return stretches


def route_frames(
    waypoints: Sequence[Waypoint], streets: StreetMap | None = None
) -> dict[int, tuple[Stretch, bool]]:
    """Which stretch each segment of the route is, and whether it runs its way round."""
    return {
        segment: (stretch, forwards)
        for stretch in route_stretches(waypoints, streets)
        for segment, forwards in zip(stretch.segments, stretch.forwards, strict=True)
    }


@dataclass(frozen=True)
class RouteEstimate:
    """Where an access point probably stands, measured along one stretch of the route.

    The same weighted centroid as `Estimate`, in the one dimension a notebook
    always has: how far along a stretch between two named crossings you were. A
    notebook of bare crossing names places every access point this way,
    coordinates or none.
    """

    stretch: Stretch
    fraction: float
    spread: float
    sightings: int
    plain_spread: float = 0.0

    @property
    def barely_pinned(self) -> bool:
        """True when the signal barely narrowed this down past where you walked.

        The same comparison `Estimate.barely_pinned` makes, in fractions of one
        stretch instead of metres.
        """
        return self.plain_spread <= 0 or self.spread > self.plain_spread * PINNED

    @property
    def position(self) -> Position:
        return Position(self.stretch.start, self.stretch.end, self.fraction)

    @property
    def few_sightings(self) -> bool:
        """Fewer than three sightings on this stretch: too few to say much."""
        return self.sightings < 3


def _route_estimate_from(
    by_stretch: Mapping[Stretch, list[tuple[float, float]]],
) -> RouteEstimate | None:
    """Weighted centroid of the places along one stretch a network was heard from.

    The stretch is the one it was loudest on, by total weight, and only the
    sightings made there count. Sightings from two stretches are not averaged:
    the route is a path, not a ruler, and a walk that doubles back covers one
    place twice at two very different distances from the start. Averaging those
    puts every access point at the corner you turned round at, which is the one
    place none of them is. Every pass over the chosen stretch does count,
    including a pass that ran the other way, and that is what cancels the lag
    `RepeatedStretch.shift` measures.

    The spread is the weighted mean distance from the centroid, as a fraction of
    the stretch, and carries the same warning as `_estimate_from`: it says how
    much the walk pinned the access point down, not how accurate the centroid is.
    """
    if not by_stretch:
        return None
    loudest = max(by_stretch, key=lambda s: sum(weight for _, weight in by_stretch[s]))
    points = by_stretch[loudest]
    total = sum(weight for _, weight in points)  # > 0: signal_weight nunca da cero
    along = sum(place * weight for place, weight in points) / total
    spread = sum(weight * abs(place - along) for place, weight in points) / total
    plain_along = sum(place for place, _ in points) / len(points)
    plain = sum(abs(place - plain_along) for place, _ in points) / len(points)
    return RouteEstimate(loudest, along, spread, len(points), plain)


# --- Joining the log with the notebook ---------------------------------------


@dataclass(frozen=True)
class PlacedScan:
    scan: LogRecord
    position: Position


@dataclass(frozen=True)
class PlacedNetwork:
    """A network, where you were when it came in strongest, and an estimate of
    where the access point stands: on the map when the notebook has
    coordinates, and along the route either way."""

    network: SeenNetwork
    position: Position
    seen: int
    first_seen: datetime
    last_seen: datetime
    best_seen: datetime
    connected: bool = False
    estimate: Estimate | None = None
    route_estimate: RouteEstimate | None = None


@dataclass
class Reconciliation:
    waypoints: list[Waypoint]
    scans: list[LogRecord]
    placed: list[PlacedScan]
    before: int
    after: int
    networks: list[PlacedNetwork]
    streets: StreetMap | None = None

    def route_order(self, position: Position) -> tuple[int, float]:
        return (self.waypoints.index(position.start), position.fraction)

    def write_geojson(self, path: str | Path) -> None:
        """Write the located networks, the crossings and the route as GeoJSON, for a map.

        One FeatureCollection: a Point per network that has coordinates -- the
        access point estimate where there is one, else the place of its
        strongest sighting, and `placed_by` says which -- a Point per crossing
        with coordinates, and the route as a LineString through them. Every
        feature carries `kind` for styling. Positions are [longitude, latitude].
        """
        features: list[dict[str, Any]] = []
        for item in self.networks:
            n, p, e = item.network, item.position, item.estimate
            placed = e or item.route_estimate
            if e is not None:
                lat, lon, placed_by = e.lat, e.lon, "estimate"
            elif (place := p.coordinates) is not None:
                lat, lon = place
                placed_by = "strongest sighting"
            else:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                    "properties": {
                        "kind": "network",
                        "ssid": n.ssid,
                        "bssid": n.bssid,
                        "security": n.security,
                        "open": n.open,
                        "frequency_mhz": n.frequency,
                        "channel": n.channel,
                        "best_signal_dbm": n.signal_dbm,
                        "best_signal_percent": n.signal_percent,
                        "seen": item.seen,
                        "connected": item.connected,
                        "first_seen": item.first_seen.isoformat(),
                        "last_seen": item.last_seen.isoformat(),
                        "best_seen": item.best_seen.isoformat(),
                        "placed_by": placed_by,
                        "spread_m": None if e is None else round(e.spread_m),
                        "sightings": None if e is None else e.sightings,
                        "barely_pinned": None if placed is None else placed.barely_pinned,
                    },
                }
            )
        crossings = [w for w in self.waypoints if w.has_coordinates]
        for w in crossings:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [w.lon, w.lat]},
                    "properties": {"kind": "crossing", "name": w.name, "time": w.time.isoformat()},
                }
            )
        if len(crossings) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[w.lon, w.lat] for w in crossings],
                    },
                    "properties": {
                        "kind": "route",
                        "from": crossings[0].name,
                        "to": crossings[-1].name,
                    },
                }
            )
        with Path(path).open("w", encoding="utf-8") as f:
            json.dump(
                {"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, indent=1
            )

    def write_csv(self, path: str | Path) -> None:
        with Path(path).open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "ssid",
                    "bssid",
                    "security",
                    "frequency_mhz",
                    "channel",
                    "best_signal_dbm",
                    "best_signal_percent",
                    "best_seen",
                    "seen",
                    "connected",
                    "first_seen",
                    "last_seen",
                    "from",
                    "to",
                    "fraction",
                    "lat",
                    "lon",
                    "estimated_lat",
                    "estimated_lon",
                    "spread_m",
                    "estimated_from",
                    "estimated_to",
                    "estimated_fraction",
                    "estimated_spread",
                    "barely_pinned",
                ]
            )
            for item in self.networks:
                n, p, e, r = item.network, item.position, item.estimate, item.route_estimate
                placed = e or r
                writer.writerow(
                    [
                        n.ssid,
                        n.bssid or "",
                        n.security or "",
                        "" if n.frequency is None else n.frequency,
                        "" if n.channel is None else n.channel,
                        "" if n.signal_dbm is None else n.signal_dbm,
                        "" if n.signal_percent is None else n.signal_percent,
                        item.best_seen.isoformat(),
                        item.seen,
                        "yes" if item.connected else "",
                        item.first_seen.isoformat(),
                        item.last_seen.isoformat(),
                        p.start.name,
                        p.end.name,
                        f"{p.fraction:.3f}",
                        "" if p.lat is None else f"{p.lat:.6f}",
                        "" if p.lon is None else f"{p.lon:.6f}",
                        "" if e is None else f"{e.lat:.6f}",
                        "" if e is None else f"{e.lon:.6f}",
                        "" if e is None else f"{e.spread_m:.0f}",
                        "" if r is None else r.position.start.name,
                        "" if r is None else r.position.end.name,
                        "" if r is None else f"{r.position.fraction:.3f}",
                        "" if r is None else f"{r.spread:.3f}",
                        "yes" if placed is not None and placed.barely_pinned else "",
                    ]
                )


def reconcile(
    log_path: str | Path,
    notebook_path: str | Path,
    by_movement: bool = True,
    streets: StreetMap | None = None,
    outing: str | None = None,
) -> Reconciliation:
    """Join a log with a notebook: positions for every scan and every network.

    :param by_movement: share out each stretch between crossings by how much
        the networks in view turned over, instead of by the clock. Falls back
        to the clock wherever the scans show no movement to go on.
    :param streets: the blocks as OpenStreetMap draws them, from `--geocode
        --streets`. Without it a scan sits on the straight line between two
        crossings, which is what this has always done.
    :param outing: which walk of the log to read, when the file holds more than
        one. The last one by default. A notebook is one walk's, so this has to
        be one walk's too: read against the whole file, the day came from the
        first scan in it and the button marks of every walk counted from one on
        top of each other.
    """
    records = records_for_outing(read_log(log_path), outing)
    if outing is not None and not records:
        raise NotebookError(f"{log_path}: no walk called {outing!r} in it")
    scans = merged_scans(
        [record for record in records if record.is_scan and record.time is not None]
    )
    if not scans:
        raise NotebookError(f"{log_path}: no timestamped scans found")
    first = _when(scans[0])
    waypoints = read_notebook(notebook_path, first.date(), first.tzinfo, button_marks(records))

    positions = (
        place_by_movement(scans, waypoints, streets)
        if by_movement
        else [locate(_when(scan), waypoints, streets) for scan in scans]
    )
    placed: list[PlacedScan] = []
    before = after = 0
    for scan, position in zip(scans, positions, strict=True):
        if position is None:
            if _when(scan) < waypoints[0].time:
                before += 1
            else:
                after += 1
            continue
        placed.append(PlacedScan(scan, position))

    best: dict[str, PlacedNetwork] = {}
    # Every place a network was heard from, with how much each should count
    # towards where it stands: on the map, which needs coordinates, and along
    # the route, which needs only the notebook's crossings in order.
    seen_from: dict[str, list[tuple[float, float, float]]] = {}
    seen_along: dict[str, dict[Stretch, list[tuple[float, float]]]] = {}
    frames = route_frames(waypoints, streets)
    for placed_scan in placed:
        scan, position = placed_scan.scan, placed_scan.position
        when, place = _when(scan), position.coordinates
        stretch, forwards = frames[waypoints.index(position.start)]
        along = position.fraction if forwards else 1.0 - position.fraction
        # A network listed twice in one look was heard once, from one place. The
        # question is per scan and nothing wider, and the difference matters:
        # this used to be a set of (network, timestamp) across the whole walk,
        # from when two interfaces wrote a record each at the same instant.
        # `merged_scans` folds those together now, so the only records left
        # sharing a second are genuinely different cycles, and keying on the
        # clock threw the second one away. The clock has one second of
        # resolution and a cycle takes less than that.
        counted: set[str] = set()
        for network in scan.networks:
            if not network.identified:
                # It cannot be placed, because placing a network means gathering
                # every sighting of it, and there is no telling which sightings
                # were of this one. It stays in the log and out of the report.
                continue
            again = network.key in counted
            counted.add(network.key)
            if not again and network.has_signal:
                weight = signal_weight(network.strength)
                seen_along.setdefault(network.key, {}).setdefault(stretch, []).append(
                    (along, weight)
                )
                if place is not None:
                    seen_from.setdefault(network.key, []).append((place[0], place[1], weight))
            current = best.get(network.key)
            if current is None:
                best[network.key] = PlacedNetwork(
                    network,
                    position,
                    1,
                    when,
                    when,
                    when,
                    network.connected,
                )
                continue
            stronger = network.strength > current.network.strength
            best[network.key] = PlacedNetwork(
                network if stronger else current.network,
                position if stronger else current.position,
                current.seen if again else current.seen + 1,
                min(current.first_seen, when),
                max(current.last_seen, when),
                when if stronger else current.best_seen,
                current.connected or network.connected,
            )
    located = {
        key: replace(
            item,
            estimate=_estimate_from(seen_from.get(key, [])),
            route_estimate=_route_estimate_from(seen_along.get(key, {})),
        )
        for key, item in best.items()
    }
    result = Reconciliation(waypoints, scans, placed, before, after, [], streets)
    result.networks = sorted(located.values(), key=lambda item: result.route_order(item.position))
    return result


# --- The report --------------------------------------------------------------


def format_confusable(pairs: Sequence[tuple[str, ...]]) -> str:
    """The warning for crossings written both ways round, or nothing when there are none."""
    if not pairs:
        return ""
    lines = [
        "These crossings are written both ways round, so each is being taken for two",
        "different corners and the blocks meeting there are split in two:",
    ]
    for pair in pairs:
        lines.append("  " + " and ".join(f'"{name}"' for name in pair))
    lines.append("Settle on one spelling in the notebook, and do this again.")
    return "\n".join(lines)


def _pinning(result: Reconciliation) -> str:
    """How many of the placed access points the walk never really established.

    Most of what a walk hears is not on the street it walked: it is inside the
    block, or a street over, heard faintly from wherever you happened to be. The
    estimate still puts a point on the map for it, and without this line that
    point looks exactly like one you walked straight past.
    """
    placed = [
        item.estimate or item.route_estimate
        for item in result.networks
        if item.estimate is not None or item.route_estimate is not None
    ]
    loose = sum(1 for one in placed if one is not None and one.barely_pinned)
    if not loose:
        return "Every one of them was pinned down by the walk."
    return (
        f"{loose} of those {len(placed)} were barely pinned down: the signal hardly moved "
        "the estimate\nfrom the middle of where you walked, so read them as heard from "
        "around there, not found."
    )


def _caveats(estimate: Estimate | RouteEstimate) -> str:
    """What to say next to an estimate that the walk did not really establish."""
    said = []
    if estimate.barely_pinned:
        said.append("barely pinned down")
    if estimate.few_sightings:
        said.append("few sightings")
    return f" ({', '.join(said)})" if said else ""


def format_report(result: Reconciliation, with_scans: bool = False) -> str:
    """Human-readable reconciliation report."""
    w = result.waypoints
    lines = [
        (
            f"Crossings: {len(w)}, from {w[0].time.strftime('%H:%M:%S')} {w[0].name} "
            f"to {w[-1].time.strftime('%H:%M:%S')} {w[-1].name}"
        ),
        (
            f"Scans: {len(result.placed)} placed, {result.before} before the first crossing, "
            f"{result.after} after the last"
        ),
        (
            f"Networks placed: {len(result.networks)} "
            f"({sum(1 for n in result.networks if n.network.open)} open)"
        ),
        "",
    ]
    if with_scans:
        lines.append("Scans along the route:")
        for placed_scan in result.placed:
            lines.append(
                f"  {_when(placed_scan.scan).strftime('%H:%M:%S')}  "
                f"{len(placed_scan.scan.networks):3d} networks  "
                f"{placed_scan.position.describe()}"
            )
        lines.append("")
    total = len(result.networks)
    on_the_map = sum(1 for item in result.networks if item.estimate is not None)
    along_route = sum(1 for item in result.networks if item.route_estimate is not None)
    if on_the_map:
        rest = total - on_the_map
        note = (
            f" (the other {rest} have no coordinates to work from, and are placed along the "
            "route instead)"
            if rest
            else ""
        )
        lines.append(
            f"Access points placed on the map from every sighting: {on_the_map} of {total}{note}"
        )
        lines.append(_pinning(result))
        lines.append("")
    elif along_route:
        lines.append(
            f"Access points placed along the route from every sighting: {along_route} of {total} "
            "(no coordinates in the notebook, so each is a fraction of a stretch, not a place "
            "on a map)"
        )
        lines.append(_pinning(result))
        lines.append("")
    confusable = format_confusable(confusable_crossings(w.name for w in result.waypoints))
    if confusable:
        lines.append(confusable)
        lines.append("")
    lines.append("Networks along the route (time and place of the strongest sighting):")
    for item in result.networks:
        n = item.network
        signal = (
            f"{n.signal_dbm} dBm" if n.signal_dbm is not None else f"{n.signal_percent}% signal"
        )
        where = item.position.describe()
        coords = ""
        if item.estimate is not None:
            e = item.estimate
            coords = f"  AP near [{e.lat:.5f}, {e.lon:.5f}] +/-{e.spread_m:.0f} m{_caveats(e)}"
        elif item.route_estimate is not None:
            r = item.route_estimate
            coords = f"  AP {r.position.describe()} +/-{r.spread:.0%} of a stretch{_caveats(r)}"
        elif item.position.lat is not None:
            coords = f"  [{item.position.lat:.5f}, {item.position.lon:.5f}]"
        flag = "  OPEN" if n.open else ""
        if item.connected:
            flag += "  CONNECTED"
        lines.append(
            f"  {item.best_seen.strftime('%H:%M:%S')}  {n.ssid or '<hidden>'}"
            f"{' ' + n.bssid if n.bssid else ''}  {signal}, seen {item.seen}x  "
            f"{where}{coords}{flag}"
        )
    return "\n".join(lines)


# --- Is it any better? -------------------------------------------------------


@dataclass(frozen=True)
class HeldOut:
    """One crossing taken out of the notebook and predicted back from the scans."""

    waypoint: Waypoint
    by_movement: float
    by_time: float

    @property
    def better(self) -> bool:
        return self.by_movement < self.by_time


def check_pace(
    log_path: str | Path,
    notebook_path: str | Path,
    streets: StreetMap | None = None,
    outing: str | None = None,
) -> list[HeldOut]:
    """Hold out each middle crossing in turn and see which method finds it again.

    The notebook is the only ground truth there is, so it is also the test: drop
    a crossing, reconcile without it, and measure how far each method puts the
    scan nearest that crossing's time from where the crossing actually was. It
    needs coordinates on the crossings -- without them there is nothing to
    measure a distance against -- and at least three in a row that have them.
    """
    records = records_for_outing(read_log(log_path), outing)
    scans = merged_scans([r for r in records if r.is_scan and r.time is not None])
    if not scans:
        raise NotebookError(f"{log_path}: no timestamped scans found")
    first = _when(scans[0])
    waypoints = read_notebook(notebook_path, first.date(), first.tzinfo, button_marks(records))

    results = []
    for index in range(1, len(waypoints) - 1):
        held = waypoints[index]
        neighbours = (waypoints[index - 1], held, waypoints[index + 1])
        target = held.coordinates
        if target is None or not all(point.has_coordinates for point in neighbours):
            continue
        without = [*waypoints[:index], *waypoints[index + 1 :]]
        nearest = min(scans, key=lambda scan: abs((_when(scan) - held.time).total_seconds()))
        errors = []
        for positions in (
            place_by_movement(scans, without, streets),
            [locate(_when(scan), without, streets) for scan in scans],
        ):
            position = positions[scans.index(nearest)]
            place = None if position is None else position.coordinates
            if place is None:
                break
            errors.append(distance_metres(place[0], place[1], target[0], target[1]))
        if len(errors) == 2:
            results.append(HeldOut(held, errors[0], errors[1]))
    return results


def format_pace_check(results: Sequence[HeldOut]) -> str:
    """Human-readable verdict on whether reading the pace beats reading the clock."""
    if not results:
        return (
            "Nothing to check: this needs coordinates on at least three crossings in a "
            "row, so that a held-out one has something to be measured against."
        )
    lines = [
        "Crossings held out, and how far each method put the nearest scan from them:",
        f"  {'crossing':<34} {'by movement':>12} {'by clock':>10}",
    ]
    for held in results:
        mark = "  <-" if held.better else ""
        lines.append(
            f"  {held.waypoint.name[:34]:<34} {held.by_movement:>10.0f} m "
            f"{held.by_time:>8.0f} m{mark}"
        )
    movement = sum(r.by_movement for r in results) / len(results)
    clock = sum(r.by_time for r in results) / len(results)
    lines.append("")
    lines.append(f"  mean error: {movement:.0f} m by movement, {clock:.0f} m by clock")
    if movement < clock:
        lines.append(f"  Reading the pace wins by {clock - movement:.0f} m on average.")
    elif movement > clock:
        lines.append(
            f"  The clock wins by {movement - clock:.0f} m: keep --pace clock on this route."
        )
    else:
        lines.append("  Nothing to choose between them here.")
    return "\n".join(lines)


# --- The same stretch, walked twice ------------------------------------------


@dataclass(frozen=True)
class Pass:
    """One walk along one stretch of street, and where it put each network.

    `places` is the weighted centroid of every sighting this pass made of a
    network, as a fraction of the stretch, always measured from the same end
    whichever way the pass was walked, so that two passes can be compared.
    """

    segment: int
    start: Waypoint
    end: Waypoint
    forwards: bool
    places: dict[str, float]
    first: datetime
    last: datetime


@dataclass(frozen=True)
class RepeatedStretch:
    """A stretch of street the route walked more than once.

    An out and back is a small controlled experiment left inside an ordinary
    walk: the same access points, the same street, the pace and the direction
    the only things that changed. Two numbers come out of it, and neither needs
    a single coordinate.

    `disagreement` is how far apart the passes put a network, on average: the
    honest error bar on placing an access point along a route, measured against
    nothing but the walk itself.

    `shift` is half the gap between the two directions, and it is systematic
    rather than random. A scan sweeps its channels over seconds and is stamped
    when it finishes, so every access point was heard a little before it was
    recorded, which places it a little further along than it was, in whichever
    direction the operator was walking. Walking back reverses that, so the two
    estimates straddle the truth and their midpoint cancels the lag. It also
    absorbs whatever else differed between the passes (which side of the street,
    which shoulder the laptop hung from), so it is an upper bound on the lag,
    not a clean measurement of it.
    """

    stretch: Stretch
    passes: list[Pass]
    shared: list[str]

    @property
    def name_from(self) -> str:
        return self.stretch.name_from

    @property
    def name_to(self) -> str:
        return self.stretch.name_to

    @property
    def length_m(self) -> float | None:
        return self.stretch.length_m

    def metres(self, fraction: float) -> float | None:
        return self.stretch.metres(fraction)

    @property
    def disagreement(self) -> float | None:
        """How far apart the passes put a network, in stretches, on average."""
        spreads = [
            max(places) - min(places) for places in map(self._places_of, self.shared) if places
        ]
        return sum(spreads) / len(spreads) if spreads else None

    @property
    def shift(self) -> float | None:
        """Half the gap between the directions; None without a pass each way."""
        there = [one for one in self.passes if one.forwards]
        back = [one for one in self.passes if not one.forwards]
        if not there or not back:
            return None
        gaps = []
        for key in self.shared:
            ahead = [one.places[key] for one in there if key in one.places]
            behind = [one.places[key] for one in back if key in one.places]
            if ahead and behind:
                gaps.append(sum(ahead) / len(ahead) - sum(behind) / len(behind))
        return (sum(gaps) / len(gaps)) / 2 if gaps else None

    def _places_of(self, key: str) -> list[float]:
        return [one.places[key] for one in self.passes if key in one.places]


def _one_pass(
    segment: int,
    forwards: bool,
    placed_scans: Sequence[PlacedScan],
    waypoints: Sequence[Waypoint],
) -> Pass:
    """Where one walk along one stretch put each of the networks it heard."""
    weighted: dict[str, float] = {}
    totals: dict[str, float] = {}
    for placed_scan in placed_scans:
        fraction = placed_scan.position.fraction
        if not forwards:
            fraction = 1.0 - fraction
        for network in placed_scan.scan.networks:
            if not network.has_signal or not network.identified:
                continue
            weight = signal_weight(network.strength)
            totals[network.key] = totals.get(network.key, 0.0) + weight
            weighted[network.key] = weighted.get(network.key, 0.0) + weight * fraction
    places = {key: weighted[key] / total for key, total in totals.items() if total > 0}
    times = [_when(placed_scan.scan) for placed_scan in placed_scans]
    return Pass(
        segment,
        waypoints[segment],
        waypoints[segment + 1],
        forwards,
        places,
        min(times),
        max(times),
    )


def repeated_stretches(result: Reconciliation) -> list[RepeatedStretch]:
    """The stretches this route walked more than once, and what each pass said."""
    scans_of: dict[int, list[PlacedScan]] = {}
    for placed_scan in result.placed:
        segment = result.waypoints.index(placed_scan.position.start)
        scans_of.setdefault(segment, []).append(placed_scan)

    repeated = []
    for stretch in route_stretches(result.waypoints, result.streets):
        walked = [
            (segment, forwards)
            for segment, forwards in zip(stretch.segments, stretch.forwards, strict=True)
            if segment in scans_of
        ]
        if len(walked) < 2:
            continue
        passes = [
            _one_pass(segment, forwards, scans_of[segment], result.waypoints)
            for segment, forwards in walked
        ]
        heard: dict[str, int] = {}
        for one in passes:
            for key in one.places:
                heard[key] = heard.get(key, 0) + 1
        repeated.append(
            RepeatedStretch(stretch, passes, sorted(key for key, n in heard.items() if n > 1))
        )
    return repeated


def check_passes(
    log_path: str | Path,
    notebook_path: str | Path,
    by_movement: bool = True,
    streets: StreetMap | None = None,
    outing: str | None = None,
) -> list[RepeatedStretch]:
    """Reconcile, then compare the passes over any stretch walked more than once."""
    return repeated_stretches(
        reconcile(log_path, notebook_path, by_movement=by_movement, streets=streets, outing=outing)
    )


def _and_metres(stretch: RepeatedStretch, fraction: float) -> str:
    metres = stretch.metres(fraction)
    return "" if metres is None else f" ({metres:.0f} m)"


def format_pass_check(stretches: Sequence[RepeatedStretch]) -> str:
    """Human-readable verdict on how well the passes over one stretch agree."""
    if not stretches:
        return (
            "Nothing to check: no stretch between two crossings was walked more than once.\n"
            "Walk a block, turn round at the corner and walk it back, and this says how far "
            "apart the two passes placed the same access points."
        )
    lines = ["Stretches walked more than once, and how far apart the passes put the networks:", ""]
    disagreements = []
    for stretch in stretches:
        length = "" if stretch.length_m is None else f", {stretch.length_m:.0f} m"
        lines.append(f'  "{stretch.name_from}" to "{stretch.name_to}"{length}')
        for one in stretch.passes:
            lines.append(
                f"    {one.first.strftime('%H:%M:%S')} to {one.last.strftime('%H:%M:%S')}, "
                f"walked {'there' if one.forwards else 'back'}"
            )
        lines.append(f"    {len(stretch.shared)} networks heard on more than one pass")
        disagreement = stretch.disagreement
        if disagreement is None:
            lines.append("    nothing heard on two passes: nothing to compare")
            lines.append("")
            continue
        disagreements.append(disagreement)
        lines.append(
            f"    the passes disagree by {disagreement:.0%} of the stretch"
            f"{_and_metres(stretch, disagreement)} on average"
        )
        shift = stretch.shift
        if shift is not None:
            lines.append(
                f"    shift in the direction of travel: {shift:.0%}"
                f"{_and_metres(stretch, abs(shift))}, which is what a scan's lag looks like"
            )
        else:
            lines.append("    every pass went the same way: no shift to measure")
        lines.append("")
    if disagreements:
        mean = sum(disagreements) / len(disagreements)
        lines.append(
            f"  Mean disagreement over {len(disagreements)} "
            f"{'stretch' if len(disagreements) == 1 else 'stretches'}: {mean:.0%} of a stretch."
        )
        lines.append(
            "  That is the error bar on placing an access point along a route, measured "
            "against nothing but the walk itself."
        )
    return "\n".join(lines)
