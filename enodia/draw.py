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

from enodia.fingerprint import Fingerprint, Location
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
BLOCK = "#e7e1d8"
BLOCK_EDGE = "#d6cec2"
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
# One name of a street per this many metres of it, at most. A grid of blocks is
# a hundred metres a side, and a name on every one of them is a pattern, not a
# label.
LABEL_EVERY_M = 250.0
LABEL_SIZE = 9.0


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
            f'<line x1="{frame.margin:.1f}" y1="{bottom:.1f}" '
            f'x2="{frame.margin + length:.1f}" y2="{bottom:.1f}" '
            f'stroke="{CROSSING}" stroke-width="2"/>'
        ),
        (
            f'<text x="{frame.margin:.1f}" y="{bottom - 6:.1f}" font-size="12" '
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
                    # Twice: once as a halo the colour of the paper, then the
                    # name on it, so it reads over whatever it crosses. Not
                    # `paint-order`, which a viewer that does not know it
                    # would draw as the halo over the name.
                    out.append(
                        f'<text {where} fill="none" stroke="{PAPER}" stroke-width="3" '
                        f'stroke-linejoin="round">{_escape(name)}</text>'
                    )
                    out.append(f'<text {where} fill="{LABEL}">{_escape(name)}</text>')
                    since = -step / 2
                since += step
    return out


def _background(frame: Frame, drawn: StreetMap) -> list[str]:
    """The neighbourhood, from the bottom up: water, parks, blocks, streets, names."""
    out = [
        f'<path d="{_rings(frame, area)}" fill="{WATER}" fill-rule="evenodd"/>'
        for area in drawn.water
        if frame.overlaps([place for ring in area for place in ring])
    ]
    out += [
        f'<path d="{_path(frame, line)}" fill="none" stroke="{WATER}" stroke-width="4" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        for line in drawn.rivers
        if frame.overlaps(line)
    ]
    out += [
        f'<path d="{_rings(frame, area)}" fill="{PARK}" fill-rule="evenodd"/>'
        for area in drawn.parks
        if frame.overlaps([place for ring in area for place in ring])
    ]
    out += [
        f'<path d="{_path(frame, shape)} Z" fill="{BLOCK}" stroke="{BLOCK_EDGE}" '
        'stroke-width="0.8"/>'
        for shape in drawn.buildings
        if _within(frame, shape)
    ]
    # Every edge first and every road over them, so that where two streets meet
    # the crossing is open and not a grey line across it.
    shown = [road for road in drawn.roads if frame.overlaps(road.line)]
    out += [
        f'<path d="{_path(frame, road.line)}" fill="none" stroke="{STREET_EDGE}" '
        'stroke-width="6.6" stroke-linecap="round" stroke-linejoin="round"/>'
        for road in shown
    ]
    out += [
        f'<path d="{_path(frame, road.line)}" fill="none" stroke="{ROAD}" stroke-width="5" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        for road in shown
    ]
    out += [
        f'<path d="{_path(frame, street.line)}" fill="none" stroke="{STREET}" '
        f'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="{_path(frame, street.line)}" fill="none" stroke="{STREET_EDGE}" '
        'stroke-width="0.8"/>'
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
        f'<rect width="100%" height="100%" fill="{PAPER}"/>',
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


# The live map's zoom, in the page itself. The view is kept in the address, as
# `#x,y,width` and `,f` while it follows you, because the page is written
# again every cycle and reloaded, and the address is the one thing a reload
# keeps: `location.reload()` loads the same URL, fragment and all. `%RELOAD%`
# is the interval in milliseconds.
ZOOM_SCRIPT = """
(function () {
  var svg = document.getElementById("map");
  var box = svg.viewBox.baseVal;
  var W = box.width, H = box.height, least = W / 30;
  var here = (svg.getAttribute("data-here") || "").split(",").map(Number);
  var view = {x: 0, y: 0, w: W, follow: true};
  var found = /^#(-?[0-9.]+),(-?[0-9.]+),([0-9.]+)(,f)?$/.exec(location.hash);
  if (found) {
    view = {x: +found[1], y: +found[2], w: +found[3], follow: !!found[4]};
  }
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
    var whole = view.w >= W;
    history.replaceState(null, "", whole ? location.pathname + location.search
      : "#" + [view.x.toFixed(1), view.y.toFixed(1), view.w.toFixed(1)].join(",")
        + (view.follow ? ",f" : ""));
    document.getElementById("follow").classList.toggle("on", view.follow && !whole);
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
  document.addEventListener("keydown", function (event) {
    var keys = {"+": "in", "=": "in", "-": "out", "0": "all", "f": "follow"};
    if (keys[event.key]) { document.getElementById(keys[event.key]).click(); }
  });
  show();
  // Not while the map is being dragged: a reload then would drop it mid-move.
  (function again() {
    setTimeout(function () { if (held) { again(); } else { location.reload(); } }, %RELOAD%);
  })();
})();
"""


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
) -> str:
    """One moment of `--locate --watch`, as a page that reloads itself every interval.

    The map's own fingerprints are the grey dots, the streets the walk put on
    it, and where the last scan puts you is the large one, with the last few
    answers fading behind it. An answer the scan could not settle between two
    stretches is drawn in another colour, and with the ring of how far apart
    its evidence lay when that is known, since a sure-looking dot is the one
    claim this picture must not make on the scan's behalf. Lost, the last
    place it knew stays on the page, greyed, with the words saying so.

    A page and not a picture, for the few lines of script that reload it and
    zoom it: any browser follows the run with nothing to install and nothing
    fetched. The wheel zooms where the pointer is and a drag moves the view,
    and zoomed in, each reload centres on where you are until the map is
    dragged away from it. Without script the page still reloads itself, whole.
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
        f'<rect width="100%" height="100%" fill="{PAPER}"/>',
    ]
    if streets is not None:
        out += _background(frame, streets)
    out += [
        f'<circle cx="{frame.x(lon):.1f}" cy="{frame.y(lat):.1f}" r="1.6" fill="{LOOSE}"/>'
        for lat, lon in places
    ]
    for index in range(len(shown) - 1):
        (lat1, lon1), (lat2, lon2) = shown[index], shown[index + 1]
        fade = (index + 1) / len(shown)
        out.append(
            f'<line x1="{frame.x(lon1):.1f}" y1="{frame.y(lat1):.1f}" x2="{frame.x(lon2):.1f}" '
            f'y2="{frame.y(lat2):.1f}" stroke="{HERE}" stroke-width="3" '
            f'stroke-linecap="round" opacity="{fade:.2f}"/>'
        )
    colour = LOST if found is None else UNSURE if found.uncertain else HERE
    if shown:
        lat, lon = shown[-1]
        x, y = frame.x(lon), frame.y(lat)
        if found is not None and found.scattered_m:
            radius = max(frame.pixels(found.scattered_m), 9.0)
            out.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{colour}" '
                f'fill-opacity="0.12" stroke="{colour}" stroke-width="1"/>'
            )
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colour}" stroke="{PAPER}" '
            'stroke-width="2.5"/>'
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
            "font-family: sans-serif; overflow: hidden; }",
            "p { margin: 0; padding: 10px 16px; font-size: 18px;",
            f"border-left: 6px solid {colour}; }}",
            "svg { display: block; width: 100%; height: calc(100vh - 44px);",
            "touch-action: none; cursor: grab; }",
            ".zoom { position: fixed; top: 52px; right: 12px; display: flex; gap: 6px; }",
            f".zoom button {{ font-size: 16px; padding: 4px 10px; background: {PAPER};",
            f"border: 1px solid {BLOCK_EDGE}; border-radius: 4px; color: {CROSSING}; }}",
            f".zoom button.on {{ background: {HERE}; color: {PAPER}; }}",
            ".credit { position: fixed; bottom: 6px; right: 12px; font-size: 11px;",
            f"color: {LABEL}; }}",
        ]
    )
    reload_s = max(round(interval), 1)
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html><head><meta charset="utf-8">',
            f'<noscript><meta http-equiv="refresh" content="{reload_s}"></noscript>',
            f"<title>Enodia: {_escape(said)}</title>",
            f"<style>{style}</style></head><body>",
            f"<p>{_escape(said)}</p>",
            (
                '<div class="zoom"><button id="in" title="Zoom in (+)">+</button>'
                '<button id="out" title="Zoom out (-)">&#8722;</button>'
                '<button id="all" title="The whole map (0)">All</button>'
                '<button id="follow" title="Keep me in the middle (f)">Follow me</button></div>'
            ),
            *out,
            credit,
            f"<script>{ZOOM_SCRIPT.replace('%RELOAD%', str(reload_s * 1000))}</script>",
            "</body></html>",
            "",
        ]
    )
