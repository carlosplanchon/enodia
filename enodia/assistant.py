"""A guided menu over the commands Enodia already has.

Enodia is forty-odd flags in one flat parser, and the workflow they add up to
(walk, reconcile, add to the map, locate, check) is only obvious to somebody who
already knows it. This makes that workflow visible: numbered menus, one decision
per screen, and the state of your own data on the way in.

It is not a chat and not an assistant in the modern sense. Nothing here writes
a sentence it was not given, nothing asks a model anything, and nothing leaves
the machine. Every suggestion is a rule over state that can be read off the
files, and the rules are in `suggestion` where they can be argued with.

Nor is it a second way of doing things. Every screen ends up calling the same
`run_walk`, `run_reconcile` or `run_map` the flags call, with the same arguments
object, so there is one implementation of each command and the menu is a way of
filling it in. What the menu adds is memory: it knows which outing you picked
two screens ago.

Plain prompts and no `curses`, deliberately. It works over ssh, it works on a
console that cannot draw a box, it copies and pastes, it adds no dependency, and
it is testable by handing it a list of keystrokes.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from enodia.fingerprint import Fingerprint, map_summary, read_map
from enodia.geocode import read_crossings
from enodia.netlog import outings, read_log, records_for_outing
from enodia.preflight import Check, check_battery, check_interfaces, check_lid, check_scan_mac
from enodia.reconcile import button_marks, merged_scans
from enodia.system import data_dir, map_path, session_logs

# Two crossings is the least a notebook can have and still be one: a walk
# between fewer than two corners has no stretch in it to place anything along.
LEAST_CROSSINGS = 2


def count(many: int, one: str, more: str = "") -> str:
    """`1 mark`, `4 marks`. A screen that says "1 marks" reads as a machine."""
    return f"{many} {one if many == 1 else (more or one + 's')}"


@dataclass(frozen=True)
class Outing:
    """One walk, as much as a log file can say about it on its own.

    Everything here is read off the log. What is deliberately absent is any
    notion of whether the outing has been reconciled, because Enodia records
    nothing of the sort anywhere, and a menu that claimed to know it would be
    inventing the one thing the operator is relying on it for. `on_map` is the
    knowable neighbour of that question, and it is knowable cheaply.
    """

    log: Path
    token: str
    began: datetime | None
    ended: datetime | None
    scans: int
    marks: int
    on_map: bool | None
    unreadable: bool = False

    @property
    def name(self) -> str:
        return self.token or self.log.stem

    def describe(self) -> str:
        if self.unreadable:
            return f"{self.log.name}  cannot be read"
        when = self.began.strftime("%Y-%m-%d %H:%M") if self.began else "no time in it"
        lasted = ""
        if self.began is not None and self.ended is not None:
            minutes = round((self.ended - self.began).total_seconds() / 60)
            lasted = f"  {minutes} min"
        return (
            f"{when}  {self.name}  {count(self.scans, 'scan')}  {count(self.marks, 'mark')}"
            f"{lasted}{'  on the map' if self.on_map else ''}"
        )


def known_logs(args: argparse.Namespace) -> list[Path]:
    """Every log the assistant is working over.

    `--log FILE` names one file and means it, the same file the walk would write
    to, so the menu looks at that and at nothing else. Without it, the directory
    the outings go in, newest written first. Reading the default directory while
    the walk writes somewhere else was the assistant offering to reconcile a set
    of outings that had nothing to do with the one it had just recorded.
    """
    if args.log:
        return [Path(args.log)]
    return session_logs(Path(args.dir) if args.dir else data_dir())


def map_tokens(map_file: Path) -> set[str] | None:
    """The walks a map already holds, by the token their outing name ends with.

    A map's `outing` is `"<when it began>/<token>"`, so the token after the
    slash says which walk it came from without reconciling anything. Cheap, and
    only an indication: `add_to_map` decides for real, and it decides by the
    whole name. A menu that said "already added" and was wrong would only be
    wrong in the direction of not offering something, which the operator can
    still ask for.

    None when the map is there and cannot be read, which is not the same answer
    as an empty map and must not become one: every rule that asks whether an
    outing is on the map would otherwise be answered from a map nobody read.
    """
    try:
        known = read_map(map_file)
    except OSError:
        return None
    return {one.outing.rsplit("/", 1)[-1] for one in known if one.outing}


def outing_summaries(logs: Sequence[Path], on_map: set[str] | None) -> list[Outing]:
    """Every walk in every log, newest first.

    One walk per walk and not one per file. `--log walk.jsonl` reused every week
    appends to the same pages, and reading only the last one hid every earlier
    outing from a menu whose whole job is to let you pick one. `--outing TOKEN`
    exists on the command line for exactly that case, and this is what makes it
    reachable from the menu.

    A log that cannot be read is listed as unreadable rather than left out: a
    file that is there and silent is worth seeing on the list, and dropping it
    would be the menu deciding on its own that it is not yours.
    """
    found = []
    for path in logs:
        try:
            records = read_log(path)
        except OSError:
            found.append(Outing(path, "", None, None, 0, 0, None, unreadable=True))
            continue
        for token in outings(records):
            own = records_for_outing(records, token)
            scans = merged_scans([r for r in own if r.is_scan and r.time is not None])
            stamps = [r.time for r in own if r.time is not None]
            found.append(
                Outing(
                    path,
                    token,
                    min(stamps, default=None),
                    max(stamps, default=None),
                    len(scans),
                    len(button_marks(own)),
                    None if on_map is None else (bool(token) and token in on_map),
                )
            )
    # Newest first, across the files as well as inside them: which file a walk
    # landed in is an accident of how the outings were started.
    return sorted(found, key=lambda one: (one.began is not None, one.began), reverse=True)


def notebook_candidates(beside: Path, here: Path) -> list[tuple[Path, int]]:
    """The files near a log that read as a notebook, with how many crossings each has.

    Nothing links a log to a notebook. There is no naming convention and this
    does not invent one: it offers what looks like a notebook and lets the
    operator say. `read_crossings` is the same reader `--geocode` uses, so a
    file it finds two crossings in is a file the rest of Enodia would accept.
    """
    found: dict[Path, int] = {}
    for directory in (beside, here):
        try:
            names = sorted(directory.glob("*.txt"))
        # `Path.glob` swallows the errors it meets while walking, so on this
        # Python a directory that cannot be read comes back empty rather than
        # raising. The guard is for the one that raises instead, and a menu that
        # was only listing files is not worth a traceback either way.
        except OSError:  # pragma: no cover - glob does not raise here
            continue
        for path in names:
            if path in found:
                continue
            try:
                crossings = read_crossings(path)
            except (OSError, ValueError):
                continue
            if len(crossings) >= LEAST_CROSSINGS:
                found[path] = len(crossings)
    return sorted(found.items())


def machine_checks(args: argparse.Namespace) -> list[Check]:
    """The preflight checks cheap enough to run every time a menu is drawn.

    Four of the eight are not: `check_scan` asks for a real scan and takes
    seconds, `check_voice` speaks out loud, `check_button` opens and closes
    devices, and `check_log` is not read-only, since it creates the directory it
    is asked about. Those belong on the preflight screen, where somebody asked
    for them.
    """
    return [check_interfaces(args.interface), check_lid(), check_scan_mac(), check_battery()]


def with_flags(args: argparse.Namespace, **overrides: object) -> argparse.Namespace:
    """The same arguments the command line was given, with this screen's choices on top.

    Built from the real parsed arguments and never from scratch, so that a flag
    added tomorrow is present here with its default instead of missing, and so
    that `enodia --assistant -i wlan0 --voice pico` walks on wlan0 with pico.
    The command then runs through exactly the function the flag runs through.

    Never by handing an argv back to argparse, which would be the obvious other
    way and is disqualified: `parser.error` exits the process, and a menu that
    can vanish from inside itself with a usage message is not a menu.

    A name the parser does not define is refused rather than set. Python is
    happy to hang an attribute nobody reads on a namespace, and `ty` cannot see
    through the keywords, so a typo here would be a screen that silently does
    the default thing forever.
    """
    unknown = sorted(set(overrides) - set(vars(args)))
    if unknown:
        raise KeyError(f"not a flag the parser defines: {', '.join(unknown)}")
    return argparse.Namespace(**{**vars(args), **overrides})


@dataclass
class Session:
    """What the assistant knows between one screen and the next."""

    args: argparse.Namespace
    ask: Callable[[str], str]
    say: Callable[[str], None]
    where: str
    map_file: Path
    outing: Outing | None = None
    notebook: Path | None = None

    def read(self) -> str:
        """One answer, with whatever was printed actually on the screen first.

        `print` to a pipe is buffered in blocks, so `enodia --assistant | tee
        walk.txt` would hold the whole menu back and show a prompt with nothing
        above it. One flush is the difference between a menu and a hang.
        """
        sys.stdout.flush()
        return self.ask("> ")


Screen = Callable[[Session], "Screen | None"]


def choose(
    session: Session,
    title: str,
    lines: Iterable[str] = (),
    options: Sequence[tuple[str, str]] = (),
    default: str | None = None,
    back: bool = True,
) -> str:
    """Draw a screen and return the key that was chosen.

    An unknown key asks again rather than picking something, which is the same
    rule the rest of Enodia follows about guessing. Enter takes the default when
    a screen has one, so that the whole of a walk can be got through by pressing
    it. `b` is added here rather than written out on every screen, so that it
    means the same thing and is worded the same way everywhere.
    """
    keyed = [*options, *([("b", "Back")] if back else [])]
    while True:
        session.say("")
        session.say(title)
        for line in lines:
            session.say(line)
        session.say("")
        for key, label in keyed:
            session.say(f"  {'Enter' if key == default else key:<5}  {label}")
        typed = session.read().strip().casefold()
        if not typed and default is not None:
            return default
        if typed in {key for key, _ in keyed}:
            return typed
        session.say(f"\n{typed!r} is not one of these." if typed else "\nNothing chosen.")


def ask_path(session: Session, title: str) -> Path | None:
    """A path typed in, or None when nothing was typed."""
    session.say("")
    session.say(title)
    typed = session.read().strip()
    return Path(typed).expanduser() if typed else None


# --- What the state suggests ---------------------------------------------------


@dataclass(frozen=True)
class Suggestion:
    """The step the state points at, and the screen that takes it."""

    said: str
    go: Screen


def suggestion(session: Session) -> Suggestion | None:
    """The next step, from rules over state that can actually be read.

    Every rule below is a condition over a file that exists. What is not here
    matters as much: there is no "this outing has not been reconciled", because
    nothing records that, and no "this notebook belongs to this log", because
    nothing links them. The honest neighbour of the first is "this outing is not
    on the map", and it is what the third rule says.
    """
    known = read_map_quietly(session.map_file)
    walks = outing_summaries(known_logs(session.args), map_tokens(session.map_file))
    usable = [one for one in walks if not one.unreadable and one.scans]
    if not usable:
        return Suggestion(
            "Walk a couple of blocks, and the rest of this has something to work on.", capture
        )
    newest = usable[0]
    if newest.marks and not notebook_candidates(newest.log.parent, Path.cwd()):
        return Suggestion(
            f"Transcribe the notebook for {newest.name}: it has "
            f"{count(newest.marks, 'mark')} and there is no notebook next to it.",
            outings_list,
        )
    if known is None:
        # Every rule left turns on what the map holds, and the map is there and
        # shut. Saying nothing beats working an answer out from a file nobody
        # managed to open.
        return None
    waiting = [one for one in usable if one.on_map is False]
    if waiting and notebook_candidates(waiting[0].log.parent, Path.cwd()):
        return Suggestion(f"Add {waiting[0].name} to the map. It is not on it yet.", map_add)
    counted = map_summary(known)
    if len(counted.outings) == 1:
        return Suggestion(
            "Walk the same blocks again on another day. A map of one outing finds that "
            "outing and proves nothing, and a second one is what makes the check mean "
            "something.",
            capture,
        )
    if len(counted.outings) > 1:
        return Suggestion("Measure the map: hold a walk out and locate it from the rest.", validate)
    return None


def read_map_quietly(map_file: Path) -> list[Fingerprint] | None:
    """The map, for a screen that is only counting, or None when it cannot be read.

    None and not an empty list. A map that cannot be opened is not a map with
    nothing in it, and every suggestion below turns on how much is in it, so
    reading the one as the other would hand out advice worked out from a file
    nobody managed to open. The commands settled this a while ago and the menu
    has to settle it the same way.
    """
    try:
        return read_map(map_file)
    except OSError:
        return None


# --- The screens ---------------------------------------------------------------


def main_menu(session: Session) -> Screen | None:
    lines = ["", "This machine"]
    for check in machine_checks(session.args):
        lines.append(f"  {'[' + check.status.lower() + ']':<7}{check.name:<11} {check.detail}")
    options = [
        ("1", "Start a new outing"),
        ("2", "Carry on with the outing under way"),
        ("3", "Reconcile an outing"),
        ("4", "Add an outing to the map"),
        ("5", "Locate: where am I?"),
        ("6", "Measure the map"),
        ("7", "Look at the outings"),
        ("8", "Preflight, all eight checks"),
        ("q", "Quit"),
    ]
    step = suggestion(session)
    if step is not None:
        lines += ["", "Suggested next step", f"  {step.said}"]
        options.insert(0, ("", "take it"))
    chosen = choose(session, "ENODIA", lines, options, default="" if step else None, back=False)
    if chosen == "" and step is not None:
        return step.go
    return {
        "1": capture,
        "2": carry_on,
        "3": pick_outing(pick_notebook(reconcile_menu, main_menu), main_menu),
        "4": map_add,
        "5": locate_menu,
        "6": validate,
        "7": outings_list,
        "8": preflight_screen,
        "q": None,
    }[chosen]


def capture(session: Session) -> Screen | None:
    return walk(session, resume=False)


def carry_on(session: Session) -> Screen | None:
    return walk(session, resume=True)


def walk(session: Session, resume: bool) -> Screen | None:
    """Run the outing itself, and come back here when it stops.

    Ctrl+C ends the walk and not the program: `run_walk` catches it, closes the
    monitor and returns, which is the whole reason that handler covers the
    function rather than only its loop.
    """
    from enodia.cli import run_walk, walk_log_path

    walking = with_flags(session.args, resume=resume, cycles=0)
    # The path is settled here and handed to the walk, so that afterwards the
    # outing offered is the one this walk wrote and not whatever file happens to
    # be newest. A walk that ends before its first record, a radio that was gone
    # all along, a directory that could not be written: each of those used to
    # come back with the previous outing on an OUTING COMPLETE screen, which is
    # a walk being reported that nobody took.
    wrote = walk_log_path(walking)
    # Which walks the file held before this one went out. `--log FILE` reused
    # means the file can be full of history, so "the newest walk in it" is not
    # the same question as "the walk this run just made", and answering the
    # first put somebody else's outing on an OUTING COMPLETE screen.
    before = {one.token for one in outing_summaries([wrote], None)}
    session.say("")
    session.say("Close the laptop, put it in the bag, and walk. Ctrl+C when you are home.")
    session.say("")
    run_walk(walking, wrote)
    after = [
        one for one in outing_summaries([wrote], map_tokens(session.map_file)) if not one.unreadable
    ]
    # A new outing writes a token nothing has seen before. A resumed one carries
    # the token that was already there, so nothing is new and the walk that was
    # carried on is the newest in the file.
    mine = [one for one in after if one.token not in before] or (after[:1] if resume else [])
    usable = [one for one in mine if one.scans]
    if not usable:
        session.say("")
        if mine:
            session.say(f"Nothing usable was recorded for {mine[0].name}.")
        else:
            session.say(f"Nothing was recorded in {wrote}.")
        return main_menu
    session.outing = usable[0]
    return outing_complete


def outing_complete(session: Session) -> Screen | None:
    one = session.outing
    if one is None:  # pragma: no cover - only reached through `walk`, which sets it
        return main_menu
    lines = ["", f"  {one.describe()}", f"  {one.log}"]
    if one.marks:
        lines.append(
            f"  Write the {count(one.marks, 'crossing')} down, one per line, in their order."
        )
    chosen = choose(
        session,
        "OUTING COMPLETE",
        lines,
        [("", "Reconcile it"), ("1", "Add it to the map"), ("m", "Main menu")],
        default="",
        back=False,
    )
    if chosen == "":
        return pick_notebook(reconcile_menu, main_menu)
    if chosen == "1":
        return pick_notebook(map_add_run, main_menu)
    return main_menu


def pick_outing(after: Screen, back: Screen) -> Screen:
    """Choose which walk to work on, then go on to `after`.

    Every walk of every log the assistant is working over, and not only the
    newest of each file, which is what makes `--outing TOKEN` reachable without
    typing it.
    """

    def screen(session: Session) -> Screen | None:
        walks = outing_summaries(known_logs(session.args), map_tokens(session.map_file))
        if not walks:
            choose(session, "NO OUTINGS", [f"  Nothing in {session.where}."], [], back=True)
            return back
        keys = [(str(index + 1), one.describe()) for index, one in enumerate(walks)]
        chosen = choose(session, "WHICH OUTING", [], keys, default="1")
        if chosen == "b":
            return back
        session.outing = walks[int(chosen) - 1]
        session.notebook = None
        return after

    return screen


def pick_notebook(after: Screen, back: Screen) -> Screen:
    """Choose the notebook, from what looks like one, or from a path typed in."""

    def screen(session: Session) -> Screen | None:
        one = session.outing
        if one is None:  # pragma: no cover - every caller picks an outing first
            return back
        found = notebook_candidates(one.log.parent, Path.cwd())
        lines = ["", "  Nothing links a log to a notebook, so these are only what look like one."]
        keys = [
            (str(index + 1), f"{path}  {count(crossings, 'crossing')}")
            for index, (path, crossings) in enumerate(found)
        ]
        keys.append(("p", "Type a path"))
        chosen = choose(
            session, f"NOTEBOOK FOR {one.name}", lines, keys, default="1" if found else "p"
        )
        if chosen == "b":
            return back
        if chosen == "p":
            typed = ask_path(session, "Where is the notebook?")
            if typed is None:
                return screen
            session.notebook = typed
        else:
            session.notebook = found[int(chosen) - 1][0]
        return after

    return screen


def reconcile_menu(session: Session) -> Screen | None:
    from enodia.cli import run_reconcile

    one, notebook = session.outing, session.notebook
    if one is None or notebook is None:  # pragma: no cover - both are set on the way here
        return main_menu
    pair = [str(one.log), str(notebook)]
    lines = ["", f"  Outing    {one.name}", f"  Notebook  {notebook}"]
    chosen = choose(
        session,
        "RECONCILE",
        lines,
        [
            ("", "The report"),
            ("1", "The report, with every scan placed"),
            ("2", "Does reading the pace beat the clock?"),
            ("3", "One block walked twice: do the passes agree?"),
            ("4", "Add it to the map"),
            ("m", "Main menu"),
        ],
        default="",
        back=False,
    )
    if chosen == "m":
        return main_menu
    if chosen == "4":
        return map_add_run
    run_reconcile(
        with_flags(
            session.args,
            reconcile=pair,
            # Which walk of the file, since the menu lists them all and a file
            # can hold several. This is what `--outing TOKEN` is on the command
            # line, reached by having picked the walk two screens ago.
            outing=one.token,
            scans=chosen == "1",
            check_pace=chosen == "2",
            check_passes=chosen == "3",
        )
    )
    return reconcile_menu


def map_add(session: Session) -> Screen | None:
    return pick_outing(pick_notebook(map_add_run, main_menu), main_menu)(session)


def map_add_run(session: Session) -> Screen | None:
    from enodia.cli import run_map

    one, notebook = session.outing, session.notebook
    if one is None or notebook is None:  # pragma: no cover - both are set on the way here
        return main_menu
    run_map(
        with_flags(
            session.args,
            map_add=[str(one.log), str(notebook)],
            outing=one.token,
            locate=None,
            check_map=False,
        ),
        session.map_file,
    )
    choose(session, "", [], [("m", "Main menu")], default="m", back=False)
    return main_menu


def locate_menu(session: Session) -> Screen | None:
    from enodia.cli import run_map

    known = read_map_quietly(session.map_file)
    lines = ["", f"  {session.map_file}"]
    if known is None:
        # Not counted as empty. The lookup itself will fail and say why, and a
        # count worked out from a file nobody opened would be a number invented
        # to fill the line.
        lines.append("  This map is there and cannot be read, so what it holds is not known.")
    else:
        lines.insert(1, f"  {map_summary(known).describe()}")
        if not known:
            lines.append("  An empty map places nothing. Add an outing to it first.")
    chosen = choose(
        session,
        "LOCATE",
        lines,
        [("", "Scan now"), ("1", "From an outing's last scan"), ("m", "Main menu")],
        default="",
        back=False,
    )
    if chosen == "m":
        return main_menu
    if chosen == "1":
        # The same picker the other commands use, and for the same reason: a
        # file can hold several walks, and "the last scan of that log" would
        # quietly mean the last walk in it rather than the one you meant.
        return pick_outing(locate_from_outing, locate_menu)
    run_map(
        with_flags(session.args, locate="", outing=None, map_add=None, check_map=False),
        session.map_file,
    )
    return locate_menu


def locate_from_outing(session: Session) -> Screen | None:
    from enodia.cli import run_map

    one = session.outing
    if one is None:  # pragma: no cover - the picker sets it on the way here
        return locate_menu
    run_map(
        with_flags(
            session.args,
            locate=str(one.log),
            outing=one.token,
            map_add=None,
            check_map=False,
        ),
        session.map_file,
    )
    return locate_menu


def validate(session: Session) -> Screen | None:
    from enodia.cli import run_map

    known = read_map_quietly(session.map_file)
    if known is None:
        choose(
            session,
            "THE MAP CANNOT BE READ",
            [
                "",
                f"  {session.map_file} is there and will not open.",
                "  Whether it is worth anything is not a question this can answer about a",
                "  file it could not read, and answering it from an empty one would be an",
                "  invented result rather than a missing one.",
            ],
            [("m", "Main menu")],
            default="m",
            back=False,
        )
        return main_menu
    counted = map_summary(known)
    if len(counted.outings) < LEAST_CROSSINGS:
        # The same argument the README makes: a map of one walk recognises that
        # walk. There is nothing to hold out and so nothing to measure, and
        # saying so beats printing a number that would only flatter it.
        chosen = choose(
            session,
            "NOTHING TO MEASURE YET",
            [
                "",
                f"  {counted.describe()}",
                "  Holding a walk out of a map that has only that walk leaves nothing to",
                "  locate it from. Walk the same blocks again on another day, add that",
                "  outing too, and this becomes a real question.",
            ],
            [("1", "Start that outing now"), ("m", "Main menu")],
            default="m",
            back=False,
        )
        return capture if chosen == "1" else main_menu
    run_map(with_flags(session.args, check_map=True, map_add=None, locate=None), session.map_file)
    choose(session, "", [], [("m", "Main menu")], default="m", back=False)
    return main_menu


def outings_list(session: Session) -> Screen | None:
    walks = outing_summaries(known_logs(session.args), map_tokens(session.map_file))
    lines = ["", f"  {session.where}"]
    lines += [f"  {one.describe()}" for one in walks] or ["  Nothing here yet."]
    lines += [
        "",
        "  Whether an outing has been reconciled is not written down anywhere, so it",
        "  is not shown. Whether it is on the map is, and it is.",
    ]
    choose(session, "OUTINGS", lines, [("m", "Main menu")], default="m", back=False)
    return main_menu


def preflight_screen(session: Session) -> Screen | None:
    from enodia.cli import run_preflight_command

    session.say("")
    run_preflight_command(session.args)
    choose(session, "", [], [("m", "Main menu")], default="m", back=False)
    return main_menu


# --- Running it ----------------------------------------------------------------


def run_assistant(
    args: argparse.Namespace,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] = print,
    isatty: Callable[[], bool] | None = None,
) -> int:
    """The menu, until it is left.

    `ask`, `say` and `isatty` are the whole seam: nothing else in this module
    touches the terminal, so a test hands it a list of keystrokes and reads what
    it printed. `ask` resolves to `input` here and not in the signature, because
    a default is bound when the function is defined: named there, the builtin
    would be captured once and the test suite's guard against a stray question
    would have nothing to stand in front of.
    """
    asking = ask if ask is not None else input
    reading = isatty if isatty is not None else sys.stdin.isatty
    if not reading():
        say(
            "--assistant asks questions, and there is no terminal here to ask on. "
            "Run it from a terminal, or use the flags directly: enodia --help."
        )
        return 2
    session = Session(
        args=args,
        ask=asking,
        say=say,
        where=str(args.log or (args.dir or data_dir())),
        map_file=Path(args.map) if args.map else map_path(),
    )
    screen: Screen | None = main_menu
    while screen is not None:
        try:
            screen = screen(session)
        except KeyboardInterrupt:
            # Ctrl+C at a prompt means "not this", not "kill the program". A
            # walk handles its own, so this is only ever a question being
            # abandoned, and the top of the menu is where that lands.
            say("")
            screen = main_menu
        except EOFError:
            # Ctrl+D, or the input running out. Nothing more will be typed.
            say("")
            return 0
    return 0
