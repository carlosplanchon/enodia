"""Enodia's log: one JSON object per line.

JSON Lines because of what the log is and where it is written. It is appended to
while walking, from a laptop that may run out of battery mid-line, so a record
has to be one line and a truncated last line has to cost only itself. And its
most hostile input is the SSIDs of other people's networks, which anyone can
choose and which may hold commas, quotes or newlines that no line-oriented text
format survives. What a backend cannot report is `null` here, not a question
mark that has to be parsed back into nothing.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCAN_EVENT = "scan"
NEW_EVENT = "new"
CONNECTED_EVENT = "connected"
DISCONNECTED_EVENT = "disconnected"
SCAN_FAILED_EVENT = "scan_failed"
SUSPENDED_EVENT = "suspended"
MARK_EVENT = "mark"
# The kernel said it threw input away. A mark is a street corner, so a mark
# that never arrived moves every crossing after it, and the hole belongs in
# the file rather than only in whatever the operator happened to hear.
BUTTON_LOST_EVENT = "button_lost"


def now_iso(ago: float = 0.0) -> str:
    """Local time as ISO 8601 with offset, e.g. 2026-09-05T00:14:03-03:00.

    `ago` moves it back by that many seconds. A button press the kernel
    timestamped two seconds before the read that delivered it happened two
    seconds ago, and dating it to the moment it was handled put two crossings at
    the same second.
    """
    when = datetime.now().astimezone()
    return (when - timedelta(seconds=ago)).isoformat(timespec="seconds")


def channel_for(frequency_mhz: int | None) -> int | None:
    """Wi-Fi channel number for a centre frequency in MHz (2.4, 5 and 6 GHz bands)."""
    if frequency_mhz is None:
        return None
    if frequency_mhz == 2484:
        return 14
    if 2412 <= frequency_mhz <= 2472:
        return (frequency_mhz - 2407) // 5
    if 5000 <= frequency_mhz <= 5895:
        return (frequency_mhz - 5000) // 5
    if 5955 <= frequency_mhz <= 7115:
        return (frequency_mhz - 5950) // 5
    return None


# --- What a log holds, however it was written --------------------------------


def parse_timestamp(value: str) -> datetime | None:
    """A record timestamp, in ISO 8601, with its offset from UTC.

    The offset is not optional. Every timestamp Enodia writes carries one, and a
    walk crosses the end of daylight saving like any other hour of the year, so
    a bare wall clock does not say which of two moments it means. Worse, Python
    refuses to compare a naive datetime with an aware one, so a single edited
    line without an offset did not cost itself: it took down every scan in the
    file, out of the sort that puts them in the order they happened.
    """
    try:
        found = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return None if found.utcoffset() is None else found


@dataclass(frozen=True)
class SeenNetwork:
    """One network as some scan saw it."""

    ssid: str
    bssid: str | None
    security: str | None
    frequency: int | None
    signal_dbm: int | None
    signal_percent: int | None
    connected: bool = False

    @property
    def channel(self) -> int | None:
        """The channel this frequency falls on. Derived, and ambiguous across
        bands: only the frequency says which band it is."""
        return channel_for(self.frequency)

    @property
    def key(self) -> str:
        """Identity across scans: the BSSID, or the SSID when the backend gives none.

        The address in one spelling of itself, since hexadecimal written out has
        two of every letter and this string is what one network being another is
        decided by. `address` already settles that at both doors a network comes
        in by, and it is settled again here because `locate_scan` takes networks
        from a caller as well, and an identity should not depend on how the
        object was built. The SSID is left alone: "Casa" and "casa" are two
        names somebody chose, not two spellings of one.
        """
        return self.bssid.casefold() if self.bssid else self.ssid

    @property
    def identified(self) -> bool:
        """Whether this network can be told apart from another one at all.

        A backend that reports no BSSID, which iwd sometimes does not, and a
        network that hides its SSID leave nothing to identify it by, and the
        empty key that results is not an identity: it is the same empty key
        every other anonymous network in the world has. Two of them are not
        evidence of anything, and everything here that folds, matches or counts
        networks by key has to leave them out, or a fresh anonymous network
        scores a perfect match against a remembered one and a map reports a
        coincidence of absence as a place.
        """
        return bool(self.key)

    @property
    def open(self) -> bool:
        """True when the network is unencrypted."""
        return self.security == "open"

    @property
    def has_signal(self) -> bool:
        """Whether the backend reported any strength at all for this sighting."""
        return self.signal_dbm is not None or self.signal_percent is not None

    @property
    def strength(self) -> float:
        """Comparable strength: dBm when the backend reports it, else estimated
        from the percentage. With no signal at all it is low enough to lose
        every comparison -- check `has_signal` rather than the value."""
        if self.signal_dbm is not None:
            return float(self.signal_dbm)
        if self.signal_percent is None:
            return -999.0
        return -100.0 + 0.6 * self.signal_percent


@dataclass
class LogRecord:
    """One record of a log: a scan, a list of new networks, or a connection change."""

    event: str
    time: datetime | None = None
    interface: str | None = None
    ssid: str | None = None
    bssid: str | None = None
    frequency: int | None = None
    signal_dbm: int | None = None
    signal_percent: int | None = None
    reason: str | None = None
    seconds: float | None = None
    number: int | None = None
    # The key code of the button that was pressed for a mark. Nothing reads it
    # to place anything, and it is here because a record has to be able to come
    # back whole: a field the writer writes and the reader drops is a field the
    # export had to go behind this type to find, and that second reader of the
    # same file is what every round of this month's bugs came out of.
    key: int | None = None
    cycle: int | None = None
    outing: str | None = None
    networks: list[SeenNetwork] = field(default_factory=list)

    @property
    def is_scan(self) -> bool:
        return self.event == SCAN_EVENT

    @property
    def channel(self) -> int | None:
        """The channel of the access point this record associated to, if any."""
        return channel_for(self.frequency)


# What a receiver can actually report. A card hears from about -100 dBm at the
# edge of usable up to -20 with the router on the desk, and a percentage is a
# percentage. A number outside these is not a reading, and here that is not
# merely untidy: `signal_weight` raises ten to the dBm over thirty, so 100000
# arrives as an OverflowError out of an estimate that had no reason to doubt
# its input, and a plausible-looking 500 silently wins every weighting it is in.
SIGNAL_DBM = (-130.0, 0.0)
PERCENT = (0.0, 100.0)
# Generous on purpose: 2.4, 5 and 6 GHz are what anybody walks past, but 900 MHz
# and 60 GHz radios exist and refusing a real reading is worse than carrying an
# odd one. Nothing here does arithmetic on a frequency, only `channel_for`,
# which answers None for anything it does not recognise.
FREQUENCY_MHZ = (100.0, 100_000.0)


def number(value: Any, low: float | None = None, high: float | None = None) -> float | None:
    """A finite number in range, or None for anything else that turned up.

    A log or a map is a file that gets copied about and edited by hand, and JSON
    will happily carry a string where a latitude belongs, or a NaN, which Python
    reads without complaint and which then poisons every average it reaches.
    Refused at the door, where it is one line, rather than found later as a
    TypeError from inside an arithmetic that had no reason to doubt its input.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        found = float(value)
    except OverflowError:
        # A JSON integer of four hundred digits is an int in Python, and float()
        # refuses it rather than handing back an infinity to be caught below.
        return None
    if found != found or found in (float("inf"), float("-inf")):  # NaN, and both infinities
        return None
    if (low is not None and found < low) or (high is not None and found > high):
        return None
    return found


def whole(value: Any, low: float | None = None, high: float | None = None) -> int | None:
    """A finite number in range as an int, or None. For measurements, where -47.5 is -47.

    A radio that reports a fraction of a dBm has measured something real, and
    dropping the fraction loses nothing anybody was going to use.
    """
    found = number(value, low, high)
    return None if found is None else int(found)


def integer(value: Any, low: int | None = None, high: int | None = None) -> int | None:
    """A whole number, or None. For the numbers that name something, not measure it.

    A cycle and a mark on the paper are identities: cycle 1 is a look at one
    place and mark 1 is a corner somebody wrote down. A fraction there does not
    mean a finer reading, it means the value is not what it says it is, and
    rounding 1.7 down would hand back an answer that collides with the real 1.
    So it is refused rather than rounded.
    """
    found = number(value, low, high)
    return None if found is None or found != int(found) else int(found)


def address(value: Any) -> str | None:
    """A BSSID as one spelling of itself, or None.

    Hexadecimal written out has two spellings of every letter, and the identity
    of a network is this string compared to another one. `AA:BB:CC:DD:EE:FF` in
    a map and `aa:bb:cc:dd:ee:ff` in a scan are one access point and scored
    nothing in common, so a perfect match came back as "not on the map". The
    file is hand-edited and copied about, which is exactly how the two spellings
    end up in one map.
    """
    found = text(value)
    return None if found is None else found.casefold()


def text(value: Any) -> str | None:
    """A string, or None. A number where a name belongs is not a name.

    Nothing crashes on an SSID that came back as a list, which is worse than if
    it did: it travels all the way to the report and prints there as `['x']`.
    """
    return value if isinstance(value, str) else None


def network_from_json(fields: dict[str, Any]) -> SeenNetwork:
    """One scanned network, read back from the shape `network_to_json` writes.

    The file's door into the type. `seen_network` is the radio's, and the two
    end at the same object, so a network read off the disk and a network heard a
    second ago are one kind of thing rather than two that resemble each other.
    """
    security = fields.get("security")
    return SeenNetwork(
        ssid=text(fields.get("ssid")) or "",
        bssid=address(fields.get("bssid")) or None,
        security=security.lower() if isinstance(security, str) and security else None,
        frequency=whole(fields.get("frequency"), *FREQUENCY_MHZ),
        signal_dbm=whole(fields.get("signal_dbm"), *SIGNAL_DBM),
        signal_percent=whole(fields.get("signal_percent"), *PERCENT),
        # `is True` and not `bool(...)`: the string "false" is a non-empty
        # string, and turning it into a network you were associated to is the
        # one kind of answer this whole boundary exists to refuse.
        connected=fields.get("connected") is True,
    )


def _record_from_json(fields: dict[str, Any]) -> LogRecord:
    stamp = fields.get("time")
    networks = fields.get("networks")
    return LogRecord(
        event=text(fields.get("event")) or "",
        time=parse_timestamp(stamp) if isinstance(stamp, str) else None,
        interface=text(fields.get("interface")),
        ssid=text(fields.get("ssid")),
        bssid=address(fields.get("bssid")),
        frequency=whole(fields.get("frequency"), *FREQUENCY_MHZ),
        signal_dbm=whole(fields.get("signal_dbm"), *SIGNAL_DBM),
        signal_percent=whole(fields.get("signal_percent"), *PERCENT),
        reason=text(fields.get("reason")),
        outing=text(fields.get("outing")),
        # A cycle and a mark number are counted from one, and both are compared
        # with `max` on the way to deciding what the next one is. A string here
        # used to stop `--resume` and the monitor's own constructor with a
        # TypeError out of an arithmetic that had no reason to doubt its input.
        seconds=number(fields.get("seconds"), 0.0),
        number=integer(fields.get("number"), 1),
        # Counted from zero, unlike the two above: the kernel's key codes begin
        # at KEY_RESERVED, which is 0.
        key=integer(fields.get("key"), 0),
        cycle=integer(fields.get("cycle"), 1),
        networks=[
            network_from_json(n)
            for n in (networks if isinstance(networks, list) else [])
            if isinstance(n, dict)
        ],
    )


def record_to_json(record: LogRecord) -> dict[str, Any]:
    """One record as it is written, which is the inverse of `_record_from_json`.

    The two are a pair on purpose. A log is written by this and read by that, and
    an export of a log is written by neither and read by both, so anything the
    pair disagrees about is a thing that leaves by one door and comes back a
    different shape. That is not a tidiness argument: `--export-public` used to
    copy fields out of the raw JSON alongside them, and a `cycle` of "abc" went
    out as "abc" and came back as nothing, out of a command whose whole job is
    knowing exactly what it published.

    A field that is None is left out rather than written as null, which is what
    the writing side did when it simply did not pass one. `networks` is the
    exception: a scan that heard nothing is a street with no Wi-Fi on it, and an
    empty list is how it says so.
    """
    out: dict[str, Any] = {}
    if record.time is not None:
        out["time"] = record.time.isoformat(timespec="seconds")
    out["event"] = record.event
    named: tuple[tuple[str, Any], ...] = (
        ("outing", record.outing),
        ("interface", record.interface),
        ("cycle", record.cycle),
        ("ssid", record.ssid),
        ("bssid", record.bssid),
        ("frequency", record.frequency),
        ("signal_dbm", record.signal_dbm),
        ("signal_percent", record.signal_percent),
        ("reason", record.reason),
        ("seconds", record.seconds),
        ("number", record.number),
        ("key", record.key),
    )
    out.update({name: value for name, value in named if value is not None})
    if record.event in (SCAN_EVENT, NEW_EVENT):
        out["networks"] = [network_to_json(one) for one in record.networks]
    return out


def network_to_json(seen: SeenNetwork) -> dict[str, Any]:
    """One network as it is written, the inverse of `network_from_json`.

    Every field written, none of them left out. A network is a row of readings
    and a reading that is missing is itself the finding: iwd reports no BSSID
    and no frequency, and a file that left those out would read as a file that
    was never asked.
    """
    return {
        "ssid": seen.ssid,
        "bssid": seen.bssid,
        "security": seen.security,
        "frequency": seen.frequency,
        "signal_dbm": seen.signal_dbm,
        "signal_percent": seen.signal_percent,
        "connected": seen.connected,
    }


# --- How a log is written ----------------------------------------------------


def seen_network(ap: Any) -> SeenNetwork:
    """An ifpeek `AccessPoint` as the network the rest of the code works with.

    The radio's own door into the type, beside `network_from_json`, which is the
    file's. Both end at the same object, so a live scan and a scan read back an
    hour later are the same thing and not two things that resemble each other.

    The frequency is kept in MHz, as reported, not as a channel number: a
    channel is derived and ambiguous across bands -- channel 1 is 2412 MHz in
    2.4 GHz and 5955 MHz in 6 GHz -- and which band an access point is on is
    what says how far away a given signal strength puts it.

    Anything the backend does not report -- iwd gives no BSSID or frequency --
    is None. Security is kept as the backend spells it, lowercased, with
    "open" for an unencrypted network.
    """
    security = (ap.security or "").lower()
    # Checked on the way in as well as on the way out. A driver that reports a
    # signal of 100000, or a percentage of 300, is no more believable than an
    # edited line, and a walk writes what it heard straight to the file.
    return SeenNetwork(
        ssid=text(ap.ssid) or "",
        bssid=address(ap.bssid),
        security=security or None,
        frequency=whole(ap.frequency, *FREQUENCY_MHZ),
        signal_dbm=whole(ap.signal_dbm, *SIGNAL_DBM),
        signal_percent=whole(ap.signal_percent, *PERCENT),
        connected=bool(ap.connected),
    )


class NetworkLog:
    """Append-only JSON Lines log of scans and connection changes."""

    def __init__(self, path: str | Path = "networks.jsonl") -> None:
        self.path = Path(path)
        # One handle on a file is one run of the loop, and that is one walk, so
        # every record it writes says which. `--resume` replaces this with the
        # token already in the file, since that run is the same walk continuing.
        self.outing = new_outing()

    def write(self, record: LogRecord) -> None:
        """Append one record, serialised the one way records are serialised."""
        with self.path.open("a", encoding="utf-8") as f:
            # ensure_ascii=False keeps accented and non-Latin SSIDs readable;
            # json still escapes every quote, comma and newline inside them.
            f.write(json.dumps(record_to_json(record), ensure_ascii=False) + "\n")

    def _append(
        self, event: str, interface: str | None = None, ago: float = 0.0, **fields: Any
    ) -> None:
        """Build the record this walk is writing, and hand it to the serializer.

        The clock goes through `parse_timestamp` on its way in, which looks like
        a detour and is the point: the timestamp of a live capture enters by the
        same door as the timestamp of a line read back off the disk, so there is
        one moment in the program and not a string in one half and a datetime in
        the other.
        """
        self.write(
            LogRecord(
                event=event,
                time=parse_timestamp(now_iso(ago)),
                outing=self.outing,
                interface=interface or None,
                **fields,
            )
        )

    def record_scan(
        self,
        ap_list: Iterable[Any],
        interface: str | None = None,
        cycle: int | None = None,
    ) -> None:
        """Record every access point seen in a scan on `interface`.

        `cycle` numbers the pass of the loop this scan belongs to. Watching two
        interfaces writes a record each, and they are one look at one place from
        one pair of feet: the reconciliation has to put them back together
        before it reads the pace off them, and the timestamp cannot be trusted
        to say so, since it has one second of resolution and a cycle whose scans
        straddle a second would come apart. The loop knows when a cycle begins,
        so it says.

        A scan record is always what the radio heard. A cycle whose scan cannot
        be trusted, because the daemon refused it or the radio was switched off,
        is recorded as `scan_failed` instead, never as a scan, so an empty list
        here is a street with no Wi-Fi on it.
        """
        fields: dict[str, Any] = {"networks": [seen_network(ap) for ap in ap_list]}
        if cycle is not None:
            fields["cycle"] = cycle
        self._append(SCAN_EVENT, interface, **fields)

    def record_new_networks(
        self, new_networks: Iterable[Any], interface: str | None = None
    ) -> None:
        """Record the access points seen for the first time."""
        self._append(NEW_EVENT, interface, networks=[seen_network(ap) for ap in new_networks])

    def record_connected(
        self,
        actual_network: str,
        bssid: str | None = None,
        interface: str | None = None,
        frequency: int | None = None,
        signal_dbm: int | None = None,
        signal_percent: int | None = None,
    ) -> None:
        """Record an association to `actual_network`, as the access point comes through.

        The BSSID is what tells one access point from another inside a network
        that has several, so two of these records with the same name and
        different BSSIDs are a roam, not a reconnection. The signal is written
        in the same fields a scanned network uses, and is what makes a roam
        readable afterwards: you moved because the first one was fading.
        """
        self._append(
            CONNECTED_EVENT,
            interface,
            ssid=str(actual_network),
            bssid=bssid,
            frequency=frequency,
            signal_dbm=signal_dbm,
            signal_percent=signal_percent,
        )

    def record_disconnected(self, interface: str | None = None) -> None:
        """Record a disconnection."""
        self._append(DISCONNECTED_EVENT, interface)

    def record_scan_failed(self, interface: str | None, reason: str) -> None:
        """Record a cycle whose scan the Wi-Fi daemon refused or could not do.

        Without it, a cycle that produced no scan looks like a cycle that never
        happened, and the reconciliation reads the hole as a street with nothing
        on it.
        """
        self._append(SCAN_FAILED_EVENT, interface, reason=reason)

    def record_suspended(self, seconds: float) -> None:
        """Record, on waking, that the machine was asleep for `seconds`."""
        self._append(SUSPENDED_EVENT, seconds=round(seconds, 1))

    def record_button_lost(self, reason: str) -> None:
        """Record that the button's own queue overran and presses may be missing.

        Not a mark and not a failure to scan: the radio is fine and the walk goes
        on. What it says is that the numbered marks either side of it may not be
        consecutive, which is the one thing a reconciliation cannot work out for
        itself from a notebook that counts from one.
        """
        self._append(BUTTON_LOST_EVENT, reason=reason)

    def record_mark(self, number: int, key: int | None = None, ago: float = 0.0) -> None:
        """Record a press of the headset button: crossing `number`, passed `ago` seconds back.

        The notebook line that names this crossing carries the same number, so
        the time comes from here and the name from the paper. Which makes this
        time the whole point of the button, and `ago` is what keeps it the time
        of the press rather than the time the press was dealt with.
        """
        self._append(MARK_EVENT, ago=ago, number=number, key=key)


# --- Naming one walk of a file -----------------------------------------------


def new_outing() -> str:
    """A token naming one run of the loop, written on every scan it records.

    An outing used to be named by when its first placed scan happened, and the
    clock has one second of resolution, so two walks that began inside the same
    second were one outing: the map refused the second as already added, and
    `check_map` held the two out together and called it one walk. It is the
    lesson `cycle` taught one layer down, arriving one layer up. Four random
    bytes are not a clock and cannot collide by two people being quick.
    """
    return secrets.token_hex(4)


def last_outing(path: str | Path) -> str | None:
    """The outing the last record of a log belongs to, or None when it says none.

    What `--resume` needs: carrying on into a file is carrying on with the walk
    that is already in it, not beginning a second one that happens to share the
    pages.
    """
    try:
        records = read_log(path)
    except OSError:
        return None
    return next((r.outing for r in reversed(records) if r.outing), None)


def _walks(records: Sequence[LogRecord]) -> list[tuple[str, LogRecord]]:
    """Each record with the walk it belongs to, carrying the last name seen.

    A record that names no walk belongs to the walk in force where it sits. Only
    the ones before any name at all are the walk called "", which is what a log
    written before the token existed is, whole. Reading every unnamed record as
    the start of another walk let one line that lost its token, to an editor or
    a half-written flush, turn into a walk of its own at the end of the file and
    become the one read by default.
    """
    current = ""
    found = []
    for record in records:
        if record.outing:
            current = record.outing
        found.append((current, record))
    return found


def outings(records: Sequence[LogRecord]) -> list[str]:
    """Every walk a log holds, in the order they began."""
    found: list[str] = []
    for name, _ in _walks(records):
        if name not in found:
            found.append(name)
    return found


def records_for_outing(records: Sequence[LogRecord], outing: str | None = None) -> list[LogRecord]:
    """The records of one walk. Without a name, of the last walk in the file.

    `--log walk.jsonl` reused every week appends to the same pages, so a file is
    not a walk and reading it as one went wrong in two ways that both looked
    like an answer. The day came from the first scan in the file, so a notebook
    of the second walk was read against the first walk's date and every scan
    fell outside it. And the button marks of the two walks both counted from
    one, so the second walk's mark 1 took the place of the first walk's, and a
    notebook of `#1` and `#2` was given times from a walk on another day.
    """
    walks = _walks(records)
    if outing is None:
        wanted = walks[-1][0] if walks else ""
    else:
        wanted = outing
    return [record for name, record in walks if name == wanted]


def last_cycle(path: str | Path) -> int:
    """The highest cycle number already in a log, or 0 when there is none.

    A cycle number says which records are one look at one place, so it has to be
    unique inside the file and not merely inside the process that wrote it. A
    restart begins counting from one again, and without this the first cycle of
    the second run and the first of the first become one cycle, folding two
    positions ten minutes apart into a single scan at the earlier one.
    """
    try:
        records = read_log(path)
    except OSError:
        return 0
    return max([0, *(r.cycle for r in records if r.cycle is not None)])


def _refuse_constant(name: str) -> float:
    """Python's json reads NaN and Infinity happily. Nothing here can use them."""
    raise ValueError(f"{name} is not a number this can use")


def _unusable(line: str) -> int:
    """1 when the line is JSON this cannot use, 0 when it is not JSON at all.

    Junk is junk and costs only itself. A line that parses as JSON and carries
    NaN or an infinity is different: it was a record somebody's walk wrote, and
    a reader that drops it silently makes the file smaller without saying so.
    """
    try:
        json.loads(line)
    except ValueError:
        return 0
    return 1


def read_log_counting(path: str | Path) -> tuple[list[LogRecord], int]:
    """Parse a log, and say how many of its lines were JSON this cannot use.

    A line that cannot be read is a line that was never finished -- the battery
    went, the process was killed mid-write -- and it costs only itself. The
    count is for the one caller that has to name what it did not carry:
    `--export-public` publishes a copy of a walk, and a copy quietly shorter
    than the walk is the thing that command exists not to do.
    """
    records = []
    lost = 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            fields = json.loads(line, parse_constant=_refuse_constant)
        except ValueError:
            lost += _unusable(line)
            continue
        if isinstance(fields, dict):
            records.append(_record_from_json(fields))
        else:
            # JSON, and not a record. `[1, 2, 3]` is a line this cannot use as
            # much as one carrying a NaN is, and counting only the second made
            # the number smaller than the reason for having it.
            lost += 1
    return records, lost


def read_log(path: str | Path) -> list[LogRecord]:
    """Parse a log, skipping any line that is not a whole JSON object."""
    return read_log_counting(path)[0]


# --- Open networks -----------------------------------------------------------


@dataclass(frozen=True)
class OpenNetwork:
    """An unencrypted network in a log, as its strongest sighting saw it."""

    network: SeenNetwork
    time: datetime | None


def find_open_networks(path: str | Path) -> list[OpenNetwork]:
    """The unencrypted networks recorded in a log, strongest first.

    One entry per network, not per sighting: the same open network seen on
    forty scans is one place you could have connected, not forty.
    """
    best: dict[str, OpenNetwork] = {}
    for record in read_log(path):
        for network in record.networks:
            if not network.open or not network.identified:
                continue
            current = best.get(network.key)
            if current is None or network.strength > current.network.strength:
                best[network.key] = OpenNetwork(network, record.time)
    return sorted(best.values(), key=lambda found: found.network.strength, reverse=True)
