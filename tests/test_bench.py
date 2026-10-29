"""T5 stage bench: manifest integrity, metric units, known-answer matching,
composite rules, and committed-baseline reproducibility.

Offline tests are unmarked (pure venv - P2/P6/P8/P9/P10 legs run without
kicad-cli).  Live-toolchain legs (P3 scratch DRC, P4 ERC/netlist, P5
board_init + rules_gen, P7 DRC) are @pytest.mark.smoke per repo convention.

Baseline rule (the declared noise): deterministic metrics have ZERO noise -
a bench re-run must reproduce every metrics/metrics_live/penalties value
exactly and land delta 0 against the committed baseline.  A legitimate
metric change therefore always requires re-recording the baseline in the
same commit (bench.py --stage .. --fixture .. --baseline).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bench  # noqa: E402
import benchlib  # noqa: E402

STAGES_DIR = REPO / "tests" / "fixtures" / "stages"
MANIFEST = yaml.safe_load((STAGES_DIR / "manifest.yaml").read_text(encoding="utf-8"))
FIXTURES = MANIFEST["fixtures"]
BASELINES = STAGES_DIR / "baselines"

OFFLINE_FIXTURES = [
    ("P2", "pd_trigger_arch"),
    ("P6", "golden_blinky2_place"), ("P6", "pd_trigger_place"),
    ("P8", "pd_trigger_verify"), ("P8", "carrier_verify"),
    ("P8", "mutant_planesplit_verify"), ("P8", "mutant_returnvia_verify"),
    ("P8", "mutant_gndchoke_verify"),
    ("P9", "pd_trigger_dfm"), ("P9", "mutant_cpl_dfm"),
    ("P10", "pd_trigger_order"),
]
SMOKE_FIXTURES = [
    ("P3", "pristine_lib"),
    ("P4", "blinky2_sch"), ("P4", "pd_trigger_sch"), ("P4", "usbbuck_sch"),
    ("P5", "pd_trigger_board_init"),
    ("P7", "golden_usbbuck4_route"), ("P7", "pd_trigger_route"),
]


def run_bench(stage, fixture, *extra):
    payload, _ = bench.run(["--stage", stage, "--fixture", fixture, *extra])
    return payload


# ------------------------------------------------------------- manifest

def test_manifest_covers_every_registered_stage():
    covered = {e["stage"] for e in FIXTURES.values()}
    assert covered == set(bench.STAGES), (
        f"stages without a fixture: {set(bench.STAGES) - covered}")


def test_manifest_rows_wellformed():
    for fid, e in FIXTURES.items():
        assert e["stage"] in bench.STAGES, fid
        for key in ("source", "method", "captured", "note"):
            assert e["provenance"].get(key), f"{fid}: provenance.{key}"
        assert e["provenance"]["method"] in ("copy", "reference"), fid
        assert e.get("files"), fid
        for name, rec in e["files"].items():
            assert set(rec) == {"path", "sha256"}, f"{fid}.{name}"
            assert len(rec["sha256"]) == 64, f"{fid}.{name}"
        for name, rec in (e.get("dirs") or {}).items():
            assert set(rec) == {"path", "sha256"}, f"{fid}.dirs.{name}"


def test_every_fixture_pin_matches_disk():
    """The freeze is real: every pinned file/dir hash matches the tree."""
    for fid, e in FIXTURES.items():
        assert benchlib.verify_fixture(e) == [], fid


def test_every_fixture_has_a_committed_baseline():
    missing = [fid for fid in FIXTURES
               if not (BASELINES / f"{fid}.score.json").is_file()]
    assert missing == []
    extra = [p.name for p in BASELINES.glob("*.score.json")
             if p.name[: -len(".score.json")] not in FIXTURES]
    assert extra == []


def test_drifted_fixture_is_refused(tmp_path):
    entry = {"files": {"x": {"path": "tests/test_bench.py",
                             "sha256": "0" * 64}}}
    bad = benchlib.verify_fixture(entry, root=REPO)
    assert bad and "drift" in bad[0]


# ------------------------------------------------------- benchlib units

_SCH_TEMPLATE = """(kicad_sch (version 20250114) (generator "test")
  (uuid "00000000-0000-0000-0000-000000000000")
  (lib_symbols)
  {body}
)
"""


def _sheet_file(tmp_path, body):
    p = tmp_path / "t.kicad_sch"
    p.write_text(_SCH_TEMPLATE.format(body=body), encoding="utf-8")
    return p


def test_wire_crossing_counted(tmp_path):
    body = """(wire (pts (xy 0 5) (xy 10 5)))
  (wire (pts (xy 5 0) (xy 5 10)))"""
    m = benchlib.sch_metrics([_sheet_file(tmp_path, body)])
    assert m["wire_crossings"] == 1
    assert m["wires"] == 2


def test_crossing_at_junction_not_counted(tmp_path):
    body = """(wire (pts (xy 0 5) (xy 10 5)))
  (wire (pts (xy 5 0) (xy 5 10)))
  (junction (at 5 5) (diameter 0) (color 0 0 0 0))"""
    m = benchlib.sch_metrics([_sheet_file(tmp_path, body)])
    assert m["wire_crossings"] == 0


def test_t_join_is_not_a_crossing(tmp_path):
    body = """(wire (pts (xy 0 5) (xy 10 5)))
  (wire (pts (xy 5 5) (xy 5 10)))"""
    m = benchlib.sch_metrics([_sheet_file(tmp_path, body)])
    assert m["wire_crossings"] == 0


def test_label_collision_counted(tmp_path):
    body = """(label "AAAA" (at 5 5 0) (effects (font (size 1.27 1.27))))
  (label "BBBB" (at 6 5 0) (effects (font (size 1.27 1.27))))
  (label "FAR" (at 80 80 0) (effects (font (size 1.27 1.27))))"""
    m = benchlib.sch_metrics([_sheet_file(tmp_path, body)])
    assert m["label_collisions"] == 1


def test_sheet_balance(tmp_path):
    m = benchlib.sch_metrics([_sheet_file(tmp_path, "")])
    assert m["sheet_balance"] == 1.0
    assert m["symbols"] == 0


def _p4_ctx(tmp_path, files, args=None):
    return {"entry": {}, "files": files, "args": args, "work": tmp_path,
            "cli": None, "render": False, "renders": [], "overridden": set()}


def test_score_p4_scores_root_plus_sch_children(tmp_path):
    """T6 P4-3: every pinned sheet enters sch_metrics - root 'sch' plus the
    sorted 'sch_*' children; non-sheet entries (pro, golden_net) do not."""
    root = _sheet_file(tmp_path, "")
    child = tmp_path / "c.kicad_sch"
    child.write_text(_SCH_TEMPLATE.format(
        body="""(wire (pts (xy 0 5) (xy 10 5)))
  (wire (pts (xy 5 0) (xy 5 10)))"""), encoding="utf-8")
    files = {"sch": root, "sch_child": child,
             "pro": tmp_path / "x.kicad_pro", "golden_net": tmp_path / "x.net"}
    metrics, live, penalties, _ = bench.score_p4(_p4_ctx(tmp_path, files))
    assert metrics["sheets"] == 2
    assert metrics["wire_crossings"] == 1     # the child's crossing counted
    assert live is None                       # offline ctx: no live leg

    # an explicit args.sheets order overrides the naming convention
    metrics, _, _, _ = bench.score_p4(
        _p4_ctx(tmp_path, files, args={"sheets": ["sch"]}))
    assert metrics["sheets"] == 1 and metrics["wire_crossings"] == 0


def test_match_known_answer_paths():
    vs = [{"check": "check_return_path", "kind": "corridor_void",
           "net": "/X", "severity": "error", "refs": []},
          {"check": "check_silk", "kind": "silk_over_pad",
           "severity": "error", "refs": ["D1"]}]
    hit = benchlib.match_known_answer(
        {"expected": [{"check": "check_return_path", "net": "/X"}]}, vs)
    assert hit["status"] == "ok" and hit["matched"] == 1

    miss = benchlib.match_known_answer(
        {"expected": [{"check": "check_return_path", "net": "/Y"}]}, vs)
    assert miss["status"] == "miss" and miss["missed"]

    ref = benchlib.match_known_answer(
        {"expected": [{"kind": "silk_over_pad", "ref": "D1"}]}, vs)
    assert ref["status"] == "ok"

    forbid = benchlib.match_known_answer({"forbid_errors": True}, vs)
    assert forbid["forbidden_errors"] == 2 and forbid["status"] == "miss"

    claimed = benchlib.match_known_answer(
        {"expected": [{"check": "check_silk"}], "forbid_errors": True}, vs)
    assert claimed["forbidden_errors"] == 1


def test_match_known_answer_severity_order_independent():
    """A severity ladder emitting warning+error for the same declared
    signature must not flip the verdict on emission order (review finding)."""
    warn = {"check": "check_return_path", "kind": "corridor_void",
            "net": "/X", "severity": "warning", "refs": []}
    err = dict(warn, severity="error")
    known = {"expected": [{"check": "check_return_path",
                           "kind": "corridor_void"}], "forbid_errors": True}
    for order in ([warn, err], [err, warn]):
        r = benchlib.match_known_answer(known, order)
        assert r["status"] == "ok" and r["forbidden_errors"] == 0, order


def test_composite_rules():
    assert benchlib.composite("P8", {"errors": 4, "warnings": 10}) == 97.0
    assert benchlib.composite("P8", {"errors": 1000}) == 0.0  # floor
    with pytest.raises(benchlib.BenchError):
        benchlib.composite("P8", {"made_up_penalty": 1})


def test_placement_refs_missing():
    cons = {"placement": {"edges": [{"ref": "J1"}],
                          "groups": [{"anchor": "U1", "members": ["C1", "C9"]}],
                          "separation": [{"a": ["Q1"], "b": ["U1"]}]}}
    comps = {"J1": {}, "U1": {}, "C1": {}}
    assert benchlib.placement_refs_missing(cons, comps) == ["C9", "Q1"]


# ------------------------------------------- offline fixtures vs baseline

@pytest.mark.parametrize("stage,fixture", OFFLINE_FIXTURES,
                         ids=[f for _, f in OFFLINE_FIXTURES])
def test_offline_fixture_matches_baseline(stage, fixture):
    payload = run_bench(stage, fixture, "--compare")
    b = payload["baseline"]
    assert b["metric_diffs"] == []
    assert b["delta"] == 0
    assert payload["status"] == "pass"


def test_known_answers_fire_where_expected():
    ka = run_bench("P8", "mutant_planesplit_verify")["known_answer"]
    assert ka["status"] == "ok" and ka["matched"] == 1
    ka = run_bench("P8", "mutant_returnvia_verify")["known_answer"]
    assert ka["status"] == "ok" and ka["matched"] == 1
    ka = run_bench("P9", "mutant_cpl_dfm")["known_answer"]
    assert ka["status"] == "ok" and ka["matched"] == 1
    ka = run_bench("P8", "pd_trigger_verify")["known_answer"]
    assert ka["status"] == "ok" and ka["forbidden_errors"] == 0
    # T6: the pre-fix GND-choke board fires the derived return-net coverage
    ka = run_bench("P8", "mutant_gndchoke_verify")["known_answer"]
    assert ka["status"] == "ok" and ka["matched"] == 3
    # T6: the carrier's 8 surviving true-defect clusters are pinned
    ka = run_bench("P8", "carrier_verify")["known_answer"]
    assert ka["status"] == "ok" and ka["matched"] == 8


def test_p10_hash_stable_and_ready():
    m = run_bench("P10", "pd_trigger_order")["metrics"]
    assert m["hash_stable"] is True
    assert m["submit_status"] == "ready_for_human"
    assert m["missing"] == 0


def test_list_covers_all_fixtures():
    payload, _ = bench.run(["--list"])
    listed = [f for s in payload["stages"].values() for f in s["fixtures"]]
    assert sorted(listed) == sorted(FIXTURES)
    # T6 P3-BENCH: the library-sanitise stage is registered and live-required
    # (scratch DRC is the only honest silk oracle)
    assert payload["stages"]["P3"]["live"] == "required"
    assert payload["stages"]["P3"]["fixtures"] == ["pristine_lib"]


def test_live_required_stage_refused_without_kicad(monkeypatch):
    """P3 has no offline leg: with no kicad-cli the run must refuse (exit 2
    at the CLI), never emit a partial score."""
    monkeypatch.setattr(bench, "_find_cli", lambda: None)
    with pytest.raises(Exception, match="needs kicad-cli"):
        bench.run(["--stage", "P3", "--fixture", "pristine_lib"])


def test_wrong_stage_for_fixture_refused():
    with pytest.raises(Exception, match="P6 fixture"):
        bench.run(["--stage", "P8", "--fixture", "pd_trigger_place"])


def test_unknown_file_override_refused():
    with pytest.raises(Exception, match="not in this fixture"):
        bench.run(["--stage", "P6", "--fixture", "pd_trigger_place",
                   "--file", "bogus=tests/test_bench.py"])


def test_judge_side_override_plus_compare_refused():
    with pytest.raises(Exception, match="judge-side"):
        bench.run(["--stage", "P6", "--fixture", "pd_trigger_place",
                   "--file", "constraints=tests/golden/blinky2/constraints.json",
                   "--compare"])


def test_render_without_workdir_refused():
    with pytest.raises(Exception, match="work-dir"):
        bench.run(["--stage", "P6", "--fixture", "pd_trigger_place",
                   "--render"])


def test_known_answer_on_unsupported_stage_refused(tmp_path):
    man = yaml.safe_load((STAGES_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    entry = man["fixtures"]["pd_trigger_place"]
    entry["known_answer"] = {"forbid_errors": True}
    tmp_man = tmp_path / "manifest.yaml"
    tmp_man.write_text(yaml.safe_dump({"version": 1,
                                       "fixtures": {"pd_trigger_place": entry}}),
                       encoding="utf-8")
    with pytest.raises(Exception, match="P8/P9"):
        bench.run(["--stage", "P6", "--fixture", "pd_trigger_place",
                   "--manifest", str(tmp_man)])


# --------------------------------------------------- live-toolchain legs

@pytest.mark.smoke
@pytest.mark.parametrize("stage,fixture", SMOKE_FIXTURES,
                         ids=[f for _, f in SMOKE_FIXTURES])
@pytest.mark.kicad_recorded
def test_live_fixture_matches_baseline(stage, fixture):
    payload = run_bench(stage, fixture, "--compare")
    b = payload["baseline"]
    assert b["metric_diffs"] == []
    assert b["delta"] == 0
    assert payload["composite_inputs"] == "full"


@pytest.mark.kicad_recorded
@pytest.mark.smoke
def test_artifact_override_detects_regression():
    """The tuning loop's failure direction: a WORSE candidate artifact must
    come back regressed=true (exit 1 at the CLI)."""
    other = REPO / "tests" / "s7_regen" / "blinky2" / "kicad" / "blinky2.kicad_sch"
    payload, _ = bench.run(["--stage", "P4", "--fixture", "pd_trigger_sch",
                            "--artifact", str(other), "--compare"])
    assert payload["status"] == "violations"
    assert payload["baseline"]["regressed"] is True
    assert payload["metrics_live"]["netlist_identical"] is False


@pytest.mark.smoke
def test_baseline_refused_with_override(tmp_path):
    other = REPO / "tests" / "s7_regen" / "blinky2" / "kicad" / "blinky2.kicad_sch"
    with pytest.raises(Exception, match="FROZEN"):
        bench.run(["--stage", "P4", "--fixture", "pd_trigger_sch",
                   "--artifact", str(other), "--baseline"])


@pytest.mark.smoke
def test_render_leg(tmp_path):
    payload = run_bench("P6", "pd_trigger_place", "--render",
                        "--work-dir", str(tmp_path))
    renders = payload["informational"]["renders"]
    assert renders and Path(renders[0]).is_file()


@pytest.mark.smoke
def test_unpinned_sibling_pro_refused(tmp_path):
    """A live leg must refuse an artifact whose sibling .kicad_pro is not
    sha-pinned (LEARNINGS 2026-08-06 [bench][kicad-cli])."""
    import shutil
    src = REPO / "tests" / "s7_regen" / "blinky2" / "kicad"
    shutil.copy(src / "blinky2.kicad_sch", tmp_path / "blinky2.kicad_sch")
    shutil.copy(src / "blinky2.kicad_pro", tmp_path / "blinky2.kicad_pro")
    entry = {"stage": "P4", "board": "blinky2",
             "provenance": {"source": "test", "method": "copy",
                            "captured": "2026-08-06", "note": "guard test"},
             "files": {"sch": {"path": "blinky2.kicad_sch",
                               "sha256": benchlib.file_sha256(
                                   tmp_path / "blinky2.kicad_sch")}}}
    tmp_man = tmp_path / "manifest.yaml"
    tmp_man.write_text(yaml.safe_dump({"version": 1,
                                       "fixtures": {"guard": entry}}),
                       encoding="utf-8")
    import benchlib as bl
    orig = bl.repo_root
    bl.repo_root = lambda: tmp_path
    try:
        with pytest.raises(Exception, match="unpinned sibling"):
            bench.run(["--stage", "P4", "--fixture", "guard",
                       "--manifest", str(tmp_man)])
    finally:
        bl.repo_root = orig
