"""Tests for the publishable copy: what it substitutes, and what it refuses to promise."""

import json
import os
import stat
import threading
from pathlib import Path

import pytest
from conftest import as_root

from enodia import anonymize, cli
from enodia.anonymize import (
    EPOCH,
    ExportError,
    Names,
    Shift,
    crossing_name,
    export_outing,
    format_export,
    key_path,
    read_key,
)
from enodia.netlog import read_log
from enodia.reconcile import reconcile
from enodia.streets import distance_metres

KEY = b"a" * 32
OTHER = b"b" * 32


def scan(minute, networks, token="3f9a2b10"):
    return json.dumps(
        {
            "time": f"2026-09-14T17:{minute:02d}:10-03:00",
            "event": "scan",
            "outing": token,
            "interface": "wlan0",
            "cycle": minute,
            "networks": networks,
        }
    )


def network(ssid, bssid, dbm=-50):
    return {
        "ssid": ssid,
        "bssid": bssid,
        "security": "psk",
        "frequency": 2412,
        "signal_dbm": dbm,
        "signal_percent": 55,
        "connected": False,
    }


def outing(tmp_path, rows=None, notebook=None):
    """A log and its notebook, with everything an export has to deal with in them."""
    log = tmp_path / "paseo.jsonl"
    log.write_text(
        "\n".join(
            rows
            if rows is not None
            else [
                json.dumps(
                    {
                        "time": "2026-09-14T17:45:03-03:00",
                        "event": "connected",
                        "outing": "3f9a2b10",
                        "interface": "wlan0",
                        "ssid": "Casa Planchon",
                        "bssid": "aa:bb:cc:dd:ee:ff",
                    }
                ),
                *(
                    scan(
                        45 + minute,
                        [
                            network("Casa Planchon", "aa:bb:cc:dd:ee:ff", -47 - minute),
                            network("ANTEL_4821", "11:22:33:44:55:66"),
                        ],
                    )
                    for minute in range(3)
                ),
            ]
        )
        + "\n"
    )
    paper = tmp_path / "libreta.txt"
    paper.write_text(
        notebook
        if notebook is not None
        else (
            "# la salida del sabado\n"
            "17:44:30 Agraciada y Freire @ -34.8600, -56.2100  # por la vereda norte\n"
            "17:51:00 Agraciada y Solari @ -34.8610, -56.2090\n"
        )
    )
    return log, paper


def exported(tmp_path, key=KEY, **kwargs):
    log, paper = kwargs.pop("files", None) or outing(tmp_path)
    out = kwargs.pop("out", tmp_path / "public")
    done = export_outing(log, paper, out, key, **kwargs)
    return done, done.log.read_text(encoding="utf-8"), done.notebook.read_text(encoding="utf-8")


# --- the pseudonyms ------------------------------------------------------------


def test_the_same_address_gets_the_same_pseudonym_everywhere_in_one_export(tmp_path):
    _, log, _ = exported(tmp_path)
    rows = [json.loads(line) for line in log.splitlines()]
    addresses = [one["bssid"] for row in rows for one in row["networks"]]
    assert len(set(addresses)) == 2
    assert all(one.startswith("ap-") for one in addresses)
    assert addresses.count(addresses[0]) == 3  # una vez por scan, siempre la misma


def test_two_kinds_of_identifier_that_happen_to_match_do_not_share_a_pseudonym():
    # The kind is part of what is hashed, so nobody can test a value against the
    # wrong space, and a name that happens to read like an address is not it.
    names = Names(key=KEY)
    assert names.of("bssid", "aa:bb") != names.of("ssid", "aa:bb")


def test_a_second_export_months_later_uses_the_same_pseudonyms(tmp_path):
    first = Names(key=KEY).of("bssid", "aa:bb:cc:dd:ee:ff")
    later = Names(key=KEY).of("bssid", "aa:bb:cc:dd:ee:ff")
    assert first == later


def test_an_export_with_another_key_shares_no_pseudonym_with_the_first(tmp_path):
    # The label is the digest, so the key decides it: the same walk exported
    # under two keys has no name in common with itself.
    mine = Names(key=KEY)
    yours = Names(key=OTHER)
    mine.of("bssid", "aa:bb:cc:dd:ee:ff")
    yours.of("bssid", "11:22:33:44:55:66")
    assert mine.of("bssid", "11:22:33:44:55:66") != yours.of("bssid", "11:22:33:44:55:66")


def test_a_mac_shaped_pseudonym_is_locally_administered_and_not_multicast(tmp_path):
    _, log, _ = exported(tmp_path, mac_shaped=True)
    (first,) = [json.loads(line) for line in log.splitlines()][:1]
    address = first["networks"][0]["bssid"]
    assert address != "aa:bb:cc:dd:ee:ff"
    leading = int(address.split(":")[0], 16)
    assert leading & 0b0000_0010  # administrada localmente: nadie la tiene asignada
    assert not leading & 0b0000_0001  # y no es multicast, porque es una tarjeta


# --- the key -------------------------------------------------------------------


def test_the_key_is_made_once_and_only_its_owner_can_read_it(tmp_path):
    where = tmp_path / "config" / "export.key"
    made, told = read_key(where)
    assert len(made) == 32
    assert "A new export key was made" in (told or "")
    assert stat.S_IMODE(where.stat().st_mode) == 0o600
    assert not list(where.parent.glob("*.new"))

    again, quiet = read_key(where)
    assert again == made and quiet is None


@as_root
def test_a_key_anybody_could_read_is_said_and_used_anyway(tmp_path):
    # Said and not corrected. The file is the operator's, and tightening the
    # permissions on something found lying there is not this program's call.
    where = tmp_path / "export.key"
    where.write_bytes(KEY)
    where.chmod(0o644)
    found, told = read_key(where)
    assert found == KEY
    assert "can be read by others" in (told or "")


def test_the_key_lives_apart_from_the_walks(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    assert key_path() == tmp_path / "conf" / "enodia" / "export.key"


# --- what goes out and what does not -------------------------------------------


def test_the_association_records_are_left_out_because_they_are_the_walkers_own(tmp_path):
    # Not a neighbour: the network the walker was on names their own home, and
    # nothing in a reconciliation reads these records anyway.
    _, log, _ = exported(tmp_path)
    assert "connected" not in {json.loads(line)["event"] for line in log.splitlines()}
    assert all(
        one["connected"] is False
        for line in log.splitlines()
        for one in json.loads(line)["networks"]
    )


def test_a_failure_reason_from_the_daemon_never_reaches_the_export(tmp_path):
    # `reason` is str(exc) from D-Bus, and those carry object paths, device
    # names and now and then an SSID. It is not worth reading to find out.
    rows = [
        scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
        json.dumps(
            {
                "time": "2026-09-14T17:46:00-03:00",
                "event": "scan_failed",
                "outing": "3f9a2b10",
                "reason": "AccessDenied: /org/freedesktop/NetworkManager/Devices/2",
            }
        ),
    ]
    _, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert "NetworkManager/Devices" not in log
    assert "withheld by --export-public" in log


def test_a_field_nobody_thought_about_does_not_leave(tmp_path):
    # Built from named keys and not copied. A key added to the log next year has
    # to be looked at before it can go out, rather than going out because nobody
    # looked.
    rows = [
        json.dumps(
            {
                "time": "2026-09-14T17:45:10-03:00",
                "event": "scan",
                "outing": "3f9a2b10",
                "networks": [{**network("Casa", "aa:bb:cc:dd:ee:ff"), "vendor": "Huawei"}],
                "operator_note": "salí desde casa",
            }
        )
    ]
    _, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert "operator_note" not in log and "Huawei" not in log


def test_a_record_whose_time_cannot_be_read_is_left_out_and_counted(tmp_path):
    # An unshifted timestamp is the one field that would publish the day and the
    # hour somebody was on a street, so a time that cannot be moved does not go.
    rows = [
        scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
        json.dumps({"time": "2026-09-14T17:46:00", "event": "scan", "networks": []}),
    ]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert done.withheld == 1
    assert "2026" not in log
    assert "1 record left out" in format_export(done)


def test_a_network_with_no_bssid_keeps_a_pseudonym_for_its_name(tmp_path):
    # Its name is its only identity, and `identified` going false makes every
    # reader in Enodia drop it without a word.
    rows = [scan(45, [network("Cafe libre", None)]) for _ in range(2)]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    named = {one["ssid"] for line in log.splitlines() for one in json.loads(line)["networks"]}
    assert len(named) == 1 and next(iter(named)).startswith("ssid-")
    assert done.kept_names == 1
    assert "1 network had no address of its own" in format_export(done)


def test_names_are_kept_or_removed_when_asked(tmp_path):
    _, removed, _ = exported(tmp_path)
    assert "Casa Planchon" not in removed and '"ssid": ""' in removed

    _, pseudonymous, _ = exported(tmp_path, out=tmp_path / "b", ssid="pseudonym")
    assert "Casa Planchon" not in pseudonymous and '"ssid": "ssid-' in pseudonymous

    _, kept, _ = exported(tmp_path, out=tmp_path / "c", ssid="keep")
    assert "Casa Planchon" in kept


# --- the clock and the map -----------------------------------------------------


def test_the_times_keep_their_intervals_and_lose_their_day(tmp_path):
    _, log, _ = exported(tmp_path)
    stamps = [json.loads(line)["time"] for line in log.splitlines()]
    assert stamps[0].startswith("1970-01-01T")
    assert "2026" not in log
    # 17:45:10, 17:46:10, 17:47:10 in the original: a minute apart, still.
    assert stamps == [
        "1970-01-01T00:00:40+00:00",
        "1970-01-01T00:01:40+00:00",
        "1970-01-01T00:02:40+00:00",
    ]


def test_a_notebook_that_starts_before_the_log_does_not_go_negative(tmp_path):
    # Marking the corner and then walking is the ordinary way round, so the
    # notebook usually starts first and shifting by the log alone would put it
    # before zero.
    _, _, paper = exported(tmp_path)
    times = [line.split()[0] for line in paper.splitlines()[1:]]
    assert times == ["00:00:00", "00:06:30"]  # el primer cruce es el origen


def test_the_coordinates_keep_their_distances_from_each_other(tmp_path):
    _, _, paper = exported(tmp_path)
    places = []
    for line in paper.splitlines()[1:]:
        lat, lon = line.split(" @ ")[1].split(", ")
        places.append((float(lat), float(lon)))
    before = distance_metres(-34.8600, -56.2100, -34.8610, -56.2090)
    after = distance_metres(*places[0], *places[1])
    assert abs(before - after) < 0.5  # medio metro sobre ciento cuarenta y cuatro
    assert "-34.86" not in paper and "-56.21" not in paper


# --- the notebook ---------------------------------------------------------------


def test_the_crossings_keep_their_streets_apart_and_lose_their_names(tmp_path):
    # Street by street, so the same street at two corners is still the same
    # street, which is what the canonical frame lives on.
    _, _, paper = exported(tmp_path)
    assert "Agraciada" not in paper and "Freire" not in paper
    named = [line.split(" @ ")[0].split(" ", 1)[1] for line in paper.splitlines()[1:]]
    first, second = (one.split(" y ") for one in named)
    assert first[0] == second[0]  # Agraciada, la misma calle en las dos esquinas
    assert first[1] != second[1]
    assert all(one.startswith("street-") for one in (*first, *second))


def test_a_crossing_that_is_not_two_streets_becomes_one_place():
    names = Names(key=KEY)
    assert crossing_name(names, "Plaza Independencia").startswith("place-")
    both = crossing_name(names, "Agraciada y Freire").split(" y ")
    assert len(both) == 2 and all(one.startswith("street-") for one in both)
    # A corner written with a comma keeps it, and its halves are the same
    # streets they are at any other corner.
    renamed = crossing_name(names, "Treinta y Tres, Agraciada")
    assert " y " not in renamed and renamed.split(", ")[1] == both[0]
    assert renamed.split(", ")[0] == crossing_name(names, "Rivera, Treinta y Tres").split(", ")[1]


def test_a_comment_in_the_notebook_does_not_survive_the_export(tmp_path):
    # The opposite of what --geocode does to the same file, and deliberately:
    # there the operator's comments are worth keeping, here they are arbitrary
    # prose about somebody's afternoon.
    _, _, paper = exported(tmp_path)
    assert "#" not in paper and "vereda" not in paper


# --- the point of the whole thing ------------------------------------------------


def test_the_exported_outing_still_reconciles(tmp_path):
    # It is an example of a walk. If Enodia cannot read it back, it is an
    # example of nothing.
    log, paper = outing(tmp_path)
    done = export_outing(log, paper, tmp_path / "public", KEY)
    before = reconcile(log, paper)
    after = reconcile(done.log, done.notebook)
    assert len(after.placed) == len(before.placed)
    assert len(after.networks) == len(before.networks)
    # The day is gone and the shape of the walk is not: the gap between the two
    # crossings, and the gap from the first crossing to the first scan.
    assert after.waypoints[1].time - after.waypoints[0].time == (
        before.waypoints[1].time - before.waypoints[0].time
    )
    assert after.placed[0].scan.time - after.waypoints[0].time == (
        before.placed[0].scan.time - before.waypoints[0].time
    )


def test_the_export_says_it_is_pseudonymous_and_not_anonymous(tmp_path):
    done, _, _ = exported(tmp_path)
    said = format_export(done)
    assert "pseudonymised and not anonymous" in said
    assert "A radio fingerprint locates itself" in said
    assert "The shape of the walk survives" in said
    assert "Read it before publishing it" in said


def test_a_place_that_already_holds_anything_is_not_exported_into(tmp_path):
    # The realistic second run is the one after somebody has read the first
    # export line by line, and replacing that quietly is a loss nobody gets back.
    # A directory, not two filenames: whether the place is taken is a question
    # the kernel answers, and not one this works out by counting files.
    log, paper = outing(tmp_path)
    export_outing(log, paper, tmp_path / "public", KEY)
    with pytest.raises(ExportError, match="is already there"):
        export_outing(log, paper, tmp_path / "public", KEY)

    # Anything at all, including a file somebody kept on purpose after deleting
    # the other. Inferring a crash from that overwrote what had been approved.
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "outing-1970-01-01.jsonl").write_text("read and edited by hand\n")
    with pytest.raises(ExportError, match="is already there"):
        export_outing(log, paper, mine, KEY)
    assert (mine / "outing-1970-01-01.jsonl").read_text() == "read and edited by hand\n"


def test_a_log_with_no_usable_scans_is_refused(tmp_path):
    log, paper = outing(tmp_path, rows=[json.dumps({"event": "scan", "networks": []})])
    with pytest.raises(ExportError, match="no timestamped scans found"):
        export_outing(log, paper, tmp_path / "public", KEY)


def test_a_shift_with_nowhere_to_move_leaves_a_place_where_it_is():
    # Only reachable before the origin is settled, which every export does.
    assert Shift(began=EPOCH).where(-34.9, -56.2) == (-34.9, -56.2)


def test_a_notebook_with_no_coordinates_is_exported_all_the_same(tmp_path):
    log, paper = outing(
        tmp_path, notebook="17:44:30 Agraciada y Freire\n17:51:00 Agraciada y Solari\n"
    )
    done = export_outing(log, paper, tmp_path / "public", KEY)
    assert "@" not in done.notebook.read_text(encoding="utf-8")
    assert done.crossings == 2


# --- from the command line --------------------------------------------------------


def test_export_public_writes_both_files_and_says_what_it_did(capsys, tmp_path):
    log, paper = outing(tmp_path)
    code = cli.main(["--export-public", str(log), str(paper), "--out", str(tmp_path / "public")])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 access points" in out and "2 crossings" in out
    assert (tmp_path / "public" / "notebook.txt").exists()


def test_export_public_reports_a_notebook_it_cannot_read(capsys, tmp_path):
    log, paper = outing(tmp_path, notebook="17:0 A\nB\n")
    assert cli.main(["--export-public", str(log), str(paper), "--out", str(tmp_path / "p")]) == 1
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags",
    [
        ["--ssid", "keep"],
        ["--mac-shaped"],
        ["--key-file", "clave"],
    ],
)
def test_export_flags_without_export_public_are_an_error(flags, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main([*flags, "--voice", "none", "--button", "off"])
    assert stopped.value.code == 2
    assert "only meaningful together with --export-public" in capsys.readouterr().err


def test_out_belongs_to_both_commands_that_write_a_file_of_their_own(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--voice", "none", "--button", "off", "--out", "algo"])
    assert stopped.value.code == 2
    assert "--out: only meaningful together with --geocode or --export-public" in (
        capsys.readouterr().err
    )


def test_the_key_file_can_be_named_on_the_command_line(capsys, tmp_path):
    log, paper = outing(tmp_path)
    mine = tmp_path / "mia.key"
    mine.write_bytes(KEY)
    code = cli.main(
        [
            "--export-public",
            str(log),
            str(paper),
            "--out",
            str(tmp_path / "public"),
            "--key-file",
            str(mine),
        ]
    )
    assert code == 0
    assert not key_path().exists()  # y no se creó ninguna otra
    rows = (tmp_path / "public" / f"outing-{EPOCH.date().isoformat()}.jsonl").read_text()
    assert '"bssid": "ap-' in rows


def test_the_assistant_refuses_the_export_because_it_is_a_command(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--assistant", "--export-public", "a.jsonl", "b.txt"])
    assert stopped.value.code == 2
    assert "--export-public is a command of its own" in capsys.readouterr().err


def test_the_default_destination_is_a_directory_beside_you(capsys, tmp_path, monkeypatch):
    log, paper = outing(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--export-public", str(log), str(paper)]) == 0
    assert (tmp_path / "public" / "notebook.txt").exists()


def test_a_log_line_that_is_not_a_record_costs_only_itself(tmp_path):
    # The same tolerance `read_log` has. A line that was never finished, a blank
    # one, a record with no time at all: none of those is a reason to refuse to
    # export the walk they sit in.
    rows = [
        scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
        "",
        "no es json",
        "[1, 2]",
        json.dumps({"event": "scan", "networks": []}),
        scan(46, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
    ]
    done, _, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert done.scans == 2
    # El registro sin hora y el `[1, 2]`: los dos estaban en el archivo y no salieron.
    assert done.withheld == 2


def test_a_walk_that_carries_no_time_at_all_is_refused(tmp_path):
    # Only reachable with a notebook whose times come from marks the log does
    # not have either, which is a pair of files that describe no walk.
    from enodia.anonymize import earliest

    with pytest.raises(ExportError, match="nothing in this walk carries a time"):
        earliest([], [])


# --- what the key actually decides ------------------------------------------------


def test_the_pseudonym_comes_from_the_key_and_not_from_the_order_of_appearance():
    # A counter reads better and is a lie: it numbers things as they turn up, so
    # the same router exported twice gets two different names and the key
    # decides nothing. Two exports agreeing is the reason the key is kept.
    first = Names(key=KEY)
    first.of("bssid", "aa:aa:aa:aa:aa:aa")
    first.of("bssid", "bb:bb:bb:bb:bb:bb")
    second = Names(key=KEY)
    second.of("bssid", "bb:bb:bb:bb:bb:bb")
    assert second.of("bssid", "aa:aa:aa:aa:aa:aa") == first.of("bssid", "aa:aa:aa:aa:aa:aa")


def test_two_exports_of_one_walk_under_two_keys_are_not_the_same_file(tmp_path):
    # The strongest form of the same question. If the file comes out identical
    # whatever the key, the key is decoration.
    log, paper = outing(tmp_path)
    mine = export_outing(log, paper, tmp_path / "mine", KEY)
    yours = export_outing(log, paper, tmp_path / "yours", OTHER)
    assert mine.log.read_bytes() != yours.log.read_bytes()
    assert mine.notebook.read_bytes() != yours.notebook.read_bytes()


def test_one_address_written_two_ways_is_one_access_point(tmp_path):
    # Enodia settles this on the way in with `address`, and the export reads raw
    # JSON, so a hand-edited file could carry both spellings of one router.
    rows = [
        scan(45, [network("Casa", "AA:BB:CC:DD:EE:FF")]),
        scan(46, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
    ]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    named = {one["bssid"] for line in log.splitlines() for one in json.loads(line)["networks"]}
    assert len(named) == 1 and done.networks == 1


def test_an_export_key_that_is_not_a_key_is_refused(tmp_path):
    # An HMAC keyed with nothing is one anybody can compute, so every pseudonym
    # in the export would be worked out by whoever received it.
    empty = tmp_path / "vacia.key"
    empty.write_bytes(b"")
    with pytest.raises(ExportError, match="holds 0 bytes and an export key is 32"):
        read_key(empty)

    short = tmp_path / "corta.key"
    short.write_bytes(b"secreto")
    with pytest.raises(ExportError, match="holds 7 bytes"):
        read_key(short)


# --- exactly one walk ---------------------------------------------------------------


def two_walks(tmp_path):
    """`--log walk.jsonl` reused: two outings on the same pages."""
    rows = [
        json.dumps(
            {
                "time": f"2026-09-{day}T17:{45 + minute}:10-03:00",
                "event": "scan",
                "outing": token,
                "cycle": minute + 1,
                "networks": [network(ssid, address)],
            }
        )
        for day, token, ssid, address in (
            (14, "old11111", "Vieja", "11:11:11:11:11:11"),
            (20, "new22222", "Nueva", "22:22:22:22:22:22"),
        )
        for minute in range(2)
    ]
    return outing(tmp_path, rows=rows)


def test_a_file_of_several_walks_is_refused_rather_than_guessed_at(tmp_path):
    # Everywhere else the last walk in the file is a sensible default. Here it
    # would mean publishing the other walks because somebody did not know the
    # file held any, which is the one mistake this command exists to prevent.
    log, paper = two_walks(tmp_path)
    with pytest.raises(ExportError, match="holds 2 walks and an export is one walk"):
        export_outing(log, paper, tmp_path / "public", KEY)


def test_the_walk_that_was_asked_for_is_the_only_one_that_leaves(tmp_path):
    log, paper = two_walks(tmp_path)
    done = export_outing(log, paper, tmp_path / "public", KEY, outing="new22222")
    rows = [json.loads(line) for line in done.log.read_text(encoding="utf-8").splitlines()]
    assert len({row["outing"] for row in rows}) == 1
    assert len(rows) == 2  # los dos scans de esa caminata y nada más
    addresses = {one["bssid"] for row in rows for one in row["networks"]}
    assert addresses == {Names(key=KEY).of("bssid", "22:22:22:22:22:22")}


def test_a_walk_the_file_does_not_hold_is_said_and_not_guessed_at(tmp_path):
    log, paper = two_walks(tmp_path)
    with pytest.raises(ExportError, match="no walk called 'cccc3333'"):
        export_outing(log, paper, tmp_path / "public", KEY, outing="cccc3333")


def test_choosing_the_walk_from_the_command_line(capsys, tmp_path):
    log, paper = two_walks(tmp_path)
    assert cli.main(["--export-public", str(log), str(paper), "--out", str(tmp_path / "a")]) == 1
    assert "holds 2 walks" in capsys.readouterr().err

    code = cli.main(
        [
            "--export-public",
            str(log),
            str(paper),
            "--out",
            str(tmp_path / "b"),
            "--outing",
            "old11111",
        ]
    )
    assert code == 0 and "2 scans" in capsys.readouterr().out


# --- the fields nobody asked for -----------------------------------------------------


def test_the_interface_is_substituted_rather_than_published(tmp_path):
    # Usually wlan0 and usually nothing, but Linux takes any name and somebody's
    # says something. Kept rather than dropped, because a log written before
    # `cycle` existed needs it to tell two radios apart.
    _, log, _ = exported(tmp_path)
    named = {json.loads(line).get("interface") for line in log.splitlines()}
    assert named == {Names(key=KEY).of("radio", "wlan0")}
    assert "wlan0" not in log


def test_a_reason_enodia_wrote_itself_is_kept_and_any_other_is_not(tmp_path):
    # Allowed by exact match rather than sanitised by event, so a record kind
    # added later cannot bring arbitrary text through a door that was only ever
    # checked for one event.
    rows = [
        scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
        json.dumps(
            {
                "time": "2026-09-14T17:46:00-03:00",
                "event": "scan_failed",
                "reason": "radio soft blocked (rfkill)",
            }
        ),
        json.dumps(
            {
                "time": "2026-09-14T17:47:00-03:00",
                "event": "button_lost",
                "reason": "SYN_DROPPED",
            }
        ),
        json.dumps(
            {
                "time": "2026-09-14T17:48:00-03:00",
                "event": "scan_failed",
                "reason": "/org/freedesktop/NetworkManager/Devices/2",
            }
        ),
    ]
    _, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert "radio soft blocked (rfkill)" in log and "SYN_DROPPED" in log
    assert "NetworkManager" not in log and "withheld by --export-public" in log


# --- both files, or neither ---------------------------------------------------------


def test_an_export_that_dies_halfway_leaves_nothing_to_clean_up(tmp_path, monkeypatch):
    # Half an export is worse than none: the log landed, the notebook did not,
    # and the refusal to overwrite then stopped the same command putting it
    # right. The message on ExportError already promised this.
    from enodia import anonymize

    log, paper = outing(tmp_path)
    calls = {"n": 0}
    real = anonymize.os.fsync

    def flaky(handle):
        calls["n"] += 1
        if calls["n"] == 2:  # el segundo archivo, la libreta
            raise OSError("disk full")
        return real(handle)

    monkeypatch.setattr(anonymize.os, "fsync", flaky)
    with pytest.raises(OSError, match="disk full"):
        export_outing(log, paper, tmp_path / "public", KEY)
    monkeypatch.undo()
    # Not the destination, and not the directory it was being built in either.
    assert not (tmp_path / "public").exists()
    assert sorted(one.name for one in tmp_path.iterdir()) == ["libreta.txt", "paseo.jsonl"]

    # And the same command puts it right, which is the point.
    done = export_outing(log, paper, tmp_path / "public", KEY)
    assert done.log.exists() and done.notebook.exists()


# --- a walk longer than a day --------------------------------------------------------


def test_a_walk_that_runs_over_midnight_keeps_the_days_between_its_crossings(tmp_path):
    # One `date` directive at the top would fold every crossing onto the same
    # day, and the intervals are the whole of what survives an export, so
    # losing one is losing the data rather than hiding it.
    rows = [
        json.dumps(
            {
                "time": stamp,
                "event": "scan",
                "outing": "3f9a2b10",
                "cycle": 1,
                "networks": [network("Casa", "aa:bb:cc:dd:ee:ff")],
            }
        )
        for stamp in ("2026-09-14T10:00:10-03:00", "2026-09-16T11:00:10-03:00")
    ]
    log, paper = outing(
        tmp_path,
        rows=rows,
        notebook=(
            "2026-09-14 10:00:00 Agraciada y Freire\n2026-09-16 11:00:00 Agraciada y Solari\n"
        ),
    )
    done = export_outing(log, paper, tmp_path / "public", KEY)
    assert done.notebook.read_text(encoding="utf-8").count("date ") == 2

    before = reconcile(log, paper)
    after = reconcile(done.log, done.notebook)
    gap = before.waypoints[1].time - before.waypoints[0].time
    assert after.waypoints[1].time - after.waypoints[0].time == gap
    assert gap.days == 2


# --- one boundary, not two -----------------------------------------------------------


def test_a_network_is_read_through_the_same_door_every_other_reader_uses(tmp_path):
    # Two parsers means two sets of rules about what a frequency or a signal may
    # be, and the one that drifts is the one nobody is reading.
    rows = [
        scan(
            45,
            [
                {
                    "ssid": ["not a name"],
                    "bssid": ["not an address"],
                    "security": "PSK",
                    "frequency": 10**9,
                    "signal_dbm": 100000,
                    "signal_percent": 300,
                    "connected": True,
                    "vendor": "Huawei",
                }
            ],
        )
    ]
    _, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows), ssid="keep")
    (one,) = json.loads(log.splitlines()[0])["networks"]
    assert one["bssid"] is None and one["ssid"] == ""  # ninguno era texto
    assert one["frequency"] is None and one["signal_dbm"] is None
    assert one["signal_percent"] is None  # fuera de rango, como en cualquier lectura
    assert one["security"] == "psk" and one["connected"] is False
    assert "vendor" not in one


def test_an_export_never_writes_a_line_enodia_would_refuse_to_read(tmp_path):
    # Python reads NaN without complaint and Enodia's own reader throws the line
    # away, so an export that let one through would be writing a file that is
    # not quite the walk it says it is.
    rows = [
        scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
        scan(46, [network("Casa", "aa:bb:cc:dd:ee:ff")]).replace(
            '"signal_dbm": -50', '"signal_dbm": NaN'
        ),
    ]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert "NaN" not in log
    assert done.scans == 1 and done.withheld == 1
    assert len(read_log(done.log)) == done.scans  # lo que dice y lo que se lee coinciden
    assert "was not something this could publish" in format_export(done)


def test_a_line_that_is_not_json_at_all_is_passed_over_and_not_counted(tmp_path):
    # Junk is junk, and `read_log` passes over it without a word. What is worth
    # counting is a record that was in the walk and did not make it out.
    rows = ["no es json", scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")])]
    done, _, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert done.scans == 1 and done.withheld == 0


# --- what went in and is still there ---------------------------------------------------


def leaking(monkeypatch, field="interface"):
    """An export that forgets to substitute one field, which is the bug this looks for."""
    real = anonymize.public_record

    def forgetful(names, shift, record, ssid):
        out = real(names, shift, record, ssid)
        if out is not None and field in out:
            out[field] = getattr(record, field)
        return out

    monkeypatch.setattr(anonymize, "public_record", forgetful)


def test_a_name_that_survived_the_export_is_named_in_the_report(tmp_path, monkeypatch):
    # The failure this exists for: a field nobody passed through `Names`, which
    # is what `interface` and `reason` both were until somebody went looking.
    leaking(monkeypatch)
    rows = [scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")])]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert done.survived == (("wlan0", 1),)
    assert "wlan0" in log
    assert "still in what came out of it" in format_export(done)


def test_an_export_that_leaks_nothing_says_nothing_about_leaks(tmp_path):
    done, _, _ = exported(tmp_path)
    assert done.survived == ()
    assert "still in what came out of it" not in format_export(done)


def test_the_check_reads_the_walks_that_were_left_out_of_the_export(tmp_path):
    # Publishing a neighbour from a walk the operator had forgotten the file
    # held is the worse of the two mistakes, so the search set is the whole
    # file even though the export is one walk of it.
    rows = [
        scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")], token="3f9a2b10"),
        scan(46, [network("Vecino", "11:22:33:44:55:66")], token="c4d5e6f7"),
    ]
    log, paper = outing(tmp_path, rows=rows)
    assert "Vecino" in anonymize.what_went_in(log, paper, "remove")


def test_a_name_kept_on_purpose_is_not_reported_as_having_survived(tmp_path):
    # With --ssid keep the names are in the output because that was asked for,
    # and reporting them would be reporting the flag back at whoever set it.
    done, log, _ = exported(tmp_path, ssid="keep")
    assert "ANTEL_4821" in log
    assert done.survived == ()


def test_a_comment_dropped_from_the_notebook_is_still_checked_for(tmp_path):
    # The export drops comments whole, so nothing downstream would ever notice
    # one coming out. That is the reason to look for them rather than not to.
    paper = "17:44:30 Agraciada y Freire  # me crucé con Ana\n17:51:00 Agraciada y Solari\n"
    log, notebook = outing(tmp_path, notebook=paper)
    went = anonymize.what_went_in(log, notebook, "remove")
    assert "me crucé con Ana" in went
    assert {"Agraciada", "Freire", "Agraciada y Freire"} <= went


def test_a_short_name_that_matches_a_pseudonym_is_reported_and_explained(tmp_path):
    # A substring search cannot tell a leak from a coincidence, and the report
    # says so rather than pretending the finding is a verdict.
    rows = [scan(45, [network("ap", "aa:bb:cc:dd:ee:ff")])]
    done, _, _ = exported(tmp_path, files=outing(tmp_path, rows=rows), ssid="pseudonym")
    assert [value for value, _ in done.survived] == ["ap"]
    assert "not believed" in format_export(done)


def test_a_notebook_line_that_is_not_a_crossing_at_all_is_passed_over(tmp_path):
    # `date 2026-09-14` names a day and not a street, and a line the notebook's
    # own readers do not take is not a name this should go hunting for.
    paper = "date 2026-09-14\n\n17:44:30 Agraciada y Freire\n"
    log, notebook = outing(tmp_path, notebook=paper)
    went = anonymize.what_went_in(log, notebook, "remove")
    assert "Agraciada y Freire" in went
    assert not any(one.startswith("date") for one in went)


def test_a_log_line_that_is_not_a_record_is_passed_over_by_the_check(tmp_path):
    # The same junk `read_log` passes over, read here by a second reader that
    # has to survive it too.
    rows = ["no es json", "[1, 2, 3]", scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")])]
    log, paper = outing(tmp_path, rows=rows)
    assert anonymize.what_went_in(log, paper, "remove") >= {"Casa", "aa:bb:cc:dd:ee:ff"}


# --- the export serialises the record, not the file -------------------------------------


def walk_with_every_kind(tmp_path):
    """A walk holding each record an export has to carry through whole."""
    return outing(
        tmp_path,
        rows=[
            scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
            json.dumps(
                {
                    "time": "2026-09-14T17:46:00-03:00",
                    "event": "mark",
                    "outing": "3f9a2b10",
                    "number": 1,
                    "key": 164,
                }
            ),
            json.dumps(
                {
                    "time": "2026-09-14T17:47:00-03:00",
                    "event": "suspended",
                    "outing": "3f9a2b10",
                    "seconds": 42.5,
                }
            ),
            json.dumps(
                {
                    "time": "2026-09-14T17:48:00-03:00",
                    "event": "scan_failed",
                    "outing": "3f9a2b10",
                    "interface": "wlan0",
                    "reason": "radio soft blocked (rfkill)",
                }
            ),
            scan(49, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
        ],
    )


def test_every_field_of_an_exported_record_survives_being_read_back(tmp_path):
    # The export is a second serialisation of the same records, so everything it
    # does not set out to change has to come back identical. A mark that lost
    # its key, or a suspend that lost its seconds, would be a walk published
    # shorter than it was walked.
    log, paper = walk_with_every_kind(tmp_path)
    before = read_log(log)
    done, _, _ = exported(tmp_path, files=(log, paper))
    after = read_log(done.log)
    kept = [(r.event, r.number, r.key, r.seconds, r.cycle, r.reason) for r in before]
    assert [(r.event, r.number, r.key, r.seconds, r.cycle, r.reason) for r in after] == kept


def test_a_networks_field_that_is_not_a_list_cannot_reach_the_export(tmp_path):
    # It used to reach `_record` as whatever the file held and take the export
    # down with it: a dict there iterates as its own keys, and every reader
    # below expected a network.
    for broken in ('{"ssid": "Casa Planchon"}', "5", '"Casa Planchon"'):
        rows = [
            scan(45, [network("Vecino", "aa:bb:cc:dd:ee:ff")]),
            scan(46, []).replace('"networks": []', f'"networks": {broken}'),
        ]
        out = tmp_path / broken[:4].strip('"{')
        _, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows), out=out)
        assert "Casa Planchon" not in log
        assert json.loads(log.splitlines()[1])["networks"] == []


def test_a_cycle_that_is_not_a_number_goes_out_as_the_reader_will_read_it(tmp_path):
    # The export wrote "abc" and Enodia read back nothing, so the file said one
    # thing and its own reader said another.
    rows = [scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")]).replace('"cycle": 45', '"cycle": "x"')]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert "cycle" not in json.loads(log.splitlines()[0])
    assert read_log(done.log)[0].cycle is None


def test_a_scan_that_heard_nothing_is_still_a_scan_that_heard_nothing(tmp_path):
    # An empty list is a street with no Wi-Fi on it, and a missing field is a
    # scan that never happened.
    rows = [scan(45, []), scan(46, [network("Casa", "aa:bb:cc:dd:ee:ff")])]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert json.loads(log.splitlines()[0])["networks"] == []
    assert done.scans == 2 and done.networks == 1


# --- the domains of the strings that do cross -------------------------------------------


def test_an_event_outside_enodias_own_vocabulary_is_not_a_record_of_a_walk(tmp_path):
    # Naming fields off a type stops a field nobody named from leaving. It does
    # nothing about a field whose type is `str` and whose value came out of a
    # file somebody could have edited, and `event` is the plainest case of that.
    rows = [
        scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")]),
        json.dumps(
            {
                "time": "2026-09-14T17:46:00-03:00",
                "event": "Carlos-secret-event",
                "outing": "3f9a2b10",
            }
        ),
    ]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert "Carlos-secret-event" not in log
    assert len(log.splitlines()) == 1 and done.withheld == 1


def test_a_security_label_this_does_not_know_is_withheld_and_counted(tmp_path):
    # Withheld rather than refused: an unknown label is far likelier a daemon
    # saying something new than an attack, and the count is so that a walk never
    # loses one of its readings without saying so.
    rows = [
        scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff") | {"security": "Carlos-private-security"}]),
        scan(46, [network("Otra", "11:22:33:44:55:66")]),
    ]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert "carlos-private-security" not in log
    labels = [one["security"] for row in log.splitlines() for one in json.loads(row)["networks"]]
    assert labels == ["withheld by --export-public", "psk"]
    assert done.relabelled == 1
    assert "1 security label this does not know" in format_export(done)


def test_a_backend_that_reported_no_security_at_all_still_reports_none(tmp_path):
    # None says the backend answered nothing, which is a reading of its own and
    # not the same as a label this would not repeat.
    rows = [scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff") | {"security": None}])]
    done, log, _ = exported(tmp_path, files=outing(tmp_path, rows=rows))
    assert json.loads(log)["networks"][0]["security"] is None
    assert done.relabelled == 0


def test_a_free_string_outside_its_domain_is_looked_for_in_what_came_out(tmp_path):
    # The leak check follows the same rule: a known label is in the export on
    # purpose, and only the values outside the domain are worth hunting for.
    log, paper = outing(tmp_path, rows=[scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff")])])
    went = anonymize.what_went_in(log, paper, "remove")
    assert "psk" not in went and "scan" not in went and "connected" not in went

    rows = [scan(45, [network("Casa", "aa:bb:cc:dd:ee:ff") | {"security": "odd-label"}])]
    log, paper = outing(tmp_path, rows=rows)
    assert "odd-label" in anonymize.what_went_in(log, paper, "remove")


# --- both files, or neither, even at the last step ---------------------------------------


def test_a_move_that_fails_leaves_what_was_there_exactly_as_it_was(tmp_path):
    # One rename and not two, so there is nothing half done to undo, and nothing
    # this could take away by mistake. The earlier design undid the first of two
    # renames, and when it ran over a file somebody had kept it undid that too.
    log, paper = outing(tmp_path)
    out = tmp_path / "public"
    out.mkdir()
    (out / "kept").write_text("mine\n")
    with pytest.raises(ExportError, match="is already there"):
        export_outing(log, paper, out, KEY)
    assert sorted(one.name for one in out.iterdir()) == ["kept"]
    assert (out / "kept").read_text() == "mine\n"


def test_two_exports_racing_for_one_place_leave_one_export_and_one_refusal(tmp_path):
    # Both look, both find nothing, both build. The rename is what settles it,
    # which is why there is no lock here: two renames onto one directory cannot
    # both succeed, so the second is told the place is taken rather than
    # quietly replacing the first, which is what used to happen.
    log, paper = outing(tmp_path)
    out = tmp_path / "public"
    gate = threading.Barrier(2)
    real = anonymize.export_log

    def both_get_this_far(*args, **kwargs):
        gate.wait()
        return real(*args, **kwargs)

    done: list[str] = []

    def run(key):
        try:
            export_outing(log, paper, out, key)
            done.append("wrote it")
        except ExportError as exc:
            done.append(str(exc))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(anonymize, "export_log", both_get_this_far)
        threads = [threading.Thread(target=run, args=(key,)) for key in (KEY, OTHER)]
        for one in threads:
            one.start()
        for one in threads:
            one.join()
    assert done.count("wrote it") == 1
    assert sum("is already there" in one for one in done) == 1
    assert sorted(one.name for one in out.iterdir()) == ["notebook.txt", "outing-1970-01-01.jsonl"]


def test_two_readers_racing_for_a_key_that_does_not_exist_yet_agree_on_one(tmp_path):
    # The key is the root of the dataset's identity. Made through a temporary
    # moved into place, both would have made one and the second would have
    # replaced the first: an export already written under a key that no longer
    # exists anywhere, so the walk can never be added to again.
    where = tmp_path / "export.key"
    gate = threading.Barrier(2)
    real = anonymize._write_secret

    def both_get_this_far(path, content):
        gate.wait()
        return real(path, content)

    got: list[bytes] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(anonymize, "_write_secret", both_get_this_far)
        threads = [
            threading.Thread(target=lambda: got.append(read_key(where)[0])) for _ in range(2)
        ]
        for one in threads:
            one.start()
        for one in threads:
            one.join()
    assert got[0] == got[1] == where.read_bytes()


# --- one thing, one pseudonym --------------------------------------------------------


def test_one_street_written_three_ways_is_still_one_street(tmp_path):
    # The promise is that a street turning up at two corners is one street, and
    # the rest of Enodia decides that with `folded`. Hashing the spelling made
    # three corners of one avenue into three avenues, in a file whose point is
    # being reconciled again.
    paper = "17:44:30 Agraciada y Freire\n17:46:00 agraciada y Solari\n17:48:00 AGRACIÁDA y Otra\n"
    rows = [scan(44 + minute, [network("Casa", "aa:bb:cc:dd:ee:ff")]) for minute in (1, 2, 4)]
    _, _, notebook = exported(tmp_path, files=outing(tmp_path, rows=rows, notebook=paper))
    corners = [line.split(maxsplit=1)[1] for line in notebook.splitlines() if " y " in line]
    assert len({corner.split(" y ")[0] for corner in corners}) == 1
    assert len({corner.split(" y ")[1] for corner in corners}) == 3


def test_a_place_that_is_not_a_corner_is_folded_the_same_way():
    # `place` comes off the same handwritten page as `street` does.
    names = Names(key=KEY)
    assert names.of("place", "Plaza Matríz") == names.of("place", "plaza  matriz")
    # And an SSID is not: `Casa` and `casa` are two names somebody chose.
    assert names.of("ssid", "Casa") != names.of("ssid", "casa")


# --- the last corners of the filesystem ------------------------------------------------


def test_a_directory_that_is_already_there_and_empty_is_still_already_there(tmp_path):
    # `rename` is not the refusal it looks like: it replaces a destination
    # directory that is empty. Reading its permissions first and copying them
    # was racy, and copying only the mode dropped its ACLs, its owner and its
    # extended attributes without a word. The name is claimed with `mkdir`
    # instead, which refuses anything already there and needs no look first.
    log, paper = outing(tmp_path)
    out = tmp_path / "public"
    out.mkdir(mode=0o700)
    with pytest.raises(ExportError, match="is already there"):
        export_outing(log, paper, out, KEY)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert list(out.iterdir()) == []


def test_a_name_that_leads_nowhere_is_not_a_free_name(tmp_path):
    # A dangling symlink is a name that is taken and a file that is not there.
    log, paper = outing(tmp_path)
    out = tmp_path / "public"
    out.symlink_to(tmp_path / "nada")
    with pytest.raises(ExportError, match="is already there"):
        export_outing(log, paper, out, KEY)


def test_an_export_is_private_while_it_is_being_written(tmp_path):
    # It used to be built with whatever the umask gave and only made private at
    # the end, so a walk was readable by anybody on the machine for as long as
    # it took to write it. Private first and opened at the last moment is the
    # only order with no such window.
    log, paper = outing(tmp_path)
    out = tmp_path / "public"
    seen: list[int] = []
    real = anonymize.os.fsync

    def look(handle):
        for one in tmp_path.iterdir():
            if one.name.startswith(".public"):
                seen.append(stat.S_IMODE(one.stat().st_mode))
        return real(handle)

    old = os.umask(0o022)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(anonymize.os, "fsync", look)
            export_outing(log, paper, out, KEY)
    finally:
        os.umask(old)
    assert set(seen) == {0o700}
    assert stat.S_IMODE(out.stat().st_mode) == 0o755


def test_a_key_is_never_seen_half_written(tmp_path):
    # `O_EXCL` on the real name creates it before the bytes, so whoever lost the
    # race read a key of nought bytes and was told the file was broken while it
    # was merely unfinished.
    where = tmp_path / "export.key"
    named = threading.Event()
    looked = threading.Event()
    real = anonymize.os.link

    def hold_it_there(source, target):
        made = real(source, target)
        named.set()
        looked.wait(5)
        return made

    seen: list[object] = []

    def loser():
        named.wait(5)
        try:
            seen.append(len(read_key(where)[0]))
        except ExportError as exc:  # pragma: no cover - lo que esto existe para evitar
            seen.append(str(exc))
        looked.set()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(anonymize.os, "link", hold_it_there)
        threads = [threading.Thread(target=lambda: read_key(where)), threading.Thread(target=loser)]
        for one in threads:
            one.start()
        for one in threads:
            one.join()
    assert seen == [32]


def test_a_key_that_is_a_name_leading_nowhere_stops_instead_of_going_round(tmp_path):
    # `exists` answers whether what the name leads to is there, and the loop was
    # asking whether the name is taken. A symlink to nothing is the one case
    # where those disagree: `exists` said no, `link` said the name was in use,
    # and between them the command went round until somebody killed it.
    where = tmp_path / "export.key"
    where.symlink_to(tmp_path / "nada")
    with pytest.raises(ExportError, match="does not lead to a file that can be read"):
        read_key(where)


def test_a_key_that_is_not_a_regular_file_stops_instead_of_waiting(tmp_path):
    # A FIFO with nobody writing to it waits for a writer that never comes, and
    # a character device hands over as many bytes as anyone cares to ask for.
    # Both hang a command whose job here was to read half a line of hexadecimal.
    where = tmp_path / "export.key"
    where.mkdir()
    with pytest.raises(ExportError, match="is not a regular file"):
        read_key(where)

    pipe = tmp_path / "pipe.key"
    os.mkfifo(pipe)
    with pytest.raises(ExportError, match="is not a regular file"):
        read_key(pipe)

    # And through a symlink, which is how it would turn up without being asked for.
    pointed = tmp_path / "pointed.key"
    pointed.symlink_to(pipe)
    with pytest.raises(ExportError, match="is not a regular file"):
        read_key(pointed)


def test_a_key_reached_through_a_symlink_is_read_like_any_other(tmp_path):
    # Taken and leading somewhere is not the broken case. `--key-file` is the
    # tidy way to keep a key elsewhere, and a link to one still works.
    real = tmp_path / "somewhere.key"
    real.write_bytes(b"k" * 32)
    real.chmod(0o600)
    where = tmp_path / "export.key"
    where.symlink_to(real)
    assert read_key(where) == (b"k" * 32, None)


def test_a_claim_that_could_not_be_built_on_is_given_back(tmp_path):
    # The name is claimed before anything is built, so a failure making the
    # directory to build in left the claim standing and empty, and the next run
    # was told the destination already existed.
    log, paper = outing(tmp_path)
    out = tmp_path / "public"
    real = Path.mkdir

    def no_room(self, *args, **kwargs):
        if self.name.startswith(".public"):
            raise OSError(28, "No space left on device")
        return real(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "mkdir", no_room)
        with pytest.raises(OSError, match="No space left"):
            export_outing(log, paper, out, KEY)
    assert not out.exists()
    assert export_outing(log, paper, out, KEY).log.exists()  # y el mismo comando funciona


def test_an_export_that_is_written_is_not_reported_as_one_that_was_not(tmp_path):
    # The rename happened, so the export is there and complete. Raising over the
    # step after it told the operator nothing had happened while a finished
    # export sat on the disk, and the refusal to overwrite then stopped them
    # running the command again to find out.
    log, paper = outing(tmp_path)
    out = tmp_path / "public"
    real = anonymize._fsync_dir

    def not_the_parent(path):
        if path == out.parent:
            raise OSError(5, "Input/output error")
        return real(path)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(anonymize, "_fsync_dir", not_the_parent)
        done = export_outing(log, paper, out, KEY)
    assert done.log.exists() and done.notebook.exists()
    assert done.unconfirmed == "Input/output error"
    assert "The export is written" in format_export(done)
