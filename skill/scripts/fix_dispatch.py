"""fix_dispatch.py - turn a failed gate into fixer work orders (SPEC 4, S13).

The uniform fix-loop protocol: a gate fails -> the orchestrator clusters the
failing violations (cluster_violations.py, S5) -> ONE fixer agent per cluster,
in parallel where regions don't overlap -> fixers edit via scripts only ->
re-run the gate. This script does the deterministic middle: it reads a gate
result (gate.py output, `failing` list) or any report carrying `violations`,
clusters them, and writes one work-order JSON per cluster - the complete brief
a fixer agent needs (domain, allowed scripts, guidance, violations with
coordinates, sidecar artifact paths). With --state, each order is also
registered as an open issue in state.json (ids allocated there).

CLI: fix_dispatch.py --input gate_result.json --board B.kicad_pcb
       [--radius 5] [--out-dir DIR] [--state state.json] [--out summary.json]
Exit: 0 nothing to dispatch, 1 orders written (work to do), 2 error.

Work order shape (log/workorders/wo-<id>.json):
    {"id", "gate", "phase", "board", "fixer", "role_prompt",
     "allowed_scripts": [...], "guidance": [...], "remediations": [...],
     "cluster": {net, kinds, checks, severity, count, region, violations[]},
     "artifacts": {name: path...}, "scope":
     "fix ONLY these violations; do not touch unrelated nets/regions"}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import checklib  # noqa: E402
import cluster_violations  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "fix_dispatch"
SKILL = SCRIPTS.parent  # .claude/skills/hwde

# Per-domain script whitelists (SPEC 5 rule 2: the prompt lists the scripts the
# agent may use, in order of preference) and the load-bearing guidance lines.
DOMAINS: dict[str, dict] = {
    "router": {
        "scripts": ["route_edit.py", "kc.py", "render.py", "stitch_vias.py",
                    "route_cleanup.py"],
        "guidance": [
            "Edit copper ONLY via route_edit.py ops "
            "(add_track/add_via/remove-by-uuid); never raw file edits.",
            "DRC violations carry the item uuid (items[].uuid) - remove by "
            "uuid, then re-add corrected geometry. Check violations carry "
            "coordinates; locate the uuid by matching start/end in the board "
            "file text (read-only grounding).",
            "Repair width rule: match the same-net copper abutting the "
            "segment's endpoints, never below the check's required minimum.",
            "If an edit crosses a zone fill, refill before re-gating: "
            "kicad-cli pcb drc --refill-zones --save-board (kc.run_drc "
            "refill=True).",
        ],
    },
    "placement": {
        "scripts": ["place_edit.py", "place_metrics.py", "render.py"],
        "guidance": [
            "Move/rotate/flip parts ONLY via place_edit.py --ops (absolute "
            "ops, atomic, verified).",
            "Moving a placed-and-routed part strands its copper: after any "
            "move on a routed board, the affected nets must be re-routed "
            "(route_edit removals + route_auto/route_critical) before the "
            "routed gates re-pass. Flag this in your summary.",
            "Re-check with place_metrics.py (or gate.py --gate place).",
        ],
    },
    "plane": {
        "scripts": ["planes_gen.py", "plane_repair.py", "stitch_vias.py",
                    "route_edit.py", "kc.py"],
        "guidance": [
            "plane_repair.py detects and repairs split planes itself; run it "
            "before hand-crafting jumpers.",
            "Always refill zones after copper edits on plane layers "
            "(kicad-cli pcb drc --refill-zones --save-board).",
            "plane_repair/route_cleanup mutate the board in place and "
            "self-detect regressions (exit 1): on exit 1 restore the "
            "pre-fix snapshot (state.py restore) and report.",
        ],
    },
    "silk": {
        "scripts": ["place_edit.py", "render.py"],
        "guidance": [
            "place_edit.py carries add_text (board-frame silk text, "
            "idempotent), move_text (refdes/value field repositioning) "
            "and set_text (hide / shrink / re-layer a refdes that has no "
            "legal spot - silk_place residual fix_ops carry them) ops. Labels must be PIN-LOCKED (a "
            "label readable against the wrong pin is worse than none); "
            "footprint-INTERNAL silk defects are librarian edits "
            "(approval + lib/EDITS.md), not board text.",
            "A silk collision may also be fixed by nudging the FOOTPRINT "
            "via place_edit within placement legality.",
            "Verify with check_silk.py, the drc_routed gate (err+warn "
            "includes silk), and a render of the region READ back.",
        ],
    },
    "schematic": {
        "scripts": ["schlib.py", "netlist_audit.py", "kc.py"],
        "guidance": [
            "The schematic SOURCE is kicad/gen/<sheet>.py (schlib generator "
            "pattern); edit the generator, rebuild, never hand-edit the "
            ".kicad_sch.",
            "A schematic change after P5 invalidates the board (netlist "
            "drift): report it as `requires_pipeline_rewind` in your summary "
            "instead of silently continuing - the orchestrator decides.",
            "Re-gate with kc.py erc and netlist_audit.py.",
        ],
    },
    "library": {
        "scripts": ["lib_pull.py", "fp_verify.py", "datasheet_extract.py"],
        "guidance": [
            "Re-pull the part with lib_pull.py, verify with fp_verify.py "
            "against the datasheet land-pattern JSON.",
            "A footprint change under placed parts requires re-import/"
            "re-placement of those refs - flag `requires_pipeline_rewind`.",
        ],
    },
    "fab": {
        "scripts": ["fab_export.py", "bom_cpl.py", "dfm_check.py"],
        "guidance": [
            "Export-stage defects: re-run fab_export.py (fresh out dir) and "
            "re-check with dfm_check.py before re-gating.",
        ],
    },
    "parts": {
        "scripts": ["parts_search.py", "bom_cpl.py"],
        "guidance": [
            "Fill missing LCSC assignments in parts/parts.json via "
            "parts_search.py (prefer Basic, in stock), then rebuild BOM/CPL "
            "with bom_cpl.py.",
        ],
    },
    "review": {
        "scripts": ["render.py"],
        "guidance": [
            "No script owns this violation kind - triage: either identify "
            "the right domain and say so, or escalate to human with a "
            "render and a one-paragraph explanation.",
        ],
    },
}

SIDECARS = ["constraints.json", "decoupling.json", "parts.json"]

# Trigger-indexed knowledge (T4): reference/remediations/<check_id>.md, keyed
# by the FINDING type, never by topic - the fixer never knows it "needs EMI
# knowledge", it knows which check fired. Lookup is file existence: drop a new
# <kind>.md in that dir and every work order carrying that kind picks it up.
REMEDIATION_DIR = SKILL / "reference" / "remediations"
REMEDIATION_GUIDANCE = (
    "Read the remediation reference(s) listed in `remediations` FIRST: what "
    "the measurement means, the known false-positive classes for this finding "
    "type, the cheapest-first fix ladder, and the traps this project already "
    "hit. They are keyed to your cluster's kinds."
)


def remediation_paths(kinds) -> list[str]:
    """The remediation refs that exist for a cluster's kinds (sorted, unique)."""
    out = []
    for kind in sorted({k for k in (kinds or []) if k}):
        ref = REMEDIATION_DIR / f"{kind}.md"
        if ref.is_file():
            out.append(str(ref).replace("\\", "/"))
    return out


def load_input(path: Path) -> tuple[list[dict], dict]:
    """Accept a gate.py result (failing[]), a checklib/kc report
    (violations[]), or a cluster_violations payload (clusters[] - reclustered
    from their violations)."""
    data = checklib.load_json(path, "input report")
    meta = {"gate": data.get("gate"), "phase": data.get("phase"),
            "input": data.get("input") or data.get("board")}
    if "failing" in data:
        return data["failing"], meta
    if "violations" in data and isinstance(data["violations"], list):
        return data["violations"], meta
    if "clusters" in data:
        vs = [v for c in data["clusters"] for v in c.get("violations", [])]
        return vs, meta
    raise CheckError(f"{path}: no failing/violations/clusters list found")


def erc_fallback(clusters: list[dict]) -> None:
    """ERC violations have no kind and mostly unknown-to-FIXER_HINTS types;
    the schematic domain owns them all."""
    for c in clusters:
        if c["fixer"] == "review" and c["violations"] and all(
                v.get("source") == "erc" for v in c["violations"]):
            c["fixer"] = "schematic"


# XC-2 (T6): production dispatches were dominated by one-violation clusters -
# pd-trigger P8 alone produced 10 single-violation orders, and SKILL.md says
# "spawn one fixer per order" (= 10 fixer sessions for work one agent provably
# did in one pass). Small same-domain clusters therefore batch into one order.
MERGE_MAX_SRC = 2   # clusters at/below this size are merge candidates
MERGE_CAP = 8       # max violations in one merged order


def merge_small_clusters(clusters: list[dict]) -> list[dict]:
    """Batch same-fixer clusters of <= MERGE_MAX_SRC violations into one
    cluster (capped at MERGE_CAP violations); larger clusters and lone
    candidates pass through untouched. Region becomes the union bbox; the
    per-violation coordinates are all preserved in `violations`."""
    small: dict[str, list[dict]] = {}
    out: list[dict] = []
    for c in clusters:
        if c["count"] <= MERGE_MAX_SRC:
            small.setdefault(c["fixer"], []).append(c)
        else:
            out.append(c)
    for fixer, cands in small.items():
        if len(cands) == 1:
            out.append(cands[0])
            continue
        batch: list[dict] = []
        for c in cands + [None]:                    # None flushes the tail
            if c is not None and (not batch or
                    sum(b["count"] for b in batch) + c["count"] <= MERGE_CAP):
                batch.append(c)
                continue
            if len(batch) == 1:
                out.append(batch[0])
            elif batch:
                vs = [v for b in batch for v in b["violations"]]
                nets = {b.get("net") for b in batch}
                sev = max((b["severity"] for b in batch),
                          key=lambda s: cluster_violations.SEV_RANK.get(s, 0))
                out.append({
                    "net": nets.pop() if len(nets) == 1 else None,
                    "kinds": sorted({k for b in batch for k in b["kinds"]}),
                    "checks": sorted({k for b in batch for k in b["checks"]}),
                    "severity": sev, "count": len(vs),
                    "region": cluster_violations.region_of(vs),
                    "fixer": fixer, "violations": vs,
                    "merged_from": len(batch),
                })
            batch = [c] if c is not None else []
    out.sort(key=lambda c: (-cluster_violations.SEV_RANK.get(c["severity"], 0),
                            -c["count"]))
    return out


def parallel_groups(orders: list[dict]) -> list[list[int]]:
    """Group order ids whose regions don't overlap (bbox test, 1 mm margin):
    orders inside one group are safe to run in parallel; groups run in
    sequence. Region-less orders are serialized (own group each)."""
    margin = 1.0

    def bbox(o):
        return (o["cluster"]["region"] or {}).get("bbox")

    def overlaps(a, b):
        return not (a[2] + margin < b[0] or b[2] + margin < a[0]
                    or a[3] + margin < b[1] or b[3] + margin < a[1])

    groups: list[dict] = []  # {"ids": [...], "boxes": [...]}
    for o in orders:
        bb = bbox(o)
        if bb is None:
            groups.append({"ids": [o["id"]], "boxes": [None]})
            continue
        placed = False
        for g in groups:
            if all(b is not None and not overlaps(bb, b) for b in g["boxes"]):
                g["ids"].append(o["id"])
                g["boxes"].append(bb)
                placed = True
                break
        if not placed:
            groups.append({"ids": [o["id"]], "boxes": [bb]})
    return [g["ids"] for g in groups]


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", required=True,
                    help="gate result / check report / cluster payload JSON")
    ap.add_argument("--board", required=True, help="the .kicad_pcb (or "
                    ".kicad_sch for ERC clusters) the fixers operate on")
    ap.add_argument("--radius", type=float,
                    default=cluster_violations.DEFAULT_RADIUS_MM)
    ap.add_argument("--out-dir",
                    help="work-order dir (default <workspace>/log/workorders "
                         "with --state, else <board dir>/workorders)")
    ap.add_argument("--state", help="state.json - register orders as open "
                                    "issues (ids allocated there)")
    ap.add_argument("--out", help="write summary JSON here instead of stdout")
    args = ap.parse_args(argv)

    board = Path(args.board)
    if not board.exists():
        raise CheckError(f"board not found: {board}")

    violations, meta = load_input(Path(args.input))
    clusters = cluster_violations.cluster(violations, args.radius)
    erc_fallback(clusters)
    clusters = merge_small_clusters(clusters)

    st = None
    if args.state:
        import state as state_mod
        st = state_mod.State.load(Path(args.state))

    if args.out_dir:
        out_dir = Path(args.out_dir)
    elif st is not None:
        out_dir = Path(st.data["workspace"]) / "log" / "workorders"
    else:
        out_dir = board.parent / "workorders"

    artifacts = {"board": str(board).replace("\\", "/")}
    sch = board.with_suffix(".kicad_sch")
    if sch.exists():
        artifacts["schematic"] = str(sch).replace("\\", "/")
    for name in SIDECARS:
        f = board.parent / name
        if f.exists():
            artifacts[name.split(".")[0]] = str(f).replace("\\", "/")

    orders: list[dict] = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    for c in clusters:
        domain = DOMAINS.get(c["fixer"], DOMAINS["review"])
        if st is not None:
            rec = st.open_issue({
                "gate": meta.get("gate"), "phase": meta.get("phase"),
                "fixer": c["fixer"], "net": c.get("net"),
                "kinds": c.get("kinds"), "severity": c.get("severity"),
                "count": c.get("count"), "region": c.get("region"),
                "work_order": None,
            })
            oid = rec["id"]
        else:
            oid = len(orders) + 1
        remediations = remediation_paths(c.get("kinds"))
        guidance = list(domain["guidance"])
        if remediations:
            guidance.insert(0, REMEDIATION_GUIDANCE)
        order = {
            "id": oid, "created": ts,
            "gate": meta.get("gate"), "phase": meta.get("phase"),
            "board": artifacts["board"], "fixer": c["fixer"],
            "role_prompt": str(SKILL / "agents" / "fixer.md").replace("\\", "/"),
            "allowed_scripts": domain["scripts"],
            "guidance": guidance,
            "remediations": remediations,
            "cluster": {k: c[k] for k in ("net", "kinds", "checks", "severity",
                                          "count", "region", "violations")},
            "artifacts": artifacts,
            "scope": "fix ONLY these violations; do not touch unrelated "
                     "nets/regions; re-run the failed gate when done",
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        wo_path = out_dir / f"wo-{oid}.json"
        wo_path.write_text(json.dumps(order, indent=1), encoding="utf-8")
        if st is not None:
            for rec in st.data["open_issues"]:
                if rec["id"] == oid:
                    rec["work_order"] = str(wo_path).replace("\\", "/")
        orders.append(order)

    if st is not None:
        st.save()

    by_domain: dict[str, int] = {}
    for o in orders:
        by_domain[o["fixer"]] = by_domain.get(o["fixer"], 0) + 1
    payload = {
        "script": SCRIPT,
        "status": "violations" if orders else "pass",
        "board": board.name, "gate": meta.get("gate"),
        "counts": {"orders": len(orders), "violations": len(violations),
                   "by_domain": by_domain,
                   "with_remediation": sum(1 for o in orders
                                           if o["remediations"])},
        "orders": [{"id": o["id"], "fixer": o["fixer"],
                    "severity": o["cluster"]["severity"],
                    "net": o["cluster"]["net"], "kinds": o["cluster"]["kinds"],
                    "count": o["cluster"]["count"],
                    "remediations": o["remediations"],
                    "work_order": str(out_dir / f"wo-{o['id']}.json")
                    .replace("\\", "/")}
                   for o in orders],
        "parallel_groups": parallel_groups(orders),
        "out_dir": str(out_dir).replace("\\", "/"),
    }
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
