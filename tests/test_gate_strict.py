"""U2 acceptance tests: gate report validation, commit scoping, strict
verify coverage (codex C4 + C7).

The tamper suite: error-shaped / empty / stale / wrong-input / wrong-tool
reports must all be REFUSED (exit 2, never a pass); an unscoped commit must
refuse; the lumina-carrier's historic "dfm pass with edge checks silently
skipped" scenario must yield a visible `skipped_error` and fail under strict.

Hermetic: pure venv + committed fixtures (goldens, T5 pd_trigger stage
gerbers). No kicad-cli.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
GOLDEN = REPO / "tests" / "golden"
STAGES = REPO / "tests" / "fixtures" / "stages"
GATES_YAML = SCRIPTS.parent / "reference" / "gates.yaml"
PD_PCB = STAGES / "pd_trigger" / "route" / "pd-trigger.kicad_pcb"
PD_GERBERS = STAGES / "pd_trigger" / "fab" / "gerbers"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import checklib  # noqa: E402
import gate  # noqa: E402
import verify_all  # noqa: E402


# --------------------------------------------------------------- helpers

def _board(tmp_path: Path, name: str = "b.kicad_pcb") -> Path:
    p = tmp_path / name
    p.write_text('(kicad_pcb (version 20260101) (generator "test"))',
                 encoding="utf-8")
    return p


def _creep(net="V48_RTN", sev="error"):
    return {"check": "check_creepage", "kind": "creepage", "severity": sev,
            "net": net, "refs": ["U22"], "pos": [1.0, 1.0],
            "msg": "x", "source": "check_creepage"}


def _verify_report(board: Path, violations=None) -> dict:
    vios = violations or []
    rep = {"script": "verify_all", "board": board.name,
           "status": "violations" if vios else "pass",
           "counts": checklib.summarize(vios),
           "checks": {}, "violations": vios}
    return checklib.stamp(rep, board)


def _gate_main(tmp_path: Path, report: dict, gate_name="verify",
               extra_argv=(), input_arg: str | None = None) -> int:
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(report), encoding="utf-8")
    argv = ["--gate", gate_name, "--report", str(rp), *extra_argv]
    if input_arg:
        argv.append(input_arg)
    return gate.main(argv)


# ------------------------------------------- report validation (codex C4)

def test_valid_report_passes(tmp_path):
    """Control: a fresh, matching, stamped report grades normally."""
    board = _board(tmp_path)
    assert _gate_main(tmp_path, _verify_report(board)) == 0


def test_valid_report_with_violations_exit1(tmp_path):
    """A valid report with findings is GRADED (exit 1), not refused."""
    board = _board(tmp_path)
    rep = _verify_report(board, [_creep()])
    assert _gate_main(tmp_path, rep) == 1


def test_error_shaped_report_refused(tmp_path):
    board = _board(tmp_path)
    rep = _verify_report(board)
    rep["status"] = "error"
    assert _gate_main(tmp_path, rep) == 2


def test_empty_report_refused(tmp_path):
    _board(tmp_path)
    assert _gate_main(tmp_path, {}) == 2


def test_missing_violations_key_refused(tmp_path):
    """C4: a missing `violations` key is INVALID, never an empty pass."""
    board = _board(tmp_path)
    rep = _verify_report(board)
    del rep["violations"]
    assert _gate_main(tmp_path, rep) == 2


def test_missing_schema_version_refused(tmp_path):
    board = _board(tmp_path)
    rep = _verify_report(board)
    del rep["report_schema"]
    assert _gate_main(tmp_path, rep) == 2


def test_missing_digest_refused(tmp_path):
    board = _board(tmp_path)
    rep = _verify_report(board)
    del rep["input_digest"]
    assert _gate_main(tmp_path, rep) == 2


def test_stale_report_refused_on_input_change(tmp_path):
    """The board changed after the report was generated -> digest mismatch."""
    board = _board(tmp_path)
    rep = _verify_report(board)
    board.write_text(board.read_text(encoding="utf-8")
                     .replace("(version 20260101)", "(version 20260102)"),
                     encoding="utf-8")
    assert _gate_main(tmp_path, rep) == 2


def test_uuid_churn_does_not_stale_a_report(tmp_path):
    """The digest is NORMALIZED (statelib): a UUID restamp is not a design
    change and must not refuse the report."""
    board = _board(tmp_path)
    board.write_text('(kicad_pcb (version 20260101) (generator "test") '
                     '(uuid "aaaaaaaa-1111-2222-3333-444444444444"))',
                     encoding="utf-8")
    rep = _verify_report(board)
    board.write_text(board.read_text(encoding="utf-8").replace(
        "aaaaaaaa-1111-2222-3333-444444444444",
        "bbbbbbbb-1111-2222-3333-444444444444"), encoding="utf-8")
    assert _gate_main(tmp_path, rep) == 0


def test_old_report_refused_and_bound_configurable(tmp_path):
    from datetime import datetime, timedelta, timezone
    board = _board(tmp_path)
    rep = _verify_report(board)
    rep["generated_at"] = (datetime.now(timezone.utc)
                           - timedelta(hours=48)).isoformat(
                               timespec="seconds")
    assert _gate_main(tmp_path, rep) == 2
    assert _gate_main(tmp_path, rep,
                      extra_argv=["--max-report-age-h", "100"]) == 0


def test_future_report_refused(tmp_path):
    from datetime import datetime, timedelta, timezone
    board = _board(tmp_path)
    rep = _verify_report(board)
    rep["generated_at"] = (datetime.now(timezone.utc)
                           + timedelta(hours=2)).isoformat(timespec="seconds")
    assert _gate_main(tmp_path, rep) == 2


def test_wrong_input_refused(tmp_path):
    """Recorded input != the positionally given input -> refused."""
    board = _board(tmp_path)
    other = _board(tmp_path, "other.kicad_pcb")
    rep = _verify_report(board)
    assert _gate_main(tmp_path, rep, input_arg=str(other)) == 2
    # control: naming the SAME input passes
    assert _gate_main(tmp_path, rep, input_arg=str(board)) == 0


def test_recorded_input_gone_refused(tmp_path):
    board = _board(tmp_path)
    rep = _verify_report(board)
    board.unlink()
    assert _gate_main(tmp_path, rep) == 2


def test_wrong_tool_refused(tmp_path):
    """A kc DRC report must not grade the erc gate (same producer, wrong
    tool) and a verify_all report must not grade a drc gate (wrong producer)."""
    board = _board(tmp_path)
    kc_rep = checklib.stamp({
        "script": "kc", "tool": "drc", "input": str(board),
        "counts": {"total": 0}, "status": "pass", "violations": []}, board)
    sch = tmp_path / "b.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")
    assert _gate_main(tmp_path, kc_rep, gate_name="erc") == 2
    assert _gate_main(tmp_path, _verify_report(board), gate_name="drc") == 2


def test_validate_report_returns_input(tmp_path):
    board = _board(tmp_path)
    rep = _verify_report(board)
    got = gate.validate_report("verify", {"tool": "verify"}, rep, None)
    assert got == board
    with pytest.raises(RuntimeError, match="refused"):
        gate.validate_report("verify", {"tool": "verify"}, "not-a-dict", None)


# ------------------------------------- coverage through evaluate (C7 tail)

def test_evaluate_moves_fully_waived_check_in_coverage():
    g = {"tool": "verify", "fail_severities": ["error"], "max_count": 0}
    report = {"script": "verify_all", "status": "violations",
              "counts": {"total": 1}, "violations": [_creep()],
              "coverage": {"strict": True, "required": ["check_creepage"],
                           "ran": ["check_creepage"], "passed": [],
                           "failed": ["check_creepage"], "waived": [],
                           "not_applicable": {}, "skipped_error": {}}}
    wv = [{"kind": "creepage", "net": "V48_RTN", "reason": "r",
           "approved": "H4"}]
    res = gate.evaluate("verify", g, report, waivers=wv)
    assert res["status"] == "pass"
    assert res["coverage"]["failed"] == []
    assert res["coverage"]["waived"] == ["check_creepage"]
    # the report object itself is never mutated
    assert report["coverage"]["failed"] == ["check_creepage"]


# ------------------------------------- waivers that match nothing (#677)

_VG = {"tool": "verify", "fail_severities": ["error"], "max_count": 0}


def _wv(net="V48_RTN", **over):
    return {"check": "check_creepage", "kind": "creepage", "net": net,
            "reason": "r", "approved": "test 2026-11-05", **over}


def test_waiver_on_missing_net_is_named():
    """Known answer: a waiver on VBUS_TYPO against a board with no such net
    waives nothing and says so, naming the entry and the nets that do fire."""
    rep = {"violations": [_creep()], "counts": {"total": 1}}
    res = gate.evaluate("verify", _VG, rep,
                        waivers=[_wv(), _wv(net="VBUS_TYPO")])
    assert res["status"] == "pass" and res["waived_count"] == 1
    assert [u["index"] for u in res["waivers_unmatched"]] == [1]
    u = res["waivers_unmatched"][0]
    assert u["waiver"]["net"] == "VBUS_TYPO"
    assert "waiver 1" in u["problem"] and "VBUS_TYPO" in u["problem"]
    assert "V48_RTN" in u["problem"]       # the typo's fix is in the message


def test_matching_waiver_gives_no_unmatched_entry():
    rep = {"violations": [_creep()], "counts": {"total": 1}}
    res = gate.evaluate("verify", _VG, rep, waivers=[_wv()])
    assert res["status"] == "pass"
    assert "waivers_unmatched" not in res


def test_waiver_on_below_bar_finding_is_not_unmatched():
    """A waiver covering a warning does nothing at the gate, but it matches
    a finding and bin/score.py honours it: it is not dead."""
    rep = {"violations": [_creep(sev="warning")], "counts": {"total": 1}}
    res = gate.evaluate("verify", _VG, rep, waivers=[_wv()])
    assert "waivers_unmatched" not in res


def test_invalid_waiver_is_not_also_unmatched():
    """A broken binding already reports through waivers_invalid."""
    rep = {"violations": [_creep()], "counts": {"total": 1},
           "input_digest": "sexpr_no_uuid:" + "1" * 64}
    stale = _wv(net="VBUS_TYPO", artifact="sexpr_no_uuid:" + "0" * 64)
    res = gate.evaluate("verify", _VG, rep, waivers=[stale])
    assert res["waivers_invalid"] and "waivers_unmatched" not in res


def _durable(rep, net):
    return _wv(net=net, artifact=rep["input_digest"],
               checker_version=rep["checker_version"], expires="2099-01-01")


def test_strict_gate_fails_on_unmatched_waiver(tmp_path):
    board = _board(tmp_path)
    rep = _verify_report(board, [_creep()])
    g = dict(_VG, strict=True)
    res = gate.evaluate("verify_release", g, rep,
                        waivers=[_durable(rep, "V48_RTN")])
    assert res["status"] == "pass" and "waivers_unmatched" not in res
    res = gate.evaluate("verify_release", g, rep,
                        waivers=[_durable(rep, "V48_RTN"),
                                 _durable(rep, "VBUS_TYPO")])
    assert res["status"] == "fail" and res["failing_count"] == 0
    assert res["waivers_unmatched"][0]["waiver"]["net"] == "VBUS_TYPO"


def test_gate_main_warns_on_unmatched_waiver(tmp_path, capsys):
    """Non-strict keeps grading (exit 0) and says so on stderr too."""
    board = _board(tmp_path)
    sidecar = tmp_path / "w.json"
    sidecar.write_text(json.dumps({"waivers": [_wv(), _wv(net="VBUS_TYPO")]}),
                       encoding="utf-8")
    rep = _verify_report(board, [_creep()])
    assert _gate_main(tmp_path, rep,
                      extra_argv=("--waivers", str(sidecar))) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["waivers_unmatched"][0]["index"] == 1
    assert "warning: waiver 1" in cap.err and "VBUS_TYPO" in cap.err


def test_gate_main_prints_invalid_waiver(tmp_path, capsys):
    """#709: a gate that throws out an expired waiver names the entry and its
    problem on stderr, like the unmatched ones. This is the lenient path, which
    keeps grading on what is left; a strict gate fails on it (#725)."""
    board = _board(tmp_path)
    rep = _verify_report(board, [_creep()])
    sidecar = tmp_path / "w.json"
    sidecar.write_text(json.dumps({"waivers": [
        _durable(rep, "V48_RTN"),
        dict(_durable(rep, "V48_RTN"), expires="2000-01-01")]}),
        encoding="utf-8")
    _gate_main(tmp_path, rep, extra_argv=("--waivers", str(sidecar)),
               gate_name="verify")
    cap = capsys.readouterr()
    res = json.loads(cap.out)
    assert res["waivers_invalid"][0]["index"] == 1
    lines = [l for l in cap.err.splitlines() if "waiver 1" in l]
    assert len(lines) == 1 and "expired" in lines[0]


def _strict_main(tmp_path, capsys, rep, waivers):
    """Run the strict gate through main(): (exit code, result, stderr)."""
    sidecar = tmp_path / "w.json"
    sidecar.write_text(json.dumps({"waivers": waivers}), encoding="utf-8")
    code = _gate_main(tmp_path, rep, extra_argv=("--waivers", str(sidecar)),
                      gate_name="verify_release")
    cap = capsys.readouterr()
    return code, json.loads(cap.out), cap.err


@pytest.mark.parametrize("over, says", [
    ({"expires": "2000-01-01"}, "expired"),
    ({"expires": "someday"}, "unparsable expires"),
    ({"artifact": "sexpr_no_uuid:" + "0" * 64}, "approved against artifact"),
    ({"checker_version": -1}, "approved under checker_version"),
    ({"refs": 5}, "refs must be a list"),
    ({"refs": "U22"}, "refs must be a list"),
    ({"refs": [["U22"]]}, "refs must be a list"),
])
def test_strict_gate_grades_invalid_durable_waiver(tmp_path, capsys, over,
                                                   says):
    """#725 known answer: a strict gate with one broken durable waiver gives
    a graded result (exit 1, not a traceback), the waiver named in
    waivers_invalid and not applied, so its finding still fails."""
    board = _board(tmp_path)
    rep = _verify_report(board, [_creep()])
    code, res, err = _strict_main(tmp_path, capsys, rep,
                                  [dict(_durable(rep, "V48_RTN"), **over)])
    assert code == 1
    assert res["script"] == "gate" and res["status"] == "fail"
    assert res["failing_count"] == 1 and res["waived_count"] == 0
    bad = res["waivers_invalid"]
    assert [b["index"] for b in bad] == [0]
    assert bad[0]["waiver"]["net"] == "V48_RTN"
    assert says in bad[0]["problems"][0]
    assert "waivers_unmatched" not in res
    assert "FAIL: waiver 0" in err and says in err


def test_strict_gate_grades_waiver_missing_durable_fields(tmp_path, capsys):
    """#725: a legacy reason+approved waiver lands the same way, naming each
    missing binding."""
    board = _board(tmp_path)
    rep = _verify_report(board, [_creep()])
    code, res, err = _strict_main(tmp_path, capsys, rep, [_wv()])
    assert code == 1 and res["status"] == "fail" and res["failing_count"] == 1
    probs = res["waivers_invalid"][0]["problems"]
    assert len(probs) == 1 and "not durable" in probs[0]
    assert "artifact/checker_version/expires" in probs[0]
    assert "FAIL: waiver 0" in err and "not durable" in err


def test_strict_gate_invalid_waiver_blocks_even_with_no_finding_left(
        tmp_path, capsys):
    """#725: only one entry is broken and the good one covers the finding.
    Nothing is left failing, but a release gate still blocks on a dead
    waiver, as it does on an unmatched one."""
    board = _board(tmp_path)
    rep = _verify_report(board, [_creep()])
    code, res, _ = _strict_main(tmp_path, capsys, rep, [
        _durable(rep, "V48_RTN"),
        dict(_durable(rep, "VBUS"), expires="2000-01-01")])
    assert code == 1 and res["status"] == "fail"
    assert res["failing_count"] == 0 and res["waived_count"] == 1
    assert [b["index"] for b in res["waivers_invalid"]] == [1]


def test_strict_gate_clean_durable_waiver_unchanged(tmp_path, capsys):
    """Control: a good durable waiver still waives, exit 0, no invalid list."""
    board = _board(tmp_path)
    rep = _verify_report(board, [_creep()])
    code, res, err = _strict_main(tmp_path, capsys, rep,
                                  [_durable(rep, "V48_RTN")])
    assert code == 0 and res["status"] == "pass" and res["waived_count"] == 1
    assert "waivers_invalid" not in res and "waiver 0" not in err


# #731: a waiver file in the wrong shape, on a lenient and a strict gate
_BOTH_GATES = [("verify", "warning"), ("verify_release", "FAIL")]
_NOT_OBJECTS = ["x", 1, None, True, ["U22"]]
_BAD_REFS = [5, "U22", [["U22"]], ["U22", 5], "", 0, {}, False]


def _sidecar_main(tmp_path, capsys, rep, doc, gate_name):
    """Run a gate through main() on a sidecar holding doc: (exit code,
    stdout JSON, stderr)."""
    sidecar = tmp_path / "w.json"
    sidecar.write_text(json.dumps(doc), encoding="utf-8")
    code = _gate_main(tmp_path, rep, extra_argv=("--waivers", str(sidecar)),
                      gate_name=gate_name)
    cap = capsys.readouterr()
    return code, json.loads(cap.out), cap.err


def _entry_for(gate_name, rep, **over):
    """A waiver of the V48_RTN finding that is good on this gate but for over:
    a strict gate needs the durable fields, a lenient one doesn't."""
    good = _wv() if gate_name == "verify" else _durable(rep, "V48_RTN")
    return {**good, **over}


@pytest.mark.parametrize("gate_name, level", _BOTH_GATES)
@pytest.mark.parametrize("entry", _NOT_OBJECTS)
def test_entry_not_an_object_is_graded(tmp_path, capsys, gate_name, level,
                                       entry):
    """{"waivers": ["x"]} gives the gate's normal JSON with the entry named by
    index, on every gate. It matches nothing, so the finding still fails."""
    rep = _verify_report(_board(tmp_path), [_creep()])
    code, res, err = _sidecar_main(tmp_path, capsys, rep, {"waivers": [entry]},
                                   gate_name)
    assert code == 1
    assert res["script"] == "gate" and res["status"] == "fail"
    assert res["failing_count"] == 1 and res["waived_count"] == 0
    bad = res["waivers_invalid"]
    assert [b["index"] for b in bad] == [0]
    assert "must be an object" in bad[0]["problems"][0]
    assert repr(entry) in bad[0]["problems"][0]
    assert f"{level}: waiver 0 ignored (invalid)" in err


@pytest.mark.parametrize("gate_name, level", _BOTH_GATES)
@pytest.mark.parametrize("refs", _BAD_REFS)
def test_bad_refs_are_graded(tmp_path, capsys, gate_name, level, refs):
    """refs: 5 raised and refs: "U22" read as set("U22") on a lenient gate;
    refs: "" or 0 dropped the refs scope, so the waiver covered every ref on
    the net. All of them are invalid and match nothing, on both gates."""
    rep = _verify_report(_board(tmp_path), [_creep()])
    code, res, err = _sidecar_main(
        tmp_path, capsys, rep, {"waivers": [_entry_for(gate_name, rep,
                                                       refs=refs)]}, gate_name)
    assert code == 1
    assert res["status"] == "fail"
    assert res["failing_count"] == 1 and res["waived_count"] == 0
    bad = res["waivers_invalid"]
    assert [b["index"] for b in bad] == [0]
    assert bad[0]["waiver"]["net"] == "V48_RTN"
    assert "refs must be a list" in bad[0]["problems"][0]
    assert "waivers_unmatched" not in res
    assert f"{level}: waiver 0 ignored (invalid)" in err


@pytest.mark.parametrize("bad", [
    "x", 1, {"refs": 5}, {"refs": "U22"}])
def test_bad_entry_next_to_a_good_one(tmp_path, capsys, bad):
    """The good waiver still applies and the index names the bad one. A
    lenient gate passes with a warning; a strict gate fails on the bad entry
    even with no finding left."""
    rep = _verify_report(_board(tmp_path), [_creep()])
    for gate_name, want_code, want_status in (("verify", 0, "pass"),
                                              ("verify_release", 1, "fail")):
        entry = _entry_for(gate_name, rep, **bad) if isinstance(bad, dict) \
            else bad
        code, res, _ = _sidecar_main(
            tmp_path, capsys, rep,
            {"waivers": [_entry_for(gate_name, rep), entry]}, gate_name)
        assert (code, res["status"]) == (want_code, want_status), gate_name
        assert res["failing_count"] == 0 and res["waived_count"] == 1
        assert [b["index"] for b in res["waivers_invalid"]] == [1]


@pytest.mark.parametrize("gate_name", ["verify", "verify_release"])
@pytest.mark.parametrize("refs", [None, []])
def test_empty_refs_stay_an_unscoped_waiver(tmp_path, capsys, gate_name, refs):
    """Control: null and [] are how a waiver says no refs scope."""
    rep = _verify_report(_board(tmp_path), [_creep()])
    code, res, _ = _sidecar_main(
        tmp_path, capsys, rep, {"waivers": [_entry_for(gate_name, rep,
                                                       refs=refs)]}, gate_name)
    assert code == 0 and res["waived_count"] == 1
    assert "waivers_invalid" not in res


@pytest.mark.parametrize("gate_name", ["verify", "verify_release"])
@pytest.mark.parametrize("doc", [
    [1], [], "x", 5, None, {}, {"waivers": "x"}, {"waivers": None}])
def test_wrong_top_level_is_one_clear_refusal(tmp_path, capsys, gate_name, doc):
    """A file with no list of waivers can't be graded entry by entry: exit 2,
    an error naming the file, and no traceback."""
    rep = _verify_report(_board(tmp_path), [_creep()])
    code, res, _ = _sidecar_main(tmp_path, capsys, rep, doc, gate_name)
    assert code == 2
    assert res["script"] == "gate" and res["status"] == "error"
    assert "Traceback" not in res["error"] and "\n" not in res["error"]
    assert str(tmp_path / "w.json") in res["error"]
    assert "'waivers' list" in res["error"]


@pytest.mark.parametrize("w", [
    *_NOT_OBJECTS, *({**_wv(), "refs": r} for r in _BAD_REFS)])
def test_waiver_matches_is_false_for_a_bad_shape(w):
    """score.py, score_checks and bench call waiver_matches without grading
    the entry first: a bad one waives nothing and doesn't raise."""
    assert gate.waiver_matches(w, _creep()) is False


# ------------------------------------------------ commit scoping (codex C4)

def _tmp_repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()

    def git(*a):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True,
                              text=True)
    assert git("init").returncode == 0
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    return repo, git


def _ws_board(repo: Path, name="foo") -> Path:
    ws = repo / "boards" / name / "kicad"
    ws.mkdir(parents=True)
    board = ws / f"{name}.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    return board


def test_commit_without_scope_refused(tmp_path):
    """C4 accept: an unscoped commit is refused - no input, no commit."""
    repo, git = _tmp_repo(tmp_path)
    (repo / "a.txt").write_text("hi", encoding="utf-8")
    res = gate.git_commit_on_pass("gate pass", repo)
    assert res["committed"] is False and res["ok"] is False
    assert "boards/<name>" in res["reason"]
    assert "a.txt" in git("status", "--porcelain").stdout  # untouched


def test_commit_non_boards_input_refused(tmp_path):
    """The git add -A fallback is GONE: an input outside boards/ refuses
    even on an otherwise clean tree."""
    repo, git = _tmp_repo(tmp_path)
    ws = repo / "ws"
    ws.mkdir()
    board = ws / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    res = gate.git_commit_on_pass("gate pass", repo, input_file=board)
    assert res["committed"] is False and res["ok"] is False
    assert "boards/<name>" in res["reason"]


def test_commit_prestaged_outside_scope_refused(tmp_path):
    """Pre-staged index entries outside the workspace would ride along in
    `git commit` - refuse, and leave both index and worktree untouched."""
    repo, git = _tmp_repo(tmp_path)
    board = _ws_board(repo)
    (repo / "other.txt").write_text("parallel WIP", encoding="utf-8")
    git("add", "other.txt")
    res = gate.git_commit_on_pass("gate pass", repo, input_file=board)
    assert res["committed"] is False and res["ok"] is False
    assert "pre-staged" in res["reason"] and "other.txt" in res["reason"]
    staged = git("diff", "--cached", "--name-only").stdout
    assert "other.txt" in staged            # still staged, not committed
    assert "boards/foo" not in staged       # ws file was never staged


def test_commit_prestaged_inside_scope_ok(tmp_path):
    repo, git = _tmp_repo(tmp_path)
    board = _ws_board(repo)
    git("add", "--", "boards/foo")
    res = gate.git_commit_on_pass("gate pass", repo, input_file=board)
    assert res["committed"] is True and res["ok"] is True


def test_commit_nothing_to_do_is_ok(tmp_path):
    repo, git = _tmp_repo(tmp_path)
    board = _ws_board(repo)
    res = gate.git_commit_on_pass("first", repo, input_file=board)
    assert res["committed"] is True and res["ok"] is True
    res2 = gate.git_commit_on_pass("noop", repo, input_file=board)
    assert res2["committed"] is False and res2["ok"] is True


def test_cli_exit2_when_requested_commit_refused(tmp_path):
    """Gate PASS + --commit that cannot occur (input outside boards/) is an
    OPERATIONAL error: exit 2, not a silent pass. Safe against the real
    repo: the refusal short-circuits before any git mutation."""
    board = _board(tmp_path)
    rep = _verify_report(board)
    rc = _gate_main(tmp_path, rep, extra_argv=["--commit", "u2 test"])
    assert rc == 2


# --------------------------------------- verify_all strict coverage (C7)

BLINKY = GOLDEN / "blinky2" / "blinky2.kicad_pcb"
ALL_CHECKS = [c["name"] for c in verify_all.CHECKS]
# check_ratings needs the workspace parts dir, which the golden boards lack
CONSTRAINT_NEEDING = ["check_return_path", "check_current",
                      "check_decoupling", "check_creepage",
                      "check_thermal", "check_pdn", "check_ratings",
                      "check_power_path"]


def test_verify_all_nonstrict_skips_are_visible(tmp_path):
    summary, _ = verify_all.run(["--pcb", str(BLINKY),
                                 "--reports-dir", str(tmp_path / "rep")])
    cov = summary["coverage"]
    assert cov["strict"] is False
    assert cov["required"] == ALL_CHECKS
    assert sorted(cov["skipped_error"]) == sorted(CONSTRAINT_NEEDING)
    assert summary["status"] != "error"          # exploratory: still lenient
    for n in CONSTRAINT_NEEDING:
        assert summary["checks"][n]["status"] == "skipped"
    # the summary is a stamped, gate-consumable report
    assert summary["report_schema"] == checklib.REPORT_SCHEMA
    assert summary["input_digest"].startswith("sexpr_no_uuid:")


def test_verify_all_strict_missing_inputs_fail(tmp_path):
    summary, _ = verify_all.run(["--pcb", str(BLINKY), "--strict",
                                 "--reports-dir", str(tmp_path / "rep")])
    assert summary["status"] == "error"
    for n in CONSTRAINT_NEEDING:
        assert summary["checks"][n]["status"] == "skipped_error"
    assert summary["coverage"]["strict"] is True
    assert sorted(summary["coverage"]["skipped_error"]) \
        == sorted(CONSTRAINT_NEEDING)


def test_verify_all_strict_with_declared_na_passes(tmp_path):
    """Strict + properly declared not_applicable (reason + approver) is the
    sanctioned way to narrow coverage - and it is visible in the matrix."""
    cpath = tmp_path / "constraints.json"
    cpath.write_text(json.dumps({"verification": {"not_applicable": {
        n: {"reason": "no such nets on this board", "approved": "test 2026"}
        for n in CONSTRAINT_NEEDING}}}), encoding="utf-8")
    summary, _ = verify_all.run(["--pcb", str(BLINKY), "--strict",
                                 "--constraints", str(cpath),
                                 "--reports-dir", str(tmp_path / "rep")])
    # blinky2's two stock 1.0 mm header drills under IPC-2222 (#474) are
    # warnings, below the fail bar; nothing else
    assert summary["status"] in ("pass", "violations")
    assert summary["counts"]["by_severity"] == {"warning": 2}
    assert {k for k, v in summary["counts"]["by_check"].items() if v} \
        == {"check_connector_fit"}
    cov = summary["coverage"]
    board_only = ["check_diffpair", "check_silk", "check_courtyard", "check_route_style",
                  "check_connector_fit", "check_ep_paste",
                  "check_mating", "check_mate_pins", "check_bom_sync",
                  "check_model_paths", "check_antenna", "check_straps",
                  "check_model_fit", "check_usb_phy", "check_vias",
                  "check_reset_drive", "check_si", "check_fp_attr",
                  "check_length_groups", "check_cap_load",
                  "check_wicking", "check_ddr_timing",
                  "check_testpoints", "check_rf_match", "check_surge",
                  "check_via_stubs", "check_isolation",
                  "check_body_clash", "check_kelvin"]
    assert sorted(cov["required"]) == sorted(board_only)
    assert sorted(cov["ran"]) == sorted(board_only)
    assert cov["skipped_error"] == {}
    assert set(cov["not_applicable"]) == set(CONSTRAINT_NEEDING)
    for e in cov["not_applicable"].values():
        assert e["reason"] and e["approved"]


def test_verify_all_na_missing_approver_refused(tmp_path):
    cpath = tmp_path / "constraints.json"
    cpath.write_text(json.dumps({"verification": {"not_applicable": {
        "check_thermal": {"reason": "r"}}}}), encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="approved"):
        verify_all.run(["--pcb", str(BLINKY), "--constraints", str(cpath),
                        "--reports-dir", str(tmp_path / "rep")])


@pytest.mark.parametrize("approved", [
    "PENDING", "pending owner review", "TBD", "TODO", "N/A", "none",
    "not approved", "Not yet", "awaiting H4", "<who> <when>", "---", True])
def test_placeholder_approval_refused(approved):
    """Issue #27: any non-empty `approved` used to pass, even PENDING."""
    assert checklib.approval_problem(approved)


@pytest.mark.parametrize("approved", [
    "H4", "owner", "a", "Test Owner 2026-08-14", "Ihsan S. 2026-08-08",
    "Nora 2026-10-28"])
def test_real_approval_accepted(approved):
    assert checklib.approval_problem(approved) is None


def test_gate_waiver_pending_refused(tmp_path):
    p = tmp_path / "verify-waivers.json"
    p.write_text(json.dumps({"waivers": [
        {"kind": "creepage", "net": "V48_RTN", "reason": "r",
         "approved": "PENDING"}]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="placeholder"):
        gate.load_waivers(p)
    p.write_text(json.dumps({"waivers": [
        {"kind": "creepage", "net": "V48_RTN", "reason": "r",
         "approved": "H4 2026-10-28"}]}), encoding="utf-8")
    assert len(gate.load_waivers(p)) == 1


def test_verify_all_na_pending_approver_refused(tmp_path):
    cpath = tmp_path / "constraints.json"
    cpath.write_text(json.dumps({"verification": {"not_applicable": {
        "check_thermal": {"reason": "r", "approved": "TBD"}}}}),
        encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="placeholder"):
        verify_all.run(["--pcb", str(BLINKY), "--constraints", str(cpath),
                        "--reports-dir", str(tmp_path / "rep")])


def test_verify_all_na_unknown_check_refused(tmp_path):
    """A typo in an N/A declaration must never silently disable a check."""
    cpath = tmp_path / "constraints.json"
    cpath.write_text(json.dumps({"verification": {"not_applicable": {
        "check_termal": {"reason": "r", "approved": "a"}}}}),
        encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="unknown check"):
        verify_all.run(["--pcb", str(BLINKY), "--constraints", str(cpath),
                        "--reports-dir", str(tmp_path / "rep")])


def test_coverage_matrix_buckets():
    results = [
        {"name": "a", "status": "pass", "violations": []},
        {"name": "b", "status": "violations",
         "violations": [{"severity": "error"}]},
        {"name": "c", "status": "violations",
         "violations": [{"severity": "warning"}]},
        {"name": "d", "status": "skipped", "violations": [],
         "reason": "no constraints"},
        {"name": "e", "status": "error", "violations": [],
         "error": "boom"},
        {"name": "f", "status": "not_applicable", "violations": []},
    ]
    na = {"f": {"reason": "r", "approved": "a"}}
    cov = verify_all.coverage_matrix(results, na, strict=True)
    assert cov["required"] == ["a", "b", "c", "d", "e"]
    assert cov["ran"] == ["a", "b", "c"]
    assert cov["failed"] == ["b"]
    assert cov["passed"] == ["a", "c"]      # warnings-only still passes
    assert cov["skipped_error"] == {"d": "no constraints", "e": "boom"}
    assert cov["not_applicable"]["f"]["approved"] == "a"


# ------------------------------------------- place_metrics strict (C7)

def test_place_metrics_strict_coverage(tmp_path):
    import place_metrics
    board = tmp_path / "blinky2.kicad_pcb"
    shutil.copy(BLINKY, board)              # no sidecars beside the copy
    payload, _ = place_metrics.run(["--pcb", str(board)])
    assert payload["status"] != "error"
    assert sorted(payload["coverage"]["skipped_error"]) == \
        ["decoupler_distance", "edges", "keepouts"]
    strict, _ = place_metrics.run(["--pcb", str(board), "--strict"])
    assert strict["status"] == "error"
    assert "never ran" in strict["error"]


def test_place_metrics_full_sidecars_full_coverage():
    import place_metrics
    payload, _ = place_metrics.run(["--pcb", str(BLINKY), "--strict"])
    assert payload["status"] != "error"
    cov = payload["coverage"]
    assert cov["skipped_error"] == {}
    assert sorted(cov["ran"]) == sorted(cov["required"])


# ------------------------------ dfm strict: the carrier scenario (accept)

def _tampered_gerbers(tmp_path: Path) -> Path:
    """Copy the frozen pd-trigger gerbers and break the outline: turn one
    mid-contour Edge.Cuts DRAW into a MOVE (D01 -> D02), splitting the ring
    into two open chains that cannot polygonize (the carrier's 1 nm joint
    mismatch, exaggerated far beyond any snap tolerance). Deleting a draw
    would NOT work: gerber coordinates are modal, so the next draw simply
    continues from the previous point and the contour stays closed."""
    gdir = tmp_path / "gerbers"
    shutil.copytree(PD_GERBERS, gdir)
    edge = gdir / "pd-trigger-Edge_Cuts.gm1"
    lines = edge.read_text(encoding="utf-8").splitlines()
    draws = [i for i, ln in enumerate(lines)
             if ln.endswith("D01*") and "I" not in ln]
    assert draws, "fixture has no linear Edge.Cuts draws"
    mid = draws[len(draws) // 2]
    lines[mid] = lines[mid].replace("D01*", "D02*")
    edge.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return gdir


def test_dfm_intact_runs_edge_checks(tmp_path):
    import dfm_check
    payload = dfm_check.run(PD_PCB, fab_dir=PD_GERBERS, polarity=False)
    cov = payload["coverage"]
    assert "copper_to_edge" in cov["ran"]
    assert "hole_to_edge" in cov["ran"]
    assert "copper_to_edge" not in cov["skipped_error"]


def test_dfm_open_outline_is_visible_and_fails_strict(tmp_path):
    """The carrier's historic 'dfm pass with edge checks silently skipped'
    scenario: an unclosable Edge.Cuts now yields skipped_error coverage for
    both edge-distance families and REFUSES under strict."""
    import dfm_check
    gdir = _tampered_gerbers(tmp_path)
    payload = dfm_check.run(PD_PCB, fab_dir=gdir, polarity=False)
    cov = payload["coverage"]
    assert "copper_to_edge" in cov["skipped_error"]
    assert "hole_to_edge" in cov["skipped_error"]
    assert "closed outline" in cov["skipped_error"]["copper_to_edge"]
    assert any(v.get("kind") == "dfm_open_outline"
               for v in payload["violations"])
    strict = dfm_check.run(PD_PCB, fab_dir=gdir, polarity=False, strict=True)
    assert strict["status"] == "error"
    assert "copper_to_edge" in strict["error"]


# ----------------------------------------------- release gates (gates.yaml)

def test_release_gates_are_strict():
    gates = yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]
    assert gates["verify_release"]["strict"] is True
    assert gates["verify_release"]["tool"] == "verify"
    assert gates["dfm_release"]["strict"] is True
    assert gates["dfm_release"]["tool"] == "dfm"
    # the exploratory gates stay lenient
    assert not gates["verify"].get("strict")
    assert not gates["dfm"].get("strict")


# ------------------------------------------------- stamp mechanics (C4)

def test_stamp_fields_and_uuid_invariance(tmp_path):
    a = tmp_path / "a.kicad_pcb"
    a.write_text('(kicad_pcb (uuid "11111111-2222-3333-4444-555555555555") '
                 '(version 1))', encoding="utf-8")
    pa = checklib.report("x", a, [])
    assert pa["report_schema"] == checklib.REPORT_SCHEMA
    assert pa["generated_at"] and pa["input"] == str(a)
    b = tmp_path / "b.kicad_pcb"
    b.write_text('(kicad_pcb (uuid "66666666-7777-8888-9999-000000000000") '
                 '(version 1))', encoding="utf-8")
    pb = checklib.report("x", b, [])
    assert pa["input_digest"] == pb["input_digest"]      # UUIDs are churn
    c = tmp_path / "c.kicad_pcb"
    c.write_text('(kicad_pcb (version 2))', encoding="utf-8")
    pc = checklib.report("x", c, [])
    assert pc["input_digest"] != pa["input_digest"]      # content is not


def test_commit_goes_into_the_boards_repo(tmp_path, monkeypatch):
    """Boards live in their own repo (HWDE_BOARDS_ROOT, any dir name): a
    workspace there is a commit scope, and the commit lands in THAT repo,
    not hwde's. With the root unset the same path is no workspace at all."""
    root, git = _tmp_repo(tmp_path)                 # its dir is named "r"
    board = root / "foo" / "kicad" / "foo.kicad_pcb"
    board.parent.mkdir(parents=True)
    board.write_text("(kicad_pcb)", encoding="utf-8")

    monkeypatch.setenv("HWDE_BOARDS_ROOT", str(tmp_path / "elsewhere"))
    assert gate.workspace_dir(board) is None
    assert gate.commit_repo(board) == gate.env.repo_root()

    monkeypatch.setenv("HWDE_BOARDS_ROOT", str(root))
    assert gate.workspace_dir(board) == (root / "foo").resolve()
    assert gate.commit_repo(board).resolve() == root.resolve()
    res = gate.git_commit_on_pass("gate pass", gate.commit_repo(board),
                                  input_file=board)
    assert res["committed"] is True and res["ok"] is True
    assert "foo/kicad/foo.kicad_pcb" in git("show", "--name-only",
                                            "HEAD").stdout
