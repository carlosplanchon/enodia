"""The checked-in sample is executable documentation, not a hand-written screenshot."""

import importlib.util
import json
import re
from datetime import date, timedelta, timezone
from pathlib import Path

import pytest
from conftest import slow

from enodia import cli
from enodia.reconcile import read_notebook, route_stretches
from enodia.streets import read_streets

SAMPLES = Path(__file__).parents[1] / "samples"
MONTEVIDEO = SAMPLES / "synthetic_montevideo"
DOLORES = SAMPLES / "dolores"
TZ = timezone(timedelta(hours=-3))
STREETS = ["--streets", str(MONTEVIDEO / "rivera-streets.jsonl")]
OUTINGS = ("rivera-2026-09-14", "rivera-2026-09-17")


def generator():
    """The script beside the sample, loaded by path: it is not part of the package."""
    spec = importlib.util.spec_from_file_location("make_rivera", MONTEVIDEO / "make_rivera.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rivera_sample_uses_the_verified_osm_geometry():
    # Four real corners of Avenida Rivera, as --geocode wrote them, and the
    # three blocks between them measured along the street as OpenStreetMap
    # draws it, not along the chord.
    waypoints = read_notebook(MONTEVIDEO / "rivera-2026-09-14.txt", date(2026, 9, 14), TZ)
    assert [(one.name, one.lat, one.lon) for one in waypoints] == [
        ("Rivera y Avenida Doctor Francisco Soca", -34.903126, -56.156023),
        ("Rivera y Brito del Pino", -34.903131, -56.157581),
        ("Rivera y Simón Bolívar", -34.903101, -56.159047),
        ("Rivera y Obligado", -34.903011, -56.160359),
    ]
    streets = read_streets(MONTEVIDEO / "rivera-streets.jsonl")
    lengths = [stretch.length_m for stretch in route_stretches(waypoints, streets)]
    assert lengths == pytest.approx([142.14, 133.79, 120.17], abs=0.1)


def test_the_sample_logs_are_what_the_generator_writes(tmp_path):
    # The radios are invented, and the script that invents them is checked in,
    # so the logs can be made again and compared. A log nobody can regenerate
    # is indistinguishable from one written by hand to agree with the docs.
    written = generator().write_sample(tmp_path)
    assert [one.name for one in written] == [
        "rivera-2026-09-14.jsonl",
        "rivera-2026-09-14.txt",
        "rivera-2026-09-17.jsonl",
        "rivera-2026-09-17.txt",
        "rivera-query.jsonl",
    ]
    for one in written:
        assert one.read_bytes() == (MONTEVIDEO / one.name).read_bytes(), one.name


def build_map(target):
    """The map of both outings, the way samples/synthetic_montevideo/README.md builds it."""
    for outing in OUTINGS:
        log, notebook = MONTEVIDEO / f"{outing}.jsonl", MONTEVIDEO / f"{outing}.txt"
        command = ["--map-add", str(log), str(notebook)]
        assert cli.main([*command, "--map", str(target), *STREETS]) == 0


def test_rivera_sample_rebuilds_the_readme_location(tmp_path, capsys):
    mapa = tmp_path / "map.jsonl"
    build_map(mapa)
    capsys.readouterr()
    query = str(MONTEVIDEO / "rivera-query.jsonl")
    assert cli.main(["--locate", query, "--map", str(mapa), "--voice", "none"]) == 0
    out = capsys.readouterr().out
    assert (
        'You are between "Rivera y Brito del Pino" and "Rivera y Simón Bolívar", 53% of the way'
        in out
    )
    assert "around [-34.90311, -56.15836]" in out
    assert "2 walks agree, best similarity 89%, spread 0% of the stretch (1 m)" in out
    assert "from evidence last gathered 2026-09-17 17:02" in out


def reconcile(outing, *extra):
    log, notebook = str(MONTEVIDEO / f"{outing}.jsonl"), str(MONTEVIDEO / f"{outing}.txt")
    return ["--reconcile", log, notebook, *STREETS, *extra]


def test_the_sample_artifacts_are_still_what_the_commands_produce(tmp_path, capsys):
    # A checked-in artifact nobody regenerates is indistinguishable from one
    # written by hand to agree with the documentation, and it drifts silently:
    # change a line of the report and the file beside it goes on describing
    # output the code no longer produces, which is the failure this whole
    # project keeps finding in other forms.
    assert cli.main(reconcile("rivera-2026-09-14", "--scans")) == 0
    assert capsys.readouterr().out == (MONTEVIDEO / "rivera-report.txt").read_text(encoding="utf-8")

    # One run writes all three. Separate from the one above because each of
    # these flags adds a line to the report naming the file it wrote.
    made = {name: tmp_path / name for name in ("networks.csv", "walk.geojson", "plan.svg")}
    flags = ["--csv", str(made["networks.csv"]), "--geojson", str(made["walk.geojson"])]
    assert cli.main(reconcile("rivera-2026-09-14", *flags, "--svg", str(made["plan.svg"]))) == 0
    capsys.readouterr()
    for name, written in made.items():
        beside = MONTEVIDEO / f"rivera-{name}"
        assert written.read_text(encoding="utf-8") == beside.read_text(encoding="utf-8"), beside

    assert cli.main(reconcile("rivera-2026-09-14", "--check-pace")) == 0
    pace = (MONTEVIDEO / "rivera-pace-check.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == pace
    assert cli.main(reconcile("rivera-2026-09-17", "--check-passes")) == 0
    passes = (MONTEVIDEO / "rivera-pass-check.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == passes

    assert cli.main(["--check-map", "--map", str(MONTEVIDEO / "rivera-map.jsonl")]) == 0
    checked = (MONTEVIDEO / "rivera-map-check.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == checked


def test_the_sample_map_is_what_the_two_outings_build(tmp_path):
    # The map is checked in so that --locate can be run against it without
    # building anything first, which makes it the one artifact most able to
    # disagree with the logs it claims to come from.
    mapa = tmp_path / "map.jsonl"
    build_map(mapa)
    built = sorted(mapa.read_text(encoding="utf-8").splitlines())
    kept = sorted((MONTEVIDEO / "rivera-map.jsonl").read_text(encoding="utf-8").splitlines())
    assert built == kept


# --- The Dolores outing, published ------------------------------------------------

DOLORES_STREETS = ["--streets", str(DOLORES / "dolores-streets.jsonl")]
DOLORES_FILES = [str(DOLORES / "dolores-outing.jsonl"), str(DOLORES / "dolores-notebook.txt")]


def test_the_real_outing_names_no_network_and_no_address():
    # Exported with --keep-places: the streets are real and the networks are
    # not. A name or an address that came through as it was would be somebody's
    # router published, so every one is checked against the shape a pseudonym
    # has, in the log and in the map built from it.
    for path in (DOLORES / "dolores-outing.jsonl", DOLORES / "dolores-map.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            for network in json.loads(line).get("networks", []):
                assert network["ssid"] == "" or re.fullmatch(r"ssid-[0-9a-f]{12}", network["ssid"])
                assert re.fullmatch(r"ap-[0-9a-f]{12}", network["bssid"]), network["bssid"]
    interfaces = {
        json.loads(line).get("interface")
        for line in (DOLORES / "dolores-outing.jsonl").read_text(encoding="utf-8").splitlines()
    }
    assert all(one is None or re.fullmatch(r"radio-[0-9a-f]{12}", one) for one in interfaces)


def test_the_real_outing_map_is_what_its_outing_builds(tmp_path):
    mapa = tmp_path / "map.jsonl"
    assert cli.main(["--map-add", *DOLORES_FILES, "--map", str(mapa), *DOLORES_STREETS]) == 0
    built = sorted(mapa.read_text(encoding="utf-8").splitlines())
    kept = sorted((DOLORES / "dolores-map.jsonl").read_text(encoding="utf-8").splitlines())
    assert built == kept


def dolores(*extra):
    return ["--reconcile", *DOLORES_FILES, *DOLORES_STREETS, *extra]


def test_the_real_outing_artifacts_are_what_the_commands_produce(tmp_path, capsys):
    # The report, the checks of the passes walked twice, and one run for the
    # plan, the table and the GeoJSON, all of them from the exported outing, as
    # the synthetic sample is held to its own.
    assert cli.main(dolores("--scans")) == 0
    report = (DOLORES / "dolores-report.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == report
    assert cli.main(dolores("--check-passes")) == 0
    passes = (DOLORES / "dolores-pass-check.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == passes
    made = {name: tmp_path / name for name in ("networks.csv", "walk.geojson", "plan.svg")}
    flags = ["--csv", str(made["networks.csv"]), "--geojson", str(made["walk.geojson"])]
    assert cli.main(dolores(*flags, "--svg", str(made["plan.svg"]))) == 0
    capsys.readouterr()
    for name, written in made.items():
        beside = DOLORES / f"dolores-{name}"
        assert written.read_text(encoding="utf-8") == beside.read_text(encoding="utf-8"), beside


@slow
def test_the_real_outing_pace_check_is_what_the_command_says(capsys):
    # Every middle crossing held out and found again, by movement and by the
    # clock: on this walk the clock won, which is what that file says.
    assert cli.main(dolores("--check-pace")) == 0
    pace = (DOLORES / "dolores-pace-check.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == pace


@slow
@pytest.mark.parametrize(
    ("flags", "artifact"),
    [([], "dolores-map-check.txt"), (["--along", "levels"], "dolores-map-check-levels.txt")],
)
def test_the_real_outing_map_check_is_what_the_command_says(flags, artifact, capsys):
    # The numbers the documentation quotes for the Dolores outing, which
    # were for a long time the ones nobody but its walker could reproduce.
    assert cli.main(["--check-map", "--map", str(DOLORES / "dolores-map.jsonl"), *flags]) == 0
    assert capsys.readouterr().out == (DOLORES / artifact).read_text(encoding="utf-8")
