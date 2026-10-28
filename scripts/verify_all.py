"""verify_all.py - run the P8 verification suite in parallel, merge results.

Runs every deterministic check (S4 crown jewels + S5) concurrently, each writing
its own reports/checks/<name>.json, then merges them into one stable summary
(reports/checks/summary.json). reports/checks is the WORKSPACE's (the dir
holding state.json above the board); a board outside any workspace writes
<pcb dir>/reports/checks. Old workspaces may still hold kicad/reports/checks;
readers resolve through statelib.check_reports_dir and `state.py resume`
moves them (workspace_schema 2). The orchestrator's `verify` gate (gates.yaml)
reads this summary; cluster_violations.py groups its violations for fixers.

Default (exploratory) mode: a check is SKIPPED (not failed) when an input it
requires is absent - e.g. no constraints.json means the constraint-driven
checks do not run. check_silk, check_diffpair, check_route_style and
check_mating need only the board (check_route_style fails on arcs and
off-45 segments, warns on jogs);
check_ratings needs the workspace parts dir (--parts, default <ws>/parts
beside the board's kicad/ dir).

--strict (U2, codex C7 - release contexts): every APPLICABLE check must run;
a missing input is a `skipped_error` coverage failure and the summary status
becomes "error" ("could not verify"), never a pass. Applicability is DECLARED
per board in constraints.json, never inferred from file presence:

    "verification": {"not_applicable": {
        "check_thermal": {"reason": "...", "approved": "<who> <when>"}}}

Every entry needs a non-empty reason + approved and must name a known check
(a typo must not silently disable a check). Declared-N/A checks do not run in
either mode.

Registering a check (TEST-12, docs/failure-modes.md section 4): the built-in
suite is BUILTIN_CHECKS; every other check joins by dropping a fragment
scripts/checks.d/<name>.yaml (format: checks.d/README.md), so no check row
edits this file. load_checks() validates each fragment - name equal to the
file stem, a <name>.py script beside checks.d/, known inputs only, every
placeholder an input the args may use, no name taken twice - and raises
CheckError on the first bad one: a broken fragment stops the suite loudly
rather than dropping its check.

Summary schema (stable - downstream consumers may rely on it):
    {"script": "verify_all", "board": "<name>", "status": pass|violations|error,
     "counts": {"total", "by_severity"{}, "by_source"{}, "by_check"{}},
     "checks": {"<name>": {"status": pass|violations|error|skipped|
                           skipped_error|not_applicable,
                           "counts"{}, "report": "<path>"|null, "reason"?}},
     "coverage": {"strict", "required"[], "ran"[], "passed"[], "failed"[],
                  "waived"[], "not_applicable"{name: {reason, approved}},
                  "skipped_error"{name: reason}},
     "violations": [ ...merged normalized violations, each keeps its "source"...],
     "report_schema", "generated_at", "input", "input_digest"}

coverage.failed = ran with error-severity findings; passed = ran without
(warnings allowed - the verify gate fails on [error]). Skips land in
coverage.skipped_error in BOTH modes (a coverage hole is a fact either way);
only strict turns them into an error status. coverage.waived is filled by
gate.py at evaluation (waivers are gate-side artifacts).

Exit: 0 all pass, 1 any violations, 2 any check errored (could not verify),
a strict coverage failure, or a bad invocation.

CLI: --pcb board.kicad_pcb [--constraints c.json] [--decoupling d.json]
     [--parts DIR] [--reports-dir DIR] [--out summary.json] [--jobs N]
     [--strict]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import string
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import statelib  # noqa: E402

SCRIPT = "verify_all"
CHECKS_D = HERE / "checks.d"
# the inputs a check may need; verify_all resolves each from the CLI
INPUT_KEYS = ("constraints", "decoupling", "parts")

# Each check: how to invoke it and which inputs it requires. `report` is the
# per-check JSON filename under the reports dir. New checks register through
# checks.d fragments (load_checks), not here.
BUILTIN_CHECKS = [
    {"name": "check_return_path", "needs": ["constraints"],
     "args": lambda a: ["--constraints", a["constraints"]]},
    {"name": "check_current", "needs": ["constraints"],
     "args": lambda a: ["--constraints", a["constraints"]]},
    {"name": "check_decoupling", "needs": ["decoupling"],
     "args": lambda a: ["--metadata", a["decoupling"]]},
    {"name": "check_diffpair", "needs": [],
     "args": lambda a: (["--constraints", a["constraints"]]
                        if a.get("constraints") else [])},
    {"name": "check_creepage", "needs": ["constraints"],
     "args": lambda a: ["--constraints", a["constraints"]]},
    {"name": "check_thermal", "needs": ["constraints"],
     "args": lambda a: ["--constraints", a["constraints"]]},
    {"name": "check_silk", "needs": [],
     "args": lambda a: []},
    {"name": "check_route_style", "needs": [],
     "args": lambda a: []},
    {"name": "check_mating", "needs": [],
     "args": lambda a: []},
    {"name": "check_pdn", "needs": ["constraints", "decoupling"],
     "args": lambda a: ["--constraints", a["constraints"],
                        "--decoupling", a["decoupling"]]},
    {"name": "check_ratings", "needs": ["parts"],
     "args": lambda a: ["--parts", a["parts"]] + (
         ["--constraints", a["constraints"]] if a.get("constraints") else [])},
]


def _fields(tokens: list, where: str) -> set[str]:
    """The {placeholder} names in a fragment's argument tokens."""
    if not isinstance(tokens, list) or \
            not all(isinstance(t, str) for t in tokens):
        raise checklib.CheckError(f"{where} must be a list of strings")
    try:
        return {f for t in tokens for _, f, _, _ in string.Formatter().parse(t)
                if f is not None}
    except ValueError as exc:
        raise checklib.CheckError(f"{where}: {exc}") from exc


def _fragment_check(path: Path, taken: set[str]) -> dict:
    """One checks.d fragment as a CHECKS entry, validated (module doc)."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise checklib.CheckError(f"{path}: unreadable: {exc}") from exc
    if not isinstance(doc, dict):
        raise checklib.CheckError(f"{path}: not a mapping")
    extra = set(doc) - {"name", "needs", "args", "optional"}
    if extra:
        raise checklib.CheckError(f"{path}: unknown keys {sorted(extra)}")
    name = doc.get("name")
    if name != path.stem:
        raise checklib.CheckError(
            f"{path}: name {name!r} must equal the file stem {path.stem!r}")
    if name in taken:
        raise checklib.CheckError(f"{path}: check {name!r} already registered")
    script = path.parent.parent / f"{name}.py"
    if not script.is_file():
        raise checklib.CheckError(f"{path}: script {script} missing")
    needs = doc.get("needs") or []
    optional = doc.get("optional") or {}
    if not isinstance(needs, list) or not isinstance(optional, dict):
        raise checklib.CheckError(
            f"{path}: needs must be a list, optional a mapping")
    bad = [k for k in [*needs, *optional] if k not in INPUT_KEYS]
    if bad:
        raise checklib.CheckError(
            f"{path}: unknown inputs {bad} (known: {', '.join(INPUT_KEYS)})")
    if set(needs) & set(optional):
        raise checklib.CheckError(f"{path}: an input is both needed and "
                                  "optional")
    args = doc.get("args") or []
    stray = _fields(args, f"{path}: args") - set(needs)
    for key, tokens in optional.items():
        stray |= _fields(tokens, f"{path}: optional.{key}") - {key}
    if stray:
        # an args placeholder must be a needed input (always present), an
        # optional one only its own key (present when it is appended)
        raise checklib.CheckError(
            f"{path}: placeholders {sorted(stray)} name no input the "
            "args may use")

    def argv(a, args=args, optional=optional):
        out = [t.format_map(a) for t in args]
        for key, tokens in optional.items():
            if a.get(key):
                out += [t.format_map(a) for t in tokens]
        return out
    return {"name": name, "needs": list(needs), "args": argv,
            "script": script}


def load_checks(checks_d: Path = CHECKS_D) -> list[dict]:
    """BUILTIN_CHECKS plus one entry per checks.d/*.yaml fragment, in file
    name order."""
    checks = list(BUILTIN_CHECKS)
    taken = {c["name"] for c in checks}
    for path in sorted(Path(checks_d).glob("*.yaml")):
        check = _fragment_check(path, taken)
        taken.add(check["name"])
        checks.append(check)
    return checks


CHECKS = load_checks()


def check_script(check: dict) -> Path:
    """The script a CHECKS entry runs: a fragment's own, else <name>.py."""
    return check.get("script") or HERE / f"{check['name']}.py"


def load_not_applicable(constraints_path: str | None) -> dict:
    """The board's declared-N/A checks (constraints.json verification block),
    validated: every entry must name a known check and carry non-empty
    reason + approved - N/A declarations are human artifacts, and a typo
    must never silently disable a check (codex C7)."""
    if not constraints_path:
        return {}
    doc = checklib.load_json(constraints_path, "constraints")
    na = (doc.get("verification") or {}).get("not_applicable") or {}
    known = {c["name"] for c in CHECKS}
    for name, entry in na.items():
        if name not in known:
            raise checklib.CheckError(
                f"verification.not_applicable names unknown check {name!r} "
                f"(known: {', '.join(sorted(known))})")
        if not isinstance(entry, dict) \
                or not str(entry.get("reason") or "").strip() \
                or not str(entry.get("approved") or "").strip():
            raise checklib.CheckError(
                f"verification.not_applicable[{name!r}] needs non-empty "
                "'reason' and 'approved'")
    return na


def run_one(check: dict, inputs: dict, reports_dir: Path,
            na: dict | None = None, strict: bool = False) -> dict:
    """Run one check as a subprocess; return its merged-summary entry."""
    name = check["name"]
    if na and name in na:
        return {"name": name, "status": "not_applicable",
                "counts": {"total": 0}, "report": None,
                "reason": na[name].get("reason"), "violations": []}
    missing = [k for k in check["needs"] if not inputs.get(k)]
    if missing:
        return {"name": name,
                "status": "skipped_error" if strict else "skipped",
                "counts": {"total": 0},
                "report": None, "reason": f"no {'/'.join(missing)}",
                "violations": []}
    report_path = reports_dir / f"{name}.json"
    # a check that ERRORS does not write --out (cli_wrap prints to stdout); drop
    # any stale report first so this run cannot read a previous run's pass file.
    try:
        report_path.unlink()
    except FileNotFoundError:
        pass
    cmd = [sys.executable, str(check_script(check)), "--pcb", inputs["pcb"],
           *check["args"](inputs), "--out", str(report_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    payload = None
    if report_path.exists():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
    if payload is None:  # error path: cli_wrap printed JSON to stdout, no file
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {"script": name, "status": "error",
                       "error": (proc.stderr or proc.stdout or
                                 f"exit {proc.returncode}").strip()[:400]}
    return {
        "name": name,
        "status": payload.get("status", "error"),
        "counts": payload.get("counts", {"total": 0}),
        "report": str(report_path) if report_path.exists() else None,
        "error": payload.get("error"),
        "violations": payload.get("violations", []),
    }


def constraints_drift(constraints_path: str | None) -> dict | None:
    """Twin-sidecar preflight (T6, ladder row 154): the pipeline keeps
    constraints.json in BOTH <ws>/architecture/ and <ws>/kicad/; checks run
    against the given (kicad/) copy. When the architecture twin exists and
    parses to a DIFFERENT object (formatting drift is fine), return a
    warning violation - the 61-vs-53-answers class becomes visible at H4.
    Reconciliation is an owner decision; no winner is picked here."""
    if not constraints_path:
        return None
    given = Path(constraints_path)
    twin = given.resolve().parent.parent / "architecture" / "constraints.json"
    if not twin.is_file():
        return None
    try:
        a = json.loads(twin.read_text(encoding="utf-8"))
        b = json.loads(given.read_text(encoding="utf-8"))
        same = a == b
    except (OSError, json.JSONDecodeError) as exc:
        same, a = False, f"unreadable twin: {exc}"
    if same:
        return None
    return {
        "check": "verify_all", "severity": "warning", "pos": None,
        "layer": None, "net": None, "refs": [],
        "msg": (f"architecture/constraints.json diverges from {given.name} "
                f"in {given.parent.name}/; checks ran against the latter - "
                "reconcile or delete the stale twin"),
        "source": "verify_all", "kind": "constraints_drift",
        "twin": str(twin).replace("\\", "/"),
        "items": [{"msg": "constraints twin drift", "pos": None}],
    }


def coverage_matrix(results: list[dict], na: dict, strict: bool) -> dict:
    """The C7 coverage matrix: which checks were required, which actually
    ran, and where the holes are. Skips land in skipped_error in BOTH modes
    (a coverage hole is a fact either way); only strict makes them fail the
    run. `waived` is filled by gate.py at evaluation time."""
    ran, passed, failed = [], [], []
    skipped_error: dict[str, str] = {}
    for r in results:
        st = r["status"]
        if st in ("pass", "violations"):
            ran.append(r["name"])
            if any(v.get("severity") == "error" for v in r["violations"]):
                failed.append(r["name"])
            else:
                passed.append(r["name"])
        elif st in ("skipped", "skipped_error"):
            skipped_error[r["name"]] = r.get("reason") or "input missing"
        elif st == "error":
            skipped_error[r["name"]] = (r.get("error")
                                        or "check errored")[:200]
    return {
        "strict": strict,
        "required": [r["name"] for r in results
                     if r["status"] != "not_applicable"],
        "ran": ran, "passed": passed, "failed": failed,
        "waived": [],
        "not_applicable": {n: {"reason": e.get("reason"),
                               "approved": e.get("approved")}
                           for n, e in na.items()},
        "skipped_error": skipped_error,
    }


def merge(board, results: list[dict], na: dict | None = None,
          strict: bool = False) -> dict:
    all_v: list[dict] = []
    checks: dict[str, dict] = {}
    by_check: dict[str, int] = {}
    errored = False
    for r in results:
        for v in r["violations"]:
            all_v.append(v)
        by_check[r["name"]] = len(r["violations"])
        if r["status"] in ("error", "skipped_error"):
            errored = True  # skipped_error only exists under strict
        entry = {"status": r["status"], "counts": r["counts"],
                 "report": r["report"]}
        if r.get("reason"):
            entry["reason"] = r["reason"]
        if r.get("error"):
            entry["error"] = r["error"]
        checks[r["name"]] = entry
    counts = checklib.summarize(all_v)
    counts["by_check"] = by_check
    status = "error" if errored else ("violations" if all_v else "pass")
    return {"script": SCRIPT, "board": Path(board).name, "status": status,
            "counts": counts, "checks": checks,
            "coverage": coverage_matrix(results, na or {}, strict),
            "violations": all_v}


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Run the P8 verification suite in parallel and merge.")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--constraints", help="constraints.json")
    ap.add_argument("--decoupling", help="decoupling.json")
    ap.add_argument("--parts", help="P3 parts dir: parts.json + per-part "
                    "extractions (default <ws>/parts when it exists)")
    ap.add_argument("--reports-dir", help="dir for per-check + summary JSON "
                    "(default: <workspace>/reports/checks, or "
                    "<pcb dir>/reports/checks outside a workspace)")
    ap.add_argument("--out", help="write the summary here (also to reports dir)")
    ap.add_argument("--jobs", type=int, default=8, help="max parallel checks")
    ap.add_argument("--strict", action="store_true",
                    help="release mode (codex C7): every applicable check "
                         "must run; a missing input is a coverage failure "
                         "(status error), never a skip")
    args = ap.parse_args(argv)

    pcb = Path(args.pcb)
    if not pcb.exists():
        raise checklib.CheckError(f"board not found: {pcb}")
    na = load_not_applicable(args.constraints)
    reports_dir = Path(args.reports_dir) if args.reports_dir else \
        statelib.reports_dir_for(pcb)
    reports_dir.mkdir(parents=True, exist_ok=True)
    parts = Path(args.parts) if args.parts else \
        pcb.resolve().parent.parent / "parts"
    inputs = {"pcb": str(pcb), "constraints": args.constraints,
              "decoupling": args.decoupling,
              "parts": str(parts) if parts.is_dir() else None}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) \
            as ex:
        results = list(ex.map(
            lambda c: run_one(c, inputs, reports_dir, na, args.strict),
            CHECKS))
    # keep CHECKS order (ThreadPoolExecutor.map preserves input order)
    summary = merge(pcb, results, na, args.strict)
    checklib.stamp(summary, pcb)
    drift = constraints_drift(args.constraints)
    if drift is not None:
        summary["violations"].append(drift)
        counts = checklib.summarize(summary["violations"])
        counts["by_check"] = {**summary["counts"]["by_check"],
                              "verify_all": 1}
        summary["counts"] = counts
        if summary["status"] == "pass":
            summary["status"] = "violations"
    (reports_dir / "summary.json").write_text(
        json.dumps(summary, indent=1), encoding="utf-8")
    return summary, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
