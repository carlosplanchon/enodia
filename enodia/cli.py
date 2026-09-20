"""Command line entry point: `enodia`."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from enodia import __version__
from enodia.anonymize import ExportError, export_outing, format_export, key_path, read_key
from enodia.button import ButtonMarker, find_button_devices, list_input_devices
from enodia.draw import svg_map
from enodia.fingerprint import (
    Location,
    RadioBlocked,
    add_to_map,
    check_map,
    format_location,
    format_map_check,
    locate_scan,
    read_map,
    scan_from_log,
    scan_now,
)
from enodia.geocode import (
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
from enodia.netlog import NetworkLog, find_open_networks, outings, read_log
from enodia.preflight import format_preflight, run_preflight
from enodia.reconcile import (
    NotebookError,
    check_pace,
    check_passes,
    format_pace_check,
    format_pass_check,
    format_report,
    reconcile,
)
from enodia.streets import StreetMap, read_streets, write_streets
from enodia.system import data_dir, map_path, session_log_path
from enodia.voice import BackgroundVoice, ESpeak, PicoTTS, VoiceController, default_voice


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
        help="write this one log file, as JSON Lines, instead of one file per outing",
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
        help="join a log with a notebook of timed street crossings ('17:52:10 Agraciada y Freire' "
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
        "to an artificial origin. Both files together, because a notebook of real street "
        "corners says where you walked whatever the log says",
    )
    parser.add_argument(
        "--ssid",
        choices=("remove", "pseudonym", "keep"),
        default="remove",
        help="with --export-public: what to do with the names of the networks. Removed by "
        "default, since a name is chosen by a person and often says which person",
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
        "--buildings",
        action="store_true",
        help="with --geocode --streets: a second request for the buildings around what was "
        "found, so that --svg can draw the blocks and not just the lines. Asked for "
        "separately because the box to ask about is not known until the crossings are",
    )
    parser.add_argument(
        "--svg",
        metavar="FILE",
        help="with --reconcile: draw the walk as a plan, from the OpenStreetMap geometry in "
        "--streets. No tiles: it is written once and opens with nothing fetched. Needs "
        "coordinates on the crossings",
    )
    parser.add_argument(
        "--overpass-url",
        default=OVERPASS_URL,
        metavar="URL",
        help="with --geocode: the Overpass endpoint to ask (default: %(default)s)",
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
        "--check-map",
        action="store_true",
        help="hold out each walk in the map in turn, locate its scans from the rest, and "
        "report how far off it was, with and without the signal",
    )
    parser.add_argument(
        "--match",
        choices=["networks", "signal"],
        default="networks",
        help="what a fingerprint is matched on: which networks are in view, or also how "
        "strong they came in, which is more precise and less portable between radios "
        "(default: networks)",
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
    if 0.0 < place.fraction < 1.0:
        voice.say(f"{place.name_from} to {place.name_to}", lang=names)
        voice.say(f"{round(place.fraction * 100)} percent", lang=lang)
    else:
        voice.say(place.name_to if place.fraction >= 1.0 else place.name_from, lang=names)
    if found.uncertain:
        voice.say("Uncertain", lang=lang)


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
            buildings=args.buildings,
        )
        # Nothing resolved means nothing to write: a copy of the notebook with no
        # coordinates in it would only be a second file to keep in step.
        written = write_geocoded(args.geocode, target, result) if result.found else 0
    except (GeocodeError, NotebookError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_geocoding(result, target, written))
    if args.streets:
        try:
            shapes = write_streets(args.streets, result.drawn, result.buildings)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        blocks = f" and {len(result.buildings)} buildings" if result.buildings else ""
        print(f"{shapes} shapes into {args.streets}: the streets it asked about{blocks}")
    if not written:
        print("\nNothing was resolved, so no notebook was written.")
    return 0 if written else 1


def run_map(args: argparse.Namespace, map_file: Path) -> int:
    """The map commands: add an outing to it, look yourself up in it, or measure it."""
    by_signal = args.match == "signal"
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
        print(
            format_map_check(
                fingerprints, check_map(fingerprints), check_map(fingerprints, by_signal=True)
            )
        )
        return 0

    voice = make_voice(args.voice)
    try:
        try:
            networks = (
                scan_from_log(args.locate, say_which_walk(args.locate, args.outing))
                if args.locate
                else scan_now(args.interface)
            )
        except RadioBlocked as exc:
            print(f"Cannot scan: {exc}")
            voice.say("Radio blocked", lang=args.lang)
            return 1
        except (NotebookError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        try:
            known = read_map(map_file)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        found = locate_scan(known, networks, by_signal=by_signal)
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
                        )
                    )
                )
            print("\n\n".join(reports))
            return 0
        result = reconcile(log, notebook, by_movement=by_movement, streets=drawn, outing=walk)
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
            picture = svg_map(result, drawn)
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
            ("--key-file", args.key_file is not None),
        )
        if given
    ]
    if export_only and not args.export_public:
        parser.error(f"{', '.join(export_only)}: only meaningful together with --export-public")
    if args.streets is not None and not (args.geocode or args.reconcile or args.map_add):
        parser.error("--streets: only meaningful together with --geocode, --reconcile or --map-add")
    if args.buildings and not (args.geocode and args.streets):
        parser.error("--buildings: only meaningful together with --geocode and --streets")
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
    if args.geocode and args.area is None:
        parser.error("--geocode: --area is needed, to say which city the notebook walks")
    mapping = bool(args.map_add) or args.locate is not None or args.check_map
    # The assistant reaches the map too, so naming one is meaningful with it.
    chooses_a_map = mapping or args.assistant
    if args.pace is not None and not (args.reconcile or args.map_add):
        parser.error("--pace: only meaningful together with --reconcile or --map-add")
    map_only = [
        flag
        for flag, given in (("--map", args.map is not None), ("--match", args.match != "networks"))
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
