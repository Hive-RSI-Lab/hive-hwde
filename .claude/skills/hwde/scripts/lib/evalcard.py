"""evalcard - per-board design scorecards, the suite score and the results store.

docs/design-evals.md is the contract; this module is its code.

Findings: one shape for every grader (`finding()`): check, kind, refs, net,
pos, severity, failure_mode, ladder, plus area, verdict, weight, msg and fix.
The first six are what tests/golden/scorecard/triage.yaml matches on.
`fix` is the one line a model mid-design acts on: the remedy the check wrote
after the ';' in its message, else the remediation file for the kind.

Area score: a finding counts by how likely it is to be real. A triaged finding
is 1 (real) or 0 (fp); an untriaged or waived one counts at its check's
precision from the check scorecard (docs/check-scorecard.jsonl, last line),
shrunk toward PRIOR_PRECISION by its sample size: (tp + 1) / (tp + fp + 2),
so two triaged findings do not make a check certain, and a check nobody has
triaged counts at 0.5. weight = severity
weight x that probability, and an area scores 1 / (1 + sum of its weights).
A waiver is the designer's call, not ground truth, so a waived finding still
counts; the scorecard reports it as waived. An area no check ran in (a bare
board has no constraints, so its power checks skip) can be passed to
`board_card()` as unscored: its score is None and it stays out of the
composite and the suite, because no finding there is not a clean bill.

Suite score: the mean of per-board scores with a 95 % cluster-bootstrap
interval (resample clusters - briefs, or boards when each board is its own
cluster - then members within each). The RNG is seeded, so a suite re-scores
identically.

Report: `report_md()` writes the last recorded suite and its boards from the
store as markdown, so the tables are generated, never hand-edited.

Results store: results/<kind>.jsonl under the repo (HWDE_RESULTS_ROOT
overrides), one JSON object per line, appended. Every record carries the run
key (`run_key()`): hwde commit, KiCad version, harness, model, subject
(fixture or board), seed and detail level.
"""
from __future__ import annotations

import json
import os
import random
import statistics
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
REMEDIATIONS = HERE.parents[1] / "reference" / "remediations"
SCORECARD_HISTORY = REPO / "docs" / "check-scorecard.jsonl"

AREAS = ("schematic", "layout", "signal_integrity", "power", "manufacturing")
# Which area a check's findings land in. A check missing here lands in
# "layout" and the scorecard lists it under unmapped_checks.
CHECK_AREA = {
    "gate_erc": "schematic",
    "check_ratings": "schematic",
    "gate_drc": "layout",
    "check_mating": "layout",
    "check_return_path": "signal_integrity",
    "check_diffpair": "signal_integrity",
    "check_route_style": "signal_integrity",
    "check_current": "power",
    "check_decoupling": "power",
    "check_pdn": "power",
    "check_thermal": "power",
    "check_creepage": "power",
    "check_silk": "manufacturing",
    "gate_dfm": "manufacturing",
    "dfm_check": "manufacturing",
}
SEVERITY_WEIGHT = {"error": 1.0, "warning": 0.25, "info": 0.0}
PRIOR_PRECISION = 0.5
BOOT_N = 2000
BOOT_SEED = 0


# ------------------------------------------------------------ findings

def fix_line(v: dict) -> str | None:
    """What to change, in one line: the check's own remedy clause, else a
    pointer to the kind's remediation note, else None."""
    msg = str(v.get("msg") or "")
    if ";" in msg:
        tail = msg.rsplit(";", 1)[1].strip()
        if tail and not tail.lower().startswith("advisory"):
            return tail
    kind = v.get("kind")
    if kind and (REMEDIATIONS / f"{kind}.md").is_file():
        return f"see reference/remediations/{kind}.md"
    return None


def finding(check: str, v: dict, verdict: str, precision: float | None,
            failure_modes: dict | None = None) -> dict:
    """One raw check violation in the shared finding shape, weighted."""
    sev = v.get("severity") or "warning"
    p = {"real": 1.0, "fp": 0.0}.get(
        verdict, PRIOR_PRECISION if precision is None else precision)
    kind = v.get("kind")
    fm = (failure_modes or {}).get((check, kind)) or (failure_modes or {}).get((check, None))
    return {
        "check": check, "kind": kind, "refs": v.get("refs") or [],
        "net": v.get("net"), "pos": v.get("pos"), "severity": sev,
        "failure_mode": fm, "ladder": v.get("ladder"),
        "area": CHECK_AREA.get(check, "layout"), "verdict": verdict,
        "p_real": round(p, 4),
        "weight": round(SEVERITY_WEIGHT.get(sev, 0.25) * p, 4),
        "msg": v.get("msg"), "fix": fix_line(v),
    }


def rank(findings: list[dict]) -> list[dict]:
    """Most likely real and most severe first; ties keep a stable order."""
    return sorted(findings, key=lambda f: (-f["weight"], f["check"],
                                           str(f.get("kind")), str(f.get("refs"))))


def gate_findings(ws: Path) -> list[dict]:
    """The ERC, DRC and DFM gates' recorded verdicts as raw violations, read
    not re-run. state.json's `gates` is the record; reports/gate-<g>.json,
    when present, gives the failing items. A failing gate with no report
    is one finding carrying the failing count; a gate in neither place is a
    `gate_missing` finding."""
    try:
        gates = json.loads((ws / "state.json").read_text(encoding="utf-8")).get("gates") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        gates = {}
    out = []
    for gate, check in (("erc", "gate_erc"), ("drc_routed", "gate_drc"),
                        ("dfm", "gate_dfm")):
        p = ws / "reports" / f"gate-{gate}.json"
        rep = {}
        if p.is_file():
            try:
                rep = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rep = {}
        rec = gates.get(gate) if isinstance(gates.get(gate), dict) else {}
        status = rec.get("status") or rep.get("status")
        if status is None:
            out.append({"check": check, "severity": "error", "kind": "gate_missing",
                        "msg": f"the {gate} gate has no recorded result; "
                               f"run the {gate} gate", "refs": []})
            continue
        if status == "pass":
            continue
        items = [f for f in rep.get("failing") or [] if isinstance(f, dict)]
        for f in items:
            out.append({**f, "check": check,
                        "kind": f.get("kind") or f.get("type") or gate,
                        "severity": f.get("severity") or "error"})
        if not items:
            n = (rec.get("last") or {}).get("failing_count") or rep.get("failing_count")
            out.append({"check": check, "severity": "error", "kind": f"{gate}_fail",
                        "msg": f"the {gate} gate's last run failed"
                               + (f" with {n} violation(s)" if n else "")
                               + f"; fix them and re-run the {gate} gate",
                        "refs": []})
    return out


def precisions(history: Path = SCORECARD_HISTORY) -> dict[str, float]:
    """Each check's precision from the last check-scorecard line, Laplace-
    smoothed: (tp + 1) / (tp + fp + 2)."""
    if not history.is_file():
        return {}
    lines = [ln for ln in history.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return {}
    checks = json.loads(lines[-1]).get("checks") or {}
    return {c: round(((d.get("tp") or 0) + 1) / ((d.get("tp") or 0) + (d.get("fp") or 0) + 2), 4)
            for c, d in checks.items()}


def load_failure_modes(path: Path) -> dict:
    """(check, kind) -> failure-mode id from docs/failure-modes.yaml, when it
    exists. Two entry forms are read:

    - {id, checks: [{check, kind?} | check]};
    - {id, coverage: {script, rule}}, the form docs/failure-modes.yaml uses:
      script names the check, rule is '-' (any kind) or a list of kinds
      split on ',' and ';', where 'other_script:kind' names another check.
      A parenthetical note ('(partial)', '(when family listed)') is dropped.

    The first mode to claim a (check, kind) keeps it; anything else maps
    nothing."""
    if not path.is_file():
        return {}
    import re
    import yaml
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    modes = doc.get("modes") if isinstance(doc, dict) else doc

    def bare(s) -> str:
        return re.sub(r"\s*\(.*?\)", "", str(s or "")).strip()

    out = {}
    for m in modes or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        for c in m.get("checks") or []:
            if isinstance(c, dict) and c.get("check"):
                out.setdefault((c["check"], c.get("kind")), m["id"])
            elif isinstance(c, str):
                out.setdefault((c, None), m["id"])
        cov = m.get("coverage")
        script = bare(cov.get("script")) if isinstance(cov, dict) else ""
        if not script or script == "-" or " " in script:
            continue
        for item in re.split(r"[,;]", str(cov.get("rule") or "-")):
            item = bare(item)
            chk, kind = (item.split(":", 1) if ":" in item else (script, item))
            if chk and " " not in chk and " " not in kind:
                out.setdefault((chk, None if kind in ("", "-") else kind), m["id"])
    return out


# ------------------------------------------------------------ scoring

def area_scores(findings: list[dict]) -> dict:
    out = {}
    for a in AREAS:
        fs = [f for f in findings if f["area"] == a]
        expected = round(sum(f["weight"] for f in fs), 4)
        out[a] = {"score": round(1.0 / (1.0 + expected), 4),
                  "expected_real": expected, "findings": len(fs)}
    return out


def board_card(board: str, findings: list[dict], info: dict | None = None,
               top: int = 10, scored_areas=None) -> dict:
    """A board's scorecard: areas, composite (unweighted area mean until
    bring-up data can fit weights) and the ranked findings. scored_areas,
    when given, names the areas some check actually ran in; any other area
    has score None (no check looked, which is not a clean bill) and stays
    out of the composite."""
    ranked = rank(findings)
    areas = area_scores(ranked)
    if scored_areas is not None:
        for a in AREAS:
            if a not in scored_areas:
                areas[a]["score"] = None
    scores = [a["score"] for a in areas.values() if a["score"] is not None]
    composite = round(100 * statistics.fmean(scores), 2) if scores else None
    unmapped = sorted({f["check"] for f in ranked if f["check"] not in CHECK_AREA})
    return {"board": board, "composite": composite, "areas": areas,
            "counts": {"findings": len(ranked),
                       "by_verdict": _count(ranked, "verdict")},
            "top_findings": ranked[:top], "findings": ranked,
            "unmapped_checks": unmapped, "info": info or {}}


def _count(fs, key):
    c: dict = {}
    for f in fs:
        c[f[key]] = c.get(f[key], 0) + 1
    return dict(sorted(c.items()))


def cluster_bootstrap(groups: dict[str, list[float]], n: int = BOOT_N,
                      seed: int = BOOT_SEED) -> dict:
    """Mean of all values and a 95 % interval: resample clusters with
    replacement, then values within each drawn cluster."""
    groups = {k: v for k, v in groups.items() if v}
    allv = [x for v in groups.values() for x in v]
    if not allv:
        return {"mean": None, "ci95": None, "n": 0, "clusters": 0}
    mean = statistics.fmean(allv)
    keys = sorted(groups)
    if len(keys) < 2:
        return {"mean": round(mean, 3), "ci95": None, "n": len(allv),
                "clusters": len(keys)}
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        draw = []
        for k in rng.choices(keys, k=len(keys)):
            g = groups[k]
            draw.extend(rng.choices(g, k=len(g)))
        means.append(statistics.fmean(draw))
    means.sort()
    lo, hi = means[int(0.025 * n)], means[int(0.975 * n) - 1]
    return {"mean": round(mean, 3), "ci95": [round(lo, 3), round(hi, 3)],
            "n": len(allv), "clusters": len(keys)}


def suite_score(cards: list[dict], cluster_of=None) -> dict:
    """Suite composite and per-area means with intervals. cluster_of maps a
    card to its cluster (a brief); default each board is its own."""
    cluster_of = cluster_of or (lambda c: c["board"])

    def grouped(val):  # recurring-defect-ok: held-files-unchecked — groups scorecards by brief for the bootstrap, not board rows
        g: dict[str, list[float]] = {}
        for c in cards:
            g.setdefault(cluster_of(c), []).append(val(c))
        return g

    def kept(g):  # an unscored area (None) is left out, not counted as 0 or 100
        return {k: [x for x in v if x is not None] for k, v in g.items()}

    out = {"boards": len(cards),
           "composite": cluster_bootstrap(kept(grouped(lambda c: c["composite"])))}
    out["areas"] = {a: cluster_bootstrap(kept(grouped(
        lambda c, a=a: None if c["areas"][a]["score"] is None
        else 100 * c["areas"][a]["score"]))) for a in AREAS}
    return out


# ------------------------------------------------------------ results store

def results_root() -> Path:
    return Path(os.environ.get("HWDE_RESULTS_ROOT") or (REPO / "results"))


def hwde_commit() -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=12", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def run_key(subject: str, harness: str = "frozen", model: str | None = None,
            seed: int | None = None, detail: str | None = None,
            kicad: str | None = None) -> dict:
    return {"hwde_commit": hwde_commit(), "kicad": kicad, "harness": harness,
            "model": model, "subject": subject, "seed": seed, "detail": detail}


def append(kind: str, record: dict) -> Path:
    """Append one record to results/<kind>.jsonl, stamped with a UTC time."""
    root = results_root()
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{kind}.jsonl"
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return p


# ------------------------------------------------------------ report

def read_store(kind: str) -> list[dict]:
    p = results_root() / f"{kind}.jsonl"
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _ci(x: dict) -> str:
    if x.get("mean") is None:
        return "-"
    ci = x.get("ci95")
    return f"{x['mean']:.1f}" + (f" [{ci[0]:.1f}, {ci[1]:.1f}]" if ci else "")


def report_md(records: list[dict]) -> str:
    """The last suite record and, for each of its boards, that board's last
    record before it, as markdown."""
    idx = max((i for i, r in enumerate(records) if r.get("kind") == "suite"),
              default=None)
    if idx is None:
        raise ValueError("no suite record in the store")
    suite = records[idx]
    names = suite["subject"].split(":", 1)[1].split(",")
    last = {}
    for r in records[:idx]:
        if r.get("kind") == "board" and r.get("subject") in names:
            last[r["subject"]] = r
    short = {"schematic": "schematic", "layout": "layout",
             "signal_integrity": "SI", "power": "power",
             "manufacturing": "mfg"}
    out = [f"Suite of {suite['boards']} boards, recorded {suite['ts']} at hwde "
           f"{suite.get('hwde_commit')}. Scores are 0-100 with a 95 % "
           "cluster-bootstrap interval.", "",
           "| area | mean [95 % CI] |", "|---|---|",
           f"| composite | {_ci(suite['composite'])} |"]
    out += [f"| {short[a]} | {_ci(suite['areas'][a])} |" for a in AREAS]
    out += ["", "| board | composite | " + " | ".join(short[a] for a in AREAS)
            + " | findings | top finding |",
            "|---|---|" + "---|" * len(AREAS) + "---|---|"]
    for n in sorted(names):
        r = last.get(n)
        if r is None:
            out.append(f"| {n} | missing | " + " | ".join("-" for _ in AREAS) + " | - | - |")
            continue
        top = (r.get("top_findings") or [{}])[0]
        what = f"{top.get('check')}/{top.get('kind')}" if top else "-"
        out.append(f"| {n} | {r['composite']:.1f} | "
                   + " | ".join(f"{100 * r['areas'][a]['score']:.0f}" for a in AREAS)
                   + f" | {r['counts']['findings']} | {what} |")
    return "\n".join(out) + "\n"
