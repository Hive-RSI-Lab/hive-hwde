"""lib/bringuplib.py and `state.py bringup`: a bring-up close records what
was measured (task 261028-184258).

The results schema's rules (a verdict needs a reading and a limit, a pass
lies inside its limit, a derate says to what, a skip says why), the outcome
each file supports, and the bench-session conversion that lets a project's
own bench (hifi-receiver-2's session.json) feed the close unchanged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bringuplib  # noqa: E402
import state as state_mod  # noqa: E402


def doc(*items, **kw):
    return {"schema": bringuplib.SCHEMA, "board": "b", "date": "2026-11-05",
            "items": list(items), **kw}


V33 = {"name": "+3V3", "measured": 3.31, "unit": "V",
       "limit": {"min": 3.2, "max": 3.4}, "verdict": "pass"}


def test_well_formed_pass():
    d = doc(V33, {"name": "plays Spotify", "measured": "y", "limit": "works",
                  "verdict": "pass"})
    assert bringuplib.check(d) == []
    o = bringuplib.outcome(d)
    assert o["outcome"] == "passed" and o["event"] == "bringup_passed"
    assert o["counts"]["pass"] == 2


@pytest.mark.parametrize("item,needle", [
    ({"name": "x", "verdict": "pass", "limit": {"max": 1}}, "without a measured"),
    ({"name": "x", "verdict": "pass", "measured": 1}, "without a limit"),
    ({"name": "x", "verdict": "pass", "measured": 5, "limit": {"max": 1}},
     "lies outside"),
    ({"name": "x", "verdict": "fail", "measured": 0.5, "limit": {"max": 1}},
     "lies inside"),
    ({"name": "x", "verdict": "derated", "measured": 5, "limit": {"max": 1}},
     "needs a note"),
    ({"name": "x", "verdict": "skip"}, "needs a reason"),
    ({"name": "x", "verdict": "ok", "measured": 1, "limit": "y"}, "not one of"),
    ({"name": "x", "verdict": "pass", "measured": 1, "limit": {"typ": 1}},
     "numeric min/max/nom"),
])
def test_item_problems(item, needle):
    probs = bringuplib.check(doc(item))
    assert any(needle in p for p in probs), probs


def test_document_problems():
    assert any("schema" in p for p in bringuplib.check(
        {"date": "2026-11-05", "items": [V33]}))
    assert any("date" in p for p in bringuplib.check(doc(V33, date="soon")))
    assert any("non-empty" in p for p in bringuplib.check(doc()))
    assert any("twice" in p for p in bringuplib.check(doc(V33, V33)))


def test_outcomes_worst_first():
    fail = dict(V33, name="5V", verdict="fail", measured=4.1,
                limit={"min": 4.75})
    der = {"name": "P", "measured": 38, "limit": {"min": 45},
           "verdict": "derated", "note": "charger budget"}
    req_skip = {"name": "eth", "verdict": "skip", "reason": "no cable"}
    opt_skip = dict(req_skip, optional=True)
    assert bringuplib.outcome(doc(V33, der, fail))["outcome"] == "failed"
    assert bringuplib.outcome(doc(V33, der))["event"] == "bringup_derated"
    o = bringuplib.outcome(doc(V33, req_skip))
    assert o["outcome"] == "incomplete" and o["event"] is None \
        and o["unmeasured"] == ["eth"]
    assert bringuplib.outcome(doc(V33, opt_skip))["outcome"] == "passed"
    assert bringuplib.outcome(doc(opt_skip))["outcome"] == "incomplete"


# hifi-receiver-2 bench/ session.json, trimmed to the keys it writes
SESSION = {
    "board": "lite-main", "info": {"mac": "24:0a:c4:12:34:56"},
    "mock": False, "started": "2026-11-05 14:02:11",
    "selftests": [{"stage": "laptop USB (5 V)", "pass": True, "items": [
        {"name": "codec i2c", "pass": True, "value": "ack", "limit": "ack"},
        {"name": "pd_contract", "pass": False, "expected": True,
         "value": "5 V", "limit": ">= 9 V"},
        {"name": "vbus_v", "pass": True, "value": 5.04, "limit": 4.5}]}],
    "verdicts": {"self-test on a laptop port, amp held off": "pass",
                 "power into 4 Ω (1% THD+N ≥ 90% of 50 W)": "pass: 47.2 W",
                 "chip temperature at 1/8 power into 4 Ω":
                     "no temperature (give --temp-cmd or --temp-manual)"},
    "checklist": {"Spotify": "y", "IR remote": "skip"},
}


def test_from_session():
    d = bringuplib.from_session(SESSION, evidence="s.json")
    assert bringuplib.check(d) == []
    by = {i["name"]: i for i in d["items"]}
    assert by["laptop USB (5 V): codec i2c"]["verdict"] == "pass"
    assert by["laptop USB (5 V): pd_contract"]["verdict"] == "skip"
    assert by["laptop USB (5 V): pd_contract"]["optional"] is True
    assert by["laptop USB (5 V): vbus_v"]["limit"] == "4.5"
    p = by["power into 4 Ω (1% THD+N ≥ 90% of 50 W)"]
    assert p["verdict"] == "pass" and p["measured"] == "47.2 W"
    assert "self-test on a laptop port, amp held off" not in by
    assert by["chip temperature at 1/8 power into 4 Ω"]["verdict"] == "skip"
    assert by["Spotify"]["verdict"] == "pass" and by["IR remote"]["verdict"] == "skip"
    assert all(i["evidence"] == "s.json" for i in d["items"])
    assert d["date"] == "2026-11-05" and d["board"] == "lite-main"
    # the soak and the IR remote were not measured: not a pass yet
    o = bringuplib.outcome(d)
    assert o["outcome"] == "incomplete"
    assert set(o["unmeasured"]) == {"chip temperature at 1/8 power into 4 Ω",
                                    "IR remote"}


def test_mock_session_refused():
    with pytest.raises(bringuplib.BringupError, match="mock"):
        bringuplib.from_session(dict(SESSION, mock=True))


def ws_with(tmp_path, results: dict | None, board="b") -> Path:
    ws = tmp_path / "ws"
    state_mod.State.init(ws, board).save()
    if results is not None:
        (ws / "bringup").mkdir()
        (ws / "bringup" / "results.json").write_text(json.dumps(results),
                                                     encoding="utf-8")
    return ws


def history(ws: Path) -> list[dict]:
    return json.loads((ws / "state.json").read_text(encoding="utf-8"))["history"]


def test_state_bringup_logs_the_supported_event(tmp_path, capsys):
    ws = ws_with(tmp_path, doc(V33))
    assert state_mod.main(["bringup", "--workspace", str(ws),
                           "--msg", "remote run"]) == 0
    ev = history(ws)[-1]
    assert ev["event"] == "bringup_passed"
    assert ev["results"] == "bringup/results.json"
    assert ev["results_sha256"] == bringuplib.sha256(ws / "bringup/results.json")
    assert ev["counts"]["pass"] == 1 and ev["msg"] == "remote run"
    assert bringuplib.evidence_problems(ws, ev) == []


def test_state_bringup_refusals(tmp_path, capsys):
    ws = ws_with(tmp_path, None)
    assert state_mod.main(["bringup", "--workspace", str(ws)]) == 2   # no file
    ws = ws_with(tmp_path / "a", doc(V33, {"name": "eth", "verdict": "skip",
                                           "reason": "no cable"}))
    assert state_mod.main(["bringup", "--workspace", str(ws)]) == 2   # incomplete
    ws = ws_with(tmp_path / "b", doc(dict(V33, measured=9.0)))
    assert state_mod.main(["bringup", "--workspace", str(ws)]) == 2   # pass outside
    ws = ws_with(tmp_path / "c", doc(V33, board="other"))
    assert state_mod.main(["bringup", "--workspace", str(ws)]) == 2   # wrong board
    assert not [h for h in history(ws) if h["event"].startswith("bringup")]


def test_state_bringup_reads_a_bench_session(tmp_path, capsys):
    s = json.loads(json.dumps(SESSION))
    s["board"] = "b"
    s["verdicts"]["chip temperature at 1/8 power into 4 Ω"] = "pass: 96 °C"
    s["checklist"]["IR remote"] = "y"
    ws = ws_with(tmp_path, None)
    (ws / "bench").mkdir()
    (ws / "bench" / "session.json").write_text(json.dumps(s), encoding="utf-8")
    assert state_mod.main(["bringup", "--workspace", str(ws), "--results",
                           "bench/session.json"]) == 0
    assert history(ws)[-1]["event"] == "bringup_passed"
