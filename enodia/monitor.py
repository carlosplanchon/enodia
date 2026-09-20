"""The scan loop: announce connection changes, signal and new networks, and log every scan."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime
from threading import Event, Thread
from typing import Any

import ifpeek

from enodia.button import ButtonMarker
from enodia.netlog import (
    MARK_EVENT,
    NetworkLog,
    last_cycle,
    last_outing,
    read_log,
    records_for_outing,
)
from enodia.system import battery
from enodia.voice import Speaker, VoiceController

SCAN_FAILURE_REMINDER = (
    12  # cycles between "still no scan" reminders: a minute at the default interval
)
SUSPEND_THRESHOLD = 1.0  # seconds the two clocks may disagree by before it counts as a suspend
BATTERY_WARNINGS = (20, 10)  # percent: said once each, on the way down


def _boottime() -> float:
    """Seconds since boot, time asleep included. Falls back to the monotonic
    clock where the kernel offers nothing better, and then never sees a suspend."""
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


def _count(number: int, unit: str) -> str:
    return f"{number} {unit}" if number == 1 else f"{number} {unit}s"


def describe_duration(seconds: float) -> str:
    """'45 seconds', '12 minutes', '2 hours, 5 minutes': a duration for the ear."""
    whole = round(seconds)
    if whole < 60:
        return _count(whole, "second")
    minutes, _ = divmod(whole, 60)
    if minutes < 60:
        return _count(minutes, "minute")
    hours, minutes = divmod(minutes, 60)
    return f"{_count(hours, 'hour')}, {_count(minutes, 'minute')}"


def network_key(ap: Any) -> str:
    """Identity of a scanned network: its BSSID, or the SSID when the backend (iwd) gives none."""
    return ap.bssid or ap.ssid


class WifiMonitor:
    """Polls the Wi-Fi interfaces and reports what changed, by voice and in the log."""

    def __init__(
        self,
        voice: Speaker | None = None,
        log: NetworkLog | None = None,
        interfaces: Sequence[str] | None = None,
        time_between_scans: float = 5,
        lang: str = "en-US",
        ssid_lang: str = "es-ES",
        log_every_scan: bool = True,
        speak_status: bool = False,
        speak_signal: bool = False,
        say_time_every: float = 0,
        quiet: bool = False,
        speak_time: bool = True,
        fresh_scan: bool = True,
        speak_names_up_to: int = 3,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        boot_clock: Callable[[], float] | None = None,
        battery_reader: Callable[[], tuple[int, str] | None] = battery,
        rfkill_reader: Callable[[str], str | None] | None = None,
    ) -> None:
        """
        :param interfaces: Wi-Fi interfaces to watch; None discovers them each cycle.
        :param lang: language of the announcements.
        :param ssid_lang: language used to pronounce network names.
        :param log_every_scan: record the full access point list on every cycle,
            not only connection changes and new networks.
        :param speak_status: also say the per-cycle status ("Scanning" and the
            time). Events (connection changes, new networks) are always spoken;
            status messages are only printed unless this is set.
        :param speak_signal: also say the signal quality every cycle. Off on its
            own: it is 1.7 seconds of a number that seldom changes.
        :param say_time_every: say the time every this many seconds, as an event
            that is never skipped, even while network names are being read. It
            may then be heard a few seconds after the moment it names. 0 leaves
            the time to `speak_status`, once per cycle and only while the voice
            is free. With both set, this one replaces the per-cycle time.
        :param quiet: say only the time, the button's marks and the failures.
            Nothing about networks or connections: those are in the log. The
            time is said every cycle, or every `say_time_every` seconds.
        :param speak_time: False silences the time everywhere, `quiet` included.
            With a headset button the marks carry the time, and the only thing
            the spoken time was still for is a sign of life.
        :param fresh_scan: ask the Wi-Fi daemon to scan on every cycle, about five
            seconds each. Off, it reads the daemon's current view, which is whatever
            the last full scan saw: cheaper, but stale by days when the laptop
            travelled asleep, and iwd rarely scans by itself while connected.
        :param speak_names_up_to: name new networks one by one up to this many
            per cycle; beyond it, say the count and name only the open ones.
        :param clock: source of monotonic time, used to sleep only what is left
            of `time_between_scans` after the cycle's own work.
        :param sleep: how to wait between cycles. The default waits on the stop
            flag, so `stop()` cuts the wait short instead of leaving the loop
            asleep for up to a whole interval.
        :param boot_clock: a clock that keeps counting while the machine is
            suspended, unlike `clock`; the two drifting apart is how a suspend
            is noticed. Defaults to CLOCK_BOOTTIME.
        :param battery_reader: where the charge comes from, for the warnings at
            BATTERY_WARNINGS percent.
        :param rfkill_reader: whether the radio behind an interface is switched
            off: "hard", "soft" or None. The kernel's rfkill switches by default.
        """
        self.voice: Speaker = voice if voice is not None else VoiceController()
        self.log = log if log is not None else NetworkLog()
        self.interfaces = list(interfaces) if interfaces else None
        self.time_between_scans = time_between_scans
        self.lang = lang
        self.ssid_lang = ssid_lang
        self.log_every_scan = log_every_scan
        self.speak_status = speak_status
        self.speak_signal = speak_signal
        self.say_time_every = say_time_every
        self._time_last_said: float | None = None
        self.quiet = quiet
        self.speak_time = speak_time
        self.fresh_scan = fresh_scan
        self.speak_names_up_to = speak_names_up_to
        self._stopping = Event()
        self._sleep = sleep if sleep is not None else self._wait
        self._clock = clock
        # On the real monotonic clock the boot clock is the real one too. With a
        # stand-in `clock` and no stand-in to pair it with, suspend detection is
        # simply off: measuring a fake monotonic clock against the real boot
        # clock would read as a suspend of hours.
        if boot_clock is None and clock is time.monotonic:
            boot_clock = _boottime
        self._boot_clock: Callable[[], float] | None = boot_clock
        self._last_tick: tuple[float, float] | None = None
        self._battery = battery_reader
        self._rfkill = rfkill_reader
        self._battery_warned: set[int] = set()
        self._scan_failures: dict[str, int] = {}
        # Taken from the log rather than started at zero: `--resume`, and a
        # `--log FILE` written to a second time, both carry on into a file that
        # already has cycles in it, and a number used twice folds two places
        # into one scan at the earlier of them. Unique per file, not per process.
        self._cycle = last_cycle(self.log.path)
        self._thread: Thread | None = None
        self.button: ButtonMarker | None = None
        self.marks = 0
        self.old_ap: dict[str, tuple[str | None, str | None]] = {}
        self.scanned_networks: set[str] = set()

    def _wait(self, seconds: float) -> None:
        """Wait between cycles, waking at once if the loop was asked to stop."""
        self._stopping.wait(seconds)

    def stop(self) -> None:
        """Ask the scan loop to finish after the current cycle."""
        self._stopping.set()

    def wifi_interfaces(self) -> list[str]:
        """The interfaces to watch this cycle."""
        return list(self.interfaces) if self.interfaces else ifpeek.get_wifi_interfaces()

    @staticmethod
    def actual_time(now: datetime | None = None) -> str:
        """The time as Enodia says it: '17 hours, 25 minutes, 10 seconds'.

        On the 24-hour clock, because what is heard gets written in the notebook
        and the notebook is read on the 24-hour clock (see `enodia.reconcile`).
        """
        if now is None:
            # Local wall-clock time, naive on purpose: it is what gets said and written down.
            now = datetime.now()  # noqa: DTZ005
        return f"{now.hour} hours, {now.minute} minutes, {now.second} seconds"

    @staticmethod
    def association_signal(interface: str) -> dict[str, int | None]:
        """How the access point `interface` is associated to comes through.

        The same fields a scanned network carries, so an association can be
        compared with the scans around it.
        """
        return {
            "frequency": ifpeek.access_point_frequency(interface),
            "signal_dbm": ifpeek.access_point_signal_dbm(interface),
            "signal_percent": ifpeek.access_point_signal_percent(interface),
        }

    def say_actual_network(
        self,
        actual_network: str,
        bssid: str | None,
        interface: str,
    ) -> None:
        """Announce and log the network an interface is now associated to."""
        self.voice.say("Now connected to", lang=self.lang, silent=self.quiet)
        self.voice.say(actual_network, lang=self.ssid_lang, silent=self.quiet)
        self.log.record_connected(
            actual_network, bssid, interface, **self.association_signal(interface)
        )

    def check_connection(self, interface: str) -> None:
        """Announce association changes on `interface`, or its signal quality if unchanged.

        An association is identified by ESSID *and* BSSID, so moving between two
        access points of the same extended network -- two `Ceibal` on the same
        street -- is seen and recorded. A roam is only logged and printed, never
        spoken unless `speak_status` is set: the network's name has not changed,
        and the audio channel is the scarce one.
        """
        essid = ifpeek.access_point_essid(interface)
        bssid = ifpeek.access_point_mac_address(interface)
        # An interface never seen before counts as disconnected, so starting up
        # with no association announces nothing.
        previous = self.old_ap.get(interface, (None, None))
        if (essid, bssid) != previous:
            if essid is None:
                self.voice.say("Now disconnected", lang=self.lang, silent=self.quiet)
                self.log.record_disconnected(interface)
            elif previous[0] == essid:
                self.voice.say(
                    f"Roaming on {essid}",
                    lang=self.lang,
                    silent=self.quiet or not self.speak_status,
                )
                self.log.record_connected(
                    essid, bssid, interface, **self.association_signal(interface)
                )
            else:
                self.say_actual_network(essid, bssid, interface)
            self.old_ap[interface] = (essid, bssid)
        elif essid:
            signal_percent = ifpeek.access_point_signal_percent(interface)
            self.voice.say(
                f"Signal quality is {signal_percent}",
                lang=self.lang,
                silent=self.quiet or not self.speak_signal,
                status=True,
            )

    def resume_from_log(self) -> int:
        """Seed the networks already seen from the log, and return how many were recovered.

        An outing that is interrupted and restarted should not announce every
        network again: the log is the memory of what has been seen. Networks
        recorded by earlier outings into the same log count as seen too. The
        mark count carries on as well, so the numbers said after a restart do
        not collide with the ones already in the notebook. And the outing is the
        one already in the file: this is the same walk, interrupted, and the map
        has to see it as one.
        """
        try:
            records = read_log(self.log.path)
        except OSError:
            return 0
        carried = last_outing(self.log.path)
        if carried:
            self.log.outing = carried
        # Every network in the file, deliberately: an outing that walked past
        # this one's street last week already said those names out loud, and the
        # point of the whole file is not to say them again. An anonymous network
        # answers to the empty key here, so the first one heard stands for all of
        # them and the rest are not announced. Nothing can tell them apart, and
        # announcing one every cycle for four hours would be worse than quiet.
        recovered = {network.key for record in records for network in record.networks}
        recovered -= self.scanned_networks
        self.scanned_networks |= recovered
        # The marks, though, are this walk's alone. Every run numbers them from
        # one, so an older outing in the same file that reached mark 100 used to
        # make this one carry on at 101, and the paper says 3.
        mine = records_for_outing(records, carried)
        self.marks = max(
            [self.marks, *(r.number for r in mine if r.event == MARK_EVENT and r.number)]
        )
        return len(recovered)

    def button_overran(self) -> None:
        """The kernel dropped input. Say so, and write it down.

        A press that never arrived is a crossing that never arrived, and the
        numbers said afterwards carry on as if nothing had gone missing, so the
        paper and the log agree with each other and both are short a corner.
        Nothing here can recover it. What it can do is refuse to let it pass
        quietly, and leave a record where the reconciliation will run into it.
        """
        print("The button's queue overran: a mark may be missing.")
        self.log.record_button_lost("SYN_DROPPED")
        self.voice.say("Button overflow, a mark may be missing", lang=self.lang)

    def mark(self, key: int | None = None, ago: float = 0.0) -> int:
        """A press of the headset button: a crossing, passed now.

        Says the number so that the operator writes it next to the crossing's
        name: "Mark 7" on the headphones, "7 Agraciada y Freire" on the paper,
        and the reconciliation joins the two by the number.
        """
        self.marks += 1
        print(f"Mark {self.marks}.")
        self.log.record_mark(self.marks, key, ago)
        self.voice.say(f"Mark {self.marks}", lang=self.lang)
        return self.marks

    def use_button(self, button: ButtonMarker) -> None:
        """Take presses from `button` as marks, and stop it on `close()`."""
        button.on_press = self.mark
        if button.on_lost is None:
            button.on_lost = lambda path: self.voice.say("Button lost", lang=self.lang)
        if button.on_lost_events is None:
            button.on_lost_events = self.button_overran
        self.button = button
        button.start()

    def radio_blocked(self, interface: str) -> str | None:
        """ "hard", "soft" or None: whether rfkill has the radio behind `interface` off.

        What distinguishes a street with no Wi-Fi from a radio that was switched
        off by an airplane-mode key knocked inside the backpack: both scan to
        nothing. The interface's operstate is not it: that is the association,
        and a whole outing is walked joined to no network, DOWN to the kernel,
        with forty networks in view.
        """
        reader = self._rfkill if self._rfkill is not None else ifpeek.interface_rfkill
        try:
            return reader(interface)
        # Anything at all: sysfs misbehaving is not where the walk ends.
        except Exception as exc:  # noqa: BLE001
            print(f"Could not read the radio switch of {interface}: {exc}")
            return None

    def check_battery(self) -> None:
        """Say when the battery crosses each of BATTERY_WARNINGS on the way down.

        Four hours in a backpack is a battery's worth, and the screen that would
        show the gauge is shut. Said once per threshold, and not at all while
        charging: a laptop on mains is not in a backpack.
        """
        reading = self._battery()
        if reading is None:
            return
        percent, status = reading
        if status in ("Charging", "Full", "Not charging"):  # on mains, one way or another
            return
        for threshold in BATTERY_WARNINGS:
            if percent <= threshold and threshold not in self._battery_warned:
                self._battery_warned.add(threshold)
                print(f"Battery at {percent} percent.")
                self.voice.say(f"Battery at {percent} percent", lang=self.lang)
                break

    def scan_access_points(self, interface: str) -> list[Any] | None:
        """Nearby access points as the Wi-Fi daemon sees them; None if it could not scan."""
        try:
            ap_list = ifpeek.scan_access_points(interface=interface, fresh=self.fresh_scan)
        # Anything at all: no daemon, no [scan] extra, D-Bus refusing -- the loop must go on.
        except Exception as exc:  # noqa: BLE001
            self.scan_failed(interface, str(exc))
            return None
        self.scan_recovered(interface)
        return ap_list

    def scan_failed(self, interface: str, reason: str, spoken: str = "Scan failed") -> None:
        """A cycle without a scan is a hole in the record: say so, and keep saying so.

        The reason is printed every time, but the screen is shut inside a
        backpack. So it is also spoken -- `spoken` the first time, and then once
        every SCAN_FAILURE_REMINDER cycles while it lasts -- because a heartbeat
        that sounds healthy over a log filling with nothing is the one failure
        the operator cannot hear.
        """
        failures = self._scan_failures.get(interface, 0)
        self._scan_failures[interface] = failures + 1
        print(f"Scan failed on {interface}: {reason}")
        self.log.record_scan_failed(interface, reason)
        if failures == 0:
            self.voice.say(f"{spoken} on {interface}", lang=self.lang)
        elif failures % SCAN_FAILURE_REMINDER == 0:
            self.voice.say(f"Still no scan on {interface}", lang=self.lang)

    def scan_recovered(self, interface: str) -> None:
        """Say that scanning works again, if it had stopped working."""
        if self._scan_failures.pop(interface, 0):
            self.voice.say(f"Scanning again on {interface}", lang=self.lang)

    def notice_sleep(self, now: float) -> None:
        """Notice that the machine was suspended since the last cycle, and say so.

        A closed lid suspends most laptops, and this one runs with the lid
        closed inside a backpack. CLOCK_BOOTTIME keeps counting through a
        suspend and CLOCK_MONOTONIC does not, so the two drifting apart between
        one cycle and the next is exactly the time asleep. The log gets a
        `suspended` record so the hole in it is explained.

        `now` is the monotonic reading the loop already took for this cycle.
        """
        if self._boot_clock is None:
            return
        tick = (now, self._boot_clock())
        if self._last_tick is not None:
            slept = (tick[1] - self._last_tick[1]) - (tick[0] - self._last_tick[0])
            if slept >= SUSPEND_THRESHOLD:
                print(f"The laptop slept for {describe_duration(slept)}.")
                self.log.record_suspended(slept)
                self.voice.say(f"The laptop slept for {describe_duration(slept)}", lang=self.lang)
        self._last_tick = tick

    def scan_networks(self) -> list[Any]:
        """One monitoring cycle over every Wi-Fi interface.

        Returns the networks seen for the first time.
        """
        self.voice.say(
            "Scanning", lang=self.lang, silent=self.quiet or not self.speak_status, status=True
        )
        self._cycle += 1
        interfaces = self.wifi_interfaces()
        if not interfaces:
            self.voice.say("Interface not found.", lang=self.lang)
            return []

        new_networks: list[Any] = []
        for interface in interfaces:
            try:
                self.check_connection(interface)
            # Reading who you are joined to is a side question, and the walk is
            # the scanning. A D-Bus call for the association falling over used
            # to end the outing with a traceback while the scan itself was fine.
            # Not recorded as `scan_failed`: nothing has failed to scan yet.
            except Exception as exc:  # noqa: BLE001
                print(f"Could not read the association of {interface}: {exc}")
            blocked = self.radio_blocked(interface)
            if blocked:
                # Nothing the daemon answers now is worth recording: a hole in the
                # record, said and written as one, not as an empty street.
                self.scan_failed(interface, f"radio {blocked} blocked (rfkill)", "Radio blocked")
                continue
            ap_list = self.scan_access_points(interface)
            if ap_list is None:
                continue
            print(f"{interface}: {len(ap_list)} networks")
            if self.log_every_scan:
                self.log.record_scan(ap_list, interface, self._cycle)
            for ap in ap_list:
                key = network_key(ap)
                if key not in self.scanned_networks:
                    self.scanned_networks.add(key)
                    new_networks.append(ap)

        if new_networks:
            self.log.record_new_networks(new_networks)
            self.announce_new_networks(new_networks)
        return new_networks

    def announce_new_networks(self, new_networks: Sequence[Any]) -> None:
        """Say what is new without hogging the headphones.

        Up to `speak_names_up_to` new networks are named one by one. Past that
        only the count is said -- twenty-five names in a row can be neither
        written down nor remembered, and while they are being spoken nothing
        else can be -- plus the names of the open ones, which are the ones
        worth hearing. The log has every name regardless.
        """
        count = len(new_networks)
        if count == 1:
            self.voice.say("New network found", lang=self.lang, silent=self.quiet)
        else:
            self.voice.say(f"{count} new networks", lang=self.lang, silent=self.quiet)
        if count <= self.speak_names_up_to:
            named = list(new_networks)
        else:
            named = [ap for ap in new_networks if (ap.security or "").lower() == "open"]
            if named:
                self.voice.say(f"{len(named)} open", lang=self.lang, silent=self.quiet)
        # One name once: a terminal's nine open access points share a single SSID,
        # and nine repetitions of it through the headphones say nothing the first did not.
        names: list[str] = []
        for ap in named:
            if ap.ssid not in names:
                names.append(ap.ssid)
        for name in names:
            self.voice.say(name, lang=self.ssid_lang, silent=self.quiet, optional=True)

    def say_time(self, now: float) -> None:
        """Say the time after a cycle that began at `now` on the monotonic clock.

        Two rhythms. With `say_time_every`, every that many seconds, queued as an
        event so that it is never skipped: a fixed beat for the notebook. Without
        it, every cycle as a status, only while the voice is free, so that it is
        never late.
        """
        if self.say_time_every > 0:
            due = self._time_last_said is None or now - self._time_last_said >= self.say_time_every
            if due:
                self._time_last_said = now
                self.voice.say(self.actual_time(), lang=self.lang, silent=not self.speak_time)
            return
        self.voice.say(
            self.actual_time(),
            lang=self.lang,
            silent=not self.speak_time or not (self.speak_status or self.quiet),
            status=True,
        )

    def scan_networks_loop(self, cycles: int = 0) -> None:
        """Scan forever, or `cycles` times, saying the time between scans.

        Sleeps only what is left of `time_between_scans` after the cycle's own
        work, so scans keep the cadence that was asked for instead of drifting
        by however long the cycle took. Evenly spaced scans are what let
        `enodia.reconcile` place them along the route.
        """
        done = 0
        while (cycles <= 0 or done < cycles) and not self._stopping.is_set():
            started = self._clock()
            self.notice_sleep(started)
            self.check_battery()
            self.scan_networks()
            self.say_time(started)
            done += 1
            if cycles <= 0 or done < cycles:
                remaining = self.time_between_scans - (self._clock() - started)
                if remaining > 0:
                    print(f"Waiting {remaining:.1f} seconds.")
                    self._sleep(remaining)
                else:
                    print(f"Cycle took {-remaining:.1f} seconds longer than the interval.")

    def auto_scan(self, cycles: int = 0) -> Thread:
        """Run the scan loop in its own thread and return it.

        The thread is a daemon, so it never keeps the interpreter alive on its
        own, and `stop()` or `close()` ends it. Prefer the context manager:

            with WifiMonitor(voice=voice, log=log) as monitor:
                monitor.auto_scan()
                ...
        """
        self._stopping.clear()
        self._thread = Thread(
            target=self.scan_networks_loop,
            kwargs={"cycles": cycles},
            name="enodia-scan",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def close(self, timeout: float = 5.0) -> None:
        """Stop the scan loop, wait for its thread, and close the voice.

        The voice is closed only if it queues speech (`BackgroundVoice`), so
        that whatever is still queued gets said before the program ends.
        """
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        if self.button is not None:
            self.button.close()
        close_voice = getattr(self.voice, "close", None)
        if close_voice is not None:
            close_voice()

    def __enter__(self) -> WifiMonitor:  # noqa: PYI034 -- `Self` needs Python 3.11
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
