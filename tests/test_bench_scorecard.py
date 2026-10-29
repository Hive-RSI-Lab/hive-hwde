"""bench.py --scorecard and lib/evalcard.py: per-board design scorecards.

Offline. Each test builds its own state: a workspace copied from the golden
blinky2 board under tmp_path, its own gate reports, its own results root.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bench  # noqa: E402
import evalcard  # noqa: E402
from checklib import CheckError  # noqa: E402

GOLDEN = REPO / "tests" / "golden" / "blinky2"


def _gate(reports: Path, gate: str, failing=()):
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"gate-{gate}.json").write_text(json.dumps(
        {"gate": gate, "status": "fail" if failing else "pass",
         "failing": list(failing)}), encoding="utf-8")


def _workspace(tmp_path: Path, name="PCB-9999-A_eval", gates=("erc", "drc_routed", "dfm")):
    ws = tmp_path / name
    (ws / "kicad").mkdir(parents=True)
    for f in GOLDEN.iterdir():
        if f.is_file():
            shutil.copy(f, ws / "kicad" / f.name)
    for g in gates:
        _gate(ws / "reports", g)
    return ws


# ------------------------------------------------------------ findings

def test_finding_weight_follows_verdict_then_precision():
    v = {"severity": "error", "kind": "k", "msg": "x"}
    assert evalcard.finding("check_pdn", v, "real", 0.1)["weight"] == 1.0
    assert evalcard.finding("check_pdn", v, "fp", 0.9)["weight"] == 0.0
    assert evalcard.finding("check_pdn", v, "untriaged", 0.3)["weight"] == 0.3
    assert evalcard.finding("check_pdn", v, "waived", 0.3)["weight"] == 0.3
    # no measured precision: the prior, not 1 and not 0
    assert evalcard.finding("check_pdn", v, "untriaged", None)["p_real"] \
        == evalcard.PRIOR_PRECISION
    w = evalcard.finding("check_pdn", {**v, "severity": "warning"}, "real", None)
    assert w["weight"] == evalcard.SEVERITY_WEIGHT["warning"]


def test_finding_shape_carries_triage_keys_and_area():
    f = evalcard.finding("check_return_path", {"kind": "k", "refs": ["U1"],
                                               "net": "SDA", "pos": [1, 2]},
                         "untriaged", None, {("check_return_path", "k"): "FM-SI-01"})
    for key in ("check", "kind", "refs", "net", "pos", "severity",
                "failure_mode", "ladder", "fix"):
        assert key in f
    assert f["area"] == "signal_integrity" and f["failure_mode"] == "FM-SI-01"
    assert evalcard.finding("check_new", {}, "untriaged", None)["area"] == "layout"


def test_fix_line_takes_the_remedy_clause_but_not_an_advisory():
    assert evalcard.fix_line({"msg": "too hot; add thermal vias"}) == "add thermal vias"
    assert evalcard.fix_line({"msg": "1 via; advisory: plane-fed"}) is None
    assert evalcard.fix_line({"msg": "no clause", "kind": "clearance"}) \
        == "see reference/remediations/clearance.md"
    assert evalcard.fix_line({"msg": "no clause", "kind": "no_such_kind"}) is None


def test_rank_puts_likely_real_errors_first():
    fs = [evalcard.finding("check_silk", {"severity": "warning"}, "untriaged", 0.5),
          evalcard.finding("check_pdn", {"severity": "error"}, "real", None),
          evalcard.finding("check_pdn", {"severity": "error"}, "fp", None)]
    assert [f["weight"] for f in evalcard.rank(fs)] == [1.0, 0.125, 0.0]


def test_area_score_is_one_over_one_plus_expected_real():
    fs = [evalcard.finding("check_pdn", {"severity": "error"}, "real", None),
          evalcard.finding("check_thermal", {"severity": "error"}, "untriaged", 0.5)]
    a = evalcard.area_scores(fs)
    assert a["power"] == {"score": 0.4, "expected_real": 1.5, "findings": 2}
    assert a["layout"]["score"] == 1.0          # an area with no findings


def test_gate_findings_state_report_and_missing(tmp_path):
    ws = tmp_path / "ws"
    _gate(ws / "reports", "drc_routed", [{"type": "clearance", "msg": "a; b"}])
    (ws / "state.json").write_text(json.dumps({"gates": {
        "erc": {"status": "pass"},                      # state says pass
        "drc_routed": {"status": "fail"},               # report has items
    }}))
    got = evalcard.gate_findings(ws)                    # dfm: in neither
    assert [(v["check"], v["kind"]) for v in got] == [
        ("gate_drc", "clearance"), ("gate_dfm", "gate_missing")]


def test_gate_failing_in_state_without_a_report(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "state.json").write_text(json.dumps({"gates": {
        "erc": {"status": "fail", "last": {"failing_count": 4}},
        "drc_routed": {"status": "pass"}, "dfm": {"status": "pass"}}}))
    got = evalcard.gate_findings(ws)
    assert [(v["check"], v["kind"]) for v in got] == [("gate_erc", "erc_fail")]
    assert "4 violation" in got[0]["msg"]


# ------------------------------------------------------------ suite score

def test_bootstrap_is_seeded_and_brackets_the_mean():
    groups = {"a": [80.0, 90.0], "b": [60.0], "c": [100.0, 95.0, 85.0]}
    one = evalcard.cluster_bootstrap(groups)
    assert one == evalcard.cluster_bootstrap(groups)
    lo, hi = one["ci95"]
    assert lo <= one["mean"] <= hi and one["clusters"] == 3 and one["n"] == 6


def test_bootstrap_with_one_cluster_has_no_interval():
    assert evalcard.cluster_bootstrap({"a": [1.0, 2.0]})["ci95"] is None
    assert evalcard.cluster_bootstrap({})["mean"] is None


def test_suite_clusters_by_the_given_key():
    cards = [evalcard.board_card(b, []) for b in ("x1", "x2", "y1")]
    s = evalcard.suite_score(cards, cluster_of=lambda c: c["board"][0])
    assert s["composite"]["clusters"] == 2 and s["boards"] == 3
    assert evalcard.suite_score(cards)["composite"]["clusters"] == 3


# ------------------------------------------------------------ store

def test_store_appends_keyed_records(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_RESULTS_ROOT", str(tmp_path / "results"))
    key = evalcard.run_key("PCB-1", harness="hwde", model="m", seed=2, detail="terse")
    p = evalcard.append("scorecard", {**key, "x": 1})
    evalcard.append("scorecard", {**key, "x": 2})
    lines = [json.loads(ln) for ln in p.read_text().splitlines()]
    assert [ln["x"] for ln in lines] == [1, 2]
    for k in ("ts", "hwde_commit", "kicad", "harness", "model", "subject",
              "seed", "detail"):
        assert k in lines[0]


def test_precision_is_smoothed_by_sample_size(tmp_path):
    h = tmp_path / "h.jsonl"
    h.write_text(json.dumps({"checks": {"a": {"tp": 2, "fp": 0},
                                        "b": {"tp": 0, "fp": 0},
                                        "c": {"tp": 98, "fp": 0}}}) + "\n")
    p = evalcard.precisions(h)
    assert p == {"a": 0.75, "b": 0.5, "c": 0.99}
    assert evalcard.precisions(tmp_path / "absent") == {}


def test_failure_modes_map_both_entry_forms(tmp_path):
    assert evalcard.load_failure_modes(tmp_path / "absent.yaml") == {}
    p = tmp_path / "fm.yaml"
    p.write_text("modes:\n  - id: FM-1\n    checks: [{check: check_pdn, kind: k}]\n"
                 "  - id: FM-2\n    checks: [check_silk]\n  - nope\n")
    assert evalcard.load_failure_modes(p) == {("check_pdn", "k"): "FM-1",
                                              ("check_silk", None): "FM-2"}


# ------------------------------------------------------------ bench --scorecard

def test_scorecard_scores_a_workspace_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_RESULTS_ROOT", str(tmp_path / "results"))
    ws = _workspace(tmp_path, gates=("erc", "drc_routed"))   # no DFM gate
    out, _ = bench.run(["--scorecard", str(ws), "--record", "--top", "3"])
    card = out["boards"][0]
    assert out["status"] == "pass" and card["board"] == ws.name
    assert set(card["areas"]) == set(evalcard.AREAS)
    assert len(card["top_findings"]) <= 3 and "findings" not in card
    assert any(f["kind"] == "gate_missing" and f["area"] == "manufacturing"
               for f in card["top_findings"])
    assert card["areas"]["manufacturing"]["score"] < 1.0
    rows = [json.loads(ln) for ln in
            (tmp_path / "results" / "scorecard.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["board", "suite"]
    assert rows[0]["subject"] == ws.name and rows[0]["harness"] == "frozen"
    # a frozen board re-scores identically
    again, _ = bench.run(["--scorecard", str(ws), "--top", "3"])
    assert again["boards"] == out["boards"]


def test_scorecard_without_record_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_RESULTS_ROOT", str(tmp_path / "results"))
    bench.run(["--scorecard", str(_workspace(tmp_path))])
    assert not (tmp_path / "results").exists()


def test_scorecard_refuses_a_non_workspace_and_stray_flags(tmp_path):
    with pytest.raises(CheckError):
        bench.run(["--scorecard", str(tmp_path)])
    with pytest.raises(CheckError):
        bench.run(["--record", "--list"])


def test_report_rewrites_only_between_markers(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_RESULTS_ROOT", str(tmp_path / "results"))
    md = tmp_path / "doc.md"
    md.write_text(f"intro\n{bench.REPORT_BEGIN}\nold\n{bench.REPORT_END}\noutro\n")
    with pytest.raises(CheckError):          # empty store: nothing to report
        bench.run(["--scorecard-report", str(md)])
    ws = _workspace(tmp_path, gates=("erc", "drc_routed"))
    bench.run(["--scorecard", str(ws), "--record"])
    bench.run(["--scorecard-report", str(md)])
    text = md.read_text()
    assert text.startswith("intro\n") and text.endswith("outro\n")
    assert "old" not in text and f"| {ws.name} |" in text
    assert "gate_dfm/gate_missing" in text
    nomark = tmp_path / "plain.md"
    nomark.write_text("no markers\n")
    with pytest.raises(CheckError):
        bench.run(["--scorecard-report", str(nomark)])


# ------------------------------------------------------------ brief detail levels

E2E_DIR = REPO / "tests" / "fixtures" / "stages" / "e2e"
REQ_NUMBER = r"\d+(?:\.\d+)?\s*(?:V|A|mA|%)"


@pytest.mark.parametrize("variant", sorted(E2E_DIR.glob("*/brief.*.md")),
                         ids=lambda p: f"{p.parent.name}/{p.name}")
def test_brief_variant_states_the_requirements_and_no_hidden_bound(variant):
    """docs/design-evals.md section 2: every level states the same
    requirements; no level states a hidden bound."""
    import re
    import yaml
    typical = (variant.parent / "brief.md").read_text(encoding="utf-8")
    text = variant.read_text(encoding="utf-8")
    norm = lambda s: {re.sub(r"\s+", "", m) for m in re.findall(REQ_NUMBER, s)}  # noqa: E731
    assert norm(typical) <= norm(text), norm(typical) - norm(text)
    bounds = yaml.safe_load((variant.parent / "bounds.yaml").read_text(encoding="utf-8"))
    for key in ("target_usd", "area_mm2"):
        assert key not in text
    hidden = [bounds["layout"]["area_mm2_max"], bounds["cost"]["target_usd"]]
    for n in hidden:
        assert not re.search(rf"(?<![\d.]){re.escape(str(n))}(?![\d])", text), n


# ------------------------------------------------------------ failure-modes coverage form

def test_failure_modes_read_the_coverage_form(tmp_path):
    p = tmp_path / "fm.yaml"
    p.write_text(
        "modes:\n"
        "  - {id: A-1, coverage: {script: check_x, rule: 'k1,k2 (partial)'}}\n"
        "  - {id: A-2, coverage: {script: check_y (partial), rule: '-'}}\n"
        "  - {id: A-3, coverage: {script: check_x, rule: 'k3; dfm_check:dfm_k'}}\n"
        "  - {id: A-4, coverage: {script: sim_run bench, rule: k9}}\n"
        "  - {id: A-5, coverage: {script: '-', rule: '-'}}\n"
        "  - {id: A-6, coverage: {script: check_x, rule: k1}}\n")
    m = evalcard.load_failure_modes(p)
    assert m == {("check_x", "k1"): "A-1", ("check_x", "k2"): "A-1",
                 ("check_y", None): "A-2", ("check_x", "k3"): "A-3",
                 ("dfm_check", "dfm_k"): "A-3"}
    # a script name with a space and a '-' script map nothing; first claim wins
    assert "A-4" not in m.values() and "A-5" not in m.values() and "A-6" not in m.values()


def test_repo_failure_modes_map_the_corpus_kinds():
    m = evalcard.load_failure_modes(REPO / "docs" / "failure-modes.yaml")
    assert m[("dfm_check", "dfm_trace_width")].startswith("FAB-")
    assert m[("check_silk", "silk_over_pad")].startswith("FAB-")
    assert m[("check_mating", "mating_faces_inward")] == "MECH-01"


# ------------------------------------------------------------ unscored areas

def test_unscored_area_stays_out_of_composite_and_suite():
    f = evalcard.finding("check_silk", {"severity": "error", "kind": "s"}, "real", None)
    card = evalcard.board_card("b", [f], scored_areas={"manufacturing", "layout"})
    assert card["areas"]["power"]["score"] is None
    assert card["areas"]["manufacturing"]["score"] == 0.5
    assert card["composite"] == 75.0          # mean of layout 1.0 and mfg 0.5 only
    full = evalcard.board_card("c", [f])      # no scored_areas: every area counts
    assert full["areas"]["power"]["score"] == 1.0 and full["composite"] == 90.0
    suite = evalcard.suite_score([card, full])
    assert suite["areas"]["power"]["n"] == 1 and suite["areas"]["layout"]["n"] == 2


# ------------------------------------------------------------ bench --scorecard-corpus

def _corpus(tmp_path, monkeypatch):
    import human_corpus
    man = tmp_path / "corpus.yaml"
    man.write_text(
        "boards:\n"
        "  - {id: b1, url: 'https://github.com/o/r', domain: power, layers: 2,\n"
        "     outcome: {label: product}}\n"
        "  - {id: b2, url: 'https://github.com/o/r', domain: power, layers: 2}\n"
        "  - {id: b3, url: 'https://github.com/o/s', domain: rf, layers: 4}\n"
        "  - {id: gone, url: 'https://github.com/o/t', excluded: no licence}\n")
    monkeypatch.setattr(human_corpus, "MANIFEST", man)
    monkeypatch.setenv("HWDE_RESULTS_ROOT", str(tmp_path / "results"))
    cache = tmp_path / "cache"
    checks = {"check_silk": {"status": "violations"}, "check_mating": {"status": "pass"},
              "check_diffpair": {"status": "error"}, "check_pdn": {"status": "skipped"}}
    silk = {"check": "check_silk", "source": "check_silk", "severity": "error",
            "kind": "silk_over_pad", "msg": "silk on pad"}
    dfm = {"check": "dfm", "source": "check.dfm", "severity": "error",
           "kind": "dfm_trace_width", "msg": "trace too thin"}
    for bid, vs in (("b1", [silk]), ("b2", [])):
        (cache / "runs" / bid).mkdir(parents=True)
        (cache / "runs" / bid / "result.json").write_text(json.dumps({
            "verify_summary": {"checks": checks, "violations": vs},
            "dfm_report": {"status": "violations", "violations": [dfm]}}))
    return cache


def test_scorecard_corpus_scores_recorded_runs(tmp_path, monkeypatch):
    cache = _corpus(tmp_path, monkeypatch)
    out, _ = bench.run(["--scorecard-corpus", "--cache", str(cache), "--record"])
    cards = {c["board"]: c for c in out["boards"]}
    assert set(cards) == {"b1", "b2"}                  # excluded board never read
    assert out["unscored"] == [{"board": "b3",
                                "why": "no result.json; run human_corpus.py run"}]
    b1 = cards["b1"]
    assert b1["info"]["gates"].startswith("excluded")
    assert not any(f["check"].startswith("gate_") for f in b1["top_findings"])
    assert {f["check"] for f in b1["top_findings"]} == {"check_silk", "dfm_check"}
    assert all(f["area"] == "manufacturing" for f in b1["top_findings"])
    assert b1["by_failure_mode"] == {"FAB-01": 1, "FAB-09": 1}
    # skipped (pdn) and errored (diffpair) checks leave their area unscored
    assert b1["areas"]["power"]["score"] is None
    assert b1["areas"]["signal_integrity"]["score"] is None
    assert b1["areas"]["layout"]["score"] == 1.0
    assert b1["info"]["skipped_checks"] == ["check_pdn"]
    assert b1["info"]["check_errors"] == ["check_diffpair"]
    assert b1["outcome"] == "product" and cards["b2"]["outcome"] == ""
    assert cards["b2"]["composite"] > b1["composite"]
    assert out["suite"]["composite"]["clusters"] == 1   # b1, b2 share a repo
    rows = [json.loads(ln) for ln in
            (tmp_path / "results" / "corpus-scorecard.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["board", "board", "suite"]
    assert rows[-1]["unscored"][0]["board"] == "b3"
    assert not (tmp_path / "results" / "scorecard.jsonl").exists()


def test_scorecard_corpus_names_ids_and_refuses_strays(tmp_path, monkeypatch):
    cache = _corpus(tmp_path, monkeypatch)
    out, _ = bench.run(["--scorecard-corpus", "b2", "--cache", str(cache)])
    assert [c["board"] for c in out["boards"]] == ["b2"]
    assert not (tmp_path / "results").exists()          # no --record, no write
    with pytest.raises(CheckError):                     # nothing recorded at all
        bench.run(["--scorecard-corpus", "b3", "--cache", str(cache)])
    with pytest.raises(CheckError):
        bench.run(["--cache", str(cache), "--list"])
    with pytest.raises(CheckError, match="unknown board"):
        bench.run(["--scorecard-corpus", "nope", "--cache", str(cache)])
