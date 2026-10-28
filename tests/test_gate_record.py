"""U16 acceptance tests: a gate result that never reached state.json.

The defect this closes (bb-buck, 2026-08-16): the board reached P9 with a
complete fab package and six PASSING gate reports on disk while state.json
recorded NONE of them - 114 history events, zero gate events, `gates: {}`,
`gates_passed: []`, `resume` still naming P4 erc as the next gate. The
orchestrator ran `gate.py --gate <g> <input> --commit` at every phase but
never `state.py record-gate`, which SKILL rule 3 requires and nothing
enforced. With no input hashes, freshness, invalidation and attestation were
all unavailable on a board that had otherwise passed everything.

Two mechanical teeth, tested here:
  1. `gate.py` RECORDS the result itself when the input sits in a workspace
     (--workspace, or the first parent holding a state.json). Corpus inputs
     with no workspace are untouched, so the golden/mutant suite is unaffected.
  2. `state.py set-phase` REFUSES to advance past a gate phase whose gate has
     no recorded result (--force is the loud escape hatch).

Hermetic except test_bb_buck_*, which read the committed live workspace.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
SKILL = SCRIPTS.parent
GOLDEN = REPO / "tests" / "golden" / "blinky2"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import checklib  # noqa: E402
import gate  # noqa: E402
import state as state_mod  # noqa: E402
import statelib  # noqa: E402
import task_router  # noqa: E402
from checklib import CheckError  # noqa: E402
from _boards import board_path, need_board, need_shipped_checks  # noqa: E402

BOARD = "wb"


# --------------------------------------------------------------- helpers

def make_ws(tmp_path: Path, phase: str = "P8") -> Path:
    """A minimal but REAL workspace: state.json v2 + the kicad/ files the
    verify gate's inputs resolve to."""
    ws = tmp_path / "boards" / BOARD
    (ws / "kicad").mkdir(parents=True)
    (ws / "kicad" / f"{BOARD}.kicad_pcb").write_text(
        '(kicad_pcb (version 20260101) (generator "test"))', encoding="utf-8")
    for name in ("constraints.json", "decoupling.json"):
        (ws / "kicad" / name).write_text("{}", encoding="utf-8")
    state_mod.State.init(ws, BOARD, phase, force=True)
    return ws


def verify_report(board: Path, violations=None) -> dict:
    vios = violations or []
    return checklib.stamp(
        {"script": "verify_all", "board": board.name,
         "status": "violations" if vios else "pass",
         "counts": checklib.summarize(vios), "checks": {},
         "violations": vios}, board)


def run_gate(ws: Path, report: dict, extra=(), gate_name="verify") -> int:
    rp = ws / "reports" / "r.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report), encoding="utf-8")
    return gate.main(["--gate", gate_name, "--report", str(rp), *extra])


def load(ws: Path) -> dict:
    return json.loads((ws / "state.json").read_text(encoding="utf-8"))


def _err(net="V48"):
    return {"check": "check_creepage", "kind": "creepage", "severity": "error",
            "net": net, "refs": ["U1"], "pos": [1.0, 1.0], "msg": "x",
            "source": "check_creepage"}


# ------------------------------------------------- 1. the gate records itself

def test_gate_records_itself_with_input_hashes(tmp_path):
    """The whole point: no separate record step, and the recorded entry
    carries the freshness key (input hashes), not just a status."""
    ws = make_ws(tmp_path)
    board = ws / "kicad" / f"{BOARD}.kicad_pcb"
    assert run_gate(ws, verify_report(board)) == 0

    g = load(ws)["gates"]["verify"]
    assert g["status"] == "pass" and g["attempts"] == 1 and g["phase"] == "P8"
    inputs = g["last"]["inputs"]
    assert set(inputs) == {"pcb", "constraints", "decoupling", "waivers"}
    assert inputs["pcb"] == statelib.hash_artifact(board, "sexpr_no_uuid")
    # the gate result is now hash-fresh evidence, not just a status
    assert state_mod.State.load(ws / "state.json") \
        .freshness()["gates"]["verify"]["fresh"] is True


def test_explicit_workspace_flag_records(tmp_path, capsys):
    ws = make_ws(tmp_path)
    board = ws / "kicad" / f"{BOARD}.kicad_pcb"
    assert run_gate(ws, verify_report(board),
                    extra=["--workspace", str(ws)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["record_result"]["recorded"] is True
    assert out["record_result"]["inputs"]["pcb"]
    assert load(ws)["gates"]["verify"]["status"] == "pass"


def test_workspace_flag_without_state_json_refuses(tmp_path):
    """A --workspace typo that silently records nowhere is the bug, not the
    fix: an explicit workspace with no state.json is exit 2."""
    ws = make_ws(tmp_path)
    board = ws / "kicad" / f"{BOARD}.kicad_pcb"
    assert run_gate(ws, verify_report(board),
                    extra=["--workspace", str(tmp_path / "nope")]) == 2


def test_failing_gate_is_recorded_too(tmp_path):
    """The fix loop's evidence is EVERY attempt - a fail records, then the
    repaired re-run records attempt 2 over it."""
    ws = make_ws(tmp_path)
    board = ws / "kicad" / f"{BOARD}.kicad_pcb"
    assert run_gate(ws, verify_report(board, [_err()])) == 1
    g = load(ws)["gates"]["verify"]
    assert g["status"] == "fail" and g["attempts"] == 1

    assert run_gate(ws, verify_report(board)) == 0
    g = load(ws)["gates"]["verify"]
    assert g["status"] == "pass" and g["attempts"] == 2
    assert [h["status"] for h in g["history"]] == ["fail", "pass"]


def test_no_record_opts_out(tmp_path):
    ws = make_ws(tmp_path)
    board = ws / "kicad" / f"{BOARD}.kicad_pcb"
    assert run_gate(ws, verify_report(board), extra=["--no-record"]) == 0
    assert load(ws)["gates"] == {}


def test_corpus_input_without_a_workspace_still_gates(tmp_path, capsys):
    """A golden/mutant input has no state.json above it: the gate grades it
    exactly as before, says it recorded nothing, and keeps its exit code."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(verify_report(board)), encoding="utf-8")
    assert gate.main(["--gate", "verify", "--report", str(rp)]) == 0
    rr = json.loads(capsys.readouterr().out)["record_result"]
    assert rr == {"ok": True, "recorded": False,
                  "reason": "no state.json above the input - nothing to record "
                            "(corpus/scratch input)"}


def test_golden_board_gate_records_nothing(tmp_path, capsys):
    """The committed corpus itself: gating tests/golden/blinky2 in place must
    not try to write state anywhere (the suite would dirty the tree)."""
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(verify_report(GOLDEN / "blinky2.kicad_pcb")),
                  encoding="utf-8")
    assert gate.main(["--gate", "verify", "--report", str(rp)]) == 0
    assert json.loads(capsys.readouterr().out)["record_result"]["ok"] is True


def test_unrecordable_state_is_an_operational_error(tmp_path, capsys):
    """A record that SHOULD have happened and did not is exit 2 - a caller
    keying on exit 0 must not believe the evidence was preserved. Here the
    workspace still carries a v1 state.json (State.load refuses it)."""
    ws = make_ws(tmp_path)
    board = ws / "kicad" / f"{BOARD}.kicad_pcb"
    data = load(ws)
    data["version"] = 1
    (ws / "state.json").write_text(json.dumps(data), encoding="utf-8")
    assert run_gate(ws, verify_report(board)) == 2
    rr = json.loads(capsys.readouterr().out)["record_result"]
    assert rr["ok"] is False and "state_migrate.py" in rr["reason"]


def test_place_gate_records_on_a_real_board(tmp_path, capsys):
    """End to end through the real tool (place_metrics, pure venv): a golden
    board copied into a workspace records a fresh pass."""
    ws = tmp_path / "boards" / "blinky2"
    (ws / "kicad").mkdir(parents=True)
    import shutil
    for f in ("blinky2.kicad_pcb", "blinky2.kicad_sch", "constraints.json",
              "decoupling.json"):
        shutil.copy2(GOLDEN / f, ws / "kicad" / f)
    state_mod.State.init(ws, "blinky2", "P6", force=True)
    rc = gate.main(["--gate", "place", str(ws / "kicad" / "blinky2.kicad_pcb")])
    assert rc == 0, capsys.readouterr().out
    st = state_mod.State.load(ws / "state.json")
    fresh = st.freshness()["gates"]["place"]
    assert fresh["fresh"] is True and fresh["hash_valid"] is True


def _place_report(board: Path, violations) -> dict:
    return checklib.stamp(
        {"script": "place_metrics", "board": board.name,
         "status": "violations" if violations else "pass",
         "counts": checklib.summarize(violations), "violations": violations},
        board)


def _overlap():
    return {"check": "place", "source": "check.place",
            "kind": "courtyard_overlap", "severity": "error", "net": None,
            "refs": ["C301", "U301"], "pos": [60.135, 41.6875], "msg": "x"}


def test_place_gate_applies_and_hashes_place_waivers(tmp_path, capsys):
    """PCB-0018-A (2026-10-02): every place finding had an entry in
    reports/place-waivers.json, yet the place gate failed all 14 and its
    recorded inputs carried no waivers hash - only the verify tool looked
    for a sidecar. The place gate now applies <ws>/reports/place-waivers.json
    and hashes it into its inputs; verify-waivers.json stays verify's."""
    ws = make_ws(tmp_path, phase="P6")
    board = ws / "kicad" / f"{BOARD}.kicad_pcb"
    waiver = {"check": "place", "kind": "courtyard_overlap", "net": None,
              "refs": ["C301", "U301"], "pos": [60.135, 41.6875],
              "reason": "pads 0.225 mm apart, legal under the scoped rule",
              "approved": "test"}
    (ws / "reports").mkdir(parents=True, exist_ok=True)
    # the verify sidecar alone does not waive a place finding
    (ws / "reports" / "verify-waivers.json").write_text(
        json.dumps({"waivers": [waiver]}), encoding="utf-8")
    report = _place_report(board, [_overlap()])
    assert run_gate(ws, report, gate_name="place") == 1
    out = json.loads(capsys.readouterr().out)
    assert out["failing_count"] == 1 and "waived" not in out
    assert load(ws)["gates"]["place"]["last"]["inputs"]["place_waivers"] \
        is None

    (ws / "reports" / "place-waivers.json").write_text(
        json.dumps({"waivers": [waiver]}), encoding="utf-8")
    assert run_gate(ws, report, gate_name="place") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["failing_count"] == 0 and out["waived_count"] == 1
    inputs = load(ws)["gates"]["place"]["last"]["inputs"]
    assert set(inputs) == {"pcb", "constraints", "decoupling",
                           "place_waivers"}
    assert inputs["place_waivers"] == statelib.hash_artifact(
        ws / "reports" / "place-waivers.json", "json_canonical")
    # editing the waiver file makes the recorded pass stale
    (ws / "reports" / "place-waivers.json").write_text(
        json.dumps({"waivers": [dict(waiver, reason="reworded")]}),
        encoding="utf-8")
    fresh = state_mod.State.load(ws / "state.json").freshness()
    assert fresh["gates"]["place"]["changed_inputs"] == ["place_waivers"]


# ------------------------------------------- 2. set-phase needs the evidence

def test_set_phase_refuses_to_leave_an_unrecorded_gate_phase(tmp_path):
    """bb-buck's exact walk: P4 -> P5 with no erc result recorded."""
    ws = make_ws(tmp_path, phase="P4")
    st = state_mod.State.load(ws / "state.json")
    with pytest.raises(CheckError) as exc:
        st.set_phase("P5")
    assert "erc (P4)" in str(exc.value) and "gate.py --workspace" in str(exc.value)
    assert st.data["phase"] == "P4"        # refused, not half-applied


def test_set_phase_allows_the_recorded_walk(tmp_path):
    ws = make_ws(tmp_path, phase="P4")
    st = state_mod.State.load(ws / "state.json")
    ok = {"status": "pass", "failing_count": 0, "counts": {"total": 0}}
    st.record_gate("erc", ok, "P4")
    assert [w["kind"] for w in st.set_phase("P5")] == ["digest_discipline"]
    assert st.set_phase("P6", require_gates=True) is not None
    with pytest.raises(CheckError):
        st.set_phase("P7")                 # place (P6) not recorded
    st.record_gate("place", ok, "P6")
    st.set_phase("P7")
    assert st.data["phase"] == "P7"


def test_force_advances_loudly(tmp_path, capsys):
    ws = make_ws(tmp_path, phase="P4")
    rc = state_mod.main(["set-phase", "--workspace", str(ws), "--phase", "P5",
                         "--force"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    kinds = [w["kind"] for w in out["warnings"]]
    assert "gate_coverage" in kinds
    forced = [e for e in load(ws)["history"] if e["event"] == "phase_forced"]
    assert forced and forced[0]["missing"] == ["erc (P4)"]


def test_cli_set_phase_refusal_is_exit_2(tmp_path, capsys):
    ws = make_ws(tmp_path, phase="P4")
    assert state_mod.main(["set-phase", "--workspace", str(ws),
                           "--phase", "P5"]) == 2
    assert "no recorded gate result" in capsys.readouterr().out
    assert load(ws)["phase"] == "P4"


def test_advancing_past_a_recorded_fail_warns(tmp_path):
    ws = make_ws(tmp_path, phase="P4")
    st = state_mod.State.load(ws / "state.json")
    st.record_gate("erc", {"status": "fail", "failing_count": 2,
                           "counts": {"total": 2}}, "P4")
    warns = st.set_phase("P5")
    assert any(w["kind"] == "gate_coverage" and "recorded FAIL" in w["msg"]
               for w in warns)


def test_going_backwards_is_never_gated(tmp_path):
    """A fix loop rewinds the phase all the time - only ADVANCING needs
    evidence."""
    ws = make_ws(tmp_path, phase="P8")
    st = state_mod.State.load(ws / "state.json")
    st.set_phase("P6")
    assert st.data["phase"] == "P6"


# ------------------------------------------- 3. the owed set has ONE source

def test_sim_joins_the_owed_set_when_the_board_ships_testbenches(tmp_path):
    ws = make_ws(tmp_path, phase="P8")
    assert ("P8", "sim") not in state_mod.applicable_gate_order(ws, BOARD, {})
    (ws / "kicad" / "sims").mkdir()
    order = state_mod.applicable_gate_order(ws, BOARD, {})
    assert order[3:5] == [("P8", "verify"), ("P8", "sim")]

    st = state_mod.State.load(ws / "state.json")
    ok = {"status": "pass", "failing_count": 0, "counts": {"total": 0}}
    for ph, g in (("P4", "erc"), ("P6", "place"), ("P7", "drc_routed"),
                  ("P8", "verify")):
        st.record_gate(g, ok, ph)
    assert st.resume_summary()["next_gate"] == {"phase": "P8", "gate": "sim"}
    with pytest.raises(CheckError) as exc:
        st.set_phase("P9")
    assert "sim (P8)" in str(exc.value)


def test_releaselib_required_gates_uses_the_same_definition(tmp_path):
    import releaselib
    ws = make_ws(tmp_path)
    imap = statelib.load_map()
    assert releaselib.required_gates(ws, BOARD, imap, {}) == [
        g for _, g in state_mod.applicable_gate_order(ws, BOARD, {})]
    (ws / "kicad" / "sims").mkdir()
    assert "sim" in releaselib.required_gates(ws, BOARD, imap, {})


# ------------------------------------------- 4. the recipes carry --workspace

@pytest.mark.parametrize("verb", sorted(task_router.load_tasks()["verbs"]))
def test_every_planned_gate_step_carries_the_workspace(verb):
    """The plan the router hands the orchestrator is the surface that
    actually gets copied - every gate step in it records."""
    payload, _ = task_router.run(["--verb", verb])
    steps = [s for s in payload["recipe"]["steps"] if s.get("kind") == "gate"]
    for s in steps:
        assert "--workspace" in s["command"], (verb, s["command"])


def test_full_run_recipe_and_skill_state_the_gate_form():
    recipe = (SKILL / "reference" / "recipes"
              / "full-run.md").read_text(encoding="utf-8")
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for name, text in (("full-run.md", recipe), ("SKILL.md", skill)):
        for line in text.splitlines():
            if "gate.py --gate" in line:
                assert "--workspace" in line, f"{name}: {line}"
        assert "--workspace" in text, name
    assert "set-phase" in recipe and "recorded result" in recipe


# ------------------------------------------- 5. the board that found the hole

BB = board_path("bb-buck")


def test_bb_buck_gates_are_recorded():
    """Back-recorded from its committed reports (five digest-matched, place
    re-run against the routed board). Six gates, each with input hashes -
    the board keeps honest provenance instead of an empty `gates: {}`."""
    need_board("bb-buck")
    st = state_mod.State.load(BB / "state.json")
    summary = st.resume_summary()
    assert summary["gates_passed"] == ["erc", "place", "drc_routed", "verify",
                                       "sim", "dfm"]
    for g, entry in st.data["gates"].items():
        assert entry["last"]["inputs"], g
        assert all(v for v in entry["last"]["inputs"].values()), g


def test_bb_buck_gates_are_fresh():
    """Whether the live board's gates are fresh depends on what the boards
    repo did to the board since, not on hwde (a model-path repair there
    staled four of them, 2026-10-08), so it is the boards repo's check and
    runs here only when asked."""
    need_shipped_checks()
    need_board("bb-buck")
    summary = state_mod.State.load(BB / "state.json").resume_summary()
    assert summary["gates_passed_fresh"] == summary["gates_passed"]
    assert summary["gates_stale"] == [] and summary["next_gate"] is None


def test_bb_buck_gerbers_artifact_is_hashed_and_unmarked():
    """The P6 move_fp / P7 reroute_net marks on `gerbers` outlived the
    re-export: the zip was regenerated at P9 and never re-hashed, so the
    registry still carried sha256 null plus two stale marks."""
    need_board("bb-buck")
    st = state_mod.State.load(BB / "state.json")
    entry = st.data["artifacts"]["gerbers"]
    assert entry["sha256"] and entry["sha256"].startswith("gerber_design:")
    assert not entry.get("stale")
    assert st.freshness()["summary"]["human_hold_pending"] == 0
