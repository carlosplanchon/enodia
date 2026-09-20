"""Tests for looking a notebook's crossings up on OpenStreetMap."""

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from enodia import geocode
from enodia.geocode import (
    Crossing,
    GeocodeError,
    Proxy,
    clusters,
    comment_at,
    corner_streets,
    crossing_times,
    escape,
    format_geocoding,
    geocode_notebook,
    geocoded_path,
    implausible_stretches,
    junction_of,
    node_places,
    notebook_marks,
    open_socket,
    overpass_query,
    parse_proxy,
    post_overpass,
    read_crossings,
    ways_by_street,
    write_geocoded,
)
from enodia.reconcile import NotebookError

TZ = timezone(timedelta(hours=-3))


def notebook(tmp_path, text, name="libreta.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def way(number, name, nodes, **tags):
    return {"type": "way", "id": number, "tags": {"name": name, **tags}, "nodes": list(nodes)}


def node(number, lat, lon):
    return {"type": "node", "id": number, "lat": lat, "lon": lon}


# A cross: Agraciada runs through 10, 11, 12; Freire meets it at 10; Solari at 12.
ANSWER = {
    "elements": [
        way(1, "Avenida Agraciada", [10, 11, 12], alt_name="Agraciada"),
        way(2, "Freire", [10, 20]),
        way(3, "Solari", [12, 30]),
        node(10, -34.9000, -56.2000),
        node(11, -34.9005, -56.1990),
        node(12, -34.9010, -56.1980),
        node(20, -34.9000, -56.2010),
        node(30, -34.9010, -56.1970),
    ]
}


def answering(answer=ANSWER, seen=None):
    """A stand-in for the one network call, recording what it was asked."""

    def fetch(query, url=geocode.OVERPASS_URL, proxy=None):
        if seen is not None:
            seen.update(query=query, url=url, proxy=proxy)
        return answer

    return fetch


# --- reading the notebook without destroying it -------------------------------


def test_only_the_lines_that_name_a_crossing_are_crossings(tmp_path):
    path = notebook(
        tmp_path,
        "# domingo\n\ndate 2026-09-06\n17:00 A y B\n#7 C y D\n17:10 E y F @ -34.9, -56.2\n",
    )
    found = read_crossings(path)
    assert [one.line for one in found] == [4, 5, 6]
    assert [one.name for one in found] == ["A y B", "C y D", "E y F"]
    assert found[2].placed and found[2].coordinates == (-34.9, -56.2)
    assert not found[0].placed and found[0].coordinates is None


def test_read_crossings_and_read_notebook_agree_on_which_lines_are_crossings(tmp_path):
    from enodia.reconcile import read_notebook

    text = "# nota\n17:00 A y B\ndate 2026-09-06\n\n17:10 C y D  # tarde\n"
    path = notebook(tmp_path, text)
    ours = read_crossings(path)
    theirs = read_notebook(path, datetime(2026, 9, 5, tzinfo=TZ).date(), TZ)
    assert [one.name for one in ours] == [point.name for point in theirs]


def test_a_leading_mark_number_is_not_mistaken_for_the_start_of_a_comment():
    assert comment_at("#7 Plaza Fabini  # nota\n") == len("#7 Plaza Fabini  ")
    assert comment_at("17:00 A y B\n") == len("17:00 A y B\n")
    assert comment_at("  # entera\n") == 2


def test_a_byte_order_mark_does_not_swallow_the_first_crossing(tmp_path):
    path = notebook(tmp_path, "﻿17:00 A y B\n17:10 C y D\n")
    assert [one.name for one in read_crossings(path)] == ["A y B", "C y D"]


# --- splitting a crossing into two streets ------------------------------------


def test_a_corner_is_split_with_the_accents_and_capitals_it_was_written_with():
    assert corner_streets("Yaguarón y 18 de Julio") == ("Yaguarón", "18 de Julio")
    assert corner_streets("Agraciada Y Freire") == ("Agraciada", "Freire")  # la Y mayúscula
    assert corner_streets("Rivera e Italia") == ("Rivera", "Italia")
    assert corner_streets("Agraciada / Freire") == ("Agraciada", "Freire")


def test_a_name_that_is_not_two_streets_is_not_a_corner():
    assert corner_streets("Plaza Independencia") is None
    assert corner_streets("A y B y C") is None  # tres mitades
    assert corner_streets("Agraciada y agraciada") is None  # una calle consigo misma


# --- the query ----------------------------------------------------------------


def test_every_street_is_asked_for_by_exact_name_because_overpass_regex_is_not_unicode():
    query = overpass_query(["Yaguarón", "Freire"], "Montevideo")
    assert '["name"="Yaguarón"]' in query
    assert "~" not in query.split("[highway!~")[1].split("]")[1]  # ningún regex sobre los nombres
    assert query.count("way(area.a)") == 2 * len(geocode.NAME_TAGS)
    assert 'area[name="Montevideo"]->.a;' in query
    assert ".w out body;" in query and "node(w.w);" in query and "out skel;" in query


def test_a_street_is_asked_for_under_its_other_names_as_well():
    query = overpass_query(["Agraciada"], "Montevideo")
    for tag in ("name", "alt_name", "short_name", "official_name", "name:es", "loc_name"):
        assert f'["{tag}"="Agraciada"]' in query


def test_footways_and_steps_are_kept_out_of_the_query():
    query = overpass_query(["Agraciada"], "Montevideo")
    assert "footway" in query and "steps" in query and "platform" in query


def test_an_area_given_as_four_numbers_becomes_a_bounding_box():
    query = overpass_query(["Agraciada"], "-34.91, -56.21, -34.89, -56.18")
    assert "[bbox:-34.91,-56.21,-34.89,-56.18]" in query
    assert "area[" not in query and "way[highway]" in query


def test_a_quotation_mark_cannot_reach_the_query_unescaped():
    assert escape('the "big" one') == 'the \\"big\\" one'
    assert escape("back\\slash") == "back\\\\slash"
    assert '\\"' in overpass_query(['the "big" one'], "Montevideo")


def test_a_control_character_in_a_street_name_is_refused_outright():
    with pytest.raises(GeocodeError, match="control characters"):
        escape("Agraciada\nway[highway]")


# --- working the crossings out, offline ---------------------------------------


def test_two_streets_that_share_one_node_make_a_junction_there():
    streets = ways_by_street(ANSWER["elements"], ["Agraciada", "Freire"])
    places = node_places(ANSWER["elements"])
    found, why = junction_of(streets["agraciada"], streets["freire"], places)
    assert why == "" and found is not None
    assert (found.lat, found.lon) == (-34.9000, -56.2000)
    assert found.nodes == 1 and found.spread_m == 0.0 and found.touching


def test_a_street_is_recognised_by_any_of_the_names_the_map_gave_it():
    streets = ways_by_street(ANSWER["elements"], ["Agraciada"])
    assert list(streets) == ["agraciada"]  # casó por alt_name
    assert ways_by_street(ANSWER["elements"], ["AGRACIADA"]) == {"agraciada": [[10, 11, 12]]}
    assert ways_by_street(ANSWER["elements"], ["Ninguna"]) == {}


def test_rubbish_in_the_answer_is_ignored_rather_than_believed():
    junk = ["not a dict", {"type": "relation"}, {"type": "node", "id": "x", "lat": 1, "lon": 2}]
    assert ways_by_street(junk, ["A"]) == {}
    assert node_places(junk) == {}


def test_an_avenue_mapped_as_two_carriageways_makes_one_junction_not_two():
    # Two shared nodes 20 m apart: one crossing split by a median, not two crossings.
    elements = [
        way(1, "Bulevar", [10, 11]),
        way(2, "Solari", [10, 11, 12]),
        node(10, -34.90000, -56.20000),
        node(11, -34.90018, -56.20000),
        node(12, -34.90100, -56.20000),
    ]
    streets, places = ways_by_street(elements, ["Bulevar", "Solari"]), node_places(elements)
    found, why = junction_of(streets["bulevar"], streets["solari"], places)
    assert found is not None and why == ""
    assert found.nodes == 2 and found.lat == pytest.approx(-34.90009)
    assert found.spread_m == pytest.approx(10, abs=2)


def test_two_junctions_a_block_apart_are_refused_with_the_distance_named():
    elements = [
        way(1, "Circular", [10, 12]),
        way(2, "Solari", [10, 12]),
        node(10, -34.90000, -56.20000),
        node(12, -34.90200, -56.20000),
    ]
    streets, places = ways_by_street(elements, ["Circular", "Solari"]), node_places(elements)
    found, why = junction_of(streets["circular"], streets["solari"], places)
    assert found is None
    assert "cross in 2 places" in why and " m apart" in why


def test_streets_that_only_come_close_are_placed_between_them_and_marked():
    elements = [
        way(1, "Rambla", [10]),
        way(2, "Solari", [20]),
        node(10, -34.90000, -56.20000),
        node(20, -34.90018, -56.20000),
    ]
    streets, places = ways_by_street(elements, ["Rambla", "Solari"]), node_places(elements)
    found, why = junction_of(streets["rambla"], streets["solari"], places)
    assert found is not None and why == "" and not found.touching
    assert found.lat == pytest.approx(-34.90009)


def test_streets_that_never_come_close_are_reported_as_never_meeting():
    elements = [
        way(1, "Lejana", [10]),
        way(2, "Solari", [20]),
        node(10, -34.90000, -56.20000),
        node(20, -34.91000, -56.20000),
    ]
    streets, places = ways_by_street(elements, ["Lejana", "Solari"]), node_places(elements)
    found, why = junction_of(streets["lejana"], streets["solari"], places)
    assert found is None and "never meet" in why and "closest they come" in why


def test_a_street_the_area_does_not_have_says_so():
    places = node_places(ANSWER["elements"])
    found, why = junction_of([[10]], [], places)
    assert found is None and "not in the area" in why


def test_places_far_apart_land_in_different_clusters():
    near = [(-34.9000, -56.2000), (-34.90018, -56.2000)]
    far = [*near, (-34.9100, -56.2000)]
    assert len(clusters(near, 60.0)) == 1
    assert len(clusters(far, 60.0)) == 2


# --- what the walk says about it ----------------------------------------------


def stamps(*minutes):
    return [datetime(2026, 9, 5, 17, minute, tzinfo=TZ) for minute in minutes]


def crossings(count):
    return [Crossing(number, f"C{number}", "", 0) for number in range(1, count + 1)]


def test_a_stretch_nobody_could_have_walked_is_found():
    here = {1: (-34.9000, -56.2000), 2: (-34.9000, -56.1900)}  # unos 900 m
    wrong = implausible_stretches(crossings(2), here, stamps(0, 1))
    assert len(wrong) == 1 and wrong[0][2] > 10


def test_a_long_stop_between_two_crossings_is_not_taken_for_a_bad_lookup():
    here = {1: (-34.9000, -56.2000), 2: (-34.9000, -56.19995)}
    assert implausible_stretches(crossings(2), here, stamps(0, 10)) == []


def test_a_stretch_with_an_end_missing_or_no_time_at_all_is_simply_not_checked():
    here = {1: (-34.9000, -56.2000)}
    assert implausible_stretches(crossings(2), here, stamps(0, 1)) == []
    both = {1: (-34.9000, -56.2000), 2: (-34.9000, -56.1900)}
    assert implausible_stretches(crossings(2), both, stamps(5, 5)) == []


def test_the_two_walks_over_the_notebook_disagreeing_is_an_error_not_a_wrong_answer():
    with pytest.raises(GeocodeError, match="should not happen"):
        implausible_stretches(crossings(3), {}, stamps(0, 1))


def test_a_notebook_of_button_marks_has_no_times_of_its_own(tmp_path):
    path = notebook(tmp_path, "#1 A y B\n#2 C y D\n")
    assert crossing_times(path) == []
    marks = [
        (1, datetime(2026, 9, 5, 17, 0, tzinfo=TZ)),
        (2, datetime(2026, 9, 5, 17, 5, tzinfo=TZ)),
    ]
    assert crossing_times(path, marks) == [marks[0][1], marks[1][1]]


def test_the_marks_come_out_of_a_log(tmp_path):
    log = tmp_path / "paseo.jsonl"
    log.write_text('{"time": "2026-09-05T17:00:00-03:00", "event": "mark", "number": 1}\n')
    assert [number for number, _ in notebook_marks(log)] == [1]
    assert notebook_marks(None) == []


# --- the proxy ----------------------------------------------------------------


class FakeSocket:
    """A socket that records what it was told, and never opens anything."""

    def __init__(self, fail=None):
        self.fail, self.timeout, self.proxy, self.connected = fail, None, None, None

    def settimeout(self, seconds):
        self.timeout = seconds

    def set_proxy(self, kind, host, port, rdns=None, username=None, password=None):
        self.proxy = (kind, host, port, rdns, username, password)

    def connect(self, where):
        if self.fail is not None:
            raise self.fail
        self.connected = where


class FakeSocks:
    """Enough of PySocks to exercise the branch without installing it."""

    SOCKS5 = 2

    def __init__(self, fail=None):
        self.made = FakeSocket(fail)

    def socksocket(self):
        return self.made


def test_a_socks_proxy_url_is_parsed_into_its_parts():
    assert parse_proxy("socks5://127.0.0.1:9050") == Proxy("127.0.0.1", 9050)
    assert parse_proxy("socks5h://127.0.0.1:9150") == Proxy("127.0.0.1", 9150)
    assert parse_proxy("socks5://me:secret@host:1080") == Proxy("host", 1080, "me", "secret")


def test_a_proxy_that_is_not_socks5_is_refused_by_name():
    with pytest.raises(GeocodeError, match="only socks5"):
        parse_proxy("http://127.0.0.1:3128")
    with pytest.raises(GeocodeError, match="a bare host"):
        parse_proxy("127.0.0.1:9050")


def test_a_proxy_url_without_a_port_is_refused_rather_than_guessed_at():
    with pytest.raises(GeocodeError, match="both a host and a port"):
        parse_proxy("socks5://127.0.0.1")


def test_without_a_proxy_the_socket_goes_straight_to_the_endpoint():
    asked = {}

    def connect(where, timeout=None):
        asked.update(where=where, timeout=timeout)
        return "the socket"

    assert open_socket("overpass-api.de", 443, None, 30, connect=connect) == "the socket"
    assert asked == {"where": ("overpass-api.de", 443), "timeout": 30}


def test_the_socks_proxy_is_told_to_resolve_the_hostname_and_not_this_machine():
    # rdns=True is the whole point: resolving overpass-api.de here would announce
    # what is about to be asked even though the request itself goes over Tor.
    fake = FakeSocks()
    made = open_socket(
        "overpass-api.de",
        443,
        Proxy("127.0.0.1", 9050, "me", "secret"),
        30,
        connect=None,
        import_socks=lambda name: fake,
    )
    assert made is fake.made
    assert made.proxy == (FakeSocks.SOCKS5, "127.0.0.1", 9050, True, "me", "secret")
    assert made.connected == ("overpass-api.de", 443) and made.timeout == 30


def test_a_proxy_that_cannot_be_reached_fails_instead_of_connecting_directly():
    fake = FakeSocks(fail=ConnectionRefusedError("nobody home"))
    with pytest.raises(GeocodeError, match=r"proxy at 127\.0\.0\.1:9050 could not be used"):
        open_socket(
            "overpass-api.de",
            443,
            Proxy("127.0.0.1", 9050),
            30,
            connect=None,
            import_socks=lambda name: fake,
        )


def test_the_socks_extra_being_missing_says_how_to_install_it():
    def missing(name):
        raise ImportError(f"no module named {name}")

    with pytest.raises(GeocodeError, match="uv sync --extra socks"):
        open_socket(
            "overpass-api.de",
            443,
            Proxy("127.0.0.1", 9050),
            30,
            connect=None,
            import_socks=missing,
        )


def test_the_suite_refuses_to_open_a_real_socket():
    from conftest import RealRadioCall

    with pytest.raises(RealRadioCall, match="no test may go out to the network"):
        geocode.open_socket("overpass-api.de", 443)


def test_no_proxy_environment_variable_can_redirect_the_lookup(monkeypatch):
    # urllib would read these through getproxies(). http.client never does, which
    # is why it is what this module uses.
    for name in ("ALL_PROXY", "HTTPS_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.setenv(name, "socks5://10.0.0.1:1080")
    # Checked on the imports themselves and not on the text, since the module
    # explains in prose why urllib was turned down.
    tree = ast.parse(Path(geocode.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for found in ast.walk(tree)
        if isinstance(found, ast.Import)
        for alias in found.names
    }
    assert "urllib.request" not in imported and "urllib.parse" in imported
    asked = {}
    open_socket("overpass-api.de", 443, None, 30, connect=lambda w, timeout=None: asked.update(w=w))
    assert asked["w"] == ("overpass-api.de", 443)


def test_the_connection_verifies_the_certificate_against_the_endpoints_name(monkeypatch):
    link = geocode.OverpassConnection("overpass-api.de")
    assert (
        link.context.check_hostname and link.context.verify_mode == __import__("ssl").CERT_REQUIRED
    )

    class FakeContext:
        def __init__(self):
            self.asked = None

        def wrap_socket(self, sock, server_hostname=None):
            self.asked = (sock, server_hostname)
            return "the tls socket"

    context = FakeContext()
    link = geocode.OverpassConnection("overpass-api.de", proxy=Proxy("h", 1), context=context)
    monkeypatch.setattr(geocode, "open_socket", lambda *a: "the plain socket")
    link.connect()
    assert context.asked == ("the plain socket", "overpass-api.de")
    assert link.sock == "the tls socket"


# --- the answer ---------------------------------------------------------------


class FakeAnswer:
    def __init__(self, status=200, body=b"{}", location=None):
        self.status, self.body, self.location = status, body, location

    def read(self):
        return self.body

    def getheader(self, name):
        return self.location


class FakeLink:
    def __init__(self, answer=None, fail=None):
        self.answer, self.fail, self.sent, self.closed = answer, fail, None, False

    def __call__(self, host, port=443, *, proxy=None, timeout=None):
        self.opened = (host, port, proxy, timeout)
        return self

    def request(self, method, path, body=None, headers=None):
        if self.fail is not None:
            raise self.fail
        self.sent = (method, path, body, headers)

    def getresponse(self):
        return self.answer

    def close(self):
        self.closed = True


def test_a_good_answer_comes_back_parsed_and_the_query_goes_in_the_body():
    link = FakeLink(FakeAnswer(body=json.dumps(ANSWER).encode()))
    assert post_overpass("[out:json];", connection=link) == ANSWER
    method, path, body, headers = link.sent
    assert method == "POST" and path == "/api/interpreter"
    assert b"data=" in body and b"out%3Ajson" in body
    assert headers["User-Agent"].startswith("enodia/")
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert link.opened[:2] == ("overpass-api.de", 443) and link.closed


def test_a_partial_answer_carrying_a_remark_is_refused_rather_than_used():
    # 200, valid JSON, half the streets, and a note saying so. Crossings worked
    # out from it would look exactly like real ones.
    body = json.dumps({"remark": "runtime error: Query timed out", "elements": []}).encode()
    with pytest.raises(GeocodeError, match="incomplete and was not used"):
        post_overpass("q", connection=FakeLink(FakeAnswer(body=body)))


def test_a_redirect_is_refused_because_nobody_asked_for_the_other_host():
    link = FakeLink(FakeAnswer(status=302, location="https://elsewhere.example/"))
    with pytest.raises(GeocodeError, match=r"redirected to https://elsewhere\.example/"):
        post_overpass("q", connection=link)


def test_a_rate_limited_answer_names_the_status_it_got():
    with pytest.raises(GeocodeError, match="answered 429"):
        post_overpass("q", connection=FakeLink(FakeAnswer(status=429, body=b"slow down")))


def test_a_body_that_is_not_json_is_reported_with_what_it_said():
    with pytest.raises(GeocodeError, match="not JSON"):
        post_overpass("q", connection=FakeLink(FakeAnswer(body=b"<html>nope</html>")))
    with pytest.raises(GeocodeError, match="not an Overpass result"):
        post_overpass("q", connection=FakeLink(FakeAnswer(body=b"[1, 2]")))


def test_a_connection_that_falls_over_is_an_error_and_not_a_traceback():
    link = FakeLink(fail=TimeoutError("timed out"))
    with pytest.raises(GeocodeError, match="timed out"):
        post_overpass("q", connection=link)
    assert link.closed


def test_the_endpoint_has_to_be_https():
    with pytest.raises(GeocodeError, match="https URL"):
        post_overpass("q", url="http://overpass-api.de/api/interpreter")


# --- the whole thing ----------------------------------------------------------


WALK = (
    "# domingo\n"
    "17:00:00 Agraciada y Freire\n"
    "17:02:00 Agraciada y Solari   # se me pasó\n"
    "17:04:00 Plaza Independencia\n"
    "17:06:00 Freire y Solari @ -34.9000, -56.1900\n"
)


def test_a_notebook_is_looked_up_in_one_request_and_written_beside_itself(tmp_path):
    seen = {}
    path = notebook(tmp_path, WALK)
    result = geocode_notebook(path, "Montevideo", fetch=answering(seen=seen))
    target = geocoded_path(path)
    assert target.name == "libreta.geo.txt"
    assert write_geocoded(path, target, result) == 2

    assert target.read_text(encoding="utf-8").splitlines() == [
        "# domingo",
        "17:00:00 Agraciada y Freire @ -34.900000, -56.200000",
        "17:02:00 Agraciada y Solari @ -34.901000, -56.198000   # se me pasó",
        "17:04:00 Plaza Independencia",
        "17:06:00 Freire y Solari @ -34.9000, -56.1900",
    ]
    assert path.read_text(encoding="utf-8") == WALK  # el original, intacto
    assert seen["query"].count("way(area.a)") == 3 * len(geocode.NAME_TAGS)


def test_the_geocoded_notebook_reads_back_with_the_coordinates_it_was_given(tmp_path):
    from enodia.reconcile import read_notebook

    path = notebook(tmp_path, WALK)
    result = geocode_notebook(path, "Montevideo", fetch=answering())
    target = geocoded_path(path)
    write_geocoded(path, target, result)
    points = read_notebook(target, datetime(2026, 9, 5, tzinfo=TZ).date(), TZ)
    assert points[0].coordinates == (-34.9, -56.2)
    assert points[2].coordinates is None  # Plaza Independencia sigue sin nada


def test_a_notebook_with_no_suffix_still_gets_a_geo_file_beside_it(tmp_path):
    assert geocoded_path(tmp_path / "libreta").name == "libreta.geo"
    assert geocoded_path(tmp_path / "libreta.2026.txt").name == "libreta.2026.geo.txt"


def test_a_geocoded_notebook_that_already_exists_is_refused_rather_than_overwritten(tmp_path):
    path = notebook(tmp_path, WALK)
    result = geocode_notebook(path, "Montevideo", fetch=answering())
    target = geocoded_path(path)
    target.write_text("lo que arreglé a mano\n", encoding="utf-8")
    with pytest.raises(GeocodeError, match="already there and was left alone"):
        write_geocoded(path, target, result)
    assert target.read_text(encoding="utf-8") == "lo que arreglé a mano\n"


def test_line_endings_and_a_missing_last_newline_survive_the_rewrite(tmp_path):
    path = notebook(tmp_path, "17:00:00 Agraciada y Freire\r\n17:02:00 Agraciada y Solari")
    result = geocode_notebook(path, "Montevideo", fetch=answering())
    target = geocoded_path(path)
    write_geocoded(path, target, result)
    with target.open(encoding="utf-8", newline="") as written:
        text = written.read()
    assert text.endswith("-56.198000")  # sigue sin salto final
    assert text.count("\r\n") == 1  # y el final de línea de Windows quedó como estaba


def test_a_notebook_with_no_crossings_at_all_is_an_error(tmp_path):
    with pytest.raises(GeocodeError, match="no crossings found"):
        geocode_notebook(notebook(tmp_path, "# solo un comentario\n"), "Montevideo")


def test_a_notebook_whose_lines_are_all_placed_asks_for_nothing(tmp_path):
    path = notebook(tmp_path, "17:00 A y B @ -34.9, -56.2\n17:02 C y D @ -34.9, -56.19\n")

    def refuse(*args, **kwargs):  # pragma: no cover - nunca debería llamarse
        raise AssertionError("nothing left to ask about")

    result = geocode_notebook(path, "Montevideo", fetch=refuse)
    assert result.streets == 0 and result.found == {}


def test_a_street_the_area_does_not_have_is_named_by_how_it_was_written(tmp_path):
    path = notebook(tmp_path, "17:00:00 Agraciada y Inexistente\n17:02:00 Agraciada y Freire\n")
    result = geocode_notebook(path, "Montevideo", fetch=answering())
    assert result.missing == ["Inexistente"]
    assert any("not in the area" in one.reason for one in result.skipped)
    report = format_geocoding(result, geocoded_path(path), 1)
    assert "Streets the area does not have under that name:\n  Inexistente" in report
    assert "or write the name the map uses" in report


def test_the_same_corner_written_twice_is_looked_up_once_and_written_both_times(tmp_path):
    path = notebook(
        tmp_path,
        "17:00:00 Agraciada y Freire\n17:02:00 Agraciada y Solari\n17:04:00 freire Y agraciada\n",
    )
    result = geocode_notebook(path, "Montevideo", fetch=answering())
    assert len(result.found) == 3
    assert result.found[1] == result.found[3]  # la misma esquina, escrita al revés
    report = format_geocoding(result, geocoded_path(path), 3)
    assert "written both ways round here" in report
    assert '"Agraciada y Freire" and "freire Y agraciada"' in report


def test_a_corner_the_walk_says_could_not_have_been_reached_is_dropped(tmp_path):
    # Freire y Solari se resolvería a 900 m de la anterior, en dos minutos.
    far = {
        "elements": [
            way(1, "Agraciada", [10, 12]),
            way(2, "Freire", [10]),
            way(3, "Solari", [12]),
            node(10, -34.9000, -56.2000),
            node(12, -34.9000, -56.1900),
        ]
    }
    path = notebook(tmp_path, "17:00:00 Agraciada y Freire\n17:00:10 Agraciada y Solari\n")
    result = geocode_notebook(path, "Montevideo", fetch=answering(far))
    assert result.found == {}  # ninguno de los dos, porque no se sabe cuál está mal
    assert any("m/s" in one.reason for one in result.skipped)


def test_a_notebook_of_button_marks_says_the_walk_could_not_check_it(tmp_path):
    path = notebook(tmp_path, "#1 Agraciada y Freire\n#2 Agraciada y Solari\n")
    result = geocode_notebook(path, "Montevideo", fetch=answering())
    assert len(result.found) == 2 and not result.walk_known
    report = format_geocoding(result, geocoded_path(path), 2)
    assert "carries no times of its own" in report and "--marks" in report


def test_the_marks_log_gives_an_untimed_notebook_its_times(tmp_path):
    path = notebook(tmp_path, "#1 Agraciada y Freire\n#2 Agraciada y Solari\n")
    marks = [
        (1, datetime(2026, 9, 5, 17, 0, tzinfo=TZ)),
        (2, datetime(2026, 9, 5, 17, 2, tzinfo=TZ)),
    ]
    result = geocode_notebook(path, "Montevideo", marks=marks, fetch=answering())
    assert result.walk_known and result.checked == 1


def test_an_approximate_corner_is_counted_and_said_so(tmp_path):
    near = {
        "elements": [
            way(1, "Agraciada", [10]),
            way(2, "Freire", [20]),
            node(10, -34.90000, -56.20000),
            node(20, -34.90018, -56.20000),
        ]
    }
    path = notebook(tmp_path, "17:00:00 Agraciada y Freire\n17:02:00 Agraciada y Freire\n")
    result = geocode_notebook(path, "Montevideo", fetch=answering(near))
    assert result.approximate == 2
    assert "where they touch" in format_geocoding(result, Path("x"), 2)


def test_the_report_names_every_line_it_left_alone_and_why(tmp_path):
    path = notebook(tmp_path, WALK)
    result = geocode_notebook(path, "Montevideo", fetch=answering())
    report = format_geocoding(result, geocoded_path(path), 2)
    assert "4 crossings, 3 streets asked for in one request." in report
    assert "2 lines gained coordinates" in report
    assert "line 4, Plaza Independencia: not a corner of two streets" in report
    assert "1 stretch checked against the pace" in report


def test_a_bounding_box_is_drawn_around_what_was_placed():
    from enodia.geocode import around, buildings_query

    box = around([(-34.9000, -56.2000), (-34.9010, -56.1980)], margin_m=0.0)
    assert box == "-34.901000,-56.200000,-34.900000,-56.198000"
    padded = around([(-34.9000, -56.2000), (-34.9010, -56.1980)])
    assert padded != box  # el margen deja aire alrededor
    query = buildings_query(box)
    assert f"[bbox:{box}]" in query and "way[building];" in query and "out geom;" in query


def test_the_building_outlines_come_out_of_an_answer_with_geometry():
    from enodia.geocode import drawn_buildings

    elements = [
        {
            "type": "way",
            "geometry": [
                {"lat": -34.9, "lon": -56.2},
                {"lat": -34.9, "lon": -56.199},
                {"lat": -34.8995, "lon": -56.199},
            ],
        },
        {"type": "way", "geometry": [{"lat": -34.9, "lon": -56.2}]},  # dos puntos no son manzana
        {"type": "way"},
        {"type": "node", "id": 1},
        "ni siquiera un dict",
    ]
    (only,) = drawn_buildings(elements)
    assert len(only) == 3 and only[0] == (-34.9, -56.2)


def test_the_buildings_are_a_second_request_and_only_when_asked_for(tmp_path):
    asked = []

    def counting(query, url=None, proxy=None):
        asked.append(query)
        if "way[building]" in query:
            return {
                "elements": [
                    {
                        "type": "way",
                        "geometry": [
                            {"lat": -34.9, "lon": -56.2},
                            {"lat": -34.9, "lon": -56.199},
                            {"lat": -34.8995, "lon": -56.199},
                        ],
                    }
                ]
            }
        return ANSWER

    path = notebook(tmp_path, WALK)
    plain = geocode_notebook(path, "Montevideo", fetch=counting)
    assert len(asked) == 1 and plain.buildings == []

    asked.clear()
    withblocks = geocode_notebook(path, "Montevideo", buildings=True, fetch=counting)
    assert len(asked) == 2 and "way[building]" in asked[1]
    assert len(withblocks.buildings) == 1


def test_a_junction_is_one_junction_however_its_nodes_came_out_of_the_answer():
    # Three nodes in a line fifty metres apart, at a sixty metre threshold, are
    # one junction split by a median. Dropping each into the first group it
    # touches and stopping there gives one group for A, B, C and two for A, C,
    # B, because B joins A and nothing goes back to merge in C. Which order they
    # arrive in is OpenStreetMap's business, not the street's.
    a, b, c = (-34.90000, -56.2), (-34.90045, -56.2), (-34.90090, -56.2)
    for order in ([a, b, c], [a, c, b], [c, a, b], [b, c, a]):
        assert len(clusters(order, 60.0)) == 1
    assert len(clusters([a, (-34.9050, -56.2)], 60.0)) == 2


def test_a_proxy_port_that_is_not_a_number_is_an_error_and_not_a_traceback():
    with pytest.raises(GeocodeError, match="Port could not be cast"):
        parse_proxy("socks5://localhost:nope")


def test_a_notebook_that_cannot_be_read_never_reaches_the_network(tmp_path):
    # The one thing here that leaves the machine is the request, and a notebook
    # with a line that says 25:99 is not going to become readable after one.
    # Reading every failure as "no times known" let it go out anyway.
    nb = tmp_path / "libreta.txt"
    nb.write_text("25:99 Agraciada y Freire\n17:10 Agraciada y Solari\n")
    asked = []

    def fetch(query, url=None, proxy=None):
        asked.append(query)
        return {"elements": []}

    with pytest.raises(NotebookError, match="is not a time"):
        geocode_notebook(nb, "Montevideo", fetch=fetch)
    assert asked == []


def test_a_notebook_with_no_times_of_its_own_is_geocoded_all_the_same(tmp_path):
    # And the other half: a notebook written with the headset button carries no
    # times, which is ordinary, and the lookup runs with the walk unchecked.
    nb = tmp_path / "libreta.txt"
    nb.write_text("Agraciada y Freire\nAgraciada y Solari\n")
    asked = []

    def fetch(query, url=None, proxy=None):
        asked.append(query)
        return {"elements": []}

    result = geocode_notebook(nb, "Montevideo", fetch=fetch)
    assert len(asked) == 1
    assert result.walk_known is False


def test_marks_that_do_not_answer_the_notebook_never_reach_the_network(tmp_path):
    # Giving --marks and a notebook the log cannot answer is a mismatch, often
    # the wrong walk of the log picked, and reading it as "the times are
    # unknown" sent the one request that leaves this machine anyway.
    nb = tmp_path / "libreta.txt"
    nb.write_text("#7 Agraciada y Freire\n#8 Agraciada y Solari\n")
    asked = []

    def fetch(query, url=None, proxy=None):
        asked.append(query)
        return {"elements": []}

    marks = [
        (1, datetime(2026, 9, 17, 18, tzinfo=timezone(timedelta(hours=-3)))),
        (2, datetime(2026, 9, 17, 18, 10, tzinfo=timezone(timedelta(hours=-3)))),
    ]
    with pytest.raises(NotebookError, match="the log has no mark #7"):
        geocode_notebook(nb, "Montevideo", marks=marks, fetch=fetch)
    assert asked == []
