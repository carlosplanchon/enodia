"""Tests for reading logs back and reconciling them with a notebook of timed crossings."""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from ifpeek import AccessPoint

from enodia import cli, netlog
from enodia.netlog import LogRecord, NetworkLog, SeenNetwork, parse_timestamp, read_log
from enodia.reconcile import (
    NotebookError,
    Position,
    UntimedNotebook,
    Waypoint,
    _in_view,
    _step,
    check_pace,
    check_passes,
    confusable_crossings,
    distance_metres,
    format_confusable,
    format_pace_check,
    format_pass_check,
    format_report,
    locate,
    merged_scans,
    network_turnover,
    read_notebook,
    reconcile,
    route_stretches,
    signal_weight,
)

TZ = timezone(timedelta(hours=-3))


def ap(ssid, bssid="aa:bb:cc:dd:ee:01", dbm=-60, percent=70, security="psk", frequency=2412):
    return AccessPoint(
        ssid=ssid,
        bssid=bssid,
        frequency=frequency,
        signal_dbm=dbm,
        signal_percent=percent,
        security=security,
        connected=False,
    )


def write_log(path, monkeypatch, scans):
    """scans: list of (iso timestamp, [AccessPoint, ...]) written as 'Actual networks' blocks."""
    stamps = iter(stamp for stamp, _ in scans)
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(path)
    for _, networks in scans:
        log.record_scan(networks)
    return path


# --- parsing ------------------------------------------------------------------


def test_parse_timestamp():
    assert parse_timestamp("2026-09-05T00:58:39-03:00") == datetime(
        2026, 9, 5, 0, 58, 39, tzinfo=TZ
    )
    assert parse_timestamp("Home") is None
    # The `date`-style stamps of the first versions are no longer understood.
    assert parse_timestamp("Sun Jun  3 16:33:16 -0300 2012") is None


def test_a_network_without_a_bssid_is_keyed_by_name(tmp_path, monkeypatch):
    path = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            (
                "2026-09-05T17:45:00-03:00",
                [ap("Home", bssid=None, security="wpa", dbm=-61, percent=70)],
            ),
        ],
    )
    n = read_log(path)[0].networks[0]
    assert (n.ssid, n.bssid, n.security, n.channel, n.signal_dbm) == ("Home", None, "wpa", 1, -61)
    assert n.key == "Home" and not n.open
    assert n.signal_percent == 70
    assert n.strength == -61


def test_strength_falls_back_to_the_percentage_when_there_is_no_dbm(tmp_path, monkeypatch):
    path = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            (
                "2026-09-05T17:45:00-03:00",
                [
                    ap("Cafe", bssid="aa:bb:cc:dd:ee:03", security="open", dbm=-42),
                    ap("Odd", bssid=None, dbm=None, frequency=None, percent=62),
                ],
            ),
        ],
    )
    cafe, odd = read_log(path)[0].networks
    assert cafe.key == "aa:bb:cc:dd:ee:03" and cafe.open
    assert (odd.bssid, odd.channel, odd.signal_dbm) == (None, None, None)
    assert odd.strength == pytest.approx(-100 + 60 * 0.62)


def test_a_log_in_the_old_text_format_is_no_longer_read(tmp_path):
    # The text format is gone from both ends: nothing writes it, nothing reads it.
    path = tmp_path / "networks.txt"
    path.write_text(
        "-" * 90 + "\n - - - Actual networks - - -\n2026-09-05T16:33:16-03:00\n"
        "- Address: aa:bb:cc:dd:ee:01, ESSID: Home, Encryption: WPA, "
        "Channel: 1, Signal: -61, Quality: 49/70\n" + "-" * 90 + "\n"
    )
    assert read_log(path) == []


def test_read_log_current_format(tmp_path, monkeypatch):
    path = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            (
                "2026-09-05T17:45:00-03:00",
                [ap("Home"), ap("Cafe", bssid=None, security="open", dbm=None, frequency=None)],
            ),
        ],
    )
    blocks = read_log(path)
    assert len(blocks) == 1 and blocks[0].is_scan
    assert blocks[0].time == datetime(2026, 9, 5, 17, 45, tzinfo=TZ)
    home, cafe = blocks[0].networks
    assert (home.ssid, home.bssid, home.channel, home.signal_dbm, home.security) == (
        "Home",
        "aa:bb:cc:dd:ee:01",
        1,
        -60,
        "psk",
    )
    assert (cafe.bssid, cafe.channel, cafe.signal_dbm, cafe.open) == (None, None, None, True)


# --- notebook -------------------------------------------------------------------


def test_read_notebook(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text(
        "# domingo\n"
        "17:52:10 Agraciada y Freire\n"
        "17:58 Agraciada y San Fructuoso   # sin segundos\n"
        "18:05:30 Plaza Vidiella @ -34.8612, -56.2072\n"
        "\n"
        "date 2018-06-10\n"
        "03:25:00 McDonald's Paso Molino\n"
        "2018-06-11 01:00:00 Con fecha propia\n"
    )
    w = read_notebook(nb, datetime(2018, 6, 3).date(), TZ)
    assert [x.name for x in w] == [
        "Agraciada y Freire",
        "Agraciada y San Fructuoso",
        "Plaza Vidiella",
        "McDonald's Paso Molino",
        "Con fecha propia",
    ]
    assert w[0].time == datetime(2018, 6, 3, 17, 52, 10, tzinfo=TZ)
    assert w[1].time == datetime(2018, 6, 3, 17, 58, 0, tzinfo=TZ)
    assert (w[2].lat, w[2].lon) == (-34.8612, -56.2072) and w[2].has_coordinates
    assert not w[0].has_coordinates
    assert w[3].time == datetime(2018, 6, 10, 3, 25, tzinfo=TZ)
    assert w[4].time == datetime(2018, 6, 11, 1, 0, tzinfo=TZ)


def test_read_notebook_midnight_rollover(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("23:50 A\n00:10 B\n00:20 C\n")
    w = read_notebook(nb, datetime(2018, 6, 9).date(), TZ)
    assert w[0].time.day == 9
    assert w[1].time == datetime(2018, 6, 10, 0, 10, tzinfo=TZ)
    assert w[2].time == datetime(2018, 6, 10, 0, 20, tzinfo=TZ)


def test_read_notebook_errors(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("17:52:10 A\nesto no es una hora\n")
    with pytest.raises(NotebookError, match=re.escape("bad.txt:2")):
        read_notebook(bad, datetime(2018, 6, 3).date(), TZ)
    short = tmp_path / "short.txt"
    short.write_text("17:52:10 A\n")
    with pytest.raises(NotebookError, match="at least two"):
        read_notebook(short, datetime(2018, 6, 3).date(), TZ)


def test_a_crossing_that_cannot_be_on_the_earth_is_refused_not_believed(tmp_path):
    # The notebook is the ground truth every other check measures against, so a
    # latitude of 999 fails nowhere: it makes distances and geometry that are
    # absurd and that look exactly as legitimate as the rest of the report.
    # And somebody who typed @ meant to give a coordinate, so saying the line is
    # wrong beats quietly reading it as a crossing with no coordinates at all.
    bad = tmp_path / "raro.txt"
    bad.write_text("17:00 A @ 999, -56.2\n17:10 B @ -34.9, -56.2\n")
    with pytest.raises(NotebookError, match=re.escape("raro.txt:1: 999 is not a latitude")):
        read_notebook(bad, datetime(2026, 9, 17).date(), TZ)

    bad.write_text("17:00 A @ -34.9, -56.2\n17:10 B @ -34.9, 999\n")
    with pytest.raises(NotebookError, match=re.escape("raro.txt:2: 999 is not a longitude")):
        read_notebook(bad, datetime(2026, 9, 17).date(), TZ)

    # A run of four hundred digits matches the pattern and is an infinity.
    bad.write_text("17:00 A @ " + "9" * 400 + ", -56.2\n17:10 B @ -34.9, -56.2\n")
    with pytest.raises(NotebookError, match="is not a latitude"):
        read_notebook(bad, datetime(2026, 9, 17).date(), TZ)

    good = tmp_path / "bien.txt"
    good.write_text("17:00 A @ -90, -180\n17:10 B @ 90, 180\n")
    assert [
        point.coordinates for point in read_notebook(good, datetime(2026, 9, 17).date(), TZ)
    ] == [
        (-90.0, -180.0),
        (90.0, 180.0),
    ]


def test_a_date_directive_that_is_not_a_date_says_so(tmp_path):
    # The 31st of February matches the shape of a date and is not one, and this
    # was the one conversion of the parser left outside the handler that turns
    # an impossible time into a line number and a reason.
    bad = tmp_path / "feb.txt"
    bad.write_text("date 2026-02-31\n17:00 A\n17:10 B\n")
    with pytest.raises(
        NotebookError, match=re.escape("feb.txt:1: 'date 2026-02-31' is not a date")
    ):
        read_notebook(bad, datetime(2026, 9, 17).date(), TZ)


# --- placing --------------------------------------------------------------------


def wp(hh, mm, name, lat=None, lon=None):
    return Waypoint(datetime(2018, 6, 3, hh, mm, tzinfo=TZ), name, lat, lon)


def test_locate_and_describe():
    a, b, c = wp(17, 0, "A", -34.0, -56.0), wp(17, 10, "B", -34.1, -56.2), wp(17, 20, "C")
    pos = locate(datetime(2018, 6, 3, 17, 5, tzinfo=TZ), [a, b, c])
    assert (pos.start, pos.end, pos.fraction) == (a, b, 0.5)
    assert pos.lat == pytest.approx(-34.05) and pos.lon == pytest.approx(-56.1)
    assert pos.describe() == 'between "A" and "B", 50% of the way'
    assert locate(a.time, [a, b, c]).describe() == 'at "A"'
    assert (
        locate(b.time, [a, b, c]).fraction == 0.0
    )  # the crossing itself belongs to the next segment
    assert locate(c.time, [a, b, c]).describe() == 'at "C"'
    later = locate(datetime(2018, 6, 3, 17, 15, tzinfo=TZ), [a, b, c])
    assert later.lat is None and later.describe() == 'between "B" and "C", 50% of the way'
    assert locate(datetime(2018, 6, 3, 16, 59, tzinfo=TZ), [a, b, c]) is None
    assert locate(datetime(2018, 6, 3, 17, 21, tzinfo=TZ), [a, b, c]) is None
    assert Position(a, a, 0.0).describe() == 'at "A"'
    same = locate(a.time, [a, wp(17, 0, "A again")])
    assert same.fraction == 0.0
    stayed = locate(datetime(2018, 6, 3, 17, 5, tzinfo=TZ), [wp(17, 0, "Bar"), wp(17, 10, "Bar")])
    assert stayed.fraction == 0.5 and stayed.describe() == 'at "Bar"'


def test_reconcile_end_to_end(tmp_path, monkeypatch):
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:45:00-03:00", [ap("X", dbm=-70)]),
            (
                "2026-09-05T17:50:00-03:00",
                [ap("X", dbm=-50), ap("Y", bssid="aa:bb:cc:dd:ee:02", dbm=-80)],
            ),
            ("2026-09-05T17:55:00-03:00", [ap("X", dbm=-65)]),
            (
                "2026-09-05T18:00:00-03:00",
                [ap("Open", bssid=None, security="open", dbm=None, percent=40, frequency=None)],
            ),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45:00 A\n17:55:00 B @ -34.0, -56.0\n18:05:00 C @ -34.2, -56.4\n")

    result = reconcile(log, nb)
    assert (len(result.placed), result.before, result.after) == (4, 0, 0)
    by_key = {item.network.key: item for item in result.networks}
    x = by_key["aa:bb:cc:dd:ee:01"]
    assert (x.seen, x.network.signal_dbm, x.position.fraction) == (3, -50, 0.5)
    assert x.position.describe() == 'between "A" and "B", 50% of the way'
    assert x.first_seen.minute == 45 and x.last_seen.minute == 55 and x.best_seen.minute == 50
    opened = by_key["Open"]
    assert opened.network.open and opened.position.fraction == 0.5
    assert opened.position.lat == pytest.approx(-34.1) and opened.position.lon == pytest.approx(
        -56.2
    )
    assert [item.network.ssid for item in result.networks] == ["X", "Y", "Open"]  # along the route

    report = format_report(result, with_scans=True)
    assert "Crossings: 3, from 17:45:00 A to 18:05:00 C" in report
    assert "Scans: 4 placed, 0 before the first crossing, 0 after the last" in report
    assert "Networks placed: 3 (1 open)" in report
    assert '17:45:00    1 networks  at "A"' in report
    assert (
        '  18:00:00  Open  40% signal, seen 1x  between "B" and "C", 50% of the way  '
        "AP near [-34.10000, -56.20000] +/-0 m (barely pinned down, few sightings)  OPEN"
    ) in report
    assert "  17:50:00  X aa:bb:cc:dd:ee:01  -50 dBm, seen 3x" in report

    out = tmp_path / "out.csv"
    result.write_csv(out)
    rows = out.read_text().splitlines()
    assert rows[0] == (
        "ssid,bssid,security,frequency_mhz,channel,best_signal_dbm,best_signal_percent,"
        "best_seen,seen,connected,first_seen,last_seen,from,to,fraction,lat,lon,"
        "estimated_lat,estimated_lon,spread_m,"
        "estimated_from,estimated_to,estimated_fraction,estimated_spread,barely_pinned"
    )
    assert rows[1].startswith("X,aa:bb:cc:dd:ee:01,psk,2412,1,-50,")
    assert ",2026-09-05T17:50:00-03:00,3," in rows[1]  # strongest at 17:50, seen three times
    assert "2412" in rows[1]  # la frecuencia cruda, no solo el canal derivado
    assert rows[3].startswith("Open,,open,,,,40,2026-09-05T18:00:00-03:00,1,"), rows[3]
    assert ",B,C,0.500,-34.100000,-56.200000,-34.100000,-56.200000,0," in rows[3], rows[3]
    # Un solo avistamiento: el mismo sitio sobre el recorrido, y sin fijar.
    assert rows[3].endswith(",B,C,0.500,0.000,yes"), rows[3]


def test_reconcile_scans_outside_the_notebook(tmp_path, monkeypatch):
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:40:00-03:00", [ap("X")]),
            ("2026-09-05T17:50:00-03:00", [ap("X")]),
            ("2026-09-05T18:10:00-03:00", [ap("X")]),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45 A\n17:55 B\n")
    result = reconcile(log, nb)
    assert (len(result.placed), result.before, result.after) == (1, 1, 1)


def test_reconcile_without_scans(tmp_path):
    log = tmp_path / "empty.jsonl"
    log.write_text('{"time": "2026-09-05T17:40:00-03:00", "event": "disconnected"}\n')
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45 A\n17:55 B\n")
    with pytest.raises(NotebookError, match="no timestamped scans"):
        reconcile(log, nb)


# --- CLI ------------------------------------------------------------------------


def test_cli_reconcile(tmp_path, monkeypatch, capsys):
    log = write_log(
        tmp_path / "networks.jsonl", monkeypatch, [("2026-09-05T17:50:00-03:00", [ap("X")])]
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45 A\n17:55 B\n")
    out = tmp_path / "out.csv"
    assert cli.main(["--reconcile", str(log), str(nb), "--csv", str(out), "--scans"]) == 0
    printed = capsys.readouterr().out
    assert (
        "Crossings: 2" in printed
        and "Scans along the route:" in printed
        and f"CSV written to {out}" in printed
    )
    assert out.exists()


def test_cli_reconcile_error(tmp_path, capsys):
    log = tmp_path / "networks.jsonl"
    log.write_text("")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45 A\n17:55 B\n")
    assert cli.main(["--reconcile", str(log), str(nb)]) == 1
    assert "error:" in capsys.readouterr().err
    assert cli.main(["--reconcile", str(tmp_path / "missing.txt"), str(nb)]) == 1


def test_two_interfaces_seeing_one_network_count_as_one_sighting(tmp_path, monkeypatch):
    # Watching wlan0 and wlan1 writes two blocks at the same instant. A network
    # both of them see was seen once, from one place.
    stamps = iter(["2026-09-05T17:50:00-03:00"] * 2 + ["2026-09-05T17:52:00-03:00"] * 2)
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "networks.jsonl")
    for _ in range(2):
        log.record_scan([ap("X")], "wlan0")
        log.record_scan([ap("X")], "wlan1")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45 A\n17:55 B\n")

    result = reconcile(tmp_path / "networks.jsonl", nb)
    # Two blocks at one instant are one look at one place from one pair of feet,
    # so they are folded into one before anything is placed or paced.
    assert len(result.placed) == 2
    assert [one.scan.interface for one in result.placed] == [None, None]
    assert result.networks[0].seen == 2  # vista en dos momentos, no en cuatro


def test_the_network_you_were_on_is_marked_in_the_report(tmp_path, monkeypatch):
    connected_ap = ap("Home")._replace(connected=True)
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:45:00-03:00", [connected_ap, ap("Vecino", bssid="aa:bb:cc:dd:ee:02")]),
            # Más tarde ya no es la nuestra, pero lo fue: el informe lo recuerda.
            ("2026-09-05T17:50:00-03:00", [ap("Home"), ap("Vecino", bssid="aa:bb:cc:dd:ee:02")]),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45 A\n17:55 B\n")

    result = reconcile(log, nb)
    marked = {item.network.ssid: item.connected for item in result.networks}
    assert marked == {"Home": True, "Vecino": False}
    report = format_report(result)
    assert "Home aa:bb:cc:dd:ee:01" in report and "CONNECTED" in report
    assert "Vecino" in report and report.count("CONNECTED") == 1


# --- pace from the networks themselves ------------------------------------------


def test_network_turnover_measures_how_much_the_view_changed():
    assert network_turnover({"a", "b"}, {"a", "b"}) == 0.0  # quieto
    assert network_turnover({"a", "b"}, {"c", "d"}) == 1.0  # nada en común
    assert network_turnover({"a", "b"}, {"b", "c"}) == pytest.approx(1 - 1 / 3)
    assert network_turnover(set(), set()) == 0.0  # sin datos, sin movimiento


def seen(*names):
    """Scanned networks, one BSSID per name: the key is the BSSID, so two calls
    naming the same networks must produce the same keys and no others."""
    return [ap(name, bssid=f"aa:bb:cc:dd:ee:{ord(name[0]):02x}") for name in names]


def test_a_stop_stays_a_stop_instead_of_being_smeared(tmp_path, monkeypatch):
    # Four scans two minutes apart. The first three see exactly the same
    # networks: the operator was standing still. On the clock they would be
    # spread evenly down the block; by movement they pile up where the stop was.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", seen("A", "B")),
            ("2026-09-05T17:04:00-03:00", seen("A", "B")),
            ("2026-09-05T17:06:00-03:00", seen("A", "B")),
            ("2026-09-05T17:08:00-03:00", seen("C", "D")),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Start\n17:10 End\n")

    by_clock = [s.position.fraction for s in reconcile(log, nb, by_movement=False).placed]
    by_movement = [s.position.fraction for s in reconcile(log, nb).placed]

    assert by_clock == pytest.approx([0.2, 0.4, 0.6, 0.8])
    assert by_movement == pytest.approx([0.2, 0.2, 0.2, 0.8])


def test_a_steady_pace_lands_where_the_clock_would(tmp_path, monkeypatch):
    # When the view turns over evenly, reading the pace agrees with the clock:
    # the two only diverge where the pace was not steady.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", seen("A", "B")),
            ("2026-09-05T17:04:00-03:00", seen("B", "C")),
            ("2026-09-05T17:06:00-03:00", seen("C", "D")),
            ("2026-09-05T17:08:00-03:00", seen("D", "E")),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Start\n17:10 End\n")

    by_clock = [s.position.fraction for s in reconcile(log, nb, by_movement=False).placed]
    by_movement = [s.position.fraction for s in reconcile(log, nb).placed]
    assert by_movement == pytest.approx(by_clock)


def test_with_nothing_to_go_on_it_falls_back_to_the_clock(tmp_path, monkeypatch):
    # One scan in the stretch, and a stretch where the view never changes at
    # all: neither says anything about pace, so the clock decides.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", seen("A")),
            ("2026-09-05T17:14:00-03:00", seen("B")),
            ("2026-09-05T17:16:00-03:00", seen("B")),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:10 B\n17:20 C\n")
    placed = reconcile(log, nb).placed
    assert [s.position.fraction for s in placed] == pytest.approx([0.2, 0.4, 0.6])


def test_a_hole_in_the_log_is_skipped_and_the_scans_around_it_measure_the_pace(tmp_path):
    # A cycle the radio could not scan is a `scan_failed` record, not a scan:
    # the pace is read from the scans on either side of it. Here the first two
    # scans see the same networks (a stop) and the third all new ones (a move).
    log = tmp_path / "networks.jsonl"
    log.write_text(
        '{"time": "2026-09-05T17:02:00-03:00", "event": "scan", "networks": '
        '[{"ssid": "A", "bssid": "aa:bb:cc:dd:ee:01"}, '
        '{"ssid": "B", "bssid": "aa:bb:cc:dd:ee:02"}]}\n'
        '{"time": "2026-09-05T17:04:00-03:00", "event": "scan", "networks": '
        '[{"ssid": "A", "bssid": "aa:bb:cc:dd:ee:01"}, '
        '{"ssid": "B", "bssid": "aa:bb:cc:dd:ee:02"}]}\n'
        '{"time": "2026-09-05T17:05:00-03:00", "event": "scan_failed", "interface": "wlan0", '
        '"reason": "radio soft blocked (rfkill)"}\n'
        '{"time": "2026-09-05T17:06:00-03:00", "event": "scan", "networks": '
        '[{"ssid": "C", "bssid": "aa:bb:cc:dd:ee:03"}, '
        '{"ssid": "D", "bssid": "aa:bb:cc:dd:ee:04"}]}\n'
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Start\n17:10 End\n")
    placed = reconcile(log, nb).placed
    assert len(placed) == 3
    # The stop stays a stop: the second scan sits where the first did. By the
    # clock it would have been 0.4.
    assert [s.position.fraction for s in placed] == pytest.approx([0.2, 0.2, 0.6])


def test_a_hole_in_the_scans_is_filled_at_the_pace_of_the_rest_of_the_stretch(
    tmp_path, monkeypatch
):
    # Six scans five seconds apart, each seeing a brand new set, except a hole
    # of thirty seconds after the third: the daemon refused for a while. The
    # turnover across the hole is 1.0, exactly what a five-second step reads,
    # so the hole used to count as one step and the three scans after it were
    # placed too early, at 0.6, 0.8 and 1.0. A step longer than three of the
    # usual ones is a hole, and a hole is filled at the pace of the rest.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            (f"2026-09-05T17:00:{second:02d}-03:00", seen(name))
            for second, name in ((0, "A"), (5, "B"), (10, "C"), (40, "D"), (45, "E"), (50, "F"))
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00:00 Start\n17:00:50 End\n")
    placed = reconcile(log, nb).placed
    assert [s.position.fraction for s in placed] == pytest.approx([0.0, 0.1, 0.2, 0.8, 0.9, 1.0])


def test_distance_between_two_points():
    # A tenth of a degree of latitude is about 11 km anywhere.
    assert distance_metres(-34.90, -56.19, -34.80, -56.19) == pytest.approx(11_119, rel=0.01)
    assert distance_metres(-34.90, -56.19, -34.90, -56.19) == 0.0


def test_a_lower_path_loss_exponent_pulls_the_estimate_to_the_strongest_sighting(
    tmp_path, monkeypatch
):
    # One network heard loud near the start and faint near the end. At the
    # default exponent the estimate sits well along the block; at 1, weighing
    # by received power, it all but collapses onto the loud sighting, which is
    # the trade the flag exists to measure. The report says which was used
    # only when it is not the default.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", [ap("A", dbm=-40)]),
            ("2026-09-05T17:08:00-03:00", [ap("A", dbm=-80)]),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Start @ -34.90, -56.190\n17:10 End @ -34.90, -56.170\n")
    default = reconcile(log, nb)
    sharp = reconcile(log, nb, path_loss=1.0)
    loud_lon = default.placed[0].position.lon
    assert abs(sharp.networks[0].estimate.lon - loud_lon) < abs(
        default.networks[0].estimate.lon - loud_lon
    )
    assert default.path_loss == 3.0 and sharp.path_loss == 1.0
    assert "path loss exponent" not in format_report(default)
    assert "Signal weighed with a path loss exponent of 1, not the default 3" in format_report(
        sharp
    )
    assert check_passes(log, nb, path_loss=1.0) == []  # nothing walked twice, but it runs


def test_check_pace_holds_out_a_crossing_and_measures_both_methods(tmp_path, monkeypatch):
    # The operator stood near the first crossing for six minutes, then walked
    # fast. The middle crossing is a fifth of the way along, not halfway, so
    # the clock misplaces it and the networks do not.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", seen("A", "B")),
            ("2026-09-05T17:04:00-03:00", seen("A", "B")),
            ("2026-09-05T17:05:00-03:00", seen("A", "B")),
            ("2026-09-05T17:08:00-03:00", seen("C", "D")),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text(
        "17:00 Start @ -34.90, -56.190\n"
        "17:05 Middle @ -34.90, -56.186\n"  # un quinto del camino
        "17:10 End @ -34.90, -56.170\n"
    )

    checks = check_pace(log, nb)
    assert len(checks) == 1
    held = checks[0]
    assert held.waypoint.name == "Middle"
    assert held.by_movement == pytest.approx(0, abs=1)  # lo clava
    assert held.by_time > 500  # el reloj lo manda media cuadra más allá
    assert held.better

    report = format_pace_check(checks)
    assert "Middle" in report and "mean error" in report
    assert "Reading the pace wins by" in report


def test_check_pace_measures_along_the_walk_where_the_route_turns(tmp_path, monkeypatch):
    # An L: east for a block, then south for a block, at a steady pace, with
    # a scan taken at the corner at the moment the notebook says. Both methods
    # put that scan halfway, which is where the corner is along the walk, so
    # both are right. Measured in a straight line they were sixty metres off,
    # because without the corner the two legs reconcile as one chord.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:30-03:00", seen("A", "B")),
            ("2026-09-05T17:05:00-03:00", seen("B", "C")),
            ("2026-09-05T17:07:30-03:00", seen("C", "D")),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text(
        "17:00 Start @ -34.90000, -56.1900\n"
        "17:05 Corner @ -34.90000, -56.1890\n"  # 91 m east
        "17:10 End @ -34.90082, -56.1890\n"  # 91 m south
    )
    (held,) = check_pace(log, nb)
    assert held.waypoint.name == "Corner"
    assert held.by_movement == pytest.approx(0, abs=1) and held.by_time == pytest.approx(0, abs=1)
    chord_middle = (-34.90041, -56.1895)
    assert distance_metres(*chord_middle, -34.90000, -56.1890) > 60  # what a straight line said
    assert "how far along the walk" in format_pace_check([held])


def test_check_pace_has_nothing_to_measure_when_the_crossings_share_a_point(tmp_path, monkeypatch):
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [("2026-09-05T17:05:00-03:00", seen("A"))],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A @ -34.90, -56.19\n17:05 B @ -34.90, -56.19\n17:10 C @ -34.90, -56.19\n")
    assert check_pace(log, nb) == []


def test_check_pace_needs_coordinates(tmp_path, monkeypatch):
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", seen("A")),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:05 B\n17:10 C\n")
    assert check_pace(log, nb) == []
    assert "needs coordinates" in format_pace_check([])


def test_check_pace_reports_when_the_clock_wins(tmp_path, monkeypatch):
    # A steady walk: both methods agree, so neither wins and the report says so.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", seen("A", "B")),
            ("2026-09-05T17:05:00-03:00", seen("B", "C")),
            ("2026-09-05T17:08:00-03:00", seen("C", "D")),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text(
        "17:00 Start @ -34.90, -56.190\n"
        "17:05 Middle @ -34.90, -56.180\n"
        "17:10 End @ -34.90, -56.170\n"
    )
    report = format_pace_check(check_pace(log, nb))
    assert "Nothing to choose between them" in report


def test_check_pace_without_scans(tmp_path):
    log = tmp_path / "empty.jsonl"
    log.write_text("")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A @ -34.9, -56.1\n17:10 B @ -34.9, -56.2\n")
    with pytest.raises(NotebookError, match="no timestamped scans"):
        check_pace(log, nb)


def test_check_pace_skips_a_crossing_it_cannot_place(tmp_path, monkeypatch):
    # The only scan falls before the notebook begins, so with the middle
    # crossing held out there is nothing to measure against.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T16:50:00-03:00", seen("A")),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text(
        "17:00 Start @ -34.90, -56.190\n"
        "17:05 Middle @ -34.90, -56.186\n"
        "17:10 End @ -34.90, -56.170\n"
    )
    assert check_pace(log, nb) == []


# --- where the access point itself stands ----------------------------------------


def test_signal_weight_follows_the_path_loss_model():
    # A sighting 30 dB stronger is ten times closer at n=3, a thousand at n=1.
    assert signal_weight(-40) / signal_weight(-70) == pytest.approx(10)
    assert signal_weight(-40, exponent=1) / signal_weight(-70, exponent=1) == pytest.approx(1000)
    assert signal_weight(-40) > signal_weight(-41)


def walked(tmp_path, monkeypatch, sightings):
    """A straight walk east, seeing one network at the given strengths."""
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    stamps = iter(f"2026-09-05T17:{minute:02d}:00-03:00" for minute, _ in sightings)
    log = NetworkLog(tmp_path / "networks.jsonl")
    for _, dbm in sightings:
        log.record_scan([ap("Target", bssid="aa:bb:cc:dd:ee:aa", dbm=dbm)], "wlan0")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 West @ -34.9000, -56.2000\n17:20 East @ -34.9000, -56.1800\n")
    return log.path, nb


def test_the_access_point_is_placed_from_every_sighting_not_just_the_best(tmp_path, monkeypatch):
    # Heard weakly at both ends of the block and strongly in the middle: it
    # stands in the middle, and the middle is what the estimate says.
    log, nb = walked(tmp_path, monkeypatch, [(2, -85), (10, -45), (18, -85)])
    item = reconcile(log, nb).networks[0]
    assert item.estimate is not None
    assert item.estimate.sightings == 3
    assert item.estimate.lon == pytest.approx(-56.1900, abs=0.0002)  # la mitad
    assert not item.estimate.few_sightings


def test_the_estimate_leans_towards_the_strong_sightings(tmp_path, monkeypatch):
    # Same three places, but strongest at the eastern end: the estimate moves there.
    log, nb = walked(tmp_path, monkeypatch, [(2, -85), (10, -75), (18, -45)])
    east = reconcile(log, nb).networks[0].estimate
    assert east.lon > -56.185  # empujada hacia el este
    assert east.spread_m > 0  # los avistamientos no estaban todos en un punto


def test_without_coordinates_there_is_no_estimate(tmp_path, monkeypatch):
    log, _ = walked(tmp_path, monkeypatch, [(2, -85), (10, -45), (18, -85)])
    nb = tmp_path / "sin_coords.txt"
    nb.write_text("17:00 West\n17:20 East\n")
    item = reconcile(log, nb).networks[0]
    assert item.estimate is None
    # Sin estimación, el informe sigue diciendo dónde estabas.
    assert "AP near" not in format_report(reconcile(log, nb))


def test_a_network_with_no_signal_at_all_gets_no_estimate(tmp_path):
    log = tmp_path / "networks.jsonl"
    log.write_text(
        '{"time": "2026-09-05T17:10:00-03:00", "event": "scan", "networks": '
        '[{"ssid": "Mute", "bssid": "aa:bb:cc:dd:ee:aa"}]}\n'
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 West @ -34.9, -56.2\n17:20 East @ -34.9, -56.18\n")
    result = reconcile(log, nb)
    item = result.networks[0]
    assert not item.network.has_signal and item.estimate is None
    # Sin estimación, el informe cae a decir dónde estabas.
    assert "AP near" not in format_report(result)
    assert "[-34.90000," in format_report(result)


def test_the_estimate_reaches_the_csv(tmp_path, monkeypatch):
    log, nb = walked(tmp_path, monkeypatch, [(2, -85), (10, -45), (18, -85)])
    result = reconcile(log, nb)
    out = tmp_path / "out.csv"
    result.write_csv(out)
    header, row = out.read_text().splitlines()
    assert header.endswith("estimated_fraction,estimated_spread,barely_pinned")
    assert row.split(",")[-8].startswith("-34.90")
    assert float(row.split(",")[-6]) >= 0
    assert row.split(",")[-5:-3] == ["West", "East"]
    assert row.split(",")[-1] == ""  # oído de cerca al pasar: bien fijado


def test_the_report_says_how_many_access_points_it_placed(tmp_path, monkeypatch):
    log, nb = walked(tmp_path, monkeypatch, [(2, -85), (10, -45), (18, -85)])
    report = format_report(reconcile(log, nb))
    assert "Access points placed on the map from every sighting: 1 of 1" in report


def test_the_report_mentions_the_networks_it_could_not_place(tmp_path, monkeypatch):
    stamps = iter(["2026-09-05T17:10:00-03:00"])
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "networks.jsonl")
    log.record_scan(
        [
            ap("Heard", bssid="aa:bb:cc:dd:ee:aa", dbm=-50),
            ap("Mute", bssid="aa:bb:cc:dd:ee:bb", dbm=None, percent=None),
        ],
        "wlan0",
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 West @ -34.9, -56.2\n17:20 East @ -34.9, -56.18\n")
    report = format_report(reconcile(log.path, nb))
    assert "1 of 2 (the other 1 have no coordinates to work from" in report


# --- the notebook with button marks ------------------------------------------------


def stamp(hh, mm, ss=0):
    return datetime(2026, 9, 5, hh, mm, ss, tzinfo=TZ)


def test_untimed_lines_take_the_marks_in_order(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("Agraciada y Freire\nAgraciada y San Fructuoso @ -34.87, -56.21\n")
    marks = [(1, stamp(17, 52, 10)), (2, stamp(17, 58))]
    w = read_notebook(nb, stamp(17, 0).date(), TZ, marks)
    assert [(x.name, x.time) for x in w] == [
        ("Agraciada y Freire", stamp(17, 52, 10)),
        ("Agraciada y San Fructuoso", stamp(17, 58)),
    ]
    assert (w[1].lat, w[1].lon) == (-34.87, -56.21)


def test_a_numbered_line_names_its_mark(tmp_path):
    # Mark 2 was a mistake and is skipped; the line after it takes the next unused one.
    nb = tmp_path / "libreta.txt"
    nb.write_text("#1 Freire\n#3 Plaza\nNicaragua\n")
    marks = [(1, stamp(17, 0)), (2, stamp(17, 3)), (3, stamp(17, 10)), (4, stamp(17, 20))]
    w = read_notebook(nb, stamp(17, 0).date(), TZ, marks)
    assert [(x.name, x.time) for x in w] == [
        ("Freire", stamp(17, 0)),
        ("Plaza", stamp(17, 10)),
        ("Nicaragua", stamp(17, 20)),
    ]


def test_timed_and_untimed_lines_mix(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45 A\nB\n17:55:00 C\n")
    w = read_notebook(nb, stamp(17, 0).date(), TZ, [(1, stamp(17, 50))])
    assert [x.time for x in w] == [stamp(17, 45), stamp(17, 50), stamp(17, 55)]


def test_untimed_line_without_marks_in_the_log_is_an_error(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:45 A\nB\n")
    with pytest.raises(NotebookError, match="no button marks"):
        read_notebook(nb, stamp(17, 0).date(), TZ)


def test_a_mark_number_the_log_lacks_is_an_error(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("#9 Plaza\n#1 A\n")
    with pytest.raises(NotebookError, match="no mark #9"):
        read_notebook(nb, stamp(17, 0).date(), TZ, [(1, stamp(17, 0))])


def test_more_untimed_lines_than_marks_is_an_error(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("A\nB\nC\n")
    with pytest.raises(NotebookError, match="used up"):
        read_notebook(nb, stamp(17, 0).date(), TZ, [(1, stamp(17, 0)), (2, stamp(17, 5))])


def test_comments_are_still_comments_and_mark_numbers_are_not(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("# the whole line is a comment\n#2 Plaza # the big one\n17:45 A # arrived\n")
    w = read_notebook(nb, stamp(17, 0).date(), TZ, [(1, stamp(17, 0)), (2, stamp(17, 30))])
    assert [(x.name, x.time) for x in w] == [("Plaza", stamp(17, 30)), ("A", stamp(17, 45))]


def test_reconcile_takes_the_times_from_the_buttons_marks(tmp_path, monkeypatch):
    stamps = iter(
        [
            "2026-09-05T17:45:00-03:00",  # mark 1
            "2026-09-05T17:50:00-03:00",  # scan
            "2026-09-05T17:55:00-03:00",  # mark 2
        ]
    )
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "networks.jsonl")
    log.record_mark(1, 164)
    log.record_scan([ap("X")], "wlan0")
    log.record_mark(2, 164)
    nb = tmp_path / "libreta.txt"
    nb.write_text("Agraciada y Freire\nAgraciada y San Fructuoso\n")

    result = reconcile(log.path, nb)
    assert [w.name for w in result.waypoints] == ["Agraciada y Freire", "Agraciada y San Fructuoso"]
    assert result.placed[0].position.fraction == pytest.approx(0.5)
    assert "no timestamped" not in format_report(result)
    assert check_pace(log.path, nb) == []  # sin coordenadas no hay nada que medir


def test_button_marks_skips_malformed_records():
    from enodia.netlog import LogRecord
    from enodia.reconcile import button_marks

    records = [
        LogRecord("mark", time=stamp(17, 0), number=1),
        LogRecord("mark", time=None, number=2),
        LogRecord("mark", time=stamp(17, 5), number=None),
        LogRecord("scan", time=stamp(17, 6), number=9),
    ]
    assert button_marks(records) == [(1, stamp(17, 0))]


# --- GeoJSON ----------------------------------------------------------------------


def test_geojson_has_networks_crossings_and_the_route(tmp_path, monkeypatch):
    import json

    log, nb = walked(tmp_path, monkeypatch, [(2, -85), (10, -45), (18, -85)])
    result = reconcile(log, nb)
    out = tmp_path / "walk.geojson"
    result.write_geojson(out)
    collection = json.loads(out.read_text(encoding="utf-8"))
    assert collection["type"] == "FeatureCollection"
    kinds = [f["properties"]["kind"] for f in collection["features"]]
    assert kinds == ["network", "crossing", "crossing", "route"]

    network = collection["features"][0]
    lon, lat = network["geometry"]["coordinates"]  # [longitude, latitude]
    assert -56.3 < lon < -56.1 and -35.0 < lat < -34.8
    assert network["properties"]["placed_by"] == "estimate"
    assert network["properties"]["ssid"] == "Target" and network["properties"]["sightings"] == 3
    route = collection["features"][-1]
    assert route["geometry"]["type"] == "LineString" and len(route["geometry"]["coordinates"]) == 2
    assert (route["properties"]["from"], route["properties"]["to"]) == ("West", "East")


def test_geojson_falls_back_to_the_strongest_sighting(tmp_path):
    import json

    log = tmp_path / "networks.jsonl"
    log.write_text(
        '{"time": "2026-09-05T17:10:00-03:00", "event": "scan", "networks": '
        '[{"ssid": "Mute", "bssid": "aa:bb:cc:dd:ee:aa"}]}\n'
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 West @ -34.9, -56.2\n17:20 East @ -34.9, -56.18\n")
    out = tmp_path / "walk.geojson"
    reconcile(log, nb).write_geojson(out)
    network = json.loads(out.read_text())["features"][0]
    assert network["properties"]["placed_by"] == "strongest sighting"
    assert network["properties"]["spread_m"] is None
    assert network["geometry"]["coordinates"] == [-56.19, -34.9]


def test_geojson_without_coordinates_has_nothing_to_draw(tmp_path, monkeypatch):
    import json

    log, _ = walked(tmp_path, monkeypatch, [(2, -85), (10, -45)])
    nb = tmp_path / "sin_coords.txt"
    nb.write_text("17:00 West\n17:20 East\n")
    out = tmp_path / "walk.geojson"
    reconcile(log, nb).write_geojson(out)
    assert json.loads(out.read_text())["features"] == []


# --- placed along the route, with no coordinates at all -----------------------


def out_and_back(tmp_path, monkeypatch, sightings, notebook):
    """A walk down a stretch and straight back up it, seeing the given networks."""
    stamps = iter(stamp for stamp, _ in sightings)
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "networks.jsonl")
    for _, networks in sightings:
        log.record_scan(networks, "wlan0")
    nb = tmp_path / "libreta.txt"
    nb.write_text(notebook)
    return log.path, nb


def test_the_access_point_is_placed_along_the_route_without_any_coordinates(tmp_path, monkeypatch):
    # A notebook of bare crossing names: no centroid on a map is possible, but
    # the route itself is a ruler, and the same weighted centroid runs on it.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", [ap("Target", bssid="aa:bb:cc:dd:ee:aa", dbm=-85)]),
            ("2026-09-05T17:05:00-03:00", [ap("Target", bssid="aa:bb:cc:dd:ee:aa", dbm=-45)]),
            ("2026-09-05T17:08:00-03:00", [ap("Target", bssid="aa:bb:cc:dd:ee:aa", dbm=-85)]),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:10 B\n17:20 C\n")
    item = reconcile(log, nb, by_movement=False).networks[0]
    assert item.estimate is None  # sin coordenadas no hay mapa
    assert item.route_estimate is not None
    place = item.route_estimate.position
    assert (place.start.name, place.end.name) == ("A", "B")
    assert place.fraction == pytest.approx(0.5, abs=0.001)  # heard from both ends alike
    assert item.route_estimate.spread == pytest.approx(0.025, abs=0.005)
    assert not item.route_estimate.few_sightings


def test_the_route_estimate_belongs_to_the_stretch_it_was_loudest_on(tmp_path, monkeypatch):
    # Heard from two stretches. They are not averaged: the route is a path, and
    # a fraction of one stretch is not comparable with a fraction of another.
    # It belongs to the stretch it was loudest on, and only those sightings count.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:08:00-03:00", [ap("Across", bssid="aa:bb:cc:dd:ee:bb", dbm=-80)]),
            ("2026-09-05T17:12:00-03:00", [ap("Across", bssid="aa:bb:cc:dd:ee:bb", dbm=-40)]),
            ("2026-09-05T17:16:00-03:00", [ap("Across", bssid="aa:bb:cc:dd:ee:bb", dbm=-50)]),
            ("2026-09-05T17:20:00-03:00", [ap("Last", bssid="aa:bb:cc:dd:ee:cc", dbm=-60)]),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:10 B\n17:20 C\n")
    placed = {item.network.ssid: item for item in reconcile(log, nb, by_movement=False).networks}
    across, last = placed["Across"], placed["Last"]
    assert across.route_estimate is not None and last.route_estimate is not None
    assert across.route_estimate.stretch.name_from == "B"  # el tramo donde sonó fuerte
    assert across.route_estimate.sightings == 2  # el avistamiento débil de A a B no cuenta
    assert across.route_estimate.position.describe().startswith('between "B" and "C"')
    # The very end of the walk is the end of the last stretch, not the start of
    # one past it.
    assert last.route_estimate.position.describe() == 'at "C"'


def test_the_report_places_networks_along_the_route_when_it_cannot_map_them(tmp_path, monkeypatch):
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:02:00-03:00", [ap("Target", bssid="aa:bb:cc:dd:ee:aa", dbm=-85)]),
            ("2026-09-05T17:05:00-03:00", [ap("Target", bssid="aa:bb:cc:dd:ee:aa", dbm=-45)]),
            ("2026-09-05T17:08:00-03:00", [ap("Target", bssid="aa:bb:cc:dd:ee:aa", dbm=-85)]),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:10 B\n")
    report = format_report(reconcile(log, nb, by_movement=False))
    assert "Access points placed along the route from every sighting: 1 of 1" in report
    assert "not a place on a map" in report
    assert 'AP between "A" and "B", 50% of the way +/-3% of a stretch' in report


def test_the_report_says_which_networks_fell_back_to_the_route(tmp_path, monkeypatch):
    # One crossing has coordinates and one does not, so one network is placed on
    # the map and the other only along the route.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            ("2026-09-05T17:05:00-03:00", [ap("Mapped", bssid="aa:bb:cc:dd:ee:aa", dbm=-50)]),
            ("2026-09-05T17:15:00-03:00", [ap("Not", bssid="aa:bb:cc:dd:ee:bb", dbm=-50)]),
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A @ -34.9000, -56.2000\n17:10 B @ -34.9000, -56.1980\n17:20 C\n")
    report = format_report(reconcile(log, nb, by_movement=False))
    assert "Access points placed on the map from every sighting: 1 of 2" in report
    assert "the other 1 have no coordinates to work from, and are placed along the route" in report
    assert 'AP between "B" and "C", 50% of the way' in report


# --- the same stretch, walked twice -------------------------------------------


THERE_AND_BACK = [
    (
        "2026-09-05T17:00:30-03:00",
        [
            ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-80),
            ap("Silent", bssid="aa:bb:cc:dd:ee:cc", dbm=None, percent=None),
        ],
    ),
    ("2026-09-05T17:01:00-03:00", [ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-60)]),
    (
        "2026-09-05T17:01:30-03:00",
        [
            ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-40),
            ap("Once", bssid="aa:bb:cc:dd:ee:bb", dbm=-50),
        ],
    ),
    ("2026-09-05T17:02:30-03:00", [ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-80)]),
    ("2026-09-05T17:03:00-03:00", [ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-60)]),
    ("2026-09-05T17:03:30-03:00", [ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-40)]),
]


def test_two_passes_over_one_stretch_are_compared_in_the_same_direction(tmp_path, monkeypatch):
    # Walked down and straight back up. Both passes hear "Mid" strongest at the
    # end of their own pass, which is opposite ends of the same street: the gap
    # between them is the scan's lag, doubled, and their midpoint cancels it.
    log, nb = out_and_back(tmp_path, monkeypatch, THERE_AND_BACK, "17:00 A\n17:02 B\n17:04 A\n")
    (stretch,) = check_passes(log, nb, by_movement=False)
    assert (stretch.name_from, stretch.name_to) == ("A", "B")
    assert [one.forwards for one in stretch.passes] == [True, False]
    assert stretch.shared == ["aa:bb:cc:dd:ee:aa"]  # "Once" solo se oyó en una pasada
    # A network with no signal at all cannot be weighted, so no pass places it.
    assert not any("aa:bb:cc:dd:ee:cc" in one.places for one in stretch.passes)
    there, back = (one.places["aa:bb:cc:dd:ee:aa"] for one in stretch.passes)
    assert there == pytest.approx(0.689, abs=0.001)
    assert back == pytest.approx(0.311, abs=0.001)
    assert stretch.disagreement == pytest.approx(0.378, abs=0.001)
    assert stretch.shift == pytest.approx(0.189, abs=0.001)
    assert stretch.length_m is None and stretch.metres(0.5) is None


def test_the_pass_check_reports_metres_when_the_crossings_have_coordinates(tmp_path, monkeypatch):
    log, nb = out_and_back(
        tmp_path,
        monkeypatch,
        THERE_AND_BACK,
        "17:00 A @ -34.9000, -56.2000\n17:02 B @ -34.9000, -56.1980\n"
        "17:04 A @ -34.9000, -56.2000\n",
    )
    (stretch,) = check_passes(log, nb, by_movement=False)
    assert stretch.length_m == pytest.approx(182, abs=2)
    assert stretch.metres(0.5) == pytest.approx(91, abs=1)
    report = format_pass_check([stretch])
    assert '"A" to "B", 182 m' in report
    assert "17:00:30 to 17:01:30, walked there" in report
    assert "17:02:30 to 17:03:30, walked back" in report
    assert "1 networks heard on more than one pass" in report
    assert "the passes disagree by 38% of the stretch (69 m) on average" in report
    assert "shift in the direction of travel: 19% (34 m), which is what a scan's lag" in report
    assert "Mean disagreement over 1 stretch: 38% of a stretch." in report


def test_a_stretch_walked_twice_the_same_way_has_no_shift_to_measure(tmp_path, monkeypatch):
    # Round the block and down the same street again: two passes, one direction.
    log, nb = out_and_back(
        tmp_path,
        monkeypatch,
        [
            ("2026-09-05T17:00:30-03:00", [ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-40)]),
            ("2026-09-05T17:01:30-03:00", [ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-80)]),
            ("2026-09-05T17:06:30-03:00", [ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-40)]),
            ("2026-09-05T17:07:30-03:00", [ap("Mid", bssid="aa:bb:cc:dd:ee:aa", dbm=-80)]),
        ],
        "17:00 A\n17:02 B\n17:04 C\n17:06 A\n17:08 B\n",
    )
    (stretch,) = check_passes(log, nb, by_movement=False)
    assert [one.forwards for one in stretch.passes] == [True, True]
    assert stretch.disagreement == pytest.approx(0.0, abs=0.001)  # el mismo sesgo en ambas
    assert stretch.shift is None
    report = format_pass_check([stretch])
    assert "every pass went the same way: no shift to measure" in report


def test_a_shift_needs_a_network_heard_in_both_directions(tmp_path, monkeypatch):
    # Three passes, and the one network heard twice was heard on the two that
    # went the same way: there is a disagreement to report and no shift.
    log, nb = out_and_back(
        tmp_path,
        monkeypatch,
        [
            ("2026-09-05T17:00:30-03:00", [ap("There", bssid="aa:bb:cc:dd:ee:aa", dbm=-40)]),
            ("2026-09-05T17:03:00-03:00", [ap("Back", bssid="aa:bb:cc:dd:ee:bb", dbm=-40)]),
            ("2026-09-05T17:05:30-03:00", [ap("There", bssid="aa:bb:cc:dd:ee:aa", dbm=-40)]),
        ],
        "17:00 A\n17:02 B\n17:04 A\n17:06 B\n",
    )
    (stretch,) = check_passes(log, nb, by_movement=False)
    assert [one.forwards for one in stretch.passes] == [True, False, True]
    assert stretch.shared == ["aa:bb:cc:dd:ee:aa"]
    assert stretch.disagreement == pytest.approx(0.5, abs=0.001)
    assert stretch.shift is None


def test_passes_that_share_no_network_have_nothing_to_compare(tmp_path, monkeypatch):
    log, nb = out_and_back(
        tmp_path,
        monkeypatch,
        [
            ("2026-09-05T17:01:00-03:00", [ap("Early", bssid="aa:bb:cc:dd:ee:aa", dbm=-40)]),
            ("2026-09-05T17:03:00-03:00", [ap("Late", bssid="aa:bb:cc:dd:ee:bb", dbm=-40)]),
        ],
        "17:00 A\n17:02 B\n17:04 A\n",
    )
    (stretch,) = check_passes(log, nb, by_movement=False)
    assert stretch.shared == [] and stretch.disagreement is None
    report = format_pass_check([stretch])
    assert "nothing heard on two passes: nothing to compare" in report
    assert "Mean disagreement" not in report


def test_a_walk_that_never_doubles_back_has_nothing_to_check(tmp_path, monkeypatch):
    log, nb = out_and_back(
        tmp_path,
        monkeypatch,
        [("2026-09-05T17:01:00-03:00", [ap("One", bssid="aa:bb:cc:dd:ee:aa", dbm=-40)])],
        "17:00 A\n17:02 B\n",
    )
    assert check_passes(log, nb) == []
    assert "Nothing to check" in format_pass_check([])
    assert "turn round at the corner" in format_pass_check([])


# --- what the walk never really established -----------------------------------


def test_an_access_point_walked_past_is_pinned_down_and_one_heard_from_afar_is_not(
    tmp_path, monkeypatch
):
    # "Near" peaks sharply as you walk past it, so weighting the sightings by
    # signal pulls the estimate well clear of the middle of the walk. "Far" is
    # heard faintly and evenly from a block away, so the weights say nothing and
    # the estimate is just where you happened to be. Both get a point on the
    # map, and only one of them was measured.
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [
            (
                f"2026-09-05T17:0{minute}:00-03:00",
                [
                    ap("Near", bssid="aa:bb:cc:dd:ee:aa", dbm=near),
                    ap("Far", bssid="aa:bb:cc:dd:ee:bb", dbm=-85),
                ],
            )
            for minute, near in [(2, -85), (4, -80), (5, -40), (6, -80), (8, -85)]
        ],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 West @ -34.9000, -56.2000\n17:10 East @ -34.9000, -56.1800\n")
    placed = {i.network.ssid: i for i in reconcile(log, nb, by_movement=False).networks}
    near, far = placed["Near"].estimate, placed["Far"].estimate
    assert near is not None and far is not None
    assert not near.barely_pinned
    assert far.barely_pinned
    report = format_report(reconcile(log, nb, by_movement=False))
    assert "1 of those 2 were barely pinned down" in report
    assert "Far aa:bb:cc:dd:ee:bb" in report and "(barely pinned down)" in report


def test_a_single_sighting_was_never_pinned_down_by_anything(tmp_path, monkeypatch):
    log, nb = walked(tmp_path, monkeypatch, [(10, -45)])
    item = reconcile(log, nb).networks[0]
    assert item.estimate is not None and item.estimate.barely_pinned
    assert "Every one of them was pinned down" not in format_report(reconcile(log, nb))


def test_a_walk_that_pinned_everything_down_says_so(tmp_path, monkeypatch):
    log, nb = walked(tmp_path, monkeypatch, [(2, -85), (10, -45), (18, -85)])
    assert "Every one of them was pinned down by the walk." in format_report(reconcile(log, nb))


# --- one corner, written both ways round --------------------------------------


def test_a_corner_written_both_ways_round_is_reported_and_not_merged():
    # Reported, because Enodia is silently taking it for two corners. Not
    # merged, because "Treinta y Tres" is one street and no rule can tell the
    # two shapes apart from a name on its own.
    assert confusable_crossings(["Treinta y Tres", "Solari"]) == []
    assert confusable_crossings(["Agraciada y Freire", "Freire y Agraciada"]) == [
        ("Agraciada y Freire", "Freire y Agraciada")
    ]
    assert confusable_crossings(["Rivera e Italia", "italia  E  RIVERA"]) == [
        ("Rivera e Italia", "italia  E  RIVERA")
    ]
    assert confusable_crossings(["Agraciada / Freire", "Freire y Agraciada"]) != []
    assert confusable_crossings(["A y B y C", "C y B y A"]) == []  # tres mitades, no es esquina
    # With a comma the street with a "y" in its name is one half, and both ways round is caught.
    assert confusable_crossings(["Treinta y Tres, Zorrilla", "Zorrilla, Treinta y Tres"]) == [
        ("Treinta y Tres, Zorrilla", "Zorrilla, Treinta y Tres")
    ]
    assert confusable_crossings(["Treinta y Tres, Zorrilla", "Zorrilla y Treinta y Tres"]) == []


def test_the_report_names_the_crossings_to_settle_on(tmp_path, monkeypatch):
    log = write_log(
        tmp_path / "networks.jsonl",
        monkeypatch,
        [(f"2026-09-05T17:0{m}:00-03:00", [ap("Casa", dbm=-50)]) for m in (1, 3, 5)],
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text(
        "17:00 Agraciada y Freire\n17:02 Solari\n17:04 Freire y Agraciada\n17:06 Treinta y Tres\n"
    )
    report = format_report(reconcile(log, nb, by_movement=False))
    assert "written both ways round" in report
    assert '"Agraciada y Freire" and "Freire y Agraciada"' in report
    assert "Treinta y Tres" not in report.split("written both ways round")[1].split("Settle")[0]
    assert format_confusable([]) == ""


def test_a_stretch_is_one_stretch_however_its_crossings_were_spelled():
    # The same block walked twice, its corners spelled differently the second
    # time. Without folding these would be two stretches walked once each, and
    # --check-passes would have nothing to compare.
    points = [
        wp(17, 0, "Yaguarón"),
        wp(17, 2, "Solari"),
        wp(17, 4, "yaguaron"),
    ]
    (one,) = route_stretches(points)
    assert one.segments == (0, 1) and one.forwards == (True, False)


# --- two radios are not two places ---------------------------------------------


def test_two_interfaces_standing_still_are_not_walking(tmp_path, monkeypatch):
    # The pace estimate reads how much the view turned over. Two cards see
    # different sets (a 5 GHz one hears eight networks where a 2.4 GHz one hears
    # forty), so alternating between them looks like a whole block walked every
    # few seconds while the operator has not moved at all.
    stamps = iter(
        stamp for stamp in ["2026-09-05T17:02:00-03:00"] * 2 + ["2026-09-05T17:06:00-03:00"] * 2
    )
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "networks.jsonl")
    for _ in range(2):
        log.record_scan(seen("A", "B", "C", "D", "E"), "wlan0")
        log.record_scan(seen("X", "Y", "Z"), "wlan1")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Start\n17:10 End\n")

    placed = reconcile(log.path, nb).placed
    assert len(placed) == 2  # dos ciclos, no cuatro registros
    # Standing still, so the clock is all there is to go on and the two land
    # where it puts them. Before the fix they were spread down the block by a
    # turnover of 1.0 that came from swapping radios.
    assert [one.position.fraction for one in placed] == pytest.approx([0.2, 0.6])


def test_a_cycle_that_only_one_card_answered_is_no_evidence_of_walking(tmp_path, monkeypatch):
    # Standing still with two cards, and in the third cycle only wlan0 answered.
    # Folded, that cycle is still one card's look, and against the two-card
    # looks either side of it three networks of eight are missing: a turnover
    # of 0.375 each way, the only movement the stretch appeared to hold, so the
    # scans used to land at 0.2, 0.2, 0.5 and 0.8. Standing still is the clock.
    stamps = iter(
        ["2026-09-05T17:02:00-03:00"] * 2
        + ["2026-09-05T17:04:00-03:00"] * 2
        + ["2026-09-05T17:06:00-03:00"]
        + ["2026-09-05T17:08:00-03:00"] * 2
    )
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "networks.jsonl")
    for cycle in range(4):
        log.record_scan(seen("A", "B", "C", "D", "E"), "wlan0")
        if cycle != 2:
            log.record_scan(seen("X", "Y", "Z"), "wlan1")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Start\n17:10 End\n")
    placed = reconcile(log.path, nb).placed
    assert [one.position.fraction for one in placed] == pytest.approx([0.2, 0.4, 0.6, 0.8])


def test_a_cycle_keeps_the_stronger_reading_of_the_two_radios(tmp_path, monkeypatch):
    stamps = iter(["2026-09-05T17:02:00-03:00"] * 2)
    monkeypatch.setattr(netlog, "now_iso", lambda ago=0.0: next(stamps))
    log = NetworkLog(tmp_path / "networks.jsonl")
    log.record_scan([ap("Casa", bssid="aa:bb:cc:dd:ee:aa", dbm=-80)], "wlan0")
    log.record_scan([ap("Casa", bssid="aa:bb:cc:dd:ee:aa", dbm=-45)], "wlan1")
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 Start\n17:10 End\n")
    (only,) = reconcile(log.path, nb).networks
    assert only.seen == 1 and only.network.signal_dbm == -45


def test_a_time_that_is_not_a_time_is_a_notebook_error_and_not_a_traceback(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A y B\n25:59 C y D\n")
    with pytest.raises(NotebookError, match="is not a time"):
        read_notebook(nb, datetime(2026, 9, 5, tzinfo=TZ).date(), TZ)


def test_two_radios_are_never_a_step_even_when_a_cycle_straddles_a_second():
    # merged_scans folds a cycle into one record by its timestamp. When the two
    # scans of one cycle land either side of a second boundary they stay apart,
    # and this is what keeps them from reading as a block walked.
    def net(name):
        return SeenNetwork(name, f"aa:bb:cc:dd:ee:{ord(name):02x}", "wpa2", 2412, -50, None)

    wlan0 = LogRecord("scan", interface="wlan0", networks=[net(c) for c in "ABCDE"])
    wlan1 = LogRecord("scan", interface="wlan1", networks=[net(c) for c in "XYZ"])
    assert _step(wlan0, wlan1) is None  # dos radios: ninguna evidencia, ni de quietud
    assert _step(wlan0, wlan0) == 0.0  # el mismo radio, la misma vista
    moved = LogRecord("scan", interface="wlan0", networks=[net(c) for c in "XYZ"])
    assert _step(wlan0, moved) == 1.0  # el mismo radio, otra vista: eso sí es caminar


def test_a_cycle_that_straddles_a_second_is_still_one_cycle(tmp_path):
    # The fix before this one grouped on the timestamp, which has one second of
    # resolution. A cycle whose two scans land either side of a second came
    # apart, and then every pair was two different radios and worth nothing, so
    # the walk read as standing still. The loop writes down which cycle a scan
    # belongs to, and that is what they are grouped on.
    log = tmp_path / "networks.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-05T17:00:{second:02d}-03:00",
                    "event": "scan",
                    "cycle": cycle,
                    "interface": card,
                    "networks": [
                        {"ssid": n, "bssid": f"aa:bb:cc:dd:ee:{ord(n):02x}"} for n in seen
                    ],
                }
            )
            for cycle, second, card, seen in (
                (1, 0, "wlan0", "ABC"),
                (1, 1, "wlan1", "XYZ"),
                (2, 5, "wlan0", "DEF"),
                (2, 6, "wlan1", "UVW"),
            )
        )
        + "\n"
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00:00 A\n17:00:10 B\n")
    placed = reconcile(log, nb).placed
    assert len(placed) == 2
    assert [len(one.scan.networks) for one in placed] == [6, 6]
    assert [one.scan.time.second for one in placed] == [0, 5]  # la hora del primero del ciclo
    # And the two cycles saw nothing in common, which is a whole block walked.
    assert _step(placed[0].scan, placed[1].scan) == 1.0


def test_a_log_written_before_cycles_existed_still_groups_on_the_clock(tmp_path):
    log = tmp_path / "networks.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": "2026-09-05T17:02:00-03:00",
                    "event": "scan",
                    "interface": card,
                    "networks": [
                        {"ssid": n, "bssid": f"aa:bb:cc:dd:ee:{ord(n):02x}"} for n in seen
                    ],
                }
            )
            for card, seen in (("wlan0", "ABC"), ("wlan1", "XYZ"))
        )
        + "\n"
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:10 B\n")
    (only,) = reconcile(log, nb).placed
    assert len(only.scan.networks) == 6


def test_a_cycle_number_reused_after_a_restart_would_fold_two_places_into_one(tmp_path):
    # The guard is in the monitor, which never writes a number twice into one
    # file. This is what it is guarding against: two runs both numbering from
    # one, ten minutes and a block apart, arriving as a single scan at the
    # earlier time with every network of both.
    log = tmp_path / "networks.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-05T20:{minute:02d}:00-03:00",
                    "event": "scan",
                    "cycle": 1,
                    "interface": card,
                    "networks": [{"ssid": name, "bssid": f"aa:bb:cc:dd:ee:{ord(name):02x}"}],
                }
            )
            for minute, card, name in ((0, "wlan0", "A"), (0, "wlan1", "B"), (10, "wlan0", "X"))
        )
        + "\n"
    )
    folded = merged_scans([r for r in read_log(log) if r.is_scan])
    assert (
        len(folded) == 1 and len(folded[0].networks) == 3
    )  # lo que NO puede pasar en un log nuestro

    # Numbered the way the monitor numbers them, they stay two places.
    log.write_text(
        log.read_text().replace(
            '"cycle": 1, "interface": "wlan0", "networks": [{"ssid": "X"',
            '"cycle": 2, "interface": "wlan0", "networks": [{"ssid": "X"',
        )
    )
    folded = merged_scans([r for r in read_log(log) if r.is_scan])
    assert [len(one.networks) for one in folded] == [2, 1]


def test_two_cycles_inside_one_second_are_two_sightings_of_the_network(tmp_path):
    # The mirror image of the bug above, one layer down and four versions later.
    # Counting a sighting per (network, timestamp) was right while two radios
    # wrote a record each at the same instant. `merged_scans` folds those
    # together now, so the only records still sharing a second are genuinely
    # different cycles, and keying on the clock threw the second one away: the
    # network was recorded as heard once and its place along the route was
    # estimated from half the evidence, with nothing anywhere saying so.
    log = tmp_path / "networks.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-05T20:00:{second:02d}-03:00",
                    "event": "scan",
                    "cycle": cycle,
                    "interface": "wlan0",
                    "networks": [{"ssid": "X", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": dbm}],
                }
            )
            for cycle, second, dbm in ((1, 0, -50), (2, 0, -60), (3, 30, -70))
        )
        + "\n"
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("19:59:30 Alfa y Bravo\n20:01:30 Alfa y Charlie\n")
    result = reconcile(log, nb)
    assert len(result.placed) == 3
    (network,) = result.networks
    assert network.seen == 3
    assert network.route_estimate is not None and network.route_estimate.sightings == 3


def test_a_network_listed_twice_in_one_scan_was_still_heard_once(tmp_path):
    # And this is the job the counting still has, now asked per scan: one look
    # at one place is one sighting, however many times it turns up in the list.
    log = tmp_path / "networks.jsonl"
    log.write_text(
        json.dumps(
            {
                "time": "2026-09-05T20:00:00-03:00",
                "event": "scan",
                "cycle": 1,
                "networks": [
                    {"ssid": "X", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -50},
                    {"ssid": "X", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -55},
                ],
            }
        )
        + "\n"
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("19:59:30 Alfa y Bravo\n20:01:30 Alfa y Charlie\n")
    (network,) = reconcile(log, nb).networks
    assert network.seen == 1
    assert network.route_estimate is not None and network.route_estimate.sightings == 1


# --- one file, several walks ---------------------------------------------------


def two_walks_in_one_file(tmp_path):
    """`--log walk.jsonl` reused: two walks, a day apart, on the same pages."""
    log = tmp_path / "walk.jsonl"
    rows = []
    for day, hour, walk, ssid in (("17", 17, "aaaa1111", "Casa"), ("18", 18, "bbbb2222", "Bar")):
        rows.append(
            json.dumps(
                {
                    "time": f"2026-09-{day}T{hour}:00:00-03:00",
                    "event": "mark",
                    "number": 1,
                    "outing": walk,
                }
            )
        )
        rows.append(
            json.dumps(
                {
                    "time": f"2026-09-{day}T{hour}:05:00-03:00",
                    "event": "scan",
                    "cycle": 1,
                    "outing": walk,
                    "networks": [{"ssid": ssid, "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -50}],
                }
            )
        )
        rows.append(
            json.dumps(
                {
                    "time": f"2026-09-{day}T{hour}:10:00-03:00",
                    "event": "mark",
                    "number": 2,
                    "outing": walk,
                }
            )
        )
    log.write_text("\n".join(rows) + "\n")
    nb = tmp_path / "libreta.txt"
    nb.write_text("#1 A\n#2 B\n")
    return log, nb


def test_a_notebook_of_button_marks_takes_the_times_of_its_own_walk(tmp_path):
    # The worst of the family. Every run numbers its marks from one, so a file
    # of two walks holds two mark 1s, and read as one file the second walk's
    # took the place of the first walk's: a notebook of `#1` and `#2` came back
    # with times from a walk on another day, and nothing anywhere said so.
    log, nb = two_walks_in_one_file(tmp_path)
    first = reconcile(log, nb, outing="aaaa1111")
    assert [point.time.day for point in first.waypoints] == [17, 17]
    assert [item.network.ssid for item in first.networks] == ["Casa"]

    second = reconcile(log, nb, outing="bbbb2222")
    assert [point.time.day for point in second.waypoints] == [18, 18]
    assert [item.network.ssid for item in second.networks] == ["Bar"]


def test_the_last_walk_in_a_file_is_the_one_read_when_none_is_named(tmp_path):
    log, nb = two_walks_in_one_file(tmp_path)
    assert [item.network.ssid for item in reconcile(log, nb).networks] == ["Bar"]


def test_a_walk_the_file_does_not_hold_is_said_and_not_guessed_at(tmp_path):
    log, nb = two_walks_in_one_file(tmp_path)
    with pytest.raises(NotebookError, match="no walk called 'cccc3333'"):
        reconcile(log, nb, outing="cccc3333")


def test_a_day_comes_from_the_walk_being_read_and_not_from_the_file(tmp_path):
    # The other half of the same bug, and the one that made it look like an
    # empty answer rather than a wrong one: a notebook without a `date` line
    # takes the day of the first scan, and that was the first scan of the file.
    log, nb = two_walks_in_one_file(tmp_path)
    nb.write_text("18:00 A\n18:10 B\n")
    placed = reconcile(log, nb, outing="bbbb2222").placed
    assert len(placed) == 1 and placed[0].scan.time.day == 18


def test_a_date_that_is_not_a_date_says_so_wherever_the_line_wrote_it(tmp_path):
    # Fixed once for the `date` directive, and the line that carries its own
    # date is the other place the 31st of February can be written.
    bad = tmp_path / "feb.txt"
    bad.write_text("2026-02-31 17:00 A\n2026-03-01 17:10 B\n")
    with pytest.raises(NotebookError, match=re.escape("feb.txt:1: '2026-02-31 17:00 A'")):
        read_notebook(bad, datetime(2026, 9, 17).date(), TZ)


# --- a network with nothing to identify it -------------------------------------


def anonymous(freq, dbm=-50):
    """What a backend with no BSSID gives for a network that hides its name."""
    return {"ssid": "", "bssid": None, "frequency": freq, "signal_dbm": dbm}


def test_two_anonymous_networks_are_not_one_network(tmp_path):
    # `key` is the BSSID or the SSID, and a network with neither answers to the
    # empty string, which is the same empty string every anonymous network in
    # the world answers to. Folded on it, one of them stood for all of them.
    log = tmp_path / "networks.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-05T17:00:0{second}-03:00",
                    "event": "scan",
                    "cycle": 1,
                    "interface": card,
                    "networks": [anonymous(freq), {"ssid": "Real", "bssid": "aa:bb:cc:dd:ee:01"}],
                }
            )
            for second, card, freq in ((0, "wlan0", 2412), (1, "wlan1", 5180))
        )
        + "\n"
    )
    (folded_scan,) = merged_scans([r for r in read_log(log) if r.is_scan])
    # The two radios' named network is one network. Their anonymous ones are two
    # readings that nothing can say are the same, and both survive.
    assert len(folded_scan.networks) == 3
    assert sorted(n.frequency or 0 for n in folded_scan.networks) == [0, 2412, 5180]
    # And what is in view, for the pace, counts only what can be told apart.
    assert _in_view(folded_scan) == {"aa:bb:cc:dd:ee:01"}


def test_a_network_with_no_identity_is_logged_and_not_placed(tmp_path):
    # Placing a network means gathering every sighting of it, and there is no
    # telling which sightings were of this one.
    log = tmp_path / "networks.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-05T17:0{minute}:00-03:00",
                    "event": "scan",
                    "cycle": minute,
                    "networks": [anonymous(2412), {"ssid": "Real", "bssid": "aa:bb:cc:dd:ee:01"}],
                }
            )
            for minute in (1, 5)
        )
        + "\n"
    )
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:00 A\n17:10 B\n")
    result = reconcile(log, nb)
    assert [item.network.ssid for item in result.networks] == ["Real"]
    assert len(result.placed[0].scan.networks) == 2  # en el log siguen estando las dos


def test_a_line_that_carries_its_own_date_moves_the_day_with_it(tmp_path):
    # The day moved for a rollover and not for a date written on the line, so
    # `2026-09-18 23:50` followed by a bare `00:10` put the second crossing
    # twenty-three hours before the first, and the walk ran backwards.
    nb = tmp_path / "libreta.txt"
    nb.write_text("2026-09-18 23:50 A\n00:10 B\n00:20 C\n")
    found = read_notebook(nb, datetime(2026, 9, 17).date(), TZ)
    assert [point.time.isoformat() for point in found] == [
        "2026-09-18T23:50:00-03:00",
        "2026-09-19T00:10:00-03:00",
        "2026-09-19T00:20:00-03:00",
    ]


def test_a_walk_that_goes_back_in_time_is_refused(tmp_path):
    # Only reachable with dates written on the lines, since a bare time earlier
    # than the last one has just rolled over. Everything downstream reads the
    # crossings in the order they are written, so a route that goes backwards is
    # not a route anything can be placed along.
    nb = tmp_path / "libreta.txt"
    nb.write_text("2026-09-18 18:00 A\n2026-09-17 19:00 B\n")
    with pytest.raises(NotebookError, match="is earlier than the crossing before it"):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ)


def test_a_time_typed_wrong_is_an_error_and_not_a_crossings_name(tmp_path):
    # `MARKED_LINE` takes almost any line as a crossing's name, so a typo in a
    # time stopped being an error and quietly became a notebook of a different
    # kind: a crossing literally called "17:0 A", taking the next button mark.
    nb = tmp_path / "libreta.txt"
    nb.write_text("17:0 A\nB\n")
    marks = [
        (1, datetime(2026, 9, 17, 17, tzinfo=TZ)),
        (2, datetime(2026, 9, 17, 17, 10, tzinfo=TZ)),
    ]
    with pytest.raises(NotebookError, match="begins like a time and is not one"):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ, marks)

    nb.write_text("2026-9-18 17:00 A\nB\n")
    with pytest.raises(NotebookError, match="begins like a time and is not one"):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ, marks)

    # A crossing whose name begins with a number is still a crossing.
    nb.write_text("18 de Julio y Yaguaron\n8 de Octubre y Garibaldi\n")
    assert [point.name for point in read_notebook(nb, datetime(2026, 9, 17).date(), TZ, marks)] == [
        "18 de Julio y Yaguaron",
        "8 de Octubre y Garibaldi",
    ]


def test_a_notebook_with_no_times_is_not_a_notebook_that_cannot_be_read(tmp_path):
    # Two failures that mean opposite things to a caller that only wanted the
    # times: this one is fine and its times are not known.
    nb = tmp_path / "libreta.txt"
    nb.write_text("A y B\nC y D\n")
    with pytest.raises(UntimedNotebook):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ)

    nb.write_text("25:99 A y B\n17:10 C y D\n")
    with pytest.raises(NotebookError) as broken:
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ)
    assert not isinstance(broken.value, UntimedNotebook)


def test_a_date_directive_cannot_take_the_walk_backwards(tmp_path):
    # The check on the times could be walked round: the directive used to forget
    # the crossing above it, and then a later time on an earlier day passed
    # unexamined. Letting the rollover quietly undo the directive instead would
    # be an instruction ignored without a word, which is no better.
    nb = tmp_path / "libreta.txt"
    nb.write_text("2026-09-18 18:00 A\ndate 2026-09-17\n19:00 B\n")
    with pytest.raises(NotebookError, match="is before the crossing above it"):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ)

    # Forwards, and to the same day, are both ordinary.
    nb.write_text("2026-09-18 23:50 A\ndate 2026-09-19\n00:10 B\n")
    assert [point.time.day for point in read_notebook(nb, datetime(2026, 9, 18).date(), TZ)] == [
        18,
        19,
    ]


def test_a_date_directive_typed_wrong_is_an_error_and_not_a_crossing(tmp_path):
    # `date 2026-9-18` is one month digit short of the shape, and taken for a
    # crossing's name it ate a button mark and shifted every crossing after it
    # onto the wrong press, which is worse than refusing the line.
    nb = tmp_path / "libreta.txt"
    nb.write_text("date 2026-9-18\nA\nB\n")
    marks = [(n, datetime(2026, 9, 17, 18, n * 10, tzinfo=TZ)) for n in (1, 2, 3)]
    with pytest.raises(NotebookError, match="begins like a date directive and is not one"):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ, marks)


def test_one_press_of_the_button_is_one_crossing(tmp_path):
    nb = tmp_path / "libreta.txt"
    nb.write_text("#1 A\n#1 B\n")
    marks = [
        (1, datetime(2026, 9, 17, 18, tzinfo=TZ)),
        (2, datetime(2026, 9, 17, 18, 10, tzinfo=TZ)),
    ]
    with pytest.raises(NotebookError, match="mark #1 is used twice"):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ, marks)


def test_a_log_that_numbered_one_mark_twice_is_refused(tmp_path):
    # `dict(marks)` keeps the last of a repeated number without a word, and the
    # crossing that answers to it would silently take the wrong time.
    nb = tmp_path / "libreta.txt"
    nb.write_text("#1 A\n#2 B\n")
    twice = [
        (1, datetime(2026, 9, 17, 18, tzinfo=TZ)),
        (1, datetime(2026, 9, 17, 19, tzinfo=TZ)),
        (2, datetime(2026, 9, 17, 20, tzinfo=TZ)),
    ]
    with pytest.raises(NotebookError, match="the log has mark #1 more than once"):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ, twice)


def test_marks_that_cannot_cover_the_notebook_are_a_mismatch_not_unknown_times(tmp_path):
    # Marks were given and they do not answer the notebook, which is often the
    # wrong walk of the log picked. Reading that as "the times are unknown" sent
    # `--geocode` on to ask Overpass about a notebook it had not managed to read.
    nb = tmp_path / "libreta.txt"
    nb.write_text("#7 A\n#8 B\n")
    marks = [
        (1, datetime(2026, 9, 17, 18, tzinfo=TZ)),
        (2, datetime(2026, 9, 17, 18, 10, tzinfo=TZ)),
    ]
    with pytest.raises(NotebookError) as named:
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ, marks)
    assert not isinstance(named.value, UntimedNotebook)
    assert "Its marks run 1 to 2" in str(named.value)

    nb.write_text("A\nB\nC\n")
    with pytest.raises(NotebookError) as short:
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ, marks)
    assert not isinstance(short.value, UntimedNotebook)

    # And with no marks at all it is the ordinary state of a button notebook.
    with pytest.raises(UntimedNotebook):
        read_notebook(nb, datetime(2026, 9, 17).date(), TZ)
