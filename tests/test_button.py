"""Tests for the headset button. No real input device is touched: devices are
FIFOs in a temporary directory, and sysfs is a directory tree built to order."""

import os
import time

import pytest

from enodia import button
from enodia.button import (
    EV_KEY,
    EV_SYN,
    INPUT_EVENT,
    KEY_NEXTSONG,
    KEY_PLAYPAUSE,
    MEDIA_KEYS,
    SYN_DROPPED,
    SYN_REPORT,
    WORD_BITS,
    ButtonMarker,
    InputDevice,
    event_age,
    find_button_devices,
    list_input_devices,
    parse_key_capabilities,
)

KEY_A = 30


def event(kind, code, value, at=0.0):
    """One `struct input_event`. `at` is the kernel's stamp of when it happened."""
    seconds, useconds = divmod(round(at * 1_000_000), 1_000_000)
    return INPUT_EVENT.pack(seconds, useconds, kind, code, value)


def bitmap_for(codes):
    """A sysfs `capabilities/key` string for these key codes."""
    bits = 0
    for code in codes:
        bits |= 1 << code
    words = []
    while bits:
        words.append(f"{bits & ((1 << WORD_BITS) - 1):x}")
        bits >>= WORD_BITS
    return " ".join(reversed(words)) or "0"


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for the button thread"
        time.sleep(0.01)


def fake_sysfs(root, devices):
    """devices: {eventN: (name, codes)}; a name of None means no device directory."""
    sysfs, dev = root / "sys", root / "dev"
    dev.mkdir()
    for entry, (name, codes) in devices.items():
        (sysfs / entry).mkdir(parents=True)
        if name is None:
            continue
        (sysfs / entry / "device" / "capabilities").mkdir(parents=True)
        (sysfs / entry / "device" / "name").write_text(name + "\n")
        (sysfs / entry / "device" / "capabilities" / "key").write_text(bitmap_for(codes) + "\n")
    return sysfs, dev


# --- sysfs ------------------------------------------------------------------------


def test_parse_key_capabilities_round_trips():
    assert parse_key_capabilities("") == frozenset()
    assert parse_key_capabilities("0") == frozenset()
    assert parse_key_capabilities("1") == {0}
    for codes in ({KEY_A}, {KEY_PLAYPAUSE}, {KEY_A, KEY_PLAYPAUSE, KEY_NEXTSONG, 300}):
        assert parse_key_capabilities(bitmap_for(codes)) == codes


def test_list_input_devices_reads_names_and_keys(tmp_path):
    sysfs, dev = fake_sysfs(
        tmp_path,
        {
            "event0": ("AT Translated Set 2 keyboard", {KEY_A, KEY_PLAYPAUSE}),
            "event1": ("Headset Buttons", {KEY_PLAYPAUSE}),
            "event2": ("Some Mouse", {272}),
            "event10": ("Late Device", {KEY_A}),
            "event3": (None, set()),
        },
    )
    devices = list_input_devices(sysfs, dev)
    assert [d.path.name for d in devices] == ["event0", "event1", "event2", "event3", "event10"]
    assert devices[1] == InputDevice(dev / "event1", "Headset Buttons", frozenset({KEY_PLAYPAUSE}))
    assert devices[3].name == "event3" and devices[3].keys == frozenset()


def test_find_button_devices_keeps_those_with_media_keys(tmp_path):
    sysfs, dev = fake_sysfs(
        tmp_path,
        {
            "event0": ("Keyboard", {KEY_A, KEY_PLAYPAUSE}),
            "event1": ("Headset", {KEY_NEXTSONG}),
            "event2": ("Mouse", {272}),
        },
    )
    assert [d.name for d in find_button_devices(sysfs, dev)] == ["Keyboard", "Headset"]
    assert all(d.has_media_keys for d in find_button_devices(sysfs, dev))


# --- listening --------------------------------------------------------------------


@pytest.fixture
def fifo(tmp_path):
    path = tmp_path / "event9"
    os.mkfifo(path)
    return path


def test_only_presses_of_media_keys_reach_the_callback(fifo):
    presses = []
    marker = ButtonMarker([fifo], on_press=lambda code, ago: presses.append(code), debounce=0.0)
    assert marker.open() == [fifo]
    writer = os.open(fifo, os.O_WRONLY)
    marker.start()
    try:
        os.write(
            writer,
            b"".join(
                [
                    event(EV_KEY, KEY_PLAYPAUSE, 1),  # press: counts
                    event(EV_KEY, KEY_PLAYPAUSE, 0),  # release: no
                    event(EV_KEY, KEY_PLAYPAUSE, 2),  # repeat: no
                    event(EV_KEY, KEY_A, 1),  # not a media key: no
                    event(EV_SYN, 0, 0),  # not a key at all
                    event(EV_KEY, KEY_NEXTSONG, 1),  # press: counts
                ]
            ),
        )
        wait_for(lambda: len(presses) == 2)
        assert presses == [KEY_PLAYPAUSE, KEY_NEXTSONG]
    finally:
        os.close(writer)
        marker.close()


def test_a_named_device_counts_any_key(fifo):
    presses = []
    marker = ButtonMarker(
        [str(fifo)], on_press=lambda code, ago: presses.append(code), keys=None, debounce=0.0
    )
    marker.open()
    writer = os.open(fifo, os.O_WRONLY)
    marker.start()
    try:
        os.write(writer, event(EV_KEY, KEY_A, 1))
        wait_for(lambda: presses == [KEY_A])
    finally:
        os.close(writer)
        marker.close()


def test_a_device_that_goes_away_is_reported(fifo):
    lost = []
    marker = ButtonMarker([fifo], on_lost=lost.append)
    marker.open()
    writer = os.open(fifo, os.O_WRONLY)
    marker.start()
    os.close(writer)  # the headset is unplugged: end of file
    wait_for(lambda: lost == [str(fifo)])
    wait_for(lambda: not marker._fds)  # and the descriptor was released
    marker.close()


def test_debounce_makes_a_nervous_thumb_one_press():
    ticks = iter([0.0, 0.4, 0.9, 1.5, 1.6])
    presses = []
    marker = ButtonMarker(
        [], on_press=lambda code, ago: presses.append(code), debounce=1.0, clock=lambda: next(ticks)
    )
    assert [marker.press(KEY_PLAYPAUSE) for _ in range(5)] == [True, False, False, True, False]
    assert presses == [KEY_PLAYPAUSE, KEY_PLAYPAUSE]


def test_a_press_with_nobody_listening_is_fine():
    marker = ButtonMarker([], debounce=0.0)
    assert marker.press(KEY_PLAYPAUSE)


def test_open_without_permission_says_which_group(tmp_path, monkeypatch):
    def refuse(path, flags):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(button.os, "open", refuse)
    marker = ButtonMarker([tmp_path / "event0", tmp_path / "event1"])
    with pytest.raises(PermissionError, match="'input' group"):
        marker.open()


def test_open_keeps_what_it_can(tmp_path, fifo, monkeypatch, capsys):
    real_open = os.open

    def open_some(path, flags):
        if str(path).endswith("event0"):
            raise PermissionError(13, "Permission denied")
        return real_open(path, flags)

    monkeypatch.setattr(button.os, "open", open_some)
    marker = ButtonMarker([tmp_path / "event0", fifo, tmp_path / "missing"])
    assert marker.open() == [fifo]  # denied and missing are skipped, not fatal
    assert "Could not open" in capsys.readouterr().out
    marker.close()
    assert not marker._fds


def test_start_with_nothing_to_listen_to_ends_at_once():
    marker = ButtonMarker([])
    marker.start()
    marker._thread.join(timeout=5)
    assert not marker._thread.is_alive()
    marker.close()


def test_media_keys_are_the_ones_a_headset_sends():
    assert {KEY_PLAYPAUSE, KEY_NEXTSONG} <= MEDIA_KEYS
    assert KEY_A not in MEDIA_KEYS
    assert INPUT_EVENT.size in (16, 24)  # 32- or 64-bit struct input_event


def test_a_read_error_is_a_lost_device_too(fifo, monkeypatch):
    # A headset unplugged mid-walk: the kernel answers the next read with an
    # error, not with end of file.
    lost = []
    marker = ButtonMarker([fifo], on_lost=lost.append)
    marker.open()
    writer = os.open(fifo, os.O_WRONLY)
    real_read = os.read

    def unplugged(fd, size):
        if fd in marker._fds:
            raise OSError(19, "No such device")
        return real_read(fd, size)

    monkeypatch.setattr(button.os, "read", unplugged)
    marker.start()
    os.write(writer, event(EV_KEY, KEY_PLAYPAUSE, 1))  # wakes select; the read then fails
    wait_for(lambda: lost == [str(fifo)])
    os.close(writer)
    marker.close()
    assert not marker._fds


def drained(marker, *events):
    """Feed raw events through one read, the way a buffered device arrives."""
    read, write = os.pipe()
    os.write(write, b"".join(events))
    os.close(write)
    try:
        marker._drain(read)
    finally:
        os.close(read)


def test_two_presses_read_together_are_timed_by_when_they_happened():
    # The kernel stamps an event with when it happened, and this used to throw
    # that away and time the press by when the listener got round to reading it.
    # Two presses buffered into one read then looked simultaneous and the second
    # was debounced out of existence. The button is not decoration here: every
    # press is a crossing, and a crossing lost moves everything after it.
    presses = []
    marker = ButtonMarker(
        [],
        on_press=lambda code, ago: presses.append(code),
        debounce=1.0,
        clock=lambda: 100.0,  # el reloj de proceso no se mueve entre las dos
        age_of=lambda at: None,  # ningun reloj reclama un sello inventado
    )
    drained(
        marker,
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=10.0),
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=12.0),
    )
    assert presses == [KEY_PLAYPAUSE, KEY_PLAYPAUSE]


def test_a_nervous_thumb_is_still_one_press_by_the_events_own_clock():
    presses = []
    marker = ButtonMarker(
        [],
        on_press=lambda code, ago: presses.append(code),
        debounce=1.0,
        clock=lambda: 100.0,
        age_of=lambda at: None,
    )
    drained(
        marker,
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=10.0),
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=10.2),
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=11.5),
    )
    assert presses == [KEY_PLAYPAUSE, KEY_PLAYPAUSE]


def test_an_event_that_carries_no_time_is_timed_by_the_reading_clock():
    # A zero timeval says nothing, and reading it as "the same instant as the
    # last one" would lose exactly the presses this exists to keep. The two
    # clocks are also never compared against each other: a press timed one way
    # is never debounced against a press timed the other.
    # A clock that moves a tenth of a second every time it is read, which the
    # listener now reads for the batch anchor as well as for the debounce.
    tick = [100.0]

    def clock():
        tick[0] += 0.1
        return tick[0]

    presses = []
    marker = ButtonMarker(
        [], on_press=lambda code, ago: presses.append(code), debounce=1.0, clock=clock
    )
    drained(marker, event(EV_KEY, KEY_PLAYPAUSE, 1), event(EV_KEY, KEY_PLAYPAUSE, 1))
    assert presses == [KEY_PLAYPAUSE]  # ambas por el reloj de lectura, y muy juntas

    # And now one of each, a tenth of a second apart on two different clocks.
    drained(marker, event(EV_KEY, KEY_PLAYPAUSE, 1, at=100.15))
    assert presses == [KEY_PLAYPAUSE, KEY_PLAYPAUSE]


def test_a_press_that_waited_in_the_buffer_is_still_written_when_it_happened():
    # The last press of a batch was taken to have happened now, which holds
    # while the reads keep up and not when the process was off the CPU for five
    # seconds: one stale press arrived with no age at all and was recorded five
    # seconds late. Which clock evdev stamped it with is determined, not
    # assumed, by asking each one and seeing which puts it in the recent past.
    seen = []
    marker = ButtonMarker([], on_press=lambda code, ago: seen.append(ago), debounce=0.0)
    drained(marker, event(EV_KEY, KEY_PLAYPAUSE, 1, at=time.time() - 5.0))
    assert seen[0] == pytest.approx(5.0, abs=0.5)


def test_an_evdev_timestamp_is_read_by_whichever_clock_claims_it():
    assert event_age(time.time() - 3.0) == pytest.approx(3.0, abs=0.5)
    assert event_age(time.clock_gettime(time.CLOCK_MONOTONIC) - 3.0) == pytest.approx(3.0, abs=0.5)
    # A timestamp from the future, and one older than any press in a buffer.
    assert event_age(time.time() + 60.0) is None
    assert event_age(1.0, clocks=(time.CLOCK_REALTIME,)) is None


def test_a_press_after_the_kernel_dropped_input_is_not_a_crossing():
    # SYN_DROPPED is the kernel saying its queue overran and it threw input
    # away. What came before it is still good. What comes after, until the next
    # SYN_REPORT, is the tail of a packet whose head is gone, and counting it
    # would put a crossing where there was none. A lost press is noticed by the
    # person who pressed it; an invented one never is.
    presses = []
    told = []
    marker = ButtonMarker(
        [],
        on_press=lambda code, ago: presses.append(code),
        on_lost_events=lambda: told.append("overran"),
        debounce=0.0,
        age_of=lambda at: None,
    )
    drained(
        marker,
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=10.0),
        event(EV_SYN, SYN_DROPPED, 0, at=11.0),
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=11.5),
        event(EV_SYN, SYN_REPORT, 0, at=12.0),
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=13.0),
    )
    assert presses == [KEY_PLAYPAUSE, KEY_PLAYPAUSE]  # la de antes y la de después del SYN_REPORT
    assert told == ["overran"]


def test_an_ordinary_syn_report_is_not_an_overrun():
    presses = []
    told = []
    marker = ButtonMarker(
        [],
        on_press=lambda code, ago: presses.append(code),
        on_lost_events=lambda: told.append("overran"),
        debounce=0.0,
        age_of=lambda at: None,
    )
    drained(
        marker,
        event(EV_KEY, KEY_PLAYPAUSE, 1, at=10.0),
        event(EV_SYN, SYN_REPORT, 0, at=10.0),
    )
    assert presses == [KEY_PLAYPAUSE] and told == []


def test_an_overrun_with_nobody_listening_is_fine():
    marker = ButtonMarker([], debounce=0.0, age_of=lambda at: None)
    drained(marker, event(EV_SYN, SYN_DROPPED, 0, at=10.0))


def test_a_broken_packet_is_still_broken_in_the_next_read():
    # The rule is to ignore events up to and including the next SYN_REPORT, and
    # that boundary does not care where a read happens to end: the drop can
    # arrive in one and its SYN_REPORT in the next.
    presses = []
    told = []
    marker = ButtonMarker(
        [],
        on_press=lambda code, ago: presses.append(code),
        on_lost_events=lambda: told.append("overran"),
        debounce=0.0,
        age_of=lambda at: None,
    )
    drained(marker, event(EV_SYN, SYN_DROPPED, 0, at=10.0))
    drained(marker, event(EV_KEY, KEY_PLAYPAUSE, 1, at=11.0), event(EV_SYN, SYN_REPORT, 0, at=11.0))
    assert presses == [] and told == ["overran"]

    # And once the packet has been closed, the device is itself again.
    drained(marker, event(EV_KEY, KEY_PLAYPAUSE, 1, at=12.0))
    assert presses == [KEY_PLAYPAUSE]


def test_a_device_that_goes_away_takes_its_broken_packet_with_it(tmp_path):
    # The file descriptor is about to be free for the kernel to hand out again,
    # and the next device to get that number has nothing to do with this one.
    marker = ButtonMarker([], debounce=0.0, age_of=lambda at: None)
    read, write = os.pipe()
    marker._fds[read] = tmp_path / "event9"
    os.write(write, event(EV_SYN, SYN_DROPPED, 0, at=10.0))
    os.close(write)
    marker._drain(read)
    assert marker._desynced == {read}
    marker._drain(read)  # end of file: the device went away
    assert marker._desynced == set()
