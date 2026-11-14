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

The netlist checks (check_straps, check_usb_phy, check_reset_drive,
check_cap_load, check_transformer) read the board's own <stem>.net. A board
that carries only its schematic (#885: a pinned foreign board holds kicad/
and state.json) gets one exported once per run from <stem>.kicad_sch into
<reports dir>/netlist/ (never beside the board) and passed as --netlist;
--netlist names one outright. With no kicad-cli, or an export that fails,
the checks see no netlist and say so (no_netlist), and the summary's
`netlist` entry says why.

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
                           skipped_error|not_applicable|nothing_to_judge,
                           "counts"{}, "report": "<path>"|null, "reason"?}},
     "coverage": {"strict", "required"[], "ran"[], "passed"[], "failed"[],
                  "waived"[], "not_applicable"{name: {reason, approved}},
                  "skipped_error"{name: reason},
                  "nothing_to_judge"{name: reason},
                  "not_judged"{name: [what]}},
     "violations": [ ...merged normalized violations, each keeps its "source"...],
     "report_schema", "generated_at", "input", "input_digest"}

coverage.failed = ran with error-severity findings; passed = ran without
(warnings allowed - the verify gate fails on [error]). Skips land in
coverage.skipped_error in BOTH modes (a coverage hole is a fact either way);
only strict turns them into an error status. coverage.waived is filled by
gate.py at evaluation (waivers are gate-side artifacts).

A check that ran and found nothing it could judge (its report carries
`nothing_to_judge`: no MCU on an MCU check, no netlist) is not a pass: it
stays in `ran` but leaves `passed` for coverage.nothing_to_judge, and its
checks entry reads status nothing_to_judge. An error finding keeps it in
`failed`; a warning about the hole (mcu_unknown) doesn't make it a pass. It
fails neither mode (a board with no MCU is fine); it is said out loud.
`not_judged` lists what a check that judged other things had to leave out.

The connector checks are the exception (#910, the laptop board's backwards
SD socket): check_mating or check_connector_fit reporting nothing_to_judge
while its not_judged names connectors means the board HAS connectors and no
one of them was checked. Under --strict (the release gate) merge() adds a
verify_all error, kind connector_check_unjudged, naming them; mid-pipeline
runs keep it in coverage only (nothing_to_judge, not_judged), as for any
other check, so an early board isn't buried in it. The way past it is a verify-waivers.json
entry for that kind whose refs and reason name the connectors (durable,
since the release gate is strict). check_mate_pins is left out: it has
nothing to judge on any board without a mating pair, which is most.

Edge connectors get the same at release even when others were judged (#911):
each entry of check_mating's edge_unjudged (a J/P/CN part within a few mm of
the outline that no family knows) becomes a check_mating error, kind
mating_edge_unjudged, under --strict.

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
INPUT_KEYS = ("constraints", "decoupling", "parts", "netlist")

# Each check: how to invoke it and which inputs it requires. `report` is the
# per-check JSON filename under the reports dir. New checks register through
# checks.d fragments (load_checks), not here.
BUILTIN_CHECKS = [
    {"name": "check_return_path", "needs": ["constraints"],
     "args": lambda a: ["--constraints", a["constraints"]]},
    # No constraints.json still judges the switch nodes inferred from the
    # parts (an LM2596's SW pin at its rated current).
    {"name": "check_current", "needs": [],
     "args": lambda a: (["--constraints", a["constraints"]]
                        if a.get("constraints") else [])},
    {"name": "check_decoupling", "needs": ["decoupling"],
     "args": lambda a: ["--metadata", a["decoupling"]]},
    {"name": "check_diffpair", "needs": [],
     "args": lambda a: (["--constraints", a["constraints"]]
                        if a.get("constraints") else [])},
    {"name": "check_creepage", "needs": ["constraints"],
     "args": lambda a: ["--constraints", a["constraints"]]},
    {"name": "check_thermal", "needs": [],
     "args": lambda a: (["--constraints", a["constraints"]]
                        if a.get("constraints") else [])},
    {"name": "check_silk", "needs": [],
     "args": lambda a: []},
    {"name": "check_route_style", "needs": [],
     "args": lambda a: []},
    {"name": "check_mating", "needs": [],
     "args": lambda a: (["--constraints", a["constraints"]]
                        if a.get("constraints") else [])
     + (["--parts", a["parts"]] if a.get("parts") else [])},
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
        bad = checklib.approval_problem(entry.get("approved"))
        if bad:
            raise checklib.CheckError(
                f"verification.not_applicable[{name!r}]: {bad}")
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
        "nothing_to_judge": payload.get("nothing_to_judge"),
        "not_judged": payload.get("not_judged"),
        "edge_unjudged": payload.get("edge_unjudged"),
    }


def board_netlist(pcb: Path, reports_dir: Path, given: str | None = None
                  ) -> tuple[Path | None, dict | None]:
    """The netlist to hand the netlist checks, and a note for the summary.
    A given one wins; a .net the checks find beside the board themselves
    (check_straps.find_netlist) is left to them (None, None). Otherwise the
    board's own <stem>.kicad_sch is exported once into <reports
    dir>/netlist/, so a schematic-only board is still judged (#885). No
    schematic, no kicad-cli or a failed export returns None with the
    reason: the checks then say there is no netlist."""
    from lib import legacy_net  # noqa: PLC0415
    out = Path(reports_dir) / "netlist" / f"{pcb.stem}.net"
    if given:
        if not Path(given).is_file():
            raise checklib.CheckError(f"netlist not found: {given}")
        if not legacy_net.is_legacy_net(Path(given)):
            return Path(given), {"source": "given", "path": str(given)}
        found = Path(given)
    else:
        from check_straps import find_netlist  # noqa: PLC0415
        found = find_netlist(pcb)
    if found is not None:
        if not legacy_net.is_legacy_net(found):
            return None, None
        # an eeschema 4/5 netlist has no pin names: fill them (#1023)
        try:
            legacy_net.from_shipped(found, out)
        except (OSError, legacy_net.LegacyError, ValueError) as exc:
            return None, {"source": None,
                          "reason": f"{found.name} (KiCad 4/5) unreadable: {exc}"}
        return out, {"source": "legacy", "from": found.name, "path": str(out)}
    sch = pcb.with_suffix(".kicad_sch")
    legacy = pcb.with_suffix(".sch")
    if not sch.is_file() and legacy_net.is_legacy(legacy):
        r = legacy_net.netlist(legacy, out, pcb=pcb)
        if r.get("status") != "pass":
            return None, {"source": None, "reason": r["error"][:300]}
        return out, {"source": "legacy", "from": f"{r['from']} ({r['source']})",
                     "path": str(out)}
    if not sch.is_file():
        return None, {"source": None,
                      "reason": f"no {pcb.with_suffix('.net').name} or "
                                f"{sch.name} beside the board"}
    from lib import env  # noqa: PLC0415
    import kc  # noqa: PLC0415  (deferred: only this path needs kicad-cli)
    try:
        cli = env.find_kicad_cli()
    except env.EnvError as exc:
        return None, {"source": None, "reason": f"kicad-cli: {exc}"}
    if cli is None:
        return None, {"source": None,
                      "reason": f"no kicad-cli on this host to export "
                                f"{sch.name}"}
    out.unlink(missing_ok=True)
    r = kc.export_netlist(cli, sch, out)
    if r.get("status") != "pass" or not out.is_file():
        return None, {"source": None,
                      "reason": f"netlist export from {sch.name} failed: "
                                f"{str(r.get('error') or r)[:300]}"}
    return out, {"source": "exported", "from": sch.name, "path": str(out)}


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
    nothing: dict[str, str] = {}
    not_judged: dict[str, list] = {}
    for r in results:
        st = r["status"]
        if r.get("not_judged"):
            not_judged[r["name"]] = r["not_judged"]
        if st in ("pass", "violations"):
            ran.append(r["name"])
            if any(v.get("severity") == "error" for v in r["violations"]):
                failed.append(r["name"])
            elif r.get("nothing_to_judge"):
                nothing[r["name"]] = r["nothing_to_judge"]
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
        "nothing_to_judge": nothing,
        "not_judged": not_judged,
    }


CONNECTOR_CHECKS = ("check_mating", "check_connector_fit")


def connector_unjudged(r: dict, strict: bool) -> dict | None:
    """A connector check that judged no connector on a board that has some
    (#910): an error naming them, under strict only. None otherwise."""
    if not strict or r["name"] not in CONNECTOR_CHECKS or not r.get("nothing_to_judge") \
            or not r.get("not_judged"):
        return None
    left = [str(x) for x in r["not_judged"]]
    refs = sorted({x.split(":")[0].split()[0] for x in left if x.split()})
    return checklib.violation(
        "verify_all", "error", None, None, None,
        refs,
        f"{r['name']} judged no connector, but the board has "
        f"{len(left)}: {'; '.join(left[:8])}"
        + (" ..." if len(left) > 8 else "")
        + " - teach the check these parts (a connector_mating.yaml family, "
        "an extraction) or waive kind connector_check_unjudged in "
        "verify-waivers.json with a reason naming each connector",
        "verify_all", kind="connector_check_unjudged", unjudged_by=r["name"],
        not_judged=left)


def edge_unjudged(r: dict, strict: bool) -> list[dict]:
    """Under strict, an error for each edge connector check_mating could not
    judge (#911): no family row says which way it mates, so nothing showed
    its mouth faces off the board."""
    if not strict or r["name"] != "check_mating":
        return []
    out = []
    for e in r.get("edge_unjudged") or []:
        out.append(checklib.violation(
            "mating", "error", e.get("pos"), None, None, [e["ref"]],
            f"{e['ref']} ({e.get('footprint')}) sits "
            f"{e.get('edge_gap_mm')} mm from the board edge but no "
            "connector_mating.yaml family knows it, so nothing checked that "
            "its mating face points off the board - add a family row (and a "
            "`mouth` entry if the pads don't show it), declare it in "
            "constraints placement.mates, or waive kind mating_edge_unjudged "
            "for this ref with the drawing you checked it against",
            "check_mating", kind="mating_edge_unjudged",
            footprint=e.get("footprint"), edge_gap_mm=e.get("edge_gap_mm")))
    return out


def merge(board, results: list[dict], na: dict | None = None,
          strict: bool = False) -> dict:
    all_v: list[dict] = []
    checks: dict[str, dict] = {}
    by_check: dict[str, int] = {}
    errored = False
    # #911: check_mating's unjudged edge connectors are its own errors at
    # release, so they count against it in coverage too
    results = [{**r, "violations": r["violations"] + edge,
                "status": "violations" if r["status"] == "pass" else
                r["status"]} if (edge := edge_unjudged(r, strict)) else r
               for r in results]
    for r in results:
        for v in r["violations"]:
            all_v.append(v)
        by_check[r["name"]] = len(r["violations"])
        if (hole := connector_unjudged(r, strict)) is not None:
            all_v.append(hole)
            by_check["verify_all"] = by_check.get("verify_all", 0) + 1
        if r["status"] in ("error", "skipped_error"):
            errored = True  # skipped_error only exists under strict
        entry = {"status": r["status"], "counts": r["counts"],
                 "report": r["report"]}
        if r.get("nothing_to_judge"):
            entry["reason"] = r["nothing_to_judge"]
            if r["status"] == "pass":
                entry["status"] = "nothing_to_judge"
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
    ap.add_argument("--netlist", help="kicadsexpr netlist for the netlist "
                    "checks (default: <stem>.net beside the board, else one "
                    "exported from <stem>.kicad_sch into the reports dir)")
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
    netlist, netlist_note = board_netlist(pcb, reports_dir, args.netlist)
    inputs["netlist"] = str(netlist) if netlist else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) \
            as ex:
        results = list(ex.map(
            lambda c: run_one(c, inputs, reports_dir, na, args.strict),
            CHECKS))
    # keep CHECKS order (ThreadPoolExecutor.map preserves input order)
    summary = merge(pcb, results, na, args.strict)
    checklib.stamp(summary, pcb)
    if netlist_note:
        summary["netlist"] = netlist_note
    drift = constraints_drift(args.constraints)
    if drift is not None:
        summary["violations"].append(drift)
        counts = checklib.summarize(summary["violations"])
        prior = summary["counts"]["by_check"]
        counts["by_check"] = {**prior,
                              "verify_all": prior.get("verify_all", 0) + 1}
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
