"""Tests for the JSON Lines log, the reader for the old text format, and open networks."""

import json

from ifpeek import AccessPoint

from enodia import netlog
from enodia.netlog import (
    LogRecord,
    NetworkLog,
    SeenNetwork,
    _record_from_json,
    channel_for,
    find_open_networks,
    integer,
    last_cycle,
    last_outing,
    network_from_json,
    outings,
    parse_timestamp,
    read_log,
    read_log_counting,
    record_to_json,
    records_for_outing,
    seen_network,
)


def ap(**overrides):
    fields = {
        "ssid": "Home",
        "bssid": "aa:bb:cc:dd:ee:ff",
        "frequency": 5180,
        "signal_dbm": -47,
        "signal_percent": 100,
        "security": "psk",
        "connected": False,
    }
    fields.update(overrides)
    return AccessPoint(**fields)


def written(path):
    """The log as a list of records."""
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_channel_for_known_bands():
    assert channel_for(2412) == 1
    assert channel_for(2472) == 13
    assert channel_for(2484) == 14
    assert channel_for(5180) == 36
    assert channel_for(5885) == 177
    assert channel_for(5955) == 1
    assert channel_for(7115) == 233


def test_channel_for_unknown():
    assert channel_for(None) is None
    assert channel_for(1000) is None


def test_a_scanned_access_point_becomes_the_network_the_rest_of_the_code_uses():
    assert seen_network(ap()) == SeenNetwork(
        ssid="Home",
        bssid="aa:bb:cc:dd:ee:ff",
        security="psk",
        frequency=5180,
        signal_dbm=-47,
        signal_percent=100,
        connected=False,
    )


def test_what_a_backend_does_not_report_is_none_and_not_a_guess():
    network = seen_network(
        ap(bssid=None, frequency=None, signal_dbm=None, security="open", signal_percent=30)
    )
    assert network.bssid is None and network.frequency is None and network.signal_dbm is None
    assert network.security == "open" and network.signal_percent == 30


def test_a_backend_that_names_no_security_is_not_a_backend_that_says_open():
    assert seen_network(ap(security="")).security is None


def test_each_event_is_written_as_one_json_object(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    log = NetworkLog(path)
    log.record_connected("Home", "aa:bb:cc:dd:ee:ff", "wlan0")
    log.record_scan([ap()], "wlan1")
    log.record_new_networks([ap(ssid="Cafe", security="open")])
    log.record_disconnected("wlan0")

    records = written(path)
    assert [r["event"] for r in records] == ["connected", "scan", "new", "disconnected"]
    assert all(r["time"] == "2026-09-05T00:00:00-03:00" for r in records)
    assert (records[0]["ssid"], records[0]["bssid"], records[0]["interface"]) == (
        "Home",
        "aa:bb:cc:dd:ee:ff",
        "wlan0",
    )
    assert records[1]["interface"] == "wlan1"
    assert records[2]["networks"][0]["security"] == "open"
    assert "interface" not in records[2]  # las redes nuevas son de todas las interfaces
    # Every record says which walk wrote it, the marks included: one file can
    # hold several, and reading it as one gave a notebook the other walk's times.
    assert len({r["outing"] for r in records}) == 1
    assert records[3] == {
        "time": "2026-09-05T00:00:00-03:00",
        "event": "disconnected",
        "outing": records[3]["outing"],
        "interface": "wlan0",
    }


def test_a_connection_without_a_bssid_records_null(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_connected("Home")
    blocks = read_log(path)
    assert (blocks[0].ssid, blocks[0].bssid, blocks[0].interface) == ("Home", None, None)


def test_hostile_ssids_survive_the_round_trip(tmp_path, monkeypatch):
    # An SSID is chosen by whoever owns the network, and can hold anything the
    # old line-oriented format could not carry.
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    nasty = 'Casa, Piso 2 "el fondo"\nEncryption: NONE, Quality: 70/70'
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_scan([ap(ssid=nasty)])

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1  # sigue siendo una linea
    blocks = read_log(path)
    assert len(blocks) == 1 and blocks[0].networks[0].ssid == nasty
    assert not blocks[0].networks[0].open  # el 'Encryption: NONE' del nombre no confunde nada


def test_non_latin_ssids_are_written_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_scan([ap(ssid="Panadería Ñandú 東京")])
    assert "Panadería Ñandú 東京" in path.read_text(encoding="utf-8")


def test_a_truncated_last_line_costs_only_itself(tmp_path, monkeypatch):
    # The battery dies mid-write: everything already written is still readable.
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    log = NetworkLog(path)
    log.record_scan([ap()])
    log.record_scan([ap(ssid="Cafe")])
    with path.open("a", encoding="utf-8") as f:
        f.write('{"time": "2026-09-05T00:00:01-03:00", "event": "scan", "netw')

    blocks = read_log(path)
    assert len(blocks) == 2
    assert [b.networks[0].ssid for b in blocks] == ["Home", "Cafe"]


def test_read_log_round_trips_every_field(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_scan([ap()], "wlan0")

    block = read_log(path)[0]
    assert block.is_scan and block.interface == "wlan0"
    assert block.time.isoformat() == "2026-09-05T00:00:00-03:00"
    network = block.networks[0]
    assert (network.ssid, network.bssid, network.channel, network.signal_dbm) == (
        "Home",
        "aa:bb:cc:dd:ee:ff",
        36,
        -47,
    )
    assert network.security == "psk" and network.signal_percent == 100


def test_read_log_of_an_empty_file(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert read_log(empty) == []


def test_read_jsonl_skips_what_is_not_a_record(tmp_path):
    path = tmp_path / "odd.jsonl"
    path.write_text('{"event": "scan", "networks": "not a list"}\n[1, 2]\n{"event": "scan"}\n')
    blocks = read_log(path)
    assert [b.event for b in blocks] == ["scan", "scan"]
    assert blocks[0].networks == [] and blocks[0].time is None


def test_find_open_networks_keeps_the_strongest_sighting(tmp_path, monkeypatch):
    stamps = iter(["2026-09-05T00:00:00-03:00", "2026-09-05T00:00:05-03:00"])
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    path = tmp_path / "networks.jsonl"
    log = NetworkLog(path)
    log.record_scan(
        [
            ap(ssid="Cafe", bssid="11:22:33:44:55:66", security="open", signal_dbm=-70),
            ap(ssid="Home", security="psk"),
        ]
    )
    log.record_scan(
        [
            ap(ssid="Cafe", bssid="11:22:33:44:55:66", security="open", signal_dbm=-40),
            ap(ssid="Plaza", bssid="11:22:33:44:55:77", security="open", signal_dbm=-85),
        ]
    )

    found = find_open_networks(path)
    assert [f.network.ssid for f in found] == ["Cafe", "Plaza"]  # ordenadas por senal
    assert found[0].network.signal_dbm == -40  # el mejor avistamiento
    assert found[0].time.isoformat() == "2026-09-05T00:00:05-03:00"
    assert "Home" not in [f.network.ssid for f in found]


def test_a_network_with_nothing_reported_reads_as_nulls(tmp_path):
    path = tmp_path / "odd.jsonl"
    path.write_text(
        '{"time": "2026-09-05T00:00:00-03:00", "event": "scan", "networks": ['
        '{"ssid": "Mystery", "bssid": null, "security": null,'
        ' "channel": null, "signal_dbm": null, "signal_percent": null}]}\n'
    )
    network = read_log(path)[0].networks[0]
    assert network.security is None and not network.open
    assert (network.bssid, network.channel, network.signal_dbm) == (None, None, None)
    assert network.signal_percent is None and network.key == "Mystery"
    assert not network.has_signal and network.strength == -999.0


def test_blank_lines_in_a_log_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "gappy.jsonl"
    NetworkLog(path).record_scan([ap()])
    with path.open("a", encoding="utf-8") as f:
        f.write("\n   \n")
    NetworkLog(path).record_scan([ap(ssid="Cafe")])
    assert [b.networks[0].ssid for b in read_log(path)] == ["Home", "Cafe"]


def test_the_frequency_survives_where_a_channel_number_would_not(tmp_path, monkeypatch):
    # Channel 1 is 2412 MHz in 2.4 GHz and 5955 MHz in 6 GHz. Which band an
    # access point is on is what says how far a given signal puts it.
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_scan(
        [
            ap(ssid="Vieja", frequency=2412),
            ap(ssid="Nueva", bssid="aa:bb:cc:dd:ee:02", frequency=5955),
        ]
    )
    old, new = read_log(path)[0].networks
    assert old.channel == new.channel == 1
    assert (old.frequency, new.frequency) == (2412, 5955)


def test_a_scan_carries_nothing_but_what_the_radio_heard(tmp_path, monkeypatch):
    # No interface state on a scan: a cycle that cannot be trusted is a
    # `scan_failed` record, so an empty scan is an empty street, nothing else.
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    log = NetworkLog(path)
    log.record_scan([ap()], "wlan0")
    log.record_scan([], "wlan0")
    full, empty = written(path)
    assert set(full) == set(empty) == {"time", "event", "outing", "interface", "networks"}
    assert empty["networks"] == []


def test_an_association_records_its_frequency(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_connected("Home", "aa:bb:cc:dd:ee:ff", "wlan0", 5180, -47, 100)
    record = read_log(path)[0]
    assert record.frequency == 5180 and record.channel == 36


def test_has_signal_with_either_measure():
    from enodia.netlog import SeenNetwork

    dbm_only = SeenNetwork("A", None, None, None, -60, None)
    percent_only = SeenNetwork("B", None, None, None, None, 40)
    neither = SeenNetwork("C", None, None, None, None, None)
    assert dbm_only.has_signal and percent_only.has_signal and not neither.has_signal


def test_scan_failed_and_suspended_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    log = NetworkLog(path)
    log.record_scan_failed("wlan0", "org.freedesktop.DBus.Error.AccessDenied")
    log.record_suspended(612.34)
    failed, slept = read_log(path)
    assert (failed.event, failed.interface, failed.reason) == (
        "scan_failed",
        "wlan0",
        "org.freedesktop.DBus.Error.AccessDenied",
    )
    assert (slept.event, slept.seconds) == ("suspended", 612.3)
    assert not failed.is_scan and not slept.is_scan  # ninguno cuenta como observación


def test_a_mark_round_trips_with_its_number(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T17:52:10-03:00")
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_mark(7, key=164)
    record = read_log(path)[0]
    assert (record.event, record.number, record.time.isoformat()) == (
        "mark",
        7,
        "2026-09-05T17:52:10-03:00",
    )
    assert written(path)[0]["key"] == 164
    assert not record.is_scan


def test_a_log_line_carrying_nan_is_not_a_record(tmp_path):
    # Python's json reads NaN and Infinity without complaint, and either would
    # poison every average it reached.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        '{"time": "2026-09-05T17:00:00-03:00", "event": "scan", "networks": '
        '[{"ssid": "x", "bssid": "aa", "signal_dbm": NaN}]}\n'
        '{"time": "2026-09-05T17:01:00-03:00", "event": "scan", "networks": []}\n'
    )
    (only,) = read_log(path)
    assert only.time.minute == 1


def test_a_reading_that_overflows_to_infinity_is_not_a_reading(tmp_path):
    # Refusing the bare NaN and Infinity tokens is not enough, and this is the
    # hole it leaves: 1e400 is ordinary JSON, no parse_constant hook ever sees
    # it, and float() turns it into inf all the same. Every number read back
    # from a file is checked for being finite because of this line.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        '{"time": "2026-09-05T17:00:00-03:00", "event": "scan", "networks": '
        '[{"ssid": "x", "bssid": "aa", "signal_dbm": 1e400, "frequency": 2412}]}\n'
    )
    (record,) = read_log(path)
    (network,) = record.networks
    assert network.signal_dbm is None
    assert network.frequency == 2412


def test_a_cycle_that_is_not_a_whole_number_counted_from_one_is_not_a_cycle(tmp_path):
    # `last_cycle` takes the max of these, and the monitor calls it in its own
    # constructor, so one hand-edited line used to stop the walk from starting
    # at all with a TypeError comparing a str to an int. A fraction is refused
    # rather than rounded: cycle 1.7 quietly becoming cycle 1 would fold a look
    # at one place into a look at another.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {"time": "2026-09-05T17:00:00-03:00", "event": "scan", "cycle": bad, "networks": []}
            )
            for bad in ("oops", 1.7, 0, -1, None, True, [2], {"n": 1})
        )
        + "\n"
        + json.dumps(
            {"time": "2026-09-05T17:00:01-03:00", "event": "scan", "cycle": 4, "networks": []}
        )
        + "\n"
    )
    assert [record.cycle for record in read_log(path)] == [None] * 8 + [4]
    assert last_cycle(path) == 4


def test_a_mark_number_that_is_not_a_number_never_reaches_the_count(tmp_path):
    # The same arithmetic, in the other place that resumes an outing: the marks
    # already on the paper are counted on from, with a max over these.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"time": "2026-09-05T17:00:00-03:00", "event": "mark", "number": bad})
            for bad in ("oops", 2.5, 0)
        )
        + "\n"
        + json.dumps({"time": "2026-09-05T17:00:01-03:00", "event": "mark", "number": 3})
        + "\n"
    )
    numbers = [record.number for record in read_log(path)]
    assert numbers == [None, None, None, 3]
    assert max([0, *(n for n in numbers if n)]) == 3


def test_a_suspend_of_minus_three_seconds_is_not_a_suspend(tmp_path):
    path = tmp_path / "networks.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"time": "2026-09-05T17:00:00-03:00", "event": "suspended", "seconds": bad})
            for bad in ("oops", -3, None)
        )
        + "\n"
    )
    assert [record.seconds for record in read_log(path)] == [None, None, None]


def test_a_name_that_is_not_a_name_does_not_travel_to_the_report(tmp_path):
    # Nothing crashes on an SSID that came back as a list, which is worse than
    # if it did: it arrives in the report and prints there as ['x'].
    path = tmp_path / "networks.jsonl"
    path.write_text(
        json.dumps(
            {
                "time": "2026-09-05T17:00:00-03:00",
                "event": ["scan"],
                "interface": 5,
                "networks": [
                    {"ssid": ["x"], "bssid": 7, "signal_dbm": -50},
                    {"ssid": "Real", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -60},
                ],
            }
        )
        + "\n"
    )
    (record,) = read_log(path)
    assert record.event == "" and not record.is_scan  # nada que no sea texto es un evento
    assert record.interface is None
    assert [(n.ssid, n.bssid) for n in record.networks] == [
        ("", None),
        ("Real", "aa:bb:cc:dd:ee:01"),
    ]


def test_a_measurement_keeps_its_fraction_and_an_identity_does_not():
    # The two answer differently on purpose. -47.5 dBm is a reading with a
    # fraction on it and truncating it loses nothing anybody was going to use.
    assert netlog.whole(-47.5) == -47
    assert integer(-47.5) is None
    assert integer(3) == 3 and integer(3.0) == 3


def test_a_signal_that_no_receiver_could_report_is_not_a_reading(tmp_path):
    # `signal_weight` raises ten to the dBm over thirty, so a dBm of 100000
    # arrived as an OverflowError out of an estimate that had no reason to
    # doubt its input. A plausible-looking 500 is worse: it never raises, it
    # just wins every weighting it is in.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        json.dumps(
            {
                "time": "2026-09-05T17:00:00-03:00",
                "event": "scan",
                "networks": [
                    {"ssid": "Loca", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": 100000},
                    {"ssid": "Tambien", "bssid": "aa:bb:cc:dd:ee:02", "signal_percent": 300},
                    {"ssid": "Baja", "bssid": "aa:bb:cc:dd:ee:03", "signal_dbm": -900},
                    {"ssid": "Real", "bssid": "aa:bb:cc:dd:ee:04", "signal_dbm": -47},
                ],
            }
        )
        + "\n"
    )
    (record,) = read_log(path)
    assert [n.signal_dbm for n in record.networks] == [None, None, None, -47]
    assert [n.has_signal for n in record.networks] == [False, False, False, True]


def test_the_live_scan_goes_through_the_same_door_as_the_file():
    # A driver reporting nonsense is no more believable than an edited line, and
    # a walk writes what it heard straight into the file.
    written = seen_network(ap(ssid=["x"], signal_dbm=100000, signal_percent=300, frequency=10**9))
    assert written.ssid == ""
    assert written.signal_dbm is None and written.signal_percent is None
    assert written.frequency is None


def test_a_json_integer_of_four_hundred_digits_is_not_a_number(tmp_path):
    # float() refuses it rather than handing back an infinity, so the check for
    # being finite never got the chance to run.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        '{"time": "2026-09-05T17:00:00-03:00", "event": "scan", "cycle": 1'
        + "0" * 400
        + ', "networks": [{"ssid": "X", "bssid": "aa", "frequency": 1'
        + "0" * 400
        + "}]}\n"
    )
    (record,) = read_log(path)
    assert record.cycle is None
    assert record.networks[0].frequency is None


def test_the_outing_of_a_log_that_is_not_there_is_nobodys(tmp_path):
    assert last_outing(tmp_path / "todavia-no.jsonl") is None


def test_a_timestamp_with_no_offset_is_not_a_timestamp(tmp_path):
    # Python refuses to compare a naive datetime with an aware one, so one
    # edited line without an offset did not cost itself: it took down every scan
    # in the file, out of the sort that puts them in the order they happened.
    assert parse_timestamp("2026-09-17T17:00:00") is None
    assert parse_timestamp("no es una hora") is None
    assert parse_timestamp("2026-09-17T17:00:00-03:00") is not None
    path = tmp_path / "networks.jsonl"
    path.write_text(
        '{"time": "2026-09-17T17:00:00", "event": "scan", "cycle": 1, "networks": []}\n'
        '{"time": "2026-09-17T17:00:01-03:00", "event": "scan", "cycle": 2, "networks": []}\n'
    )
    assert [record.time is None for record in read_log(path)] == [True, False]


def test_a_connected_flag_that_is_not_a_flag_is_not_an_association():
    # "false" is a non-empty string, and bool() of one is True, so a field that
    # meant the opposite of what it said used to arrive as an association.
    assert network_from_json({"ssid": "X", "connected": "false"}).connected is False
    assert network_from_json({"ssid": "X", "connected": 1}).connected is False
    assert network_from_json({"ssid": "X", "connected": True}).connected is True


def test_a_log_of_several_walks_is_read_one_walk_at_a_time(tmp_path):
    path = tmp_path / "networks.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-{day}T17:00:00-03:00",
                    "event": "scan",
                    "outing": walk,
                    "networks": [],
                }
            )
            for day, walk in (("17", "aaaa1111"), ("17", "aaaa1111"), ("18", "bbbb2222"))
        )
        + "\n"
    )
    records = read_log(path)
    assert outings(records) == ["aaaa1111", "bbbb2222"]
    assert len(records_for_outing(records, "aaaa1111")) == 2
    assert len(records_for_outing(records, "bbbb2222")) == 1
    assert len(records_for_outing(records)) == 1  # la última, sin pedir ninguna
    assert records_for_outing(records, "no-existe") == []
    assert records_for_outing([]) == []


def test_a_log_written_before_the_token_existed_holds_one_walk_called_nothing(tmp_path):
    # No special case anywhere: an old file holds exactly one walk, named "".
    path = tmp_path / "networks.jsonl"
    path.write_text(
        '{"time": "2026-09-17T17:00:00-03:00", "event": "scan", "networks": []}\n'
        '{"time": "2026-09-17T17:00:05-03:00", "event": "scan", "networks": []}\n'
    )
    records = read_log(path)
    assert outings(records) == [""]
    assert records_for_outing(records) == records


def test_a_record_that_lost_its_token_does_not_become_a_walk_of_its_own(tmp_path):
    # One line that lost its outing, to an editor or a half-written flush, used
    # to open a walk called "" at the end of the file, and that walk became the
    # one read by default. It belongs to the walk in force where it sits.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        '{"time": "2026-09-17T17:00:00-03:00", "event": "scan", "outing": "aaaa1111", '
        '"networks": []}\n'
        '{"time": "2026-09-17T17:00:05-03:00", "event": "scan", "networks": []}\n'
    )
    records = read_log(path)
    assert outings(records) == ["aaaa1111"]
    assert last_outing(path) == "aaaa1111"
    assert records_for_outing(records) == records  # las dos definiciones coinciden

    # And the old prefix of a file that later started writing tokens is its own
    # walk, the one called "", which is what a whole legacy log is.
    path.write_text(
        '{"time": "2026-09-16T17:00:00-03:00", "event": "scan", "networks": []}\n'
        '{"time": "2026-09-17T17:00:00-03:00", "event": "scan", "outing": "aaaa1111", '
        '"networks": []}\n'
    )
    records = read_log(path)
    assert outings(records) == ["", "aaaa1111"]
    assert len(records_for_outing(records, "")) == 1


# --- one record, written and read by a pair ------------------------------------------


def test_a_record_written_and_read_again_is_the_same_record():
    # The pair is the point. A log is written by one of these and read by the
    # other, and anything they disagree about is a field that leaves by one door
    # and comes back a different shape, which is where `--export-public` kept
    # finding its bugs while it was reading the raw file behind both of them.
    when = parse_timestamp("2026-09-14T17:45:03-03:00")
    networks = [
        SeenNetwork("Casa", "aa:bb:cc:dd:ee:ff", "wpa2", 2412, -47, 55, False),
        SeenNetwork("", None, None, None, None, 30, False),
    ]
    for record in (
        LogRecord(
            event="scan",
            time=when,
            outing="3f9a2b10",
            interface="wlan0",
            cycle=7,
            networks=networks,
        ),
        LogRecord(event="new", time=when, outing="3f9a2b10", interface="wlan0", networks=networks),
        LogRecord(
            event="connected",
            time=when,
            outing="3f9a2b10",
            interface="wlan0",
            ssid="Casa",
            bssid="aa:bb:cc:dd:ee:ff",
            frequency=5180,
            signal_dbm=-47,
            signal_percent=100,
        ),
        LogRecord(event="disconnected", time=when, outing="3f9a2b10", interface="wlan0"),
        LogRecord(
            event="scan_failed",
            time=when,
            outing="3f9a2b10",
            interface="wlan0",
            reason="radio soft blocked (rfkill)",
        ),
        LogRecord(event="suspended", time=when, outing="3f9a2b10", seconds=42.5),
        LogRecord(event="mark", time=when, outing="3f9a2b10", number=3, key=164),
        LogRecord(event="button_lost", time=when, outing="3f9a2b10", reason="SYN_DROPPED"),
    ):
        assert _record_from_json(record_to_json(record)) == record


def test_a_mark_keeps_the_key_it_was_pressed_with_when_it_is_read_back(tmp_path, monkeypatch):
    # The one field the writer wrote and the reader dropped, which is why the
    # export had to go behind the type to the raw file to keep it.
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_mark(3, 164)
    (record,) = read_log(path)
    assert (record.number, record.key) == (3, 164)


def test_a_mark_pressed_by_no_key_at_all_says_nothing_about_a_key(tmp_path, monkeypatch):
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    NetworkLog(path).record_mark(3)
    assert "key" not in written(path)[0]
    assert read_log(path)[0].key is None


def test_a_scan_that_heard_nothing_still_writes_that_it_heard_nothing(tmp_path, monkeypatch):
    # An empty list is a street with no Wi-Fi on it, and a missing field is a
    # scan that never happened. They are not the same reading.
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T00:00:00-03:00")
    path = tmp_path / "networks.jsonl"
    log = NetworkLog(path)
    log.record_scan([], "wlan0", 1)
    log.record_suspended(2.0)
    scan, suspended = written(path)
    assert scan["networks"] == []
    assert "networks" not in suspended


def test_a_line_that_is_json_and_cannot_be_used_is_counted_and_junk_is_not(tmp_path):
    # Junk costs only itself. A line that parses as JSON and carries a number
    # nothing here can represent was a record somebody's walk wrote, and a
    # reader that drops it without a word makes the file quietly shorter.
    path = tmp_path / "networks.jsonl"
    path.write_text(
        '{"time": "2026-09-14T17:45:03-03:00", "event": "scan", "networks": []}\n'
        "no es json\n"
        "\n"
        '{"time": "2026-09-14T17:46:03-03:00", "event": "scan", "signal_dbm": NaN}\n'
    )
    records, lost = read_log_counting(path)
    assert (len(records), lost) == (1, 1)
    assert read_log(path) == records


def test_json_that_is_not_a_record_at_all_is_a_line_that_could_not_be_used(tmp_path):
    # `[1, 2, 3]` parses and is not a record, which makes it a line this cannot
    # use as much as one carrying a NaN is. Counting only the second made the
    # number smaller than the reason for having it.
    path = tmp_path / "networks.jsonl"
    path.write_text('[1, 2, 3]\n"hola"\n42\nno es json\n')
    assert read_log_counting(path) == ([], 3)
