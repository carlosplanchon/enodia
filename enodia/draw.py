"""The walk as a plan: the streets, the blocks, the route and what was heard.

An SVG written by hand, from the OpenStreetMap geometry `--streets` already
keeps. No tiles, so nothing is fetched when the picture is opened and nothing
leans on a service run on donations for somebody else's benefit. The lines are
the real ones OpenStreetMap draws, rendered here instead of there, which is also
what lets the same data be measured rather than only looked at.

The hard part of a picture like this is not drawing it, it is not lying with it.
A point on a plan of real streets reads as a fact, and most of what a walk hears
was never established: it was audible from somewhere along a block. So an access
point the walk pinned down is a dot, one it did not is a ring the size of how far
its sightings were spread, with nothing in the middle, and the two never look
alike. The legend says which is which.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from math import atan2, cos, degrees, log10, radians

from enodia.fingerprint import Band, Fingerprint, Location
from enodia.reconcile import Reconciliation
from enodia.streets import (
    SURROUNDINGS_M,
    Area,
    Line,
    Place,
    Street,
    StreetMap,
    distance_metres,
    joined,
)

WIDTH = 1200.0
MARGIN = 48.0
PAPER = "#faf8f5"
# The ground between the streets, and not the paper around the picture: tinted,
# so that the streets drawn over it in white stand out, which on the paper they
# hardly did. Buildings, where there are any, darker again.
LAND = "#ebe6dd"
BLOCK = "#d8d0c3"
BLOCK_EDGE = "#c6bcae"
STREET = "#ffffff"
STREET_EDGE = "#ded7cc"
ROUTE = "#1a6b8a"
CROSSING = "#22303a"
NETWORK = "#b8442a"
OPEN_NETWORK = "#1f7a4d"
LOOSE = "#9a9186"
WATER = "#aad3df"
PARK = "#cdebb0"
ROAD = "#fdfcfa"
LABEL = "#6b645b"
HERE = "#1a6b8a"
UNSURE = "#c98a12"
LOST = "#9a9186"
# The map's own fingerprints on the live map, in a colour no answer is drawn in:
# grey was already that of an answer that lost you.
FINGERPRINT = "#a08cd6"
# How much of the map shows through the panel of what the run is doing: the
# alpha of a colour written in hex, after the colour.
TRANSLUCENT = "d9"
# The band of how far off an answer may be: twice as wide as a walked street, so
# that the street shows through it, and faint enough that the fingerprints on it
# do too.
BAND_WIDTH = 14.0
BAND_OPACITY = 0.25
# One name of a street per this many metres of it, at most. A grid of blocks is
# a hundred metres a side, and a name on every one of them is a pattern, not a
# label.
LABEL_EVERY_M = 250.0
LABEL_SIZE = 9.0

# The live map at night: a dark counterpart for every colour it draws with, so
# that nothing on the page is left light. The drawing carries the light ones as
# its own attributes, which any viewer shows, and a page asked for the dark
# theme lays these over them by class, since a rule of CSS outranks an
# attribute of the SVG it styles.
DARK_PAPER = "#16191d"
DARK_BLOCK = "#23272d"
DARK_BLOCK_EDGE = "#30353c"
DARK_ROAD = "#2e343b"
DARK_ROAD_EDGE = "#3d444d"
DARK_STREET = "#3b424b"
DARK_STREET_EDGE = "#4b535d"
DARK_WATER = "#1d3d4f"
DARK_PARK = "#1e3527"
DARK_LABEL = "#9aa4ae"
DARK_FINGERPRINT = "#a597cf"
DARK_HERE = "#4aa3df"
DARK_UNSURE = "#e0a33a"
DARK_LOST = "#7d8590"
DARK_CROSSING = "#d6dce3"
# How sure the last scan was of where it puts you: the class the dot, its ring
# and the line of words above the map carry, and their colour in the light and
# in the dark.
STATES = {
    "here": (HERE, DARK_HERE),
    "unsure": (UNSURE, DARK_UNSURE),
    "lost": (LOST, DARK_LOST),
}
# The dark theme, part by part: the page around the map, then each class of the
# drawing. One selector a rule, so that each can be put under the root that
# asks for the theme.
DARK_RULES = (
    ("body", f"background: {DARK_PAPER}; color: {DARK_CROSSING};"),
    (
        ".zoom button",
        f"background: {DARK_PAPER}; border-color: {DARK_BLOCK_EDGE}; color: {DARK_CROSSING};",
    ),
    (".zoom button.on", f"background: {DARK_HERE}; color: {DARK_PAPER};"),
    (".credit", f"color: {DARK_LABEL};"),
    (
        ".telemetry",
        (
            f"background: {DARK_PAPER}{TRANSLUCENT}; border-color: {DARK_BLOCK_EDGE}; "
            f"color: {DARK_CROSSING};"
        ),
    ),
    ("#beat.late", f"background: {DARK_UNSURE}; color: {DARK_PAPER};"),
    ("svg", f"background: {DARK_PAPER};"),
    (".paper", f"fill: {DARK_PAPER};"),
    (".water", f"fill: {DARK_WATER};"),
    (".river", f"stroke: {DARK_WATER};"),
    (".park", f"fill: {DARK_PARK};"),
    (".building", f"fill: {DARK_BLOCK}; stroke: {DARK_BLOCK_EDGE};"),
    (".road-edge", f"stroke: {DARK_ROAD_EDGE};"),
    (".road", f"stroke: {DARK_ROAD};"),
    (".street", f"stroke: {DARK_STREET};"),
    (".street-edge", f"stroke: {DARK_STREET_EDGE};"),
    (".halo", f"stroke: {DARK_PAPER};"),
    (".label", f"fill: {DARK_LABEL};"),
    ("line.scale", f"stroke: {DARK_CROSSING};"),
    ("text.scale", f"fill: {DARK_CROSSING};"),
    (".fingerprint", f"fill: {DARK_FINGERPRINT};"),
    (".trail", f"stroke: {DARK_HERE};"),
    (".you", f"stroke: {DARK_PAPER};"),
    (".alternative", f"stroke: {DARK_UNSURE};"),
    *(
        rule
        for state, (_, dark) in STATES.items()
        for rule in (
            (f".you.{state}", f"fill: {dark};"),
            (f".ring.{state}", f"fill: {dark}; stroke: {dark};"),
            (f".band.{state}", f"stroke: {dark};"),
            (f"p.{state}", f"border-left-color: {dark};"),
        )
    ),
)


@dataclass(frozen=True)
class Frame:
    """The window from latitude and longitude onto the picture.

    Equirectangular, with the longitude squeezed by the cosine of the latitude
    so the streets meet at the angles they meet at on the ground. The same flat
    earth `distance_metres` assumes, and just as fine over a walk.
    """

    south: float
    west: float
    north: float
    east: float
    width: float
    height: float
    margin: float

    @property
    def squeeze(self) -> float:
        return max(cos(radians((self.north + self.south) / 2)), 0.01)

    def x(self, lon: float) -> float:
        span = (self.east - self.west) * self.squeeze
        across = 0.5 if span <= 0 else ((lon - self.west) * self.squeeze) / span
        return self.margin + across * (self.width - 2 * self.margin)

    def y(self, lat: float) -> float:
        span = self.north - self.south
        down = 0.5 if span <= 0 else (self.north - lat) / span
        return self.margin + down * (self.height - 2 * self.margin)

    def holds(self, place: Place) -> bool:
        return self.south <= place[0] <= self.north and self.west <= place[1] <= self.east

    def overlaps(self, shape: Sequence[Place]) -> bool:
        """Whether a shape's box crosses the frame's.

        Not whether a corner of it is inside: a river that fills the picture
        can have every one of its vertices beyond the edges.
        """
        lats = [lat for lat, _ in shape]
        lons = [lon for _, lon in shape]
        return (
            min(lats) <= self.north
            and max(lats) >= self.south
            and min(lons) <= self.east
            and max(lons) >= self.west
        )

    def pixels(self, metres: float) -> float:
        """How long a distance on the ground is on the picture."""
        span = distance_metres(self.south, self.west, self.south, self.east)
        return 0.0 if span <= 0 else (metres / span) * (self.width - 2 * self.margin)

    @classmethod
    def around(cls, places: Sequence[Place], width: float = WIDTH, margin: float = MARGIN) -> Frame:
        """A frame with everything in it, and the neighbourhood around it as air.

        `SURROUNDINGS_M` on every side, or a tenth of the walk's own size when
        that is more: enough to show the river or the park a few blocks off
        that says where the walk was.
        """
        lats = [lat for lat, _ in places]
        lons = [lon for _, lon in places]
        around_lat = SURROUNDINGS_M / 111_320
        around_lon = around_lat / max(cos(radians(sum(lats) / len(lats))), 0.01)
        pad_lat = max((max(lats) - min(lats)) * 0.1, around_lat)
        pad_lon = max((max(lons) - min(lons)) * 0.1, around_lon)
        south, north = min(lats) - pad_lat, max(lats) + pad_lat
        west, east = min(lons) - pad_lon, max(lons) + pad_lon
        squeeze = max(cos(radians((north + south) / 2)), 0.01)
        across = (east - west) * squeeze
        tall = (north - south) / across if across > 0 else 1.0
        height = min(max((width - 2 * margin) * tall + 2 * margin, 300.0), width * 2)
        return cls(south, west, north, east, width, height, margin)


def _path(frame: Frame, line: Line) -> str:
    return " ".join(
        f"{'M' if index == 0 else 'L'}{frame.x(lon):.1f},{frame.y(lat):.1f}"
        for index, (lat, lon) in enumerate(line)
    )


def _rings(frame: Frame, area: Area) -> str:
    return " ".join(f"{_path(frame, ring)} Z" for ring in area)


def _within(frame: Frame, line: Line) -> bool:
    """Whether any of a shape falls in the frame, so the rest can be left out.

    A street runs the length of the city and the walk was four blocks. Drawing
    all of it would be megabytes of path outside the picture.
    """
    return any(frame.holds(place) for place in line)


def _escape(text: str) -> str:
    """Text as SVG can carry it: markup escaped, and control characters, which XML
    refuses outright and an SSID may well hold, replaced by a space."""
    plain = "".join(ch if ch >= " " or ch in "\t\n" else " " for ch in text)
    return plain.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _scale_bar(frame: Frame) -> list[str]:
    """A bar of a round number of metres, so the picture can be measured."""
    across = distance_metres(frame.south, frame.west, frame.south, frame.east)
    if across <= 0:
        return []
    rough = across / 5
    step = 10 ** int(log10(rough))
    metres = max(int(rough / step) * step, 10)
    length = (metres / across) * (frame.width - 2 * frame.margin)
    bottom = frame.height - frame.margin / 2
    return [
        (
            f'<line class="scale" x1="{frame.margin:.1f}" y1="{bottom:.1f}" '
            f'x2="{frame.margin + length:.1f}" y2="{bottom:.1f}" '
            f'stroke="{CROSSING}" stroke-width="2"/>'
        ),
        (
            f'<text class="scale" x="{frame.margin:.1f}" y="{bottom - 6:.1f}" font-size="12" '
            f'fill="{CROSSING}">{metres:.0f} m</text>'
        ),
    ]


def _legend(frame: Frame, loose: int) -> list[str]:
    at = frame.width - frame.margin
    rows = [
        (NETWORK, "network, pinned down by the walk"),
        (OPEN_NETWORK, "open network"),
    ]
    if loose:
        rows.append((LOOSE, f"{loose} only heard from around there"))
    out = []
    for index, (colour, label) in enumerate(rows):
        y = frame.margin / 2 + index * 16
        out.append(f'<circle cx="{at - 150:.1f}" cy="{y:.1f}" r="4" fill="{colour}"/>')
        out.append(
            f'<text x="{at - 140:.1f}" y="{y + 4:.1f}" font-size="11" fill="{CROSSING}">'
            f"{_escape(label)}</text>"
        )
    return out


def _labels(frame: Frame, streets: Sequence[Street]) -> list[str]:
    """The names of the streets, along them, and never upside down.

    Each at the middle of a segment long enough to hold it, turned to the
    segment's angle and then by half a turn if that left it reading from right
    to left, and set just beside the street rather than on it: on a walked
    street the line down its middle is the walk's own evidence, and a name
    written over it hides both. Rotated rather than set on a `textPath`, which is the older and
    plainer half of SVG and shows in every viewer. At most one name of a street
    every `LABEL_EVERY_M` metres of it.
    """
    by_name: dict[str, list[Line]] = {}
    for street in streets:
        by_name.setdefault(street.name, []).append(street.line)
    out = []
    for name, lines in by_name.items():
        wide = len(name) * LABEL_SIZE * 0.55 + 8
        for line in joined(lines):
            since = LABEL_EVERY_M
            for here, there in pairwise(line):
                step = distance_metres(*here, *there)
                x1, y1 = frame.x(here[1]), frame.y(here[0])
                x2, y2 = frame.x(there[1]), frame.y(there[0])
                middle = ((here[0] + there[0]) / 2, (here[1] + there[1]) / 2)
                room = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                if since + step / 2 >= LABEL_EVERY_M and room >= wide and frame.holds(middle):
                    angle = degrees(atan2(y2 - y1, x2 - x1))
                    if angle > 90:
                        angle -= 180
                    elif angle <= -90:
                        angle += 180
                    x, y = (x1 + x2) / 2, (y1 + y2) / 2
                    where = (
                        f'x="{x:.1f}" y="{y:.1f}" dy="-4.5" font-size="{LABEL_SIZE:g}" '
                        f'text-anchor="middle" transform="rotate({angle:.1f} {x:.1f} {y:.1f})"'
                    )
                    # Twice: once as a halo the colour of the ground, then the
                    # name on it, so it reads over whatever it crosses. Not
                    # `paint-order`, which a viewer that does not know it
                    # would draw as the halo over the name.
                    out.append(
                        f'<text class="halo" {where} fill="none" stroke="{LAND}" stroke-width="3" '
                        f'stroke-linejoin="round">{_escape(name)}</text>'
                    )
                    out.append(f'<text class="label" {where} fill="{LABEL}">{_escape(name)}</text>')
                    since = -step / 2
                since += step
    return out


def _background(frame: Frame, drawn: StreetMap) -> list[str]:
    """The neighbourhood, from the bottom up: water, parks, blocks, streets, names."""
    out = [
        f'<path class="water" d="{_rings(frame, area)}" fill="{WATER}" fill-rule="evenodd"/>'
        for area in drawn.water
        if frame.overlaps([place for ring in area for place in ring])
    ]
    out += [
        f'<path class="river" d="{_path(frame, line)}" fill="none" stroke="{WATER}" '
        'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
        for line in drawn.rivers
        if frame.overlaps(line)
    ]
    out += [
        f'<path class="park" d="{_rings(frame, area)}" fill="{PARK}" fill-rule="evenodd"/>'
        for area in drawn.parks
        if frame.overlaps([place for ring in area for place in ring])
    ]
    out += [
        f'<path class="building" d="{_path(frame, shape)} Z" fill="{BLOCK}" stroke="{BLOCK_EDGE}" '
        'stroke-width="0.8"/>'
        for shape in drawn.buildings
        if _within(frame, shape)
    ]
    # Every edge first and every road over them, so that where two streets meet
    # the crossing is open and not a grey line across it.
    shown = [road for road in drawn.roads if frame.overlaps(road.line)]
    out += [
        f'<path class="road-edge" d="{_path(frame, road.line)}" fill="none" stroke="{STREET_EDGE}" '
        'stroke-width="6.6" stroke-linecap="round" stroke-linejoin="round"/>'
        for road in shown
    ]
    out += [
        f'<path class="road" d="{_path(frame, road.line)}" fill="none" stroke="{ROAD}" '
        'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
        for road in shown
    ]
    out += [
        f'<path class="street" d="{_path(frame, street.line)}" fill="none" stroke="{STREET}" '
        f'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path class="street-edge" d="{_path(frame, street.line)}" fill="none" '
        f'stroke="{STREET_EDGE}" stroke-width="0.8"/>'
        for street in drawn.streets
        if _within(frame, street.line)
    ]
    # Every named street in the box when the neighbourhood was asked for, the
    # walked ones among them, and only the walked ones when it was not.
    out += _labels(frame, drawn.roads or drawn.streets)
    return out


def _attribution(frame: Frame) -> str:
    """The credit the Open Database License asks of anything drawn from its data."""
    return (
        f'<text x="{frame.width - frame.margin:.1f}" y="{frame.height - frame.margin / 2:.1f}" '
        f'font-size="10" fill="{LABEL}" text-anchor="end">'
        "\u00a9 OpenStreetMap contributors</text>"
    )


def svg_map(
    result: Reconciliation,
    streets: StreetMap | None = None,
    width: float = WIDTH,
    names: bool = False,
) -> str | None:
    """The whole walk as one SVG, or None when there is nothing placed to draw.

    Needs coordinates. A reconciliation of a notebook of bare crossing names
    knows the order of the blocks and not where any of them is, which is a
    perfectly good answer and not a picture.

    `names` writes each pinned network's name beside its dot, in small type.
    Only the pinned ones: a ring is where a network was heard from, not where
    it is, and a name on it would say otherwise. A plan of a few hundred
    networks is dense and the names overlap, which the file being vector is
    for. A hidden network has no name to write.
    """
    drawn = streets if streets is not None else result.streets
    places = [
        place
        for source in (
            (point.coordinates for point in result.waypoints),
            (item.position.coordinates for item in result.placed),
        )
        for place in source
        if place is not None
    ]
    if len(places) < 2:
        return None
    frame = Frame.around(places, width)
    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {frame.width:.0f} '
            f'{frame.height:.0f}" width="{frame.width:.0f}" height="{frame.height:.0f}">'
        ),
        f'<rect width="100%" height="100%" fill="{LAND}"/>',
    ]

    if drawn is not None:
        out += _background(frame, drawn)

    walked = tuple(
        place for item in result.placed if (place := item.position.coordinates) is not None
    )
    if len(walked) >= 2:
        out.append(
            f'<path d="{_path(frame, walked)}" fill="none" stroke="{ROUTE}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round" opacity="0.85"/>'
        )

    loose = 0
    for item in result.networks:
        estimate = item.estimate
        if estimate is None:
            continue
        where = (estimate.lat, estimate.lon)
        if not frame.holds(where):
            continue
        x, y = frame.x(where[1]), frame.y(where[0])
        if estimate.barely_pinned:
            loose += 1
            radius = max(frame.pixels(estimate.spread_m), 6.0)
            out.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="none" '
                f'stroke="{LOOSE}" stroke-width="1.2" stroke-dasharray="3 3"/>'
            )
            continue
        colour = OPEN_NETWORK if item.network.open else NETWORK
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{colour}"/>')
        if names and item.network.ssid:
            out.append(
                f'<text x="{x + 5:.1f}" y="{y + 2.5:.1f}" font-size="6" fill="{colour}">'
                f"{_escape(item.network.ssid)}</text>"
            )

    for point in result.waypoints:
        place = point.coordinates
        if place is None or not frame.holds(place):
            continue
        x, y = frame.x(place[1]), frame.y(place[0])
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{PAPER}" stroke="{CROSSING}" '
            'stroke-width="2"/>'
        )
        out.append(
            f'<text x="{x + 8:.1f}" y="{y - 6:.1f}" font-size="12" fill="{CROSSING}">'
            f"{_escape(point.name)}</text>"
        )

    out += _scale_bar(frame)
    out += _legend(frame, loose)
    if drawn is not None and (drawn.streets or drawn.surroundings):
        out.append(_attribution(frame))
    out.append("</svg>")
    return "\n".join(out)


# The theme the address asks for, set on the page before any of it is drawn.
# In the head and apart from the rest, because the page is reloaded every few
# seconds, and a theme set only at the end of it could let each reload show the
# other one first.
THEME_SCRIPT = """
(function () {
  var theme = new URLSearchParams(location.hash.slice(1)).get("theme");
  if (theme === "dark" || theme === "light") { document.documentElement.classList.add(theme); }
})();
"""

# The live map's zoom and theme, in the page itself. Both are kept in the
# address, as `#view=x,y,width`, `&follow` while it follows you and
# `&theme=dark` or `&theme=light` once one is chosen, because the page is
# written again every cycle and reloaded, and the address is the one thing a
# reload keeps: `location.reload()` loads the same URL, fragment and all. Read
# with `URLSearchParams`, and written by hand because `URLSearchParams` would
# escape the commas. `%RELOAD%` is the interval in milliseconds.
ZOOM_SCRIPT = """
(function () {
  var svg = document.getElementById("map");
  var box = svg.viewBox.baseVal;
  var W = box.width, H = box.height, least = W / 30;
  var here = (svg.getAttribute("data-here") || "").split(",").map(Number);
  var saved = new URLSearchParams(location.hash.slice(1));
  var asked = (saved.get("view") || "").split(",").map(Number);
  var view = {x: 0, y: 0, w: W, follow: true};
  if (asked.length === 3 && asked.every(isFinite)) {
    view = {x: asked[0], y: asked[1], w: asked[2], follow: saved.has("follow")};
  }
  var root = document.documentElement, button = document.getElementById("theme");
  var system = window.matchMedia("(prefers-color-scheme: dark)");
  var theme = root.classList.contains("dark") ? "dark"
    : root.classList.contains("light") ? "light" : "";
  function dark() { return theme ? theme === "dark" : system.matches; }
  function tall() { return view.w * H / W; }
  function show() {
    view.w = Math.min(Math.max(view.w, least), W);
    if (view.follow && here.length === 2 && view.w < W) {
      view.x = here[0] - view.w / 2;
      view.y = here[1] - tall() / 2;
    }
    view.x = Math.min(Math.max(view.x, 0), W - view.w);
    view.y = Math.min(Math.max(view.y, 0), H - tall());
    svg.setAttribute("viewBox", [view.x, view.y, view.w, tall()].join(" "));
    var whole = view.w >= W, kept = [];
    if (!whole) {
      kept.push("view=" + [view.x.toFixed(1), view.y.toFixed(1), view.w.toFixed(1)].join(","));
      if (view.follow) { kept.push("follow"); }
    }
    if (theme) { kept.push("theme=" + theme); }
    history.replaceState(null, "", kept.length ? "#" + kept.join("&")
      : location.pathname + location.search);
    document.getElementById("follow").classList.toggle("on", view.follow && !whole);
    root.classList.toggle("dark", theme === "dark");
    root.classList.toggle("light", theme === "light");
    button.textContent = dark() ? "Light" : "Dark";
  }
  function point(event) {
    var at = svg.createSVGPoint();
    at.x = event.clientX; at.y = event.clientY;
    return at.matrixTransform(svg.getScreenCTM().inverse());
  }
  function zoom(by, around) {
    var at = around || {x: view.x + view.w / 2, y: view.y + tall() / 2};
    var wide = Math.min(Math.max(view.w / by, least), W), share = wide / view.w;
    view.x = at.x - (at.x - view.x) * share;
    view.y = at.y - (at.y - view.y) * share;
    view.w = wide;
    if (around) { view.follow = false; }
    show();
  }
  svg.addEventListener("wheel", function (event) {
    event.preventDefault();
    zoom(event.deltaY < 0 ? 1.25 : 0.8, point(event));
  }, {passive: false});
  svg.addEventListener("dblclick", function (event) { zoom(2, point(event)); });
  var held = null;
  svg.addEventListener("pointerdown", function (event) {
    held = point(event);
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", function (event) {
    if (!held) { return; }
    var now = point(event);
    view.x -= now.x - held.x;
    view.y -= now.y - held.y;
    view.follow = false;
    show();
  });
  svg.addEventListener("pointerup", function () { held = null; });
  document.getElementById("in").onclick = function () { zoom(1.5); };
  document.getElementById("out").onclick = function () { zoom(1 / 1.5); };
  document.getElementById("all").onclick = function () {
    view = {x: 0, y: 0, w: W, follow: true}; show();
  };
  document.getElementById("follow").onclick = function () {
    view.follow = true;
    if (view.w >= W) { view.w = W / 4; }
    show();
  };
  // Whichever the page shows now, the system's or one chosen before, the other.
  button.onclick = function () { theme = dark() ? "light" : "dark"; show(); };
  document.addEventListener("keydown", function (event) {
    var keys = {"+": "in", "=": "in", "-": "out", "0": "all", "f": "follow", "t": "theme"};
    if (keys[event.key]) { document.getElementById(keys[event.key]).click(); }
  });
  show();
  // Not while the map is being dragged: a reload then would drop it mid-move.
  (function again() {
    setTimeout(function () { if (held) { again(); } else { location.reload(); } }, %RELOAD%);
  })();
})();
"""


# How long a page may go without a new one before the live map says it has had
# no news: three reloads, and never under half a minute, because a fresh scan
# takes about five seconds a card, and two cards can make a cycle of ten to
# twenty whatever the interval.
LATE_S = 30

# The panel's first line: how long ago the page was written, counted in the
# browser. A page that has stopped changing looks just like one that is live,
# since it reloads itself whatever becomes of the run. `setInterval` is handed
# `tick` by name, and the reload's own `}, %RELOAD%);` is not imitated.
BEAT_SCRIPT = """
(function () {
  var beat = document.getElementById("beat");
  var written = Number(beat.getAttribute("data-written"));
  var late = Number(beat.getAttribute("data-late"));
  function tick() {
    var age = Math.max(0, Math.round((Date.now() - written) / 1000));
    var overdue = age >= late;
    beat.textContent = overdue ? "No news for " + age + " s: is Enodia still running?"
      : "Updated " + age + " s ago";
    beat.classList.toggle("late", overdue);
    beat.hidden = false;
  }
  tick();
  setInterval(tick, 1000);
})();
"""


def _panel(notes: Sequence[str], written: float | None, reload_s: int) -> str:
    """The panel of what the run is doing, or nothing when there is nothing to say.

    The heartbeat only when the page says when it was written, and hidden until
    the script fills it: without script, the time on the line above the map is
    the one the page has.
    """
    if not notes and written is None:
        return ""
    beat = ""
    if written is not None:
        late = max(3 * reload_s, LATE_S)
        beat = (
            f'<div id="beat" data-written="{round(written * 1000)}" data-late="{late}" '
            "hidden></div>"
        )
    lines = "".join(f"<div>{_escape(note)}</div>" for note in notes)
    return f'<div class="telemetry">{beat}{lines}</div>'


def _dark(root: str) -> str:
    """The dark theme's rules, each under `root`: the page that asks for it."""
    return " ".join(
        [f"{root} {{ color-scheme: dark; }}"]
        + [f"{root} {selector} {{ {rules} }}" for selector, rules in DARK_RULES]
    )


def mapped_places(known: Sequence[Fingerprint]) -> list[Place]:
    """Where the map's fingerprints were taken, the ones that say so."""
    return [
        (one.place.lat, one.place.lon)
        for one in known
        if one.place.lat is not None and one.place.lon is not None
    ]


def live_map(
    known: Sequence[Fingerprint],
    found: Location | None,
    trail: Sequence[Place],
    said: str,
    streets: StreetMap | None = None,
    interval: float = 5.0,
    width: float = WIDTH,
    *,
    notes: Sequence[str] = (),
    written: float | None = None,
    band: Band | None = None,
) -> str:
    """One moment of `--locate --watch`, as a page that reloads itself every interval.

    The map's own fingerprints are the violet dots, the streets the walk put on
    it, and where the last scan puts you is the large one, with the last few
    answers fading behind it and `band`, the streets it may be on, shaded
    around it. An answer the scan could not settle between two stretches is
    drawn in another colour, with a hollow dot on the other stretch it could be
    on, since a sure-looking dot is the one claim this picture must not make on
    the scan's behalf. One whose coordinates were withheld, two places in the
    map sharing its names, gets a ring the size of how far apart they lay.
    Lost, the last place it knew stays on the page, greyed, with the words
    saying so.

    A page and not a picture, for the few lines of script that reload it and
    zoom it: any browser follows the run with nothing to install and nothing
    fetched. The wheel zooms where the pointer is and a drag moves the view,
    and zoomed in, each reload centres on where you are until the map is
    dragged away from it. The page is light or dark as the system is, and a
    button turns it to the other. The dark is only CSS over the drawing's own
    colours, so without script the page still follows the system, and still
    reloads itself, whole.

    `notes` are the lines of a panel of what the run is doing, beside the map,
    and `written` is when the page was written, in seconds since the epoch.
    With it the panel counts how long ago that was, and says so in another
    colour once no new page has come for three reloads or `LATE_S`. Without it
    there is no such line: the page has no clock of its own, and only the run
    that writes it knows when that was.
    """
    places = mapped_places(known)
    frame = Frame.around(places or [(0.0, 0.0)], width)
    shown = [place for place in trail if frame.holds(place)]
    here = ""
    if shown:
        here = f' data-here="{frame.x(shown[-1][1]):.1f},{frame.y(shown[-1][0]):.1f}"'
    out = [
        (
            f'<svg id="map" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {frame.width:.0f} '
            f'{frame.height:.0f}"{here}>'
        ),
        f'<rect class="paper" width="100%" height="100%" fill="{LAND}"/>',
    ]
    if streets is not None:
        out += _background(frame, streets)
    state = "lost" if found is None else "unsure" if found.uncertain else "here"
    colour = STATES[state][0]
    if band is not None and band.pieces:
        # One path for all of it, so that where two pieces meet at a corner the
        # shade is not laid on twice.
        pieces = " ".join(_path(frame, piece) for piece in band.pieces)
        out.append(
            f'<path class="band {state}" d="{pieces}" fill="none" stroke="{colour}" '
            f'stroke-width="{BAND_WIDTH:g}" stroke-opacity="{BAND_OPACITY:g}" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    out += [
        f'<circle class="fingerprint" cx="{frame.x(lon):.1f}" cy="{frame.y(lat):.1f}" r="1.6" '
        f'fill="{FINGERPRINT}"/>'
        for lat, lon in places
    ]
    for index in range(len(shown) - 1):
        (lat1, lon1), (lat2, lon2) = shown[index], shown[index + 1]
        fade = (index + 1) / len(shown)
        out.append(
            f'<line class="trail" x1="{frame.x(lon1):.1f}" y1="{frame.y(lat1):.1f}" '
            f'x2="{frame.x(lon2):.1f}" y2="{frame.y(lat2):.1f}" stroke="{HERE}" stroke-width="3" '
            f'stroke-linecap="round" opacity="{fade:.2f}"/>'
        )
    elsewhere = None
    if found is not None and found.uncertain and found.alternative is not None:
        elsewhere = found.alternative.coordinates
    if elsewhere is not None and frame.holds(elsewhere):
        out.append(
            f'<circle class="alternative" cx="{frame.x(elsewhere[1]):.1f}" '
            f'cy="{frame.y(elsewhere[0]):.1f}" r="6" fill="none" stroke="{UNSURE}" '
            'stroke-width="2.5"/>'
        )
    if shown:
        lat, lon = shown[-1]
        x, y = frame.x(lon), frame.y(lat)
        if found is not None and found.scattered_m:
            radius = max(frame.pixels(found.scattered_m), 9.0)
            out.append(
                f'<circle class="ring {state}" cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" '
                f'fill="{colour}" fill-opacity="0.12" stroke="{colour}" stroke-width="1"/>'
            )
        out.append(
            f'<circle class="you {state}" cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colour}" '
            f'stroke="{PAPER}" stroke-width="2.5"/>'
        )
    out += _scale_bar(frame)
    out.append("</svg>")
    # The credit on the page and not in the picture, where zooming in would
    # carry it off the screen.
    credit = (
        '<div class="credit">\u00a9 OpenStreetMap contributors</div>'
        if streets is not None and (streets.streets or streets.surroundings)
        else ""
    )
    style = " ".join(
        [
            f"body {{ margin: 0; background: {PAPER}; color: {CROSSING};",
            "font-family: sans-serif; overflow: hidden;",
            "display: flex; flex-direction: column; height: 100vh; }",
            "p { margin: 0; padding: 10px 16px; font-size: 18px;",
            f"border-left: 6px solid {colour}; }}",
            # The map below the line on top, however many lines a narrow window
            # makes of it, and what floats over the map placed on the map: put
            # on the window at a guess of that line's height, the buttons
            # covered the end of it.
            ".stage { position: relative; flex: 1; min-height: 0; }",
            # The ground behind the whole map, since the picture's own covers
            # its first view and not one zoomed in elsewhere.
            f"svg {{ display: block; width: 100%; height: 100%; background: {LAND};",
            "touch-action: none; cursor: grab; }",
            ".zoom { position: absolute; top: 8px; right: 12px; display: flex; gap: 6px; }",
            f".zoom button {{ font-size: 16px; padding: 4px 10px; background: {PAPER};",
            f"border: 1px solid {BLOCK_EDGE}; border-radius: 4px; color: {CROSSING}; }}",
            f".zoom button.on {{ background: {HERE}; color: {PAPER}; }}",
            ".credit { position: absolute; bottom: 6px; right: 12px; font-size: 11px;",
            f"color: {LABEL}; }}",
            # Over the map and not in the way of it: the wheel and the drag go
            # through to the map beneath.
            ".telemetry { position: absolute; top: 8px; left: 12px; max-width: 56ch;",
            "padding: 6px 10px; font-size: 12px; line-height: 1.5; pointer-events: none;",
            f"background: {PAPER}{TRANSLUCENT}; border: 1px solid {BLOCK_EDGE};",
            f"border-radius: 4px; color: {CROSSING}; }}",
            # Amber behind the words and not in them, which on the paper would
            # hardly be read.
            f"#beat.late {{ background: {UNSURE}; color: {CROSSING}; font-weight: bold;",
            "margin: 0 -4px; padding: 0 4px; border-radius: 3px; }",
            # Too narrow for the panel and the buttons side by side.
            "@media (max-width: 700px) { .telemetry { top: 48px; } }",
            # The system's dark, unless the page was set light, and the page's
            # own whenever it was set dark.
            f"@media (prefers-color-scheme: dark) {{ {_dark('html:not(.light)')} }}",
            _dark("html.dark"),
        ]
    )
    reload_s = max(round(interval), 1)
    panel = _panel(notes, written, reload_s)
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html><head><meta charset="utf-8">',
            f'<noscript><meta http-equiv="refresh" content="{reload_s}"></noscript>',
            f"<title>Enodia: {_escape(said)}</title>",
            f"<style>{style}</style>",
            f"<script>{THEME_SCRIPT}</script></head><body>",
            f'<p class="{state}">{_escape(said)}</p>',
            '<div class="stage">',
            (
                '<div class="zoom"><button id="in" title="Zoom in (+)">+</button>'
                '<button id="out" title="Zoom out (-)">&#8722;</button>'
                '<button id="all" title="The whole map (0)">All</button>'
                '<button id="follow" title="Keep me in the middle (f)">Follow me</button>'
                '<button id="theme" title="Light or dark (t)">Dark</button></div>'
            ),
            *([panel] if panel else []),
            *out,
            credit,
            "</div>",
            f"<script>{ZOOM_SCRIPT.replace('%RELOAD%', str(reload_s * 1000))}</script>",
            *([f"<script>{BEAT_SCRIPT}</script>"] if written is not None else []),
            "</body></html>",
            "",
        ]
    )
