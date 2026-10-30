"""bin/score.py's parts that need no KiCad: the pins and the charges
(docs/score.md). The gates themselves run in `bin/hwde score`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "scripts"))

import score  # noqa: E402


def test_every_pin_matches_its_digest_and_classifies_its_scripts():
    pins = score.pins()
    assert {"hwde-led-test", "lite-main", "core"} <= {p.name for p in pins}
    assert set(score.PINS) == {p.name for p in pins}
    for p in pins:
        assert score.problems(p) == [], p.name


def test_hifi_pins_name_a_commit():
    for p in score.pins():
        if p.project == "hifi-receiver-2":
            assert len(p.commit) == 40 and p.dir == ROOT / "pinned" / p.name


@pytest.fixture
def pin(tmp_path, monkeypatch):
    d = tmp_path / "b"
    (d / "kicad" / "gen").mkdir(parents=True)
    (d / "state.json").write_text("{}")
    (d / "kicad" / "gen" / "root.py").write_text("")
    (d / "kicad" / "gen" / "fix.py").write_text("")
    pf = tmp_path / "b.json"
    pf.write_text(json.dumps({"board": "b", "dir": str(d), "design": ["kicad/gen/root.py"],
                              "stand_in": {"kicad/gen/fix.py": "hwde can't"}}))
    monkeypatch.setattr(score, "ROOT", tmp_path)
    p = score.read_pin(pf)
    monkeypatch.setitem(score.PINS, "b", score.digest(p))
    return p


def test_a_clean_pin_has_no_problems_and_charges_its_stand_ins(pin):
    assert score.problems(pin) == []
    assert score.script_charges(pin) == ["kicad/gen/fix.py"]


def test_an_edited_board_stops_the_score(pin):
    (pin.dir / "state.json").write_text('{"gates": {}}')
    assert any("digest" in m for m in score.problems(pin))


def test_a_new_script_is_charged_until_classified(pin):
    (pin.dir / "kicad" / "gen" / "new.sh").write_text("")
    msgs = score.problems(pin)
    assert any("new.sh is in the board but not classified" in m for m in msgs)


def test_a_dropped_stand_in_row_is_not_a_free_point(pin):
    # taking the row out of the pin without the script leaves it unclassified
    pin.stand_in.clear()
    assert any("fix.py is in the board but not classified" in m for m in score.problems(pin))


def test_gate_charges_count_failing_and_unwaived_warnings():
    w = {"check": "check_ratings", "net": "+5V", "reason": "r", "approved": "x 2026-10-29"}
    result = {
        "failing": [{"check": "check_thermal", "net": "GND"}],
        "warnings": [{"check": "check_ratings", "net": "+5V"},
                     {"check": "check_route_style", "net": "SDA"}],
        "warnings_count": 4,
    }
    c = score.gate_charges(result, [w])
    assert c == {"check_thermal": 1, "check_route_style": 1, "(warnings past the cap)": 2}
    assert sum(score.gate_charges({"failing": [], "warnings": [], "warnings_count": 0}).values()) == 0


def test_gate_charges_take_a_plain_string_warning():
    # a gate may report a warning (or a failing finding) as a plain string
    result = {"failing": ["board has no outline"],
              "warnings": ["3 3D model(s) missing", {"check": "check_thermal"}],
              "warnings_count": 3}
    w = {"check": "check_thermal", "net": None, "reason": "r", "approved": "x 2026-10-29"}
    assert score.gate_charges(result, [w]) == {"?": 2, "(warnings past the cap)": 1}


def test_a_finding_is_named_by_where_it_is_not_its_wording():
    f = score.finding_of("verify", {"check": "check_current", "net": "+3V3", "refs": ["R2", "C1"],
                                    "pos": [10.00004, 20.5], "message": "0.3 mm carries 0.8 A"})
    assert f == {"gate": "verify", "check": "check_current", "net": "+3V3",
                 "refs": ["C1", "R2"], "pos": [10.0, 20.5]}
    # a plain string, or a finding with nowhere to be, goes by its message
    assert score.finding_of("erc", "pin types: 3 untyped")["message"] == "pin types: 3 untyped"


def _f(**kw):
    return score.finding_of(kw.pop("gate", "verify"), {"check": "check_route_style", **kw})


def test_real_findings_cost_nothing_false_and_unjudged_cost_one_missed_real_costs_one():
    real = {**_f(net="SDA", pos=[1, 2]), "verdict": "real", "reason": "a staircase"}
    gone = {**_f(net="SCL", pos=[3, 4]), "verdict": "real", "reason": "a staircase"}
    false = {**_f(net="GND", pos=[5, 6]), "verdict": "false", "reason": "a pour neck"}
    found = [_f(net="SDA", pos=[1.01, 2]), _f(net="GND", pos=[5, 6]), _f(net="X", pos=[0, 0])]
    j = score.judge(found, [real, gone, false])
    assert (len(j.real), len(j.missed), len(j.false), len(j.unjudged)) == (1, 1, 1, 1)
    assert j.charges == 3
    # a sharper check that finds a new real defect, once it is in the ledger, costs nothing
    new = {**found[2], "verdict": "real", "reason": "r"}
    assert score.judge(found, [real, gone, false, new]).charges == 2
    # a looser check that stops reporting known defects is charged for each
    assert score.judge([], [real, gone, false]).charges == 2


def test_one_entry_is_one_finding_and_a_crashed_gate_misses_nothing():
    e = {**_f(net="SDA"), "verdict": "real", "reason": "r"}
    assert score.judge([_f(net="SDA"), _f(net="SDA")], [e]).charges == 1
    erc = {**score.finding_of("erc", "x"), "verdict": "real", "reason": "r"}
    assert score.judge([], [erc], ran={"verify"}).charges == 0
    assert score.judge([], [erc], ran={"erc"}).charges == 1


def test_the_ledger_is_in_the_digest_and_a_verdict_needs_a_reason(pin):
    k = score.known_path(pin)
    k.parent.mkdir(parents=True)
    k.write_text(json.dumps({"known": [{**_f(net="A"), "verdict": "real", "reason": ""}]}))
    msgs = score.problems(pin)
    assert any("digest" in m for m in msgs) and any("no reason" in m for m in msgs)
    k.write_text(json.dumps({"known": [{**_f(net="A"), "verdict": "maybe"}]}))
    assert any("verdict of" in m for m in score.problems(pin))


def test_draft_known_adds_only_what_the_ledger_lacks(pin):
    assert score.draft_known(pin, [_f(net="A"), _f(net="B")]) == 2
    known = score.read_known(pin)
    assert [e["verdict"] for e in known] == ["?", "?"]
    known[0].update(verdict="real", reason="r")
    score.known_path(pin).write_text(json.dumps({"known": known}))
    assert score.draft_known(pin, [_f(net="A"), _f(net="B"), _f(net="C")]) == 1
    assert [e["verdict"] for e in score.read_known(pin)] == ["real", "?", "?"]
