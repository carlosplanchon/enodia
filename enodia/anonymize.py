"""A copy of an outing that can be published, and what that word cannot promise.

The README has asked for a long time for one real walk with its notebook, so
that the numbers it shows come from a street instead of from a fixture. Doing
that by hand means publishing the Wi-Fi landscape of a neighbourhood: the log
carries the hardware addresses and the chosen names of everybody's routers, and
the notebook carries the corners somebody walked and the minute they passed
each one.

So this is an export layer and nothing else. It never touches the files Enodia
works from. Inside, the real BSSIDs and SSIDs are the identity of the
observations and they stay; the substitution happens on the way out, when it is
asked for by name.

Two things it is careful about, because they are the ones that would make it a
lie rather than a feature.

It is **pseudonymization**. A stable identifier stays a stable identifier: if
`ap-1c8a74f992ae` appears eighty times it is still one thing appearing eighty times,
which is exactly what makes the file useful and exactly what stops the word
"anonymous" being true of it. And a radio fingerprint locates itself: the set of
access points at a corner *is* that corner's identity, which is the whole
mechanism `locate` runs on, so anybody who walks the same streets with their own
scanner can join their real addresses back onto this structure. Substituting
names does not change that and is not claimed to.

The second is what gets substituted. A network whose name is removed and whose
address is replaced is still the same row of numbers; a **crossing** whose name
survives is an address in plain text. The log and the notebook are exported
together, with one set of pseudonyms across both, because a pseudonymized log
beside a real notebook still says where somebody was walking and when.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import cos, degrees, radians
from pathlib import Path
from typing import Any

from enodia.geocode import corner_streets
from enodia.netlog import (
    BUTTON_LOST_EVENT,
    CONNECTED_EVENT,
    DISCONNECTED_EVENT,
    MARK_EVENT,
    NEW_EVENT,
    SCAN_EVENT,
    SCAN_FAILED_EVENT,
    SUSPENDED_EVENT,
    LogRecord,
    SeenNetwork,
    outings,
    read_log_counting,
    records_for_outing,
)
from enodia.reconcile import (
    DATE_DIRECTIVE,
    MARKED_LINE,
    NOTEBOOK_LINE,
    Waypoint,
    button_marks,
    folded,
    read_notebook,
    strip_comment,
)
from enodia.streets import EARTH_RADIUS_M, distance_metres
from enodia.system import config_dir

KEY_FILE = "export.key"
KEY_BYTES = 32
# A date nobody will mistake for the day somebody walked, and one Enodia can
# still read back: the exported outing has to reconcile, or it is an example of
# nothing.
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Somewhere in the Gulf of Guinea, which is where null island is and where
# nobody's neighbourhood is. The walk is laid out from here in metres.
ORIGIN = (0.0, 0.0)
# The association records name the operator's own network rather than a
# neighbour's, which makes them the line that ties a walk to the walker. Nothing
# in a reconciliation reads them, so they are left out rather than dressed up.
LEFT_OUT = (CONNECTED_EVENT, DISCONNECTED_EVENT)
# What goes in the place of a string whose value is not one this knows.
WITHHELD = "withheld by --export-public"
# The only reasons Enodia writes in its own words. Everything else in that field
# is `str(exc)` straight from the Wi-Fi daemon, and a D-Bus message carries
# object paths, device names and now and then an SSID. Allowed by exact match
# rather than sanitised by event, so a record kind added later cannot bring
# arbitrary text through a door that was only ever checked for one event.
PLAIN_REASONS = (
    "radio soft blocked (rfkill)",
    "radio hard blocked (rfkill)",
    "SYN_DROPPED",
)
# The events this knows how to publish: every event Enodia writes except the two
# associations, and nothing else. Naming fields off a type stops a field nobody
# named from leaving, and does nothing about a field whose type is `str` and
# whose value came out of a file somebody could have edited. `event` is the
# plainest case: the value is Enodia's own vocabulary, so one outside it is not
# a record of a walk and does not go out as one.
PUBLIC_EVENTS = (
    SCAN_EVENT,
    NEW_EVENT,
    SCAN_FAILED_EVENT,
    SUSPENDED_EVENT,
    MARK_EVENT,
    BUTTON_LOST_EVENT,
)
# And the same for the other free string that crosses. These are every label
# `ifpeek` produces, read from its own source: NetworkManager answers open,
# 8021x, psk or secured, wpa_supplicant those and wep, and iwd's word for the
# network type is passed through as it comes. That last one is why an unknown
# label is withheld and counted rather than refused: one this has not met is far
# likelier a backend saying something new than an attack, and the report gives
# the number so the loss is never silent.
PLAIN_SECURITY = ("open", "wep", "psk", "8021x", "secured")
# What the notebook is called inside an export. The log's name carries the
# artificial date, so that a directory of two files says which walk it is not.
NOTEBOOK_NAME = "notebook.txt"
# The kinds whose spelling is not their identity, which is every kind that came
# out of a notebook somebody wrote by hand.
FOLDED_KINDS = ("street", "place")
# The two events that carry a list of networks, and the only ones where an empty
# list means something. `netlog` writes them for exactly these, and an export
# that wrote the field on a mark or a suspend would be inventing a reading.
CARRY_NETWORKS = (SCAN_EVENT, NEW_EVENT)
# The fields of the source files that carry a name or an address somebody chose,
# looked for again in what is about to be written. The rest of a record is
# numbers, and a number going out unchanged is the point of the exercise.
IDENTIFYING = ("bssid", "ssid", "interface", "reason")
# The two fields published as they stand when the value is one this knows. Only
# the values outside the domain are looked for: a known one is in the export on
# purpose, and searching for it would report `open` forty times on every walk.
# `LEFT_OUT` counts as known here, because an association is a record this drops
# deliberately, and `connected` is also the name of a field on every network.
BOUNDED = (("event", PUBLIC_EVENTS + LEFT_OUT), ("security", PLAIN_SECURITY))
# `strip_comment` keeps a leading `#7 ` because it names a button mark rather
# than starting a comment. The same two cases, from the other side: this wants
# the prose that one throws away.
MARK_PREFIX = re.compile(r"^#\d+\s")


class ExportError(ValueError):
    """The export cannot be made, and says why rather than writing half of one."""


def key_path(environ: Any = None, home: Path | None = None) -> Path:
    """Where the export key lives, unless one is named on the command line."""
    return config_dir(environ, home) / KEY_FILE


def read_key(path: Path) -> tuple[bytes, str | None]:
    """The export key, made on first use, and a word about it if one is owed.

    Made once and kept, so that a walk exported this month and one exported next
    year use the same pseudonyms, which is what publishing an experiment in
    parts needs. A mode only its owner can read, because it is a secret: anyone
    holding it can work out which pseudonym any address of yours was given.

    Made once means once even when two of these run at the same moment. Written
    the way the map is written, with a temporary moved into place, both would
    have found no key, both would have made one, and the second would have
    replaced the first: two exports of the same walk under two keys, only one of
    which still exists, so the walk exported first can never be added to again.
    Creation cannot replace anything instead, and whoever loses the race goes
    round again and reads the key that won.

    Which makes the question asked at the top of the loop "is this name taken",
    and it has to be asked as that. Asking whether the file is there answers a
    different question, and the two disagree about a symlink to nothing, which
    is a name that is taken and a file that is not there. This went round for
    ever on one.

    And what is read has to be a file. A key is 32 bytes on a disk, and opening
    the name without saying so will read anything the name happens to be: a FIFO
    with nobody writing to it waits for a writer that never comes, and a
    character device hands over as many bytes as anyone cares to ask for. Both
    hang a command whose only job here was to read half a line of hexadecimal.
    The name is opened without waiting, asked what it is through the descriptor
    already held, and read only once it has said it is a regular file, which
    also leaves no moment between the asking and the reading for it to become
    something else.
    """
    while True:
        try:
            # The question is whether a name is taken, and `exists` answers a
            # different one: whether what the name leads to is there. A symlink
            # to nothing is a name that is taken and leads nowhere, so `exists`
            # said no, `link` said the name was in use, and the two answers
            # together spun this loop until somebody killed the command.
            path.lstat()
        except FileNotFoundError:
            entry = False
        else:
            entry = True
        if entry:
            told = None
            found, there = _read_secret(path)
            if len(found) != KEY_BYTES:
                # An empty key is one anybody can compute, so every pseudonym in
                # the export would be worked out by whoever received it. A short
                # one is also what a machine that lost power mid-write leaves,
                # which is why the length is checked rather than assumed.
                raise ExportError(
                    f"{path} holds {there.st_size} bytes and an export key is {KEY_BYTES}. "
                    "Delete it to have a new one made, or name another with --key-file."
                )
            mode = stat.S_IMODE(there.st_mode)
            if mode & 0o077:
                # Said and not corrected. The file is the operator's, and a
                # program that quietly tightened the permissions on something it
                # found would be making a decision that is not its own.
                told = f"{path} can be read by others (mode {mode:04o}), and it is the export key."
            return found, told
        made = secrets.token_bytes(KEY_BYTES)
        try:
            _write_secret(path, made)
        except FileExistsError:
            # Somebody else made one between the look and the write. Theirs is
            # the key now, and round again to read it.
            continue
        return made, (
            f"A new export key was made in {path}. Keep it: it is what makes the pseudonyms "
            "of one export match the pseudonyms of the next."
        )


def _read_secret(path: Path) -> tuple[bytes, os.stat_result]:
    """A secret, read only once the name has said it is a regular file.

    `O_NONBLOCK` so that opening it cannot wait, and `fstat` on the descriptor
    already open rather than on the name, so nothing can be swapped underneath
    between finding out what it is and reading it. One byte more than a key is
    asked for, so that a file which is too long is as visible as one too short.
    """
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise ExportError(
            f"{path} is a name that does not lead to a file that can be read "
            f"({exc.strerror}). Delete it, or name another with --key-file."
        ) from exc
    try:
        there = os.fstat(handle)
        if not stat.S_ISREG(there.st_mode):
            raise ExportError(
                f"{path} is not a regular file, and an export key is 32 bytes in one. "
                "Delete it, or name another with --key-file."
            )
        return os.read(handle, KEY_BYTES + 1), there
    finally:
        os.close(handle)


def _write_secret(path: Path, content: bytes) -> None:
    """Create a secret whole, or raise FileExistsError because somebody already did.

    Two properties are wanted at once and the obvious two ways each give one.
    A temporary moved into place is never seen half written, and always replaces
    what is there, which for a file whose whole value is being the only one is
    the wrong half of the bargain. `O_EXCL` on the real name never replaces
    anything, and creates the name before the bytes, so whoever loses the race
    can read a key of nought bytes and be told the file is broken while it is
    merely unfinished.

    `link` gives both. The content is written and fsynced under a temporary
    name, so it is complete before anything can see it, and linking that inode
    to the real name either works or fails because somebody got there first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, named = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".new")
    beside = Path(named)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(content)
            os.fchmod(out.fileno(), 0o600)
            out.flush()
            os.fsync(out.fileno())
        os.link(beside, path)
        _fsync_dir(path.parent)
    finally:
        beside.unlink(missing_ok=True)


@dataclass
class Names:
    """The pseudonyms of one export, and what it had to do to make them.

    What goes in the file is the HMAC itself, shortened. Publishing a digest is
    the thing a bare hash of a network name cannot afford, because anybody can
    run a list of common names through `sha256` and see which ones match. Keyed,
    nobody can compute the digest of a guess without the key, and the name can
    be the digest.
    """

    key: bytes
    mac_shaped: bool = False
    numbered: dict[tuple[str, str], str] = field(default_factory=dict)
    kept_names: set[str] = field(default_factory=set)
    relabelled: set[str] = field(default_factory=set)

    def of(self, kind: str, value: str) -> str:
        """The pseudonym for one value of one kind, made once and reused.

        The name written is the HMAC itself, shortened. A counter would read
        better, `ap-0042` against `ap-1c8a74f992ae`, and it would be a lie. A
        counter numbers things in the order they turn up, so the same router
        exported twice gets two different names and the key decides nothing at
        all. Two exports months apart agreeing is the reason the key is kept,
        and a name that does not depend on the key cannot deliver it.

        Publishing the digest is safe precisely because it is an HMAC. Without
        the key nobody can work out the digest of a guessed network name, which
        is the whole objection to a bare `sha256(ssid)`.
        """
        # The address in one spelling of itself, the way `netlog.address` settles
        # it on the way in. Without this the same router written in capitals in
        # one file and in lower case in another gets two pseudonyms.
        canonical = _canonical(kind, value)
        seen = self.numbered.get((kind, canonical))
        if seen is not None:
            return seen
        digest = hmac.new(self.key, f"{kind}\0{canonical}".encode(), "sha256")
        if self.mac_shaped and kind == "bssid":
            made = _mac_shaped(digest.digest())
        else:
            made = f"{'ap' if kind == 'bssid' else kind}-{digest.hexdigest()[:12]}"
        self.numbered[(kind, canonical)] = made
        return made


def _canonical(kind: str, value: str) -> str:
    """One spelling of a value, so that one thing gets one pseudonym.

    The rest of Enodia already decides this, and an export that decided it
    differently would publish a walk with a different shape from the one that
    was walked. `folded` is what the canonical frame and the confusable-name
    check both compare by: case, accents and runs of spaces do not make two
    streets, because a notebook is written by hand weeks apart and `Yaguaron`
    one week against `Yaguaron` with its accent the next is one street. Hashing
    the spelling instead turned it into two, and three corners of one avenue
    came out as three avenues.

    A BSSID is folded for case and nothing else, the way `address` and
    `SeenNetwork.key` fold it. An SSID is left exactly as it is: `Casa` and
    `casa` are two names somebody chose.
    """
    if kind == "bssid":
        return value.casefold()
    return folded(value) if kind in FOLDED_KINDS else value


def _mac_shaped(digest: bytes) -> str:
    """Six bytes of the digest as an address that says it is not a real one.

    The two bits every MAC carries in its first byte: multicast off, because
    this stands in for one card and not a group, and locally administered on,
    because that is the half of the space nobody is assigned out of. A reader
    who knows what those bits mean can see at a glance that it was made up.
    """
    raw = bytearray(digest[:6])
    raw[0] &= 0b1111_1110
    raw[0] |= 0b0000_0010
    return ":".join(f"{one:02x}" for one in raw)


@dataclass
class Shift:
    """The artificial clock and the artificial place a walk is moved onto.

    The intervals are the data and the time of day is a pattern of life, so the
    first moment of the two files together becomes the epoch and everything
    keeps its distance from it. Of the two files together, because marking the
    corner before walking is the ordinary way round, so the notebook usually
    starts first and shifting by the log alone would put it before zero.
    """

    began: datetime
    lat: float | None = None
    lon: float | None = None

    def when(self, moment: datetime) -> datetime:
        return EPOCH + (moment - self.began)

    def where(self, lat: float, lon: float) -> tuple[float, float]:
        """The same point relative to the walk, laid out from the origin.

        Not a subtraction of degrees. The offsets are worked out in metres and
        put back down at the origin's own latitude, so the distances between the
        crossings come out identical instead of moving by the percent or so that
        a change of latitude costs a degree of longitude.
        """
        if self.lat is None or self.lon is None:  # pragma: no cover - set before any use
            return (lat, lon)
        north = distance_metres(self.lat, self.lon, lat, self.lon) * (1 if lat >= self.lat else -1)
        east = distance_metres(self.lat, self.lon, self.lat, lon) * (1 if lon >= self.lon else -1)
        squeeze = max(cos(radians(ORIGIN[0])), 0.01)
        moved_lat = ORIGIN[0] + degrees(north / EARTH_RADIUS_M)
        moved_lon = ORIGIN[1] + degrees(east / (EARTH_RADIUS_M * squeeze))
        return (round(moved_lat, 6), round(moved_lon, 6))


@dataclass
class Exported:
    """What an export did, so that the person publishing it can read it back."""

    log: Path
    notebook: Path
    scans: int
    networks: int
    crossings: int
    kept_names: int
    relabelled: int
    ssid: str
    withheld: int = 0
    survived: tuple[tuple[str, int], ...] = ()
    unconfirmed: str | None = None


def crossing_name(names: Names, name: str) -> str:
    """A crossing renamed street by street, or as one place when it is not a corner.

    Street by street so that the same street turning up at two corners is still
    the same street, which is what the canonical frame and the confusable-name
    check both live on. `corner_streets` is the same reader `--geocode` uses, so
    a name it will not split here is one Enodia would not have split either.
    """
    halves = corner_streets(name)
    if halves is None:
        return names.of("place", name)
    return f"{names.of('street', halves[0])} y {names.of('street', halves[1])}"


def public_network(names: Names, seen: SeenNetwork, ssid: str) -> dict[str, Any]:
    """One scanned network as it goes out.

    Given the network and not the JSON it was written as. Whoever calls this has
    already read the file through `read_log`, so there is one set of rules in the
    program about what a frequency or a signal may be, and this cannot write a
    line Enodia's own reader would throw away.

    The name is dropped by default and the address is substituted, with one
    exception that matters: a network whose backend gave no BSSID is identified
    by its name and nothing else, and `identified` going false makes every
    reader in Enodia drop it without a word. Those keep a pseudonym for the
    name, because removing it would not be publishing less, it would be
    publishing a shorter walk.
    """
    address, name = seen.bssid, seen.ssid
    label = seen.security
    if label is not None and label not in PLAIN_SECURITY:
        # None would say the backend reported nothing, which is a reading of its
        # own and not this one. A marker says the label was there and is not
        # being repeated.
        names.relabelled.add(label)
        label = WITHHELD
    out: dict[str, Any] = {
        "ssid": name,
        "bssid": names.of("bssid", address) if address else None,
        "security": label,
        "frequency": seen.frequency,
        "signal_dbm": seen.signal_dbm,
        "signal_percent": seen.signal_percent,
    }
    if ssid == "keep":
        out["ssid"] = name
    elif ssid == "pseudonym" or not address:
        if ssid == "remove" and name:
            names.kept_names.add(name)
        out["ssid"] = names.of("ssid", name) if name else name
    else:
        out["ssid"] = ""
    # The flag that says which of these the walker was on, which is a thing
    # about the walker and not about the street.
    out["connected"] = False
    return out


def public_record(
    names: Names, shift: Shift, record: LogRecord, ssid: str
) -> dict[str, Any] | None:
    """One log record as it goes out, or None when it cannot go out at all.

    Built by naming fields off the typed record, which is what turns the rule
    round. A field added to `LogRecord` next year does not appear here because
    somebody has to write it in, where copying a dict of whatever the file held
    meant the default was to publish. That is the difference between "everything
    but the dangerous parts leave" and "nothing leaves that was not named".

    A record whose time could not be read is left out rather than carried: an
    unshifted timestamp is the one field that would publish the day and the hour
    somebody was on a particular street, and passing one through because it was
    too malformed to move is the failure this command exists to avoid.
    """
    if record.time is None or record.event not in PUBLIC_EVENTS:
        return None
    out: dict[str, Any] = {
        "time": shift.when(record.time).isoformat(timespec="seconds"),
        "event": record.event,
    }
    if record.outing:
        out["outing"] = names.of("outing", record.outing)
    if record.interface:
        # Usually `wlan0` and usually nothing, but Linux takes any name for an
        # interface and somebody's says something. Kept rather than dropped
        # because a log written before `cycle` existed needs it to tell two
        # radios apart, which is what stops a pace estimate inventing movement.
        out["interface"] = names.of("radio", record.interface)
    if record.cycle is not None:
        out["cycle"] = record.cycle
    if record.reason is not None:
        out["reason"] = record.reason if record.reason in PLAIN_REASONS else WITHHELD
    if record.seconds is not None:
        out["seconds"] = record.seconds
    if record.number is not None:
        out["number"] = record.number
    if record.key is not None:
        out["key"] = record.key
    if record.event in CARRY_NETWORKS:
        # Written even when empty, because a scan that heard nothing is a street
        # with no Wi-Fi on it and that is a reading like any other.
        out["networks"] = [public_network(names, one, ssid) for one in record.networks]
    return out


def export_log(
    records: Sequence[LogRecord], names: Names, shift: Shift, ssid: str
) -> tuple[list[str], int, int, int]:
    """One walk of the log, record by record, with the association records left out.

    Given the records and not the file. The walk was picked out by
    `records_for_outing`, which is the one place in Enodia that knows a record
    naming no token belongs to the walk in force where it sits, and reading the
    file a second time here to work that out again was how the two came apart.
    """
    rows: list[str] = []
    scans = 0
    withheld = 0
    seen: set[str] = set()
    for record in records:
        if record.event in LEFT_OUT:
            continue
        out = public_record(names, shift, record, ssid)
        if out is None:
            withheld += 1
            continue
        if record.is_scan:
            scans += 1
        # Counted by what identifies them after the substitution, which is the
        # address when there is one and the name when there is not: the same
        # rule `SeenNetwork.key` uses, so the number means what a reader of the
        # exported file would count.
        seen.update(
            str(one["bssid"] or one["ssid"])
            for one in out.get("networks", ())
            if one["bssid"] or one["ssid"]
        )
        rows.append(json.dumps(out, ensure_ascii=False))
    return rows, scans, len(seen), withheld


def export_notebook(waypoints: Sequence[Waypoint], names: Names, shift: Shift) -> list[str]:
    """The notebook, written again from what was parsed out of it.

    Written again and not patched, which is the opposite of what `--geocode`
    does to the same file. That one keeps every byte because the operator's own
    comments are worth keeping; here the operator's own comments are arbitrary
    prose about their afternoon, and the only way to be sure none of it goes out
    is not to carry any of it.
    """
    rows: list[str] = []
    day = None
    for point in waypoints:
        moved = shift.when(point.time)
        if moved.date() != day:
            # A directive every time the day turns over, and not one at the top.
            # A walk that runs past midnight, or one interrupted and carried on
            # two days later, would otherwise come back reading as the same day
            # and lose the gap: the intervals are the whole of what survives
            # here, so losing one is losing the data rather than hiding it.
            day = moved.date()
            rows.append(f"date {day.isoformat()}")
        line = f"{moved.strftime('%H:%M:%S')} {crossing_name(names, point.name)}"
        if point.lat is not None and point.lon is not None:
            lat, lon = shift.where(point.lat, point.lon)
            line += f" @ {lat:.6f}, {lon:.6f}"
        rows.append(line)
    return rows


def earliest(records: Sequence[LogRecord], waypoints: Iterable[Waypoint]) -> datetime:
    """The first moment either file knows about, within the walk being exported."""
    moments = [point.time for point in waypoints]
    moments += [record.time for record in records if record.time is not None]
    if not moments:
        raise ExportError("nothing in this walk carries a time, so there is nothing to shift")
    return min(moments)


def _comment(line: str) -> str:
    """The operator's own prose on one notebook line, if there is any."""
    body = line.split(" ", 1)[1] if MARK_PREFIX.match(line) else line
    return body.partition("#")[2].strip()


def what_went_in(log: Path, notebook: Path, ssid: str) -> set[str]:
    """Every name and address the two source files carry, to look for afterwards.

    Read from the files and not from the pseudonyms `Names` handed out, which is
    the whole of what makes this worth running. Checking the values that went
    through `Names` only proves that what was substituted was substituted; the
    leak worth catching is the field nobody passed through it, which is exactly
    what `interface` and `reason` were until somebody went looking.

    Every walk in the file and not only the exported one, for the same reason:
    publishing a neighbour from a walk the operator had forgotten the file held
    is the worse of the two mistakes.

    With `--ssid keep` the names are not collected, because they are in the
    output on purpose and reporting them would be reporting the flag back.
    """
    wanted = [key for key in IDENTIFYING if key != "ssid" or ssid != "keep"]
    went: set[str] = set()
    for raw in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            fields = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(fields, dict):
            continue
        networks = fields.get("networks")
        holders = [fields]
        if isinstance(networks, list):
            holders += [one for one in networks if isinstance(one, dict)]
        for holder in holders:
            went.update(
                value for key in wanted if isinstance(value := holder.get(key), str) and value
            )
            went.update(
                value
                for key, known in BOUNDED
                if isinstance(value := holder.get(key), str) and value and value not in known
            )
    for raw in notebook.read_text(encoding="utf-8", errors="replace").splitlines():
        whole = raw.lstrip("\ufeff").strip()
        went.add(_comment(whole))
        line = strip_comment(raw)
        if not line or DATE_DIRECTIVE.match(line):
            continue
        found = NOTEBOOK_LINE.match(line) or MARKED_LINE.match(line)
        if found is None:  # pragma: no cover - MARKED_LINE takes any line with a character in it
            continue
        name = found.group("name").strip()
        went.add(name)
        # Street by street as well as whole, because the export takes a corner
        # apart before renaming it and a half surviving on its own is a street
        # name in plain text just the same.
        went.update(corner_streets(name) or ())
    went.discard("")
    return went


def survivors(went_in: set[str], text: str) -> tuple[tuple[str, int], ...]:
    """What went in and is still in the bytes about to go out, and how often.

    A plain substring search, folded for case, over exactly what `_write_both`
    is about to write. It reports rather than refuses: a network called `date`
    matches every directive in the notebook and one called `2402` matches every
    frequency, and a refusal on those would be an export nobody could make with
    no way around it. The count is there so that one hit and eighty read
    differently at a glance.
    """
    haystack = text.casefold()
    counted = ((value, haystack.count(value.casefold())) for value in sorted(went_in))
    return tuple((value, times) for value, times in counted if times)


def export_outing(
    log: Path,
    notebook: Path,
    out: Path,
    key: bytes,
    ssid: str = "remove",
    mac_shaped: bool = False,
    outing: str | None = None,
) -> Exported:
    """Write a publishable copy of one outing, and say what it did.

    Refuses a destination that is already there rather than writing over it, the
    way `--geocode` refuses: the realistic second run is the one after somebody
    has read the first export line by line before publishing it, and quietly
    replacing that is the kind of loss nobody gets back.
    """
    whole, unusable = read_log_counting(log)
    walks = outings(whole)
    if outing is None and len(walks) > 1:
        # Refused rather than chosen. Everywhere else in Enodia the last walk in
        # the file is a sensible default, and here it would mean publishing the
        # other walks as well because somebody did not know the file held any.
        # A command for publishing does not guess at what was meant to go out.
        raise ExportError(
            f"{log} holds {len(walks)} walks and an export is one walk. "
            "Name it with --outing: " + ", ".join(one or "(unnamed)" for one in walks)
        )
    walk = outing if outing is not None else (walks[0] if walks else "")
    records = records_for_outing(whole, walk)
    if not records:
        raise ExportError(f"{log}: no walk called {walk!r} in it")
    scans = [one for one in records if one.is_scan and one.time is not None]
    if not scans:
        raise ExportError(f"{log}: no timestamped scans found")
    first = min(one.time for one in scans if one.time is not None)
    waypoints = read_notebook(notebook, first.date(), first.tzinfo, button_marks(records))

    names = Names(key=key, mac_shaped=mac_shaped)
    shift = Shift(began=earliest(records, waypoints))
    placed = [point for point in waypoints if point.lat is not None and point.lon is not None]
    if placed:
        shift.lat, shift.lon = placed[0].lat, placed[0].lon

    rows, counted, networks, withheld = export_log(records, names, shift, ssid)
    lines = export_notebook(waypoints, names, shift)
    written = ("\n".join(rows) + "\n", "\n".join(lines) + "\n")
    # Against the bytes themselves and not against the objects they were built
    # from, because the substitution being right is what is in question.
    survived = survivors(what_went_in(log, notebook, ssid), "".join(written))
    log_name = f"outing-{EPOCH.date().isoformat()}.jsonl"
    unconfirmed = _commit(out, (log_name, written[0]), (NOTEBOOK_NAME, written[1]))
    return Exported(
        out / log_name,
        out / NOTEBOOK_NAME,
        counted,
        networks,
        len(waypoints),
        len(names.kept_names),
        len(names.relabelled),
        ssid,
        withheld + unusable,
        survived,
        unconfirmed,
    )


def _fsync_dir(path: Path) -> None:
    """Make a directory's own contents durable, which is not what fsync on a file does."""
    folder = os.open(path, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def _commit(out: Path, *files: tuple[str, str]) -> str | None:
    """Build the export beside where it goes, and move the whole directory into place.

    The directory is the unit, and that took three designs. Two files written
    straight out left half an export when the second write failed. Two written
    to temporaries and moved left half an export when the second rename failed,
    because two renames are two calls and POSIX will not make them one. Trying
    to tell that half from a file somebody had kept was worse than either: it
    cannot be told, and guessing wrong overwrote a walk that had been read and
    approved.

    `--out` must not exist. That is the fourth design and the simplest rule yet,
    and the reason is that `rename` is not the refusal it looks like: it happily
    replaces a destination directory that is empty. So a run could take the
    place of a directory somebody had just made with permissions of their own,
    and even reading those permissions first and copying them was both racy and
    a half measure, since replacing the inode drops its ACLs, its owner and its
    extended attributes without a word. Respecting part of what the operator set
    up and silently dropping the rest is worse than not offering it: `chmod 700`
    on the export afterwards is one command and it keeps every one of them.

    So the name is claimed with `mkdir`, which fails if anything at all is
    already there, a dangling symlink included, and needs no look beforehand to
    race with. It doubles as the one honest answer to what permissions this
    directory should end up with, since it is a directory made right here with
    the operator's own umask, and the staging directory is given its mode at the
    last moment.

    What a crash can leave is the claimed name, empty, and the staging directory
    beside it. Neither is half an export.

    Returns what stopped the directory being made durable, when the export was
    written and that last step was the only thing that failed.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        # The claim. Atomic, so there is nothing to check first and nothing to
        # race with: whatever is there, including a name that leads nowhere,
        # this stops here.
        out.mkdir()
    except OSError as exc:
        raise ExportError(
            f"{out} is already there, and an export is a whole directory rather than "
            f"files put into one ({exc.strerror}). Delete it, or name another with --out."
        ) from exc
    # Unguessable, the way the map's temporary is: a predictable name in a
    # directory others can write to is a name somebody else can get to first.
    staging = out.parent / f".{out.name}.{secrets.token_hex(6)}.new"
    moved = False
    mine = False
    unconfirmed = None
    try:
        # Inside, because the name above is already claimed. Making this one
        # first and failing here, on a full disk or an exhausted inode table,
        # left the claim standing and empty, and the next run was then told the
        # destination already existed. `mine` so that the one case this can fail
        # for without having made anything, a name already taken, never has that
        # directory cleaned up underneath whoever does own it.
        #
        # Private while it is written, whatever the umask says. It used to be
        # built at 0755 and only made private at the end if the destination was,
        # so a walk the operator had arranged to read privately was readable by
        # anybody on the machine for as long as it took to write it. Private
        # first and opened at the end is the only order with no such window.
        staging.mkdir(mode=0o700)
        mine = True
        for name, content in files:
            with (staging / name).open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        # The directory as well as the files in it, or the rename can land with
        # the names there and the contents not.
        _fsync_dir(staging)
        os.chmod(staging, stat.S_IMODE(out.stat().st_mode))
        os.rename(staging, out)
        moved = True
        # The parent as well, and after the rename rather than before: the
        # contents being durable is no use if the name they arrived under is
        # not, and a machine that loses power here would come back to a walk
        # that was exported and is not there.
        try:
            _fsync_dir(out.parent)
        except OSError as exc:
            # Said and not raised. The export is written and complete, and
            # reporting a failure over it told the operator that nothing had
            # happened while a finished export sat there, and the refusal to
            # overwrite then stopped them running the command again to find out.
            unconfirmed = exc.strerror
    finally:
        if not moved:
            if mine:
                for name, _ in files:
                    (staging / name).unlink(missing_ok=True)
                staging.rmdir()
            out.rmdir()
    return unconfirmed


def format_export(done: Exported) -> str:
    """What was written, and the three things the word cannot promise."""
    lines = [
        f"{done.log}  {done.scans} scans, {done.networks} access points",
        f"{done.notebook}  {done.crossings} crossings",
    ]
    if done.unconfirmed:
        lines.append(
            f"\nThe export is written. What could not be confirmed is that the directory "
            f"itself will survive the machine losing power ({done.unconfirmed}), which is "
            "worth knowing before this is the only copy."
        )
    if done.survived:
        many = len(done.survived)
        lines.append(
            f"\nLook at {many} name{'' if many == 1 else 's'} that went into the export and "
            "is still in what came out of it:"
        )
        lines += [
            f"  {value!r}, {times} time{'' if times == 1 else 's'}"
            for value, times in done.survived
        ]
        lines += [
            "  This is a substring search, so an ordinary name matches something innocent:",
            "  a network called 'date' is in every directive of the notebook and one called",
            "  '2402' is in every frequency. It is here to be looked at, not believed.",
        ]
    if done.withheld:
        lines.append(
            f"\n{done.withheld} record{'' if done.withheld == 1 else 's'} left out because "
            "something in it was not something this could publish. A time that cannot be "
            "moved is a time that would have gone out as it was, a number this cannot write "
            "back is a line Enodia would refuse to read, and an event outside Enodia's own "
            "vocabulary is not a record of a walk."
        )
    if done.relabelled:
        many = done.relabelled
        lines.append(
            f"\n{many} security label{'' if many == 1 else 's'} this does not know "
            f"{'was' if many == 1 else 'were'} withheld rather than published. Every label "
            "the backends are known to write goes out as it is, so this is either a daemon "
            "saying something new, which is worth telling me about, or a line somebody edited."
        )
    if done.kept_names:
        lines.append(
            f"\n{done.kept_names} network{'' if done.kept_names == 1 else 's'} had no address "
            "of its own, so the name was given a pseudonym instead of being removed: without "
            "one of the two it is not a network any reader here would keep."
        )
    lines += [
        "",
        "This is pseudonymised and not anonymous, which is three separate things:",
        "  A pseudonym is stable. One appearing eighty times is one router eighty times,",
        "  which is what makes the file worth having and what the word anonymous would deny.",
        "  A radio fingerprint locates itself. The set of access points at a corner is that",
        "  corner's identity, which is how --locate works, so anybody who walks the same",
        "  streets with their own scanner can join their real addresses onto this.",
        "  The shape of the walk survives. The coordinates were moved to an artificial",
        "  origin and the geometry between them is intact, which is what reproducing the",
        "  numbers needs and what makes the route searchable against a map.",
        "",
        "Read it before publishing it. That is the only check that counts.",
    ]
    return "\n".join(lines)
