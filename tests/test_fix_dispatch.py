"""T6 - fix_dispatch routing + batching (P8B-1, XC-2).

Locks the two fix-loop plumbing fixes:
  1. Reviewer findings route to REAL fixer domains: a violation carrying an
     explicit "domain" (verify-reviewer.md transcribes one per finding) wins
     over the kind-keyed FIXER_HINTS fallback. All 19 production review
     dispatches routed to the render-only 'review' dead end because reviewer
     kinds are free slugs FIXER_HINTS cannot enumerate.
  2. Same-domain single/small clusters batch into one work order (pd-trigger
     P8: 10 one-violation orders = 10 fixer spawns for one agent's work).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import cluster_violations  # noqa: E402
import fix_dispatch  # noqa: E402


def _review(kind, domain=None, pos=(10.0, 10.0), net=None, sev="error"):
    v = {"check": "board-review", "severity": sev,
         "pos": list(pos) if pos else None, "layer": None, "net": net,
         "refs": [], "msg": f"review finding {kind}", "source": "review.board",
         "kind": kind}
    if domain:
        v["domain"] = domain
    return v


# ------------------------------------------------------------ domain routing

def test_fixer_domains_in_sync_with_dispatch():
    """cluster_violations cannot import fix_dispatch (dispatch imports it);
    the hardcoded valid-domain set must track the DOMAINS table."""
    assert cluster_violations.FIXER_DOMAINS == frozenset(fix_dispatch.DOMAINS)


def test_explicit_domain_routes_review_finding():
    clusters = cluster_violations.cluster(
        [_review("silk-polarity-missing", domain="silk")], 5.0)
    assert len(clusters) == 1
    assert clusters[0]["fixer"] == "silk"


def test_missing_domain_keeps_fallback():
    clusters = cluster_violations.cluster(
        [_review("promised-artifact")], 5.0)
    assert clusters[0]["fixer"] == "review"


def test_invalid_domain_ignored():
    clusters = cluster_violations.cluster(
        [_review("current-return", domain="wizardry")], 5.0)
    assert clusters[0]["fixer"] == "review"


def test_known_kind_still_routes_by_hint_without_domain():
    v = {"check": "check_current", "severity": "error", "pos": [1.0, 1.0],
         "layer": "F.Cu", "net": "+5V", "refs": [], "msg": "x",
         "source": "check_current", "kind": "undersized_track"}
    clusters = cluster_violations.cluster([v], 5.0)
    assert clusters[0]["fixer"] == "router"


def test_sim_kinds_have_fixer_hints():
    hints = cluster_violations.FIXER_HINTS
    assert hints["sim_bound_fail"] == "schematic"
    assert hints["sim_measure_missing"] == "review"
    assert hints["sim_engine_error"] == "review"


def test_domain_routed_order_gets_domain_scripts(tmp_path):
    """End to end: a review finding with domain=silk produces an order whose
    whitelist carries place_edit.py (the actual fix tool), not render-only."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    findings = {"gate": "verify", "phase": "P8", "violations": [
        _review("silk-polarity-missing", domain="silk", pos=(50.0, 50.0)),
        _review("current-return", domain="router", pos=(90.0, 20.0)),
        _review("promised-artifact", domain=None, pos=None),
    ]}
    inp = tmp_path / "r.json"
    inp.write_text(json.dumps(findings), encoding="utf-8")
    payload, _ = fix_dispatch.run(["--input", str(inp), "--board", str(board),
                                   "--out-dir", str(tmp_path / "wo")])
    by_fixer = {o["fixer"]: o for o in payload["orders"]}
    assert set(by_fixer) == {"silk", "router", "review"}
    silk_wo = json.loads(
        Path(by_fixer["silk"]["work_order"]).read_text(encoding="utf-8"))
    assert "place_edit.py" in silk_wo["allowed_scripts"]
    router_wo = json.loads(
        Path(by_fixer["router"]["work_order"]).read_text(encoding="utf-8"))
    assert "route_edit.py" in router_wo["allowed_scripts"]
    review_wo = json.loads(
        Path(by_fixer["review"]["work_order"]).read_text(encoding="utf-8"))
    assert review_wo["allowed_scripts"] == ["render.py"]


# ------------------------------------------------------------ small-cluster merge

def test_ten_singles_two_domains_merge_to_two_orders(tmp_path):
    """The pd-trigger shape: 10 single-violation findings across 2 domains
    -> at most 3 orders (here exactly 2: 6 silk + 4 router)."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    vs = [_review(f"silk-{i}", domain="silk", pos=(10.0 * i + 5, 5.0))
          for i in range(6)]
    vs += [_review(f"copper-{i}", domain="router", pos=(10.0 * i + 5, 60.0))
           for i in range(4)]
    inp = tmp_path / "r.json"
    inp.write_text(json.dumps({"gate": "verify", "violations": vs}),
                   encoding="utf-8")
    payload, _ = fix_dispatch.run(["--input", str(inp), "--board", str(board),
                                   "--out-dir", str(tmp_path / "wo")])
    assert payload["counts"]["orders"] <= 3
    by_fixer = {o["fixer"]: o for o in payload["orders"]}
    assert by_fixer["silk"]["count"] == 6
    assert by_fixer["router"]["count"] == 4
    # per-violation coordinates survive the merge; region is the union bbox
    wo = json.loads(
        Path(by_fixer["silk"]["work_order"]).read_text(encoding="utf-8"))
    assert len(wo["cluster"]["violations"]) == 6
    assert wo["cluster"]["region"]["bbox"] is not None
    kinds = wo["cluster"]["kinds"]
    assert kinds == sorted(kinds) and len(kinds) == 6


def test_merge_respects_cap():
    singles = [
        {"net": None, "kinds": [f"k{i}"], "checks": ["c"], "severity": "error",
         "count": 1, "region": {"bbox": [i, 0, i + 1, 1], "center": [i, 0.5]},
         "fixer": "silk",
         "violations": [{"severity": "error", "pos": [float(i), 0.5],
                         "kind": f"k{i}"}]}
        for i in range(12)
    ]
    merged = fix_dispatch.merge_small_clusters(singles)
    assert sum(c["count"] for c in merged) == 12
    assert all(c["count"] <= fix_dispatch.MERGE_CAP for c in merged)
    assert len(merged) == 2                      # 8 + 4


def test_large_clusters_pass_through_untouched():
    big = {"net": "+5V", "kinds": ["undersized_track"], "checks": ["c"],
           "severity": "error", "count": 5,
           "region": {"bbox": [0, 0, 9, 9], "center": [4, 4]},
           "fixer": "router",
           "violations": [{"severity": "error", "pos": [float(i), 1.0],
                           "kind": "undersized_track"} for i in range(5)]}
    lone = {"net": None, "kinds": ["x"], "checks": ["c"], "severity": "warning",
            "count": 1, "region": {"bbox": None, "center": None},
            "fixer": "plane", "violations": [{"severity": "warning",
                                              "pos": None, "kind": "x"}]}
    merged = fix_dispatch.merge_small_clusters([big, lone])
    assert big in merged and lone in merged and len(merged) == 2


def test_merged_order_severity_and_net():
    a = {"net": "+5V", "kinds": ["a"], "checks": ["c"], "severity": "warning",
         "count": 1, "region": {"bbox": [0, 0, 1, 1], "center": [0.5, 0.5]},
         "fixer": "silk", "violations": [{"severity": "warning",
                                          "pos": [0.5, 0.5], "kind": "a"}]}
    b = {"net": "+3V3", "kinds": ["b"], "checks": ["c"], "severity": "error",
         "count": 1, "region": {"bbox": [5, 5, 6, 6], "center": [5.5, 5.5]},
         "fixer": "silk", "violations": [{"severity": "error",
                                          "pos": [5.5, 5.5], "kind": "b"}]}
    merged = fix_dispatch.merge_small_clusters([a, b])
    assert len(merged) == 1
    m = merged[0]
    assert m["severity"] == "error"              # max of the mergees
    assert m["net"] is None                      # nets differ -> no single net
    assert m["merged_from"] == 2
    assert m["region"]["bbox"] == [0.5, 0.5, 5.5, 5.5]
