"""Tests for the preflight. ifpeek is stood in for; every file root is temporary."""

from typing import ClassVar

from conftest import as_root

from enodia import preflight
from enodia.button import ButtonMarker
from enodia.preflight import (
    FAIL,
    OK,
    WARN,
    Check,
    check_battery,
    check_button,
    check_interfaces,
    check_lid,
    check_log,
    check_scan,
    check_scan_mac,
    check_voice,
    format_preflight,
    run_preflight,
)
from enodia.voice import VoiceController, VoiceUnavailable


class Radio:
    def __init__(self, interfaces=("wlan0",), blocked=(None,), networks=3, error=None):
        self.interfaces, self.blocked, self.networks, self.error = (
            list(interfaces),
            dict(zip(interfaces, blocked, strict=False)),
            networks,
            error,
        )

    def install(self, monkeypatch):
        monkeypatch.setattr(preflight.ifpeek, "get_wifi_interfaces", lambda: list(self.interfaces))
        monkeypatch.setattr(preflight.ifpeek, "interface_rfkill", lambda i: self.blocked.get(i))

        def scan(interface=None, fresh=False):
            if self.error:
                raise self.error
            return [object()] * self.networks

        monkeypatch.setattr(preflight.ifpeek, "scan_access_points", scan)
        return self


def test_interfaces_radio_on_blocked_and_missing(monkeypatch):
    Radio().install(monkeypatch)
    assert check_interfaces() == Check("interfaces", OK, "wlan0 (radio on)")
    Radio(("wlan0", "wlan1"), (None, "soft")).install(monkeypatch)
    assert check_interfaces() == Check("interfaces", WARN, "wlan0 (radio on), wlan1 (soft blocked)")
    Radio(("wlan0",), ("hard",)).install(monkeypatch)
    assert check_interfaces() == Check("interfaces", FAIL, "wlan0 (hard blocked): no radio on")
    Radio(()).install(monkeypatch)
    assert check_interfaces().status == FAIL


def test_scan_ok_empty_refused_and_impossible(monkeypatch):
    Radio(networks=7).install(monkeypatch)
    assert check_scan() == Check("scan", OK, "wlan0: 7 networks found by a fresh scan")
    Radio(networks=0).install(monkeypatch)
    assert check_scan().status == WARN
    Radio(error=PermissionError("not in group 'network'")).install(monkeypatch)
    failed = check_scan()
    assert failed.status == FAIL and "not in group" in failed.detail
    Radio(()).install(monkeypatch)
    assert check_scan() == Check("scan", FAIL, "nothing to scan with")


def test_the_preflight_tries_the_radios_the_walk_will_use(monkeypatch):
    # Checking whichever card the kernel lists first, when -i wlan1 says the
    # walk is on the other one, reports on a radio nobody is taking anywhere.
    Radio(("wlan0", "wlan1"), (None, None), networks=7).install(monkeypatch)
    assert check_interfaces(["wlan1"]).detail == "wlan1 (radio on)"
    assert check_scan(["wlan1"]).detail == "wlan1: 7 networks found by a fresh scan"


def test_one_radio_that_cannot_scan_is_a_warning_and_not_a_reason_to_stay_home(monkeypatch):
    # The monitor asks every interface and carries on with whichever answers.
    broken = Radio(("wlan0", "wlan1"), (None, None), networks=7)
    original = broken.install(monkeypatch)

    def scan(interface=None, fresh=False):
        if interface == "wlan0":
            raise PermissionError("first radio broken")
        return [object()] * 7

    monkeypatch.setattr(preflight.ifpeek, "scan_access_points", scan)
    found = check_scan()
    assert found.status == WARN
    assert "wlan1: 7 networks" in found.detail and "wlan0: first radio broken" in found.detail
    assert original is broken


class Engine:
    said: ClassVar[list[str]] = []

    def say(self, text, lang="en-US"):
        Engine.said.append(text)


class MissingEngine:
    def say(self, text, lang="en-US"):
        raise VoiceUnavailable("espeak-ng is not installed")


def test_voice_actually_speaks(capsys):
    Engine.said.clear()
    assert check_voice(VoiceController(Engine())) == Check("voice", OK, "Engine spoke")
    assert Engine.said == ["preflight"]
    assert check_voice(None).status == WARN
    assert check_voice(VoiceController()).status == WARN
    failed = check_voice(VoiceController(MissingEngine()))
    assert failed.status == FAIL and "not installed" in failed.detail


def test_lid(tmp_path):
    conf = tmp_path / "logind.conf"
    conf.write_text("[Login]\nHandleLidSwitch=ignore\n")
    assert check_lid(conf, ()).status == OK
    conf.write_text("[Login]\nHandleLidSwitch=suspend\n")
    assert check_lid(conf, ()).status == FAIL
    conf.write_text("[Login]\n")
    unset = check_lid(conf, ())
    assert unset.status == FAIL and "HandleLidSwitch=ignore" in unset.detail
    assert "desktop environment" in unset.detail


def test_button_off_none_ok_and_denied(tmp_path, monkeypatch):
    import os

    assert check_button(None, "off") == Check("button", OK, "off")
    assert check_button(None, "auto").status == WARN
    fifo = tmp_path / "event4"
    os.mkfifo(fifo)
    assert check_button(ButtonMarker([fifo]), "auto") == Check("button", OK, str(fifo))
    assert check_button(ButtonMarker([tmp_path / "nope"]), str(tmp_path / "nope")).status == FAIL
    from enodia import button as button_module

    monkeypatch.setattr(
        button_module.os, "open", lambda p, f: (_ for _ in ()).throw(PermissionError(13, "denied"))
    )
    denied = check_button(ButtonMarker([fifo]), "auto")
    assert denied.status == FAIL and "'input' group" in denied.detail


def supply(root, percent, status="Discharging"):
    (root / "BAT0").mkdir(parents=True)
    (root / "BAT0" / "type").write_text("Battery\n")
    (root / "BAT0" / "capacity").write_text(f"{percent}\n")
    (root / "BAT0" / "status").write_text(f"{status}\n")
    return root


def test_battery_levels(tmp_path):
    assert check_battery(supply(tmp_path / "a", 87)) == Check("battery", OK, "87%, discharging")
    assert check_battery(supply(tmp_path / "b", 35)).status == WARN
    low = check_battery(supply(tmp_path / "c", 12))
    assert low.status == FAIL and "not enough" in low.detail
    assert check_battery(tmp_path / "none").status == WARN


def test_log_explicit_or_per_outing(tmp_path, monkeypatch):
    assert check_log(tmp_path / "mine.jsonl") == Check("log", OK, str(tmp_path / "mine.jsonl"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    fresh = check_log(None)
    assert fresh.status == OK and fresh.detail.startswith("new outing: ")
    assert str(tmp_path / "xdg" / "enodia") in fresh.detail


def test_log_directory_that_cannot_be_made(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("XDG_DATA_HOME", str(blocker))  # a file where a directory must go
    assert check_log(None).status == FAIL


def test_run_and_format(tmp_path, monkeypatch):
    Radio().install(monkeypatch)
    conf = tmp_path / "logind.conf"
    conf.write_text("[Login]\nHandleLidSwitch=ignore\n")
    checks = run_preflight(
        VoiceController(Engine()),
        None,
        "off",
        tmp_path / "log.jsonl",
        lid_conf=conf,
        lid_dropins=(),
        battery_root=supply(tmp_path / "power", 90),
    )
    assert [c.name for c in checks] == [
        "interfaces",
        "scan",
        "voice",
        "lid",
        "scan mac",
        "button",
        "battery",
        "log",
    ]
    assert [c.name for c in checks if c.status != OK] == ["scan mac"]  # sin daemon conocido
    report = format_preflight(checks)
    assert report.endswith("Ready to go.") and "OK    interfaces  wlan0 (radio on)" in report

    conf.write_text("[Login]\n")
    checks = run_preflight(
        None,
        None,
        "auto",
        None,
        lid_conf=conf,
        lid_dropins=(),
        battery_root=supply(tmp_path / "low", 9),
    )
    report = format_preflight(checks)
    assert report.endswith("Do not go: lid, battery.")


def test_color_paints_status_and_verdict():
    checks = [Check("scan", OK, "fine"), Check("lid", FAIL, "suspends")]
    plain = format_preflight(checks)
    assert "\033[" not in plain
    painted = format_preflight(checks, color=True)
    assert "\033[32mOK   \033[0m scan" in painted
    assert "\033[31mFAIL \033[0m lid" in painted
    assert painted.endswith("\033[1m\033[31mDo not go: lid.\033[0m")
    assert format_preflight([checks[0]], color=True).endswith("\033[1m\033[32mReady to go.\033[0m")


def test_log_check_uses_the_directory_given(tmp_path):
    outings = tmp_path / "paseos"
    fresh = check_log(None, outings)
    assert fresh.status == OK and fresh.detail.startswith(f"new outing: {outings}/")


def test_log_check_continues_a_recent_log_only_when_asked(tmp_path, monkeypatch):
    import os
    import time

    Radio().install(monkeypatch)

    outings = tmp_path / "paseos"
    outings.mkdir()
    recent = outings / "2026-09-14T17-00-00.jsonl"
    recent.write_text("{}\n")
    os.utime(recent, times=(time.time() - 60,) * 2)  # escrito hace un minuto
    assert check_log(None, outings).detail.startswith("new outing: ")
    assert check_log(None, outings, resume=True).detail == f"continuing {recent}"
    assert run_preflight(None, None, "off", None, log_dir=outings, resume=True)[-1].detail == (
        f"continuing {recent}"
    )


class TestScanMac:
    """What the daemons were told about the address a scan goes out under."""

    def _where(self, tmp_path, value=None, iwd=None):
        lib, etc = tmp_path / "lib", tmp_path / "etc"
        lib.mkdir(parents=True, exist_ok=True)
        etc.mkdir(parents=True, exist_ok=True)
        conf = tmp_path / "NetworkManager.conf"
        conf.write_text("[device]\n" + (f"wifi.scan-rand-mac-address={value}\n" if value else ""))
        where = tmp_path / "iwd.conf"
        if iwd:
            where.write_text(f"[General]\nAddressRandomization={iwd}\n")
        return {
            "nm_conf": conf,
            "nm_before": (lib,),
            "nm_after": (etc,),
            "nm_intern": tmp_path / "intern.conf",
            "iwd_conf": where,
        }

    def test_nothing_configured_at_all_is_unknown_and_not_an_ok(self, tmp_path):
        # Scanning also works through wpa_supplicant, which leaves neither of
        # these files behind. Answering with NetworkManager's default would be
        # answering for a daemon that may not be the one running, which is the
        # failure this check exists to catch.
        found = check_scan_mac(**self._where(tmp_path))
        assert found.status == WARN
        assert found.detail.startswith("unknown: no NetworkManager or iwd configuration found")
        assert "wpa_supplicant" in found.detail

    def test_randomisation_turned_off_is_a_warning_that_says_what_it_costs(self, tmp_path):
        found = check_scan_mac(**self._where(tmp_path, value="no"))
        assert found.status == WARN
        assert "wifi.scan-rand-mac-address=no" in found.detail
        assert "under this card's own address" in found.detail
        assert "Only the daemon you actually run" not in found.detail

    def test_iwd_is_reported_as_the_interface_address_and_not_as_the_scan(self, tmp_path):
        # iwd documents AddressRandomization as the address the interface uses.
        # Saying it is what NetworkManager's scanning setting controls would
        # claim more than the manual does.
        found = check_scan_mac(**self._where(tmp_path, iwd="disabled"))
        assert found.status == WARN
        assert "about the address the interface uses" in found.detail
        assert "not established here, so check that yourself" in found.detail
        assert "goes out under this card's own address" not in found.detail

    def test_two_daemons_configured_at_once_says_only_one_is_yours(self, tmp_path):
        found = check_scan_mac(**self._where(tmp_path, value="no", iwd="disabled"))
        assert found.status == WARN
        assert "Only the daemon you actually run is about you." in found.detail

    def test_only_networkmanager_earns_the_word_randomised(self, tmp_path):
        # And only on its own. iwd's setting is about the interface address, and
        # what it does for scanning is not something this has a source for, so
        # it is never half of an OK either.
        found = check_scan_mac(**self._where(tmp_path, value="yes"))
        assert found.status == OK
        assert found.detail == "NetworkManager randomises it (wifi.scan-rand-mac-address=yes)"

        both = check_scan_mac(**self._where(tmp_path, value="yes", iwd="network"))
        assert both.status == WARN
        assert "AddressRandomization=network" in both.detail
        assert "not established here" in both.detail

    def test_a_value_that_is_neither_a_yes_nor_a_no_is_not_a_yes(self, tmp_path):
        # The setting is a boolean. Reading anything that is not an off as an on
        # turned `wifi.scan-rand-mac-address=banana` into a green line saying
        # the scans are randomised, which is the answer this exists not to give.
        found = check_scan_mac(**self._where(tmp_path, value="banana"))
        assert found.status == WARN
        assert "neither of the values it takes" in found.detail
        for yes in ("yes", "true", "1", "on"):
            assert check_scan_mac(**self._where(tmp_path, value=yes)).status == OK
        for no in ("no", "false", "0", "off"):
            assert check_scan_mac(**self._where(tmp_path, value=no)).status == WARN

    def test_device_sections_that_disagree_are_reported_and_not_collapsed(self, tmp_path):
        where = self._where(tmp_path)
        where["nm_conf"].write_text(
            "[device-wlan0]\nwifi.scan-rand-mac-address=yes\n"
            "[device-wlan1]\nwifi.scan-rand-mac-address=no\n"
        )
        found = check_scan_mac(**where)
        assert found.status == WARN
        assert "[device-wlan0]=yes, [device-wlan1]=no" in found.detail
        assert "depends on match-device and is not read here" in found.detail

    def test_the_preflight_asks_about_it(self, monkeypatch, tmp_path):
        Radio().install(monkeypatch)
        names = [check.name for check in run_preflight(None, None, "off", tmp_path / "l.jsonl")]
        assert "scan mac" in names


def test_a_log_that_cannot_be_written_to_fails_before_the_walk(tmp_path):
    # Taking the path on trust is what lets a preflight pass and the first scan
    # of the outing fail, two hours later, with the screen shut in a bag.
    good = tmp_path / "walk.jsonl"
    assert check_log(good) == Check("log", OK, str(good))
    assert good.exists()  # abierto de verdad, no mirado

    nowhere = tmp_path / "no-existe" / "walk.jsonl"
    found = check_log(nowhere)
    assert found.status == FAIL and "cannot be written to" in found.detail


def test_a_radio_switch_that_cannot_be_read_is_said_and_not_raised(monkeypatch):
    # A command whose whole job is to report has no business ending in a
    # traceback because one sysfs read went wrong.
    Radio().install(monkeypatch)

    def explode(interface):
        raise OSError("sysfs vanished")

    monkeypatch.setattr(preflight.ifpeek, "interface_rfkill", explode)
    assert check_interfaces() == Check(
        "interfaces", WARN, "wlan0 (switch unreadable: sysfs vanished): no radio known to be on"
    )

    Radio(("wlan0", "wlan1"), (None, None)).install(monkeypatch)
    monkeypatch.setattr(
        preflight.ifpeek, "interface_rfkill", lambda i: None if i == "wlan0" else explode(i)
    )
    mixed = check_interfaces()
    assert mixed.status == WARN
    assert mixed.detail == "wlan0 (radio on), wlan1 (switch unreadable: sysfs vanished)"


def test_a_configuration_file_behind_a_predicate_is_said_and_not_assumed(tmp_path):
    # Whether NetworkManager loads that file decides whether what it sets
    # applies, and the predicate depends on the daemon's version and its
    # environment, neither of which is read here.
    lib, etc = tmp_path / "lib", tmp_path / "etc"
    lib.mkdir(parents=True)
    etc.mkdir(parents=True)
    (lib / "10-base.conf").write_text("[device]\nwifi.scan-rand-mac-address=yes\n")
    (etc / "90-later.conf").write_text(
        "[.config]\nenable=env:TAG\n\n[device]\nwifi.scan-rand-mac-address=no\n"
    )
    found = check_scan_mac(
        nm_conf=tmp_path / "NetworkManager.conf",
        nm_before=(lib,),
        nm_after=(etc,),
        nm_intern=tmp_path / "intern.conf",
        iwd_conf=tmp_path / "iwd.conf",
    )
    assert found.status == WARN
    assert "90-later.conf" in found.detail
    assert "is not established" in found.detail


@as_root
def test_a_lid_file_that_cannot_be_read_is_not_a_lid_that_is_safe(tmp_path):
    # logind reads that file with its own privileges and this does not, so
    # reporting what the readable files said is a green line about a
    # configuration that may not be the machine's.
    conf = tmp_path / "logind.conf"
    conf.write_text("[Login]\nHandleLidSwitch=ignore\n")
    dropins = tmp_path / "conf.d"
    dropins.mkdir()
    (dropins / "10-base.conf").write_text("[Login]\nHandleLidSwitch=ignore\n")
    private = dropins / "90-private.conf"
    private.write_text("[Login]\nHandleLidSwitch=suspend\n")
    private.chmod(0o000)
    try:
        found = check_lid(conf=conf, dropins=(dropins,))
    finally:
        private.chmod(0o600)
    assert found.status == WARN
    assert "90-private.conf" in found.detail
    assert "cannot say" in found.detail


class TestScanMacFalseGreens:
    """The three ways a privacy check could come back green and be wrong."""

    def _where(self, tmp_path, main):
        (tmp_path / "NetworkManager.conf").write_text(main)
        return {
            "nm_conf": tmp_path / "NetworkManager.conf",
            "nm_before": (),
            "nm_after": (),
            "nm_intern": tmp_path / "intern.conf",
            "iwd_conf": tmp_path / "iwd.conf",
        }

    def test_the_setting_in_a_section_that_does_not_read_it_does_nothing(self, tmp_path):
        # The key parses in any section and only does anything in a device one,
        # so the same line under [main] was reported as the machine's setting.
        found = check_scan_mac(**self._where(tmp_path, "[main]\nwifi.scan-rand-mac-address=yes\n"))
        assert found.status == WARN
        assert "not a section NetworkManager reads device properties from" in found.detail

    def test_a_mask_means_only_some_of_the_address_varies(self, tmp_path):
        # Confirmed against NetworkManager's manual: the mask fixes some bits of
        # the generated address and randomises only the rest.
        found = check_scan_mac(
            **self._where(
                tmp_path,
                "[device]\nwifi.scan-rand-mac-address=yes\n"
                "wifi.scan-generate-mac-address-mask=FF:FF:FF:FF:FF:FF\n",
            )
        )
        assert found.status == WARN
        assert "fixes some bits of the scanning address" in found.detail

    @as_root
    def test_a_file_that_cannot_be_read_is_not_a_file_that_says_nothing(self, tmp_path):
        etc = tmp_path / "etc"
        etc.mkdir()
        private = etc / "90-private.conf"
        private.write_text("[device]\nwifi.scan-rand-mac-address=no\n")
        private.chmod(0o000)
        where = self._where(tmp_path, "[device]\nwifi.scan-rand-mac-address=yes\n")
        where["nm_after"] = (etc,)
        try:
            found = check_scan_mac(**where)
        finally:
            private.chmod(0o600)
        assert found.status == WARN
        assert "90-private.conf (no permission to read it)" in found.detail
        assert "actually in force is not established" in found.detail
