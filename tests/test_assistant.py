"""Tests for the guided menu: the screens, the keys, and what it refuses to claim."""

import json
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from conftest import as_root

from enodia import assistant, cli
from enodia.assistant import (
    Outing,
    count,
    map_tokens,
    notebook_candidates,
    outing_summaries,
    run_assistant,
    with_flags,
)
from enodia.preflight import Check

TZ = timezone(timedelta(hours=-3))
READY = [Check("interfaces", "OK", "wlan0 (radio on)"), Check("battery", "OK", "84%, discharging")]


def scripted(*keys):
    """An `ask` that reads from a script and ends the way a terminal does.

    Running out raises EOFError, which is what `input` does at the end of stdin,
    so a test that forgets to quit ends instead of spinning forever.
    """
    answers = iter(keys)

    def ask(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    return ask


def walked(tmp_path, token="8d91f3ac", marks=2, notebook=True, scans=4):
    """A directory with one outing's log in it, and a notebook beside it."""
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    rows = [
        json.dumps(
            {
                "time": f"2026-09-17T17:{45 + minute}:00-03:00",
                "event": "scan",
                "cycle": minute,
                "outing": token,
                "networks": [
                    {
                        "ssid": f"Casa {minute}",
                        "bssid": f"aa:bb:cc:dd:ee:0{minute}",
                        "signal_dbm": -50 - minute,
                    }
                ],
            }
        )
        for minute in range(scans)
    ]
    rows += [
        json.dumps(
            {
                "time": f"2026-09-17T17:{46 + number}:30-03:00",
                "event": "mark",
                "number": number + 1,
                "outing": token,
            }
        )
        for number in range(marks)
    ]
    (logs / f"2026-09-17T17-45-0{len(token) % 10}.jsonl").write_text("\n".join(rows) + "\n")
    if notebook:
        (logs / "libreta.txt").write_text(
            "17:45:00 Agraciada y Freire\n17:48:00 Agraciada y Solari\n"
        )
    return logs


def records(token="new22222", marks=2, scans=3):
    """One walk's worth of records, as a log holds them."""
    rows = [
        json.dumps(
            {
                "time": f"2026-09-18T11:{minute:02d}:00-03:00",
                "event": "scan",
                "cycle": minute,
                "outing": token,
                "networks": [
                    {"ssid": "Casa", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -50 - minute}
                ],
            }
        )
        for minute in range(scans)
    ]
    rows += [
        json.dumps(
            {
                "time": f"2026-09-18T11:{30 + number}:00-03:00",
                "event": "mark",
                "number": number + 1,
                "outing": token,
            }
        )
        for number in range(marks)
    ]
    return "\n".join(rows) + "\n"


def walks_into_the_log(monkeypatch, token="new22222", marks=2):
    """A walk that writes what a walk writes, into the path it was handed."""

    def fake(args, log=None):
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(records(token, marks))
        print("(walked)")
        return 0

    monkeypatch.setattr(cli, "run_walk", fake)


def started(tmp_path, *keys, extra=(), machine=None, monkeypatch=None, say=None):
    """Run the assistant over a temporary directory, with those keys typed."""
    if monkeypatch is not None:
        monkeypatch.setattr(assistant, "machine_checks", lambda args: machine or READY)
    args = cli.build_parser().parse_args(
        [
            "--assistant",
            "--voice",
            "none",
            "--button",
            "off",
            "--dir",
            str(tmp_path / "logs"),
            "--map",
            str(tmp_path / "mapa.jsonl"),
            *extra,
        ]
    )
    return run_assistant(args, ask=scripted(*keys), say=say or print, isatty=lambda: True)


# --- getting in and out --------------------------------------------------------


def test_the_assistant_refuses_a_pipe_because_it_has_nothing_to_ask_on(capsys, tmp_path):
    # Reading the pipe anyway would make the menu's numbering an interface
    # nobody could ever change, and answering EOF with a clean exit 0 would make
    # a cron job that did nothing look exactly like one that worked.
    args = cli.build_parser().parse_args(["--assistant"])
    assert run_assistant(args, ask=scripted(), isatty=lambda: False) == 2
    out = capsys.readouterr().out
    assert "no terminal here to ask on" in out and "enodia --help" in out


def test_quitting_the_main_menu_leaves_with_nothing_done(capsys, tmp_path, monkeypatch):
    assert started(tmp_path, "q", monkeypatch=monkeypatch) == 0
    assert "ENODIA" in capsys.readouterr().out


def test_running_out_of_answers_ends_it_the_way_ctrl_d_does(capsys, tmp_path, monkeypatch):
    # Ctrl+D, or the input running out. Nothing more will be typed, so looping
    # on it would spin forever.
    assert started(tmp_path, monkeypatch=monkeypatch) == 0


def test_ctrl_c_at_a_prompt_returns_to_the_menu_and_does_not_end_the_program(
    capsys, tmp_path, monkeypatch
):
    monkeypatch.setattr(assistant, "machine_checks", lambda args: READY)
    interrupted = {"once": False}

    def ask(prompt):
        if not interrupted["once"]:
            interrupted["once"] = True
            raise KeyboardInterrupt
        return "q"

    args = cli.build_parser().parse_args(
        ["--assistant", "--voice", "none", "--button", "off", "--dir", str(tmp_path / "logs")]
    )
    assert run_assistant(args, ask=ask, isatty=lambda: True) == 0
    assert capsys.readouterr().out.count("ENODIA") == 2  # se volvió a dibujar


def test_an_unknown_key_asks_again_instead_of_guessing(capsys, tmp_path, monkeypatch):
    # Guessing is the one thing the rest of Enodia refuses to do, and a menu
    # that took the nearest key would be the same mistake with a keyboard.
    monkeypatch.setattr(assistant, "suggestion", lambda session: None)
    assert started(tmp_path, "zzz", "", "q", monkeypatch=monkeypatch) == 0
    out = capsys.readouterr().out
    assert "'zzz' is not one of these." in out
    assert "Nothing chosen." in out  # y un Enter sin sugerencia tampoco elige nada


# --- what it can say about an outing -------------------------------------------


def test_an_outing_is_described_by_what_its_log_actually_holds(tmp_path):
    logs = walked(tmp_path)
    (one,) = outing_summaries(sorted(logs.glob("*.jsonl")), set())
    assert one.token == "8d91f3ac" and one.scans == 4 and one.marks == 2
    assert "4 scans" in one.describe() and "2 marks" in one.describe()
    assert "on the map" not in one.describe()

    (also,) = outing_summaries(sorted(logs.glob("*.jsonl")), {"8d91f3ac"})
    assert also.on_map and "on the map" in also.describe()


@as_root
def test_a_log_that_cannot_be_read_is_listed_as_unreadable_and_not_skipped(tmp_path):
    # A file that is there and silent is worth seeing on the list. Dropping it
    # would be the menu deciding on its own that it is not yours.
    logs = walked(tmp_path)
    broken = logs / "roto.jsonl"
    broken.write_text("{}\n")
    broken.chmod(0o000)
    try:
        found = outing_summaries(sorted(logs.glob("*.jsonl")), set())
    finally:
        broken.chmod(0o600)
    assert any(one.unreadable and "cannot be read" in one.describe() for one in found)


def test_an_outing_with_no_time_in_it_says_so_rather_than_inventing_one(tmp_path):
    one = Outing(Path("x.jsonl"), "", None, None, 0, 0, False)
    assert "no time in it" in one.describe()
    assert one.name == "x"  # sin token, el nombre del archivo


@as_root
def test_the_map_is_read_by_the_token_after_the_slash_and_never_by_reconciling(tmp_path):
    mapa = tmp_path / "mapa.jsonl"
    mapa.write_text(
        json.dumps(
            {
                "from": "Alfa",
                "to": "Bravo",
                "fraction": 0.5,
                "outing": "2026-09-17T17:45:00-03:00/8d91f3ac",
                "networks": [],
            }
        )
        + "\n"
    )
    assert map_tokens(mapa) == {"8d91f3ac"}
    assert map_tokens(tmp_path / "no-esta.jsonl") == set()

    # A map that is there and shut is not a map with nothing in it, and every
    # rule that asks whether an outing is on it has to be told the difference.
    unreadable = tmp_path / "cerrado.jsonl"
    unreadable.write_text("{}\n")
    unreadable.chmod(0o000)
    try:
        assert map_tokens(unreadable) is None
    finally:
        unreadable.chmod(0o600)


def test_the_outings_screen_never_says_whether_an_outing_was_reconciled(
    capsys, tmp_path, monkeypatch
):
    # Nothing in Enodia records it. The honest neighbour is whether the outing
    # is on the map, and that is what the screen shows.
    walked(tmp_path)
    started(tmp_path, "7", "", "q", monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "is not written down anywhere" in out
    assert "reconciled" not in out.replace("has been reconciled is not written down", "")


# --- the notebook --------------------------------------------------------------


def test_a_notebook_is_offered_and_never_assumed(tmp_path):
    logs = walked(tmp_path)
    found = notebook_candidates(logs, tmp_path)
    assert [path.name for path, _ in found] == ["libreta.txt"]
    assert found[0][1] == 2  # dos cruces

    # A text file that is not a notebook is not offered, and the log never is.
    (logs / "vacio.txt").write_text("\n\n# solo comentarios\n")
    assert [path.name for path, _ in notebook_candidates(logs, tmp_path)] == ["libreta.txt"]


@as_root
def test_a_directory_that_cannot_be_read_holds_no_notebooks(tmp_path):
    logs = walked(tmp_path)
    shut = tmp_path / "cerrado"
    shut.mkdir()
    shut.chmod(0o000)
    try:
        assert [path.name for path, _ in notebook_candidates(logs, shut)] == ["libreta.txt"]
    finally:
        shut.chmod(0o700)


def test_a_file_that_cannot_be_read_as_a_notebook_is_passed_over(tmp_path, monkeypatch):
    logs = walked(tmp_path)

    def refuse(path):
        raise ValueError("no es texto")

    monkeypatch.setattr(assistant, "read_crossings", refuse)
    assert notebook_candidates(logs, tmp_path) == []


# --- the flags a screen builds -------------------------------------------------


def test_the_assistant_keeps_the_flags_it_was_started_with():
    args = cli.build_parser().parse_args(["--assistant", "-i", "wlan0", "--voice", "pico"])
    built = with_flags(args, reconcile=["a", "b"])
    assert built.interface == ["wlan0"] and built.voice == "pico"
    assert built.reconcile == ["a", "b"]


def test_an_override_the_parser_does_not_define_is_refused_rather_than_added():
    # Python will hang an attribute nobody reads on a namespace, and `ty` cannot
    # see through the keywords, so a typo would be a screen that silently does
    # the default thing forever.
    args = cli.build_parser().parse_args(["--assistant"])
    with pytest.raises(KeyError, match="not a flag the parser defines: recncile"):
        with_flags(args, recncile=["a", "b"])


def test_one_mark_is_not_one_marks():
    assert count(1, "mark") == "1 mark"
    assert count(4, "mark") == "4 marks"
    assert count(2, "crossing") == "2 crossings"


# --- the screens ---------------------------------------------------------------


def taken(monkeypatch, name):
    """Stand a runner in, and record the arguments the screen built for it."""
    seen = []

    def fake(args, *rest):
        seen.append(args)
        print(f"({name} ran)")
        return 0

    monkeypatch.setattr(cli, name, fake)
    return seen


def test_reconciling_from_the_menu_runs_the_same_function_as_the_flag(
    capsys, tmp_path, monkeypatch
):
    # One implementation of each command, and the menu is a way of filling it
    # in. A second code path here would go out of step with the flag's.
    logs = walked(tmp_path)
    seen = taken(monkeypatch, "run_reconcile")
    started(tmp_path, "3", "1", "1", "", "m", "q", monkeypatch=monkeypatch)
    (built,) = seen
    assert built.reconcile == [
        str(next(logs.glob("*.jsonl"))),
        str(logs / "libreta.txt"),
    ]
    assert built.scans is False and built.check_pace is False


def test_each_reconcile_choice_asks_for_a_different_report(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    seen = taken(monkeypatch, "run_reconcile")
    started(tmp_path, "3", "1", "1", "1", "2", "3", "m", "q", monkeypatch=monkeypatch)
    assert [one.scans for one in seen] == [True, False, False]
    assert [one.check_pace for one in seen] == [False, True, False]
    assert [one.check_passes for one in seen] == [False, False, True]


def test_adding_to_the_map_turns_off_every_command_it_is_not(capsys, tmp_path, monkeypatch):
    # `run_map` asks about --map-add before --locate, so a locate that left
    # map_add standing would quietly add to the map instead.
    logs = walked(tmp_path)
    seen = taken(monkeypatch, "run_map")
    started(tmp_path, "4", "1", "1", "m", "q", monkeypatch=monkeypatch)
    (built,) = seen
    assert built.map_add == [str(next(logs.glob("*.jsonl"))), str(logs / "libreta.txt")]
    assert built.locate is None and built.check_map is False


def test_locating_scans_now_or_reads_a_walk_and_never_adds_to_the_map(
    capsys, tmp_path, monkeypatch
):
    logs = walked(tmp_path)
    seen = taken(monkeypatch, "run_map")
    started(tmp_path, "5", "", "1", "1", "2", "m", "q", monkeypatch=monkeypatch)
    assert [one.locate for one in seen] == ["", str(next(logs.glob("*.jsonl"))), ""]
    assert [one.watch for one in seen] == [False, False, True]  # "2" follows along
    assert all(one.map_add is None and one.check_map is False for one in seen)


def test_locating_from_a_file_of_several_walks_reads_the_one_you_picked(
    capsys, tmp_path, monkeypatch
):
    # The same picker the other commands use. "The last scan of that log" would
    # quietly mean the last walk in it rather than the one you meant.
    walk = two_walks_in_one_file(tmp_path)
    monkeypatch.setattr(assistant, "machine_checks", lambda args: READY)
    seen = taken(monkeypatch, "run_map")
    args = cli.build_parser().parse_args(
        ["--assistant", "--voice", "none", "--button", "off", "--log", str(walk)]
    )
    run_assistant(args, ask=scripted("5", "1", "2", "m", "q"), isatty=lambda: True)
    assert seen[0].locate == str(walk) and seen[0].outing == "old11111"


def test_going_back_from_the_walk_picker_returns_to_the_locate_screen(
    capsys, tmp_path, monkeypatch
):
    walked(tmp_path)
    seen = taken(monkeypatch, "run_map")
    started(tmp_path, "5", "1", "b", "m", "q", monkeypatch=monkeypatch)
    assert seen == []


def test_locating_with_no_outings_to_pick_from_says_so(capsys, tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    seen = taken(monkeypatch, "run_map")
    started(tmp_path, "5", "1", "b", "m", "q", monkeypatch=monkeypatch)
    assert seen == []
    assert "NO OUTINGS" in capsys.readouterr().out


def test_a_map_of_one_outing_is_told_to_walk_again_rather_than_measured(
    capsys, tmp_path, monkeypatch
):
    # The same argument the README makes: a map of one walk recognises that
    # walk. There is nothing to hold out, so there is nothing to measure.
    walked(tmp_path)
    (tmp_path / "mapa.jsonl").write_text(
        json.dumps(
            {
                "from": "Alfa",
                "to": "Bravo",
                "fraction": 0.5,
                "outing": "2026-09-17T17:45:00-03:00/8d91f3ac",
                "networks": [],
            }
        )
        + "\n"
    )
    seen = taken(monkeypatch, "run_map")
    started(tmp_path, "6", "", "q", monkeypatch=monkeypatch)
    assert seen == []
    out = capsys.readouterr().out
    assert "NOTHING TO MEASURE YET" in out and "again on another day" in out


def test_a_map_of_two_outings_is_measured(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    (tmp_path / "mapa.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "from": "Alfa",
                    "to": "Bravo",
                    "fraction": 0.5,
                    "outing": f"2026-09-1{day}T17:45:00-03:00/{token}",
                    "networks": [],
                }
            )
            for day, token in ((7, "8d91f3ac"), (8, "c41d77e2"))
        )
        + "\n"
    )
    seen = taken(monkeypatch, "run_map")
    started(tmp_path, "6", "", "q", monkeypatch=monkeypatch)
    assert len(seen) == 1 and seen[0].check_map is True


def test_the_preflight_screen_runs_all_eight_checks(capsys, tmp_path, monkeypatch):
    seen = taken(monkeypatch, "run_preflight_command")
    started(tmp_path, "8", "", "q", monkeypatch=monkeypatch)
    assert len(seen) == 1


def test_picking_an_outing_where_there_are_none_says_so(capsys, tmp_path, monkeypatch):
    started(tmp_path, "3", "b", "q", monkeypatch=monkeypatch)
    assert "NO OUTINGS" in capsys.readouterr().out


def test_back_from_a_screen_returns_to_the_one_above(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    seen = taken(monkeypatch, "run_reconcile")
    started(tmp_path, "3", "b", "q", monkeypatch=monkeypatch)
    assert seen == []
    assert capsys.readouterr().out.count("ENODIA") == 2


def test_a_notebook_path_can_always_be_typed_in(capsys, tmp_path, monkeypatch):
    logs = walked(tmp_path, notebook=False)
    mine = tmp_path / "mia.txt"
    mine.write_text("17:45:00 A y B\n17:48:00 C y D\n")
    seen = taken(monkeypatch, "run_reconcile")
    started(tmp_path, "3", "1", "p", str(mine), "", "m", "q", monkeypatch=monkeypatch)
    assert seen[0].reconcile == [str(next(logs.glob("*.jsonl"))), str(mine)]


def test_a_notebook_path_left_empty_asks_again(capsys, tmp_path, monkeypatch):
    walked(tmp_path, notebook=False)
    seen = taken(monkeypatch, "run_reconcile")
    started(tmp_path, "3", "1", "p", "", "b", "q", monkeypatch=monkeypatch)
    assert seen == []


def test_going_back_from_the_notebook_screen_leaves_the_flow(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    seen = taken(monkeypatch, "run_reconcile")
    started(tmp_path, "3", "1", "b", "q", monkeypatch=monkeypatch)
    assert seen == []


# --- walking from the menu, and what the walk leaves behind ---------------------


def test_a_walk_that_ends_offers_the_outing_it_just_recorded(capsys, tmp_path, monkeypatch):
    # The whole reason the walk runs under the menu: Ctrl+C ends the outing and
    # hands control back, with the log it just wrote already chosen.
    logs = walked(tmp_path)
    walks_into_the_log(monkeypatch)
    seen = taken(monkeypatch, "run_reconcile")
    started(tmp_path, "1", "", "1", "", "m", "q", monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "OUTING COMPLETE" in out and "(walked)" in out
    assert "Write the 2 crossings down" in out
    assert seen[0].reconcile[1] == str(logs / "libreta.txt")
    assert seen[0].reconcile[0] != str(
        next(logs.glob("2026-09-17*.jsonl"))
    )  # el nuevo, no el viejo
    assert seen[0].outing == "new22222"


def test_carrying_on_asks_the_walk_to_resume(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    seen = []
    monkeypatch.setattr(cli, "run_walk", lambda args, log=None: seen.append(args))
    started(tmp_path, "2", "m", "q", monkeypatch=monkeypatch)
    assert seen[0].resume is True and seen[0].cycles == 0


def test_a_walk_that_wrote_nothing_goes_back_to_the_menu(capsys, tmp_path, monkeypatch):
    # Interrupted before its first record, or pointed at a directory it could
    # not write. There is no outing to offer, and inventing one would be worse.
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(cli, "run_walk", lambda args, log=None: None)
    started(tmp_path, "1", "q", monkeypatch=monkeypatch)
    assert "OUTING COMPLETE" not in capsys.readouterr().out


def test_the_outing_just_walked_can_go_straight_onto_the_map(capsys, tmp_path, monkeypatch):
    logs = walked(tmp_path)
    walks_into_the_log(monkeypatch)
    seen = taken(monkeypatch, "run_map")
    started(tmp_path, "1", "1", "1", "m", "q", monkeypatch=monkeypatch)
    assert seen[0].map_add[1] == str(logs / "libreta.txt")
    assert seen[0].outing == "new22222"


def test_leaving_the_outing_screen_goes_to_the_menu(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    walks_into_the_log(monkeypatch)
    started(tmp_path, "1", "m", "q", monkeypatch=monkeypatch)
    assert capsys.readouterr().out.count("ENODIA") == 2


def test_the_reconcile_screen_can_hand_straight_over_to_the_map(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    seen = taken(monkeypatch, "run_map")
    started(tmp_path, "3", "1", "1", "4", "m", "q", monkeypatch=monkeypatch)
    assert len(seen) == 1 and seen[0].map_add is not None


def test_leaving_the_reconcile_screen_goes_to_the_menu(capsys, tmp_path, monkeypatch):
    # At this depth back and the main menu are the same place, so the screen
    # offers one key for it and not two that mean the same thing.
    walked(tmp_path)
    seen = taken(monkeypatch, "run_reconcile")
    started(tmp_path, "3", "1", "1", "b", "m", "q", monkeypatch=monkeypatch)
    assert seen == []
    assert "'b' is not one of these." in capsys.readouterr().out


# --- the rules -----------------------------------------------------------------


def test_with_nothing_walked_yet_the_suggestion_is_to_walk(capsys, tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(cli, "run_walk", lambda args, log=None: print("(walked)"))
    started(tmp_path, "", "q", monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "the rest of this has something to work on" in out and "(walked)" in out


def test_an_outing_with_marks_and_no_notebook_is_suggested_for_transcribing(
    capsys, tmp_path, monkeypatch
):
    walked(tmp_path, notebook=False)
    started(tmp_path, "q", monkeypatch=monkeypatch)
    assert "Transcribe the notebook for 8d91f3ac: it has 2 marks" in capsys.readouterr().out


def test_an_outing_that_is_not_on_the_map_is_suggested_for_adding(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    started(tmp_path, "q", monkeypatch=monkeypatch)
    assert "Add 8d91f3ac to the map. It is not on it yet." in capsys.readouterr().out


def test_a_map_of_one_outing_suggests_walking_the_same_blocks_again(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    (tmp_path / "mapa.jsonl").write_text(
        json.dumps(
            {
                "from": "Alfa",
                "to": "Bravo",
                "fraction": 0.5,
                "outing": "2026-09-17T17:45:00-03:00/8d91f3ac",
                "networks": [],
            }
        )
        + "\n"
    )
    started(tmp_path, "q", monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "finds that outing and proves nothing" in out


def test_a_map_of_two_outings_suggests_measuring_it(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    (tmp_path / "mapa.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "from": "Alfa",
                    "to": "Bravo",
                    "fraction": 0.5,
                    "outing": f"2026-09-1{day}T17:45:00-03:00/{token}",
                    "networks": [],
                }
            )
            for day, token in ((7, "8d91f3ac"), (8, "c41d77e2"))
        )
        + "\n"
    )
    started(tmp_path, "q", monkeypatch=monkeypatch)
    assert "Measure the map" in capsys.readouterr().out


@as_root
def test_a_map_that_cannot_be_read_is_not_a_map_with_nothing_in_it(tmp_path):
    shut = tmp_path / "mapa.jsonl"
    shut.write_text("{}\n")
    shut.chmod(0o000)
    try:
        assert assistant.read_map_quietly(shut) is None
    finally:
        shut.chmod(0o600)
    assert assistant.read_map_quietly(tmp_path / "no-esta.jsonl") == []


def test_a_notebook_in_both_places_looked_in_is_offered_once(tmp_path):
    logs = walked(tmp_path)
    assert len(notebook_candidates(logs, logs)) == 1


def test_the_menu_header_runs_only_the_checks_that_cost_nothing(monkeypatch):
    # A scan takes seconds, speaking is loud, opening the button touches
    # devices, and the log check creates a directory. None of those belong in a
    # header drawn every time the menu comes back.
    from enodia import preflight

    monkeypatch.setattr(preflight.ifpeek, "get_wifi_interfaces", lambda: ["wlan0"])
    monkeypatch.setattr(preflight.ifpeek, "interface_rfkill", lambda i: None)
    monkeypatch.setattr(preflight, "lid_switch_setting", lambda **kw: ("ignore", ()))
    monkeypatch.setattr(preflight, "battery", lambda *a: (80, "Discharging"))
    from enodia.system import ScanMac

    monkeypatch.setattr(preflight, "scan_mac_setting", lambda **kw: ScanMac({}, {}, {}, None, ()))
    args = cli.build_parser().parse_args(["--assistant"])
    named = [check.name for check in assistant.machine_checks(args)]
    assert named == ["interfaces", "lid", "scan mac", "battery"]
    assert "scan" not in named and "voice" not in named and "button" not in named


def test_a_state_no_rule_speaks_to_suggests_nothing(capsys, tmp_path, monkeypatch):
    # A log that names no walk, with no marks and no notebook beside it, and an
    # empty map. Every rule looks and passes, and the honest answer is silence
    # rather than a step made up to fill the space.
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-09-17T17-45-03.jsonl").write_text(
        json.dumps(
            {
                "time": "2026-09-17T17:45:00-03:00",
                "event": "scan",
                "networks": [{"ssid": "X", "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -50}],
            }
        )
        + "\n"
    )
    started(tmp_path, "q", monkeypatch=monkeypatch)
    assert "Suggested next step" not in capsys.readouterr().out


def test_a_question_the_test_did_not_script_is_refused_rather_than_waited_on(tmp_path, monkeypatch):
    # A suite that reached the real keyboard would not fail. It would sit
    # waiting for a key that is never coming, with nobody watching.
    from conftest import RealRadioCall

    monkeypatch.setattr(assistant, "machine_checks", lambda args: READY)
    args = cli.build_parser().parse_args(["--assistant", "--dir", str(tmp_path / "logs")])
    with pytest.raises(RealRadioCall, match="asked the real keyboard"):
        run_assistant(args, isatty=lambda: True)


# --- which outings the assistant is working over -------------------------------


def two_walks_in_one_file(tmp_path):
    """`--log walk.jsonl` reused: two outings on the same pages."""
    walk = tmp_path / "walk.jsonl"
    walk.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": f"2026-09-{day}T{hour}:00:00-03:00",
                    "event": "scan",
                    "cycle": 1,
                    "outing": token,
                    "networks": [{"ssid": ssid, "bssid": "aa:bb:cc:dd:ee:01", "signal_dbm": -50}],
                }
            )
            for day, hour, token, ssid in (
                (17, 10, "old11111", "Vieja"),
                (18, 11, "new22222", "Nueva"),
            )
        )
        + "\n"
    )
    (tmp_path / "libreta.txt").write_text("10:00:00 A y B\n10:10:00 C y D\n")
    return walk


def test_a_log_named_on_the_command_line_is_the_one_the_menu_works_over(
    capsys, tmp_path, monkeypatch
):
    # The walk writes where --log says, so a menu reading the default directory
    # instead was offering a set of outings that had nothing to do with the one
    # it had just recorded, and could miss that outing entirely.
    walk = two_walks_in_one_file(tmp_path)
    monkeypatch.setattr(assistant, "machine_checks", lambda args: READY)
    args = cli.build_parser().parse_args(
        ["--assistant", "--voice", "none", "--button", "off", "--log", str(walk)]
    )
    assert assistant.known_logs(args) == [walk]
    run_assistant(args, ask=scripted("7", "", "q"), isatty=lambda: True)
    out = capsys.readouterr().out
    assert str(walk) in out


def test_every_walk_of_a_file_is_offered_and_not_only_the_last(capsys, tmp_path, monkeypatch):
    # A file can hold several, which is what `--outing TOKEN` exists for on the
    # command line. Listing only the last one hid every earlier outing from a
    # menu whose whole job is to let you pick one.
    walk = two_walks_in_one_file(tmp_path)
    found = outing_summaries([walk], set())
    assert [one.token for one in found] == ["new22222", "old11111"]  # el más nuevo primero

    monkeypatch.setattr(assistant, "machine_checks", lambda args: READY)
    seen = taken(monkeypatch, "run_reconcile")
    args = cli.build_parser().parse_args(
        ["--assistant", "--voice", "none", "--button", "off", "--log", str(walk)]
    )
    run_assistant(args, ask=scripted("3", "2", "1", "", "m", "q"), isatty=lambda: True)
    out = capsys.readouterr().out
    assert "old11111" in out and "new22222" in out
    assert seen[0].outing == "old11111"  # y el comando lee la que se eligió


def test_a_walk_that_recorded_nothing_does_not_end_somebody_elses_outing(
    capsys, tmp_path, monkeypatch
):
    # The radio was gone before the first scan, or the directory could not be
    # written. Offering the previous outing on an OUTING COMPLETE screen is
    # reporting a walk that nobody took.
    walked(tmp_path, token="oldwalk1")
    monkeypatch.setattr(cli, "run_walk", lambda args, log=None: 1)
    started(tmp_path, "1", "q", monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "Nothing was recorded in" in out
    assert "OUTING COMPLETE" not in out  # y menos aún con la salida anterior adentro


@as_root
def test_a_map_that_cannot_be_read_suspends_the_rules_that_depend_on_it(
    capsys, tmp_path, monkeypatch
):
    # Suggesting from a map nobody managed to open is advice worked out from a
    # file that was never read.
    walked(tmp_path)
    shut = tmp_path / "mapa.jsonl"
    shut.write_text("{}\n")
    shut.chmod(0o000)
    try:
        started(tmp_path, "q", monkeypatch=monkeypatch)
    finally:
        shut.chmod(0o600)
    out = capsys.readouterr().out
    assert "Suggested next step" not in out
    assert "to the map. It is not on it yet." not in out
    assert "Measure the map: hold a walk out" not in out


def test_an_outing_whose_map_cannot_be_read_is_claimed_neither_on_it_nor_off_it(tmp_path):
    logs = walked(tmp_path)
    (one,) = outing_summaries(sorted(logs.glob("*.jsonl")), None)
    assert one.on_map is None
    assert "on the map" not in one.describe()


@as_root
def test_the_screens_that_count_the_map_say_when_they_could_not_read_it(
    capsys, tmp_path, monkeypatch
):
    # Counting a map nobody opened as an empty one is an invented result rather
    # than a missing one, and both screens turn on that count.
    walked(tmp_path)
    shut = tmp_path / "mapa.jsonl"
    shut.write_text("{}\n")
    shut.chmod(0o000)
    seen = taken(monkeypatch, "run_map")
    try:
        started(tmp_path, "5", "m", "6", "", "q", monkeypatch=monkeypatch)
    finally:
        shut.chmod(0o600)
    out = capsys.readouterr().out
    assert "is there and cannot be read, so what it holds is not known" in out
    assert "THE MAP CANNOT BE READ" in out
    assert seen == []  # y no se mide nada


def test_an_empty_map_says_it_places_nothing(capsys, tmp_path, monkeypatch):
    walked(tmp_path)
    (tmp_path / "mapa.jsonl").write_text("")
    started(tmp_path, "5", "m", "q", monkeypatch=monkeypatch)
    assert "An empty map places nothing" in capsys.readouterr().out


def test_the_offer_to_walk_again_actually_walks(capsys, tmp_path, monkeypatch):
    # The option was rendered, numbered and perfectly pressable, and nothing was
    # wired behind it: whichever key you chose, the screen went back to the menu.
    walked(tmp_path)
    (tmp_path / "mapa.jsonl").write_text(
        json.dumps(
            {
                "from": "Alfa",
                "to": "Bravo",
                "fraction": 0.5,
                "outing": "2026-09-17T17:45:00-03:00/8d91f3ac",
                "networks": [],
            }
        )
        + "\n"
    )
    walks_into_the_log(monkeypatch)
    started(tmp_path, "6", "1", "m", "q", monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "NOTHING TO MEASURE YET" in out and "(walked)" in out


def test_an_outing_that_recorded_only_failures_is_not_somebody_elses_outing(
    capsys, tmp_path, monkeypatch
):
    # `--log FILE` reused means the file can be full of history, so "the newest
    # walk in it" is not the same question as "the walk this run just made".
    walk = tmp_path / "walk.jsonl"
    walk.write_text(records("old11111", marks=0, scans=2))

    def only_failures(args, log=None):
        with log.open("a", encoding="utf-8") as out:
            out.write(
                json.dumps(
                    {
                        "time": "2026-09-19T11:00:00-03:00",
                        "event": "scan_failed",
                        "outing": "new22222",
                        "reason": "radio soft blocked (rfkill)",
                    }
                )
                + "\n"
            )

    monkeypatch.setattr(cli, "run_walk", only_failures)
    monkeypatch.setattr(assistant, "machine_checks", lambda args: READY)
    args = cli.build_parser().parse_args(
        ["--assistant", "--voice", "none", "--button", "off", "--log", str(walk)]
    )
    run_assistant(args, ask=scripted("1", "q"), isatty=lambda: True)
    out = capsys.readouterr().out
    assert "Nothing usable was recorded for new22222." in out
    assert "OUTING COMPLETE" not in out and "old11111  2 scans" not in out


def test_carrying_an_outing_on_offers_the_one_it_carried_on(capsys, tmp_path, monkeypatch):
    # A resumed walk writes no new token, so "the walk this run made" is the one
    # that was already there and was continued.
    walk = tmp_path / "walk.jsonl"
    walk.write_text(records("old11111", marks=1, scans=2))

    def carries_on(args, log=None):
        with log.open("a", encoding="utf-8") as out:
            out.write(records("old11111", marks=0, scans=1))

    monkeypatch.setattr(cli, "run_walk", carries_on)
    monkeypatch.setattr(assistant, "machine_checks", lambda args: READY)
    args = cli.build_parser().parse_args(
        ["--assistant", "--voice", "none", "--button", "off", "--log", str(walk)]
    )
    run_assistant(args, ask=scripted("2", "m", "q"), isatty=lambda: True)
    out = capsys.readouterr().out
    assert "OUTING COMPLETE" in out and "old11111" in out
