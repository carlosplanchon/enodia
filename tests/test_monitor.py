"""Tests for the scan loop. ifpeek is replaced at the module seam; no radio is touched."""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from ifpeek import AccessPoint

from enodia import monitor, netlog
from enodia.button import ButtonMarker
from enodia.monitor import WifiMonitor, network_key
from enodia.netlog import NetworkLog, read_log
from enodia.voice import VoiceController

TZ = timezone(timedelta(hours=-3))


def ap(ssid, bssid="aa:bb:cc:dd:ee:01", security="psk", percent=80):
    return AccessPoint(
        ssid=ssid,
        bssid=bssid,
        frequency=2412,
        signal_dbm=-60,
        signal_percent=percent,
        security=security,
        connected=False,
    )


class RecordingVoice(VoiceController):
    def __init__(self):
        super().__init__()
        self.said = []

    def say(self, text, lang="en-US", silent=False, **kwargs):
        self.said.append((text, lang, silent))

    def texts(self):
        return [text for text, _, _ in self.said]


class FakeNet:
    """Stand-in for the ifpeek functions the monitor uses."""

    def __init__(self):
        self.interfaces = ["wlan0"]
        self.essid = "Home"
        self.bssid = "aa:bb:cc:dd:ee:01"
        self.percent = 62
        self.dbm = -55
        self.frequency = 2412
        self.blocked = None  # rfkill: "hard", "soft" or None
        self.aps = []
        self.scan_error = None
        self.scanned = []
        self.fresh_requests = []

    def get_wifi_interfaces(self):
        return list(self.interfaces)

    def access_point_essid(self, interface):
        return self.essid

    def access_point_mac_address(self, interface):
        return self.bssid if self.essid else None

    def access_point_signal_percent(self, interface):
        return self.percent

    def access_point_signal_dbm(self, interface):
        return self.dbm if self.essid else None

    def access_point_frequency(self, interface):
        return self.frequency if self.essid else None

    def interface_rfkill(self, interface):
        return self.blocked

    def scan_access_points(self, interface=None, fresh=False):
        self.scanned.append(interface)
        self.fresh_requests.append(fresh)
        if self.scan_error:
            raise self.scan_error
        return list(self.aps)


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    for name in (
        "get_wifi_interfaces",
        "access_point_essid",
        "access_point_mac_address",
        "access_point_signal_percent",
        "access_point_signal_dbm",
        "access_point_frequency",
        "scan_access_points",
        "interface_rfkill",
    ):
        monkeypatch.setattr(monitor.ifpeek, name, getattr(fake, name))
    return fake


@pytest.fixture
def mon(net, tmp_path):
    return WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "networks.jsonl"),
        time_between_scans=1,
        sleep=lambda seconds: None,
    )


def log_blocks(mon):
    """What the log records, parsed back."""
    return read_log(mon.log.path)


def log_events(mon):
    return [block.event for block in log_blocks(mon)]


def test_network_key_prefers_bssid():
    assert network_key(ap("Home")) == "aa:bb:cc:dd:ee:01"
    assert network_key(ap("Home", bssid=None)) == "Home"


def test_defaults():
    mon = WifiMonitor()
    assert isinstance(mon.voice, VoiceController)
    assert mon.log.path.name == "networks.jsonl"
    assert mon.interfaces is None


def test_first_cycle_announces_connection_and_new_networks(mon, net):
    net.aps = [ap("Home"), ap("Cafe", bssid="aa:bb:cc:dd:ee:02", security="open")]
    new = mon.scan_networks()
    assert [a.ssid for a in new] == ["Home", "Cafe"]
    assert mon.voice.said[:3] == [
        ("Scanning", "en-US", True),
        ("Now connected to", "en-US", False),
        ("Home", "es-ES", False),
    ]
    assert ("2 new networks", "en-US", False) in mon.voice.said
    assert ("Cafe", "es-ES", False) in mon.voice.said
    blocks = log_blocks(mon)
    assert [b.event for b in blocks] == ["connected", "scan", "new"]
    assert blocks[0].ssid == "Home"
    assert [n.ssid for n in blocks[1].networks] == ["Home", "Cafe"]
    assert blocks[2].networks[1].open
    assert net.scanned == ["wlan0"]


def test_second_cycle_reports_signal_and_no_new_networks(mon, net):
    net.aps = [ap("Home")]
    mon.scan_networks()
    mon.voice.said.clear()
    assert mon.scan_networks() == []
    assert ("Signal quality is 62", "en-US", True) in mon.voice.said
    assert "New network found" not in mon.voice.texts()
    assert log_events(mon).count("scan") == 2


def test_disconnect_and_reconnect(mon, net):
    mon.scan_networks()
    net.essid = None
    mon.scan_networks()
    assert ("Now disconnected", "en-US", False) in mon.voice.said
    assert "disconnected" in log_events(mon)
    mon.voice.said.clear()
    net.essid = "Work"
    mon.scan_networks()
    assert ("Work", "es-ES", False) in mon.voice.said
    assert log_events(mon).count("connected") == 2


def test_status_is_printed_only_by_default(mon, net):
    net.aps = [ap("Home")]
    mon.scan_networks_loop(cycles=2)
    status = [
        (t, silent)
        for t, _, silent in mon.voice.said
        if t == "Scanning" or t.startswith("Signal quality") or t.endswith("seconds")
    ]
    assert status and all(silent for _, silent in status)


def test_speak_status_says_scanning_and_the_time_but_not_the_signal(net, tmp_path):
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.txt"),
        speak_status=True,
        sleep=lambda seconds: None,
    )
    net.aps = [ap("Home")]
    mon.scan_networks_loop(cycles=2)
    spoken = [text for text, _, silent in mon.voice.said if not silent]
    silent = [text for text, _, silent in mon.voice.said if silent]
    assert "Scanning" in spoken and any(text.endswith("seconds") for text in spoken)
    assert silent == ["Signal quality is 62"]  # printed, never said, without --say-signal


def test_starting_disconnected_says_nothing_about_the_connection(mon, net):
    net.essid = None
    mon.scan_networks()
    mon.scan_networks()
    assert "Now disconnected" not in mon.voice.texts()
    assert "Now connected to" not in mon.voice.texts()
    assert log_events(mon) == ["scan", "scan"]  # el ciclo queda registrado, sin redes
    assert all(not r.networks for r in log_blocks(mon))


def test_each_interface_is_tracked_separately(mon, net):
    net.interfaces = ["wlan0", "wlan1"]
    mon.scan_networks()
    assert mon.voice.texts().count("Now connected to") == 2
    assert net.scanned == ["wlan0", "wlan1"]


def test_dedup_by_ssid_when_bssid_missing(mon, net):
    net.aps = [ap("Home", bssid=None)]
    assert len(mon.scan_networks()) == 1
    net.aps = [ap("Home", bssid=None, percent=10)]
    assert mon.scan_networks() == []


def test_fresh_scan_is_passed_to_ifpeek(mon, net):
    mon.scan_networks()
    assert net.fresh_requests == [True]
    mon.fresh_scan = False
    mon.scan_networks()
    assert net.fresh_requests == [True, False]


def test_no_interfaces(mon, net):
    net.interfaces = []
    assert mon.scan_networks() == []
    assert ("Interface not found.", "en-US", False) in mon.voice.said
    assert net.scanned == []


def test_scan_failure_is_reported_not_fatal(mon, net, capsys):
    net.scan_error = RuntimeError("no Wi-Fi daemon")
    assert mon.scan_networks() == []
    assert "no Wi-Fi daemon" in capsys.readouterr().out
    assert "connected" in log_events(mon)


def test_no_log_every_scan(mon, net):
    mon.log_every_scan = False
    net.aps = [ap("Home")]
    mon.scan_networks()
    mon.scan_networks()
    events = log_events(mon)
    assert "scan" not in events
    assert events.count("new") == 1


def test_explicit_interfaces_skip_discovery(net, tmp_path):
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.txt"),
        interfaces=["wlan9"],
        sleep=lambda seconds: None,
    )
    net.interfaces = []
    mon.scan_networks()
    assert net.scanned == ["wlan9"]


def test_loop_runs_cycles_and_sleeps_between(net, tmp_path):
    slept = []
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.txt"),
        time_between_scans=7,
        sleep=slept.append,
        clock=lambda: 0.0,
    )
    mon.scan_networks_loop(cycles=3)
    assert slept == [7, 7]
    assert mon.voice.texts().count("Scanning") == 3
    assert sum(1 for text in mon.voice.texts() if text.endswith("seconds")) == 3


def test_loop_sleeps_only_what_is_left_of_the_interval(net, tmp_path):
    # The cycle's own work counts against the wait, so scans keep their cadence
    # instead of drifting by however long speaking and scanning took.
    slept = []
    ticks = iter([0.0, 3.0, 100.0, 103.0, 200.0])
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.txt"),
        time_between_scans=7,
        sleep=slept.append,
        clock=lambda: next(ticks),
    )
    mon.scan_networks_loop(cycles=3)
    assert slept == [4.0, 4.0]


def test_loop_does_not_sleep_when_the_cycle_overruns(net, tmp_path, capsys):
    slept = []
    ticks = iter([0.0, 9.0, 100.0])
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.txt"),
        time_between_scans=7,
        sleep=slept.append,
        clock=lambda: next(ticks),
    )
    mon.scan_networks_loop(cycles=2)
    assert slept == []
    assert "2.0 seconds longer than the interval" in capsys.readouterr().out


def test_actual_time_is_said_on_the_24_hour_clock():
    # What is heard is what gets written, and the notebook is read on the 24-hour clock:
    # 17:30 must never be said as "5 hours".
    assert (
        WifiMonitor.actual_time(datetime(2018, 6, 3, 17, 30, 12))
        == "17 hours, 30 minutes, 12 seconds"
    )
    assert (
        WifiMonitor.actual_time(datetime(2018, 6, 3, 12, 0, 0)) == "12 hours, 0 minutes, 0 seconds"
    )
    assert WifiMonitor.actual_time(datetime(2018, 6, 3, 0, 5, 0)) == "0 hours, 5 minutes, 0 seconds"
    pattern = r"(1?[0-9]|2[0-3]) hours, ([0-9]|[1-5][0-9]) minutes, ([0-9]|[1-5][0-9]) seconds"
    assert re.fullmatch(pattern, WifiMonitor.actual_time())


def test_auto_scan_runs_in_a_thread(net, tmp_path):
    mon = WifiMonitor(
        voice=RecordingVoice(), log=NetworkLog(tmp_path / "n.txt"), sleep=lambda seconds: None
    )
    thread = mon.auto_scan(cycles=1)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "Scanning" in mon.voice.texts()


# --- parada y cierre -----------------------------------------------------------


class ClosableVoice(RecordingVoice):
    """A voice that queues speech, like BackgroundVoice, and must be closed."""

    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self, timeout=5.0):
        self.closed = True


def test_wait_returns_at_once_when_the_loop_is_stopped(tmp_path):
    # The default wait is the stop flag, so stop() never leaves the loop asleep
    # for a whole interval.
    mon = WifiMonitor(voice=RecordingVoice(), log=NetworkLog(tmp_path / "n.txt"))
    mon.stop()
    started = time.monotonic()
    mon._wait(30)
    assert time.monotonic() - started < 1


def test_auto_scan_is_a_daemon_and_stops_on_request(net, tmp_path):
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.txt"),
        time_between_scans=0.01,
    )
    thread = mon.auto_scan()  # sin límite de ciclos: pararía solo con stop()
    assert thread.daemon
    mon.stop()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_close_stops_the_loop_and_closes_the_voice(net, tmp_path):
    voice = ClosableVoice()
    mon = WifiMonitor(voice=voice, log=NetworkLog(tmp_path / "n.txt"), time_between_scans=0.01)
    mon.auto_scan()
    mon.close()
    assert mon._thread is None
    assert voice.closed


def test_monitor_as_a_context_manager_closes_on_exit(net, tmp_path):
    voice = ClosableVoice()
    with WifiMonitor(
        voice=voice,
        log=NetworkLog(tmp_path / "n.txt"),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    ) as mon:
        mon.scan_networks_loop(cycles=1)
    assert voice.closed


def test_close_without_a_thread_or_a_closable_voice(net, tmp_path):
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.txt"),
        sleep=lambda seconds: None,
    )
    mon.close()  # no hay hilo, y VoiceController no tiene close()
    assert mon._thread is None


# --- fidelidad de los datos ----------------------------------------------------


def test_roaming_is_logged_but_not_spoken(mon, net, tmp_path):
    # Two access points of the same extended network: the name never changes,
    # so there is nothing new to say, but the move must be on record.
    mon.scan_networks()
    net.bssid = "aa:bb:cc:dd:ee:99"
    mon.scan_networks()
    said = [text for text, _, silent in mon.voice.said if not silent]
    assert "Roaming on Home" not in said
    assert ("Roaming on Home", "en-US", True) in mon.voice.said  # impreso, no dicho
    connections = [b for b in log_blocks(mon) if b.event == "connected"]
    assert [(b.ssid, b.bssid) for b in connections] == [
        ("Home", "aa:bb:cc:dd:ee:01"),
        ("Home", "aa:bb:cc:dd:ee:99"),
    ]


def test_a_different_network_is_announced_not_treated_as_roaming(mon, net):
    mon.scan_networks()
    net.essid, net.bssid = "Work", "aa:bb:cc:dd:ee:99"
    mon.scan_networks()
    assert "Now connected to" in mon.voice.texts()
    assert "Work" in mon.voice.texts()


def test_scan_blocks_record_the_interface(mon, net):
    net.interfaces = ["wlan0", "wlan1"]
    net.aps = [ap("Home")]
    mon.scan_networks()
    scans = [b for b in read_log(mon.log.path) if b.is_scan]
    assert [b.interface for b in scans] == ["wlan0", "wlan1"]


def test_resume_from_log_seeds_the_networks_already_seen(mon, net):
    net.aps = [ap("Home"), ap("Cafe", bssid="aa:bb:cc:dd:ee:02")]
    mon.scan_networks()
    assert len(mon.scanned_networks) == 2

    restarted = WifiMonitor(
        voice=RecordingVoice(),
        log=mon.log,
        sleep=lambda seconds: None,
    )
    assert restarted.resume_from_log() == 2
    assert restarted.resume_from_log() == 0  # ya estaban, no se recuentan
    assert restarted.scan_networks() == []  # nada es nuevo: no se anuncia
    assert "New network found" not in restarted.voice.texts()


def test_resume_from_a_log_that_is_not_there(tmp_path):
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "missing.txt"),
        sleep=lambda seconds: None,
    )
    assert mon.resume_from_log() == 0


def test_an_association_records_how_the_access_point_came_through(mon, net):
    # The same fields a scanned network carries, so the two can be compared.
    net.dbm, net.frequency, net.percent = -48, 5180, 88
    mon.scan_networks()
    connected = next(r for r in log_blocks(mon) if r.event == "connected")
    assert (connected.ssid, connected.bssid) == ("Home", "aa:bb:cc:dd:ee:01")
    assert (connected.channel, connected.signal_dbm, connected.signal_percent) == (36, -48, 88)


def test_a_roam_records_the_signal_of_the_new_access_point(mon, net):
    mon.scan_networks()
    net.bssid, net.dbm, net.frequency = "aa:bb:cc:dd:ee:99", -70, 2437
    mon.scan_networks()
    roam = [r for r in log_blocks(mon) if r.event == "connected"][1]
    assert (roam.bssid, roam.channel, roam.signal_dbm) == ("aa:bb:cc:dd:ee:99", 6, -70)


def test_a_backend_that_reports_no_signal_records_nulls(mon, net):
    net.dbm, net.frequency, net.percent = None, None, None
    mon.scan_networks()
    connected = next(r for r in log_blocks(mon) if r.event == "connected")
    assert (connected.channel, connected.signal_dbm, connected.signal_percent) == (None, None, None)


def test_a_blocked_radio_is_a_failed_scan_not_an_empty_street(mon, net):
    # An airplane-mode key knocked inside the backpack: the radio hears nothing,
    # and four hours later the log has to say the hole was the radio, not the street.
    net.blocked = "soft"
    net.aps = [ap("Stale")]  # whatever the daemon still holds is not worth recording
    mon.scan_networks()
    assert ("Radio blocked on wlan0", "en-US", False) in mon.voice.said
    assert net.scanned == []
    hole = log_blocks(mon)[-1]
    assert hole.event == "scan_failed" and hole.reason == "radio soft blocked (rfkill)"
    assert "scan" not in log_events(mon)
    net.blocked = None
    mon.scan_networks()
    assert "Scanning again on wlan0" in mon.voice.texts()
    assert log_events(mon).count("scan") == 1 and net.scanned == ["wlan0"]


def test_a_walk_joined_to_no_network_is_not_a_blocked_radio(mon, net):
    # The first real outing: the laptop associated to nothing the whole way,
    # forty networks in view, and the kernel calling the interface DOWN. That is
    # every outing, and it must sound like one.
    net.essid = None
    net.aps = [ap("Bar"), ap("Kiosco", bssid="aa:bb:cc:dd:ee:02")]
    mon.scan_networks()
    assert [r.event for r in log_blocks(mon) if r.event != "new"][-1] == "scan"
    assert not any("down" in text or "blocked" in text for text in mon.voice.texts())


def test_a_healthy_empty_scan_is_an_empty_street(mon, net):
    net.aps = []
    mon.scan_networks()
    scan = log_blocks(mon)[-1]
    assert scan.is_scan and scan.networks == []
    assert "Radio blocked on wlan0" not in mon.voice.texts()


def test_an_unreadable_radio_switch_is_reported_not_fatal(mon, net, capsys, monkeypatch):
    def explode(interface):
        raise OSError("sysfs vanished")

    monkeypatch.setattr(monitor.ifpeek, "interface_rfkill", explode)
    net.aps = [ap("Home")]
    mon.scan_networks()
    assert "Could not read the radio switch of wlan0: sysfs vanished" in capsys.readouterr().out
    assert "scan" in log_events(mon)  # the scan went ahead regardless


def test_scans_record_the_frequency_and_which_network_is_ours(mon, net):
    net.aps = [ap("Home"), ap("Cafe", bssid="aa:bb:cc:dd:ee:02")]
    net.aps[0] = net.aps[0]._replace(connected=True, frequency=5955)
    mon.scan_networks()
    home, cafe = [r for r in log_blocks(mon) if r.is_scan][-1].networks
    assert home.frequency == 5955 and home.connected
    assert cafe.frequency == 2412 and not cafe.connected
    # Ambos caen en el canal 1: solo la frecuencia dice en qué banda están.
    assert home.channel == 1 and cafe.channel == 1


def test_the_suite_refuses_to_read_the_real_radio(tmp_path):
    # The guard in conftest is what keeps the fakes honest: when the monitor
    # grows a new ifpeek call, or reads sysfs anew, the suite must fail instead
    # of quietly reading this machine's Wi-Fi. This asserts the guard is armed.
    from conftest import RealRadioCall

    with pytest.raises(RealRadioCall, match="was called for real"):
        monitor.ifpeek.get_wifi_interfaces()
    # And it survives the monitor's own defensive `except Exception`.
    with pytest.raises(RealRadioCall):
        WifiMonitor(voice=RecordingVoice(), log=NetworkLog(tmp_path / "n.jsonl")).radio_blocked(
            "wlan0"
        )


# --- fallos que antes eran silenciosos ----------------------------------------------


def test_a_failed_scan_is_spoken_and_recorded(mon, net):
    net.scan_error = PermissionError("not in group 'network'")
    assert mon.scan_networks() == []
    assert ("Scan failed on wlan0", "en-US", False) in mon.voice.said
    failed = [r for r in log_blocks(mon) if r.event == "scan_failed"]
    assert len(failed) == 1 and failed[0].interface == "wlan0"
    assert "not in group" in failed[0].reason
    assert "scan" not in log_events(mon)  # un fallo no es un escaneo vacío


def test_a_persistent_failure_is_reminded_not_repeated(mon, net):
    # Spoken the first time and then once every SCAN_FAILURE_REMINDER cycles:
    # the audio channel is scarce, but a heartbeat over an empty log must not
    # sound healthy for four hours.
    net.scan_error = RuntimeError("daemon gone")
    for _ in range(monitor.SCAN_FAILURE_REMINDER * 2 + 1):
        mon.scan_networks()
    said = mon.voice.texts()
    assert said.count("Scan failed on wlan0") == 1
    assert said.count("Still no scan on wlan0") == 2
    assert log_events(mon).count("scan_failed") == monitor.SCAN_FAILURE_REMINDER * 2 + 1


def test_scanning_again_is_announced_once(mon, net):
    net.scan_error = RuntimeError("daemon gone")
    mon.scan_networks()
    mon.scan_networks()
    net.scan_error = None
    net.aps = [ap("Home")]
    mon.scan_networks()
    mon.scan_networks()
    assert mon.voice.texts().count("Scanning again on wlan0") == 1
    assert mon._scan_failures == {}


def test_failures_are_counted_per_interface(mon, net):
    net.interfaces = ["wlan0", "wlan1"]
    real = net.scan_access_points

    def only_wlan1_fails(interface=None, fresh=False):
        if interface == "wlan1":
            raise RuntimeError("wlan1 has no daemon")
        return real(interface=interface, fresh=fresh)

    monitor.ifpeek.scan_access_points = only_wlan1_fails
    net.aps = [ap("Home")]
    mon.scan_networks()
    assert ("Scan failed on wlan1", "en-US", False) in mon.voice.said
    assert "Scan failed on wlan0" not in mon.voice.texts()
    assert mon._scan_failures == {"wlan1": 1}


def test_describe_duration_for_the_ear():
    assert monitor.describe_duration(1) == "1 second"
    assert monitor.describe_duration(45.4) == "45 seconds"
    assert monitor.describe_duration(60) == "1 minute"
    assert monitor.describe_duration(12 * 60 + 30) == "12 minutes"
    assert monitor.describe_duration(3660) == "1 hour, 1 minute"
    assert monitor.describe_duration(2 * 3600 + 5 * 60) == "2 hours, 5 minutes"


def test_a_suspend_between_cycles_is_noticed_and_said(net, tmp_path):
    # The monotonic clock says five seconds passed; the boot clock says 605.
    # The difference is the ten minutes the lid was closed.
    ticks = iter([0.0, 0.0, 5.0])
    boot = iter([100.0, 705.0])
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        sleep=lambda seconds: None,
        clock=lambda: next(ticks),
        boot_clock=lambda: next(boot),
    )
    mon.scan_networks_loop(cycles=2)
    assert ("The laptop slept for 10 minutes", "en-US", False) in mon.voice.said
    slept = [r for r in log_blocks(mon) if r.event == "suspended"]
    assert len(slept) == 1 and slept[0].seconds == pytest.approx(600.0)


def test_clocks_that_agree_mean_no_suspend(net, tmp_path):
    ticks = iter([0.0, 0.0, 5.0])
    boot = iter([100.0, 105.0])
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        sleep=lambda seconds: None,
        clock=lambda: next(ticks),
        boot_clock=lambda: next(boot),
    )
    mon.scan_networks_loop(cycles=2)
    assert not any("slept" in text for text in mon.voice.texts())
    assert "suspended" not in log_events(mon)


def test_a_stand_in_clock_alone_turns_suspend_detection_off(net, tmp_path):
    # Measuring a fake monotonic clock against the real boot clock would read
    # as a suspend of hours, so with no boot clock to pair it with there is none.
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    )
    assert mon._boot_clock is None
    mon.scan_networks_loop(cycles=3)
    assert "suspended" not in log_events(mon)


def test_the_real_clocks_are_paired_by_default(net, tmp_path, monkeypatch):
    mon = WifiMonitor(voice=RecordingVoice(), log=NetworkLog(tmp_path / "n.jsonl"))
    assert mon._boot_clock is monitor._boottime
    assert monitor._boottime() >= time.monotonic() - 1  # el de arranque nunca va por detrás
    # Where the kernel has no boot clock, it falls back to monotonic and never sees a suspend.
    monkeypatch.delattr(monitor.time, "CLOCK_BOOTTIME")
    assert abs(monitor._boottime() - time.monotonic()) < 1


# --- the headset button --------------------------------------------------------------


def test_a_button_press_is_a_numbered_mark(mon, net):
    assert mon.mark(164) == 1
    assert mon.mark() == 2
    assert mon.voice.texts()[-2:] == ["Mark 1", "Mark 2"]
    marks = [r for r in log_blocks(mon) if r.event == "mark"]
    assert [r.number for r in marks] == [1, 2]


def test_marks_carry_on_after_a_restart(mon, net):
    for _ in range(3):
        mon.mark()
    restarted = WifiMonitor(voice=RecordingVoice(), log=mon.log, sleep=lambda seconds: None)
    restarted.resume_from_log()
    assert restarted.marks == 3
    assert restarted.mark() == 4  # "Mark 4", not a second "Mark 1"


def test_use_button_wires_the_marker_and_close_stops_it(mon, net):
    from enodia.button import ButtonMarker

    marker = ButtonMarker([], debounce=0.0)  # nothing to listen to: the thread ends at once
    mon.use_button(marker)
    assert marker.on_press == mon.mark and marker.on_lost is not None
    marker.press(164)
    assert "Mark 1" in mon.voice.texts()
    marker.on_lost("/dev/input/event9")
    assert "Button lost" in mon.voice.texts()
    mon.close()
    assert marker._thread is None and mon.button is marker


# --- saying what is new without hogging the headphones ------------------------------


def test_one_new_network_is_announced_and_named(mon, net):
    net.essid = None  # sin asociación: lo único que se oye es la red nueva
    net.aps = [ap("Cafe")]
    mon.scan_networks()
    said = [s for s in mon.voice.texts() if s not in ("Scanning",)]
    assert said[:2] == ["New network found", "Cafe"]


def test_a_few_new_networks_are_counted_and_named(mon, net):
    net.aps = [ap("A"), ap("B", bssid="aa:bb:cc:dd:ee:02"), ap("C", bssid="aa:bb:cc:dd:ee:03")]
    mon.scan_networks()
    said = mon.voice.texts()
    assert "3 new networks" in said and "New network found" not in said
    assert [s for s in said if s in ("A", "B", "C")] == ["A", "B", "C"]


def test_many_new_networks_are_counted_and_only_the_open_ones_named(mon, net):
    net.aps = [ap(f"net{i}", bssid=f"aa:bb:cc:dd:ee:{i:02x}") for i in range(10)]
    net.aps[3] = net.aps[3]._replace(ssid="Cafe libre", security="open")
    net.aps[8] = net.aps[8]._replace(ssid="Plaza", security="open")
    mon.scan_networks()
    said = mon.voice.texts()
    assert "10 new networks" in said and "2 open" in said
    assert "Cafe libre" in said and "Plaza" in said
    assert not any(s.startswith("net") for s in said)  # los cerrados no se leen


def test_many_new_networks_with_none_open_is_just_the_count(mon, net):
    net.aps = [ap(f"net{i}", bssid=f"aa:bb:cc:dd:ee:{i:02x}") for i in range(5)]
    mon.scan_networks()
    said = mon.voice.texts()
    assert "5 new networks" in said
    assert not any("open" in s for s in said) and not any(s.startswith("net") for s in said)


def test_say_names_zero_names_only_the_open_ones(net, tmp_path):
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        sleep=lambda seconds: None,
        speak_names_up_to=0,
    )
    net.essid = None
    net.aps = [ap("Cafe"), ap("Plaza libre", bssid="aa:bb:cc:dd:ee:02", security="open")]
    mon.scan_networks()
    said = mon.voice.texts()
    assert "2 new networks" in said and "1 open" in said
    assert "Plaza libre" in said and "Cafe" not in said


# --- the battery ------------------------------------------------------------------


def test_battery_warnings_are_said_once_each_on_the_way_down(net, tmp_path):
    readings = iter(
        [
            (45, "Discharging"),
            (21, "Discharging"),
            (20, "Discharging"),
            (19, "Discharging"),
            (10, "Discharging"),
            (9, "Discharging"),
        ]
    )
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
        battery_reader=lambda: next(readings),
    )
    mon.scan_networks_loop(cycles=6)
    said = [s for s in mon.voice.texts() if s.startswith("Battery")]
    assert said == ["Battery at 20 percent", "Battery at 10 percent"]


@pytest.mark.parametrize("reading", [(15, "Charging"), (5, "Full"), (15, "Not charging"), None])
def test_no_battery_warning_while_charging_or_without_a_battery(net, tmp_path, reading):
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
        battery_reader=lambda: reading,
    )
    mon.scan_networks_loop(cycles=2)
    assert not any(s.startswith("Battery") for s in mon.voice.texts())


def test_the_default_battery_reader_is_the_systems(net, tmp_path):
    from enodia import system

    mon = WifiMonitor(voice=RecordingVoice(), log=NetworkLog(tmp_path / "n.jsonl"))
    assert mon._battery is system.battery


def test_a_name_shared_by_several_access_points_is_said_once(mon, net):
    # Nine open access points of one bus terminal, one SSID: heard in Tres Cruces, once.
    net.essid = None
    net.aps = [ap(f"net{i}", bssid=f"aa:bb:cc:dd:ee:{i:02x}") for i in range(5)] + [
        ap("#Tres Cruces WiFi", bssid=f"aa:bb:cc:dd:ff:{i:02x}", security="open") for i in range(9)
    ]
    mon.scan_networks()
    said = mon.voice.texts()
    assert "14 new networks" in said and "9 open" in said
    assert said.count("#Tres Cruces WiFi") == 1


def test_the_per_cycle_status_is_marked_as_such(net, tmp_path):
    class Listening(RecordingVoice):
        def __init__(self):
            super().__init__()
            self.status = []

        def say(self, text, lang="en-US", silent=False, **kwargs):
            super().say(text, lang, silent)
            if kwargs.get("status"):
                self.status.append(text)

    mon = WifiMonitor(
        voice=Listening(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        speak_status=True,
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    )
    net.aps = [ap("Home")]
    mon.scan_networks_loop(cycles=2)
    status = mon.voice.status
    assert "Scanning" in status and "Signal quality is 62" in status
    assert any(s.endswith("seconds") for s in status)
    assert "Now connected to" not in status and "New network found" not in status


def test_say_signal_speaks_the_signal_quality(net, tmp_path):
    # 1.7 seconds of a number that seldom changes: off unless asked for, even with --say-status.
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        speak_signal=True,
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    )
    mon.scan_networks()
    mon.scan_networks()
    assert ("Signal quality is 62", "en-US", False) in mon.voice.said
    assert ("Scanning", "en-US", True) in mon.voice.said  # sin --say-status sigue callado


# --- the time on a fixed beat -----------------------------------------------------


class Listening(RecordingVoice):
    """Records whether each utterance came in as status."""

    def __init__(self):
        super().__init__()
        self.status_of = {}

    def say(self, text, lang="en-US", silent=False, **kwargs):
        super().say(text, lang, silent)
        self.status_of[text] = kwargs.get("status", False)


def test_say_time_every_keeps_a_fixed_beat_as_an_event(net, tmp_path):
    # Cycles start at 0, 5, 10, 15, 20 s. Every 12 s: said at 0 and at 15.
    starts = iter([0.0, 0.0, 5.0, 5.0, 10.0, 10.0, 15.0, 15.0, 20.0])
    mon = WifiMonitor(
        voice=Listening(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        say_time_every=12,
        sleep=lambda seconds: None,
        clock=lambda: next(starts),
    )
    mon.scan_networks_loop(cycles=5)
    times = [text for text, _, silent in mon.voice.said if text.endswith("seconds") and not silent]
    assert len(times) == 2
    assert all(mon.voice.status_of[t] is False for t in times)  # an event: never skipped


def test_say_time_every_replaces_the_per_cycle_time(net, tmp_path):
    starts = iter([0.0, 0.0, 5.0, 5.0, 10.0])
    mon = WifiMonitor(
        voice=Listening(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        speak_status=True,
        say_time_every=60,
        sleep=lambda seconds: None,
        clock=lambda: next(starts),
    )
    mon.scan_networks_loop(cycles=3)
    times = [text for text, _, _ in mon.voice.said if text.endswith("seconds")]
    assert len(times) == 1  # una sola, la del compás, no tres
    assert mon.voice.status_of["Scanning"] is True  # y "Scanning" sigue siendo estado


def test_without_say_time_every_the_time_is_status_once_per_cycle(net, tmp_path):
    mon = WifiMonitor(
        voice=Listening(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        speak_status=True,
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    )
    mon.scan_networks_loop(cycles=3)
    times = [text for text, _, _ in mon.voice.said if text.endswith("seconds")]
    assert len(times) == 3 and all(mon.voice.status_of[t] is True for t in times)


# --- quiet: the time, the marks and the failures, nothing else -----------------------


def quiet_monitor(net, tmp_path, **kwargs):
    return WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        quiet=True,
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
        **kwargs,
    )


def test_quiet_says_the_time_and_nothing_about_networks(net, tmp_path):
    mon = quiet_monitor(net, tmp_path)
    net.aps = [ap("Home"), ap("Cafe libre", bssid="aa:bb:cc:dd:ee:02", security="open")]
    mon.scan_networks_loop(cycles=2)
    spoken = [text for text, _, silent in mon.voice.said if not silent]
    printed = [text for text, _, silent in mon.voice.said if silent]
    assert spoken and all(text.endswith("seconds") for text in spoken)  # solo la hora
    for text in ("Scanning", "Now connected to", "Home", "2 new networks", "Cafe libre"):
        assert text in printed  # impreso, no dicho


def test_quiet_still_says_marks_and_failures(net, tmp_path, capsys):
    mon = quiet_monitor(net, tmp_path)
    mon.mark()
    net.scan_error = RuntimeError("daemon gone")
    mon.scan_networks()
    net.scan_error = None
    net.aps = [ap("Home")]
    mon.scan_networks()  # "Scanning again", which closes the failure
    net.blocked = "hard"
    mon.scan_networks()
    spoken = [text for text, _, silent in mon.voice.said if not silent]
    assert "Mark 1" in spoken and "Scan failed on wlan0" in spoken
    assert "Scanning again on wlan0" in spoken and "Radio blocked on wlan0" in spoken


def test_quiet_with_a_fixed_beat_says_the_time_on_the_beat(net, tmp_path):
    starts = iter([0.0, 0.0, 5.0, 5.0, 10.0])
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        quiet=True,
        say_time_every=60,
        sleep=lambda seconds: None,
        clock=lambda: next(starts),
    )
    mon.scan_networks_loop(cycles=3)
    spoken = [text for text, _, silent in mon.voice.said if not silent]
    assert len(spoken) == 1 and spoken[0].endswith("seconds")


def test_no_hour_silences_the_time_even_in_quiet(net, tmp_path):
    mon = quiet_monitor(net, tmp_path, speak_time=False)
    net.aps = [ap("Home")]
    mon.scan_networks_loop(cycles=2)
    mon.mark()
    spoken = [text for text, _, silent in mon.voice.said if not silent]
    assert spoken == ["Mark 1"]  # nada más que la marca
    assert any(text.endswith("seconds") for text, _, silent in mon.voice.said if silent)


def test_no_hour_with_say_status_keeps_scanning_but_not_the_time(net, tmp_path):
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        speak_status=True,
        speak_time=False,
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    )
    mon.scan_networks_loop(cycles=2)
    spoken = [text for text, _, silent in mon.voice.said if not silent]
    assert "Scanning" in spoken and not any(text.endswith("seconds") for text in spoken)


def test_no_hour_prints_the_beat_without_saying_it(net, tmp_path):
    starts = iter([0.0, 0.0, 5.0])
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(tmp_path / "n.jsonl"),
        say_time_every=60,
        speak_time=False,
        sleep=lambda seconds: None,
        clock=lambda: next(starts),
    )
    mon.scan_networks_loop(cycles=2)
    times = [(text, silent) for text, _, silent in mon.voice.said if text.endswith("seconds")]
    assert times and all(silent for _, silent in times)


def test_every_scan_of_a_cycle_is_stamped_with_that_cycle(mon, net):
    # Which records belong to one look at one place is the loop's to say. The
    # clock cannot: it has one second of resolution, and a cycle that straddles
    # a second would come apart afterwards.
    net.interfaces = ["wlan0", "wlan1"]
    net.aps = [ap("Home")]
    mon.scan_networks()
    mon.scan_networks()
    scans = [r for r in log_blocks(mon) if r.is_scan]
    assert [(r.cycle, r.interface) for r in scans] == [
        (1, "wlan0"),
        (1, "wlan1"),
        (2, "wlan0"),
        (2, "wlan1"),
    ]


def test_cycles_carry_on_from_the_log_instead_of_starting_again(net, tmp_path):
    # A restart that writes into a log which already has cycles in it: without
    # this, its first cycle and the first cycle before the restart are one
    # cycle, and the reconciliation folds two places ten minutes apart into a
    # single scan at the earlier of them.
    log = tmp_path / "n.jsonl"
    log.write_text(
        '{"time": "2026-09-05T20:00:00-03:00", "event": "scan", "cycle": 57, "networks": []}\n'
    )
    net.aps = [ap("Home")]
    mon = WifiMonitor(voice=RecordingVoice(), log=NetworkLog(log), sleep=lambda seconds: None)
    mon.scan_networks()
    assert [r.cycle for r in read_log(log) if r.is_scan] == [57, 58]


def test_a_fresh_log_starts_its_cycles_at_one(net, tmp_path):
    net.aps = [ap("Home")]
    mon = WifiMonitor(
        voice=RecordingVoice(), log=NetworkLog(tmp_path / "nuevo.jsonl"), sleep=lambda s: None
    )
    mon.scan_networks()
    assert [r.cycle for r in log_blocks(mon) if r.is_scan] == [1]


def test_a_broken_association_read_does_not_end_the_walk(mon, net, capsys):
    # Reading who you are joined to is a side question. The walk is the
    # scanning, and a D-Bus call for the association falling over used to end
    # the outing with a traceback while the scan itself was fine.
    def explode(interface):
        raise RuntimeError("association D-Bus failed")

    original = monitor.ifpeek.access_point_essid
    monitor.ifpeek.access_point_essid = explode
    try:
        net.aps = [ap("Home")]
        mon.scan_networks()
    finally:
        monitor.ifpeek.access_point_essid = original
    assert "Could not read the association of wlan0" in capsys.readouterr().out
    assert "scan" in log_events(mon)  # y el escaneo se hizo igual


def test_a_hand_edited_cycle_does_not_stop_the_walk_from_starting(tmp_path, net):
    # The constructor reads the log to find out where the cycle numbers got to,
    # so a single poisoned line used to be enough for the monitor never to be
    # built. Nothing about an unreadable old record says the next scan cannot
    # be numbered: it carries on from the numbers it could read.
    log = tmp_path / "networks.jsonl"
    log.write_text(
        '{"time": "2026-09-05T17:00:00-03:00", "event": "scan", "cycle": "oops", '
        '"networks": [{"ssid": "Vieja", "bssid": "aa:bb:cc:dd:ee:09"}]}\n'
        '{"time": "2026-09-05T17:00:05-03:00", "event": "scan", "cycle": 4, "networks": []}\n'
        '{"time": "2026-09-05T17:00:06-03:00", "event": "mark", "number": "oops"}\n'
        '{"time": "2026-09-05T17:00:07-03:00", "event": "mark", "number": 2}\n'
    )
    mon = WifiMonitor(
        voice=RecordingVoice(),
        log=NetworkLog(log),
        sleep=lambda seconds: None,
    )
    assert mon.resume_from_log() == 1
    assert mon.marks == 2
    mon.scan_networks()
    assert read_log(log)[-1].cycle == 5


def test_every_scan_of_one_run_says_which_walk_it_belongs_to(mon, net):
    mon.scan_networks()
    mon.scan_networks()
    walks = {record.outing for record in log_blocks(mon) if record.is_scan}
    assert len(walks) == 1 and None not in walks


def test_a_second_run_into_one_file_is_a_second_walk_unless_it_resumes(net, tmp_path):
    # `--log walk.jsonl` reused every week appends to the same pages, so one
    # file holds several walks and the clock cannot tell them apart. A restart
    # of the same walk is the other case, and --resume is what says which.
    path = tmp_path / "networks.jsonl"

    def started():
        return WifiMonitor(voice=RecordingVoice(), log=NetworkLog(path), sleep=lambda s: None)

    first = started()
    first.scan_networks()
    second = started()
    second.scan_networks()
    walks = [record.outing for record in read_log(path) if record.is_scan]
    assert len(walks) == 2 and walks[0] != walks[1]

    resumed = started()
    resumed.resume_from_log()
    resumed.scan_networks()
    assert [r.outing for r in read_log(path) if r.is_scan][-1] == walks[1]


def test_resuming_a_log_that_never_named_its_walk_keeps_the_new_name(net, tmp_path):
    path = tmp_path / "networks.jsonl"
    path.write_text(
        '{"time": "2026-09-05T17:00:00-03:00", "event": "scan", "cycle": 1, "networks": []}\n'
    )
    mon = WifiMonitor(voice=RecordingVoice(), log=NetworkLog(path), sleep=lambda s: None)
    fresh = mon.log.outing
    mon.resume_from_log()
    assert mon.log.outing == fresh


def test_a_button_mark_says_which_walk_it_belongs_to(mon, net):
    # Every run numbers its marks from one, so a file of two walks holds two
    # mark 1s and the reconciliation has to be able to tell them apart.
    mon.mark()
    mon.scan_networks()
    marks = [record for record in log_blocks(mon) if record.event == "mark"]
    assert len(marks) == 1
    assert marks[0].outing == mon.log.outing
    assert all(record.outing == mon.log.outing for record in log_blocks(mon))


def test_resuming_counts_on_from_this_walks_marks_and_not_the_files(net, tmp_path):
    # Every run numbers its marks from one, so an older walk in the same file
    # that reached mark 100 used to make this one carry on at 101, while the
    # paper in the operator's hand said 3.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({**row, "time": row["time"] + "-03:00"})
            for row in (
                {"time": "2026-09-17T17:00:00", "event": "mark", "number": 1, "outing": "vieja111"},
                {
                    "time": "2026-09-17T17:50:00",
                    "event": "mark",
                    "number": 100,
                    "outing": "vieja111",
                },
                {
                    "time": "2026-09-18T18:00:00",
                    "event": "mark",
                    "number": 1,
                    "outing": "nueva222",
                },
                {
                    "time": "2026-09-18T18:10:00",
                    "event": "mark",
                    "number": 2,
                    "outing": "nueva222",
                },
                {
                    "time": "2026-09-18T18:11:00",
                    "event": "scan",
                    "cycle": 1,
                    "outing": "nueva222",
                    "networks": [],
                },
            )
        )
        + "\n"
    )
    mon = WifiMonitor(voice=RecordingVoice(), log=NetworkLog(path), sleep=lambda s: None)
    mon.resume_from_log()
    assert mon.marks == 2
    assert mon.mark() == 3


def test_two_presses_read_together_keep_their_distance_in_the_log(mon, net, monkeypatch):
    # The end of the button's story. The debounce already knew the two presses
    # were two seconds apart, and then the log dated both of them to the moment
    # they were handled: two crossings at one second, and a block of no length.
    from enodia.button import EV_KEY, INPUT_EVENT, ButtonMarker

    marker = ButtonMarker([], on_press=mon.mark, debounce=1.0, clock=lambda: 100.0)
    read, write = os.pipe()
    os.write(
        write,
        INPUT_EVENT.pack(10, 0, EV_KEY, 164, 1) + INPUT_EVENT.pack(12, 0, EV_KEY, 164, 1),
    )
    os.close(write)
    try:
        marker._drain(read)
    finally:
        os.close(read)
    marks = [record for record in log_blocks(mon) if record.event == "mark"]
    assert [record.number for record in marks] == [1, 2]
    assert (marks[1].time - marks[0].time).total_seconds() == 2


def test_a_press_that_carries_no_time_is_still_written_at_the_moment_it_is_handled(mon, net):
    mon.mark()
    (record,) = [one for one in log_blocks(mon) if one.event == "mark"]
    assert record.time is not None


def test_a_batch_of_presses_is_written_against_one_reading_of_the_clock(tmp_path, monkeypatch):
    # Each mark dates itself from its own reading of the time of day, and the
    # callbacks before it have already moved that reading on. Two presses two
    # seconds apart came out three seconds apart whenever the pair straddled a
    # second boundary, which is a whole crossing's worth of error from rounding.
    from enodia.button import EV_KEY, INPUT_EVENT, ButtonMarker

    moments = iter(
        [
            datetime(2026, 9, 17, 12, 0, 1, 990000, tzinfo=TZ),
            datetime(2026, 9, 17, 12, 0, 2, 10000, tzinfo=TZ),
        ]
    )
    monkeypatch.setattr(
        netlog,
        "now_iso",
        lambda ago=0.0: (next(moments) - timedelta(seconds=ago)).isoformat(timespec="seconds"),
    )
    log = NetworkLog(tmp_path / "networks.jsonl")
    numbered = {"n": 0}

    def press(code, ago):
        numbered["n"] += 1
        log.record_mark(numbered["n"], code, ago)

    # The process clock moves by exactly the twenty milliseconds the first
    # callback took, which is what the anchor has to cancel.
    ticks = iter([0.0, 0.02])
    marker = ButtonMarker([], on_press=press, debounce=1.0, clock=lambda: next(ticks))
    read, write = os.pipe()
    os.write(
        write,
        INPUT_EVENT.pack(10, 0, EV_KEY, 164, 1) + INPUT_EVENT.pack(12, 0, EV_KEY, 164, 1),
    )
    os.close(write)
    try:
        marker._drain(read)
    finally:
        os.close(read)
    marks = [record.time for record in read_log(log.path) if record.event == "mark"]
    assert (marks[1] - marks[0]).total_seconds() == 2


def test_a_button_overrun_is_said_and_written_down(mon, net):
    # A press that never arrived is a crossing that never arrived, and the
    # numbers said afterwards carry on as if nothing had gone missing, so the
    # paper and the log agree with each other and both are short a corner.
    marker = ButtonMarker([], debounce=0.0)
    mon.use_button(marker)
    assert marker.on_lost_events is not None
    marker.on_lost_events()
    (record,) = [one for one in log_blocks(mon) if one.event == "button_lost"]
    assert record.reason == "SYN_DROPPED"
    assert "Button overflow, a mark may be missing" in mon.voice.texts()
    marker.close()


def test_a_button_that_already_says_it_lost_events_is_left_alone(mon, net):
    told = []
    marker = ButtonMarker([], debounce=0.0, on_lost_events=lambda: told.append("mine"))
    mon.use_button(marker)
    marker.on_lost_events()
    assert told == ["mine"]
    assert "Button overflow, a mark may be missing" not in mon.voice.texts()
    assert not mon.log.path.exists()  # y nada se escribió en su nombre
    marker.close()
