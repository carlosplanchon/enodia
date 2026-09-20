"""A headset button as a crossing marker.

The notebook's weak point is the time: heard through headphones, written by
hand, one misheard digit moves a whole stretch of the route. A headset has a
button, and Linux shows it as an input device that reports every press with a
timestamp. Press it at each crossing and the time is the machine's; the
notebook only needs the crossing names, in order, one per press. Enodia says
"Mark 7" on each press, so the paper line and the log record share a number.

The devices are read straight from /dev/input, with no library in between: the
kernel's `struct input_event` is a stable ABI, and the sysfs `capabilities/key`
bitmap says which devices have media keys at all. Reading /dev/input has to be
allowed -- usually by membership of the `input` group -- and needs no root.

Whether a 3.5 mm headset's button reaches the kernel depends on the sound codec;
USB and Bluetooth headsets report theirs reliably. `evtest /dev/input/eventN`
shows what a device actually sends.
"""

from __future__ import annotations

import os
import select
import struct
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
INPUT_EVENT = struct.Struct("llHHi")
# evdev hands over a timestamp and does not say which clock it came from:
# CLOCK_REALTIME unless the device was asked for another one. They are told
# apart by asking each what time it is and seeing which puts the event in the
# recent past, and they cannot both: one counts from 1970 and the other from
# boot. An hour is the oldest a press in a buffer could sensibly be.
EVENT_CLOCKS = tuple(
    getattr(time, name)
    for name in ("CLOCK_REALTIME", "CLOCK_MONOTONIC", "CLOCK_BOOTTIME")
    if hasattr(time, name)
)
OLDEST_EVENT = 3600.0
WORD_BITS = 8 * struct.calcsize("l")  # one word of the sysfs capability bitmap

# Event type and key codes, from linux/input-event-codes.h.
EV_KEY = 0x01
KEY_PRESSED = 1
# The kernel's own way of saying it dropped input on the floor. A client that
# gets SYN_DROPPED has to ignore what follows until the next SYN_REPORT and
# ask the device what state it is in, because the stream in between is a
# fragment of a picture it no longer has. Here it means something blunter: a
# press may have been lost, and a press is a street corner.
EV_SYN = 0x00
SYN_REPORT = 0
SYN_DROPPED = 3
KEY_NEXTSONG = 163
KEY_PLAYPAUSE = 164
KEY_PREVIOUSSONG = 165
KEY_PLAYCD = 200
KEY_PAUSECD = 201
KEY_MEDIA = 226
MEDIA_KEYS = frozenset(
    {
        KEY_NEXTSONG,
        KEY_PLAYPAUSE,
        KEY_PREVIOUSSONG,
        KEY_PLAYCD,
        KEY_PAUSECD,
        KEY_MEDIA,
    }
)

SYSFS_INPUT = Path("/sys/class/input")
DEV_INPUT = Path("/dev/input")
DEBOUNCE_SECONDS = 1.0


def parse_key_capabilities(bitmap: str) -> frozenset[int]:
    """The key codes a device can report, from its sysfs `capabilities/key` file.

    The file is the bitmap as space-separated hexadecimal words, most
    significant first, one native long each.
    """
    bits = 0
    for word in bitmap.split():
        bits = (bits << WORD_BITS) | int(word, 16)
    return frozenset(code for code in range(bits.bit_length()) if bits >> code & 1)


@dataclass(frozen=True)
class InputDevice:
    """One /dev/input/eventN, as sysfs describes it."""

    path: Path
    name: str
    keys: frozenset[int]

    @property
    def has_media_keys(self) -> bool:
        return bool(self.keys & MEDIA_KEYS)


def list_input_devices(sysfs: Path = SYSFS_INPUT, devices: Path = DEV_INPUT) -> list[InputDevice]:
    """Every input device the kernel lists, with its name and key capabilities."""
    found = []
    for entry in sorted(sysfs.glob("event*"), key=lambda p: (len(p.name), p.name)):
        name_file = entry / "device" / "name"
        caps_file = entry / "device" / "capabilities" / "key"
        name = name_file.read_text(errors="replace").strip() if name_file.exists() else entry.name
        keys = parse_key_capabilities(caps_file.read_text()) if caps_file.exists() else frozenset()
        found.append(InputDevice(devices / entry.name, name, keys))
    return found


def find_button_devices(sysfs: Path = SYSFS_INPUT, devices: Path = DEV_INPUT) -> list[InputDevice]:
    """The devices with media keys: headsets, and also the laptop's own keyboard.

    All of them are listened to at once, so there is nothing to choose. A press
    of the keyboard's play key marks a crossing too, which does no harm from
    inside a closed backpack.
    """
    return [device for device in list_input_devices(sysfs, devices) if device.has_media_keys]


def event_age(at: float, clocks: Sequence[int] = EVENT_CLOCKS) -> float | None:
    """How long ago an evdev timestamp was, or None when no clock claims it.

    Determined and not assumed. Without this, the last press found in a buffer
    is taken to have happened now, which holds while the reads keep up and not
    when the process was off the CPU for five seconds: one stale press then
    arrived with no age at all and was written five seconds late. Falling back
    to that when no clock recognises the timestamp keeps the old behaviour for
    the case it was right about, rather than inventing an age.
    """
    for clock in clocks:
        age = time.clock_gettime(clock) - at
        if 0.0 <= age < OLDEST_EVENT:
            return age
    return None


class ButtonMarker:
    """Listens to input devices from its own thread and calls back on every press.

    `keys` limits which key codes count. None means any key on the device does,
    which is what an explicitly named device gets. Presses closer together than
    `debounce` seconds are one press: a nervous thumb is not two crossings.
    """

    def __init__(
        self,
        devices: Iterable[InputDevice | Path | str],
        on_press: Callable[[int, float], object] | None = None,
        keys: frozenset[int] | None = MEDIA_KEYS,
        on_lost: Callable[[str], None] | None = None,
        on_lost_events: Callable[[], None] | None = None,
        debounce: float = DEBOUNCE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        age_of: Callable[[float], float | None] = event_age,
    ) -> None:
        self.paths = [Path(d.path if isinstance(d, InputDevice) else d) for d in devices]
        self.on_press = on_press
        self.on_lost = on_lost
        self.on_lost_events = on_lost_events
        self.keys = keys
        self.debounce = debounce
        self._clock = clock
        self._age_of = age_of
        self._fds: dict[int, Path] = {}
        # Which devices are still inside a packet the kernel said it broke. The
        # rule is to ignore events up to and including the next SYN_REPORT, and
        # that boundary does not care where a read happens to end: the drop can
        # arrive in one and its SYN_REPORT in the next.
        self._desynced: set[int] = set()
        self._last_press: tuple[bool, float] | None = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def open(self) -> list[Path]:
        """Open every device that can be opened, and return those.

        Raises PermissionError only when no device could be opened and at
        least one refused for lack of permission: that is the `input` group
        missing, and worth saying so.
        """
        denied: list[Path] = []
        for path in self.paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except PermissionError:
                denied.append(path)
                continue
            except OSError as exc:
                print(f"Could not open {path}: {exc}")
                continue
            self._fds[fd] = path
        if not self._fds and denied:
            raise PermissionError(
                f"no permission to read {', '.join(map(str, denied))}: "
                f"reading /dev/input usually needs membership of the 'input' group"
            )
        return list(self._fds.values())

    def start(self) -> None:
        """Start listening. `open()` first, or there is nothing to listen to."""
        self._stopping.clear()
        self._thread = threading.Thread(target=self._listen, name="enodia-button", daemon=True)
        self._thread.start()

    def _listen(self) -> None:
        while self._fds and not self._stopping.is_set():
            ready, _, _ = select.select(list(self._fds), [], [], 0.5)
            for fd in ready:
                self._drain(fd)

    def _drain(self, fd: int) -> None:
        try:
            data = os.read(fd, INPUT_EVENT.size * 64)
        except OSError as exc:  # the device went away: a headset unplugged
            self._lost(fd, str(exc))
            return
        if not data:  # end of file: nothing more will ever come from it
            self._lost(fd, "end of file")
            return
        presses = []
        # After a SYN_DROPPED the kernel is telling us its queue overran and it
        # threw input away. What came before it is still good; what comes after,
        # until the next SYN_REPORT, is the tail of a packet whose head is gone.
        # Counting those as presses would put a crossing where there was none,
        # which is worse than the press that was lost, because a lost press is
        # noticed and an invented one is not.
        after_a_drop = fd in self._desynced
        overran = False
        for offset in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
            seconds, useconds, kind, code, value = INPUT_EVENT.unpack_from(data, offset)
            if kind == EV_SYN:
                if code == SYN_DROPPED:
                    after_a_drop = overran = True
                elif code == SYN_REPORT:
                    after_a_drop = False
                continue
            if after_a_drop:
                continue
            if kind == EV_KEY and value == KEY_PRESSED and (self.keys is None or code in self.keys):
                presses.append((code, seconds + useconds / 1_000_000))
        # Carried to the next read: the SYN_REPORT that ends the broken packet may
        # not be in this one.
        if after_a_drop:
            self._desynced.add(fd)
        else:
            self._desynced.discard(fd)
        # The last press of a batch is the one being handled now, so each of the
        # others happened the difference between the two ago. A difference
        # between two event timestamps is the same number in any clock, which is
        # what lets this reach the log at all: turning one of them into a time of
        # day would mean deciding which clock evdev handed over, and nothing here
        # has established that. What the log gets is how long ago, from a moment
        # it dates itself.
        latest = presses[-1][1] if presses else 0.0
        # How long ago the last press of the batch happened, from whichever
        # clock claims its timestamp, so that a press that waited in a buffer
        # while the process was off the CPU is still written when it happened.
        # None means no clock recognised it, and then the last press is read as
        # now, which is what every press was read as before any of this.
        settled = self._age_of(latest) if latest else None
        # One anchor for the batch. Each mark dates itself from its own reading
        # of the time of day, and the callbacks before it have already moved
        # that reading on, so two presses two seconds apart came out three
        # seconds apart whenever the pair straddled a second boundary. Adding
        # what the earlier callbacks took cancels exactly that.
        started = self._clock() if len(presses) > 1 else 0.0
        for index, (code, at) in enumerate(presses):
            lag = self._clock() - started if index else 0.0
            if settled is not None:
                gap = settled + (latest - at)
            else:
                gap = latest - at if at and latest else 0.0
            self.press(code, at, max(gap, 0.0) + lag)
        if overran and self.on_lost_events is not None:
            # Said and not fixed. Re-reading the device's state would tell us
            # which keys are down now, and nothing at all about the press that
            # went missing while the queue was full, which is the only part
            # anybody here cares about.
            self.on_lost_events()

    def _lost(self, fd: int, why: str) -> None:
        path = self._fds.pop(fd)
        # The number is about to be free for the kernel to hand out again, and
        # the next device to get it has nothing to do with this one's broken
        # packet.
        self._desynced.discard(fd)
        os.close(fd)
        print(f"Lost {path}: {why}")
        if self.on_lost is not None:
            self.on_lost(str(path))

    def press(self, code: int, at: float | None = None, ago: float = 0.0) -> bool:
        """Count a press unless it is within `debounce` of the last one.

        `at` is when the press happened, off the event the kernel delivered. It
        is compared only against another event's time and never against the
        clock on the wall, so which clock evdev hands over is a question this
        does not have to answer: an interval is an interval in any of them. What
        it must not do is mix the two, so a press timed one way is never
        debounced against a press timed the other, and an event whose timestamp
        is missing or zero is timed by the reading clock as before.

        `ago` is how long before now the press happened, which is what the
        listener works out from the batch it came in and passes on. The last
        press of a batch is taken as happening now, which is the same assumption
        this made about every press before any of it carried a timestamp: the
        interval between two of them is what the kernel actually establishes.
        It is also what the
        walk's record of it is dated by, so that a crossing is written at the press
        and not to the read that delivered it. Two crossings a couple of seconds
        apart, drained together, used to be written at the same second, and a
        block of no length is not a block anything can be placed along.
        """
        from_event = bool(at)
        now = at if from_event else self._clock()
        last = self._last_press
        if last is not None and last[0] == from_event and now - last[1] < self.debounce:
            return False
        self._last_press = (from_event, now)
        if self.on_press is not None:
            self.on_press(code, ago)
        return True

    def close(self, timeout: float = 2.0) -> None:
        """Stop listening and release the devices."""
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        for fd in list(self._fds):
            os.close(fd)
        self._fds.clear()
