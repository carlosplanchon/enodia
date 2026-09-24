"""A Wi-Fi fingerprint map: where you are, from what you can hear.

Reconciling a walk turns a log into a map. This turns the map back into a
compass. Every scan a reconciliation placed is already a fingerprint, the set of
access points in view with their strengths, tied to a place, so a map is nothing
but those scans kept. Given a fresh scan, the nearest fingerprints say where you
probably stand.

This is scene analysis, not trilateration: it never asks where an access point
is, only whether this pattern has been heard before and where. That sidesteps
the weakest part of the model (a centroid is pulled towards the middle of its
sightings, and a straight street cannot say which side of it a router is on),
and it degrades into the answer this project actually wants, a fraction of the
way between two street crossings, with no coordinates anywhere.

What it cannot do is worth saying plainly. It only places you where you have
already walked. Fingerprints rot, since routers are replaced and moved. Signal
strengths are not comparable between one radio and another, which is why which
networks are in view counts for more here than how loud they are. And the floor
on its accuracy is the error of the reconciliation that placed the fingerprints,
not anything about the matching.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import pairwise
from math import exp, log
from pathlib import Path
from typing import Any

import ifpeek

from enodia.geocode import MAX_WALKING_SPEED_MS
from enodia.netlog import (
    SeenNetwork,
    _refuse_constant,
    network_from_json,
    number,
    parse_timestamp,
    read_log,
    records_for_outing,
    seen_network,
)
from enodia.reconcile import (
    NotebookError,
    Reconciliation,
    confusable_crossings,
    corner_streets,
    describe_fraction,
    distance_metres,
    folded,
    format_confusable,
    merged_scans,
    reconcile,
)
from enodia.streets import StreetMap

# --- A place, in a frame that does not depend on which walk saw it -----------

# How near a mark an answer has to be for it to be said as that mark: "at the
# corner of" rather than "97% of the way". Measured on the sample and on the
# first real outing, held out as `--check-map` holds them: at 15 m, 21 of 26
# answers said at a corner on the sample had the scan within 25 m of it, and at
# 30 m only 37 of 57. The real outing was flat either side, 63% at 15 m and 65%
# at 20 m. And a stretch whose length nobody wrote down is taken to be a block
# of a hundred metres, which is what a block is in Montevideo and in Dolores.
AT_CORNER_M = 15.0
BLOCK_M = 100.0


def canonical(name_from: str, name_to: str, fraction: float) -> tuple[str, str, float]:
    """One stretch of street, named the same way whichever direction walked it.

    A reconciliation places a scan relative to the crossings in the order the
    notebook listed them, which is the order they were walked. Walk the block
    back, or walk it again another day from the other end, and the same doorway
    comes out as `(B, A, 0.3)` where it was once `(A, B, 0.7)`. Stored like that
    a map is quietly inconsistent with itself, and neighbours that should agree
    land a whole block apart.

    So the two names are sorted, and the fraction is read from whichever end
    comes first. Alphabetical is arbitrary and that is the point: it depends on
    nothing but the names themselves, so every outing agrees on it.
    """
    if folded(name_from) <= folded(name_to):
        return name_from, name_to, fraction
    return name_to, name_from, 1.0 - fraction


@dataclass(frozen=True)
class Place:
    """Somewhere on a stretch of street, as a map file holds it."""

    name_from: str
    name_to: str
    fraction: float
    lat: float | None = None
    lon: float | None = None
    length_m: float | None = None

    @property
    def stretch(self) -> tuple[str, str]:
        """The stretch this place is on, named as the notebook named it."""
        return (self.name_from, self.name_to)

    @property
    def key(self) -> tuple[str, str]:
        """What decides whether two places are on the same stretch of street."""
        return (folded(self.name_from), folded(self.name_to))

    @property
    def coordinates(self) -> tuple[float, float] | None:
        if self.lat is None or self.lon is None:
            return None
        return (self.lat, self.lon)

    def metres(self, fraction: float) -> float | None:
        """A fraction of this stretch in metres, when its length is known."""
        return None if self.length_m is None else fraction * self.length_m

    def corner(self, within_m: float = AT_CORNER_M) -> str | None:
        """The mark this place is at, when it is near enough to one to say so, or None.

        Near enough is `within_m` along the stretch, and on a stretch too short to
        be anywhere else, the nearer of its two marks.
        """
        length = self.length_m if self.length_m else BLOCK_M
        to_first, to_second = self.fraction * length, (1.0 - self.fraction) * length
        if min(to_first, to_second) > within_m:
            return None
        return self.name_from if to_first <= to_second else self.name_to

    def describe(self) -> str:
        return describe_fraction(self.name_from, self.name_to, self.fraction)


@dataclass(frozen=True)
class Fingerprint:
    """One scan, and the place it was taken from."""

    place: Place
    networks: tuple[SeenNetwork, ...]
    outing: str = ""
    walk: str = ""
    time: datetime | None = None

    @property
    def keys(self) -> set[str]:
        return {network.key for network in self.networks if network.identified}

    @property
    def signals(self) -> dict[str, float | None]:
        return {
            n.key: (n.strength if n.has_signal else None) for n in self.networks if n.identified
        }


# --- Building the map --------------------------------------------------------


def outing_name(result: Reconciliation) -> str:
    """What to call this outing in the map: when it began, and which walk it was.

    Not the log's filename. `--log walk.jsonl` reused every week would give every
    outing the same name, and then the map would refuse the second one as
    already added, while `check_map` would take two different walks for one and
    hold them out together.

    Nor the clock alone, which is what this was and what it could not carry. The
    timestamp has one second of resolution and two walks can begin inside one
    second, so the walk itself says who it is: the loop writes a token on every
    scan it records, and it is put after the date, which stays because a name in
    the map that a person can recognise is worth the nine characters. A log
    written before the token existed still falls back to the clock, the way
    `merged_scans` falls back to it for a log written before `cycle` did.
    """
    if not result.placed:
        return ""
    scan = result.placed[0].scan
    when = scan.time
    if when is None:  # pragma: no cover - reconcile places no scan without a time
        return ""
    return f"{when.isoformat()}/{scan.outing}" if scan.outing else when.isoformat()


def fingerprints_from(result: Reconciliation, outing: str) -> list[Fingerprint]:
    """Every placed scan of one reconciliation, as one fingerprint each.

    One per placed scan, with no regrouping here, because the grouping is
    already done: `reconcile` runs `merged_scans` first, which folds each
    cycle's interfaces into a single record. Doing it again on the timestamp
    undid that, since the log keeps time to the second and two cycles can share
    one. The second place then vanished and its networks were transplanted onto
    the first, which is the mistake `cycle` was introduced to end, one layer
    further down.
    """
    prints = []
    for placed_scan in result.placed:
        when, position = placed_scan.scan.time, placed_scan.position
        name_from, name_to, fraction = canonical(
            position.start.name, position.end.name, position.fraction
        )
        length = position.length_m
        place = position.coordinates
        networks = {
            network.key: network for network in placed_scan.scan.networks if network.identified
        }
        segment = result.waypoints.index(position.start)
        prints.append(
            Fingerprint(
                Place(
                    name_from,
                    name_to,
                    fraction,
                    None if place is None else place[0],
                    None if place is None else place[1],
                    length,
                ),
                tuple(networks.values()),
                outing,
                f"{outing}#{segment}",
                when,
            )
        )
    return prints


def _record(fingerprint: Fingerprint) -> dict[str, Any]:
    place = fingerprint.place
    record: dict[str, Any] = {}
    if fingerprint.time is not None:
        record["time"] = fingerprint.time.isoformat()
    record.update(
        outing=fingerprint.outing,
        walk=fingerprint.walk,
        **{"from": place.name_from, "to": place.name_to},
        fraction=round(place.fraction, 4),
    )
    if place.lat is not None and place.lon is not None:
        record.update(lat=round(place.lat, 6), lon=round(place.lon, 6))
    if place.length_m is not None:
        record["length_m"] = round(place.length_m, 1)
    networks = []
    for network in fingerprint.networks:
        entry: dict[str, Any] = {"ssid": network.ssid, "bssid": network.bssid}
        if network.signal_dbm is not None:
            entry["signal_dbm"] = network.signal_dbm
        if network.signal_percent is not None:
            entry["signal_percent"] = network.signal_percent
        networks.append(entry)
    record["networks"] = networks
    return record


@contextmanager
def map_locked(target: Path) -> Iterator[None]:
    """Hold the map while it is read, added to and put back.

    Adding an outing is a read, a change and a write, and two of those running
    at once both read the map as it was, both wrote their own outing onto that,
    and the second one to finish left the first one's walk nowhere: no error,
    no warning, a map quietly missing an outing somebody walked. A lock beside
    the file closes that window, and closes the one where both of them decide
    the outing is not on the map yet and both add it.

    The lock is its own file rather than the map itself, because the map is
    replaced rather than written in place, and a lock on the old file protects
    nothing once the name points at a new one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(target.with_name(target.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        os.close(handle)


def write_map(
    path: str | Path, fingerprints: Iterable[Fingerprint], unless_outing: str | None = None
) -> int:
    """Add fingerprints to a map file, all of them or none, and say how many.

    One JSON object per line, the same shape as everything else Enodia writes.
    Unlike the log, which is appended to line by line because a walk that loses
    power should keep everything already written, this is written whole to a
    file beside it and moved into place. `add_to_map` treats a map holding any
    of an outing's fingerprints as holding the outing, so an append that stopped
    half way through left a walk a third of the way in and no way to finish it:
    running the command again found the outing already there and added nothing.
    Either the whole walk is on the map or none of it is, and the second is a
    state the same command can still repair.
    """
    rows = [json.dumps(_record(one), ensure_ascii=False) for one in fingerprints]
    if not rows:
        return 0
    target = Path(path)
    with map_locked(target):
        return _write_map_locked(target, rows, unless_outing)


def _write_map_locked(target: Path, rows: list[str], unless_outing: str | None) -> int:
    """The read, the change and the write, with the map held for all three."""
    if unless_outing is not None and unless_outing in {
        one.outing for one in read_map(target) if one.outing
    }:
        # Asked under the lock and not before it, so that two runs adding the
        # same outing cannot both find it absent and both put it on.
        return 0
    kept = target.read_text(encoding="utf-8") if target.exists() else ""
    if kept and not kept.endswith("\n"):
        kept += "\n"
    # The mode the map already had, or one only its owner can read. A file
    # beside it is born with whatever the umask says, so moving it into place
    # quietly reopened a map somebody had deliberately shut: this one holds the
    # names and hardware addresses of the neighbours' routers, keyed to the
    # corners they sit near, and who can read it is not a detail.
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    # The name is unguessable and the file is created with O_EXCL, which is what
    # `mkstemp` is for. A fixed name beside the map, opened for writing, was a
    # hole: anybody who could write in that directory could leave a symlink
    # waiting under it, and the next outing would pour the map through the link
    # into whatever file it pointed at and then leave the map itself as that
    # link. O_EXCL refuses a path that already exists, symlink included, and two
    # copies of Enodia running at once no longer share a temporary either.
    handle, named = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".new")
    beside = Path(named)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(kept)
            f.write("\n".join(rows) + "\n")
            os.fchmod(f.fileno(), mode)
            f.flush()
            # On disk before the move, so a power cut cannot leave the name
            # pointing at a file whose contents never arrived.
            os.fsync(f.fileno())
        os.replace(beside, target)
        # The contents were on disk before the move. The move itself lives in
        # the directory, and until that is on disk too a power cut can leave the
        # name pointing at the file it used to. One more fsync buys the last
        # step of the promise the rest of this is making.
        folder = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    finally:
        # Gone already after a move that worked. Still there after one that did
        # not, and a directory filling with half-written maps is its own problem.
        beside.unlink(missing_ok=True)
    return len(rows)


def _fingerprint_from_json(fields: dict[str, Any]) -> Fingerprint | None:
    """One line of a map, or None when it is not a fingerprint.

    Every number is checked on the way in, and not merely for being present. A
    map is a file that gets copied about, merged and hand-edited, and JSON
    carries a string where a latitude belongs without complaining. Found later,
    that is a TypeError from inside an average which had no reason to doubt its
    own input.
    """
    name_from, name_to = fields.get("from"), fields.get("to")
    fraction = number(fields.get("fraction"), 0.0, 1.0)
    if not isinstance(name_from, str) or not isinstance(name_to, str) or fraction is None:
        return None
    lat, lon = number(fields.get("lat"), -90.0, 90.0), number(fields.get("lon"), -180.0, 180.0)
    stamp = fields.get("time")
    networks = fields.get("networks")
    return Fingerprint(
        Place(
            name_from,
            name_to,
            fraction,
            lat if lon is not None else None,  # half a coordinate is no coordinate
            lon if lat is not None else None,
            number(fields.get("length_m"), 0.0),
        ),
        tuple(
            network_from_json(n)
            for n in (networks if isinstance(networks, list) else [])
            if isinstance(n, dict)
        ),
        str(fields.get("outing") or ""),
        str(fields.get("walk") or ""),
        parse_timestamp(stamp) if isinstance(stamp, str) else None,
    )


def read_map(path: str | Path) -> list[Fingerprint]:
    """The fingerprints in a map file. A map that does not exist yet is empty.

    Only a missing file is empty. A map that exists and cannot be read, because
    of its permissions or because it is a directory, raises instead: otherwise
    each of those comes back as "not on the map", which is a sentence about the
    street when it was really a sentence about the file.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    prints = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            fields = json.loads(line, parse_constant=_refuse_constant)
        except ValueError:
            continue
        if isinstance(fields, dict):
            found = _fingerprint_from_json(fields)
            if found is not None:
                prints.append(found)
    return prints


def add_to_map(
    map_path: str | Path,
    log_path: str | Path,
    notebook_path: str | Path,
    by_movement: bool = True,
    streets: StreetMap | None = None,
    outing: str | None = None,
) -> tuple[int, str]:
    """Reconcile one outing and add its scans to the map. Returns (added, outing).

    An outing already in the map is refused rather than added again: appending
    it twice would double every fingerprint it holds and pull the neighbours
    towards it, and it would break the one thing `check_map` relies on, that a
    walk appears once.
    """
    result = reconcile(
        log_path, notebook_path, by_movement=by_movement, streets=streets, outing=outing
    )
    name = outing_name(result)
    if not name:
        return 0, ""
    # Asked twice on purpose: once here, to save reconciling a walk the map
    # already holds, and once inside the lock, where the answer cannot go stale
    # between the asking and the writing.
    if name in {fingerprint.outing for fingerprint in read_map(map_path)}:
        return 0, name
    return write_map(map_path, fingerprints_from(result, name), unless_outing=name), name


# --- Finding yourself on it --------------------------------------------------

NEIGHBOURS = 5
# How far apart the fingerprints behind one answer may sit and still be one
# place. A stretch is a block, so when the map knows its length that is the
# measure, with room for crossings noted loosely. Without a length there is
# nothing to scale against, so the fallback is deliberately loose: it is there
# to catch two towns sharing a pair of crossing names, not to second-guess a
# long block, and a false alarm here withholds coordinates the map did have.
ONE_PLACE_BLOCKS = 3.0
ONE_PLACE_M = 5000.0
SIMILARITY_FLOOR = 0.15
RSSI_SCALE = 10.0
SIGNAL_UNKNOWN = 0.75
DOMINANCE = 0.6
# How far apart in time two scans of one walk can be and still be one look at
# one place. Fifteen seconds each way is the quarter of a minute LOOK_BACK is
# built on too, twenty metres at a walking pace: near enough that the scans
# see one place, wide enough that one lucky reading is not a look by itself.
ONE_LOOK_S = 15.0
# How much likelier the stretch is whose best look matched a tenth better:
# three to one, so two tenths is nine to one. This turns a similarity into a
# share of the evidence, which the tie and the path are judged on. Measured on
# the sample: at two to one the path lagged a scan more at each mark and four
# scans mid-block were called ties, at four to one nothing changed but one tie
# fewer at a mark, where a tie is the honest answer.
ODDS_PER_TENTH = 3.0


@dataclass(frozen=True)
class Rarity:
    """What each network in a map says about where you are: the rarer, the more.

    A router heard from thirty metres of one street places you; one heard down
    ten blocks hardly does, and counting the two alike let the long-range ones
    drown the short. The weight is `log(1 + N / df)`, `N` the fingerprints in
    the map and `df` how many of them hear the network: heard in all of them it
    still weighs `log 2`, never nothing, since it says you are on the map even
    if not where; heard in one it weighs `log(1 + N)`. A network the map never
    heard weighs as one heard once, which is the treatment it always had: it
    enters the union and lowers every match alike, and enough of them is what
    "too much of it has changed" is for.
    """

    weights: Mapping[str, float]
    unknown: float

    def __call__(self, key: str) -> float:
        return self.weights.get(key, self.unknown)


def key_weights(fingerprints: Sequence[Fingerprint]) -> Rarity:
    """The rarity of every network in a map, from one pass over it."""
    counted: dict[str, int] = {}
    for one in fingerprints:
        for key in one.keys:
            counted[key] = counted.get(key, 0) + 1
    total = len(fingerprints)
    return Rarity({key: log(1 + total / seen) for key, seen in counted.items()}, log(1 + total))


def similarity(
    query: Mapping[str, float | None],
    fingerprint: Fingerprint,
    by_signal: bool = False,
    weights: Rarity | None = None,
) -> float:
    """How alike a scan and a fingerprint are: 1 identical, 0 nothing in common.

    The first term is which access points are in view, as a Jaccard similarity
    over their BSSIDs, each weighed by how rare it is in the map (`Rarity`)
    when `weights` is given, and alike when not. It is the robust half: it
    survives a router dropping out of one scan, and it means the same thing on
    any radio.

    `by_signal` multiplies that by how closely the shared access points matched
    in strength, `exp(-mean|dRSSI| / RSSI_SCALE)`. It is the precise half and
    the fragile one, since one radio reads several dB apart from another and the
    operator's own body shadows differently walking one way than the other. With
    nothing to compare it falls back to the set alone, which is why the two are
    kept separate and `check_map` reports both. A fingerprint that kept no levels
    at all is scored at `SIGNAL_UNKNOWN` of its overlap rather than left alone,
    since counting "nothing to compare" as a perfect match would rank every
    level-less fingerprint above every measured one.
    """
    keys = set(query)
    here = fingerprint.keys
    union = keys | here
    if not union:
        return 0.0
    weigh = weights if weights is not None else (lambda key: 1.0)
    alike = sum(weigh(key) for key in keys & here) / sum(weigh(key) for key in union)
    if not by_signal or alike <= 0.0:
        return alike
    signals = fingerprint.signals
    deltas = []
    for key in keys & here:
        mine, theirs = query[key], signals[key]
        if mine is not None and theirs is not None:
            deltas.append(abs(mine - theirs))
    if not deltas:
        return alike * SIGNAL_UNKNOWN
    return alike * exp(-(sum(deltas) / len(deltas)) / RSSI_SCALE)


@dataclass(frozen=True)
class Location:
    """Where a scan says you probably are."""

    place: Place
    score: float
    matches: int
    spread: float
    alternative: Place | None = None
    when: datetime | None = None
    outings: tuple[str, ...] = ()
    scattered_m: float | None = None
    settled: int = 0
    alone: Place | None = None

    @property
    def uncertain(self) -> bool:
        """True when a second stretch matched nearly as well and nothing settled which.

        `settled` is how many of the scans before this one had a say, when they
        settled a tie or overruled the scan: `alternative` then names the
        stretch they ruled out of a tie, and `alone` the stretch the scan by
        itself would have been put on.

        Two stretches that meet at the corner both answers are at are not a
        doubt about where you are. A scan at a corner hears the two blocks that
        meet there alike, which is exactly when the tie comes up, and "at the
        corner of X, uncertain" said the one thing and then took it back.
        """
        if self.alternative is None or self.settled:
            return False
        here, there = self.place.corner(), self.alternative.corner()
        return here is None or there is None or folded(here) != folded(there)

    @property
    def corner(self) -> str | None:
        """The mark this answer is at, when it is near enough to one to say so."""
        return self.place.corner()

    @property
    def scattered(self) -> bool:
        """True when the fingerprints behind this were too far apart to be one place."""
        return self.scattered_m is not None

    def describe(self) -> str:
        return said(self.place)


def said(place: Place) -> str:
    """How an answer reads: the mark when it is at one, and how far along otherwise.

    Not how a reconciliation reads, which is `Place.describe` and keeps the
    exact fraction, since there the fraction is what was worked out and the
    report is about the working. An answer to "where am I" is a place for
    somebody standing in the street, and a few metres from a corner the corner
    is the true answer and 97% is a precision nobody has. A mark named after
    two streets is a corner; one that is a place, a plaza, is just where you
    are.
    """
    mark = place.corner()
    if mark is None:
        return place.describe()
    return f'at the corner of "{mark}"' if corner_streets(mark) else f'at "{mark}"'


def _spread_of(places: Sequence[Place]) -> float | None:
    """How far apart the places behind an answer sit, or None without coordinates."""
    known = [where for one in places if (where := one.coordinates) is not None]
    if len(known) < 2:
        return None
    return max(distance_metres(*here, *there) for here in known for there in known)


def _gather(pairs: Sequence[tuple[float, Place]]) -> tuple[Place, float]:
    """The weighted middle of a set of matched places, and how spread out they were."""
    total = sum(score for score, _ in pairs)
    fraction = sum(score * place.fraction for score, place in pairs) / total
    spread = sum(score * abs(place.fraction - fraction) for score, place in pairs) / total
    known = [(score, where) for score, place in pairs if (where := place.coordinates) is not None]
    lat = lon = None
    if known:
        weight = sum(score for score, _ in known)
        lat = sum(score * where[0] for score, where in known) / weight
        lon = sum(score * where[1] for score, where in known) / weight
    first = pairs[0][1]
    length = next((place.length_m for _, place in pairs if place.length_m is not None), None)
    return Place(first.name_from, first.name_to, fraction, lat, lon, length), spread


@dataclass(frozen=True)
class Look:
    """One walk's look at a scan: the part of the walk that matched it best, as one witness.

    Consecutive scans of one pass see almost the same networks and are one
    look at one place, not several. Counted one by one they were: a pass that
    scanned every five seconds filled every seat among the neighbours and
    outvoted a single better match from another outing, so the map favoured
    whoever had walked slowest. Each walk speaks once instead, with the part
    of it that matched best: the scans taken within `ONE_LOOK_S` of one of
    them, whichever such neighbourhood has the highest mean similarity. The
    mean and not the best single scan, because on a run of nearly alike scans
    the best one is a lucky reading until the scans beside it agree, and the
    scans beside it are also what say where the look was taken from. A walk
    that scanned rarely gets a look of one scan and no penalty for it. A
    fingerprint that names no walk, from a map older than the field or built
    by hand, is a look by itself.
    """

    place: Place  # the weighted middle of the scans in it
    score: float  # their mean similarity: what the walk says, and what it is ranked on
    best: float  # the best single scan among them
    when: datetime | None
    outing: str


def _look(scored: Sequence[tuple[float, Fingerprint]], floor: float) -> Look | None:
    """The neighbourhood of one walk that matched best, or None when none is evidence.

    The floor is applied to the look, before anything is put to a vote, and
    not to the best scan alone. Nothing that is not evidence on its own becomes
    evidence by turning up four times: four readings that were each too weak
    used to outvote one that cleared the floor, and the answer came back naming
    the street with the best of the losers, "you are here, best similarity
    11%", under a floor of 15%.
    """
    # Every scan without a time is a neighbourhood of one. The rest are sorted
    # by the clock and swept once, each with the scans within reach of it.
    neighbourhoods = [(score, [(score, one)]) for score, one in scored if one.time is None]
    timed = sorted(
        ((one.time.timestamp(), score, one) for score, one in scored if one.time is not None),
        key=lambda stamped: stamped[0],
    )
    low = high = 0
    for stamp, _, _ in timed:
        while timed[low][0] < stamp - ONE_LOOK_S:
            low += 1
        while high < len(timed) and timed[high][0] <= stamp + ONE_LOOK_S:
            high += 1
        beside = [(score, one) for _, score, one in timed[low:high]]
        neighbourhoods.append((sum(score for score, _ in beside) / len(beside), beside))
    mean, beside = max(neighbourhoods, key=lambda found: found[0])
    if mean <= 0.0 or mean < floor:
        return None
    place, _ = _gather([(score, one.place) for score, one in beside])
    return Look(
        place,
        mean,
        max(score for score, _ in beside),
        max((one.time for _, one in beside if one.time is not None), default=None),
        beside[0][1].outing,
    )


@dataclass(frozen=True)
class Candidate:
    """One stretch a scan could be on, and the evidence behind it."""

    place: Place
    spread: float
    score: float  # the best single match on this stretch
    weight: float  # how well its best look matched: what the stretches are ranked on
    matches: int
    when: datetime | None
    outings: tuple[str, ...]
    apart: float | None


def _candidates(
    fingerprints: Sequence[Fingerprint],
    networks: Iterable[SeenNetwork],
    by_signal: bool = False,
    neighbours: int = NEIGHBOURS,
    floor: float = SIMILARITY_FLOOR,
    by_rarity: bool = True,
) -> list[Candidate]:
    """The stretches a scan could be on, best first, or nothing when the map does not know.

    `by_rarity` weighs each network by how rare it is in the map (`Rarity`);
    off, every network counts the same, which is the matching as it was before
    the weights and is kept so that a real map can measure them.

    Each walk of the map speaks once, with its best look (`Look`), and the
    `neighbours` best looks are grouped by stretch. A stretch is ranked on its
    best look and not on its looks added up: added up, the stretch walked most
    often won at every mark, where the two stretches match about alike, and on
    the sample that put 29 scans across a mark where the best look alone puts
    7. Two walks that agree are reported, then, and not counted twice.
    """
    query = {n.key: (n.strength if n.has_signal else None) for n in networks if n.identified}
    if not query:
        return []
    weights = key_weights(fingerprints) if by_rarity else None
    by_walk: dict[object, list[tuple[float, Fingerprint]]] = {}
    for one in fingerprints:
        score = similarity(query, one, by_signal, weights)
        by_walk.setdefault(one.walk or id(one), []).append((score, one))
    looks = [look for scored in by_walk.values() if (look := _look(scored, floor)) is not None]
    if not looks:
        return []
    looks.sort(key=lambda look: look.score, reverse=True)

    groups: dict[tuple[str, str], list[Look]] = {}
    for look in looks[:neighbours]:
        groups.setdefault(look.place.key, []).append(look)
    ranked = sorted(
        groups.values(), key=lambda group: max(one.score for one in group), reverse=True
    )
    candidates = []
    for group in ranked:
        place, spread = _gather([(look.score, look.place) for look in group])
        candidates.append(
            Candidate(
                place,
                spread,
                # The best of this street, not the best of all of them. One
                # fingerprint of a street that lost can score higher than any
                # of the winner's while the winner still carries more
                # evidence, and printing that number beside the winner's name
                # says "this place matched 100%" about a place that matched 67%.
                max(look.best for look in group),
                max(look.score for look in group),
                len(group),
                max((look.when for look in group if look.when is not None), default=None),
                tuple(sorted({look.outing for look in group if look.outing})),
                _spread_of([look.place for look in group]),
            )
        )
    return candidates


def _shares(candidates: Sequence[Candidate]) -> list[float]:
    """Each candidate's share of the scan's evidence, from how well its best look matched.

    A similarity is not a share. Two stretches matching 86% and 69% are not a
    56/44 split: the second is the poorer match by a margin that no scan taken
    mid-block on the sample ever showed between the right stretch and a
    neighbour. `ODDS_PER_TENTH` says what a margin is worth, and the shares
    are the odds normalised, which is what `DOMINANCE` and the path read.
    """
    odds = [ODDS_PER_TENTH ** (10.0 * one.weight) for one in candidates]
    total = sum(odds)
    return [one / total for one in odds]


def _tied(ranked: Sequence[Candidate]) -> bool:
    """Whether the second stretch matched nearly as well as the first."""
    return len(ranked) > 1 and _shares(ranked)[0] < DOMINANCE


def _location(
    candidate: Candidate, alternative: Place | None, settled: int = 0, alone: Place | None = None
) -> Location:
    place = candidate.place
    far = place.metres(ONE_PLACE_BLOCKS) or ONE_PLACE_M
    if candidate.apart is not None and candidate.apart > far:
        # Two places in the map answer to the same pair of crossing names, and
        # the weighted middle of them is a point in between that is neither.
        # A stretch's identity is its names, and names are only unique if
        # somebody kept them so, which the map cannot check when it is fed
        # outing by outing. The coordinates are withheld rather than averaged:
        # an invented place on a map is worse than no place.
        return Location(
            Place(place.name_from, place.name_to, place.fraction, None, None, place.length_m),
            candidate.score,
            candidate.matches,
            candidate.spread,
            None,
            candidate.when,
            candidate.outings,
            candidate.apart,
        )
    return Location(
        place,
        candidate.score,
        candidate.matches,
        candidate.spread,
        alternative,
        candidate.when,
        candidate.outings,
        settled=settled,
        alone=alone,
    )


def locate_scan(
    fingerprints: Sequence[Fingerprint],
    networks: Iterable[SeenNetwork],
    by_signal: bool = False,
    neighbours: int = NEIGHBOURS,
    floor: float = SIMILARITY_FLOOR,
    by_rarity: bool = True,
) -> Location | None:
    """Where a scan puts you on the map, or None when the map does not know.

    None is an answer, and the important one. Off the map every fingerprint is a
    poor match and the best of them is still wrong, so below `floor` it says so
    instead of guessing confidently in the middle of a city it has never been to.

    The neighbours are grouped by stretch of street before anything is averaged.
    Two streets that both match are not averaged together: that would place you
    inside the block between them, where you certainly were not. When neither
    dominates, both are reported and the answer is marked uncertain.
    """
    ranked = _candidates(fingerprints, networks, by_signal, neighbours, floor, by_rarity)
    if not ranked:
        return None
    return _location(ranked[0], ranked[1].place if _tied(ranked) else None)


# How many of the scans before the last one get a say in a tie. Five-second
# cycles, so three of them are the last quarter of a minute of walking.
LOOK_BACK = 3


def _same_walk(here: Place, there: Place) -> bool:
    """Whether two places are on one stretch, or on stretches that share a mark."""
    return here.key == there.key or bool(set(here.key) & set(there.key))


def locate_sequence(
    fingerprints: Sequence[Fingerprint],
    scans: Sequence[Iterable[SeenNetwork]],
    sequence: str = "tie",
    by_signal: bool = False,
    neighbours: int = NEIGHBOURS,
    floor: float = SIMILARITY_FLOOR,
    by_rarity: bool = True,
) -> Location | None:
    """Where the last of a run of scans puts you, with the scans before it having a say.

    `sequence` says what say they get. `"tie"` places the last scan exactly as
    `locate_scan` places it, and only when two stretches match it about as well
    do the scans before it decide: each of them names where it was, and of the
    two, the stretch those scans were on, or one sharing a mark with it, is the
    one the walk supports, since a walk does not jump a block in five seconds.
    `"path"` asks them every time and chooses the likeliest path through all of
    them, which can overrule the last scan: see `_along_the_path`.

    Either way they choose between answers that each cleared the floor, and
    never make one up: a scan the map does not know stays unknown however sure
    the scans before it were, and a tie they cannot break, because they were
    unknown too or torn the same way, stays a tie and is reported as one.
    """
    if sequence == "path":
        return _along_the_path(fingerprints, scans, by_signal, neighbours, floor, by_rarity)
    if sequence != "tie":
        raise ValueError(f"sequence is 'tie' or 'path', not {sequence!r}")
    *before, last = scans
    ranked = _candidates(fingerprints, last, by_signal, neighbours, floor, by_rarity)
    if not ranked:
        return None
    if not _tied(ranked):
        return _location(ranked[0], None)
    votes = [0, 0]
    for networks in before[-LOOK_BACK:]:
        earlier = _candidates(fingerprints, networks, by_signal, neighbours, floor, by_rarity)
        if not earlier:
            continue
        named = [one.place for one in earlier[: 2 if _tied(earlier) else 1]]
        for index, candidate in enumerate(ranked[:2]):
            if any(_same_walk(candidate.place, place) for place in named):
                votes[index] += 1
    if votes[0] == votes[1]:
        return _location(ranked[0], ranked[1].place)
    chosen, other = (0, 1) if votes[0] > votes[1] else (1, 0)
    return _location(ranked[chosen], ranked[other].place, settled=votes[chosen])


# What a step between two scans costs on the likeliest path, as a share of
# the evidence: staying on a stretch costs nothing, stepping onto one that
# shares a mark costs this, and jumping anywhere else costs that. The jump is
# the number no walk has measured yet, which is why the path is a flag.
NEXT_STRETCH = 0.5
JUMP = 0.05


def _leans(candidates: Sequence[Candidate]) -> dict[tuple[str, str], tuple[float, Candidate]]:
    """Each candidate's share of its scan's evidence, in logarithms, by stretch.

    A scan that is itself torn, tied under `DOMINANCE`, contributes its
    candidates and no lean at all. Nothing that is not evidence on its own
    becomes evidence by turning up four times: without this, twenty seconds
    standing at a lookalike corner turned four ties into a verdict, since four
    small leans the same way added up to one where three did not.
    """
    if _tied(candidates):
        return {one.place.key: (0.0, one) for one in candidates}
    return {
        one.place.key: (log(share), one)
        for share, one in zip(_shares(candidates), candidates, strict=True)
    }


def _step_cost(here: Place, there: Place) -> float:
    if here.key == there.key:
        return 0.0
    return log(NEXT_STRETCH) if _same_walk(here, there) else log(JUMP)


def _along_the_path(
    fingerprints: Sequence[Fingerprint],
    scans: Sequence[Iterable[SeenNetwork]],
    by_signal: bool,
    neighbours: int,
    floor: float,
    by_rarity: bool = True,
) -> Location | None:
    """Where the last of a run of scans puts you, on the likeliest path through all of them.

    A small Viterbi over stretches. Each scan's candidates are its states, and
    a candidate's emission is the share of that scan's evidence it carries, so
    a scan that leans 62/38 leans a little and a scan the map has one answer
    for leans all the way, while a torn one does not lean (see `_leans`).
    Between scans, staying on a stretch is free, stepping onto one that shares
    a mark costs `NEXT_STRETCH`, and any other jump costs `JUMP`. The answer is
    the last stretch of the best path, and the path can only end on a candidate
    of the last scan, which is what keeps "never make one up" true. A scan
    before the last one that the map does not know has no emission and is left
    out of the run.

    The path can overrule the last scan, both ways. It corrects a scan that was
    sure and wrong, when the scans before it were sure of another street, and
    it can turn over a scan that was right, when the scans before it leaned the
    wrong way. That second half is the bet, and the report says so: `alone`
    names where the scan by itself would have gone.
    """
    *before, last = scans
    ranked = _candidates(fingerprints, last, by_signal, neighbours, floor, by_rarity)
    if not ranked:
        return None
    layers = []
    for networks in before[-LOOK_BACK:]:
        earlier = _candidates(fingerprints, networks, by_signal, neighbours, floor, by_rarity)
        if earlier:
            layers.append(earlier)
    if not layers:
        return _location(ranked[0], ranked[1].place if _tied(ranked) else None)
    layers.append(ranked)

    first = _leans(layers[0])
    scores = {key: lean for key, (lean, _) in first.items()}
    places = {key: one.place for key, (_, one) in first.items()}
    for layer in layers[1:]:
        here = _leans(layer)
        # The max is over the stretches that were states one scan back, and
        # only those: a stretch missing from the next scan dies there, and a
        # new one enters by a step from whatever was there before.
        scores = {
            key: lean + max(scores[prev] + _step_cost(places[prev], one.place) for prev in scores)
            for key, (lean, one) in here.items()
        }
        places = {key: one.place for key, (_, one) in here.items()}

    final = sorted(ranked, key=lambda one: scores[one.place.key], reverse=True)
    best = final[0]
    share = exp(scores[best.place.key]) / sum(exp(scores[one.place.key]) for one in final)
    if len(final) > 1 and share < DOMINANCE:
        # Two paths end about as well on two stretches. Uncertain, and said
        # so, even when the last scan by itself was sure.
        return _location(best, final[1].place)
    earlier = len(layers) - 1
    if _tied(ranked):
        other = ranked[1] if best is ranked[0] else ranked[0]
        return _location(best, other.place, settled=earlier)
    if best is ranked[0]:
        return _location(best, None)
    return _location(best, None, settled=earlier, alone=ranked[0].place)


# Keeping a live run to a walking pace (`Pace`). How much a walk's speed may
# change from one scan to the next, how far off one scan's place along a
# stretch is taken to be, and how long a gap starts the walk afresh. The
# first was chosen by measuring 0.1, 0.3 and 1 on the sample and on the first
# real outing: the smallest took the most of the jumps out and cost nothing in
# error, since what a scan gets wrong along a stretch is mostly a bias the
# scans beside it share, not noise they average away.
PACE_ACCELERATION = 0.1
PACE_SCAN_M = 15.0
PACE_RESET_S = 30.0


@dataclass(frozen=True)
class _Walking:
    """Where a run is along a stretch, how fast it is going, and how sure of both."""

    place: Place
    at: float  # metres from the stretch's first mark, which may be short of it or past it
    speed: float  # metres a second towards its second mark
    spread: tuple[float, float, float]  # the variance of `at`, of `speed`, and between them
    when: float


def _line(places: Sequence[Place]) -> tuple[float, float, float, float] | None:
    """A straight line of latitude and longitude against the fraction, fitted to one stretch.

    Least squares over every fingerprint of the stretch that has coordinates,
    so the fraction a run is kept to has a place on the map: a block is a
    straight line more often than not, and two passes a few metres apart
    average into the middle of the street. None without two distinct fractions
    to fit it to.
    """
    known = [(one.fraction, where) for one in places if (where := one.coordinates) is not None]
    if len({fraction for fraction, _ in known}) < 2:
        return None
    mean_f = sum(fraction for fraction, _ in known) / len(known)
    mean_lat = sum(where[0] for _, where in known) / len(known)
    mean_lon = sum(where[1] for _, where in known) / len(known)
    square = sum((fraction - mean_f) ** 2 for fraction, _ in known)
    lat = sum((fraction - mean_f) * (where[0] - mean_lat) for fraction, where in known) / square
    lon = sum((fraction - mean_f) * (where[1] - mean_lon) for fraction, where in known) / square
    return mean_lat - lat * mean_f, lat, mean_lon - lon * mean_f, lon


class Pace:
    """A live run's answers, kept to the pace somebody walks at along the stretch.

    A scan says which stretch you are on well and where along it less well:
    two in a row, five seconds apart, can put you thirty metres apart on the
    same block, which nobody walks. So the place along the stretch is followed
    with a filter of constant speed, a Kalman filter in one dimension: each
    scan moves the answer by how much it disagrees, weighed against how sure
    the walk so far is, and the speed is never more than a walk's. One scan
    that disagrees moves the dot a little and a run of them take it all the
    way, which is the difference between noise and having turned round.

    The stretch is never the filter's to choose: it is what `tie` or `path`
    said, and a stretch that shares a mark with the last one takes the walk
    over through that mark, while any other change of stretch, a stretch of no
    known length, or a gap of `PACE_RESET_S` start again from the scan. An
    answer of "not on the map" is passed through and forgets nothing, so a
    walk found again a cycle later carries on from where it was.
    """

    def __init__(
        self,
        lines: Mapping[tuple[str, str], tuple[float, float, float, float]],
        max_speed_ms: float = MAX_WALKING_SPEED_MS,
    ) -> None:
        self.lines = dict(lines)
        self.max_speed_ms = max_speed_ms
        self.walking: _Walking | None = None

    @classmethod
    def of(
        cls, fingerprints: Sequence[Fingerprint], max_speed_ms: float = MAX_WALKING_SPEED_MS
    ) -> Pace:
        """A pace for a run against this map, with a line fitted to each of its stretches."""
        by_stretch: dict[tuple[str, str], list[Place]] = {}
        for one in fingerprints:
            by_stretch.setdefault(one.place.key, []).append(one.place)
        lines = {key: line for key, places in by_stretch.items() if (line := _line(places))}
        return cls(lines, max_speed_ms)

    def _carried(self, place: Place, when: float) -> _Walking | None:
        """The walk so far, in this stretch's own measure, or None to start again."""
        walking = self.walking
        if walking is None or not 0.0 <= when - walking.when <= PACE_RESET_S:
            return None
        if walking.place.key == place.key:
            return walking
        shared = _shared_mark(walking.place, place)
        before = walking.place.length_m
        if shared is None or before is None or place.length_m is None:
            return None
        # Through the mark the two stretches share: how far short of it the
        # walk was and how fast it was heading there, turned into the new
        # stretch's measure, which may start at that mark or end at it.
        # The place and the speed turn over together or not at all, so how
        # sure the walk was of each, and of the two together, carries over.
        old_end, new_end = shared
        short = walking.at if old_end == 0.0 else before - walking.at
        towards = -walking.speed if old_end == 0.0 else walking.speed
        at = -short if new_end == 0.0 else place.length_m + short
        speed = towards if new_end == 0.0 else -towards
        return _Walking(place, at, speed, walking.spread, walking.when)

    def keep(self, found: Location | None, when: float) -> Location | None:
        """This answer with its place along the stretch kept to a walking pace."""
        if found is None:
            return None
        place = found.place
        length = place.length_m
        if length is None or length <= 0:
            self.walking = None
            return found
        seen = place.fraction * length
        noise = max(PACE_SCAN_M, found.spread * length) ** 2
        walking = self._carried(place, when)
        if walking is None:
            at, speed = seen, 0.0
            spread = (noise, self.max_speed_ms**2, 0.0)
        else:
            gap = when - walking.when
            q = PACE_ACCELERATION**2
            p_at, p_speed, p_between = walking.spread
            # Where the walk would be by now at the speed it was going, and
            # how much less sure of that the time since has made it.
            at = walking.at + walking.speed * gap
            p_at = p_at + 2 * gap * p_between + gap * gap * p_speed + q * gap**4 / 4
            p_between = p_between + gap * p_speed + q * gap**3 / 2
            p_speed = p_speed + q * gap * gap
            gain_at, gain_speed = p_at / (p_at + noise), p_between / (p_at + noise)
            miss = seen - at
            at += gain_at * miss
            speed = walking.speed + gain_speed * miss
            spread = (
                (1 - gain_at) * p_at,
                p_speed - gain_speed * p_between,
                (1 - gain_at) * p_between,
            )
        speed = max(-self.max_speed_ms, min(self.max_speed_ms, speed))
        self.walking = _Walking(place, at, speed, spread, when)
        fraction = max(0.0, min(1.0, at / length))
        line = self.lines.get(place.key)
        lat, lon = place.lat, place.lon
        if line is not None and place.coordinates is not None:
            # Only where the answer had a place to begin with: coordinates
            # withheld because two places share the stretch's names stay withheld.
            lat, lon = line[0] + line[1] * fraction, line[2] + line[3] * fraction
        return replace(found, place=replace(place, fraction=fraction, lat=lat, lon=lon))


def follow(
    fingerprints: Sequence[Fingerprint],
    scans: Iterable[Iterable[SeenNetwork]],
    sequence: str = "tie",
    by_signal: bool = False,
    by_rarity: bool = True,
) -> Iterator[Location | None]:
    """Where each scan of a live run puts you, with the scans before it having a say.

    One answer per scan, each what `locate_sequence` says of the last
    `LOOK_BACK + 1` of them, so a tie is settled by the walk the way
    `--locate LOG` settles it, and the path can be chosen the same way, on
    scans taken a few seconds apart as they happen rather than read back from
    a file. No clock and no voice, which are the command's. The scans come
    from outside rather than being taken here so that a cycle whose scan
    failed is simply not handed in: the run keeps what the cycles before it
    knew, and one cycle without a radio does not throw away the walk.
    """
    run: deque[list[SeenNetwork]] = deque(maxlen=LOOK_BACK + 1)
    for scan in scans:
        run.append(list(scan))
        yield locate_sequence(fingerprints, list(run), sequence, by_signal, by_rarity=by_rarity)


def format_location(location: Location | None, map_path: str | Path) -> str:
    """Human-readable answer to "where am I"."""
    if location is None:
        return (
            f"Not on the map: nothing in {map_path} looks like what is in view.\n"
            "Either this street has not been walked yet, or too much of it has changed."
        )
    lines = [f"You are {location.describe()}"]
    where = location.place.coordinates
    if where is not None:
        lines.append(f"  around [{where[0]:.5f}, {where[1]:.5f}]")
    spread = location.place.metres(location.spread)
    agree = "1 walk" if location.matches == 1 else f"{location.matches} walks agree"
    lines.append(
        f"  {agree}, best similarity {location.score:.0%}, "
        f"spread {location.spread:.0%} of the stretch"
        + ("" if spread is None else f" ({spread:.0f} m)")
    )
    if location.when is not None:
        lines.append(f"  from evidence last gathered {location.when.strftime('%Y-%m-%d %H:%M')}")

    def before(one: str, many: str) -> str:
        n = location.settled
        return f"The scan before it {one}" if n == 1 else f"The {n} scans before it {many}"

    if location.alternative is not None and location.settled:
        lines.append(
            f"  The scan alone could as easily be {said(location.alternative)}. "
            f"{before('settles', 'settle')} it here."
        )
    elif location.uncertain and location.alternative is not None:
        lines.append(f"  Uncertain: it could as easily be {said(location.alternative)}")
    elif location.alone is not None:
        lines.append(
            f"  The scan alone would have said {said(location.alone)}. "
            f"{before('puts', 'put')} it here."
        )
    if location.scattered_m is not None:
        lines.append(
            f"  No coordinates given: the fingerprints behind this answer are "
            f"{location.scattered_m / 1000:.0f} km apart, so two different places in the map "
            f'are called "{location.place.name_from}" and "{location.place.name_to}". '
            "Crossing names have to be unique across a map, and the middle of the two would "
            "be a place neither of them is."
        )
    return "\n".join(lines)


# --- Scanning where you stand ------------------------------------------------


class RadioBlocked(RuntimeError):
    """The Wi-Fi radio is switched off, so a scan would hear nothing."""


def scan_now(interfaces: Sequence[str] | None = None) -> list[SeenNetwork]:
    """One fresh scan of what is in view, from every radio, as one set of networks.

    Every interface and not just the first, and the same union the capture path
    makes of a cycle. The map was built from `merged_scans`, which puts both
    cards' views together, so looking yourself up with one card's half is
    training on the whole and asking with a fraction: the overlap that decides
    where you are comes out low for no reason but which radio answered.
    """
    cards = list(interfaces) if interfaces else ifpeek.get_wifi_interfaces()
    if not cards:
        raise RadioBlocked("no Wi-Fi interface found")
    seen: dict[str, SeenNetwork] = {}
    anonymous: list[SeenNetwork] = []
    answered, refused = 0, []
    for card in cards:
        try:
            stopped = ifpeek.interface_rfkill(card)
        # Not being able to read the switch is not the switch being off, and it
        # is certainly not a reason to stop asking the other cards. The scan
        # loop settled this the same way.
        except Exception as exc:  # noqa: BLE001
            print(f"Could not read the radio switch of {card}: {exc}")
            stopped = None
        if stopped:
            refused.append(f"{card}: radio {stopped} blocked (rfkill)")
            continue
        try:
            found = ifpeek.scan_access_points(interface=card, fresh=True)
        # One card refusing is not the end of the walk, the same way it is not
        # in the scan loop. A map can be built walking on wlan1 while wlan0
        # fails, and then failing to look yourself up with those same two cards
        # because wlan0 is asked first would be a strange thing to do.
        except Exception as exc:  # noqa: BLE001
            refused.append(f"{card}: {exc}")
            continue
        answered += 1
        for ap in found:
            network = seen_network(ap)
            if not network.identified:
                # Kept, and never folded: one empty key cannot stand for every
                # anonymous network the cards heard between them.
                anonymous.append(network)
            elif network.key not in seen or network.strength > seen[network.key].strength:
                seen[network.key] = network
    if not answered:
        raise RadioBlocked(", ".join(refused))
    return [*seen.values(), *anonymous]


def scans_from_log(
    log_path: str | Path, outing: str | None = None, count: int = LOOK_BACK + 1
) -> list[list[SeenNetwork]]:
    """The networks of the last cycles of one walk, oldest first, to locate without a radio.

    The last cycles and not the last records. With two interfaces the last
    record is whichever card was asked second, and half of what was in view.
    The last one is the scan to place, and the ones before it are what settles
    a tie, which a fresh scan on its own never has.

    And one walk, the last in the file unless another is named. A file can hold
    several, and the last walk in it may have nothing usable in it at all: a
    radio blocked for its whole length writes `scan_failed` and no scans. Read
    across the file, that answered with where you were last week, which is a
    worse answer than saying there is nothing here to go on.
    """
    # Timestamped, as every other reader of a log asks for. A scan without a
    # time cannot be put in order against the others, and one line missing one
    # used to take down the whole lookup rather than cost only itself.
    records = records_for_outing(read_log(log_path), outing)
    scans = merged_scans(
        [record for record in records if record.is_scan and record.time is not None]
    )
    if not scans:
        raise NotebookError(f"{log_path}: no timestamped scans found")
    return [list(scan.networks) for scan in scans[-count:]]


# --- Is the map any good? ----------------------------------------------------


@dataclass(frozen=True)
class HeldOutScan:
    """One fingerprint located from a map with its own outing, or its own walk, taken out."""

    truth: Place
    found: Location | None
    error_m: float | None
    error_fraction: float | None
    outing: str = ""
    across_mark: bool = False
    when: float | None = None  # seconds, for how fast the answers moved
    group: str = ""  # what was held out with it, so a step is only ever inside one run

    @property
    def abstained(self) -> bool:
        return self.found is None

    @property
    def wrong_stretch(self) -> bool:
        """True when the answer named a different street: a stretch with no mark in common.

        The stretch next door is not that. A scan taken at a mark is on two
        stretches at once, and an answer a few metres past that mark is a
        small error measured through it, `across_mark`, not a different
        street.
        """
        return (
            self.found is not None
            and self.found.place.key != self.truth.key
            and not self.across_mark
        )

    @property
    def own_outing_only(self) -> bool:
        """True when nothing but this scan's own outing backed the answer.

        A map built from one walk will find that walk again and prove nothing.
        This is what says so.
        """
        return self.found is not None and set(self.found.outings) <= {self.outing}


def _shared_mark(truth: Place, found: Place) -> tuple[float, float] | None:
    """Which end of each stretch is the mark the two share, as fractions, or None."""
    for truth_end, name in enumerate(truth.key):
        for found_end, other in enumerate(found.key):
            if name == other:
                return float(truth_end), float(found_end)
    return None


def _measure(one: Fingerprint, found: Location | None, group: str = "") -> HeldOutScan:
    measured = _measured(one, found)
    when = None if one.time is None else one.time.timestamp()
    return replace(measured, when=when, group=group)


def _measured(one: Fingerprint, found: Location | None) -> HeldOutScan:
    truth = one.place
    if found is None:
        return HeldOutScan(truth, None, None, None, one.outing)
    here, there = truth.coordinates, found.place.coordinates
    straight = (
        distance_metres(here[0], here[1], there[0], there[1])
        if here is not None and there is not None
        else None
    )
    if found.place.key == truth.key:
        gap = abs(found.place.fraction - truth.fraction)
        metres = truth.metres(gap) if straight is None else straight
        return HeldOutScan(truth, found, metres, gap, one.outing)
    shared = _shared_mark(truth, found.place)
    if shared is None:
        return HeldOutScan(truth, found, None, None, one.outing)
    # Through the mark: how far the scan was from it along its own stretch,
    # plus how far the answer is from it along the next one.
    to_mark = abs(truth.fraction - shared[0])
    past_mark = abs(found.place.fraction - shared[1])
    metres = straight
    if metres is None:
        here_m, there_m = truth.metres(to_mark), found.place.metres(past_mark)
        metres = None if here_m is None or there_m is None else here_m + there_m
    return HeldOutScan(truth, found, metres, to_mark + past_mark, one.outing, True)


@dataclass(frozen=True)
class MapSummary:
    """How much of a map there is, and how much of it came from different walks.

    Counted apart from the report that prints it, because a map of one outing
    has nothing to hold out and `format_map_check` answers that with a sentence
    saying so, never reaching the line with the numbers on it. Somebody looking
    at a new map wants those numbers most of all.
    """

    fingerprints: int
    walks: int
    stretches: int
    outings: tuple[str, ...]

    def describe(self) -> str:
        return (
            f"Map: {self.fingerprints} fingerprints, {self.walks} walks over "
            f"{self.stretches} stretches, {len(self.outings)} outings."
        )


def map_summary(fingerprints: Sequence[Fingerprint]) -> MapSummary:
    """What a map holds, counted. A walk is one outing down one stretch."""
    return MapSummary(
        len(fingerprints),
        len({one.walk for one in fingerprints}),
        len({one.place.key for one in fingerprints}),
        tuple(sorted({one.outing for one in fingerprints if one.outing})),
    )


def _by_outing(fingerprints: Sequence[Fingerprint]) -> bool:
    """Whether the map holds enough outings to hold one out whole."""
    return len({one.outing for one in fingerprints}) > 1


def check_map(
    fingerprints: Sequence[Fingerprint],
    by_signal: bool = False,
    sequence: str | None = None,
    by_rarity: bool = True,
    keep_pace: bool = False,
) -> list[HeldOutScan]:
    """Hold out one outing at a time, or one walk when there is only one, and locate its scans.

    The unit held out is a whole outing when the map holds more than one, and
    the question is then the one anybody asks of a map: does it know this
    street from another day. Holding out a walk, one outing down one stretch,
    left the same outing's next stretch in, and its first scan was taken five
    seconds after the held-out walk's last one, a few metres on: the copy of
    the question that holding out a walk rather than a single scan was meant to
    keep out, arriving at the end of every stretch instead.

    A map of a single outing still holds out a walk, since that is all there
    is: an outing that doubled back is tested on the pass it did not train on,
    over the same street, and the report says that such an answer is the map
    recognising a walk rather than a place. Holding out one scan at a time
    would be worthless either way, since consecutive scans are five seconds and
    a few metres apart and see almost exactly the same networks.

    `sequence`, `"tie"` or `"path"`, places each held-out scan with the scans
    before it in the same group, the way `--locate LOG --sequence` does, so
    that what the walk adds to a scan alone is measured rather than assumed.
    The run is the group in file order, which is the outing's order over the
    scans it placed: two in a row can be several cycles apart where scans fell
    outside the notebook, and the path prices that as one step.

    `keep_pace` runs the group the way `--locate --watch` runs a walk: ties
    settled by the scans before, and each answer kept to a walking pace by
    `Pace`, on the clock of the scans themselves.
    """
    by_outing = _by_outing(fingerprints)
    groups: dict[str, list[Fingerprint]] = {}
    for one in fingerprints:
        groups.setdefault(one.outing if by_outing else one.walk, []).append(one)
    if len(groups) < 2:
        return []
    results = []
    for key, held in groups.items():
        rest = [one for one in fingerprints if (one.outing if by_outing else one.walk) != key]
        pace = Pace.of(rest) if keep_pace else None
        for index, one in enumerate(held):
            if sequence is None and pace is None:
                found = locate_scan(rest, one.networks, by_signal, by_rarity=by_rarity)
            else:
                run = [earlier.networks for earlier in held[max(0, index - LOOK_BACK) : index + 1]]
                how = sequence or "tie"
                found = locate_sequence(rest, run, how, by_signal, by_rarity=by_rarity)
            if pace is not None:
                # A scan without a time is taken to be one cycle after the last.
                found = pace.keep(found, one.time.timestamp() if one.time else index * 5.0)
            results.append(_measure(one, found, key))
    return results


# How far past a walking pace two answers in a row may move before the report
# counts it as a jump: a few metres, for what the clock's second rounds away.
JUMP_SLACK_M = 5.0


def jumps(results: Sequence[HeldOutScan]) -> tuple[int, int]:
    """How many steps between two answers in a row moved faster than anybody walks, of how many.

    A step is two answers in a row of one held-out run, both on the stretch the
    scans were taken on and on the same one, with a time on each: the move
    along that stretch against `MAX_WALKING_SPEED_MS` for the seconds between.
    """
    faster = steps = 0
    for before, after in pairwise(results):
        if (
            before.group != after.group
            or before.when is None
            or after.when is None
            or before.found is None
            or after.found is None
            or before.found.place.key != after.found.place.key
            or after.found.place.key != after.truth.key
            or before.truth.key != before.found.place.key
        ):
            continue
        there, here = after.found.place, before.found.place
        moved = there.metres(abs(there.fraction - here.fraction))
        if moved is None:
            continue
        steps += 1
        faster += moved > MAX_WALKING_SPEED_MS * (after.when - before.when) + JUMP_SLACK_M
    return faster, steps


def _middle(values: Sequence[float]) -> tuple[float, float]:
    """Mean and median of what was measured."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return sum(ordered) / len(ordered), median


def _column(results: Sequence[HeldOutScan]) -> tuple[list[str], int, int]:
    """One way of matching as a column of numbers, and what the distances cover.

    The fractions and the distances are separate rows rather than one falling
    back to the other. A map that mixes notebooks with coordinates and notebooks
    without has a length for some of its stretches and not for others, and
    averaging only the ones it can measure into a single "mean error" silently
    reports a subset, dropping exactly the answers it could not measure, which
    are not the small ones.
    """
    fractions = [r.error_fraction for r in results if r.error_fraction is not None]
    metres = [r.error_m for r in results if r.error_m is not None]
    rows = [
        str(len(results)),
        str(sum(1 for r in results if r.error_fraction is not None and not r.across_mark)),
        str(sum(1 for r in results if r.across_mark)),
        str(sum(1 for r in results if r.wrong_stretch)),
        str(sum(1 for r in results if r.abstained)),
    ]
    rows += ["-", "-"] if not fractions else [f"{one:.0%}" for one in _middle(fractions)]
    rows += ["-", "-"] if not metres else [f"{one:.0f} m" for one in _middle(metres)]
    faster, steps = jumps(results)
    rows.append(f"{faster} of {steps}" if steps else "-")
    return rows, len(fractions), len(metres)


def format_map_check(
    fingerprints: Sequence[Fingerprint],
    by_networks: Sequence[HeldOutScan],
    by_signal: Sequence[HeldOutScan],
    settling_ties: Sequence[HeldOutScan],
    choosing_path: Sequence[HeldOutScan],
    keeping_pace: Sequence[HeldOutScan],
) -> str:
    """Human-readable verdict on how well the map locates a walk it has not seen."""
    if not by_networks:
        return (
            "Nothing to check: a map needs two walks before one of them can be held out.\n"
            "Add another outing, or walk a block and turn round at the corner, which "
            "covers one stretch twice and is the honest test."
        )
    labels = [
        "scans held out",
        "placed on the right stretch",
        "placed across a mark",
        "landed on the wrong stretch",
        "not on the map",
        "mean error, of a stretch",
        "median error, of a stretch",
        "mean error in metres",
        "median error in metres",
        "moved faster than a walk",
    ]
    left, placed, measured = _column(by_networks)
    right, _, _ = _column(by_signal)
    ties, _, _ = _column(settling_ties)
    path, _, _ = _column(choosing_path)
    pace, _, _ = _column(keeping_pace)
    counted = map_summary(fingerprints)
    held = (
        "Each outing held out in turn, and its scans located from the other outings:"
        if _by_outing(fingerprints)
        else "Each walk held out in turn, and its scans located from the rest of the map:"
    )
    lines = [
        counted.describe(),
        "",
        held,
        "",
        (
            f"  {'':<28}{'by networks':>13}{'and by signal':>15}"
            f"{'settling ties':>15}{'choosing the path':>18}{'at a walking pace':>19}"
        ),
    ]
    lines += [
        f"  {label:<28}{one:>13}{other:>15}{tie:>15}{run:>18}{kept:>19}"
        for label, one, other, tie, run, kept in zip(
            labels, left, right, ties, path, pace, strict=True
        )
    ]
    lines.append("")
    lines.append(
        "  A scan that landed on the wrong stretch is not a small error, it is a "
        "different street,\n  so it is counted apart rather than averaged into the distances. "
        "One placed across a mark\n  is on the stretch next door, a few metres past the mark the "
        "two share, and its error is\n  measured through that mark. Settling ties places each scan "
        "with the scans before it\n  breaking a tie between two stretches. Choosing the path takes "
        "the likeliest path through\n  them, which can overrule the scan itself. At a walking "
        "pace settles ties and keeps\n  each answer's place along the stretch to a walk's "
        "speed, as --locate --watch does.\n  Moved faster than a walk counts the steps between "
        "two answers in a row on the right\n  stretch that nobody walks in the seconds between "
        "them."
    )
    names = [name for one in fingerprints for name in one.place.stretch]
    confusable = format_confusable(confusable_crossings(names))
    if confusable:
        lines.append("")
        lines.append("\n".join("  " + line for line in confusable.splitlines()))
    if 0 < measured < placed:
        lines.append("")
        lines.append(
            f"  The distances cover the {measured} of {placed} answers whose stretch has a "
            "known length.\n  The rest are in the fractions above: their crossings carry no "
            "coordinates, so how long\n  those blocks are is written down nowhere."
        )
    answered = [held for held in by_networks if not held.abstained]
    alone = sum(1 for held in answered if held.own_outing_only)
    if alone:
        lines.append("")
        lines.append(
            f"  {alone} of {len(answered)} answers were backed only by the outing the scan "
            "came from,\n  which flatters this result: that is the map recognising a walk, "
            "not a place.\n  Walk these streets again on another day and run this again."
        )
    return "\n".join(lines)
