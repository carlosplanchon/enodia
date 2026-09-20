"""The checked-in sample is executable documentation, not a hand-written screenshot."""

from pathlib import Path

import pytest

from enodia import cli
from enodia.streets import line_length_m, read_streets

SAMPLES = Path(__file__).parents[1] / "samples"


def test_agraciada_sample_uses_the_verified_osm_geometry():
    (street,) = read_streets(SAMPLES / "agraciada-streets.jsonl").streets
    assert street.name == "Avenida Agraciada"
    assert street.line[0] == (-34.8794754, -56.1959854)
    assert street.line[-1] == (-34.8807492, -56.1955157)
    assert line_length_m(street.line) == pytest.approx(147.98, abs=0.02)


def test_agraciada_sample_rebuilds_the_readme_location(tmp_path, capsys):
    mapa = tmp_path / "map.jsonl"
    common = ["--streets", str(SAMPLES / "agraciada-streets.jsonl"), "--pace", "clock"]
    for day in (14, 17):
        assert (
            cli.main(
                [
                    "--map-add",
                    str(SAMPLES / f"agraciada-2026-09-{day}.jsonl"),
                    str(SAMPLES / f"agraciada-2026-09-{day}.txt"),
                    "--map",
                    str(mapa),
                    *common,
                ]
            )
            == 0
        )
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--locate",
                str(SAMPLES / "agraciada-query.jsonl"),
                "--map",
                str(mapa),
                "--voice",
                "none",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert (
        'between "Avenida Agraciada y Doctor Salvador García Pintos" and '
        '"Avenida Agraciada y San Fructuoso", 63% of the way' in out
    )
    assert "around [-34.88028, -56.19569]" in out
    assert "5 fingerprints agree, best similarity 100%, spread 11% of the stretch (17 m)" in out


def reconcile(*extra):
    """The walk the checked-in artifacts are all made from."""
    return [
        "--reconcile",
        str(SAMPLES / "agraciada-2026-09-14.jsonl"),
        str(SAMPLES / "agraciada-2026-09-14.txt"),
        "--streets",
        str(SAMPLES / "agraciada-streets.jsonl"),
        "--pace",
        "clock",
        *extra,
    ]


def test_the_sample_artifacts_are_still_what_the_commands_produce(tmp_path, capsys):
    # A checked-in artifact nobody regenerates is indistinguishable from one
    # written by hand to agree with the documentation, and it drifts silently:
    # change a line of the report and the file beside it goes on describing
    # output the code no longer produces, which is the failure this whole
    # project keeps finding in other forms.
    assert cli.main(reconcile("--scans")) == 0
    printed = capsys.readouterr().out
    assert printed == (SAMPLES / "agraciada-report.txt").read_text(encoding="utf-8")

    # One run writes all three. Separate from the one above because each of
    # these flags adds a line to the report naming the file it wrote.
    made = {name: tmp_path / name for name in ("networks.csv", "walk.geojson", "plan.svg")}
    assert (
        cli.main(
            reconcile(
                "--csv",
                str(made["networks.csv"]),
                "--geojson",
                str(made["walk.geojson"]),
                "--svg",
                str(made["plan.svg"]),
            )
        )
        == 0
    )
    capsys.readouterr()
    for name, written in made.items():
        beside = SAMPLES / f"agraciada-{name}"
        assert written.read_text(encoding="utf-8") == beside.read_text(encoding="utf-8"), beside

    assert cli.main(["--check-map", "--map", str(SAMPLES / "agraciada-map.jsonl")]) == 0
    checked = capsys.readouterr().out
    assert checked == (SAMPLES / "agraciada-map-check.txt").read_text(encoding="utf-8")


def test_the_sample_map_is_what_the_two_passes_build(tmp_path):
    # The map is checked in so that --locate can be run against it without
    # building anything first, which makes it the one artifact most able to
    # disagree with the logs it claims to come from.
    mapa = tmp_path / "map.jsonl"
    for day in (14, 17):
        assert (
            cli.main(
                [
                    "--map-add",
                    str(SAMPLES / f"agraciada-2026-09-{day}.jsonl"),
                    str(SAMPLES / f"agraciada-2026-09-{day}.txt"),
                    "--map",
                    str(mapa),
                    "--streets",
                    str(SAMPLES / "agraciada-streets.jsonl"),
                    "--pace",
                    "clock",
                ]
            )
            == 0
        )
    built = sorted(mapa.read_text(encoding="utf-8").splitlines())
    kept = sorted((SAMPLES / "agraciada-map.jsonl").read_text(encoding="utf-8").splitlines())
    assert built == kept
