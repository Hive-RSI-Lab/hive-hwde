"""bin/score.py's parts that need no KiCad: the pins and the charges
(docs/score.md). The gates themselves run in `bin/hwde score`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "hwde" / "scripts"))

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


def test_a_gate_note_warning_is_one_charge():
    """erc appends its pin-type coverage note to warnings as a plain string,
    outside warnings_count (gate.py)."""
    result = {"failing": [], "warnings_count": 1,
              "warnings": [{"check": "erc", "kind": "pin_not_connected"},
                           "all 370 pins of the 28 multi-pin symbols are typed passive"]}
    assert score.gate_charges(result, [{"check": "x"}]) == {"erc": 1, "(gate note)": 1}
