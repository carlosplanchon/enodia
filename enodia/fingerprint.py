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
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from math import exp
from pathlib import Path
from typing import Any

import ifpeek

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
    describe_fraction,
    distance_metres,
    folded,
    format_confusable,
    merged_scans,
    network_turnover,
    reconcile,
)
from enodia.streets import StreetMap

# --- A place, in a frame that does not depend on which walk saw it -----------


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


def similarity(
    query: Mapping[str, float | None],
    fingerprint: Fingerprint,
    by_signal: bool = False,
) -> float:
    """How alike a scan and a fingerprint are: 1 identical, 0 nothing in common.

    The first term is which access points are in view, as a Jaccard similarity
    over their BSSIDs. It is the robust half: it survives a router dropping out
    of one scan, and it means the same thing on any radio.

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
    alike = 1.0 - network_turnover(keys, here)
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

    @property
    def uncertain(self) -> bool:
        """True when a second stretch of street matched nearly as well."""
        return self.alternative is not None

    @property
    def scattered(self) -> bool:
        """True when the fingerprints behind this were too far apart to be one place."""
        return self.scattered_m is not None

    def describe(self) -> str:
        return self.place.describe()


def _spread_of(group: Sequence[tuple[float, Fingerprint]]) -> float | None:
    """How far apart the placed fingerprints of a group sit, or None without coordinates."""
    places = [where for _, one in group if (where := one.place.coordinates) is not None]
    if len(places) < 2:
        return None
    return max(distance_metres(*here, *there) for here in places for there in places)


def _gather(group: Sequence[tuple[float, Fingerprint]]) -> tuple[Place, float]:
    """The weighted middle of a set of matches, and how spread out they were."""
    total = sum(score for score, _ in group)
    fraction = sum(score * one.place.fraction for score, one in group) / total
    spread = sum(score * abs(one.place.fraction - fraction) for score, one in group) / total
    placed = [(score, one.place.coordinates) for score, one in group]
    known = [(score, where) for score, where in placed if where is not None]
    lat = lon = None
    if known:
        weight = sum(score for score, _ in known)
        lat = sum(score * where[0] for score, where in known) / weight
        lon = sum(score * where[1] for score, where in known) / weight
    first = group[0][1].place
    length = next((one.place.length_m for _, one in group if one.place.length_m is not None), None)
    return Place(first.name_from, first.name_to, fraction, lat, lon, length), spread


def locate_scan(
    fingerprints: Sequence[Fingerprint],
    networks: Iterable[SeenNetwork],
    by_signal: bool = False,
    neighbours: int = NEIGHBOURS,
    floor: float = SIMILARITY_FLOOR,
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
    query = {n.key: (n.strength if n.has_signal else None) for n in networks if n.identified}
    if not query:
        return None
    # The floor is applied here, before anything is put to a vote, and not to
    # the best one alone. A stretch wins on the sum of its fingerprints, so
    # four readings that were each too weak to be evidence used to outvote one
    # that cleared the floor, and the answer came back naming the street with
    # the best of the losers: "you are here, best similarity 11%", under a
    # floor of 15%. Nothing that is not evidence on its own becomes evidence by
    # turning up four times.
    scored = [
        (score, one)
        for score, one in ((similarity(query, one, by_signal), one) for one in fingerprints)
        if score > 0.0 and score >= floor
    ]
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)

    best = scored[:neighbours]
    groups: dict[tuple[str, str], list[tuple[float, Fingerprint]]] = {}
    for score, one in best:
        groups.setdefault(one.place.key, []).append((score, one))
    ranked = sorted(groups.values(), key=lambda group: sum(s for s, _ in group), reverse=True)
    winner = ranked[0]
    dominance = sum(s for s, _ in winner) / sum(s for s, _ in best)

    place, spread = _gather(winner)
    apart = _spread_of(winner)
    far = place.metres(ONE_PLACE_BLOCKS) or ONE_PLACE_M
    if apart is not None and apart > far:
        # Two places in the map answer to the same pair of crossing names, and
        # the weighted middle of them is a point in between that is neither.
        # A stretch's identity is its names, and names are only unique if
        # somebody kept them so, which the map cannot check when it is fed
        # outing by outing. The coordinates are withheld rather than averaged:
        # an invented place on a map is worse than no place.
        return Location(
            Place(place.name_from, place.name_to, place.fraction, None, None, place.length_m),
            max(score for score, _ in winner),
            len(winner),
            spread,
            None,
            max((one.time for _, one in winner if one.time is not None), default=None),
            tuple(sorted({one.outing for _, one in winner if one.outing})),
            apart,
        )
    alternative = _gather(ranked[1])[0] if len(ranked) > 1 and dominance < DOMINANCE else None
    when = max((one.time for _, one in winner if one.time is not None), default=None)
    outings = tuple(sorted({one.outing for _, one in winner if one.outing}))
    # The best of the street that won, not the best of all of them. One
    # fingerprint of a street that lost can score higher than any of the
    # winner's while the winner still carries more evidence, and printing that
    # number beside the winner's name says "this place matched 100%" about a
    # place that matched 67%.
    best = max(score for score, _ in winner)
    return Location(place, best, len(winner), spread, alternative, when, outings)


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
    lines.append(
        f"  {location.matches} fingerprints agree, best similarity {location.score:.0%}, "
        f"spread {location.spread:.0%} of the stretch"
        + ("" if spread is None else f" ({spread:.0f} m)")
    )
    if location.when is not None:
        lines.append(f"  from evidence last gathered {location.when.strftime('%Y-%m-%d %H:%M')}")
    if location.alternative is not None:
        lines.append(f"  Uncertain: it could as easily be {location.alternative.describe()}")
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


def scan_from_log(log_path: str | Path, outing: str | None = None) -> list[SeenNetwork]:
    """The networks of the last cycle of one walk, to locate without a radio.

    The last cycle and not the last record. With two interfaces the last record
    is whichever card was asked second, and half of what was in view.

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
    return list(scans[-1].networks)


# --- Is the map any good? ----------------------------------------------------


@dataclass(frozen=True)
class HeldOutScan:
    """One fingerprint located from a map with its own walk taken out."""

    truth: Place
    found: Location | None
    error_m: float | None
    error_fraction: float | None
    outing: str = ""

    @property
    def abstained(self) -> bool:
        return self.found is None

    @property
    def wrong_stretch(self) -> bool:
        return self.found is not None and self.found.place.key != self.truth.key

    @property
    def own_outing_only(self) -> bool:
        """True when nothing but this scan's own outing backed the answer.

        A map built from one walk will find that walk again and prove nothing.
        This is what says so.
        """
        return self.found is not None and set(self.found.outings) <= {self.outing}


def _measure(one: Fingerprint, found: Location | None) -> HeldOutScan:
    truth = one.place
    if found is None or found.place.key != truth.key:
        return HeldOutScan(truth, found, None, None, one.outing)
    gap = abs(found.place.fraction - truth.fraction)
    here, there = truth.coordinates, found.place.coordinates
    metres = (
        distance_metres(here[0], here[1], there[0], there[1])
        if here is not None and there is not None
        else truth.metres(gap)
    )
    return HeldOutScan(truth, found, metres, gap, one.outing)


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


def check_map(
    fingerprints: Sequence[Fingerprint],
    by_signal: bool = False,
) -> list[HeldOutScan]:
    """Hold out one walk at a time and locate its scans from the rest of the map.

    The unit held out is a whole walk down one stretch, never a single scan, and
    that choice is the whole difference between a measurement and a flattering
    number. Consecutive scans are five seconds and a few metres apart and see
    almost exactly the same networks, so leaving one out leaves its own
    neighbour in, and the map scores itself on a copy of the question.

    Held out by the walk, a map built from one outing that doubled back is
    tested on the pass it did not train on, over the same street: the thing
    worth knowing, from data an ordinary outing already produced.
    """
    walks: dict[str, list[Fingerprint]] = {}
    for one in fingerprints:
        walks.setdefault(one.walk, []).append(one)
    if len(walks) < 2:
        return []
    results = []
    for walk, held in walks.items():
        rest = [one for one in fingerprints if one.walk != walk]
        for one in held:
            results.append(_measure(one, locate_scan(rest, one.networks, by_signal)))
    return results


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
        str(len(fractions)),
        str(sum(1 for r in results if r.wrong_stretch)),
        str(sum(1 for r in results if r.abstained)),
    ]
    rows += ["-", "-"] if not fractions else [f"{one:.0%}" for one in _middle(fractions)]
    rows += ["-", "-"] if not metres else [f"{one:.0f} m" for one in _middle(metres)]
    return rows, len(fractions), len(metres)


def format_map_check(
    fingerprints: Sequence[Fingerprint],
    by_networks: Sequence[HeldOutScan],
    by_signal: Sequence[HeldOutScan],
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
        "landed on the wrong stretch",
        "not on the map",
        "mean error, of a stretch",
        "median error, of a stretch",
        "mean error in metres",
        "median error in metres",
    ]
    left, placed, measured = _column(by_networks)
    right, _, _ = _column(by_signal)
    counted = map_summary(fingerprints)
    lines = [
        counted.describe(),
        "",
        "Each walk held out in turn, and its scans located from the rest of the map:",
        "",
        f"  {'':<28}{'by networks':>13}{'and by signal':>15}",
    ]
    lines += [
        f"  {label:<28}{one:>13}{other:>15}"
        for label, one, other in zip(labels, left, right, strict=True)
    ]
    lines.append("")
    lines.append(
        "  A scan that landed on the wrong stretch is not a small error, it is a "
        "different street,\n  so it is counted apart rather than averaged into the distances."
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
