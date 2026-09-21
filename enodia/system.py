"""The machine Enodia runs on: where its logs go, what the battery holds, what the lid does.

Everything here reads files the kernel and systemd publish -- sysfs, logind's
configuration -- with the roots as parameters, so that tests hand in a
directory of their own and never look at the machine they run on.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SESSION_GAP = timedelta(minutes=30)
MAP_FILE = "map.jsonl"
POWER_SUPPLY = Path("/sys/class/power_supply")
LOGIND_CONF = Path("/etc/systemd/logind.conf")
# systemd's own search order, highest priority first (src/basic/constants.h).
# /usr/local/lib was missing here and is a real place distributions use.
LOGIND_DROPINS = (
    Path("/etc/systemd/logind.conf.d"),
    Path("/run/systemd/logind.conf.d"),
    Path("/usr/local/lib/systemd/logind.conf.d"),
    Path("/usr/lib/systemd/logind.conf.d"),
)
SECTION = re.compile(r"^\s*\[([^]]*)\]")
LID_SETTING = re.compile(r"^\s*HandleLidSwitch\s*=\s*(?P<value>\S+)", re.IGNORECASE)
# NetworkManager's own order, and not systemd's: /usr/lib first, then /run,
# then the main file, then /etc, then the internal file it writes itself, with
# the later ones winning. Reusing the logind rule here read it upside down.
NM_CONF = Path("/etc/NetworkManager/NetworkManager.conf")
NM_BEFORE = (Path("/usr/lib/NetworkManager/conf.d"), Path("/run/NetworkManager/conf.d"))
NM_AFTER = (Path("/etc/NetworkManager/conf.d"),)
NM_INTERN = Path("/var/lib/NetworkManager/NetworkManager-intern.conf")
IWD_CONF = Path("/etc/iwd/main.conf")
# Matched wherever they appear rather than inside a named section, the same way
# the lid setting is: a drop-in can put the key under more than one heading and
# the question here is only whether somebody turned it off.
NM_SCAN_MAC = re.compile(r"^\s*wifi\.scan-rand-mac-address\s*=\s*(?P<value>\S+)", re.IGNORECASE)
# A mask fixes some bits of the generated address and leaves the rest to be
# randomised, so with one set "randomised" no longer means the whole address.
NM_SCAN_MASK = re.compile(
    r"^\s*wifi\.scan-generate-mac-address-mask\s*=\s*(?P<value>\S+)", re.IGNORECASE
)
IWD_SCAN_MAC = re.compile(r"^\s*AddressRandomization\s*=\s*(?P<value>\S+)", re.IGNORECASE)
OFF = ("no", "false", "0", "off", "disabled")
# NetworkManager's booleans, and the reason there are two lists rather than
# one. Reading anything that is not an "off" as an "on" turned
# `wifi.scan-rand-mac-address=banana` into a green line saying the scans are
# randomised, which is the one answer this check exists not to give.
ON = ("yes", "true", "1", "on")
# `[.config] enable=false` makes NetworkManager skip a file whole. It also
# takes predicates (`nm-version-min:1.2`, `env:TAG`, `except:`), and which way
# those fall depends on the running daemon and its environment, neither of
# which is read here, so a file carrying one is reported as unread rather than
# guessed at. The main NetworkManager.conf cannot be disabled this way.
NM_ENABLE = re.compile(r"^\s*enable\s*=\s*(?P<value>\S+)", re.IGNORECASE)


def data_dir(environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """Where the logs go: `$XDG_DATA_HOME/enodia`, or `~/.local/share/enodia`."""
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_DATA_HOME")
    root = Path(base) if base else (home or Path.home()) / ".local" / "share"
    return root / "enodia"


def config_dir(environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """Where what Enodia is told goes: `$XDG_CONFIG_HOME/enodia`, or `~/.config/enodia`.

    Apart from the data directory, which is where the walks go. The one thing
    that lives here is the key `--export-public` pseudonymises with, and a
    secret has no business sitting among the outings: `session_log_path` picks
    the newest file in the data directory to resume from, and a directory Enodia
    writes logs into is not the place to leave something that must not be
    published by accident.
    """
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else (home or Path.home()) / ".config"
    return root / "enodia"


def map_path(environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """Where the fingerprint map lives: `<data dir>/map/map.jsonl`.

    In a directory of its own, and not beside the logs, which is not tidiness.
    `session_log_path` resumes the newest `*.jsonl` sitting in the data
    directory, so a map file there would sooner or later be taken for an
    outing's log and have scan records appended to it, wrecking both.
    """
    return data_dir(environ, home) / "map" / MAP_FILE


def session_logs(directory: Path) -> list[Path]:
    """The logs in a directory, the most recently written first, creating nothing.

    `session_log_path` needs this list and makes the directory on its way to it,
    which is right when an outing is about to be written into it and wrong when
    something only wants to look. A directory that is not there holds no logs,
    and so does one that cannot be read: neither is worth a traceback out of a
    menu that was only drawing a list.

    One entry failing is one entry lost and not the listing. A link to a log
    that was moved away cannot be asked when it was written, and answering that
    by handing back nothing turned a directory with one broken name in it into
    an empty one, hiding every outing the operator actually has.
    """
    try:
        names = sorted(directory.glob("*.jsonl"))
    except OSError:  # pragma: no cover - glob does not raise here
        return []
    written = []
    for path in names:
        try:
            written.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return [path for _, path in sorted(written, reverse=True)]


def session_log_path(
    directory: Path,
    now: datetime | None = None,
    gap: timedelta = SESSION_GAP,
    resume: bool = False,
) -> tuple[Path, bool]:
    """The log for this outing, and whether it continues one already under way.

    One outing, one file: a log, a notebook, one reconciliation, and the
    button's marks start from one every time. Every call is a new outing, with
    a fresh file named by the time it starts, unless `resume` asks to carry on:
    then the newest log in `directory` is this outing's if it was written to
    less than `gap` ago, a restart after a crash or a battery swap, and a new
    file it is otherwise.
    """
    now = now if now is not None else datetime.now().astimezone()
    directory.mkdir(parents=True, exist_ok=True)
    logs = session_logs(directory)
    if logs and resume:
        last_written = datetime.fromtimestamp(logs[0].stat().st_mtime).astimezone()
        # A file that says it was written in the future is a clock that moved or
        # an mtime that came off another machine, and a negative age is under
        # every gap there is. Whatever it is, it is not an outing interrupted
        # thirty minutes ago, and continuing one on that basis puts a new walk
        # into somebody else's file.
        if timedelta(0) <= now - last_written < gap:
            return logs[0], True
    # Two outings started inside one second would otherwise be handed the same
    # name, and the second would append to the first: one file holding two
    # walks, with the marks of both numbered from one.
    stem = now.strftime("%Y-%m-%dT%H-%M-%S")
    path = directory / f"{stem}.jsonl"
    twice = 2
    while path.exists():
        path = directory / f"{stem}-{twice}.jsonl"
        twice += 1
    return path, False


class Unreadable(str):
    """A file that is there and could not be read, standing in for its contents.

    Not the same answer as a file that is not there, and the preflight used to
    give both the same one. A drop-in in `/etc` that this cannot open is a file
    logind reads perfectly well with its own privileges, so skipping it and
    reporting what the vendor's copy said is a green line about a configuration
    that is not the machine's. It is a `str` so that every reader can go on
    finding nothing in it, and the ones that report to a person ask whether the
    file was readable before calling the answer settled.
    """

    __slots__ = ()


def _read(path: Path) -> str | None:
    """A file's contents, None when it is not there, `Unreadable` when it is.

    An unreadable file is empty to every parser here, which is what stops a
    permission error from becoming a traceback, and it stays distinguishable to
    anything that has to say how sure it is.
    """
    try:
        return path.read_text(errors="replace").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return Unreadable()


def battery(root: Path = POWER_SUPPLY) -> tuple[int, str] | None:
    """Charge of the first battery, 0-100, and its status ("Discharging", "Charging", "Full");
    None when the machine has no battery the kernel reports."""
    for supply in sorted(root.glob("*")):
        if _read(supply / "type") != "Battery":
            continue
        capacity = _read(supply / "capacity")
        if capacity is None or not capacity.isdigit():
            continue
        return int(capacity), _read(supply / "status") or "Unknown"
    return None


def unreadable(files: Sequence[Path]) -> tuple[Path, ...]:
    """The files of a search that are there and cannot be read."""
    return tuple(path for path in files if isinstance(_read(path), Unreadable))


def _last_setting(
    files: Sequence[Path], pattern: re.Pattern[str], section: str | None = None
) -> str | None:
    """The last time a key is set across a main file and its drop-ins, in order.

    `section` restricts the search to one `[Heading]`, because a key outside the
    heading it belongs to is not configuration, it is a line that looks like it.
    """
    setting = None
    for path in files:
        text = _read(path)
        if text is None:
            continue
        inside = section is None
        for line in text.splitlines():
            heading = SECTION.match(line)
            if heading is not None and section is not None:
                inside = heading.group(1).strip().casefold() == section.casefold()
                continue
            found = pattern.match(line) if inside else None
            if found:
                setting = found.group("value")
    return setting


def dropin_files(main: Path, directories: Sequence[Path]) -> list[Path]:
    """The files systemd would read, in the order it would read them.

    Not simply every directory in turn. systemd collects the drop-ins from all
    of them keyed by filename, keeps the copy from the highest-priority
    directory for each name, and then reads what is left **sorted by filename**,
    the last one winning. Walking the directories one after another instead lets
    `/usr/lib/10-vendor.conf` overwrite `/etc/90-local.conf`, which is backwards:
    the administrator's file is the one that is supposed to win, and here that is
    the difference between reporting that the lid is safe and the laptop
    suspending inside the backpack.
    """
    winners: dict[str, Path] = {}
    for directory in directories:
        for path in sorted(directory.glob("*.conf")):
            winners.setdefault(path.name, path)
    return [main, *(winners[name] for name in sorted(winners))]


def networkmanager_files(
    conf: Path = NM_CONF,
    before: tuple[Path, ...] = NM_BEFORE,
    after: tuple[Path, ...] = NM_AFTER,
    intern: Path = NM_INTERN,
) -> list[Path]:
    """The files NetworkManager reads, in the order it reads them, last winning.

    With the shadowing, which is a separate rule from the ordering and does not
    fall out of it. A `10-wifi.conf` in `/etc/NetworkManager/conf.d` hides the
    `10-wifi.conf` in `/usr/lib` **entirely**, so a key the vendor's copy sets
    and the administrator's does not is simply not applied. Reading both and
    letting the later win looks the same until the two files set different keys,
    and then it reports a setting nothing is actually using.
    """
    # Highest priority first, so the first sighting of a name is the survivor:
    # /etc, then /run, then /usr/lib.
    survives: dict[str, Path] = {}
    for directory in (*reversed(after), *reversed(before)):
        for path in sorted(directory.glob("*.conf")):
            survives.setdefault(path.name, path)

    def kept(directory: Path) -> list[Path]:
        return [
            path for path in sorted(directory.glob("*.conf")) if survives.get(path.name) == path
        ]

    files: list[Path] = []
    for directory in before:
        files.extend(kept(directory))
    files.append(conf)
    for directory in after:
        files.extend(kept(directory))
    files.append(intern)
    return files


def nm_file_enabled(path: Path) -> bool | None:
    """Whether NetworkManager loads this file: True, False, or None for cannot tell.

    A snippet with `[.config] enable=false` is skipped whole, so reading it and
    letting its keys win reports a setting nothing is using. `enable` also takes
    predicates about the daemon's version and environment, and those are not
    evaluated here, so such a file answers None and the caller says it could not
    tell rather than picking an answer.
    """
    text = _read(path)
    if text is None:
        return True
    section = ""
    for line in text.splitlines():
        heading = SECTION.match(line)
        if heading is not None:
            section = heading.group(1).strip().casefold()
            continue
        if section != ".config":
            continue
        setting = NM_ENABLE.match(line)
        if setting is not None:
            value = setting.group("value").casefold()
            if value in ON:
                return True
            if value in OFF:
                return False
            return None
    return True


def _by_section(files: Sequence[Path], pattern: re.Pattern[str]) -> dict[str, str]:
    """The last value each section sets, across the files in the order given."""
    found: dict[str, str] = {}
    for path in files:
        text = _read(path)
        if text is None:
            continue
        section = ""
        for line in text.splitlines():
            heading = SECTION.match(line)
            if heading is not None:
                section = heading.group(1).strip().casefold()
                continue
            setting = pattern.match(line)
            if setting is not None:
                found[section] = setting.group("value")
    return found


@dataclass(frozen=True)
class ScanMac:
    """What the daemons were told about the address a scan goes out under."""

    devices: dict[str, str]
    """`wifi.scan-rand-mac-address` by device section, which is where it counts."""
    elsewhere: dict[str, str]
    """The same key in a section NetworkManager does not read device properties from."""
    masks: dict[str, str]
    """`wifi.scan-generate-mac-address-mask` by device section, when one is set."""
    iwd: str | None
    """iwd's `AddressRandomization`, which is about the interface address."""
    unsure: tuple[tuple[Path, str], ...]
    """Files whose part in the answer could not be established, and why."""


def _for_devices(sections: dict[str, str]) -> dict[str, str]:
    """Only the sections NetworkManager reads device properties from.

    `wifi.scan-rand-mac-address` is a device property, so `[device]` and the
    `[device-*]` sections that match by device. The same line under `[main]`
    parses exactly as well and does nothing, and reporting it as the machine's
    setting turned a mistake into a green line saying the scans are randomised.
    """
    return {
        name: value
        for name, value in sections.items()
        if name == "device" or name.startswith("device-")
    }


def _elsewhere(sections: dict[str, str]) -> dict[str, str]:
    """The sections that are not device sections, to be reported as doing nothing."""
    return {
        name: value
        for name, value in sections.items()
        if not (name == "device" or name.startswith("device-"))
    }


def scan_mac_setting(
    nm_conf: Path = NM_CONF,
    nm_before: tuple[Path, ...] = NM_BEFORE,
    nm_after: tuple[Path, ...] = NM_AFTER,
    nm_intern: Path = NM_INTERN,
    iwd_conf: Path = IWD_CONF,
) -> ScanMac:
    """What NetworkManager and iwd say about the MAC a scan goes out under.

    A scan is not only listening. Asking the daemon for a fresh one makes the
    card send probe requests, and a probe request carries the sender's MAC, so
    walking a neighbourhood scanning every few seconds lays down a trail under
    whatever address the card is using. Randomising it is the difference between
    a trail and a series of unrelated sightings.

    Nothing here changes anything: that needs root, it fights the daemon that
    owns the interface, and both daemons already have a supported way to do it
    that survives reconnects. This only reads what they were told, which is what
    the operator wants to know before the zip closes rather than after.

    NetworkManager's half comes back keyed by the section that set it, because
    `[device-wlan0]` and `[device-wlan1]` can say different things through
    `match-device` and then there is no single answer for the machine. Working
    out which one applies to a given card means implementing NetworkManager's
    device matching, so instead the disagreement is handed up and reported.

    The two daemons do not mean the same thing. NetworkManager's
    `wifi.scan-rand-mac-address` is about scanning specifically, and its default
    is a random locally administered address. iwd's `AddressRandomization` is
    documented as the address the interface uses, which is a related question
    and not the same one, so it is handed back separately rather than folded
    into a single verdict about scanning.
    """
    files = networkmanager_files(nm_conf, nm_before, nm_after, nm_intern)
    # The main file cannot be disabled, so its `[.config]` is not consulted.
    read: list[Path] = []
    unsure: list[tuple[Path, str]] = []
    for path in files:
        enabled = True if path == nm_conf else nm_file_enabled(path)
        if enabled is None:
            unsure.append((path, "a [.config] enable= this cannot evaluate"))
        elif enabled:
            read.append(path)
    unsure.extend((path, "no permission to read it") for path in unreadable(read))
    unsure.extend((path, "no permission to read it") for path in unreadable([iwd_conf]))
    return ScanMac(
        _for_devices(_by_section(read, NM_SCAN_MAC)),
        _elsewhere(_by_section(read, NM_SCAN_MAC)),
        _for_devices(_by_section(read, NM_SCAN_MASK)),
        _last_setting([iwd_conf], IWD_SCAN_MAC),
        tuple(unsure),
    )


def lid_switch_setting(
    conf: Path = LOGIND_CONF,
    dropins: tuple[Path, ...] = LOGIND_DROPINS,
) -> tuple[str | None, tuple[Path, ...]]:
    """`HandleLidSwitch` as logind will read it, or None when nothing sets it.

    Read the way systemd reads it: the main file, then the drop-ins from every
    search directory collected by filename and sorted by it, with the highest
    priority copy of each name kept, the last one winning. Only `[Login]`
    counts. None means systemd's own default applies, which is `suspend`: the
    walk ends when the zip closes. A desktop environment may take the lid
    switch over and ignore all of this, which nothing here can see.
    """
    files = dropin_files(conf, dropins)
    return _last_setting(files, LID_SETTING, section="Login"), unreadable(files)
