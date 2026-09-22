"""Putting the notebook on the map: street crossings looked up on OpenStreetMap.

Coordinates on the crossings are what turn a fraction of a block into a place:
the access point centroid in metres, the GeoJSON, `--check-pace`, the distances
in `--check-map`. Looking four corners up by hand is nothing. Looking thirty up,
for a neighbourhood walked in a grid, is where the notebook stops being worth
keeping.

This is the one part of Enodia that goes online, and only when asked for by
name. The walk does not, the log does not, the reconciliation does not. It asks
Overpass once for every street the notebook mentions and works the crossings out
locally, so the network is one request and everything interesting is a pure
function over the answer.

It refuses far more than it resolves, on purpose. A crossing placed on the wrong
street moves every scan of that block, and the reconciliation then comes out
confidently wrong, which is worse than coming out short. So a name that is not
two streets, a street the area does not have, two streets that cross in two
places, an answer the server admits is incomplete, and a corner the walk says
could not have been reached on foot are all reported and left alone. What gets
written is what survived every one of those.

When a proxy is asked for, nothing here resolves the Overpass host on this
machine: the proxy does it. And nothing falls back to a direct connection if the
proxy cannot be used, since a fallback would quietly undo the one thing the
person who typed `--proxy` was asking for.
"""

from __future__ import annotations

import http.client
import importlib
import json
import re
import socket
import ssl
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from math import cos, radians
from pathlib import Path
from typing import Any

from enodia import __version__
from enodia.netlog import read_log, records_for_outing
from enodia.reconcile import (
    CORNER_SPLIT,
    DATE_DIRECTIVE,
    MARKED_LINE,
    NOTEBOOK_LINE,
    UntimedNotebook,
    button_marks,
    confusable_crossings,
    distance_metres,
    folded,
    read_notebook,
    strip_comment,
)
from enodia.streets import Street

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = f"enodia/{__version__}"
QUERY_TIMEOUT_S = 60
READ_TIMEOUT_S = 180
MAX_WALKING_SPEED_MS = 2.5
JUNCTION_CLUSTER_M = 60.0
NEARBY_JUNCTION_M = 30.0

# The names OSM may carry a street under. A notebook writes "Rivera" where the
# map says "Avenida General Rivera", and "Propios" where it says "Avenida Luis Alberto
# de Herrera" with `alt_name=Propios`. Asking for all of them and matching folded
# afterwards costs one longer query and saves most of the misses.
NAME_TAGS = ("name", "alt_name", "short_name", "official_name", "name:es", "loc_name")

# Ways that carry a street's name without being the street: a path across a
# plaza, a parking aisle, a bus platform. They share nodes with everything and
# would manufacture crossings that are not there. The pattern stays ASCII, which
# is the only kind of regex Overpass can be trusted with.
SKIP_HIGHWAYS = (
    "footway",
    "path",
    "steps",
    "cycleway",
    "construction",
    "proposed",
    "platform",
    "bus_guideway",
    "raceway",
)

# `_corner_halves` in reconcile.py folds before it splits, which lowercases the
# " Y " joining two streets. Overpass needs the name with its accents and capitals
# intact, so the split happens on the raw text and the case-insensitivity that
# folding was providing has to be put back by hand.
CORNER_SPLIT_RAW = re.compile(CORNER_SPLIT.pattern, re.IGNORECASE)
MARK_HEAD = re.compile(r"^\s*#\d+\s")
BBOX = re.compile(r"^\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?){3}\s*$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class GeocodeError(ValueError):
    """The lookup could not be completed, or could not be trusted.

    Deliberately not a `NotebookError`: a rate-limited server is not a malformed
    notebook, and the reconcile and map commands already catch `NotebookError`
    where a network failure has no business turning up.
    """


# --- Reading the notebook without destroying it ------------------------------


@dataclass(frozen=True)
class Crossing:
    """One line of the notebook that names a crossing, and where it sits in the file."""

    line: int
    name: str
    raw: str
    at: int | None
    lat: float | None = None
    lon: float | None = None

    @property
    def coordinates(self) -> tuple[float, float] | None:
        """Where the notebook already said this crossing is, if it said."""
        if self.lat is None or self.lon is None:
            return None
        return (self.lat, self.lon)

    @property
    def placed(self) -> bool:
        """True when the line already carries coordinates and is not ours to touch."""
        return self.at is None


def comment_at(raw: str) -> int:
    """Where a trailing comment starts in a raw line, or the end of the line.

    A leading `#7` names a mark and is not a comment, so the search starts after
    it. Getting this wrong puts the coordinates in front of the line, and the
    notebook comes back with a crossing called `@ -34.9, -56.2 #7 Plaza Fabini`.
    """
    head = MARK_HEAD.match(raw)
    found = raw.find("#", head.end() if head else 0)
    return len(raw) if found < 0 else found


def read_crossings(path: str | Path) -> list[Crossing]:
    """Every line of the notebook that names a crossing, with its original text.

    `read_notebook` cannot be used for this. Its `Waypoint`s carry no line number
    and no raw text, `strip_comment` has thrown the comments away and
    `splitlines()` has thrown the line endings away by the time it returns. So
    the file is walked again here, in the same branch order, keeping every byte:
    `keepends=True` both reproduces that partition exactly, down to the vertical
    tabs and form feeds `splitlines` breaks on, and keeps each line's own
    terminator to write back.
    """
    crossings = []
    # newline="" and not read_text: universal newlines would turn a notebook
    # written on Windows into one written here, and the whole point of walking
    # the file again is to hand every byte of it back untouched.
    with Path(path).open(encoding="utf-8", newline="") as source:
        text = source.read()
    for number, raw in enumerate(text.splitlines(keepends=True), 1):
        line = strip_comment(raw)
        if not line or DATE_DIRECTIVE.match(line):
            continue
        found = NOTEBOOK_LINE.match(line) or MARKED_LINE.match(line)
        if found is None:  # pragma: no cover - MARKED_LINE matches any non-empty line
            continue
        content = raw[: comment_at(raw)].rstrip()
        here = found.group("lat")
        crossings.append(
            Crossing(
                number,
                found.group("name").strip(),
                raw,
                None if here is not None else len(content),
                None if here is None else float(here),
                None if here is None else float(found.group("lon")),
            )
        )
    return crossings


def crossing_times(path: str | Path, marks: Sequence[tuple[int, datetime]] = ()) -> list[datetime]:
    """The time of each crossing, in file order, or nothing when there are none.

    Reuses `read_notebook` rather than repeating its midnight rollover, which is
    the kind of subtlety that drifts when it is written twice. A notebook whose
    times come from button marks has none of its own, and without the log it
    raises: that is not a broken notebook, it is a notebook with nothing to check
    the walk against, and the caller says so out loud rather than skipping the
    check in silence.
    """
    when = marks[0][1] if marks else None
    day = when.date() if when is not None else date(1970, 1, 1)
    try:
        found = read_notebook(path, day, when.tzinfo if when is not None else None, marks)
    except UntimedNotebook:
        # The notebook is fine and its times are not known. Every other way of
        # failing to read it is raised, because a notebook that says `25:99` is
        # not a notebook with no times in it, and going on to ask Overpass about
        # a file already known to be wrong is the opposite of what this does.
        return []
    return [point.time for point in found]


def notebook_marks(
    log_path: str | Path | None, outing: str | None = None
) -> list[tuple[int, datetime]]:
    """The button presses of one walk, to give an untimed notebook its times.

    One walk and not one file. Every run numbers its marks from one, so a file
    of two walks holds two mark 1s, and taken together the later walk's took the
    place of the earlier one's: an old notebook geocoded against it came back
    with times from a walk on another day. The last walk in the file by default,
    as everywhere else that reads a log.
    """
    if log_path is None:
        return []
    return button_marks(records_for_outing(read_log(log_path), outing))


def corner_streets(name: str) -> tuple[str, str] | None:
    """The two streets a corner is named after, as written, or None if it is not one."""
    halves = [half.strip() for half in CORNER_SPLIT_RAW.split(name)]
    if len(halves) != 2 or not all(halves) or folded(halves[0]) == folded(halves[1]):
        return None
    return (halves[0], halves[1])


# --- Asking Overpass, once ---------------------------------------------------


def escape(value: str) -> str:
    """A tag value as an Overpass string literal, refusing what cannot be escaped."""
    if CONTROL.search(value):
        raise GeocodeError(f"{value!r}: a crossing name cannot hold control characters")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def overpass_query(streets: Sequence[str], area: str, timeout: int = QUERY_TIMEOUT_S) -> str:
    """One query for every street the notebook mentions.

    Exact names and never a regular expression, and that is not a preference:
    Overpass matches with POSIX regexes, which do not do multibyte Unicode, so
    `Yaguarón` in a pattern is a coin toss. String equality handles UTF-8, and
    the tolerance for case and accents is applied here at home instead, by
    `ways_by_street`, with the project's own folding rule.
    """
    skip = '[highway!~"^(' + "|".join(SKIP_HIGHWAYS) + ')$"]'
    if BBOX.match(area):
        scope = f"[bbox:{','.join(part.strip() for part in area.split(','))}]"
        inside, within = "", ""
    else:
        scope = ""
        inside = f'area[name="{escape(area)}"]->.a;\n'
        within = "(area.a)"
    ways = "\n".join(
        f'  way{within}[highway]{skip}["{tag}"="{escape(name)}"];'
        for name in streets
        for tag in NAME_TAGS
    )
    return (
        f"[out:json][timeout:{timeout}]{scope};\n{inside}"
        f"(\n{ways}\n)->.w;\n"
        ".w out body;\nnode(w.w);\nout skel;\n"
    )


# --- The only part that touches the network ----------------------------------


@dataclass(frozen=True)
class Proxy:
    """A SOCKS5 proxy to send the one request through."""

    host: str
    port: int
    username: str | None = None
    password: str | None = None


def buildings_query(bbox: str, timeout: int = QUERY_TIMEOUT_S) -> str:
    """Every building in a box, drawn.

    `out geom` and not the node dance the streets need: nothing here has to know
    which building shares a corner with which, only what shape each one is, so
    the coordinates come back inline.
    """
    return f"[out:json][timeout:{timeout}][bbox:{bbox}];\nway[building];\nout geom;\n"


def around(places: Sequence[tuple[float, float]], margin_m: float = 150.0) -> str:
    """A bounding box around everything that was placed, with room to breathe."""
    lats = [lat for lat, _ in places]
    lons = [lon for _, lon in places]
    lat_margin = margin_m / 111_320
    lon_margin = lat_margin / max(cos(radians(sum(lats) / len(lats))), 0.01)
    return (
        f"{min(lats) - lat_margin:.6f},{min(lons) - lon_margin:.6f},"
        f"{max(lats) + lat_margin:.6f},{max(lons) + lon_margin:.6f}"
    )


def drawn_buildings(elements: Iterable[Any]) -> list[tuple[tuple[float, float], ...]]:
    """The building outlines in an answer that asked for geometry."""
    shapes = []
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "way":
            continue
        geometry = element.get("geometry")
        if not isinstance(geometry, list):
            continue
        outline = tuple(
            (float(point["lat"]), float(point["lon"]))
            for point in geometry
            if isinstance(point, dict) and "lat" in point and "lon" in point
        )
        if len(outline) >= 3:
            shapes.append(outline)
    return shapes


def parse_proxy(url: str) -> Proxy:
    """A `socks5://host:port` URL as a proxy, refusing anything else by name.

    `socks5h://` is curl's spelling for "the proxy resolves the name". Both are
    accepted and both do that, because resolving the Overpass host on this
    machine would announce what is about to be asked even though the request
    itself goes through Tor.
    """
    # The scheme is read out of the string rather than asked of `urlsplit`,
    # because what that calls the scheme of a bare `host:port` changed in 3.11.
    # 3.10 answers "127.0.0.1" and every version after it answers "", so the
    # same typed proxy was told "not 127.0.0.1", as though that were a scheme
    # somebody had asked for, on one Python and "not a bare host" on another.
    # A message that depends on which interpreter is installed is worse than
    # either of the two messages.
    named, marker, _ = url.partition("://")
    if (named if marker else "") not in ("socks5", "socks5h"):
        raise GeocodeError(
            f"{url}: only socks5:// and socks5h:// proxies are supported, "
            f"not {named if marker and named else 'a bare host'}"
        )
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:  # a port that is not a number at all
        raise GeocodeError(f"{url}: {exc}") from exc
    if not parts.hostname or not port:
        raise GeocodeError(f"{url}: a proxy needs both a host and a port, as socks5://host:port")
    return Proxy(parts.hostname, port, parts.username, parts.password)


def open_socket(
    host: str,
    port: int,
    proxy: Proxy | None = None,
    timeout: float | None = READ_TIMEOUT_S,
    connect: Callable[..., socket.socket] = socket.create_connection,
    import_socks: Callable[[str], Any] = importlib.import_module,
) -> socket.socket:
    """A connected socket to `host`, through `proxy` when there is one.

    The one function in Enodia that opens a connection to the outside, and the
    one the test suite stands in. Its two dependencies are parameters so that
    every line of it, the SOCKS branch included, can be exercised without a
    network.

    PySocks is imported here and not at the top: it is an optional extra, the
    type checker must not need it, and the walk must not care that it is
    missing. If the proxy cannot be used this raises. It never returns a direct
    connection instead, which would hand someone who asked for Tor a request
    that went out under their own address.
    """
    if proxy is None:
        return connect((host, port), timeout=timeout)
    try:
        socks = import_socks("socks")
    except ImportError as exc:
        raise GeocodeError(
            "a SOCKS proxy needs PySocks, which is not installed. "
            "Install it with: uv sync --extra socks"
        ) from exc
    through = socks.socksocket()
    through.settimeout(timeout)
    through.set_proxy(
        socks.SOCKS5,
        proxy.host,
        proxy.port,
        rdns=True,  # the proxy resolves the name; this machine never sees it
        username=proxy.username,
        password=proxy.password,
    )
    try:
        through.connect((host, port))
    except OSError as exc:  # socks.ProxyError is an OSError, so this covers both
        raise GeocodeError(
            f"the proxy at {proxy.host}:{proxy.port} could not be used: {exc}"
        ) from exc
    return through


class OverpassConnection(http.client.HTTPConnection):
    """An HTTPS connection to Overpass over a socket this module opened itself.

    `http.client` and not `urllib`, and the reason is the proxy. A default
    `urllib.request.ProxyHandler` reads `getproxies()`, so `ALL_PROXY` and its
    relatives in the environment would silently redirect a request in a tool
    whose whole point here is that `--proxy` is the only thing that can. Passing
    an explicit dictionary would disable that, but only for as long as everybody
    remembers to. `http.client` never consults the environment at all, so the
    guarantee is structural rather than a habit. It also brings chunked transfer
    decoding, which Overpass uses on large answers.
    """

    default_port = 443

    def __init__(
        self,
        host: str,
        port: int = 443,
        *,
        proxy: Proxy | None = None,
        timeout: float = READ_TIMEOUT_S,
        context: ssl.SSLContext | None = None,
    ) -> None:
        super().__init__(host, port, timeout=timeout)
        self.proxy = proxy
        self.context = context if context is not None else ssl.create_default_context()

    def connect(self) -> None:
        # `open_socket` is looked up here as a module global rather than captured
        # in a default argument, or standing it in for a test would not reach it
        # and the suite's guard would silently stop guarding.
        plain = open_socket(self.host, self.port, self.proxy, self.timeout)
        self.sock = self.context.wrap_socket(plain, server_hostname=self.host)


def post_overpass(
    query: str,
    url: str = OVERPASS_URL,
    proxy: Proxy | None = None,
    timeout: float = READ_TIMEOUT_S,
    connection: Callable[..., http.client.HTTPConnection] = OverpassConnection,
) -> dict[str, Any]:
    """Send one query and return the answer, refusing anything that is not a whole one.

    The dangerous case is not a failure, it is a success that is not one: when a
    query runs out of time or memory, Overpass answers 200 with valid JSON, a
    truncated list of elements and a `remark` saying so. Crossings worked out
    from half the streets look exactly like crossings worked out from all of
    them, and they would be written into the notebook as fact.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise GeocodeError(f"{url}: the Overpass endpoint has to be an https URL")
    link = connection(parts.hostname, parts.port or 443, proxy=proxy, timeout=timeout)
    try:
        link.request(
            "POST",
            parts.path or "/",
            body=urllib.parse.urlencode({"data": query}).encode(),
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        answer = link.getresponse()
        raw, status, location = answer.read(), answer.status, answer.getheader("Location")
    except OSError as exc:
        raise GeocodeError(f"{url}: {exc}") from exc
    finally:
        link.close()

    if 300 <= status < 400:
        raise GeocodeError(f"{url}: redirected to {location}, which is not what was asked for")
    if status != 200:
        raise GeocodeError(f"{url}: the server answered {status}, {raw[:200]!r}")
    try:
        found = json.loads(raw)
    except ValueError as exc:
        raise GeocodeError(f"{url}: the answer was not JSON, {raw[:200]!r}") from exc
    if not isinstance(found, dict):
        raise GeocodeError(f"{url}: the answer was not an Overpass result")
    remark = found.get("remark")
    if remark:
        raise GeocodeError(
            f"{url}: the answer is incomplete and was not used ({remark}). "
            "Try a smaller area, or an endpoint under less load."
        )
    return found


# --- Working the crossings out, offline --------------------------------------


@dataclass(frozen=True)
class Junction:
    """Where two named streets meet, and how sure of it the data left us."""

    lat: float
    lon: float
    nodes: int
    spread_m: float
    touching: bool = True


def ways_by_street(elements: Iterable[Any], wanted: Iterable[str]) -> dict[str, list[list[int]]]:
    """Which of the streets asked for each way belongs to, by any name OSM gave it.

    Matched folded, so `Avenida 18 de Julio` answers for `18 de Julio` only when
    the notebook wrote the whole thing. The query asked exactly, this forgives
    case and accents, and a street that still matched nothing is reported by the
    name it was written with rather than quietly dropped.
    """
    keys = {folded(name) for name in wanted}
    streets: dict[str, list[list[int]]] = {}
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        nodes = [node for node in element.get("nodes") or [] if isinstance(node, int)]
        for tag in NAME_TAGS:
            value = tags.get(tag)
            if isinstance(value, str) and folded(value) in keys:
                streets.setdefault(folded(value), []).append(nodes)
                break
    return streets


def drawn_streets(elements: Iterable[Any], wanted: Iterable[str]) -> list[Street]:
    """Every way of every street asked for, with its nodes turned into a drawn line.

    The one request already carries this: the ways came back with their node
    lists and the nodes with their coordinates, and until now all of it was
    thrown away the moment the crossings had been worked out. Kept, it is the
    shape of the blocks, which is what `--streets` writes down so a
    reconciliation can put a scan on the street instead of on the chord.
    """
    places = node_places(elements)
    keys = {folded(name): name for name in wanted}
    drawn = []
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        for tag in NAME_TAGS:
            value = tags.get(tag)
            if isinstance(value, str) and folded(value) in keys:
                line = tuple(places[node] for node in element.get("nodes") or [] if node in places)
                if len(line) >= 2:
                    drawn.append(Street(keys[folded(value)], line))
                break
    return drawn


def node_places(elements: Iterable[Any]) -> dict[int, tuple[float, float]]:
    """Where each node in the answer is."""
    places = {}
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "node":
            continue
        node, lat, lon = element.get("id"), element.get("lat"), element.get("lon")
        if isinstance(node, int) and isinstance(lat, int | float) and isinstance(lon, int | float):
            places[node] = (float(lat), float(lon))
    return places


def clusters(
    places: Sequence[tuple[float, float]], within_m: float
) -> list[list[tuple[float, float]]]:
    """Group places that are near enough to be one junction split into several nodes.

    Connected components, and the difference matters. Dropping each place into
    the first group it touches and stopping there depends on the order they
    arrive in: three nodes in a line fifty metres apart, at a sixty metre
    threshold, come out as one group given A, B, C and as two given A, C, B,
    because B joins A's group and nothing ever goes back to merge in C's. One
    junction would be reported as two, and refused, on nothing but the order
    OpenStreetMap happened to list its nodes in.
    """
    groups: list[list[tuple[float, float]]] = []
    for place in places:
        near = [
            group
            for group in groups
            if any(distance_metres(*place, *other) <= within_m for other in group)
        ]
        merged = [place]
        for group in near:
            merged += group
            groups.remove(group)
        groups.append(merged)
    return groups


def _middle(group: Sequence[tuple[float, float]], touching: bool) -> Junction:
    lat = sum(place[0] for place in group) / len(group)
    lon = sum(place[1] for place in group) / len(group)
    spread = max(distance_metres(lat, lon, *place) for place in group)
    return Junction(lat, lon, len(group), spread, touching)


def junction_of(
    first: Sequence[Sequence[int]],
    second: Sequence[Sequence[int]],
    places: Mapping[int, tuple[float, float]],
    cluster_m: float = JUNCTION_CLUSTER_M,
    nearby_m: float = NEARBY_JUNCTION_M,
) -> tuple[Junction | None, str]:
    """Where two streets meet, and what to say when they do not meet just once.

    A junction is almost never one node. An avenue mapped as two carriageways
    crosses a street twice, twenty metres apart, and a corner with turning lanes
    is several nodes within a few metres, so the shared nodes are grouped and one
    group is one crossing. Two groups a block apart are two crossings and are
    refused with both named, because picking between them is guessing about the
    thing the whole notebook exists to pin down.
    """
    shared = {node for way in first for node in way} & {node for way in second for node in way}
    spots = [places[node] for node in sorted(shared) if node in places]
    if spots:
        groups = clusters(spots, cluster_m)
        if len(groups) == 1:
            return _middle(groups[0], touching=True), ""
        middles = [_middle(group, touching=True) for group in groups]
        apart = max(
            distance_metres(one.lat, one.lon, other.lat, other.lon)
            for one in middles
            for other in middles
        )
        return None, f"those streets cross in {len(groups)} places, up to {apart:.0f} m apart"

    here = [places[node] for way in first for node in way if node in places]
    there = [places[node] for way in second for node in way if node in places]
    if not here or not there:
        return None, "one of those streets is not in the area that was searched"
    near = min(
        ((one, other) for one in here for other in there),
        key=lambda pair: distance_metres(*pair[0], *pair[1]),
    )
    gap = distance_metres(*near[0], *near[1])
    if gap <= nearby_m:
        middle = ((near[0][0] + near[1][0]) / 2, (near[0][1] + near[1][1]) / 2)
        return Junction(middle[0], middle[1], 2, gap / 2, touching=False), ""
    return None, f"those streets never meet, the closest they come is {gap:.0f} m"


# --- What the walk says about it ---------------------------------------------


def implausible_stretches(
    crossings: Sequence[Crossing],
    placed: Mapping[int, tuple[float, float]],
    times: Sequence[datetime],
    max_speed_ms: float = MAX_WALKING_SPEED_MS,
) -> list[tuple[Crossing, Crossing, float]]:
    """Consecutive crossings the coordinates say were walked faster than anyone walks.

    An upper bound and never a lower one. A slow stretch is somebody standing
    still, which is ordinary and is the reason `place_by_movement` exists at all,
    but nobody covers eight hundred metres in ninety seconds, so a pair that says
    they did is a crossing put in the wrong place.

    `placed` is keyed by line number and holds whatever coordinates that line
    will end up with, the ones already written in the notebook included.
    """
    if len(times) != len(crossings):
        raise GeocodeError(
            f"the notebook read as {len(crossings)} crossings one way and "
            f"{len(times)} the other, which should not happen"
        )
    found = []
    for index in range(len(crossings) - 1):
        one, other = crossings[index], crossings[index + 1]
        here, there = placed.get(one.line), placed.get(other.line)
        seconds = (times[index + 1] - times[index]).total_seconds()
        if here is None or there is None or seconds <= 0:
            continue
        speed = distance_metres(*here, *there) / seconds
        if speed > max_speed_ms:
            found.append((one, other, speed))
    return found


# --- The whole thing ---------------------------------------------------------


@dataclass(frozen=True)
class Skipped:
    """A crossing that got no coordinates, and why, in words."""

    line: int
    name: str
    reason: str


@dataclass
class Geocoding:
    """Everything one lookup established, before a single byte is written."""

    crossings: list[Crossing] = field(default_factory=list)
    found: dict[int, Junction] = field(default_factory=dict)
    skipped: list[Skipped] = field(default_factory=list)
    confusable: list[tuple[str, ...]] = field(default_factory=list)
    streets: int = 0
    drawn: list[Street] = field(default_factory=list)
    buildings: list[tuple[tuple[float, float], ...]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    checked: int = 0
    unchecked: int = 0
    walk_known: bool = True

    @property
    def approximate(self) -> int:
        return sum(1 for one in self.found.values() if not one.touching)


def geocode_notebook(
    path: str | Path,
    area: str,
    proxy: Proxy | None = None,
    url: str = OVERPASS_URL,
    marks: Sequence[tuple[int, datetime]] = (),
    buildings: bool = False,
    fetch: Callable[..., dict[str, Any]] | None = None,
    max_speed_ms: float = MAX_WALKING_SPEED_MS,
) -> Geocoding:
    """Look every crossing in a notebook up, and work out which answers to trust.

    `max_speed_ms` is the pace above which a stretch is taken for a wrong
    lookup rather than a fast walk: a brisk walk is under two metres a second,
    a bicycle is not, and an outing ridden rather than walked raises it.

    `fetch` defaults to None and resolves to `post_overpass` here rather than in
    the signature, because a default argument is bound when the function is
    defined: standing the network in by name would not reach it, and a test that
    believed it had would go out to the real Overpass.
    """
    ask = fetch if fetch is not None else post_overpass
    crossings = read_crossings(path)
    if not crossings:
        raise GeocodeError(f"{path}: no crossings found")
    # Read before anything is asked of Overpass. A notebook with a line that
    # cannot be read is not going to become readable after a network request,
    # and the request is the one thing here that leaves this machine.
    times = crossing_times(path, marks)
    result = Geocoding(crossings=crossings)
    result.confusable = confusable_crossings(one.name for one in crossings)

    corners: dict[int, tuple[str, str]] = {}
    for one in crossings:
        if one.placed:
            continue
        pair = corner_streets(one.name)
        if pair is None:
            result.skipped.append(
                Skipped(one.line, one.name, "not a corner of two streets, so there is none to find")
            )
            continue
        corners[one.line] = pair

    # One street however many ways the notebook spells it: asking Overpass for
    # both "Rivera" and "rivera" costs a clause and finds the same ways.
    wanted: dict[str, str] = {}
    for pair in corners.values():
        for name in pair:
            wanted.setdefault(folded(name), name)
    result.streets = len(wanted)
    if wanted:
        answer = ask(overpass_query(list(wanted.values()), area), url=url, proxy=proxy)
        elements = answer.get("elements") or []
        streets = ways_by_street(elements, wanted.values())
        places = node_places(elements)
        result.drawn = drawn_streets(elements, wanted.values())
        result.missing = [name for key, name in wanted.items() if key not in streets]
        # One lookup per corner however often the notebook names it, and keyed
        # folded, so the same corner spelled two ways gets one answer.
        answers: dict[tuple[str, ...], tuple[Junction | None, str]] = {}
        for line, (first, second) in corners.items():
            key = tuple(sorted((folded(first), folded(second))))
            if key not in answers:
                answers[key] = junction_of(
                    streets.get(folded(first), []), streets.get(folded(second), []), places
                )
            junction, why = answers[key]
            if junction is None:
                name = next(one.name for one in crossings if one.line == line)
                result.skipped.append(Skipped(line, name, why))
            else:
                result.found[line] = junction

    _check_the_walk(result, times, max_speed_ms)
    if buildings and result.found:
        # A second request, and only when asked for. The box cannot be known
        # until the crossings are, so this does not fold into the first one.
        answer = ask(
            buildings_query(around([(one.lat, one.lon) for one in result.found.values()])),
            url=url,
            proxy=proxy,
        )
        result.buildings = drawn_buildings(answer.get("elements") or [])
    return result


def _check_the_walk(
    result: Geocoding, times: Sequence[datetime], max_speed_ms: float = MAX_WALKING_SPEED_MS
) -> None:
    """Drop whatever the notebook's own times say could not have been walked."""
    result.walk_known = bool(times)
    if not times:
        return
    placed: dict[int, tuple[float, float]] = {
        one.line: where for one in result.crossings if (where := one.coordinates) is not None
    }
    placed.update((line, (junction.lat, junction.lon)) for line, junction in result.found.items())
    wrong = implausible_stretches(result.crossings, placed, times, max_speed_ms)
    both = sum(
        1
        for index in range(len(result.crossings) - 1)
        if result.crossings[index].line in placed and result.crossings[index + 1].line in placed
    )
    result.checked, result.unchecked = both, len(result.crossings) - 1 - both
    for one, other, speed in wrong:
        for crossing in (one, other):
            if crossing.line in result.found:
                del result.found[crossing.line]
        result.skipped.append(
            Skipped(
                one.line,
                f"{one.name} to {other.name}",
                f"that would have meant walking at {speed:.1f} m/s, so one of the two is wrong "
                "and neither is written",
            )
        )


# --- Writing the second notebook ---------------------------------------------


def geocoded_path(path: str | Path) -> Path:
    """Where the notebook with coordinates goes: beside the one it came from."""
    source = Path(path)
    return source.with_name(source.stem + ".geo" + source.suffix)


def write_geocoded(source: str | Path, target: str | Path, result: Geocoding) -> int:
    """Write the second notebook and return how many lines gained coordinates.

    Every line of the original comes through byte for byte, its own terminator
    included, and only the ones that resolved are touched: the coordinates go in
    ahead of any trailing comment, where the next read will find them.

    Refuses a target that already exists. The realistic second run is the one
    after you have filled four stubborn corners in by hand, and overwriting that
    without asking is the kind of loss nobody gets back.
    """
    out = Path(target)
    if out.exists():
        raise GeocodeError(
            f"{out} is already there and was left alone. Delete it, or name another with --out."
        )
    changed = {
        one.line: f"{one.raw[: one.at]} @ {found.lat:.6f}, {found.lon:.6f}{one.raw[one.at :]}"
        for one in result.crossings
        if one.at is not None and (found := result.found.get(one.line)) is not None
    }
    with Path(source).open(encoding="utf-8", newline="") as original:
        text = original.read()
    with out.open("w", encoding="utf-8", newline="") as written:
        written.write(
            "".join(
                changed.get(number, raw)
                for number, raw in enumerate(text.splitlines(keepends=True), 1)
            )
        )
    return len(changed)


def format_geocoding(result: Geocoding, target: Path, written: int) -> str:
    """What the lookup found, what it refused, and why."""
    lines = [
        f"{len(result.crossings)} crossings, {result.streets} streets asked for in one request.",
        f"{written} lines gained coordinates, written to {target}.",
    ]
    if result.approximate:
        lines.append(
            f"{result.approximate} of them are where the two streets came closest rather than "
            "where they touch:\nOpenStreetMap has no node in common there, so read those as near "
            "the corner, not on it."
        )
    if result.missing:
        lines.append("")
        lines.append("Streets the area does not have under that name:")
        lines += [f"  {name}" for name in result.missing]
        lines.append("Check the spelling against the map, or write the name the map uses.")
    if result.skipped:
        lines.append("")
        lines.append("Left without coordinates, to be filled in by hand:")
        lines += [f"  line {one.line}, {one.name}: {one.reason}" for one in result.skipped]
    lines.append("")
    if not result.walk_known:
        lines.append(
            "The walk could not check any of this: this notebook carries no times of its own.\n"
            "Pass --marks with the outing's log and the crossings will be checked against the\n"
            "pace they were actually walked at."
        )
    else:
        many = max(result.checked, 0)
        lines.append(
            f"{many} stretch{'' if many == 1 else 'es'} checked against the pace they were "
            "walked at, and\nwhatever would have needed running is in the list above."
        )
    if result.confusable:
        lines.append("")
        lines.append(
            "One corner is written both ways round here, so it will be looked up twice and\n"
            "placed twice. Settle on one spelling before you reconcile:"
        )
        lines += ["  " + " and ".join(f'"{name}"' for name in pair) for pair in result.confusable]
    return "\n".join(lines)
