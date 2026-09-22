"""The checked-in sample is executable documentation, not a hand-written screenshot."""

import importlib.util
from datetime import date, timedelta, timezone
from pathlib import Path

import pytest

from enodia import cli
from enodia.reconcile import read_notebook, route_stretches
from enodia.streets import read_streets

SAMPLES = Path(__file__).parents[1] / "samples"
TZ = timezone(timedelta(hours=-3))
STREETS = ["--streets", str(SAMPLES / "rivera-streets.jsonl")]
OUTINGS = ("rivera-2026-09-14", "rivera-2026-09-17")


def generator():
    """The script beside the sample, loaded by path: it is not part of the package."""
    spec = importlib.util.spec_from_file_location("make_rivera", SAMPLES / "make_rivera.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rivera_sample_uses_the_verified_osm_geometry():
    # Four real corners of Avenida Rivera, as --geocode wrote them, and the
    # three blocks between them measured along the street as OpenStreetMap
    # draws it, not along the chord.
    waypoints = read_notebook(SAMPLES / "rivera-2026-09-14.txt", date(2026, 9, 14), TZ)
    assert [(one.name, one.lat, one.lon) for one in waypoints] == [
        ("Rivera y Avenida Doctor Francisco Soca", -34.903126, -56.156023),
        ("Rivera y Brito del Pino", -34.903131, -56.157581),
        ("Rivera y Simón Bolívar", -34.903101, -56.159047),
        ("Rivera y Obligado", -34.903011, -56.160359),
    ]
    streets = read_streets(SAMPLES / "rivera-streets.jsonl")
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
        assert one.read_bytes() == (SAMPLES / one.name).read_bytes(), one.name


def build_map(target):
    """The map of both outings, the way samples/README.md builds it."""
    for outing in OUTINGS:
        command = ["--map-add", str(SAMPLES / f"{outing}.jsonl"), str(SAMPLES / f"{outing}.txt")]
        assert cli.main([*command, "--map", str(target), *STREETS]) == 0


def test_rivera_sample_rebuilds_the_readme_location(tmp_path, capsys):
    mapa = tmp_path / "map.jsonl"
    build_map(mapa)
    capsys.readouterr()
    query = str(SAMPLES / "rivera-query.jsonl")
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
    log, notebook = str(SAMPLES / f"{outing}.jsonl"), str(SAMPLES / f"{outing}.txt")
    return ["--reconcile", log, notebook, *STREETS, *extra]


def test_the_sample_artifacts_are_still_what_the_commands_produce(tmp_path, capsys):
    # A checked-in artifact nobody regenerates is indistinguishable from one
    # written by hand to agree with the documentation, and it drifts silently:
    # change a line of the report and the file beside it goes on describing
    # output the code no longer produces, which is the failure this whole
    # project keeps finding in other forms.
    assert cli.main(reconcile("rivera-2026-09-14", "--scans")) == 0
    assert capsys.readouterr().out == (SAMPLES / "rivera-report.txt").read_text(encoding="utf-8")

    # One run writes all three. Separate from the one above because each of
    # these flags adds a line to the report naming the file it wrote.
    made = {name: tmp_path / name for name in ("networks.csv", "walk.geojson", "plan.svg")}
    flags = ["--csv", str(made["networks.csv"]), "--geojson", str(made["walk.geojson"])]
    assert cli.main(reconcile("rivera-2026-09-14", *flags, "--svg", str(made["plan.svg"]))) == 0
    capsys.readouterr()
    for name, written in made.items():
        beside = SAMPLES / f"rivera-{name}"
        assert written.read_text(encoding="utf-8") == beside.read_text(encoding="utf-8"), beside

    assert cli.main(reconcile("rivera-2026-09-14", "--check-pace")) == 0
    pace = (SAMPLES / "rivera-pace-check.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == pace
    assert cli.main(reconcile("rivera-2026-09-17", "--check-passes")) == 0
    passes = (SAMPLES / "rivera-pass-check.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == passes

    assert cli.main(["--check-map", "--map", str(SAMPLES / "rivera-map.jsonl")]) == 0
    checked = (SAMPLES / "rivera-map-check.txt").read_text(encoding="utf-8")
    assert capsys.readouterr().out == checked


def test_the_sample_map_is_what_the_two_outings_build(tmp_path):
    # The map is checked in so that --locate can be run against it without
    # building anything first, which makes it the one artifact most able to
    # disagree with the logs it claims to come from.
    mapa = tmp_path / "map.jsonl"
    build_map(mapa)
    built = sorted(mapa.read_text(encoding="utf-8").splitlines())
    kept = sorted((SAMPLES / "rivera-map.jsonl").read_text(encoding="utf-8").splitlines())
    assert built == kept
