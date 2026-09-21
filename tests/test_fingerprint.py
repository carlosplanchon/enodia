"""Tests for the fingerprint map: building it, looking yourself up in it, measuring it."""

import json
import os
import stat
import threading
from datetime import datetime, timedelta, timezone

import ifpeek
import pytest
from ifpeek import AccessPoint

from enodia import fingerprint, netlog
from enodia.fingerprint import (
    Fingerprint,
    HeldOutScan,
    MapSummary,
    Place,
    RadioBlocked,
    add_to_map,
    canonical,
    check_map,
    fingerprints_from,
    folded,
    format_location,
    format_map_check,
    locate_scan,
    map_summary,
    outing_name,
    read_map,
    scan_from_log,
    scan_now,
    similarity,
    write_map,
)
from enodia.netlog import LogRecord, NetworkLog, SeenNetwork
from enodia.reconcile import (
    NotebookError,
    PlacedScan,
    Position,
    Reconciliation,
    Waypoint,
    reconcile,
)

TZ = timezone(timedelta(hours=-3))


def net(name, dbm=-60, bssid=None):
    return SeenNetwork(name, bssid or f"aa:bb:cc:dd:ee:{ord(name[0]):02x}", "wpa2", 2412, dbm, None)


def ap(ssid, bssid="aa:bb:cc:dd:ee:01", dbm=-60):
    return AccessPoint(
        ssid=ssid,
        bssid=bssid,
        frequency=2412,
        signal_dbm=dbm,
        signal_percent=70,
        security="psk",
        connected=False,
    )


def mark(fraction, *networks, walk="one#0", street=("Alfa", "Bravo"), when=None, length=100.0):
    """A fingerprint at a fraction of one stretch, built without a reconciliation."""
    return Fingerprint(
        Place(street[0], street[1], fraction, None, None, length),
        tuple(networks),
        walk.split("#")[0],
        walk,
        when,
    )


def walk_log(tmp_path, monkeypatch, scans, notebook, outing=""):
    """A log of timed scans and the notebook that places them."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    stamps = iter(stamp for stamp, _ in scans)
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "paseo.jsonl")
    log.outing = outing
    for _, networks in scans:
        log.record_scan(networks, "wlan0")
    nb = tmp_path / "libreta.txt"
    nb.write_text(notebook)
    return log.path, nb


# --- the canonical frame ------------------------------------------------------


def test_a_place_is_stored_in_one_frame_whichever_way_it_was_walked():
    assert canonical("Alfa", "Bravo", 0.25) == ("Alfa", "Bravo", 0.25)
    assert canonical("Bravo", "Alfa", 0.25) == ("Alfa", "Bravo", 0.75)
    # Un tramo degenerado (el mismo cruce anotado dos veces) se deja como está.
    assert canonical("Alfa", "Alfa", 0.4) == ("Alfa", "Alfa", 0.4)


def test_the_return_pass_lands_on_top_of_the_outbound_one(tmp_path, monkeypatch):
    # Walked Bravo to Alfa and straight back. The two scans below were taken at
    # the same spot on the street, a quarter of the way from Bravo, one on each
    # pass. Stored as the reconciliation phrased them they would be 0.25 and
    # 0.75 of two different-looking stretches. Canonically they are one place.
    log, nb = walk_log(
        tmp_path,
        monkeypatch,
        [
            ("2026-09-05T17:00:30-03:00", [ap("Casa")]),
            ("2026-09-05T17:03:30-03:00", [ap("Casa")]),
        ],
        "17:00 Bravo\n17:02 Alfa\n17:04 Bravo\n",
    )
    there, back = fingerprints_from(reconcile(log, nb, by_movement=False), "paseo")
    assert there.place.stretch == back.place.stretch == ("Alfa", "Bravo")
    assert there.place.fraction == pytest.approx(0.75)
    assert back.place.fraction == pytest.approx(0.75)
    assert (there.walk, back.walk) == ("paseo#0", "paseo#1")


def test_two_interfaces_scanning_at_once_make_one_fingerprint_not_two(tmp_path, monkeypatch):
    # Watching two interfaces writes a record each at the same instant, from one
    # place. Counted twice it would weigh double among the neighbours.
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: "2026-09-05T17:05:00-03:00")
    log = NetworkLog(tmp_path / "paseo.jsonl")
    log.record_scan([ap("Casa", bssid="aa:bb:cc:dd:ee:01")], "wlan0")
    log.record_scan([ap("Bar", bssid="aa:bb:cc:dd:ee:02")], "wlan1")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Alfa\n17:10 Bravo\n")
    (only,) = fingerprints_from(reconcile(log.path, nb, by_movement=False), "paseo")
    assert only.keys == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}


# --- the file -----------------------------------------------------------------


def test_a_map_survives_a_round_trip_through_the_file(tmp_path):
    path = tmp_path / "mapa.jsonl"
    when = datetime(2026, 9, 5, 17, 5, tzinfo=TZ)
    original = Fingerprint(
        Place("Alfa", "Bravo", 0.25, -34.9, -56.19, 118.4),
        (net("Casa", -55), SeenNetwork("Hidden", "aa:bb:cc:dd:ee:09", None, None, None, 40)),
        "paseo",
        "paseo#0",
        when,
    )
    assert write_map(path, [original]) == 1
    (back,) = read_map(path)
    assert (back.place, back.outing, back.walk, back.time) == (
        original.place,
        "paseo",
        "paseo#0",
        when,
    )
    assert back.keys == original.keys
    assert [n.strength for n in back.networks] == [n.strength for n in original.networks]
    # The map keeps only what matching uses. Security says nothing about where
    # you are, and the band is already in the BSSID, so neither is written.
    assert back.networks[0].security is None and back.networks[0].frequency is None
    written = json.loads(path.read_text())
    assert written["from"] == "Alfa" and written["to"] == "Bravo"
    assert written["fraction"] == 0.25 and written["length_m"] == 118.4
    assert written["networks"][0] == {
        "ssid": "Casa",
        "bssid": "aa:bb:cc:dd:ee:43",
        "signal_dbm": -55,
    }
    assert written["networks"][1]["signal_percent"] == 40  # una red sin dBm conserva su fuerza


def test_a_fingerprint_with_nothing_but_a_place_round_trips_too(tmp_path):
    path = tmp_path / "mapa.jsonl"
    write_map(path, [Fingerprint(Place("Alfa", "Bravo", 0.5), ())])
    (back,) = read_map(path)
    assert back.place.coordinates is None and back.place.length_m is None
    assert back.time is None and back.networks == ()
    assert "lat" not in json.loads(path.read_text())


def test_a_missing_map_file_reads_as_an_empty_map(tmp_path):
    assert read_map(tmp_path / "todavia-no.jsonl") == []


def test_a_map_line_that_is_not_a_fingerprint_is_skipped(tmp_path):
    path = tmp_path / "mapa.jsonl"
    path.write_text(
        "\n"
        "no es json\n"
        "[1, 2]\n"
        '{"from": "Alfa", "to": 7, "fraction": 0.5}\n'
        '{"from": "Alfa", "to": "Bravo"}\n'
        '{"from": "Alfa", "to": "Bravo", "fraction": 0.5, "networks": "no es una lista"}\n'
    )
    (only,) = read_map(path)
    assert only.place.fraction == 0.5 and only.networks == ()


def test_adding_an_outing_writes_fingerprints_and_a_second_one_appends(tmp_path, monkeypatch):
    path = tmp_path / "mapa.jsonl"
    log, nb = walk_log(
        tmp_path,
        monkeypatch,
        [("2026-09-05T17:05:00-03:00", [ap("Casa")])],
        "17:00 Alfa\n17:10 Bravo\n",
    )
    assert add_to_map(path, log, nb) == (1, "2026-09-05T17:05:00-03:00")
    # The same log under another name is the same outing and is refused, which
    # is the point of naming an outing by when it happened.
    same = tmp_path / "copia.jsonl"
    same.write_text(log.read_text())
    assert add_to_map(path, same, nb) == (0, "2026-09-05T17:05:00-03:00")
    other, nb2 = walk_log(
        tmp_path / "otro",
        monkeypatch,
        [("2026-09-12T17:05:00-03:00", [ap("Casa")])],
        "17:00 Alfa\n17:10 Bravo\n",
    )
    assert add_to_map(path, other, nb2) == (1, "2026-09-12T17:05:00-03:00")
    assert len({one.outing for one in read_map(path)}) == 2


def test_adding_the_same_outing_twice_is_refused_instead_of_doubling_it(tmp_path, monkeypatch):
    path = tmp_path / "mapa.jsonl"
    log, nb = walk_log(
        tmp_path,
        monkeypatch,
        [("2026-09-05T17:05:00-03:00", [ap("Casa")])],
        "17:00 Alfa\n17:10 Bravo\n",
    )
    assert add_to_map(path, log, nb)[0] == 1
    assert add_to_map(path, log, nb)[0] == 0
    assert len(read_map(path)) == 1


# --- similarity ---------------------------------------------------------------


def test_similarity_is_how_much_of_what_is_in_view_the_two_share():
    here = mark(0.5, net("A"), net("B"))
    assert similarity({"aa:bb:cc:dd:ee:41": -60.0, "aa:bb:cc:dd:ee:42": -60.0}, here) == 1.0
    assert similarity({"aa:bb:cc:dd:ee:41": -60.0}, here) == pytest.approx(0.5)
    assert similarity({"aa:bb:cc:dd:ee:5a": -60.0}, here) == 0.0


def test_the_signal_term_separates_two_places_that_see_the_same_networks():
    # Same access points in view from both, so on the networks alone the two are
    # indistinguishable. What tells them apart is how loud each one came in.
    near = mark(0.2, net("A", -40), net("B", -80))
    far = mark(0.8, net("A", -80), net("B", -40))
    query = {"aa:bb:cc:dd:ee:41": -42.0, "aa:bb:cc:dd:ee:42": -78.0}
    assert similarity(query, near) == similarity(query, far) == 1.0
    assert similarity(query, near, by_signal=True) > similarity(query, far, by_signal=True)


def test_a_fingerprint_with_no_levels_ranks_below_one_whose_levels_match():
    # Nothing to compare is not a perfect match. Scored as one, every fingerprint
    # that kept no levels would outrank every fingerprint that actually agreed.
    silent = mark(0.5, SeenNetwork("A", "aa:bb:cc:dd:ee:41", None, None, None, None))
    measured = mark(0.5, net("A", -50))
    query = {"aa:bb:cc:dd:ee:41": -50.0}
    assert similarity(query, silent, by_signal=True) == pytest.approx(0.75)
    assert similarity(query, measured, by_signal=True) == pytest.approx(1.0)
    assert similarity(query, silent) == 1.0  # sin el término de señal son iguales


def test_the_signal_term_does_not_bother_with_a_fingerprint_that_shares_nothing():
    assert similarity({"aa:bb:cc:dd:ee:5a": -60.0}, mark(0.5, net("A")), by_signal=True) == 0.0


# --- locating -----------------------------------------------------------------


def test_an_exact_scan_finds_the_place_it_was_taken_from():
    here = [mark(0.2, net("A"), net("B")), mark(0.8, net("C"), net("D"))]
    found = locate_scan(here, [net("A"), net("B")])
    assert found is not None
    assert found.place.fraction == pytest.approx(0.2)
    assert found.score == 1.0 and found.matches == 1 and found.spread == 0.0
    assert not found.uncertain
    assert found.describe() == 'between "Alfa" and "Bravo", 20% of the way'


def test_a_scan_with_nothing_in_view_is_not_located():
    assert locate_scan([mark(0.5, net("A"))], []) is None


def test_a_scan_sharing_nothing_with_the_map_is_not_located():
    assert locate_scan([mark(0.5, net("A"))], [net("Z")]) is None


def test_a_weak_best_match_abstains_instead_of_guessing():
    # One network in common out of nine: off the map, and saying so is the answer.
    here = mark(0.5, *[net(name) for name in "ABCDE"])
    query = [net("A"), *[net(name) for name in "VWXYZ"]]
    assert similarity({n.key: n.strength for n in query}, here) < 0.15
    assert locate_scan([here], query) is None
    assert locate_scan([here], query, floor=0.0) is not None  # el piso es lo que se abstiene


def test_two_candidate_streets_are_both_reported_instead_of_averaged():
    # Two stretches match equally. Averaging them would place you inside the
    # block between the two, which is the one place you certainly were not.
    here = [
        mark(0.2, net("A"), net("B"), street=("Alfa", "Bravo")),
        mark(0.8, net("A"), net("B"), street=("Charlie", "Delta")),
    ]
    found = locate_scan(here, [net("A"), net("B")])
    assert found is not None and found.uncertain
    assert found.place.stretch != found.alternative.stretch
    assert {found.place.stretch, found.alternative.stretch} == {
        ("Alfa", "Bravo"),
        ("Charlie", "Delta"),
    }


def test_a_dominant_street_is_reported_without_an_alternative():
    here = [
        mark(0.2, net("A"), net("B"), street=("Alfa", "Bravo")),
        mark(0.3, net("A"), net("B"), street=("Alfa", "Bravo")),
        mark(0.4, net("A"), net("B"), street=("Alfa", "Bravo")),
        mark(0.8, net("A"), net("Z"), street=("Charlie", "Delta")),
    ]
    found = locate_scan(here, [net("A"), net("B")])
    assert found is not None and not found.uncertain and found.alternative is None
    assert found.matches == 3
    assert found.place.fraction == pytest.approx(0.3)
    assert found.spread == pytest.approx(0.0667, abs=0.001)


def test_the_middle_of_the_matches_carries_their_coordinates():
    here = [
        Fingerprint(Place("Alfa", "Bravo", 0.2, -34.90, -56.20), (net("A"),)),
        Fingerprint(Place("Alfa", "Bravo", 0.4, -34.90, -56.18), (net("A"),)),
    ]
    found = locate_scan(here, [net("A")])
    assert found is not None and found.place.coordinates is not None
    assert found.place.lon == pytest.approx(-56.19)


def test_the_location_reports_the_freshest_evidence_behind_it():
    old = datetime(2025, 1, 1, 9, 0, tzinfo=TZ)
    new = datetime(2026, 9, 5, 17, 5, tzinfo=TZ)
    here = [mark(0.2, net("A"), when=old), mark(0.3, net("A"), when=new)]
    found = locate_scan(here, [net("A")])
    assert found is not None and found.when == new


# --- the report ---------------------------------------------------------------


def test_the_report_says_where_you_are_and_how_sure_it_is():
    # Two hundred metres apart, which is what a two hundred metre block allows.
    here = [
        Fingerprint(Place("Alfa", "Bravo", 0.2, -34.90, -56.200000, 200.0), (net("A"),)),
        Fingerprint(
            Place("Alfa", "Bravo", 0.4, -34.90, -56.197811, 200.0),
            (net("A"),),
            time=datetime(2026, 9, 5, 17, 5, tzinfo=TZ),
        ),
    ]
    report = format_location(locate_scan(here, [net("A")]), "mapa.jsonl")
    assert 'You are between "Alfa" and "Bravo", 30% of the way' in report
    assert "around [-34.90000, -56.19891]" in report
    assert "2 fingerprints agree, best similarity 100%, spread 10% of the stretch (20 m)" in report
    assert "from evidence last gathered 2026-09-05 17:05" in report
    assert "Uncertain" not in report


def test_the_report_says_plainly_when_you_are_not_on_the_map():
    report = format_location(None, "mapa.jsonl")
    assert "Not on the map" in report and "mapa.jsonl" in report


def test_the_report_names_the_other_candidate_street():
    here = [
        mark(0.2, net("A"), net("B"), street=("Alfa", "Bravo"), length=None),
        mark(0.8, net("A"), net("B"), street=("Charlie", "Delta"), length=None),
    ]
    report = format_location(locate_scan(here, [net("A"), net("B")]), "mapa.jsonl")
    assert "Uncertain: it could as easily be" in report
    assert " m)" not in report  # sin largo de cuadra no hay metros que dar
    assert "evidence last gathered" not in report


# --- scanning where you stand -------------------------------------------------


def test_a_live_scan_asks_every_radio_and_puts_their_views_together(monkeypatch):
    # The map was built from the union of a cycle's radios. Asking with one
    # card's half is training on the whole and querying with a fraction.
    monkeypatch.setattr(ifpeek, "get_wifi_interfaces", lambda: ["wlan0", "wlan1"])
    monkeypatch.setattr(fingerprint.ifpeek, "interface_rfkill", lambda interface: None)
    asked = []

    def scan(interface=None, fresh=False):
        asked.append((interface, fresh))
        return {
            "wlan0": [ap("Casa", dbm=-70), ap("Bar", bssid="aa:bb:cc:dd:ee:02", dbm=-50)],
            "wlan1": [ap("Casa", dbm=-44)],
        }[interface]

    monkeypatch.setattr(ifpeek, "scan_access_points", scan)
    found = {one.ssid: one for one in scan_now()}
    assert asked == [("wlan0", True), ("wlan1", True)]
    assert set(found) == {"Casa", "Bar"}
    assert found["Casa"].signal_dbm == -44  # la lectura más fuerte de las dos

    asked.clear()
    scan_now(["wlan1"])
    assert asked == [("wlan1", True)]  # y solo las que se le piden


def test_every_radio_blocked_stops_a_live_scan(monkeypatch):
    monkeypatch.setattr(fingerprint.ifpeek, "interface_rfkill", lambda interface: "soft")
    with pytest.raises(RadioBlocked, match="wlan0: radio soft blocked"):
        scan_now(["wlan0"])


def test_one_radio_blocked_and_one_working_still_answers(monkeypatch):
    # The monitor would walk on wlan1, so looking yourself up should too.
    monkeypatch.setattr(
        fingerprint.ifpeek, "interface_rfkill", lambda card: "hard" if card == "wlan0" else None
    )
    monkeypatch.setattr(
        ifpeek, "scan_access_points", lambda interface=None, fresh=False: [ap("Casa")]
    )
    assert [one.ssid for one in scan_now(["wlan0", "wlan1"])] == ["Casa"]


def test_no_wifi_interface_stops_a_live_scan(monkeypatch):
    monkeypatch.setattr(ifpeek, "get_wifi_interfaces", list)
    with pytest.raises(RadioBlocked, match="no Wi-Fi interface"):
        scan_now()


def test_the_last_scan_of_a_log_is_what_gets_located(tmp_path, monkeypatch):
    log, _ = walk_log(
        tmp_path,
        monkeypatch,
        [
            ("2026-09-05T17:00:00-03:00", [ap("Antes")]),
            ("2026-09-05T17:05:00-03:00", [ap("Ahora")]),
        ],
        "17:00 Alfa\n17:10 Bravo\n",
    )
    assert [n.ssid for n in scan_from_log(log)] == ["Ahora"]


def test_a_log_with_no_scans_cannot_be_located(tmp_path):
    log = tmp_path / "vacio.jsonl"
    log.write_text('{"time": "2026-09-05T17:00:00-03:00", "event": "mark", "number": 1}\n')
    with pytest.raises(NotebookError, match="no timestamped scans found"):
        scan_from_log(log)


def test_a_scan_with_no_time_costs_only_itself(tmp_path):
    # Every other reader of a log asks for a timestamp and this one did not, so
    # one line missing one took down the whole lookup: the scans are put in
    # order by their times, and there was nothing to order that one by.
    log = tmp_path / "torcido.jsonl"
    log.write_text(
        '{"event": "scan", "cycle": 1, "networks": [{"ssid": "Perdida"}]}\n'
        '{"time": "2026-09-05T17:00:01-03:00", "event": "scan", "cycle": 2, "networks": '
        '[{"ssid": "X", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -50}]}\n'
    )
    assert [network.ssid for network in scan_from_log(log)] == ["X"]

    only_bad = tmp_path / "solo-torcido.jsonl"
    only_bad.write_text('{"event": "scan", "cycle": 1, "networks": []}\n')
    with pytest.raises(NotebookError, match="no timestamped scans found"):
        scan_from_log(only_bad)


# --- measuring ----------------------------------------------------------------


def two_walks(fraction_a=0.2, fraction_b=0.25):
    """The same stretch walked twice, each pass seeing the same two networks."""
    return [
        mark(fraction_a, net("A", -40), net("B", -80), walk="paseo#0"),
        mark(fraction_b, net("A", -40), net("B", -80), walk="paseo#1"),
    ]


def test_the_check_holds_out_a_whole_walk_not_a_single_scan():
    # Each pass is located from the other one only. With its own neighbours left
    # in, a scan would find the one taken five seconds later and score perfectly.
    results = check_map(two_walks())
    assert len(results) == 2
    assert all(not r.abstained and not r.wrong_stretch for r in results)
    assert all(r.error_fraction == pytest.approx(0.05) for r in results)
    assert all(r.error_m == pytest.approx(5.0) for r in results)  # 5% de 100 m


def test_a_map_with_one_walk_has_nothing_to_check():
    assert check_map([mark(0.2, net("A")), mark(0.4, net("A"))]) == []


def test_landing_on_the_wrong_stretch_counts_apart_from_the_distance_error():
    # Two streets that see exactly the same networks: the map cannot tell them
    # apart, and the miss is a different street, not a small error.
    results = check_map(
        [
            mark(0.2, net("A"), net("B"), walk="paseo#0", street=("Alfa", "Bravo")),
            mark(0.2, net("A"), net("B"), walk="paseo#1", street=("Charlie", "Delta")),
        ]
    )
    assert [r.wrong_stretch for r in results] == [True, True]
    assert all(r.error_m is None and r.error_fraction is None for r in results)


def test_the_error_is_measured_in_metres_when_both_places_have_coordinates():
    here = [
        Fingerprint(Place("Alfa", "Bravo", 0.2, -34.90, -56.2000), (net("A"),), walk="p#0"),
        Fingerprint(Place("Alfa", "Bravo", 0.3, -34.90, -56.1990), (net("A"),), walk="p#1"),
    ]
    results = check_map(here)
    assert all(r.error_m == pytest.approx(91, abs=2) for r in results)


def test_an_abstention_is_neither_a_hit_nor_a_wrong_stretch():
    held = HeldOutScan(Place("Alfa", "Bravo", 0.2), None, None, None)
    assert held.abstained and not held.wrong_stretch


def test_a_scan_at_a_mark_answered_on_the_next_stretch_is_placed_across_it():
    # The scan was taken two metres before the mark and the answer is two
    # metres past it, on the stretch next door. That is a four-metre error
    # through the mark the two stretches share, and it was counted as landing
    # on the wrong stretch, which the report calls a different street.
    lunes = mark(0.98, net("A"), net("B"), walk="lunes#0", street=("Alfa", "Bravo"))
    martes = [
        mark(0.02, net("A"), net("B"), walk="martes#1", street=("Bravo", "Charlie")),
        mark(0.5, net("X"), walk="martes#0", street=("Alfa", "Bravo")),
    ]
    held, *_ = check_map([lunes, *martes])
    assert held.truth.fraction == 0.98
    assert held.across_mark and not held.wrong_stretch and not held.abstained
    assert held.error_fraction == pytest.approx(0.04)
    assert held.error_m == pytest.approx(4.0)  # dos metros hasta la marca y dos después


def test_an_error_across_a_mark_is_the_straight_distance_when_both_have_coordinates():
    before = Fingerprint(
        Place("Alfa", "Bravo", 0.98, -34.9000, -56.20002, 100.0), (net("A"),), "lunes", "lunes#0"
    )
    after = Fingerprint(
        Place("Bravo", "Charlie", 0.02, -34.9000, -56.19998, 100.0), (net("A"),), "martes", "m#1"
    )
    held = check_map([before, after])[0]
    assert held.across_mark and held.error_fraction == pytest.approx(0.04)
    assert held.error_m == pytest.approx(3.65, abs=0.1)  # 0.00004 grados de longitud


def test_an_error_across_a_mark_has_no_metres_when_a_stretch_has_no_length():
    before = mark(0.98, net("A"), walk="lunes#0", street=("Alfa", "Bravo"))
    after = mark(0.02, net("A"), walk="martes#1", street=("Bravo", "Charlie"), length=None)
    held = check_map([before, after])[0]
    assert held.across_mark and held.error_fraction == pytest.approx(0.04)
    assert held.error_m is None


def test_with_two_outings_the_whole_outing_is_held_out_and_its_next_stretch_with_it():
    # The scan at the end of one stretch has a twin five seconds later at the
    # start of the next stretch of the same outing. Holding out the walk alone
    # left that twin in, and the map scored itself on a copy of the question at
    # the end of every stretch. With another outing on the map the unit held
    # out is the outing, so the twin goes with it and the other day has to
    # answer, and here the other day cannot.
    lunes = [
        mark(0.98, net("A"), net("B"), walk="lunes#0", street=("Alfa", "Bravo")),
        mark(0.02, net("A"), net("B"), walk="lunes#1", street=("Bravo", "Charlie")),
    ]
    martes = [mark(0.5, net("X"), walk="martes#0", street=("Alfa", "Bravo"))]
    results = check_map([*lunes, *martes])
    assert [held.abstained for held in results] == [True, True, True]
    report = format_map_check(
        [*lunes, *martes], results, check_map([*lunes, *martes], by_signal=True)
    )
    assert "Each outing held out in turn, and its scans located from the other outings:" in report
    assert "placed across a mark                    0              0" in report


def test_the_check_reports_both_methods_side_by_side():
    walks = two_walks()
    report = format_map_check(walks, check_map(walks), check_map(walks, by_signal=True))
    assert "Map: 2 fingerprints, 2 walks over 1 stretches, 1 outings." in report
    assert "Each walk held out in turn" in report
    assert "by networks" in report and "and by signal" in report
    assert "scans held out                          2              2" in report
    assert "placed on the right stretch             2              2" in report
    assert "placed across a mark                    0              0" in report
    assert "mean error, of a stretch               5%             5%" in report
    assert "mean error in metres                  5 m            5 m" in report
    assert "median error in metres                5 m            5 m" in report
    assert "The distances cover" not in report  # todos los tramos tienen largo
    assert "counted apart rather than averaged" in report


def test_the_check_reports_fractions_when_no_stretch_has_a_length():
    walks = [
        mark(0.2, net("A"), walk="paseo#0", length=None),
        mark(0.4, net("A"), walk="paseo#1", length=None),
    ]
    report = format_map_check(walks, check_map(walks), check_map(walks, by_signal=True))
    assert "mean error, of a stretch              20%            20%" in report
    assert "mean error in metres                    -              -" in report
    assert "The distances cover" not in report  # ninguno tiene largo, nada que aclarar


def test_the_check_reports_nothing_measurable_when_every_scan_missed():
    walks = [
        mark(0.2, net("A"), walk="paseo#0", street=("Alfa", "Bravo")),
        mark(0.2, net("A"), walk="paseo#1", street=("Charlie", "Delta")),
    ]
    report = format_map_check(walks, check_map(walks), check_map(walks, by_signal=True))
    assert "mean error, of a stretch                -              -" in report
    assert "mean error in metres                    -              -" in report
    assert "landed on the wrong stretch             2              2" in report
    assert "placed on the right stretch             0              0" in report


def test_the_median_of_an_even_and_an_odd_number_of_errors():
    walks = [
        mark(0.0, net("A"), walk="paseo#0"),
        mark(0.2, net("A"), walk="paseo#1"),
        mark(0.6, net("A"), walk="paseo#2"),
    ]
    # Tres pasadas, tres errores: la mediana es el del medio, no un promedio.
    report = format_map_check(walks, check_map(walks), check_map(walks, by_signal=True))
    assert "median error" in report


def test_nothing_to_check_is_said_rather_than_shown_as_an_empty_table():
    report = format_map_check([], [], [])
    assert "Nothing to check" in report and "turn round at the corner" in report


# --- what the review of the design turned up ----------------------------------


def test_a_crossing_spelled_a_little_differently_is_the_same_stretch():
    # A notebook is written by hand, weeks apart. Accents and case drift, and a
    # map that took the drift for a different street would split in two silently.
    assert folded("  Yaguarón   y  18 de Julio ") == folded("yaguaron y 18 de julio")
    # "bravo" plegado va antes que "yaguaron", así que el tramo se da vuelta.
    assert canonical("yaguaron", "Bravo", 0.25) == ("Bravo", "yaguaron", 0.75)
    here = Place("Yaguarón", "Bravo", 0.2)
    there = Place("yaguaron", "bravo", 0.2)
    assert here.stretch != there.stretch  # los nombres se guardan como se escribieron
    assert here.key == there.key  # pero es una sola calle


def test_the_folded_name_is_what_groups_the_neighbours():
    here = [
        mark(0.2, net("A"), net("B"), street=("Yaguarón", "Bravo")),
        mark(0.3, net("A"), net("B"), street=("yaguaron", "bravo")),
    ]
    found = locate_scan(here, [net("A"), net("B")])
    assert found is not None and not found.uncertain and found.matches == 2


def test_an_outing_is_named_by_its_first_scan_not_by_its_file(tmp_path, monkeypatch):
    log, nb = walk_log(
        tmp_path,
        monkeypatch,
        [("2026-09-05T17:05:00-03:00", [ap("Casa")])],
        "17:00 Alfa\n17:10 Bravo\n",
    )
    assert outing_name(reconcile(log, nb, by_movement=False)) == "2026-09-05T17:05:00-03:00"


def test_an_outing_whose_scans_all_fell_outside_the_notebook_adds_nothing(tmp_path, monkeypatch):
    log, nb = walk_log(
        tmp_path,
        monkeypatch,
        [("2026-09-05T19:00:00-03:00", [ap("Casa")])],  # después del último cruce
        "17:00 Alfa\n17:10 Bravo\n",
    )
    assert outing_name(reconcile(log, nb, by_movement=False)) == ""
    assert add_to_map(tmp_path / "mapa.jsonl", log, nb) == (0, "")


def test_the_check_says_when_a_map_only_recognised_its_own_walk():
    # Everything here came from one outing, so the map is recognising a walk,
    # not a place, and the report has to say so rather than look accurate.
    walks = two_walks()
    report = format_map_check(walks, check_map(walks), check_map(walks, by_signal=True))
    assert "2 of 2 answers were backed only by the outing the scan came from" in report
    assert "recognising a walk, not a place" in report


def test_a_second_outing_over_the_same_street_is_not_flattered():
    walks = [
        Fingerprint(Place("Alfa", "Bravo", 0.2, None, None, 100.0), (net("A"),), "lunes", "l#0"),
        Fingerprint(Place("Alfa", "Bravo", 0.3, None, None, 100.0), (net("A"),), "martes", "m#0"),
    ]
    report = format_map_check(walks, check_map(walks), check_map(walks, by_signal=True))
    assert "backed only by the outing" not in report


def test_the_map_lives_where_resuming_a_log_will_never_find_it(tmp_path):
    from enodia.system import data_dir, map_path, session_log_path

    environ = {"XDG_DATA_HOME": str(tmp_path)}
    where = map_path(environ)
    assert where.parent != data_dir(environ)  # o --resume lo tomaría por un log
    write_map(where, [Fingerprint(Place("Alfa", "Bravo", 0.5), ())])
    chosen, continuing = session_log_path(data_dir(environ), resume=True)
    assert chosen != where and not continuing


def test_a_map_of_mixed_notebooks_does_not_average_metres_over_a_subset():
    # Half the stretches came from a notebook with coordinates and half did not.
    # Averaging only the measurable half into one "mean error in metres" would
    # report a number over two of four answers, and drop the two worst.
    walks = [
        mark(0.2, net("A"), net("B"), walk="p#0", street=("Alfa", "Bravo"), length=100.0),
        mark(0.3, net("A"), net("B"), walk="p#1", street=("Alfa", "Bravo"), length=100.0),
        mark(0.2, net("C"), net("D"), walk="p#2", street=("Charlie", "Delta"), length=None),
        mark(0.7, net("C"), net("D"), walk="p#3", street=("Charlie", "Delta"), length=None),
    ]
    report = format_map_check(walks, check_map(walks), check_map(walks, by_signal=True))
    assert "placed on the right stretch             4              4" in report
    assert "mean error, of a stretch              30%            30%" in report  # las cuatro
    assert "mean error in metres                 10 m           10 m" in report  # solo dos
    assert "The distances cover the 2 of 4 answers whose stretch has a known length" in report


def test_the_map_check_names_crossings_written_both_ways_round():
    # Two outings that named one corner in opposite orders. The map holds each
    # half alone, and without this nothing about the report would say so.
    walks = [
        mark(0.2, net("A"), walk="p#0", street=("Agraciada y Freire", "Solari")),
        mark(0.3, net("A"), walk="p#1", street=("Freire y Agraciada", "Solari")),
    ]
    report = format_map_check(walks, check_map(walks), check_map(walks, by_signal=True))
    assert "written both ways round" in report
    assert '"Agraciada y Freire" and "Freire y Agraciada"' in report
    assert "  Settle on one spelling in the notebook" in report


# --- two outings, which is the only way the map proves anything ---------------


def another_day(tmp_path, monkeypatch, day, drift=0, there_and_back=False):
    """The same block walked again on another day, heard a few dB differently.

    Two runs over one street a week apart is what the map is for, and until
    there are two it can only recognise the walk it was built from. The signals
    move a little between them, the way they do.
    """
    minutes = list(range(1, 9)) if there_and_back else list(range(1, 5))
    stamps = iter(f"2026-09-{day:02d}T17:{minute:02d}:00-03:00" for minute in minutes)
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / f"paseo-{day}.jsonl")
    for step, _ in enumerate(minutes):
        along = step % 4
        log.record_scan(
            [
                ap("Panaderia", bssid="aa:bb:cc:dd:ee:01", dbm=-40 - along * 15 + drift),
                ap("Kiosco", bssid="aa:bb:cc:dd:ee:02", dbm=-85 + along * 15 + drift),
            ],
            "wlan0",
        )
    nb = tmp_path / f"libreta-{day}.txt"
    nb.write_text(
        "17:00 Alfa @ -34.9000, -56.2000\n17:05 Bravo @ -34.9000, -56.1980\n"
        + ("17:10 Alfa @ -34.9000, -56.2000\n" if there_and_back else "")
    )
    return log.path, nb


def test_a_map_of_two_outings_finds_one_of_them_from_the_other(tmp_path, monkeypatch):
    # The honest test of the whole thing: hold out everything one outing left
    # behind, and see whether the other outing can still say where you were.
    mapa = tmp_path / "mapa.jsonl"
    for day, drift in ((5, 0), (12, -3)):
        added, outing = add_to_map(mapa, *another_day(tmp_path, monkeypatch, day, drift))
        assert added == 4 and outing.startswith(f"2026-09-{day:02d}")

    fingerprints = read_map(mapa)
    assert len({one.outing for one in fingerprints}) == 2
    results = check_map(fingerprints, by_signal=True)
    assert len(results) == 8
    assert not any(held.abstained or held.wrong_stretch for held in results)

    # Every answer came from the other week's walk, since holding out one outing
    # here leaves only the other, so the report stops warning that the map is
    # recognising a walk rather than a place.
    assert not any(held.own_outing_only for held in results)
    report = format_map_check(fingerprints, check_map(fingerprints), results)
    assert "Map: 8 fingerprints, 2 walks over 1 stretches, 2 outings." in report
    assert "backed only by the outing" not in report
    assert "recognising a walk, not a place" not in report


def test_one_outing_alone_can_only_recognise_itself(tmp_path, monkeypatch):
    # The same street, walked twice on one afternoon rather than on two days.
    # There is something to hold out, it is found, and the number is worthless:
    # the map is matching that walk against itself, and says so.
    mapa = tmp_path / "mapa.jsonl"
    add_to_map(mapa, *another_day(tmp_path, monkeypatch, 5, there_and_back=True))
    fingerprints = read_map(mapa)
    results = check_map(fingerprints)
    answered = [held for held in results if not held.abstained]
    assert answered and all(held.own_outing_only for held in answered)
    report = format_map_check(fingerprints, results, check_map(fingerprints, by_signal=True))
    assert "1 outings." in report and "recognising a walk, not a place" in report


def test_the_score_belongs_to_the_street_that_won():
    # One fingerprint of a street that lost can match better than any of the
    # winner's while the winner carries more evidence. Reporting the global best
    # beside the winner's name claims a match that street never made.
    here = [
        mark(0.5, net("A"), net("B"), net("C"), street=("Bravo", "Charlie")),
        mark(0.2, net("A"), net("B"), street=("Alfa", "Bravo")),
        mark(0.3, net("A"), net("B"), street=("Alfa", "Bravo")),
        mark(0.4, net("A"), net("B"), street=("Alfa", "Bravo")),
    ]
    found = locate_scan(here, [net("A"), net("B"), net("C")])
    assert found is not None
    assert found.place.stretch == ("Alfa", "Bravo")  # tres a favor le ganan a una perfecta
    assert found.score == pytest.approx(2 / 3)  # lo que esa calle midió, no el 1.0 de la otra


def test_a_map_that_cannot_be_read_is_not_an_empty_map(tmp_path):
    # "Not on the map" is a sentence about the street. A map that exists and
    # cannot be opened has to say something about the file instead.
    missing = tmp_path / "todavia-no.jsonl"
    assert read_map(missing) == []
    locked = tmp_path / "mapa"
    locked.mkdir()  # a directory where a file was expected
    with pytest.raises(OSError):
        read_map(locked)


def test_two_cycles_in_the_same_second_are_two_fingerprints():
    # The log keeps time to the second, so two cycles can share one. Grouping
    # the placed scans on the timestamp again, after merged_scans has already
    # grouped them on the cycle, makes the second place vanish and transplants
    # its networks onto the first. The same mistake cycle was introduced to
    # end, one layer downstream.
    when = datetime(2026, 9, 5, 20, 0, 0, tzinfo=TZ)
    here, there = (
        Waypoint(datetime(2026, 9, 5, 20, minute, tzinfo=TZ), name, -34.9, -56.2)
        for minute, name in ((0, "A"), (10, "B"))
    )
    placed = [
        PlacedScan(
            LogRecord("scan", when, cycle=cycle, networks=[net(name)]),
            Position(here, there, fraction),
        )
        for cycle, fraction, name in ((1, 0.1, "A"), (2, 0.2, "B"))
    ]
    prints = fingerprints_from(Reconciliation([here, there], [], placed, 0, 0, []), "paseo")
    assert len(prints) == 2
    assert [one.place.fraction for one in prints] == [0.1, 0.2]
    assert [sorted(one.keys) for one in prints] == [[net("A").key], [net("B").key]]


def test_a_card_that_refuses_does_not_stop_the_others(monkeypatch):
    # The scan loop tolerates this while walking, so a map can be built on
    # wlan1 while wlan0 fails. Refusing to look yourself up with the same two
    # cards, because wlan0 happens to be asked first, would be strange.
    monkeypatch.setattr(fingerprint.ifpeek, "interface_rfkill", lambda card: None)

    def scan(interface=None, fresh=False):
        if interface == "wlan0":
            raise RuntimeError("daemon refused")
        return [ap("Casa")]

    monkeypatch.setattr(ifpeek, "scan_access_points", scan)
    assert [one.ssid for one in scan_now(["wlan0", "wlan1"])] == ["Casa"]
    with pytest.raises(RadioBlocked, match="wlan0: daemon refused"):
        scan_now(["wlan0"])


def test_an_unreadable_radio_switch_does_not_stop_the_other_cards(monkeypatch, capsys):
    # Not being able to read the switch is not the switch being off, and it is
    # certainly no reason to stop asking the rest. The scan loop settled this
    # the same way.
    def switch(card):
        if card == "wlan0":
            raise RuntimeError("sysfs vanished")
        return None

    monkeypatch.setattr(fingerprint.ifpeek, "interface_rfkill", switch)
    monkeypatch.setattr(
        ifpeek, "scan_access_points", lambda interface=None, fresh=False: [ap("Casa")]
    )
    assert [one.ssid for one in scan_now(["wlan0", "wlan1"])] == ["Casa"]
    assert "Could not read the radio switch of wlan0" in capsys.readouterr().out


def test_a_map_line_with_a_number_that_is_not_one_is_refused(tmp_path):
    # A map gets copied about, merged and hand-edited, and JSON carries a string
    # where a latitude belongs without complaining. Found later it is a
    # TypeError from inside an average that had no reason to doubt its input.
    path = tmp_path / "mapa.jsonl"
    path.write_text(
        '{"from": "A", "to": "B", "fraction": 0.5, "lat": "oops", "lon": -56.2, "networks": []}\n'
        '{"from": "A", "to": "B", "fraction": "medio", "networks": []}\n'
        '{"from": "A", "to": "B", "fraction": 7, "networks": []}\n'
        '{"from": "A", "to": "B", "fraction": NaN, "networks": []}\n'
        '{"from": "A", "to": "B", "fraction": 0.5, "lat": 999, "lon": -56.2, "networks": []}\n'
        '{"from": "A", "to": "B", "fraction": 0.5, "length_m": "largo", "networks": []}\n'
    )
    kept = read_map(path)
    assert len(kept) == 3  # la primera, la de lat fuera de rango, y la de length_m
    assert all(one.place.coordinates is None for one in kept)  # media coordenada no es una
    assert all(one.place.length_m is None for one in kept)


def test_a_network_whose_signal_is_not_a_number_loses_its_signal(tmp_path):
    path = tmp_path / "mapa.jsonl"
    path.write_text(
        '{"from": "A", "to": "B", "fraction": 0.5, "networks": '
        '[{"ssid": "x", "bssid": "aa", "signal_dbm": "fuerte"}]}\n'
    )
    (only,) = read_map(path)
    assert only.networks[0].signal_dbm is None and not only.networks[0].has_signal


def test_two_places_that_share_a_pair_of_names_are_not_averaged_into_one():
    # A stretch is identified by its crossing names, and names are only unique
    # if somebody kept them so, which a map fed one outing at a time cannot
    # check. The middle of Montevideo and Salto is a field.
    here = [
        Fingerprint(
            Place("A", "B", 0.5, -34.90, -56.20, 100.0), (net("A"), net("B")), "uno", "u#0"
        ),
        Fingerprint(
            Place("A", "B", 0.5, -31.40, -57.90, 100.0), (net("A"), net("B")), "dos", "d#0"
        ),
    ]
    found = locate_scan(here, [net("A"), net("B")])
    assert found is not None and found.scattered
    assert found.place.coordinates is None  # el punto inventado no sale de acá
    assert found.scattered_m == pytest.approx(420_000, rel=0.1)
    report = format_location(found, "mapa.jsonl")
    assert "420 km apart" in report and "have to be unique across a map" in report


def test_fingerprints_of_one_block_are_not_taken_for_two_places():
    here = [
        Fingerprint(Place("A", "B", f, -34.90, lon, 200.0), (net("A"),), "uno", "u#0")
        for f, lon in ((0.2, -56.200000), (0.8, -56.197811))
    ]
    found = locate_scan(here, [net("A")])
    assert found is not None and not found.scattered
    assert found.place.coordinates is not None


def test_two_walks_that_began_in_the_same_second_are_two_outings(tmp_path, monkeypatch):
    # The clock has one second of resolution, and two walks can begin inside
    # one. Named by the clock alone they were one outing: the map refused the
    # second as already added, and check_map held them out together and called
    # a walk it had never seen a place it knew. So the loop writes down which
    # walk a scan belongs to, the way it already writes down which cycle.
    when = "2026-09-05T17:05:00-03:00"
    notebook = "17:00 Alfa\n17:10 Bravo\n"
    casa, nb_a = walk_log(
        tmp_path / "a", monkeypatch, [(when, [ap("Casa")])], notebook, outing="3f9a2b10"
    )
    bar, nb_b = walk_log(
        tmp_path / "b", monkeypatch, [(when, [ap("Bar")])], notebook, outing="c41d77e2"
    )
    assert outing_name(reconcile(casa, nb_a, by_movement=False)) == f"{when}/3f9a2b10"
    assert outing_name(reconcile(bar, nb_b, by_movement=False)) == f"{when}/c41d77e2"

    mapa = tmp_path / "mapa.jsonl"
    assert add_to_map(mapa, casa, nb_a)[0] == 1
    assert add_to_map(mapa, bar, nb_b)[0] == 1
    assert add_to_map(mapa, casa, nb_a)[0] == 0  # la misma sigue siendo la misma
    assert sorted(n.ssid for one in read_map(mapa) for n in one.networks) == ["Bar", "Casa"]


def test_a_log_written_before_the_token_existed_is_still_named_by_its_clock(tmp_path, monkeypatch):
    # The same fallback merged_scans keeps for a log written before `cycle`: the
    # walk says who it is when it can, and the clock names it when it cannot.
    log, nb = walk_log(
        tmp_path,
        monkeypatch,
        [("2026-09-05T17:05:00-03:00", [ap("Casa")])],
        "17:00 Alfa\n17:10 Bravo\n",
    )
    assert outing_name(reconcile(log, nb, by_movement=False)) == "2026-09-05T17:05:00-03:00"


def test_two_anonymous_networks_are_not_a_perfect_match(monkeypatch):
    # Both sides have nothing to identify their network by, both come out as the
    # empty key, and the two empty keys agreed completely: a fresh scan of one
    # anonymous router scored 100% against a remembered, different one and the
    # map answered with a place. A coincidence of absence is not evidence.
    def hidden(freq):
        return SeenNetwork(
            ssid="", bssid=None, security=None, frequency=freq, signal_dbm=-50, signal_percent=None
        )

    remembered = Fingerprint(Place("Alfa", "Bravo", 0.5), (hidden(2412),), "vieja", "vieja#0")
    assert remembered.keys == set()
    assert similarity({"": -50.0}, remembered) == 0.0
    assert locate_scan([remembered], [hidden(5180)]) is None


def test_a_card_that_hears_two_anonymous_networks_reports_both(monkeypatch):
    # One empty key cannot stand for every anonymous network two cards heard
    # between them, so they are kept whole instead of folded onto each other.
    monkeypatch.setattr(ifpeek, "get_wifi_interfaces", lambda: ["wlan0", "wlan1"])
    monkeypatch.setattr(fingerprint.ifpeek, "interface_rfkill", lambda card: None)

    def scan(interface=None, fresh=False):
        return [
            AccessPoint(
                ssid="",
                bssid=None,
                security=None,
                frequency=2412 if interface == "wlan0" else 5180,
                signal_dbm=-50,
                signal_percent=None,
                connected=False,
            )
        ]

    monkeypatch.setattr(fingerprint.ifpeek, "scan_access_points", scan)
    found = scan_now()
    assert sorted(n.frequency for n in found) == [2412, 5180]


def test_locating_from_a_log_reads_the_last_walk_in_it(tmp_path):
    # A file can hold several walks, and the last one may have nothing usable in
    # it: a radio blocked for its whole length writes scan_failed and no scans.
    # Read across the file, that answered with where you were last week.
    log = tmp_path / "walk.jsonl"
    log.write_text(
        json.dumps(
            {
                "time": "2026-09-17T17:00:00-03:00",
                "event": "scan",
                "cycle": 1,
                "outing": "vieja111",
                "networks": [{"ssid": "Old place", "bssid": "aa:bb:cc:dd:ee:01"}],
            }
        )
        + "\n"
        + json.dumps(
            {
                "time": "2026-09-18T18:00:00-03:00",
                "event": "scan_failed",
                "outing": "nueva222",
                "reason": "radio soft blocked (rfkill)",
            }
        )
        + "\n"
    )
    with pytest.raises(NotebookError, match="no timestamped scans found"):
        scan_from_log(log)
    assert [n.ssid for n in scan_from_log(log, "vieja111")] == ["Old place"]


def test_a_match_too_weak_to_be_evidence_cannot_win_by_turning_up_four_times(tmp_path):
    # Two rules that are each right on their own. Some match has to clear the
    # floor, and the street is decided by the evidence for it added up. Together
    # they let four readings that were individually below the floor outvote the
    # one that cleared it, and the answer came back naming the losers' street,
    # scored by the best of them: "you are here, best similarity 11%", under a floor
    # of 15%. Not knowing has to survive being outnumbered.
    def net(name):
        return SeenNetwork(
            ssid=name,
            bssid=f"aa:bb:cc:dd:ee:{ord(name[0]):02x}",
            security=None,
            frequency=2412,
            signal_dbm=-50,
            signal_percent=None,
        )

    query = [net(n) for n in "PQRSTU"]
    over = Fingerprint(Place("Bravo", "Charlie", 0.5), (net("P"),), "o1", "o1#0")
    under = [
        Fingerprint(
            Place("Alfa", "Bravo", 0.5),
            (net("Q"), net("V"), net("W"), net("X")),
            "o2",
            f"o2#{index}",
        )
        for index in range(4)
    ]
    found = locate_scan([over, *under], query, floor=0.15)
    assert found is not None
    assert found.score >= 0.15
    assert found.place.stretch == ("Bravo", "Charlie")

    # And with nothing over the floor at all, the answer is still no answer.
    assert locate_scan(under, query, floor=0.15) is None


def test_a_map_takes_a_whole_outing_or_none_of_it(tmp_path, monkeypatch):
    # `add_to_map` reads a map holding any of an outing's fingerprints as
    # holding the outing, so an append that stopped half way left a walk a third
    # of the way in that the same command could never finish: run again, it
    # found the outing already there and added nothing.
    mapa = tmp_path / "mapa.jsonl"
    prints = [
        Fingerprint(Place("Alfa", "Bravo", index / 3), (net("A"),), "deadbeef", f"deadbeef#{index}")
        for index in range(3)
    ]
    assert write_map(mapa, prints[:1]) == 1  # una salida anterior, ya en el mapa

    calls = {"n": 0}
    real = fingerprint.json.dumps

    def flaky(obj, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("disk full")
        return real(obj, **kwargs)

    monkeypatch.setattr(fingerprint.json, "dumps", flaky)
    with pytest.raises(OSError, match="disk full"):
        write_map(mapa, prints)
    monkeypatch.undo()
    assert len(mapa.read_text(encoding="utf-8").splitlines()) == 1  # nada a medias
    assert write_map(mapa, prints) == 3  # y se puede volver a intentar


def test_a_map_written_to_with_nothing_is_left_alone(tmp_path):
    mapa = tmp_path / "mapa.jsonl"
    assert write_map(mapa, []) == 0
    assert not mapa.exists()


def test_a_map_whose_last_line_lost_its_newline_does_not_swallow_the_next(tmp_path):
    mapa = tmp_path / "mapa.jsonl"
    write_map(mapa, [Fingerprint(Place("Alfa", "Bravo", 0.5), (net("A"),), "o1", "o1#0")])
    mapa.write_text(mapa.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    write_map(mapa, [Fingerprint(Place("Alfa", "Bravo", 0.6), (net("B"),), "o2", "o2#0")])
    assert len(read_map(mapa)) == 2


def test_the_map_keeps_the_permissions_it_had(tmp_path):
    # The atomic write fixed a transaction and broke an inode: a file written
    # beside the map is born with whatever the umask says, so moving it into
    # place reopened a map somebody had deliberately shut. This one holds the
    # names and hardware addresses of the neighbours' routers, keyed to the
    # corners they sit near, so who can read it is not a detail.
    mapa = tmp_path / "mapa.jsonl"
    write_map(mapa, [Fingerprint(Place("A", "B", 0.5), (net("A"),), "o1", "o1#0")])
    assert stat.S_IMODE(mapa.stat().st_mode) == 0o600  # y uno nuevo nace cerrado

    mapa.chmod(0o640)
    write_map(mapa, [Fingerprint(Place("A", "B", 0.6), (net("B"),), "o2", "o2#0")])
    assert stat.S_IMODE(mapa.stat().st_mode) == 0o640
    assert not (tmp_path / "mapa.jsonl.new").exists()


def test_a_bssid_is_the_same_access_point_in_either_case(tmp_path):
    # Hexadecimal written out has two spellings of every letter, and identity
    # here is one string compared to another. A map holding AA:BB:... and a scan
    # reporting aa:bb:... are one access point, and scored nothing in common.
    mapa = tmp_path / "mapa.jsonl"
    mapa.write_text(
        json.dumps(
            {
                "from": "Alfa",
                "to": "Bravo",
                "fraction": 0.5,
                "networks": [{"ssid": "Casa", "bssid": "AA:BB:CC:DD:EE:FF", "signal_dbm": -50}],
            }
        )
        + "\n"
    )
    (remembered,) = read_map(mapa)
    assert remembered.keys == {"aa:bb:cc:dd:ee:ff"}

    fresh = SeenNetwork(
        ssid="Casa",
        bssid="aa:bb:cc:dd:ee:ff",
        security=None,
        frequency=2412,
        signal_dbm=-50,
        signal_percent=None,
    )
    assert similarity({fresh.key: fresh.strength}, remembered) == 1.0
    assert locate_scan([remembered], [fresh]) is not None


def test_a_map_can_be_counted_without_being_checked(tmp_path):
    # `format_map_check` answers a one-walk map with a sentence saying there is
    # nothing to hold out, and never reaches the line with the numbers on it.
    # Somebody looking at a new map wants those numbers most of all.
    walks = two_walks()
    counted = map_summary(walks)
    assert counted.fingerprints == 2
    assert counted.walks == 2 and counted.stretches == 1
    assert counted.outings == ("paseo",)
    assert counted.describe() == "Map: 2 fingerprints, 2 walks over 1 stretches, 1 outings."
    assert counted.describe() in format_map_check(walks, check_map(walks), check_map(walks, True))

    assert map_summary([]) == MapSummary(0, 0, 0, ())


def test_the_map_is_never_written_through_a_link_somebody_left_waiting(tmp_path):
    # The temporary was named after the map, so anybody who could write in that
    # directory could leave a symlink under the name it was about to use. The
    # next outing poured the map through the link into whatever it pointed at,
    # and then left the map itself as that link.
    victim = tmp_path / "victim.txt"
    victim.write_text("something somebody cares about\n")
    mapa = tmp_path / "mapa.jsonl"
    (tmp_path / "mapa.jsonl.new").symlink_to(victim)

    assert write_map(mapa, [Fingerprint(Place("A", "B", 0.5), (net("A"),), "o1", "o1#0")]) == 1
    assert victim.read_text() == "something somebody cares about\n"
    assert not mapa.is_symlink()
    assert len(read_map(mapa)) == 1


def test_a_map_that_could_not_be_moved_into_place_leaves_nothing_behind(tmp_path, monkeypatch):
    mapa = tmp_path / "mapa.jsonl"

    def refuse(source, target):
        raise OSError("cross-device link")

    monkeypatch.setattr(fingerprint.os, "replace", refuse)
    with pytest.raises(OSError, match="cross-device link"):
        write_map(mapa, [Fingerprint(Place("A", "B", 0.5), (net("A"),), "o1", "o1#0")])
    # The lock stays, which is what a lock is. Nothing half-written does.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["mapa.jsonl.lock"]


def test_two_runs_adding_to_the_map_at_once_do_not_lose_one_of_them(tmp_path):
    # Adding an outing is a read, a change and a write. Two of those at once
    # both read the map as it was, both wrote their own outing onto that, and
    # the second to finish left the first one's walk nowhere: no error, no
    # warning, a map quietly missing an outing somebody walked.
    mapa = tmp_path / "mapa.jsonl"
    write_map(mapa, [Fingerprint(Place("A", "B", 0.1), (net("A"),), "base", "base#0")])
    together = threading.Barrier(4)

    def add(token):
        together.wait()
        write_map(mapa, [Fingerprint(Place("A", "B", 0.5), (net("B"),), token, f"{token}#0")])

    hands = [threading.Thread(target=add, args=(token,)) for token in "WXYZ"]
    for hand in hands:
        hand.start()
    for hand in hands:
        hand.join()
    assert sorted({one.outing for one in read_map(mapa)}) == ["W", "X", "Y", "Z", "base"]


def test_one_outing_added_twice_at_once_still_goes_on_once(tmp_path):
    # The other half of the same window: both runs find the outing absent and
    # both put it on, and a doubled outing pulls every answer towards itself.
    mapa = tmp_path / "mapa.jsonl"
    together = threading.Barrier(2)

    def add():
        together.wait()
        write_map(
            mapa,
            [Fingerprint(Place("A", "B", 0.5), (net("A"),), "once", "once#0")],
            unless_outing="once",
        )

    hands = [threading.Thread(target=add) for _ in range(2)]
    for hand in hands:
        hand.start()
    for hand in hands:
        hand.join()
    assert len(read_map(mapa)) == 1


def test_the_rename_is_on_disk_and_not_only_the_file_it_renamed(tmp_path, monkeypatch):
    # The contents are flushed before the move. The move itself lives in the
    # directory, and until that is on disk too a power cut can leave the name
    # pointing at the file it used to.
    synced = []
    real = fingerprint.os.fsync

    def watched(handle):
        synced.append(os.fstat(handle).st_mode)
        return real(handle)

    monkeypatch.setattr(fingerprint.os, "fsync", watched)
    write_map(tmp_path / "mapa.jsonl", [Fingerprint(Place("A", "B", 0.5), (net("A"),), "o", "w")])
    assert len(synced) == 2
    assert stat.S_ISREG(synced[0]) and stat.S_ISDIR(synced[1])
