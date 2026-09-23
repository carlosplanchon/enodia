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
from math import cos, log10, radians

from enodia.reconcile import Reconciliation
from enodia.streets import Line, Place, StreetMap, distance_metres

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

    @classmethod
    def around(cls, places: Sequence[Place], width: float = WIDTH, margin: float = MARGIN) -> Frame:
        """A frame with everything in it, a tenth of the walk's own size as air."""
        lats = [lat for lat, _ in places]
        lons = [lon for _, lon in places]
        pad_lat = max((max(lats) - min(lats)) * 0.1, 0.0004)
        pad_lon = max((max(lons) - min(lons)) * 0.1, 0.0004)
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
        out += [
            f'<path d="{_path(frame, shape)} Z" fill="{BLOCK}" stroke="{BLOCK_EDGE}" '
            'stroke-width="0.8"/>'
            for shape in drawn.buildings
            if _within(frame, shape)
        ]
        out += [
            f'<path d="{_path(frame, street.line)}" fill="none" stroke="{STREET}" '
            f'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="{_path(frame, street.line)}" fill="none" stroke="{STREET_EDGE}" '
            'stroke-width="0.8"/>'
            for street in drawn.streets
            if _within(frame, street.line)
        ]

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
            span = distance_metres(frame.south, frame.west, frame.south, frame.east)
            radius = max((estimate.spread_m / span) * (frame.width - 2 * frame.margin), 6.0)
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
    out.append("</svg>")
    return "\n".join(out)
