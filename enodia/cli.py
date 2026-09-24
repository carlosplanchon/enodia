"""Command line entry point: `enodia`."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path

from enodia import __version__
from enodia.anonymize import ExportError, export_outing, format_export, key_path, read_key
from enodia.button import ButtonMarker, find_button_devices, list_input_devices
from enodia.draw import live_map, mapped_places, svg_map
from enodia.fingerprint import (
    Calibration,
    Fingerprint,
    Levels,
    Location,
    Pace,
    RadioBlocked,
    add_to_map,
    check_map,
    follow,
    format_location,
    format_map_check,
    locate_sequence,
    read_map,
    scan_now,
    scans_from_log,
)
from enodia.geocode import (
    MAX_WALKING_SPEED_MS,
    OVERPASS_URL,
    GeocodeError,
    format_geocoding,
    geocode_notebook,
    geocoded_path,
    notebook_marks,
    parse_proxy,
    write_geocoded,
)
from enodia.monitor import WifiMonitor
from enodia.netlog import NetworkLog, SeenNetwork, find_open_networks, outings, read_log
from enodia.preflight import format_preflight, run_preflight
from enodia.reconcile import (
    PATH_LOSS_EXPONENT,
    NotebookError,
    check_pace,
    check_passes,
    folded,
    format_pace_check,
    format_pass_check,
    format_report,
    reconcile,
)
from enodia.streets import StreetMap, read_streets, write_streets
from enodia.system import data_dir, map_path, session_log_path
from enodia.voice import BackgroundVoice, ESpeak, PicoTTS, VoiceController, default_voice

# How many answers the live map keeps behind the current one: under a minute
# of walk at the default interval, enough to show which way you were going.
TRAIL = 10
# How far what the run learned about the card has to move before it is said
# again: less is the median of a few more pairs settling, not news.
CALIBRATION_NEWS_DB = 2.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enodia",
        description="Wardriving on foot, without GPS: log every Wi-Fi network you walk past, "
        "say what changes through the headphones, and afterwards place each access point "
        "along the route from a notebook of street crossings.",
    )
    parser.add_argument("--version", action="version", version=f"enodia {__version__}")
    parser.add_argument(
        "-i",
        "--interface",
        action="append",
        metavar="IFACE",
        help="Wi-Fi interface to watch (repeatable; default: every Wi-Fi interface)",
    )
    parser.add_argument(
        "-t",
        "--interval",
        type=float,
        default=5,
        metavar="SECONDS",
        help="seconds between scans (default: 5)",
    )
    where = parser.add_mutually_exclusive_group()
    where.add_argument(
        "-l",
        "--log",
        default=None,
        metavar="FILE",
        help="write this one log file, as JSON Lines, instead of one file per outing; with "
        "--locate --watch, record what the run scanned (default there: nothing is written)",
    )
    where.add_argument(
        "--dir",
        default=None,
        metavar="DIRECTORY",
        help="directory for the logs, one file per outing named by its start time "
        "(default: $XDG_DATA_HOME/enodia, that is ~/.local/share/enodia)",
    )
    parser.add_argument(
        "--voice",
        choices=["auto", "pico", "espeak", "none"],
        default="auto",
        help="speech engine; auto picks espeak-ng, or SVOX Pico when espeak-ng is missing "
        "(default: auto)",
    )
    parser.add_argument(
        "--lang", default="en-US", help="language of the announcements (default: en-US)"
    )
    parser.add_argument(
        "--ssid-lang",
        default="es-ES",
        help="language used to pronounce network names (default: es-ES)",
    )
    parser.add_argument(
        "--no-log-every-scan",
        action="store_true",
        help="log only connection changes and new networks, not every scan",
    )
    parser.add_argument(
        "--say-status",
        action="store_true",
        help="also say the per-cycle status ('Scanning' and the time); events are always spoken",
    )
    parser.add_argument(
        "--say-signal",
        action="store_true",
        help="also say the signal quality every cycle "
        "(1.7 seconds of a number that seldom changes)",
    )
    parser.add_argument(
        "--say-time-every",
        type=float,
        default=0,
        metavar="SECONDS",
        help="say the time every SECONDS seconds, never skipped, even while network names are "
        "being read (it may then come a few seconds late). Default: with --say-status, once "
        "per cycle and only while the voice is free",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="say only the time (every cycle, or every --say-time-every seconds), the button's "
        "marks and the failures. Nothing about networks or connections: those are in the log",
    )
    parser.add_argument(
        "--no-hour",
        dest="speak_time",
        action="store_false",
        help="never say the time, not even with --quiet or --say-status. With a headset button "
        "the marks carry the time, and what is left is silence except marks and failures",
    )
    parser.add_argument(
        "--say-names",
        type=int,
        default=3,
        metavar="N",
        help="name new networks one by one up to N per cycle. Past that, say the count and "
        "name only the open ones (default: 3. With 0, only open networks are ever named)",
    )
    parser.add_argument(
        "--no-fresh",
        dest="fresh",
        action="store_false",
        help="read the Wi-Fi daemon's current view instead of asking it to scan every cycle: "
        "faster, but that view can be days old and list networks that are long gone",
    )
    parser.add_argument(
        "--button",
        default="auto",
        metavar="auto|off|list|PATH",
        help="headset button that marks a crossing on each press: 'auto' listens to every "
        "input device with media keys, PATH to one /dev/input/eventN (any key counts), "
        "'list' shows the input devices and exits, 'off' disables it (default: auto)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="carry on with the outing under way: continue the newest log if it was written to "
        "less than 30 minutes ago, with its networks counted as seen and its marks counting "
        "on (default: every run is a new outing, with a new log and marks from one)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        metavar="N",
        help="stop after N scans (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="check everything an outing needs -- interfaces, a scan, the voice, the lid, "
        "the button, the battery, the log -- say one line each, and exit 1 if any fails",
    )
    parser.add_argument(
        "--open-networks",
        metavar="LOG",
        help="list the unencrypted networks recorded in a log file, strongest first, and exit",
    )
    parser.add_argument(
        "--reconcile",
        nargs=2,
        metavar=("LOG", "NOTEBOOK"),
        help="join a log with a notebook of timed street crossings ('17:52:10 Rivera y Obligado' "
        "per line) and print where every network was strongest and, with coordinates "
        "on the crossings, where each access point probably stands; offline",
    )
    parser.add_argument(
        "--pace",
        choices=["movement", "clock"],
        help="with --reconcile: how to share out each stretch between two crossings. "
        "'movement' reads the pace from how much the networks in view turn over, "
        "so a stop stays a stop; 'clock' interpolates on time, assuming a steady "
        "pace (default: movement)",
    )
    parser.add_argument(
        "--path-loss",
        type=float,
        default=PATH_LOSS_EXPONENT,
        metavar="N",
        help="with --reconcile: the path loss exponent the sightings are weighed with when "
        "an access point is placed, 10 ** (RSSI / 10n). Lower makes the strongest sighting "
        "count for more: 2 is free space, 3 a street with buildings on both sides, 1 is "
        "weighing by received power (default: %(default)s)",
    )
    parser.add_argument(
        "--check-pace",
        action="store_true",
        help="with --reconcile: hold out each crossing in turn and report which method "
        "finds it again more closely; needs coordinates on the crossings",
    )
    parser.add_argument(
        "--check-passes",
        action="store_true",
        help="with --reconcile: for any stretch the notebook shows walked more than once, "
        "report how far apart the passes put the same networks, and the shift between the "
        "two directions, which is what a scan's lag looks like; needs no coordinates",
    )
    parser.add_argument(
        "--csv", metavar="FILE", help="with --reconcile: also write the located networks as CSV"
    )
    parser.add_argument(
        "--geojson",
        metavar="FILE",
        help="with --reconcile: also write the networks, crossings and route as GeoJSON, for a map",
    )
    parser.add_argument(
        "--scans",
        action="store_true",
        help="with --reconcile: also list the position of every scan",
    )
    parser.add_argument(
        "--geocode",
        metavar="NOTEBOOK",
        help="look every crossing in a notebook up on OpenStreetMap and write a second "
        "notebook beside it carrying the coordinates it found; the original is never "
        "touched. This is the one command in Enodia that goes online, and only when asked "
        "for by name. Needs --area",
    )
    parser.add_argument(
        "--area",
        metavar="NAME",
        help="with --geocode: the city or district the notebook walks, as OpenStreetMap names "
        "it ('Montevideo'), or four numbers 's,w,n,e' for a bounding box. Without it "
        "'Freire' would match a street in Chile",
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        help="with --geocode: where to write the notebook with coordinates "
        "(default: the original's name with .geo before its suffix)",
    )
    parser.add_argument(
        "--marks",
        metavar="LOG",
        help="with --geocode: take the times of crossings that have none from this log's "
        "button marks, so the walking pace can check what OpenStreetMap answered",
    )
    parser.add_argument(
        "--proxy",
        metavar="URL",
        help="with --geocode: send the one request through a SOCKS5 proxy "
        "('socks5://127.0.0.1:9050' for Tor). The proxy resolves the hostname, never this "
        "machine, and if it cannot be reached nothing is sent. ALL_PROXY and the rest of "
        "the environment are deliberately never read. Needs: uv sync --extra socks",
    )
    parser.add_argument(
        "--export-public",
        nargs=2,
        metavar=("LOG", "NOTEBOOK"),
        help="write a publishable copy of one outing: the addresses and names of other "
        "people's networks substituted, the crossings renamed, the clock and the map moved "
        "to an artificial origin (the streets stay where they are with --keep-places). Both "
        "files together, because a notebook of real street corners says where you walked "
        "whatever the log says",
    )
    parser.add_argument(
        "--ssid",
        choices=("remove", "pseudonym", "keep"),
        default="remove",
        help="with --export-public: what to do with the names of the networks. Removed by "
        "default, since a name is chosen by a person and often says which person",
    )
    parser.add_argument(
        "--keep-places",
        action="store_true",
        help="with --export-public: leave the crossings named and placed as they are, and "
        "substitute only the networks. The route is then on the map for anyone to see, and so "
        "is roughly where each access point along it stands; the clock is moved all the same",
    )
    parser.add_argument(
        "--keep-time",
        action="store_true",
        help="with --export-public: leave every time as it was recorded instead of moving the "
        "walk to 1970-01-01, so the date and the hour of each step of it are published "
        "(default: the intervals kept and the day moved)",
    )
    parser.add_argument(
        "--mac-shaped",
        action="store_true",
        help="with --export-public: write substituted addresses as locally administered MACs "
        "rather than as ap-1c8a74f992ae, for tools downstream that insist on the shape",
    )
    parser.add_argument(
        "--key-file",
        metavar="FILE",
        help="with --export-public: the key the pseudonyms are made with. Made once in "
        "$XDG_CONFIG_HOME/enodia by default, and kept, so that two exports months apart give "
        "the same network the same pseudonym",
    )
    parser.add_argument(
        "--assistant",
        action="store_true",
        help="a guided menu over these same commands: it walks you through capturing, "
        "reconciling, adding to the map and locating, one decision per screen, and runs "
        "exactly what the flags run. Needs a terminal to ask on",
    )
    parser.add_argument(
        "--outing",
        metavar="TOKEN",
        help="with --reconcile or --map-add: which walk of the log to read, when the file "
        "holds more than one. `--log walk.jsonl` reused every week appends to the same "
        "pages, and a notebook is one walk's. The last walk in the file by default, and "
        "which one that was is printed whenever there is a choice",
    )
    parser.add_argument(
        "--streets",
        metavar="FILE",
        help="the blocks as OpenStreetMap draws them. With --geocode it is written, from the "
        "same one request the crossings came out of. With --reconcile or --map-add it is "
        "read, and every scan is placed along the street instead of on the straight line "
        "between two crossings, which is what happens without it",
    )
    parser.add_argument(
        "--surroundings",
        action="store_true",
        help="with --geocode --streets: a second request for the neighbourhood around what was "
        "found, the buildings, the water, the parks and every named street, so that --svg and "
        "--live-map can draw it. Asked for separately because the box to ask about is not "
        "known until the crossings are",
    )
    parser.add_argument(
        "--svg",
        metavar="FILE",
        help="with --reconcile: draw the walk as a plan, from the OpenStreetMap geometry in "
        "--streets. No tiles: it is written once and opens with nothing fetched. Needs "
        "coordinates on the crossings",
    )
    parser.add_argument(
        "--svg-names",
        action="store_true",
        help="with --svg: write each pinned network's name beside its dot, in small type",
    )
    parser.add_argument(
        "--overpass-url",
        default=OVERPASS_URL,
        metavar="URL",
        help="with --geocode: the Overpass endpoint to ask (default: %(default)s)",
    )
    parser.add_argument(
        "--max-speed",
        type=float,
        default=MAX_WALKING_SPEED_MS,
        metavar="M_PER_S",
        help="how fast you move, at most, in metres per second: with --geocode the pace above "
        "which a stretch is taken for a wrong lookup rather than a fast walk, with --locate "
        "--watch the speed the answers are kept to; raise it for an outing on a bicycle "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--map",
        metavar="FILE",
        default=None,
        help="the fingerprint map to build or look yourself up in "
        "(default: $XDG_DATA_HOME/enodia/map/map.jsonl)",
    )
    parser.add_argument(
        "--map-add",
        nargs=2,
        metavar=("LOG", "NOTEBOOK"),
        help="reconcile an outing and add its scans to the map, as fingerprints of the "
        "places they were taken from; an outing already in the map is not added twice",
    )
    parser.add_argument(
        "--locate",
        nargs="?",
        const="",
        default=None,
        metavar="LOG",
        help="scan now and say where on the map you are, or, given a log, locate its last "
        "scan; says so instead of guessing when nothing in view matches the map",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="with --locate and no LOG: keep scanning every --interval seconds and say where "
        "you are as it changes; --cycles bounds it, Ctrl+C stops it, --log FILE records it",
    )
    parser.add_argument(
        "--no-walking-pace",
        dest="walking_pace",
        action="store_false",
        help="with --locate --watch: take each scan's place along the stretch as it comes, "
        "instead of keeping the answers to the pace somebody walks at (default: kept)",
    )
    parser.add_argument(
        "--live-map",
        metavar="FILE",
        help="with --locate --watch: write a page every cycle showing where you are on the "
        "map, to open once in a browser and leave open, since it reloads itself; with "
        "--streets it draws the streets and whatever --surroundings brought too",
    )
    parser.add_argument(
        "--check-map",
        action="store_true",
        help="hold out each walk in the map in turn, locate its scans from the rest, and "
        "report how far off it was, with and without the signal",
    )
    parser.add_argument(
        "--card-offset",
        type=float,
        default=0.0,
        metavar="DB",
        help="with --check-map: hear every held-out scan as a card reading DB decibels above "
        "the map's would (below, when negative), to see what another card costs each way of "
        "matching and what calibrating on the run wins back (default: the same card)",
    )
    parser.add_argument(
        "--along",
        choices=["matches", "levels"],
        default="matches",
        help="how the place along the stretch is found: the middle of the fingerprints that "
        "matched, or, experimental, where the scan's levels fit how each network rises and "
        "falls along the block (default: matches)",
    )
    parser.add_argument(
        "--match",
        choices=["networks", "signal"],
        default="networks",
        help="what a fingerprint is matched on: which networks are in view, or also how "
        "strong they came in. 'signal' is barely tested and has so far done worse: on the one "
        "real walk that tried it, it lost 38 of 111 scans on streets the map knows, where "
        "'networks' lost none (default: networks)",
    )
    parser.add_argument(
        "--weigh",
        choices=["rarity", "alike"],
        default="rarity",
        help="what a network in view counts for when matched: 'rarity' weighs each by how "
        "rare it is in the map, so a router heard everywhere says less than one heard on "
        "one block; 'alike' counts them all the same (default: rarity)",
    )
    parser.add_argument(
        "--sequence",
        choices=["tie", "path"],
        default="tie",
        help="with --locate LOG, --locate --watch or --check-map: what the scans before the last "
        "one do. 'tie' settles a tie between two stretches; 'path' chooses the likeliest path "
        "through all of them and can overrule the last scan. 'path' is barely tested and has so "
        "far done no better, and was late onto a new block (default: tie)",
    )
    return parser


def make_voice(name: str) -> BackgroundVoice:
    """Voice for the `--voice` choice, speaking from its own thread.

    Speech never blocks the scan loop: the loop is what sets how finely the
    route is sampled, and saying a handful of network names costs more than a
    whole scan cycle.
    """
    if name == "none":
        return BackgroundVoice(VoiceController())
    if name == "pico":
        return BackgroundVoice(VoiceController(PicoTTS()))
    if name == "espeak":
        return BackgroundVoice(VoiceController(ESpeak()))
    voice = default_voice()
    if voice is None:
        print(
            "No speech engine found (espeak-ng, pico2wave or pico-tts): "
            "printing instead of speaking."
        )
    return BackgroundVoice(VoiceController(voice))


def use_color() -> bool:
    """Colour for a terminal only, and never when NO_COLOR is set (no-color.org)."""
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def make_button(choice: str) -> ButtonMarker | None:
    """The marker for the `--button` choice, or None when there is nothing to listen to."""
    if choice == "off":
        return None
    if choice == "auto":
        devices = find_button_devices()
        if not devices:
            print("No headset button found: no input device with media keys. Marking is off.")
            return None
        return ButtonMarker(devices)
    return ButtonMarker([choice], keys=None)


def say_location(voice: BackgroundVoice, found: Location | None, lang: str, names: str) -> None:
    """Say where you are, the crossing names in the language network names are read in.

    The same split the monitor makes: a crossing in Montevideo is no more English
    than a network called "Casa Rivas" is.
    """
    if found is None:
        voice.say("Not on the map", lang=lang)
        return
    place = found.place
    mark = found.corner
    if mark is None:
        voice.say(f"{place.name_from} to {place.name_to}", lang=names)
        voice.say(f"{round(place.fraction * 100)} percent", lang=lang)
    else:
        voice.say(mark, lang=names)
    if found.uncertain:
        voice.say("Uncertain", lang=lang)


def spoken(found: Location | None) -> tuple[str, object] | None:
    """What the voice says a place is: the corner when at one, the stretch otherwise.

    What `--watch` compares to decide whether there is something new to say.
    The stretch alone got it wrong at a corner: walking through one is leaving
    one stretch for the next at the same place, and the corner was said a
    second time for a walk that had not moved.
    """
    if found is None:
        return None
    mark = found.corner
    return ("corner", folded(mark)) if mark is not None else ("stretch", found.place.key)


def run_watch(
    args: argparse.Namespace, map_file: Path, known: Sequence[Fingerprint], voice: BackgroundVoice
) -> int:
    """`--locate --watch`: a fresh scan every interval, and where each one puts you.

    Each scan is placed with the ones before it, the way a log's last scan is,
    so a tie is settled by the walk as it happens. One line is printed per
    scan. Speech is for what changes: a new stretch or a corner reached, or the
    map losing you or finding you again, is said in full through
    `say_location`, and another tenth of the way along the same stretch is a
    status line, said only when the voice is free, since a percentage said late
    is another place. What counts as new is what would be said (`spoken`), so a
    corner walked through from one stretch to the next is said once. The loop
    keeps the walk's own cadence, sleeping only what is left of `--interval`
    after the scan and the lookup, and a fresh scan takes about five seconds
    per card, so a shorter interval is not kept. A blocked radio costs the
    cycle and nothing else, as on the walk: the run keeps what it knew, "Radio
    blocked" is said once and "Scanning again" when it comes back.

    Nothing is written unless `--log FILE` names a log, and then every cycle
    goes into it in the walk's own format: one scan record of what all the
    cards heard together, numbered by its cycle, or a `scan_failed` when the
    radio was blocked, so the hole is not read as a street with no Wi-Fi. The
    run can then be located again from the file, against another map or with
    other flags. Lines are flushed as they are printed, since a run piped into
    `tee` is a run somebody is watching.

    Each answer's place along its stretch is kept to a walking pace by `Pace`,
    so the dot moves the way somebody walks and not the way one scan after
    another lands; `--no-walking-pace` takes each scan as it comes.

    With `--match signal` the card is calibrated against the map on the run
    (`Calibration`): each scan teaches it where the level-free matching is sure,
    and once it knows, the levels are corrected before they are compared, and
    it says so. What `--log` records is what the card heard, uncorrected.

    `--live-map FILE` is the same run drawn: every cycle the page is written
    again, beside the target and renamed onto it, with the last `TRAIL`
    answers behind the current one. A map without coordinates has nothing to
    draw on, and says so before the radio is asked anything.
    """
    by_signal = args.match == "signal"
    by_rarity = args.weigh == "rarity"
    blocked = False
    pace = Pace.of(known, args.max_speed) if args.walking_pace else None
    live = None if args.live_map is None else Path(args.live_map)
    drawn = None
    trail: deque[tuple[float, float]] = deque(maxlen=TRAIL)
    if live is not None:
        if not mapped_places(known):
            print(f"error: {map_file}: no coordinates to draw", file=sys.stderr)
            return 1
        # A streets file that is not there is an error here, and not an empty
        # drawing. `read_streets` calls a missing file no geometry, because
        # `--geocode --streets` names it before writing it, but a live map
        # asked to draw the streets and drawing none said nothing: a path with
        # a typo in it, or a file never copied to this machine, gave a page of
        # grey dots and no reason why.
        if args.streets is not None and not Path(args.streets).exists():
            print(
                f"error: {args.streets}: no such streets file. --geocode --streets writes one, "
                "and without --streets the live map is drawn with no streets at all.",
                file=sys.stderr,
            )
            return 1
        try:
            drawn = walked_streets(args.streets)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    log = None if args.log is None else NetworkLog(Path(args.log))
    if log is not None:
        print(f"Recording scans to {log.path}", flush=True)
    if live is not None:
        print(f"Live map at {live}: open it in a browser", flush=True)

    def fresh() -> Iterator[list[SeenNetwork]]:
        nonlocal blocked
        done = 0
        while args.cycles <= 0 or done < args.cycles:
            started = time.monotonic()
            done += 1
            try:
                seen = scan_now(args.interface)
            except RadioBlocked as exc:
                print(f"Cannot scan: {exc}", flush=True)
                if log is not None:
                    log.record_scan_failed(None, str(exc))
                if not blocked:
                    voice.say("Radio blocked", lang=args.lang)
                blocked = True
            else:
                if log is not None:
                    log.record_scan(seen, cycle=done)
                if blocked:
                    voice.say("Scanning again", lang=args.lang)
                blocked = False
                yield seen
            if args.cycles <= 0 or done < args.cycles:
                remaining = args.interval - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)

    calibration = Calibration() if by_signal else None
    announced: float | None = None
    refused = False
    levels = Levels.of(known) if args.along == "levels" else None
    latest: list[SeenNetwork] = []

    def corrected(scans: Iterator[list[SeenNetwork]]) -> Iterator[list[SeenNetwork]]:
        """Each scan at the levels the map's card would have read, once the run knows them."""
        nonlocal announced, refused, latest
        for seen in scans:
            if calibration is None:
                latest = seen
                yield seen
                continue
            calibration.learn(known, seen, by_rarity)
            offset, measured = calibration.offset, calibration.measured
            if offset is not None and (
                announced is None or abs(offset - announced) >= CALIBRATION_NEWS_DB
            ):
                print(card_reads(offset), flush=True)
                announced = offset
            elif offset is None and measured is not None and not refused:
                print(
                    f"This card reads {abs(measured):.0f} dB off the map's card, too far to be "
                    "a card: not corrected",
                    flush=True,
                )
                refused = True
            latest = calibration.correct(seen)
            yield latest

    previous: Location | None = None
    first = True
    try:
        for answer in follow(known, corrected(fresh()), args.sequence, by_signal, by_rarity):
            if levels is not None:
                answer = levels.place(answer, latest)
            found = answer if pace is None else pace.keep(answer, time.monotonic())
            stamp = time.strftime("%H:%M:%S")
            if found is None:
                said = "not on the map"
            else:
                said = found.describe() + (", uncertain" if found.uncertain else "")
            print(f"{stamp}  {said}", flush=True)
            if live is not None:
                where = None if found is None else found.place
                if where is not None and where.lat is not None and where.lon is not None:
                    trail.append((where.lat, where.lon))
                page = live_map(known, found, trail, f"{stamp}  {said}", drawn, args.interval)
                written = live.with_name(f".{live.name}.part")
                written.write_text(page, encoding="utf-8")
                # Renamed into place, so a browser reloading mid-write reads the
                # page before or the page after and never half of one.
                os.replace(written, live)
            if first or spoken(found) != spoken(previous):
                say_location(voice, found, args.lang, args.ssid_lang)
            elif found is not None and found.corner is None and previous is not None:
                if int(found.place.fraction * 10) != int(previous.place.fraction * 10):
                    voice.say(
                        f"{round(found.place.fraction * 100)} percent", lang=args.lang, status=True
                    )
            previous, first = found, False
    except KeyboardInterrupt:
        print("\nStopped.")
    except OSError as exc:
        # The log and the live map are what touch the disk here, and a run that
        # cannot write them has stopped keeping what it was asked to keep.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def card_reads(offset: float) -> str:
    """What the run learned about this card, said once when it starts correcting for it."""
    rounded = round(offset)
    if not rounded:
        return "Calibrated against the map: this card reads as the map's card did"
    way = "below" if rounded < 0 else "above"
    return (
        f"Calibrated against the map: this card reads {abs(rounded)} dB {way} the map's card, "
        "and is corrected for that"
    )


def say_which_walk(log_path: str | None, wanted: str | None) -> str | None:
    """Which walk of a log is about to be read, said out loud when there is a choice.

    One file can hold several walks, and picking one of them is a decision, so
    it is never made in silence. Reading the whole file instead was not an
    option either: that is the bug this replaced, where a notebook of the second
    walk was read against the first walk's date.
    """
    if log_path is None:
        return wanted
    try:
        walks = outings(read_log(log_path))
    except OSError:
        return wanted  # the command itself will report this, with its own message
    if len(walks) < 2:
        return wanted
    chosen = wanted if wanted is not None else walks[-1]
    others = ", ".join(walk or "(unnamed)" for walk in walks if walk != chosen)
    print(f"{log_path} holds {len(walks)} walks. Reading {chosen or '(unnamed)'}, not {others}.")
    return chosen


def walked_streets(path: str | None) -> StreetMap | None:
    """The drawn blocks to place scans along, or None to use the straight line."""
    return None if path is None else read_streets(path)


def run_export(args: argparse.Namespace) -> int:
    """Write a publishable copy of one outing, and say what it did and did not promise."""
    log, notebook = args.export_public
    out = Path(args.out) if args.out else Path("public")
    try:
        key, told = read_key(Path(args.key_file) if args.key_file else key_path())
        if told:
            print(told)
        done = export_outing(
            Path(log),
            Path(notebook),
            out,
            key,
            ssid=args.ssid,
            mac_shaped=args.mac_shaped,
            outing=args.outing,
            keep_places=args.keep_places,
            keep_time=args.keep_time,
        )
    except (ExportError, NotebookError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_export(done))
    return 0


def run_geocode(args: argparse.Namespace) -> int:
    """Look a notebook's crossings up on OpenStreetMap and write a second notebook."""
    target = Path(args.out) if args.out else geocoded_path(args.geocode)
    try:
        result = geocode_notebook(
            args.geocode,
            args.area,
            proxy=parse_proxy(args.proxy) if args.proxy else None,
            url=args.overpass_url,
            marks=notebook_marks(args.marks, say_which_walk(args.marks, args.outing)),
            surroundings=args.surroundings,
            max_speed_ms=args.max_speed,
            drawing=args.streets is not None,
        )
        # Nothing resolved means nothing to write: a copy of the notebook with no
        # coordinates in it would only be a second file to keep in step.
        written = write_geocoded(args.geocode, target, result) if result.found else 0
    except (GeocodeError, NotebookError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_geocoding(result, target, written))
    if args.streets and not result.streets:
        # Nothing was asked, so nothing came back, and writing that down would
        # replace whatever the file held with an empty one.
        print(f"No street was asked for, so {args.streets} was left as it was.")
    elif args.streets:
        try:
            drawn = replace(result.surroundings, streets=tuple(result.drawn))
            shapes = write_streets(args.streets, drawn)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        around = result.surroundings
        kinds = [
            f"{len(found)} {one if len(found) == 1 else many}"
            for found, one, many in (
                (around.buildings, "building", "buildings"),
                (around.water, "stretch of water", "stretches of water"),
                (around.rivers, "river", "rivers"),
                (around.parks, "park", "parks"),
                (around.roads, "named street", "named streets"),
            )
            if found
        ]
        extra = "".join(f", {one}" for one in kinds)
        print(f"{shapes} shapes into {args.streets}: the streets it asked about{extra}")
    # Nothing to do is not a failure: a notebook placed whole has nothing left
    # to look up, and asking it only for its streets is what that run was for.
    done = bool(written) or all(one.placed for one in result.crossings)
    return 0 if done else 1


def run_map(args: argparse.Namespace, map_file: Path) -> int:
    """The map commands: add an outing to it, look yourself up in it, or measure it."""
    by_signal = args.match == "signal"
    by_rarity = args.weigh == "rarity"
    if args.map_add:
        log, notebook = args.map_add
        try:
            added, outing = add_to_map(
                map_file,
                log,
                notebook,
                by_movement=args.pace != "clock",
                streets=walked_streets(args.streets),
                outing=say_which_walk(log, args.outing),
            )
        except (NotebookError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if added:
            print(f"{added} fingerprints from the outing of {outing} added to {map_file}")
        elif not outing:
            print("Nothing added: no scan of that log fell between two crossings.")
        else:
            print(f"The outing of {outing} is already in {map_file}: nothing added.")
        return 0

    if args.check_map:
        try:
            fingerprints = read_map(map_file)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        offset, along = args.card_offset, args.along
        check = partial(
            check_map, fingerprints, by_rarity=by_rarity, card_offset=offset, along=along
        )
        # In the order the report's columns are read in, the calibrated one last.
        checked = [
            check(),
            check(by_signal=True),
            check(sequence="tie"),
            check(sequence="path"),
            check(keep_pace=True),
            check(by_signal=True, calibrate=True),
        ]
        print(format_map_check(fingerprints, *checked, card_offset=offset, along=along))
        return 0

    # A map that is not there is an error here, and not an empty map. `read_map`
    # calls a missing file empty because `--map-add` has to be able to create
    # one, but "not on the map" is a sentence about the street, and a path with
    # a typo in it answered that way every five seconds of a `--watch`, for as
    # long as you cared to walk.
    if not map_file.exists():
        print(
            f"error: {map_file}: no such map. --map-add builds one from an outing and its "
            "notebook, and --map names one somewhere else.",
            file=sys.stderr,
        )
        return 1
    voice = make_voice(args.voice)
    try:
        try:
            known = read_map(map_file)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.watch:
            return run_watch(args, map_file, known, voice)
        try:
            # A fresh scan stands alone, unless `--watch` keeps the run. A log
            # has the scans before its last one, and they are what settles a
            # tie between two stretches.
            scans = (
                scans_from_log(args.locate, say_which_walk(args.locate, args.outing))
                if args.locate
                else [scan_now(args.interface)]
            )
        except RadioBlocked as exc:
            print(f"Cannot scan: {exc}")
            voice.say("Radio blocked", lang=args.lang)
            return 1
        except (NotebookError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        found = locate_sequence(
            known, scans, sequence=args.sequence, by_signal=by_signal, by_rarity=by_rarity
        )
        if args.along == "levels":
            found = Levels.of(known).place(found, scans[-1])
        print(format_location(found, map_file))
        say_location(voice, found, args.lang, args.ssid_lang)
        return 0
    finally:
        voice.close()


def run_preflight_command(args: argparse.Namespace) -> int:
    """Every check an outing needs, and whether to go.

    The voice is built here only to be spoken through by `check_voice`, and it is
    closed again on the way out: it queues its speech on a thread of its own, and
    leaving that thread behind is invisible in a command that exits straight
    afterwards and is not in one that comes back to a menu.
    """
    speaking = None if args.voice == "none" else make_voice(args.voice)
    try:
        checks = run_preflight(
            None if speaking is None else speaking.controller,
            make_button(args.button),
            args.button,
            None if args.log is None else Path(args.log),
            log_dir=None if args.dir is None else Path(args.dir),
            resume=args.resume,
            interfaces=args.interface,
        )
    finally:
        if speaking is not None:
            speaking.close()
    print(format_preflight(checks, color=use_color()))
    return 1 if any(check.failed for check in checks) else 0


def run_reconcile(args: argparse.Namespace) -> int:
    """Join a log with its notebook, and write out whatever was asked for."""
    log, notebook = args.reconcile
    try:
        by_movement = args.pace != "clock"
        drawn = walked_streets(args.streets)
        walk = say_which_walk(log, args.outing)
        if args.check_pace or args.check_passes:
            reports = []
            if args.check_pace:
                reports.append(format_pace_check(check_pace(log, notebook, drawn, walk)))
            if args.check_passes:
                reports.append(
                    format_pass_check(
                        check_passes(
                            log,
                            notebook,
                            by_movement=by_movement,
                            streets=drawn,
                            outing=walk,
                            path_loss=args.path_loss,
                        )
                    )
                )
            print("\n\n".join(reports))
            return 0
        result = reconcile(
            log,
            notebook,
            by_movement=by_movement,
            streets=drawn,
            outing=walk,
            path_loss=args.path_loss,
        )
    except (NotebookError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_report(result, with_scans=args.scans))
    # The writing is inside a handler too. A destination that cannot be
    # written to is the same kind of news as a log that cannot be read, and
    # a traceback after a report has already printed is the worst of both.
    try:
        if args.csv:
            result.write_csv(args.csv)
            print(f"\nCSV written to {args.csv}")
        if args.geojson:
            result.write_geojson(args.geojson)
            print(f"\nGeoJSON written to {args.geojson}")
        if args.svg:
            picture = svg_map(result, drawn, names=args.svg_names)
            if picture is None:
                print(
                    "\nNothing to draw: a plan needs coordinates on the crossings. See --geocode."
                )
            else:
                Path(args.svg).write_text(picture, encoding="utf-8")
                print(f"\nPlan drawn into {args.svg}")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def walk_log_path(args: argparse.Namespace) -> Path:
    """Where this outing's log goes, announced, because choosing it is a decision.

    Apart from the walk so that a caller can settle it first and still know
    afterwards which file the outing went into. It must be called once and once
    only: `session_log_path` makes the directory on its way, and names the file
    after the second it was called in, so a second call to preview the name
    hands back a different one.
    """
    if args.log is not None:
        return Path(args.log)
    log_path, continuing = session_log_path(
        Path(args.dir) if args.dir else data_dir(), resume=args.resume
    )
    print(f"{'Continuing' if continuing else 'New outing:'} {log_path}")
    return log_path


def run_walk(args: argparse.Namespace, log_path: Path | None = None) -> int:
    """The outing itself: scan, speak and log until Ctrl+C or `--cycles`."""
    if log_path is None:
        log_path = walk_log_path(args)
    voice: BackgroundVoice | None = None
    monitor: WifiMonitor | None = None
    # Everything is inside the handler and not only the loop. Ctrl+C while the
    # speech engine is starting, while the log is being read back, or while a
    # headset is being opened is the same "I am done" as Ctrl+C mid-scan, and it
    # used to come out as a traceback instead: harmless for a process that was
    # ending anyway, and the end of an assistant that expected to be handed
    # control back.
    try:
        # A voice of this outing's own, every time. `BackgroundVoice.close()`
        # sets a flag it never clears, so a closed one accepts every `say` and
        # speaks none of them: holding one across two walks would leave the
        # second one silent, with nothing in the log and nothing on the screen
        # to say why.
        voice = make_voice(args.voice)
        monitor = WifiMonitor(
            voice=voice,
            log=NetworkLog(log_path),
            interfaces=args.interface,
            time_between_scans=args.interval,
            lang=args.lang,
            ssid_lang=args.ssid_lang,
            log_every_scan=not args.no_log_every_scan,
            speak_status=args.say_status,
            speak_signal=args.say_signal,
            say_time_every=args.say_time_every,
            quiet=args.quiet,
            speak_time=args.speak_time,
            fresh_scan=args.fresh,
            speak_names_up_to=args.say_names,
        )
        if args.resume:
            recovered = monitor.resume_from_log()
            if recovered or monitor.marks:
                print(
                    f"Carrying on from {log_path}: {recovered} networks already seen, "
                    f"{monitor.marks} marks made."
                )
        button = make_button(args.button)
        if button is not None:
            try:
                opened = button.open()
            except PermissionError as exc:
                opened = []
                print(f"Button unavailable: {exc}")
            if opened:
                print("Button: " + ", ".join(str(path) for path in opened))
                monitor.use_button(button)
                voice.say("Button ready", lang=args.lang)
            else:
                voice.say("Button unavailable", lang=args.lang)
        monitor.scan_networks_loop(cycles=args.cycles)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        # The monitor closes the voice it holds and the button it was given, and
        # waits for its own thread. A process that is about to exit does not care
        # about any of that; a second outing started from the same run does.
        if monitor is not None:
            monitor.close()
        elif voice is not None:
            voice.close()
    # The path again at the end: the one from the start has long scrolled off by then.
    print(f"Log: {log_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    reconcile_only = [
        flag
        for flag, given in (
            ("--check-pace", args.check_pace),
            ("--check-passes", args.check_passes),
            ("--path-loss", args.path_loss != PATH_LOSS_EXPONENT),
            ("--csv", args.csv is not None),
            ("--geojson", args.geojson is not None),
            ("--scans", args.scans),
        )
        if given
    ]
    if reconcile_only and not args.reconcile:
        parser.error(f"{', '.join(reconcile_only)}: only meaningful together with --reconcile")
    geocode_only = [
        flag
        for flag, given in (
            ("--area", args.area is not None),
            ("--marks", args.marks is not None),
            ("--proxy", args.proxy is not None),
            ("--overpass-url", args.overpass_url != OVERPASS_URL),
        )
        if given
    ]
    if geocode_only and not args.geocode:
        parser.error(f"{', '.join(geocode_only)}: only meaningful together with --geocode")
    # `--out` belongs to both of the commands that write a file of their own.
    if args.out is not None and not (args.geocode or args.export_public):
        parser.error("--out: only meaningful together with --geocode or --export-public")
    export_only = [
        flag
        for flag, given in (
            ("--ssid", args.ssid != "remove"),
            ("--mac-shaped", args.mac_shaped),
            ("--keep-places", args.keep_places),
            ("--keep-time", args.keep_time),
            ("--key-file", args.key_file is not None),
        )
        if given
    ]
    if export_only and not args.export_public:
        parser.error(f"{', '.join(export_only)}: only meaningful together with --export-public")
    if args.streets is not None and not (
        args.geocode or args.reconcile or args.map_add or args.live_map
    ):
        parser.error(
            "--streets: only meaningful together with --geocode, --reconcile, --map-add "
            "or --live-map"
        )
    if args.max_speed != MAX_WALKING_SPEED_MS and not (args.geocode or args.watch):
        parser.error("--max-speed: only meaningful together with --geocode or --locate --watch")
    if args.max_speed != MAX_WALKING_SPEED_MS and args.watch and not args.walking_pace:
        parser.error("--max-speed and --no-walking-pace contradict each other")
    if not args.walking_pace and not args.watch:
        parser.error("--no-walking-pace: only meaningful together with --locate --watch")
    if args.live_map is not None and not args.watch:
        parser.error("--live-map: only meaningful together with --locate --watch")
    if args.surroundings and not (args.geocode and args.streets):
        parser.error("--surroundings: only meaningful together with --geocode and --streets")
    reads_a_log = bool(
        args.reconcile or args.map_add or args.export_public or (args.geocode and args.marks)
    ) or bool(args.locate)
    if args.outing is not None and not reads_a_log:
        parser.error(
            "--outing: only meaningful together with --reconcile, --map-add, "
            "--export-public, --locate LOG or --geocode --marks"
        )
    if args.svg is not None and not args.reconcile:
        parser.error("--svg: only meaningful together with --reconcile")
    if args.svg_names and args.svg is None:
        parser.error("--svg-names: only meaningful together with --svg")
    if args.watch and args.locate != "":
        parser.error(
            "--watch: only meaningful together with --locate and a live scan, not --locate LOG"
        )
    if args.card_offset and not args.check_map:
        parser.error("--card-offset: only meaningful together with --check-map")
    if args.watch and args.dir is not None:
        parser.error(
            "--dir: not meaningful with --locate --watch, which records only to a --log FILE"
        )
    if args.geocode and args.area is None:
        parser.error("--geocode: --area is needed, to say which city the notebook walks")
    mapping = bool(args.map_add) or args.locate is not None or args.check_map
    # The assistant reaches the map too, so naming one is meaningful with it.
    chooses_a_map = mapping or args.assistant
    if args.pace is not None and not (args.reconcile or args.map_add):
        parser.error("--pace: only meaningful together with --reconcile or --map-add")
    map_only = [
        flag
        for flag, given in (
            ("--map", args.map is not None),
            ("--match", args.match != "networks"),
            ("--weigh", args.weigh != "rarity"),
            ("--along", args.along != "matches"),
            ("--sequence", args.sequence != "tie"),
        )
        if given
    ]
    if map_only and not chooses_a_map:
        parser.error(
            f"{', '.join(map_only)}: only meaningful together with "
            "--map-add, --locate or --check-map"
        )
    if not args.speak_time and args.say_time_every:
        parser.error("--no-hour and --say-time-every contradict each other")
    commands = [
        flag
        for flag, given in (
            ("--preflight", args.preflight),
            ("--button list", args.button == "list"),
            ("--open-networks", args.open_networks is not None),
            ("--geocode", args.geocode is not None),
            ("--reconcile", args.reconcile is not None),
            ("--map-add", args.map_add is not None),
            ("--locate", args.locate is not None),
            ("--check-map", args.check_map),
            ("--export-public", args.export_public is not None),
        )
        if given
    ]
    if args.assistant and commands:
        # Two instructions at once, and the assistant is the menu those commands
        # are on. Running one of them instead of the menu would be answering a
        # question nobody asked.
        parser.error(
            f"--assistant: {', '.join(commands)} is a command of its own, and the assistant "
            "is the menu that runs them"
        )

    if args.assistant:
        # Imported here and not at the top: the assistant is a leaf this entry
        # point reaches, and cli is what it reaches back into. At the top the
        # two would be a cycle.
        from enodia.assistant import run_assistant

        return run_assistant(args)

    if args.preflight:
        return run_preflight_command(args)

    if args.button == "list":
        for device in list_input_devices():
            print(f"{device.path}\t{device.name}\t{'media keys' if device.has_media_keys else ''}")
        return 0

    if args.open_networks:
        for found in find_open_networks(args.open_networks):
            network = found.network
            when = found.time.isoformat() if found.time else "?"
            signal = (
                f"{network.signal_dbm} dBm"
                if network.signal_dbm is not None
                else f"{network.signal_percent}%"
            )
            print(f"{when}\t{signal}\t{network.bssid or '?'}\t{network.ssid or '<hidden>'}")
        return 0

    if args.export_public:
        return run_export(args)

    if args.geocode:
        return run_geocode(args)

    if args.reconcile:
        return run_reconcile(args)

    if mapping:
        return run_map(args, Path(args.map) if args.map else map_path())

    return run_walk(args)


if __name__ == "__main__":
    sys.exit(main())
