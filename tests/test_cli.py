"""Tests for the command line."""

import importlib.metadata
import json

import pytest
from ifpeek import AccessPoint

import enodia
from enodia import cli
from enodia import voice as voice_module
from enodia.voice import ESpeak, PicoTTS


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == f"enodia {enodia.__version__}"


def test_the_version_the_module_says_is_the_version_the_package_declares():
    # It is written twice, in `pyproject.toml` and in `enodia/__init__.py`, and
    # nothing kept the two together. A release where they disagree ships a
    # package that answers one number to `--version` and another to pip, and
    # the one that is wrong is whichever the person bumping forgot.
    assert importlib.metadata.version("enodia") == enodia.__version__


def test_open_networks(tmp_path, capsys):
    log = tmp_path / "networks.jsonl"
    log.write_text(
        '{"time": "2026-09-05T00:14:03-03:00", "event": "scan", "networks": ['
        '{"ssid": "Cafe", "bssid": "11:22:33:44:55:66", "security": "open",'
        ' "channel": 6, "signal_dbm": -60, "signal_percent": 50},'
        '{"ssid": "Home", "bssid": "aa:bb:cc:dd:ee:ff", "security": "wpa2",'
        ' "channel": 1, "signal_dbm": -40, "signal_percent": 70}]}\n'
    )
    assert cli.main(["--open-networks", str(log)]) == 0
    out = capsys.readouterr().out
    assert out == "2026-09-05T00:14:03-03:00\t-60 dBm\t11:22:33:44:55:66\tCafe\n"


def test_voice_defaults_to_auto():
    assert cli.build_parser().parse_args([]).voice == "auto"


def test_make_voice(monkeypatch, capsys):
    assert not cli.make_voice("none").available
    assert isinstance(cli.make_voice("pico").controller.selected_voice, PicoTTS)
    assert isinstance(cli.make_voice("espeak").controller.selected_voice, ESpeak)

    monkeypatch.setattr(voice_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert isinstance(cli.make_voice("auto").controller.selected_voice, ESpeak)

    monkeypatch.setattr(
        voice_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("pico-tts", "paplay") else None,
    )
    assert isinstance(cli.make_voice("auto").controller.selected_voice, PicoTTS)

    monkeypatch.setattr(voice_module.shutil, "which", lambda name: None)
    assert not cli.make_voice("auto").available
    assert "No speech engine found" in capsys.readouterr().out


def test_main_runs_the_monitor(monkeypatch, tmp_path):
    created = {}

    class FakeMonitor:
        marks = 0

        def resume_from_log(self):
            return 0

        def __init__(self, **kwargs):
            created.update(kwargs)

        def close(self, timeout=5.0):
            self.closed = True

        def scan_networks_loop(self, cycles=0):
            created["cycles"] = cycles

    monkeypatch.setattr(cli, "WifiMonitor", FakeMonitor)
    assert (
        cli.main(
            [
                "--voice",
                "none",
                "--button",
                "off",
                "--interval",
                "2",
                "--cycles",
                "3",
                "--log",
                str(tmp_path / "n.txt"),
                "-i",
                "wlan0",
                "-i",
                "wlan1",
                "--lang",
                "en-GB",
                "--ssid-lang",
                "es-ES",
                "--no-log-every-scan",
            ]
        )
        == 0
    )
    assert created["cycles"] == 3
    assert created["time_between_scans"] == 2
    assert created["interfaces"] == ["wlan0", "wlan1"]
    assert created["lang"] == "en-GB"
    assert created["ssid_lang"] == "es-ES"
    assert created["log_every_scan"] is False
    assert created["speak_status"] is False
    assert created["speak_signal"] is False
    assert created["fresh_scan"] is True
    assert created["log"].path == tmp_path / "n.txt"
    assert not created["voice"].available


def test_say_status_flag(monkeypatch):
    created = {}

    class FakeMonitor:
        marks = 0

        def resume_from_log(self):
            return 0

        def __init__(self, **kwargs):
            created.update(kwargs)

        def close(self, timeout=5.0):
            self.closed = True

        def scan_networks_loop(self, cycles=0):
            pass

    monkeypatch.setattr(cli, "WifiMonitor", FakeMonitor)
    assert (
        cli.main(
            ["--voice", "none", "--button", "off", "--say-status", "--say-signal", "--no-fresh"]
        )
        == 0
    )
    assert created["speak_status"] is True
    assert created["speak_signal"] is True
    assert created["say_time_every"] == 0
    assert created["quiet"] is False
    assert created["speak_time"] is True
    assert created["fresh_scan"] is False


def test_main_handles_keyboard_interrupt(monkeypatch, capsys):
    class FakeMonitor:
        marks = 0

        def resume_from_log(self):
            return 0

        def __init__(self, **kwargs):
            pass

        def close(self, timeout=5.0):
            self.closed = True

        def scan_networks_loop(self, cycles=0):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "WifiMonitor", FakeMonitor)
    assert cli.main(["--voice", "none", "--button", "off", "--log", "walk.jsonl"]) == 0
    assert capsys.readouterr().out.endswith("\nStopped.\nLog: walk.jsonl\n")


def test_resume_reports_what_it_carried_over(monkeypatch, capsys, tmp_path):
    calls = []

    class FakeMonitor:
        marks = 0

        def __init__(self, **kwargs):
            pass

        def resume_from_log(self):
            calls.append("resumed")
            return 7

        def close(self, timeout=5.0):
            self.closed = True

        def scan_networks_loop(self, cycles=0):
            pass

    monkeypatch.setattr(cli, "WifiMonitor", FakeMonitor)
    log = str(tmp_path / "n.txt")
    assert cli.main(["--voice", "none", "--button", "off", "--log", log, "--resume"]) == 0
    assert calls == ["resumed"]
    assert (
        f"Carrying on from {log}: 7 networks already seen, 0 marks made." in capsys.readouterr().out
    )

    calls.clear()
    assert cli.main(["--voice", "none", "--button", "off", "--log", log]) == 0
    assert calls == []  # by default nothing is carried over, not even with --log


def reconcilable(tmp_path):
    log = tmp_path / "networks.jsonl"
    log.write_text(
        "".join(
            f'{{"time": "2026-09-05T17:0{minute}:00-03:00", "event": "scan", "networks": '
            f'[{{"ssid": "{name}", "bssid": "aa:bb:cc:dd:ee:0{index}", "signal_dbm": -50}}]}}\n'
            for index, (minute, name) in enumerate([(2, "A"), (4, "A"), (8, "B")])
        )
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text(
        "17:00 Start @ -34.90, -56.190\n"
        "17:05 Middle @ -34.90, -56.186\n"
        "17:10 End @ -34.90, -56.170\n"
    )
    return log, nb


def test_pace_defaults_to_movement_and_can_be_switched(tmp_path, capsys):
    log, nb = reconcilable(tmp_path)
    assert cli.main(["--reconcile", str(log), str(nb)]) == 0
    assert "Networks placed" in capsys.readouterr().out
    assert cli.main(["--reconcile", str(log), str(nb), "--pace", "clock"]) == 0
    assert "Networks placed" in capsys.readouterr().out


def test_check_pace_from_the_command_line(tmp_path, capsys):
    log, nb = reconcilable(tmp_path)
    assert cli.main(["--reconcile", str(log), str(nb), "--check-pace"]) == 0
    out = capsys.readouterr().out
    assert "Middle" in out and "by movement" in out and "mean error" in out


def test_check_passes_from_the_command_line(tmp_path, capsys):
    log = tmp_path / "networks.jsonl"
    log.write_text(
        "".join(
            f'{{"time": "2026-09-05T17:0{minute}:00-03:00", "event": "scan", "networks": '
            f'[{{"ssid": "Mid", "bssid": "aa:bb:cc:dd:ee:aa", "signal_dbm": {dbm}}}]}}\n'
            for minute, dbm in [(1, -70), (2, -40), (4, -70), (5, -40)]
        )
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:03 B\n17:06 A\n")  # una cuadra y la vuelta
    assert cli.main(["--reconcile", str(log), str(nb), "--check-passes"]) == 0
    out = capsys.readouterr().out
    assert '"A" to "B"' in out and "walked there" in out and "walked back" in out
    assert "the passes disagree by" in out and "shift in the direction of travel" in out

    # Con --check-pace se imprimen los dos informes, uno tras otro.
    assert cli.main(["--reconcile", str(log), str(nb), "--check-passes", "--check-pace"]) == 0
    both = capsys.readouterr().out
    assert "Nothing to check: this needs coordinates" in both and "walked back" in both


def test_check_pace_reports_errors_like_reconcile(tmp_path, capsys):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:10 B\n")
    assert cli.main(["--reconcile", str(empty), str(nb), "--check-pace"]) == 1


@pytest.mark.parametrize(
    "flags",
    [
        ["--pace", "clock"],
        ["--check-pace"],
        ["--check-passes"],
        ["--csv", "out.csv"],
        ["--scans"],
        ["--scans", "--csv", "out.csv"],
        ["--geojson", "out.geojson"],
    ],
)
def test_reconcile_flags_without_reconcile_are_an_error(flags, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", *flags])
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "only meaningful together with --reconcile" in err
    for flag in (f for f in flags if f.startswith("--")):
        assert flag in err


def test_pace_is_movement_unless_asked_otherwise(tmp_path, capsys):
    log, nb = reconcilable(tmp_path)
    assert cli.build_parser().parse_args([]).pace is None  # nada pasado: movimiento
    assert cli.main(["--reconcile", str(log), str(nb)]) == 0
    assert cli.main(["--reconcile", str(log), str(nb), "--pace", "movement"]) == 0
    assert "Networks placed" in capsys.readouterr().out


# --- the headset button -----------------------------------------------------------


class ButtonFakeMonitor:
    marks = 0
    used = None

    def __init__(self, **kwargs):
        self.voice = kwargs["voice"]

    def resume_from_log(self):
        return 0

    def use_button(self, marker):
        ButtonFakeMonitor.used = marker

    def close(self, timeout=5.0):
        self.closed = True

    def scan_networks_loop(self, cycles=0):
        pass


def spoken(voice):
    """What a BackgroundVoice with no engine was asked to say: it prints instead."""
    return voice


def test_button_list_prints_the_devices_and_exits(monkeypatch, capsys):
    from pathlib import Path

    from enodia.button import InputDevice

    monkeypatch.setattr(
        cli,
        "list_input_devices",
        lambda: [
            InputDevice(
                Path("/dev/input/event0"), "AT Translated Set 2 keyboard", frozenset({30, 164})
            ),
            InputDevice(Path("/dev/input/event5"), "Some Mouse", frozenset({272})),
        ],
    )
    assert cli.main(["--button", "list"]) == 0
    out = capsys.readouterr().out
    assert "/dev/input/event0\tAT Translated Set 2 keyboard\tmedia keys" in out
    assert "/dev/input/event5\tSome Mouse\t\n" in out


def test_button_off_means_no_marker(monkeypatch):
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    assert cli.main(["--voice", "none", "--button", "off"]) == 0
    assert ButtonFakeMonitor.used is None
    assert cli.make_button("off") is None


def test_button_auto_with_nothing_found_says_so(monkeypatch, capsys):
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    monkeypatch.setattr(cli, "find_button_devices", list)
    assert cli.main(["--voice", "none"]) == 0
    assert "No headset button found" in capsys.readouterr().out
    assert ButtonFakeMonitor.used is None


def test_button_auto_listens_to_what_it_finds(monkeypatch, capsys, tmp_path):
    import os

    from enodia.button import InputDevice

    fifo = tmp_path / "event7"
    os.mkfifo(fifo)
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    monkeypatch.setattr(
        cli, "find_button_devices", lambda: [InputDevice(fifo, "Headset", frozenset({164}))]
    )
    assert cli.main(["--voice", "none"]) == 0
    out = capsys.readouterr().out
    assert f"Button: {fifo}" in out and "Button ready" in out
    assert ButtonFakeMonitor.used is not None and ButtonFakeMonitor.used.paths == [fifo]
    ButtonFakeMonitor.used.close()


def test_button_path_takes_any_key(monkeypatch, capsys, tmp_path):
    import os

    fifo = tmp_path / "event3"
    os.mkfifo(fifo)
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    assert cli.main(["--voice", "none", "--button", str(fifo)]) == 0
    assert ButtonFakeMonitor.used.keys is None
    ButtonFakeMonitor.used.close()


def test_button_without_permission_is_announced(monkeypatch, capsys, tmp_path):
    from enodia import button as button_module

    def refuse(path, flags):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(button_module.os, "open", refuse)
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    assert cli.main(["--voice", "none", "--button", str(tmp_path / "event0")]) == 0
    out = capsys.readouterr().out
    assert "Button unavailable: no permission" in out and "'input' group" in out
    assert "Button unavailable" in out and ButtonFakeMonitor.used is None


def test_button_path_that_does_not_exist_is_announced(monkeypatch, capsys, tmp_path):
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    assert cli.main(["--voice", "none", "--button", str(tmp_path / "nope")]) == 0
    out = capsys.readouterr().out
    assert "Could not open" in out and "Button unavailable" in out


def test_geojson_from_the_command_line(tmp_path, capsys):
    log, nb = reconcilable(tmp_path)
    out = tmp_path / "walk.geojson"
    assert cli.main(["--reconcile", str(log), str(nb), "--geojson", str(out)]) == 0
    assert f"GeoJSON written to {out}" in capsys.readouterr().out
    assert out.read_text().startswith("{")


def test_the_default_log_is_one_file_per_outing(monkeypatch, capsys, tmp_path):
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert cli.main(["--voice", "none", "--button", "off"]) == 0
    out = capsys.readouterr().out
    assert "New outing: " in out and str(tmp_path / "xdg" / "enodia") in out
    assert cli.build_parser().parse_args([]).log is None


def test_an_explicit_log_is_used_as_given(monkeypatch, capsys, tmp_path):
    created = {}

    class Recording(ButtonFakeMonitor):
        def __init__(self, **kwargs):
            created.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(cli, "WifiMonitor", Recording)
    assert (
        cli.main(["--voice", "none", "--button", "off", "--log", str(tmp_path / "mine.jsonl")]) == 0
    )
    assert created["log"].path == tmp_path / "mine.jsonl"
    assert "New outing" not in capsys.readouterr().out


def test_preflight_from_the_command_line(monkeypatch, capsys, tmp_path):
    from enodia import preflight

    monkeypatch.setattr(preflight.ifpeek, "get_wifi_interfaces", lambda: ["wlan0"])
    monkeypatch.setattr(preflight.ifpeek, "interface_rfkill", lambda i: None)
    monkeypatch.setattr(
        preflight.ifpeek, "scan_access_points", lambda interface=None, fresh=False: [1, 2]
    )
    monkeypatch.setattr(preflight, "lid_switch_setting", lambda **kw: ("ignore", ()))
    monkeypatch.setattr(preflight, "battery", lambda *a: (80, "Discharging"))
    code = cli.main(
        ["--preflight", "--voice", "none", "--button", "off", "--log", str(tmp_path / "l.jsonl")]
    )
    out = capsys.readouterr().out
    assert code == 0 and "Ready to go." in out
    assert "WARN  voice" in out and "OK    button      off" in out

    monkeypatch.setattr(preflight, "battery", lambda *a: (5, "Discharging"))
    code = cli.main(["--preflight", "--voice", "none", "--button", "off"])
    out = capsys.readouterr().out
    assert code == 1 and "Do not go: battery." in out and "new outing: " in out


def test_color_only_on_a_terminal_and_never_with_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    assert not cli.use_color()
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    assert cli.use_color()
    monkeypatch.setenv("NO_COLOR", "1")
    assert not cli.use_color()
    monkeypatch.setenv("NO_COLOR", "")  # present but empty counts as unset
    assert cli.use_color()


def test_dir_puts_one_file_per_outing_where_asked(monkeypatch, capsys, tmp_path):
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    outings = tmp_path / "paseos"
    assert cli.main(["--voice", "none", "--button", "off", "--dir", str(outings)]) == 0
    out = capsys.readouterr().out
    assert f"New outing: {outings}/" in out and out.split("New outing: ")[1].startswith(
        str(outings)
    )
    assert outings.is_dir()


def test_log_and_dir_are_exclusive(tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--log", str(tmp_path / "a.jsonl"), "--dir", str(tmp_path)])
    assert stopped.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_preflight_checks_the_directory_asked_for(monkeypatch, capsys, tmp_path):
    from enodia import preflight

    monkeypatch.setattr(preflight.ifpeek, "get_wifi_interfaces", lambda: ["wlan0"])
    monkeypatch.setattr(preflight.ifpeek, "interface_rfkill", lambda i: None)
    monkeypatch.setattr(
        preflight.ifpeek, "scan_access_points", lambda interface=None, fresh=False: [1]
    )
    monkeypatch.setattr(preflight, "lid_switch_setting", lambda **kw: ("ignore", ()))
    monkeypatch.setattr(preflight, "battery", lambda *a: (80, "Discharging"))
    outings = tmp_path / "paseos"
    assert (
        cli.main(["--preflight", "--voice", "none", "--button", "off", "--dir", str(outings)]) == 0
    )
    assert f"new outing: {outings}/" in capsys.readouterr().out


def test_say_time_every_is_passed_in_seconds(monkeypatch):
    created = {}

    class FakeMonitor(ButtonFakeMonitor):
        def __init__(self, **kwargs):
            created.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(cli, "WifiMonitor", FakeMonitor)
    assert (
        cli.main(["--voice", "none", "--button", "off", "--say-time-every", "30", "--quiet"]) == 0
    )
    assert created["say_time_every"] == 30.0 and created["quiet"] is True


def test_no_hour_is_passed_and_contradicts_a_beat(monkeypatch, capsys):
    created = {}

    class FakeMonitor(ButtonFakeMonitor):
        def __init__(self, **kwargs):
            created.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(cli, "WifiMonitor", FakeMonitor)
    assert cli.main(["--voice", "none", "--button", "off", "--quiet", "--no-hour"]) == 0
    assert created["speak_time"] is False and created["quiet"] is True

    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", "--no-hour", "--say-time-every", "30"])
    assert stopped.value.code == 2
    assert "contradict" in capsys.readouterr().err


def test_resume_continues_a_recent_log_and_the_default_does_not(monkeypatch, capsys, tmp_path):
    import os
    import time

    outings = tmp_path / "paseos"
    outings.mkdir()
    recent = outings / "2026-09-14T17-00-00.jsonl"
    recent.write_text("{}\n")
    os.utime(recent, times=(time.time() - 60,) * 2)  # escrito hace un minuto
    ButtonFakeMonitor.used = None
    monkeypatch.setattr(cli, "WifiMonitor", ButtonFakeMonitor)
    assert cli.main(["--voice", "none", "--button", "off", "--dir", str(outings)]) == 0
    out = capsys.readouterr().out
    assert "New outing: " in out and str(recent) not in out
    assert cli.main(["--voice", "none", "--button", "off", "--dir", str(outings), "--resume"]) == 0
    assert f"Continuing {recent}" in capsys.readouterr().out


# --- the fingerprint map ------------------------------------------------------


def mapped(tmp_path):
    """A log walked down a block and back, its notebook, and where the map goes."""
    log = tmp_path / "paseo.jsonl"
    log.write_text(
        "".join(
            f'{{"time": "2026-09-05T17:0{minute}:00-03:00", "event": "scan", "networks": ['
            f'{{"ssid": "Casa", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": {dbm}}}, '
            f'{{"ssid": "Bar", "bssid": "aa:bb:cc:dd:ee:02", "signal_dbm": -70}}]}}\n'
            for minute, dbm in [(1, -40), (3, -70), (5, -70), (7, -40)]
        )
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Alfa @ -34.90, -56.200\n17:04 Bravo @ -34.90, -56.198\n17:08 Alfa\n")
    return log, nb, tmp_path / "mapa.jsonl"


def test_map_add_says_how_many_it_added_and_refuses_the_same_outing_twice(tmp_path, capsys):
    log, nb, mapa = mapped(tmp_path)
    assert cli.main(["--map-add", str(log), str(nb), "--map", str(mapa)]) == 0
    assert "4 fingerprints from the outing of 2026-09-05T17:01:00-03:00 added" in (
        capsys.readouterr().out
    )
    assert cli.main(["--map-add", str(log), str(nb), "--map", str(mapa)]) == 0
    assert "is already in" in capsys.readouterr().out


def test_map_add_says_when_no_scan_fell_between_two_crossings(tmp_path, capsys):
    log, nb, mapa = mapped(tmp_path)
    nb.write_text("19:00 Alfa\n19:10 Bravo\n")  # otra hora entera
    assert cli.main(["--map-add", str(log), str(nb), "--map", str(mapa)]) == 0
    assert "no scan of that log fell between two crossings" in capsys.readouterr().out


def test_map_add_reports_errors_like_reconcile(tmp_path, capsys):
    _, nb, mapa = mapped(tmp_path)
    code = cli.main(["--map-add", str(tmp_path / "no-existe.jsonl"), str(nb), "--map", str(mapa)])
    assert code == 1 and "error:" in capsys.readouterr().err


def test_map_add_takes_the_pace_flag_reconcile_takes(tmp_path, capsys):
    log, nb, mapa = mapped(tmp_path)
    assert cli.main(["--map-add", str(log), str(nb), "--map", str(mapa), "--pace", "clock"]) == 0
    assert "fingerprints from the outing" in capsys.readouterr().out


def test_locate_from_a_log_prints_and_speaks_where_you_are(tmp_path, capsys):
    log, nb, mapa = mapped(tmp_path)
    cli.main(["--map-add", str(log), str(nb), "--map", str(mapa)])
    capsys.readouterr()
    assert cli.main(["--locate", str(log), "--map", str(mapa), "--voice", "none"]) == 0
    out = capsys.readouterr().out
    assert "You are " in out and "fingerprints agree" in out
    assert "Say (Silent: False) > Alfa to Bravo" in out or "Say (Silent: False) > Alfa" in out


def test_locate_says_plainly_when_you_are_not_on_the_map(tmp_path, capsys):
    log, nb, mapa = mapped(tmp_path)
    cli.main(["--map-add", str(log), str(nb), "--map", str(mapa)])
    capsys.readouterr()
    elsewhere = tmp_path / "otra-ciudad.jsonl"
    elsewhere.write_text(
        '{"time": "2026-09-05T17:01:00-03:00", "event": "scan", "networks": '
        '[{"ssid": "Lejos", "bssid": "ff:ee:dd:cc:bb:aa", "signal_dbm": -50}]}\n'
    )
    assert cli.main(["--locate", str(elsewhere), "--map", str(mapa), "--voice", "none"]) == 0
    out = capsys.readouterr().out
    assert "Not on the map" in out and "Say (Silent: False) > Not on the map" in out


def test_locate_reports_a_log_it_cannot_read(tmp_path, capsys):
    _, _, mapa = mapped(tmp_path)
    code = cli.main(["--locate", str(tmp_path / "no-existe.jsonl"), "--map", str(mapa)])
    assert code == 1 and "error:" in capsys.readouterr().err


def test_locate_scans_live_when_given_no_log(monkeypatch, tmp_path, capsys):
    from enodia import fingerprint

    log, nb, mapa = mapped(tmp_path)
    cli.main(["--map-add", str(log), str(nb), "--map", str(mapa)])
    capsys.readouterr()
    monkeypatch.setattr(fingerprint.ifpeek, "get_wifi_interfaces", lambda: ["wlan0"])
    monkeypatch.setattr(fingerprint.ifpeek, "interface_rfkill", lambda interface: None)
    monkeypatch.setattr(
        fingerprint.ifpeek,
        "scan_access_points",
        lambda interface=None, fresh=False: [
            AccessPoint("Casa", "aa:bb:cc:dd:ee:01", 2412, -40, 80, "psk", False),
            AccessPoint("Bar", "aa:bb:cc:dd:ee:02", 2412, -70, 40, "psk", False),
        ],
    )
    assert cli.main(["--locate", "--map", str(mapa), "--voice", "none", "-i", "wlan0"]) == 0
    assert "You are " in capsys.readouterr().out


def test_locate_says_when_the_radio_is_switched_off(monkeypatch, tmp_path, capsys):
    from enodia import fingerprint

    _, _, mapa = mapped(tmp_path)
    monkeypatch.setattr(fingerprint.ifpeek, "interface_rfkill", lambda interface: "hard")
    code = cli.main(["--locate", "--map", str(mapa), "--voice", "none", "-i", "wlan0"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Cannot scan: wlan0: radio hard blocked" in out
    assert "Say (Silent: False) > Radio blocked" in out


def test_check_map_from_the_command_line_reports_both_methods(tmp_path, capsys):
    log, nb, mapa = mapped(tmp_path)
    cli.main(["--map-add", str(log), str(nb), "--map", str(mapa)])
    capsys.readouterr()
    assert cli.main(["--check-map", "--map", str(mapa)]) == 0
    out = capsys.readouterr().out
    assert "Map: 4 fingerprints" in out
    assert "by networks" in out and "and by signal" in out
    assert "settling ties" in out and "choosing the path" in out


def test_locate_from_a_log_can_take_the_path_the_scans_before_it_make_likeliest(tmp_path, capsys):
    log, nb, mapa = mapped(tmp_path)
    cli.main(["--map-add", str(log), str(nb), "--map", str(mapa)])
    capsys.readouterr()
    command = ["--locate", str(log), "--map", str(mapa), "--sequence", "path", "--voice", "none"]
    assert cli.main(command) == 0
    assert "You are " in capsys.readouterr().out
    assert cli.build_parser().parse_args([]).sequence == "tie"


def test_the_map_defaults_to_its_own_directory_under_the_data_directory(tmp_path, capsys):
    from enodia.system import map_path

    assert cli.main(["--check-map"]) == 0
    assert "Nothing to check" in capsys.readouterr().out
    assert map_path().parent.name == "map"  # nunca junto a los logs


@pytest.mark.parametrize(
    "flags",
    [
        ["--map", "m.jsonl"],
        ["--match", "signal"],
        ["--sequence", "path"],
        ["--map", "m.jsonl", "--match", "signal"],
    ],
)
def test_map_flags_without_a_map_command_are_an_error(flags, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", *flags])
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "only meaningful together with --map-add, --locate or --check-map" in err
    for flag in (f for f in flags if f.startswith("--")):
        assert flag in err


def test_pace_needs_reconcile_or_map_add(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", "--pace", "clock"])
    assert stopped.value.code == 2
    assert "only meaningful together with --reconcile or --map-add" in capsys.readouterr().err


def test_saying_a_location_names_the_crossing_when_you_are_on_top_of_it():
    from enodia.fingerprint import Location, Place

    said = []

    class Voice:
        def say(self, text, lang=None, **kw):
            said.append((text, lang))

    cli.say_location(Voice(), Location(Place("Alfa", "Bravo", 1.0), 1.0, 1, 0.0), "en-US", "es-ES")
    assert said == [("Bravo", "es-ES")]
    said.clear()
    cli.say_location(Voice(), Location(Place("Alfa", "Bravo", 0.0), 1.0, 1, 0.0), "en-US", "es-ES")
    assert said == [("Alfa", "es-ES")]


def test_saying_a_location_warns_when_two_streets_matched_alike():
    from enodia.fingerprint import Location, Place

    said = []

    class Voice:
        def say(self, text, lang=None, **kw):
            said.append(text)

    found = Location(
        Place("Alfa", "Bravo", 0.4), 0.5, 2, 0.1, alternative=Place("Charlie", "Delta", 0.8)
    )
    cli.say_location(Voice(), found, "en-US", "es-ES")
    assert said == ["Alfa to Bravo", "40 percent", "Uncertain"]


# --- putting the notebook on the map ------------------------------------------


def geocodable(tmp_path):
    """A notebook of corners, and a stand-in for the one network call."""
    nb = tmp_path / "libreta.txt"
    nb.write_text(
        "17:00:00 Agraciada y Freire\n17:02:00 Agraciada y Solari\n17:04:00 Plaza Independencia\n",
        encoding="utf-8",
    )
    answer = {
        "elements": [
            {"type": "way", "id": 1, "tags": {"name": "Agraciada"}, "nodes": [10, 12]},
            {"type": "way", "id": 2, "tags": {"name": "Freire"}, "nodes": [10]},
            {"type": "way", "id": 3, "tags": {"name": "Solari"}, "nodes": [12]},
            {"type": "node", "id": 10, "lat": -34.9000, "lon": -56.2000},
            {"type": "node", "id": 12, "lat": -34.9010, "lon": -56.1990},
        ]
    }
    return nb, lambda query, url=None, proxy=None: answer


def test_geocode_writes_a_second_notebook_and_leaves_the_first(monkeypatch, capsys, tmp_path):
    from enodia import geocode

    nb, answer = geocodable(tmp_path)
    before = nb.read_text(encoding="utf-8")
    monkeypatch.setattr(geocode, "post_overpass", answer)
    assert cli.main(["--geocode", str(nb), "--area", "Montevideo"]) == 0
    out = capsys.readouterr().out
    assert "2 lines gained coordinates" in out
    assert "line 3, Plaza Independencia: not a corner" in out
    assert nb.read_text(encoding="utf-8") == before
    assert "@ -34.900000, -56.200000" in (tmp_path / "libreta.geo.txt").read_text(encoding="utf-8")


def test_geocode_writes_where_out_says(monkeypatch, capsys, tmp_path):
    from enodia import geocode

    nb, answer = geocodable(tmp_path)
    monkeypatch.setattr(geocode, "post_overpass", answer)
    mine = tmp_path / "mia.txt"
    assert cli.main(["--geocode", str(nb), "--area", "Montevideo", "--out", str(mine)]) == 0
    assert mine.exists() and str(mine) in capsys.readouterr().out


def test_geocode_that_resolves_nothing_writes_nothing_and_exits_one(monkeypatch, capsys, tmp_path):
    from enodia import geocode

    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00:00 Plaza Independencia\n17:02:00 Plaza Cagancha\n", encoding="utf-8")
    monkeypatch.setattr(geocode, "post_overpass", lambda *a, **k: {"elements": []})
    assert cli.main(["--geocode", str(nb), "--area", "Montevideo"]) == 1
    assert "Nothing was resolved, so no notebook was written." in capsys.readouterr().out
    assert not (tmp_path / "libreta.geo.txt").exists()


def test_geocode_takes_its_times_from_a_marks_log(monkeypatch, capsys, tmp_path):
    from enodia import geocode

    nb, answer = geocodable(tmp_path)
    nb.write_text("#1 Agraciada y Freire\n#2 Agraciada y Solari\n", encoding="utf-8")
    log = tmp_path / "paseo.jsonl"
    log.write_text(
        '{"time": "2026-09-05T17:00:00-03:00", "event": "mark", "number": 1}\n'
        '{"time": "2026-09-05T17:02:00-03:00", "event": "mark", "number": 2}\n'
    )
    monkeypatch.setattr(geocode, "post_overpass", answer)
    assert cli.main(["--geocode", str(nb), "--area", "Montevideo", "--marks", str(log)]) == 0
    assert "1 stretch checked against the pace" in capsys.readouterr().out


def test_geocode_sends_the_request_through_the_proxy_it_was_given(monkeypatch, tmp_path):
    from enodia import geocode

    nb, answer = geocodable(tmp_path)
    seen = {}

    def fetch(query, url=None, proxy=None):
        seen.update(url=url, proxy=proxy)
        return answer(query)

    monkeypatch.setattr(geocode, "post_overpass", fetch)
    assert (
        cli.main(
            [
                "--geocode",
                str(nb),
                "--area",
                "Montevideo",
                "--proxy",
                "socks5://127.0.0.1:9050",
                "--overpass-url",
                "https://overpass.example/api/interpreter",
            ]
        )
        == 0
    )
    assert seen["proxy"] == geocode.Proxy("127.0.0.1", 9050)
    assert seen["url"] == "https://overpass.example/api/interpreter"


def test_geocode_reports_a_bad_proxy_without_a_traceback(capsys, tmp_path):
    nb, _ = geocodable(tmp_path)
    code = cli.main(["--geocode", str(nb), "--area", "Montevideo", "--proxy", "http://x:3128"])
    assert code == 1 and "only socks5" in capsys.readouterr().err


def test_geocode_without_an_area_is_an_error(capsys, tmp_path):
    nb, _ = geocodable(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--geocode", str(nb)])
    assert stopped.value.code == 2
    assert "--area is needed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags",
    [
        ["--area", "Montevideo"],
        ["--out", "x.txt"],
        ["--marks", "p.jsonl"],
        ["--proxy", "socks5://127.0.0.1:9050"],
        ["--overpass-url", "https://elsewhere.example/"],
        ["--area", "Montevideo", "--proxy", "socks5://127.0.0.1:9050"],
    ],
)
def test_geocode_flags_without_geocode_are_an_error(flags, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", *flags])
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "only meaningful together with --geocode" in err
    for flag in (f for f in flags if f.startswith("--")):
        assert flag in err


def test_geocode_writes_the_drawn_streets_when_asked(monkeypatch, capsys, tmp_path):
    from enodia import geocode
    from enodia.streets import read_streets

    nb, answer = geocodable(tmp_path)
    monkeypatch.setattr(geocode, "post_overpass", answer)
    calles = tmp_path / "calles.jsonl"
    assert not calles.exists()
    assert cli.main(["--geocode", str(nb), "--area", "Montevideo"]) == 0
    assert not calles.exists()  # sin la bandera no escribe nada

    (tmp_path / "libreta.geo.txt").unlink()
    code = cli.main(["--geocode", str(nb), "--area", "Montevideo", "--streets", str(calles)])
    assert code == 0 and "shapes into" in capsys.readouterr().out
    assert len(read_streets(calles)) == 1  # Agraciada, la única con dos nodos


def test_reconcile_places_scans_along_the_street_when_given_one(capsys, tmp_path):
    from enodia.streets import Street, write_streets

    log, nb = reconcilable(tmp_path)
    # Through the three crossings of `reconcilable`, bulging north between them.
    bend = (
        (-34.90, -56.190),
        (-34.8995, -56.188),
        (-34.90, -56.186),
        (-34.8995, -56.178),
        (-34.90, -56.170),
    )
    calles = tmp_path / "calles.jsonl"
    write_streets(calles, [Street("Curva", bend)])
    assert cli.main(["--reconcile", str(log), str(nb), "--scans"]) == 0
    plain = capsys.readouterr().out
    assert cli.main(["--reconcile", str(log), str(nb), "--scans", "--streets", str(calles)]) == 0
    assert capsys.readouterr().out != plain  # la calle movió los scans


def test_map_add_takes_the_streets_too(capsys, tmp_path):
    from enodia.streets import Street, write_streets

    log, nb = reconcilable(tmp_path)
    calles = tmp_path / "calles.jsonl"
    write_streets(calles, [Street("Curva", ((-34.90, -56.190), (-34.90, -56.170)))])
    mapa = tmp_path / "mapa.jsonl"
    code = cli.main(["--map-add", str(log), str(nb), "--map", str(mapa), "--streets", str(calles)])
    assert code == 0 and "fingerprints from the outing" in capsys.readouterr().out


def test_streets_without_a_command_that_uses_them_is_an_error(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", "--streets", "calles.jsonl"])
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "--streets: only meaningful together with --geocode, --reconcile or --map-add" in err


def test_reconcile_draws_the_walk_as_a_plan(capsys, tmp_path):
    from enodia.streets import Street, write_streets

    log, nb = reconcilable(tmp_path)
    calles = tmp_path / "calles.jsonl"
    write_streets(
        calles,
        [Street("Curva", ((-34.90, -56.190), (-34.90, -56.186), (-34.90, -56.170)))],
        [((-34.8998, -56.189), (-34.8998, -56.187), (-34.8996, -56.187), (-34.8998, -56.189))],
    )
    plan = tmp_path / "plano.svg"
    code = cli.main(
        ["--reconcile", str(log), str(nb), "--streets", str(calles), "--svg", str(plan)]
    )
    assert code == 0 and f"Plan drawn into {plan}" in capsys.readouterr().out
    drawn = plan.read_text(encoding="utf-8")
    assert drawn.startswith("<svg xmlns=") and ">Middle<" in drawn
    assert "#e7e1d8" in drawn  # la manzana


def test_a_notebook_of_bare_names_has_no_plan_to_draw(capsys, tmp_path):
    log, _ = reconcilable(tmp_path)
    nb = tmp_path / "sin-coordenadas.txt"
    nb.write_text("17:00 Start\n17:05 Middle\n17:10 End\n")
    plan = tmp_path / "plano.svg"
    assert cli.main(["--reconcile", str(log), str(nb), "--svg", str(plan)]) == 0
    assert "Nothing to draw: a plan needs coordinates" in capsys.readouterr().out
    assert not plan.exists()


@pytest.mark.parametrize(
    ("flags", "says"),
    [
        (["--buildings"], "--buildings: only meaningful together with --geocode and --streets"),
        (["--svg", "p.svg"], "--svg: only meaningful together with --reconcile"),
    ],
)
def test_drawing_flags_without_what_they_need_are_an_error(flags, says, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", *flags])
    assert stopped.value.code == 2
    assert says in capsys.readouterr().err


def test_locate_asks_every_radio_the_walk_would_use(monkeypatch, tmp_path, capsys):
    # The capture path and the reconciliation both work on the union of a
    # cycle's radios. Looking yourself up with one card's half is training on
    # the whole and querying with a fraction.
    from enodia import fingerprint

    asked = []
    monkeypatch.setattr(
        fingerprint.ifpeek, "get_wifi_interfaces", lambda: ["wlan0", "wlan1", "wlan2"]
    )
    monkeypatch.setattr(fingerprint.ifpeek, "interface_rfkill", lambda card: None)
    monkeypatch.setattr(
        fingerprint.ifpeek,
        "scan_access_points",
        lambda interface=None, fresh=False: asked.append(interface) or [],
    )
    mapa = tmp_path / "mapa.jsonl"
    mapa.write_text("")
    cli.main(["--locate", "--map", str(mapa), "--voice", "none", "-i", "wlan0", "-i", "wlan1"])
    assert asked == ["wlan0", "wlan1"]  # las dos que se pidieron, no solo la primera


def test_preflight_checks_the_radio_the_walk_will_actually_use(monkeypatch, capsys, tmp_path):
    from enodia import preflight

    monkeypatch.setattr(preflight.ifpeek, "get_wifi_interfaces", lambda: ["wlan0", "wlan1"])
    monkeypatch.setattr(preflight.ifpeek, "interface_rfkill", lambda i: None)
    asked = []

    def scan(interface=None, fresh=False):
        asked.append(interface)
        return [1, 2]

    monkeypatch.setattr(preflight.ifpeek, "scan_access_points", scan)
    monkeypatch.setattr(preflight, "lid_switch_setting", lambda **kw: ("ignore", ()))
    monkeypatch.setattr(preflight, "battery", lambda *a: (80, "Discharging"))
    cli.main(
        [
            "--preflight",
            "--voice",
            "none",
            "--button",
            "off",
            "-i",
            "wlan1",
            "--log",
            str(tmp_path / "l.jsonl"),
        ]
    )
    assert asked == ["wlan1"]  # no wlan0, que es la que el kernel lista primero
    assert "wlan1 (radio on)" in capsys.readouterr().out


def test_a_map_that_cannot_be_read_is_an_error_and_not_a_traceback(capsys, tmp_path):
    # read_map only calls a missing file empty. Everything else has to reach the
    # operator as a sentence about the file, the way --map-add already did.
    directory = tmp_path / "no-es-un-archivo"
    directory.mkdir()
    log, _ = reconcilable(tmp_path)
    for command in (["--check-map"], ["--locate", str(log), "--voice", "none"]):
        assert cli.main([*command, "--map", str(directory)]) == 1
        assert "error:" in capsys.readouterr().err


def test_streets_that_cannot_be_read_stop_the_command_instead_of_being_ignored(capsys, tmp_path):
    # Asked for the geometry and not given it, the answer is an error and not a
    # reconciliation quietly done on the straight lines it was told to stop using.
    log, nb = reconcilable(tmp_path)
    folder = tmp_path / "calles.jsonl"
    folder.mkdir()
    assert cli.main(["--reconcile", str(log), str(nb), "--streets", str(folder)]) == 1
    assert "error:" in capsys.readouterr().err
    assert cli.main(["--map-add", str(log), str(nb), "--streets", str(folder)]) == 1
    assert "error:" in capsys.readouterr().err


def two_walk_log(tmp_path):
    """One file, two walks, each with its own notebook line."""
    log = tmp_path / "walk.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-{day}T{hour}:0{minute}:00-03:00",
                    "event": "scan",
                    "cycle": 1 + minute,
                    "outing": walk,
                    "networks": [{"ssid": ssid, "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -50}],
                }
            )
            for day, hour, walk, ssid in (
                ("17", 17, "aaaa1111", "Casa"),
                ("18", 18, "bbbb2222", "Bar"),
            )
            for minute in (1, 5)
        )
        + "\n"
    )
    first = tmp_path / "primera.txt"
    first.write_text("date 2026-09-17\n17:00 A\n17:10 B\n")
    second = tmp_path / "segunda.txt"
    second.write_text("date 2026-09-18\n18:00 A\n18:10 B\n")
    return log, first, second


def test_reconcile_says_which_walk_of_the_file_it_read(capsys, tmp_path):
    log, first, second = two_walk_log(tmp_path)
    assert cli.main(["--reconcile", str(log), str(second)]) == 0
    out = capsys.readouterr().out
    assert "holds 2 walks. Reading bbbb2222, not aaaa1111." in out
    assert "Bar" in out and "Casa" not in out

    assert cli.main(["--reconcile", str(log), str(first), "--outing", "aaaa1111"]) == 0
    out = capsys.readouterr().out
    assert "Reading aaaa1111, not bbbb2222." in out
    assert "Casa" in out and "Bar" not in out


def test_a_walk_the_file_does_not_hold_is_an_error_not_an_empty_report(capsys, tmp_path):
    log, first, _ = two_walk_log(tmp_path)
    assert cli.main(["--reconcile", str(log), str(first), "--outing", "cccc3333"]) == 1
    assert "no walk called 'cccc3333'" in capsys.readouterr().err


def test_a_log_of_one_walk_says_nothing_about_choosing_it(capsys, tmp_path):
    log, nb = reconcilable(tmp_path)
    assert cli.main(["--reconcile", str(log), str(nb)]) == 0
    assert "walks. Reading" not in capsys.readouterr().out


def test_map_add_takes_the_walk_too(capsys, tmp_path):
    log, first, _ = two_walk_log(tmp_path)
    mapa = tmp_path / "mapa.jsonl"
    code = cli.main(["--map-add", str(log), str(first), "--map", str(mapa), "--outing", "aaaa1111"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Reading aaaa1111" in out and "/aaaa1111 added to" in out


def test_outing_without_a_command_that_reads_a_log_is_an_error(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", "--outing", "aaaa1111"])
    assert stopped.value.code == 2
    assert (
        "--outing: only meaningful together with --reconcile, --map-add, --export-public, "
        "--locate LOG or --geocode --marks" in capsys.readouterr().err
    )


def test_a_log_that_cannot_be_read_is_left_to_the_command_to_report(capsys, tmp_path):
    # say_which_walk opens the file first, and a failure there is not its news
    # to break: the command that follows reports it with its own message.
    missing = tmp_path / "no-esta.jsonl"
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:10 B\n")
    assert cli.main(["--reconcile", str(missing), str(nb)]) == 1
    captured = capsys.readouterr()
    assert "walks. Reading" not in captured.out
    assert "error:" in captured.err


def test_a_destination_that_cannot_be_written_to_is_an_error_not_a_traceback(capsys, tmp_path):
    # The report has already printed by then, which makes a traceback after it
    # the worst of both: an answer on the screen and a crash under it.
    log, nb = reconcilable(tmp_path)
    folder = tmp_path / "un-directorio"
    folder.mkdir()
    for flag in ("--csv", "--geojson", "--svg"):
        assert cli.main(["--reconcile", str(log), str(nb), flag, str(folder)]) == 1
        assert "error:" in capsys.readouterr().err


def test_geocode_reports_a_streets_file_it_cannot_write(monkeypatch, capsys, tmp_path):
    from enodia import geocode

    nb, answer = geocodable(tmp_path)
    monkeypatch.setattr(geocode, "post_overpass", answer)
    folder = tmp_path / "un-directorio"
    folder.mkdir()
    code = cli.main(["--geocode", str(nb), "--area", "Montevideo", "--streets", str(folder)])
    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_geocode_takes_the_marks_of_one_walk_of_the_log(monkeypatch, capsys, tmp_path):
    # The same bug as the reconciliation's, hidden one subsystem over: an old
    # notebook geocoded against a file of two walks came back with the times of
    # the other one.
    from enodia import geocode

    nb, answer = geocodable(tmp_path)
    nb.write_text("#1 Agraciada y Freire\n#2 Agraciada y Solari\n", encoding="utf-8")
    monkeypatch.setattr(geocode, "post_overpass", answer)
    marks = tmp_path / "walk.jsonl"
    marks.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-{day}T{hour}:{minute:02d}:00-03:00",
                    "event": "mark",
                    "number": number,
                    "outing": walk,
                }
            )
            for day, hour, walk in (("17", 17, "aaaa1111"), ("18", 18, "bbbb2222"))
            for number, minute in ((1, 0), (2, 10))
        )
        + "\n"
    )
    code = cli.main(
        [
            "--geocode",
            str(nb),
            "--area",
            "Montevideo",
            "--marks",
            str(marks),
            "--outing",
            "aaaa1111",
        ]
    )
    assert code == 0
    assert "holds 2 walks. Reading aaaa1111, not bbbb2222." in capsys.readouterr().out


def test_the_preflight_closes_the_voice_it_opened(monkeypatch, tmp_path):
    # It builds one only so that `check_voice` has something to speak through,
    # and that voice queues its speech on a thread of its own. Leaving the
    # thread behind is invisible in a command that exits straight afterwards,
    # and is not in one that comes back to a menu and runs the check again.
    from enodia import preflight

    closed = []
    real = cli.make_voice

    def watched(name):
        made = real(name)
        monkeypatch.setattr(made, "close", lambda: closed.append(name))
        return made

    monkeypatch.setattr(cli, "make_voice", watched)
    monkeypatch.setattr(preflight.ifpeek, "get_wifi_interfaces", lambda: ["wlan0"])
    monkeypatch.setattr(preflight.ifpeek, "interface_rfkill", lambda i: None)
    monkeypatch.setattr(
        preflight.ifpeek, "scan_access_points", lambda interface=None, fresh=False: [1]
    )
    monkeypatch.setattr(preflight, "lid_switch_setting", lambda **kw: ("ignore", ()))
    monkeypatch.setattr(preflight, "battery", lambda *a: (80, "Discharging"))
    monkeypatch.setattr(voice_module.shutil, "which", lambda name: None)
    cli.main(["--preflight", "--voice", "espeak", "--button", "off", "--log", str(tmp_path / "l")])
    assert closed == ["espeak"]


def test_ctrl_c_while_the_outing_is_starting_does_not_end_in_a_traceback(monkeypatch, capsys):
    # The handler used to cover only the scan loop, so Ctrl+C while the speech
    # engine was starting, while the log was read back, or while a headset was
    # opened came out as a traceback. Harmless for a process that was ending
    # anyway, and the end of an assistant that expected control back.
    def refuse(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "WifiMonitor", refuse)
    assert cli.main(["--voice", "none", "--button", "off", "--log", "walk.jsonl"]) == 0
    assert capsys.readouterr().out.endswith("\nStopped.\nLog: walk.jsonl\n")


def test_a_monitor_that_was_never_built_still_closes_the_voice_it_was_given(monkeypatch, capsys):
    closed = []
    real = cli.make_voice

    def watched(name):
        made = real(name)
        monkeypatch.setattr(made, "close", lambda: closed.append(name))
        return made

    def refuse(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "make_voice", watched)
    monkeypatch.setattr(cli, "WifiMonitor", refuse)
    assert cli.main(["--voice", "none", "--button", "off", "--log", "walk.jsonl"]) == 0
    assert closed == ["none"]


def test_an_interrupted_outing_closes_the_monitor_it_built(monkeypatch, capsys):
    # The monitor closes the voice it holds, the button it was given, and waits
    # for its own thread. A second outing started from the same run needs all
    # three to have happened.
    class FakeMonitor:
        marks = 0
        closed = False

        def __init__(self, **kwargs):
            pass

        def resume_from_log(self):
            return 0

        def scan_networks_loop(self, cycles=0):
            raise KeyboardInterrupt

        def close(self, timeout=5.0):
            FakeMonitor.closed = True

    monkeypatch.setattr(cli, "WifiMonitor", FakeMonitor)
    assert cli.main(["--voice", "none", "--button", "off", "--log", "walk.jsonl"]) == 0
    assert FakeMonitor.closed
    assert capsys.readouterr().out.endswith("\nStopped.\nLog: walk.jsonl\n")


def test_bare_enodia_still_walks_and_the_menu_needs_its_own_flag(monkeypatch, capsys):
    # Nothing that already works changes behaviour. Somebody who types `enodia`
    # expecting to scan is not handed a menu.
    walked = []
    monkeypatch.setattr(cli, "run_walk", lambda args: walked.append(args) or 0)
    assert cli.main(["--voice", "none", "--button", "off", "--log", "walk.jsonl"]) == 0
    assert len(walked) == 1

    from enodia import assistant

    asked = []
    monkeypatch.setattr(assistant, "run_assistant", lambda args: asked.append(args) or 0)
    assert cli.main(["--assistant", "--voice", "none", "--button", "off"]) == 0
    assert len(asked) == 1 and len(walked) == 1


@pytest.mark.parametrize(
    "flags",
    [
        ["--preflight"],
        ["--button", "list"],
        ["--open-networks", "walk.jsonl"],
        ["--reconcile", "walk.jsonl", "libreta.txt"],
        ["--map-add", "walk.jsonl", "libreta.txt"],
        ["--locate"],
        ["--check-map"],
        ["--geocode", "libreta.txt", "--area", "Montevideo"],
    ],
)
def test_the_assistant_refuses_the_commands_it_is_the_menu_for(flags, capsys):
    # Two instructions at once. Running the command instead of the menu would
    # be answering a question nobody asked.
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--assistant", *flags])
    assert stopped.value.code == 2
    assert "is a command of its own, and the assistant is the menu that runs them" in (
        capsys.readouterr().err
    )


def test_the_assistant_may_be_told_which_map_and_which_directory(monkeypatch):
    # It reaches the map and the logs, so naming either is meaningful with it,
    # where naming one without a map command is still an error.
    from enodia import assistant

    seen = []
    monkeypatch.setattr(assistant, "run_assistant", lambda args: seen.append(args) or 0)
    assert cli.main(["--assistant", "--map", "otro.jsonl", "--dir", "~/paseos"]) == 0
    assert seen[0].map == "otro.jsonl" and seen[0].dir == "~/paseos"

    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", "--map", "otro.jsonl"])
    assert stopped.value.code == 2
