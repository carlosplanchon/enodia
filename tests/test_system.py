"""Tests for what Enodia reads about the machine. Every root is a temporary directory."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from enodia.system import (
    battery,
    data_dir,
    lid_switch_setting,
    scan_mac_setting,
    session_log_path,
    session_logs,
)

TZ = timezone(timedelta(hours=-3))


def test_data_dir_follows_xdg_or_falls_back_to_home(tmp_path):
    assert data_dir({"XDG_DATA_HOME": "/somewhere/data"}) == Path("/somewhere/data/enodia")
    assert data_dir({}, home=tmp_path) == tmp_path / ".local" / "share" / "enodia"
    assert (
        data_dir({"XDG_DATA_HOME": ""}, home=tmp_path) == tmp_path / ".local" / "share" / "enodia"
    )


def test_a_fresh_directory_gets_a_new_log_named_by_the_time(tmp_path):
    now = datetime(2026, 9, 14, 17, 45, 3, tzinfo=TZ)
    path, continuing = session_log_path(tmp_path / "logs", now)
    assert (path.name, continuing) == ("2026-09-14T17-45-03.jsonl", False)
    assert (tmp_path / "logs").is_dir()


def test_a_log_written_to_recently_is_this_outing(tmp_path):
    recent = tmp_path / "2026-09-14T17-00-00.jsonl"
    recent.write_text("{}\n")
    now = datetime.now().astimezone()
    os.utime(recent, times=((now - timedelta(minutes=5)).timestamp(),) * 2)
    path, continuing = session_log_path(tmp_path, now, resume=True)
    assert (path, continuing) == (recent, True)


def test_a_log_gone_quiet_belongs_to_a_past_outing(tmp_path):
    old = tmp_path / "2026-09-13T17-00-00.jsonl"
    old.write_text("{}\n")
    now = datetime.now().astimezone()
    os.utime(old, times=((now - timedelta(hours=3)).timestamp(),) * 2)
    path, continuing = session_log_path(tmp_path, now, resume=True)
    assert path != old and not continuing
    assert path.parent == tmp_path and path.suffix == ".jsonl"


def test_the_newest_log_is_the_one_considered(tmp_path):
    now = datetime.now().astimezone()
    older, newer = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    for path, minutes in ((older, 200), (newer, 10)):
        path.write_text("{}\n")
        os.utime(path, times=((now - timedelta(minutes=minutes)).timestamp(),) * 2)
    assert session_log_path(tmp_path, now, resume=True) == (newer, True)


def power_supply(root, **supplies):
    for name, files in supplies.items():
        (root / name).mkdir(parents=True)
        for file, value in files.items():
            (root / name / file).write_text(f"{value}\n")
    return root


def test_battery_reads_the_first_battery(tmp_path):
    root = power_supply(
        tmp_path,
        AC={"type": "Mains", "online": 0},
        BAT0={"type": "Battery", "capacity": 63, "status": "Discharging"},
        BAT1={"type": "Battery", "capacity": 100, "status": "Full"},
    )
    assert battery(root) == (63, "Discharging")


def test_battery_without_one(tmp_path):
    assert battery(power_supply(tmp_path, AC={"type": "Mains"})) is None
    assert battery(tmp_path / "missing") is None


def test_battery_skips_what_it_cannot_read(tmp_path):
    root = power_supply(
        tmp_path,
        BAT0={"type": "Battery", "capacity": "unknown"},
        BAT1={"type": "Battery"},
        BAT2={"type": "Battery", "capacity": 41},
    )
    assert battery(root) == (41, "Unknown")


def test_lid_switch_setting_last_one_wins(tmp_path):
    conf = tmp_path / "logind.conf"
    conf.write_text("[Login]\n#HandleLidSwitch=suspend\nHandleLidSwitch=ignore\n")
    assert lid_switch_setting(conf, ())[0] == "ignore"
    dropins = tmp_path / "logind.conf.d"
    dropins.mkdir()
    (dropins / "10-first.conf").write_text("[Login]\nHandleLidSwitch=hibernate\n")
    (dropins / "20-later.conf").write_text("[Login]\nHandleLidSwitch = lock\n")
    assert lid_switch_setting(conf, (dropins,))[0] == "lock"


def test_lid_switch_setting_unset_or_missing(tmp_path):
    conf = tmp_path / "logind.conf"
    conf.write_text("[Login]\n#HandleLidSwitch=suspend\n")
    assert lid_switch_setting(conf, (tmp_path / "nowhere",))[0] is None
    assert lid_switch_setting(tmp_path / "missing.conf", ())[0] is None


def test_by_default_a_run_is_a_new_outing_even_with_a_recent_log(tmp_path):
    recent = tmp_path / "2026-09-14T17-00-00.jsonl"
    recent.write_text("{}\n")
    now = datetime.now().astimezone()
    os.utime(recent, times=((now - timedelta(minutes=5)).timestamp(),) * 2)
    path, continuing = session_log_path(tmp_path, now)
    assert path != recent and not continuing


def nm_files(root, main=None, etc=None, lib=None):
    """A fake NetworkManager tree: /usr/lib first, the main file, then /etc."""
    paths = {}
    for name, value in (("lib", lib), ("etc", etc)):
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        if value:
            (directory / "50-scan.conf").write_text(
                f"[device]\nwifi.scan-rand-mac-address={value}\n"
            )
        paths[name] = (directory,)
    conf = root / "NetworkManager.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    # One section throughout, so these tests are about which file wins and not
    # about which section did.
    conf.write_text("[device]\n" + (f"wifi.scan-rand-mac-address={main}\n" if main else ""))
    return {
        "nm_conf": conf,
        "nm_before": paths["lib"],
        "nm_after": paths["etc"],
        "nm_intern": root / "intern.conf",
        "iwd_conf": root / "no-hay.conf",
    }


def effective(where):
    """The one value NetworkManager ends up with, or what it disagreed about."""
    return sorted(set(scan_mac_setting(**where).devices.values()))


def test_the_scan_mac_setting_is_read_from_the_daemons(tmp_path):
    found = scan_mac_setting(**nm_files(tmp_path / "a"))
    assert (found.devices, found.iwd, found.unsure) == ({}, None, ())
    assert effective(nm_files(tmp_path / "b", main="no")) == ["no"]


def test_networkmanager_reads_usr_lib_first_and_etc_last(tmp_path):
    # NetworkManager's own order, which is not systemd's: /usr/lib, /run, the
    # main file, /etc, then the file it writes itself, later ones winning.
    assert effective(nm_files(tmp_path / "order", main="yes", etc="no", lib="yes")) == ["no"]
    assert effective(nm_files(tmp_path / "vendor", main="yes", lib="no")) == ["yes"]


def test_a_drop_in_name_that_exists_in_etc_hides_the_one_in_usr_lib_entirely(tmp_path):
    # Not key by key: the whole vendor file is skipped. Reading both and letting
    # the later win looks identical until they set different keys, and then a
    # setting nothing is using gets reported as the machine's.
    where = nm_files(tmp_path / "shadow", lib="no")
    (tmp_path / "shadow" / "etc" / "50-scan.conf").write_text("[main]\ndns=none\n")
    assert effective(where) == []


def test_the_file_networkmanager_writes_itself_wins_last(tmp_path):
    where = nm_files(tmp_path / "intern", main="yes", etc="yes")
    where["nm_intern"].write_text("[device]\nwifi.scan-rand-mac-address=no\n")
    assert effective(where) == ["no"]


def test_two_device_sections_can_disagree_and_both_are_kept(tmp_path):
    # match-device can point them at different cards, and which one your card
    # gets is a question this does not answer. Collapsing them to the last one
    # in the file would invent an answer.
    where = nm_files(tmp_path / "devices")
    where["nm_conf"].write_text(
        "[device-wlan0]\nmatch-device=interface-name:wlan0\nwifi.scan-rand-mac-address=yes\n"
        "[device-wlan1]\nmatch-device=interface-name:wlan1\nwifi.scan-rand-mac-address=no\n"
    )
    assert scan_mac_setting(**where).devices == {"device-wlan0": "yes", "device-wlan1": "no"}


def test_iwd_is_read_from_its_own_file(tmp_path):
    where = nm_files(tmp_path / "iwd")
    where["iwd_conf"].write_text("[General]\nAddressRandomization=network\n")
    found = scan_mac_setting(**where)
    assert (found.devices, found.iwd, found.unsure) == ({}, "network", ())


def test_two_outings_started_in_the_same_second_get_different_files(tmp_path):
    now = datetime(2026, 9, 14, 17, 45, 3, tzinfo=TZ)
    first, _ = session_log_path(tmp_path, now)
    first.write_text("{}\n")
    second, _ = session_log_path(tmp_path, now)
    assert second != first and not second.exists()
    second.write_text("{}\n")
    third, _ = session_log_path(tmp_path, now)
    assert third not in (first, second)


def test_the_administrators_drop_in_beats_the_vendors(tmp_path):
    # systemd collects the drop-ins from every directory keyed by filename, keeps
    # the highest-priority copy of each, and reads what is left sorted by name.
    # Walking the directories one after another lets /usr/lib win, which is
    # backwards and would report that the lid is safe when it is not.
    etc, usr = tmp_path / "etc", tmp_path / "usr"
    etc.mkdir()
    usr.mkdir()
    (etc / "90-local.conf").write_text("[Login]\nHandleLidSwitch=ignore\n")
    (usr / "10-vendor.conf").write_text("[Login]\nHandleLidSwitch=suspend\n")
    main = tmp_path / "logind.conf"
    main.write_text("[Login]\n")
    assert lid_switch_setting(main, (etc, usr))[0] == "ignore"


def test_the_same_filename_in_two_directories_is_read_once_from_the_higher(tmp_path):
    etc, usr = tmp_path / "etc", tmp_path / "usr"
    etc.mkdir()
    usr.mkdir()
    (etc / "50-lid.conf").write_text("[Login]\nHandleLidSwitch=ignore\n")
    (usr / "50-lid.conf").write_text("[Login]\nHandleLidSwitch=suspend\n")
    main = tmp_path / "logind.conf"
    main.write_text("[Login]\n")
    assert lid_switch_setting(main, (etc, usr))[0] == "ignore"


def test_the_setting_only_counts_inside_the_login_section(tmp_path):
    conf = tmp_path / "logind.conf"
    conf.write_text("[Sleep]\nHandleLidSwitch=ignore\n")
    assert lid_switch_setting(conf, ())[0] is None
    conf.write_text("[Sleep]\nHandleLidSwitch=suspend\n[Login]\nHandleLidSwitch=ignore\n")
    assert lid_switch_setting(conf, ())[0] == "ignore"


def test_a_networkmanager_file_disabled_by_dot_config_is_not_read(tmp_path):
    # `[.config] enable=false` makes NetworkManager skip the file whole, so
    # reading it and letting its keys win reports a setting nothing is using.
    where = nm_files(tmp_path / "off", lib="no")
    (tmp_path / "off" / "etc" / "90-later.conf").write_text(
        "[.config]\nenable=false\n\n[device]\nwifi.scan-rand-mac-address=yes\n"
    )
    assert effective(where) == ["no"]

    # enable=true is the default said out loud, and changes nothing.
    (tmp_path / "off" / "etc" / "90-later.conf").write_text(
        "[.config]\nenable=true\n\n[device]\nwifi.scan-rand-mac-address=yes\n"
    )
    assert effective(where) == ["yes"]


def test_a_dot_config_predicate_is_reported_as_unread_and_not_guessed_at(tmp_path):
    # `enable` also takes predicates about the daemon's version and environment.
    # Which way they fall decides whether the file applies at all, and neither
    # is read here.
    where = nm_files(tmp_path / "maybe", lib="no")
    later = tmp_path / "maybe" / "etc" / "90-later.conf"
    later.write_text(
        "[.config]\nenable=nm-version-min:1.2\n\n[device]\nwifi.scan-rand-mac-address=yes\n"
    )
    found = scan_mac_setting(**where)
    assert sorted(set(found.devices.values())) == ["no"]
    assert [path for path, _ in found.unsure] == [later]


def test_the_main_file_cannot_be_disabled_by_dot_config(tmp_path):
    where = nm_files(tmp_path / "main")
    where["nm_conf"].write_text(
        "[.config]\nenable=false\n\n[device]\nwifi.scan-rand-mac-address=no\n"
    )
    assert effective(where) == ["no"]


def test_a_log_that_says_it_was_written_in_the_future_is_not_continued(tmp_path):
    # A clock that moved, or an mtime off another machine. A negative age is
    # under every gap there is, and continuing an outing on that basis puts a
    # new walk into somebody else's file.
    old = tmp_path / "2026-09-14T12-00-00.jsonl"
    old.write_text("{}\n")
    ahead = datetime(2026, 9, 14, 12, tzinfo=TZ).timestamp() + 7200
    os.utime(old, (ahead, ahead))
    path, continuing = session_log_path(tmp_path, datetime(2026, 9, 14, 12, tzinfo=TZ), resume=True)
    assert not continuing and path != old


def test_listing_the_logs_of_a_directory_creates_nothing(tmp_path):
    # `session_log_path` needs the same list and makes the directory on its way
    # to it, which is right when an outing is about to be written into it and
    # wrong when something only wants to look.
    missing = tmp_path / "todavia-no"
    assert session_logs(missing) == []
    assert not missing.exists()

    here = tmp_path / "paseos"
    here.mkdir()
    older, newer = here / "a.jsonl", here / "b.jsonl"
    older.write_text("{}\n")
    newer.write_text("{}\n")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))
    (here / "libreta.txt").write_text("no es un log\n")
    assert session_logs(here) == [newer, older]  # el más reciente primero

    # A name that matches and cannot be asked when it was written costs itself
    # and not the listing. A link to a log that was moved away is the ordinary
    # way to get one, and answering it by hiding every outing the operator has
    # is a directory with one broken name in it reported as an empty one.
    (here / "roto.jsonl").symlink_to(tmp_path / "se-fue.jsonl")
    assert session_logs(here) == [newer, older]
