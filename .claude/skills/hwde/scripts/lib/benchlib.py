"""benchlib - pure helpers for the per-stage benchmark harness (bench.py).

Everything here is offline-deterministic: no kicad-cli, no network, no RNG,
no wall-clock in any returned value.  bench.py owns the CLI, the stage
registry and the live (kicad-cli) legs; tests import these functions
directly.

Surfaces:
  load_manifest / fixture_paths / verify_fixture   - frozen-fixture manifest
  sch_metrics                                      - P4 schematic metrics v0
  placement_refs_missing                           - P2 constraints-vs-netlist
  match_known_answer                               - P8/P9 findings scoring
  composite / WEIGHTS                              - per-stage composite score

Composite scores are comparable ONLY within one (stage, fixture) pair: the
weights are documented constants, but the raw metrics they scale are not
normalised across boards.  The tuning loop compares before/after on the SAME
fixture (v2 plan appendix); cross-fixture deltas are meaningless.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

_LIB = Path(__file__).resolve().parent
if str(_LIB.parent) not in sys.path:  # scripts/ (for schem_refdes)
    sys.path.insert(0, str(_LIB.parent))


class BenchError(Exception):
    """Fixture/manifest problem - bench.py maps this to exit 2."""


# ---------------------------------------------------------------- manifest

def repo_root() -> Path:
    return _LIB.parents[4]


def default_manifest() -> Path:
    return repo_root() / "tests" / "fixtures" / "stages" / "manifest.yaml"


def load_manifest(path: Path | None = None) -> dict:
    p = Path(path) if path else default_manifest()
    if not p.is_file():
        raise BenchError(f"stage-fixture manifest not found: {p} (the frozen "
                         "fixtures live in the hwde checkout's tests/, which a "
                         "vendored copy of the skill does not carry)")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "fixtures" not in data:
        raise BenchError(f"manifest has no 'fixtures' key: {p}")
    return data


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_sha256(path: Path) -> str:
    """One digest for a directory: sha256 over sorted (relpath, file-sha) pairs."""
    h = hashlib.sha256()
    files = sorted(p for p in Path(path).rglob("*") if p.is_file())
    for p in files:
        h.update(p.relative_to(path).as_posix().encode())
        h.update(b"\0")
        h.update(file_sha256(p).encode())
        h.update(b"\0")
    return h.hexdigest()


def fixture_entry(manifest: dict, fixture_id: str) -> dict:
    try:
        return manifest["fixtures"][fixture_id]
    except KeyError:
        known = ", ".join(sorted(manifest["fixtures"]))
        raise BenchError(f"unknown fixture '{fixture_id}' (known: {known})") from None


def fixture_paths(entry: dict, root: Path | None = None) -> dict[str, Path]:
    """files: {name: {path, sha256}} -> {name: absolute Path} (repo-relative)."""
    root = root or repo_root()
    out = {}
    for name, rec in (entry.get("files") or {}).items():
        out[name] = root / rec["path"]
    for name, rec in (entry.get("dirs") or {}).items():
        out[name] = root / rec["path"]
    return out


def verify_fixture(entry: dict, root: Path | None = None) -> list[str]:
    """Return a list of drift complaints (empty = frozen fixture intact).

    Every manifest file entry is sha256-pinned; a mismatch means the fixture
    drifted (a workspace mutated a referenced file, or a copy was edited) and
    every score computed from it would be about a DIFFERENT design.
    """
    root = root or repo_root()
    bad = []
    for name, rec in (entry.get("files") or {}).items():
        p = root / rec["path"]
        if not p.is_file():
            bad.append(f"{name}: missing file {rec['path']}")
            continue
        got = file_sha256(p)
        if got != rec["sha256"]:
            bad.append(f"{name}: sha256 drift on {rec['path']} "
                       f"(pinned {rec['sha256'][:12]}.., found {got[:12]}..)")
    for name, rec in (entry.get("dirs") or {}).items():
        p = root / rec["path"]
        if not p.is_dir():
            bad.append(f"{name}: missing dir {rec['path']}")
            continue
        got = dir_sha256(p)
        if got != rec["sha256"]:
            bad.append(f"{name}: sha256 drift on dir {rec['path']} "
                       f"(pinned {rec['sha256'][:12]}.., found {got[:12]}..)")
    return bad


# ---------------------------------------------------------- P4 sch metrics

def sch_metrics(sch_paths: list[Path], clearance: float = 0.254) -> dict:
    """Offline schematic metrics v0 (plan T5): wire crossings, label
    collisions, refdes-field overlaps, sheet balance.

    - wire_crossings: unordered wire-segment pairs whose INTERIORS cross
      (shapely .crosses(); endpoint-to-endpoint and T-joins are not
      crossings) at a point NOT on a junction dot (a 4-way junction is an
      electrical connection, not a drawing defect).  Buses excluded.
    - label_collisions: unordered pairs of visible text boxes (label /
      global_label / hierarchical_label / text) that intersect - unreadable
      overlapping text.  Label-vs-symbol-BODY is deliberately NOT counted:
      measured on the shipped pd-trigger sheet it is 50/50 the
      label-grazes-the-pin-line-it-names artifact (a local label at a pin
      stub overlaps that pin's line, which body() includes), pure noise;
      field-vs-body coverage already exists in the refdes_overlaps audit.
    - refdes_overlaps: schem_refdes.audit_sheet residue rows (Reference and
      Value fields vs everything), the T3 metric reused as-is.
    - sheet_balance: symbols per sheet; max_over_mean == 1.0 when perfectly
      balanced (or a single sheet).
    """
    import schem_refdes as sr
    from shapely.geometry import LineString, Point

    tol = 0.01
    per_sheet = []
    tot = {"symbols": 0, "wires": 0, "wire_crossings": 0,
           "label_collisions": 0, "refdes_overlaps": 0}
    for path in sch_paths:
        sheet = sr.Sheet(Path(path))

        wires = []
        for w in sr._kids(sheet.root, "wire"):
            pts = sr._pts(w)
            if len(pts) >= 2:
                ls = LineString(pts)
                if ls.length > 0:
                    wires.append(ls)
        junctions = []
        for j in sr._kids(sheet.root, "junction"):
            a = sr._nums(sr._kid(j, "at"))
            if len(a) >= 2:
                junctions.append(Point(a[0], a[1]))

        crossings = 0
        for i in range(len(wires)):
            for k in range(i + 1, len(wires)):
                if not wires[i].crosses(wires[k]):
                    continue
                x = wires[i].intersection(wires[k])
                pts = []
                if x.geom_type == "Point":
                    pts = [x]
                elif hasattr(x, "geoms"):
                    pts = [g for g in x.geoms if g.geom_type == "Point"]
                for p in pts:
                    if not any(p.distance(j) <= 0.5 + tol for j in junctions):
                        crossings += 1

        boxes = [(name, geom) for kind, geom, name in sheet.statics
                 if kind in ("label", "global_label", "hierarchical_label",
                             "text")]
        label_hits = 0
        for i in range(len(boxes)):
            for k in range(i + 1, len(boxes)):
                if boxes[i][1].intersects(boxes[k][1]):
                    label_hits += 1

        overlaps = len(sr.audit_sheet(sheet, clearance))

        per_sheet.append({"sheet": Path(path).name,
                          "symbols": len(sheet.symbols),
                          "wires": len(wires),
                          "wire_crossings": crossings,
                          "label_collisions": label_hits,
                          "refdes_overlaps": overlaps})
        tot["symbols"] += len(sheet.symbols)
        tot["wires"] += len(wires)
        tot["wire_crossings"] += crossings
        tot["label_collisions"] += label_hits
        tot["refdes_overlaps"] += overlaps

    counts = [s["symbols"] for s in per_sheet]
    mean = sum(counts) / len(counts) if counts else 0.0
    balance = round(max(counts) / mean, 4) if mean > 0 else 1.0
    return {**tot, "sheets": len(per_sheet), "sheet_balance": balance,
            "per_sheet": per_sheet}


# ------------------------------------------------------ P2 constraints leg

def placement_refs_missing(constraints: dict, components: dict) -> list[str]:
    """Constraints refs that do not exist in the netlist components
    (placement edges/groups/fixed/separation + thermal[].ref - the same
    walk netlist_audit's missing_ref check performs; bench's score_p2
    counts the defect HERE and filters kind missing_ref out of its
    audit_errors tally so it keeps exactly one weight).
    """
    placement = constraints.get("placement") or {}
    refs: list[str] = []
    for e in placement.get("edges") or []:
        refs.append(e.get("ref"))
    for g in placement.get("groups") or []:
        refs.append(g.get("anchor"))
        refs.extend(g.get("members") or [])
    refs.extend(placement.get("fixed") or [])
    for s in placement.get("separation") or []:
        refs.extend(s.get("a") or [])
        refs.extend(s.get("b") or [])
    for t in constraints.get("thermal") or []:
        refs.append(t.get("ref"))
    missing = sorted({r for r in refs if r and r not in components})
    return missing


# ------------------------------------------------------- known-answer diff

def _ka_match(exp: dict, v: dict) -> bool:
    if exp.get("check") and v.get("check") != exp["check"]:
        return False
    if exp.get("kind") and v.get("kind") != exp["kind"]:
        return False
    if exp.get("net") and v.get("net") != exp["net"]:
        return False
    if exp.get("ref") and exp["ref"] not in (v.get("refs") or []):
        return False
    return True


def match_known_answer(known: dict, violations: list[dict]) -> dict:
    """Score a findings list against a fixture's known answer.

    known:
      expected: [{check?, kind?, net?, ref?}]  - each must match >= 1 finding
                                                 (all given keys must match;
                                                 ref matches the refs list)
      forbid_errors: true                      - no error-severity finding
                                                 outside the expected set
    Returns {status: 'ok'|'miss', matched, missed, forbidden_errors}.
    """
    expected = known.get("expected") or []
    matched = [e for e in expected
               if any(_ka_match(e, v) for v in violations)]
    missed = [e for e in expected
              if not any(_ka_match(e, v) for v in violations)]
    forbidden = 0
    if known.get("forbid_errors"):
        # A finding matching ANY expected pattern is excused, however many
        # times it fires and in whatever order - a severity ladder emitting
        # both a warning and an error for the same declared signature must
        # not flip the verdict on emission order.
        for v in violations:
            if v.get("severity") != "error":
                continue
            if any(_ka_match(e, v) for e in expected):
                continue
            forbidden += 1
    status = "ok" if not missed and not forbidden else "miss"
    return {"status": status, "matched": len(matched), "missed": missed,
            "forbidden_errors": forbidden}


# ------------------------------------------------------------- composites

# Per-stage penalty weights (documented constants; see module docstring for
# the within-fixture-only comparability rule).  composite = 100 - sum(w*m),
# floored at 0, rounded to 2dp.  Penalty terms are also returned so the
# tuning loop can see WHAT moved, not just the scalar.
WEIGHTS = {
    # U15 research quality (researchlib.assess): a task with no output floors
    # the score; provenance breaches (a citation outside the task ledger) and
    # a forum-only record are the contract violations that must sink it;
    # lint/citation problems and unruled/refuted records are graded defects.
    "P1": {"no_records": 60.0, "off_ledger": 25.0, "forum_sole": 15.0,
           "lint_problems": 10.0, "citation_problems": 5.0,
           "unruled": 5.0, "refuted": 8.0},
    "P2": {"audit_errors": 15.0, "audit_warnings": 3.0,
           "placement_refs_missing": 10.0, "stackup_bad": 25.0},
    # known-answer style weighting (mirrors P8/P9): the pristine-pull fixture
    # has an exact 16 -> 0 real-DRC answer, so any before-count drift or
    # post-fix residue must visibly sink the composite, not shave points.
    "P3": {"after_violations": 10.0, "residue": 10.0,
           "missing_footprints": 25.0, "before_drift": 40.0},
    # unit weights on the drawing metrics + 0.5/ERC-warning: the s7 blinky2
    # fixture (deliberately dirty, 27 field overlaps) and the shipped
    # pd-trigger sheet (60 default-severity ERC warnings) must both stay off
    # the 0 floor or the loop has no gradient.
    "P4": {"wire_crossings": 1.0, "label_collisions": 1.0,
           "refdes_overlaps": 1.0, "sheet_balance_excess": 5.0,
           "erc_errors": 10.0, "erc_warnings": 0.5, "netlist_diffs": 25.0},
    "P5": {"setup_violations": 10.0, "not_clean": 25.0,
           "transient_silk": 1.0, "pro_floor_failures": 25.0},
    "P6": {"hpwl_total_mm": 0.05, "crossings": 1.0, "congestion_max": 2.0,
           "legality_violations": 5.0, "decap_worst_mm": 0.5},
    "P7": {"incompletion_pct": 1.0, "drc_errors": 5.0, "drc_warnings": 1.0,
           "via_count": 0.1, "track_mm": 0.01},
    # error weight 0.5: lumina-carrier's frozen board carries ~124 findings
    # under the post-T2 suite and must NOT floor at 0 - the tuning loop needs
    # gradient on exactly that fixture.
    "P8": {"errors": 0.5, "warnings": 0.1, "known_answer_missed": 40.0,
           "forbidden_errors": 10.0},
    "P9": {"errors": 0.5, "warnings": 0.1, "known_answer_missed": 40.0,
           "forbidden_errors": 10.0},
    "P10": {"not_ready": 50.0, "missing_items": 10.0},
    # E2E (reference/e2e-scoring.md): each shortfall is 1 - a category score
    # in [0, 1].  Cost keeps EEBench's 0.35; electrical + layout split its
    # 0.65 technical share, so a board with no layout scores as EEBench would
    # minus the 20 layout points.
    "E2E": {"electrical_shortfall": 45.0, "layout_shortfall": 20.0,
            "cost_shortfall": 35.0},
}


def composite(stage: str, penalties: dict[str, float]) -> float:
    """100 minus the weighted penalties this stage declares; >= 0, 2dp.

    Unknown penalty keys raise - a scorer emitting a term the weight table
    does not know about is a bug, not a silent no-op.
    """
    weights = WEIGHTS[stage]
    unknown = sorted(set(penalties) - set(weights))
    if unknown:
        raise BenchError(f"{stage}: penalty terms without weights: {unknown}")
    score = 100.0
    for key, value in penalties.items():
        score -= weights[key] * float(value or 0)
    return round(max(score, 0.0), 2)
