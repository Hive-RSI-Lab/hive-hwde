"""bench - run ONE pipeline stage in isolation on a frozen fixture (T5).

The per-stage tuning loop's judge (v2 plan appendix): edit a stage's
script/prompt/template, re-run `bench.py --stage PN --fixture F`, keep the
change iff the composite improves.  Fixtures are frozen under
tests/fixtures/stages/ (manifest.yaml, every file sha256-pinned); a score
computed from a drifted fixture is refused (exit 2).

Stages (registry below): P1 research (U15: a frozen research root - task
ledger + records + quarantined sources - scored for research quality by
researchlib.assess; the owner-graded extraction fixtures accumulate from
teaching sessions), P2 architecture, P3 library sanitise (fpfix vs real
DRC), P4 schematic, P5 board_init + rules_gen, P6 place, P7 route, P8
verify, P9 dfm, P10 order-dryrun; E2E end to end (backlog item 1: a
finished board workspace, --artifact WS, scored against a held-out brief's
hidden bounds - electrical incl. SPICE at tolerance corners, layout, BOM
cost; reference/e2e-scoring.md is its contract).

score.json metric classes (the determinism contract, LEARNINGS 2026-08-06
[tests][freerouting]):
  metrics       offline-deterministic (pure venv).  Declared noise: ZERO -
                two runs on one fixture must match exactly (everything is
                rounded via checklib.rnd conventions upstream).
  metrics_live  deterministic GIVEN the pinned toolchain (kicad-cli 10.0.3
                ERC/DRC/netlist-export legs).  Exact-compare too, but only
                present when kicad-cli resolves; tests needing them are
                smoke-marked.
  informational wall_s, tokens, cost_usd, renders - NEVER compared, never
                in the composite.  Wall-clock and completion ratios of LIVE
                routing runs belong here, not in metrics (bench never runs
                Freerouting; it scores frozen routed boards).
  composite     100 - weighted penalties (benchlib.WEIGHTS), computed from
                metrics (+ metrics_live when available; composite_inputs
                records which).  Comparable ONLY within one
                (stage, fixture): raw metrics are not cross-board
                normalised.

Baselines: tests/fixtures/stages/baselines/<fixture>.score.json, written by
--baseline (refused with --artifact/--file overrides), compared by
--compare.  A compare fails (exit 1) iff composite < baseline composite;
metric drift at equal-or-better composite is reported, not failed.
--compare refuses (exit 2) when composite_inputs differ between the two.

Token/cost: for agent-driven stages the DRIVER session knows the spend, the
script cannot - pass --tokens/--cost-usd and bench records them
informationally.

Fixture capture (U7 learning mode): --freeze records a NEW graded fixture -
copies every --from/--from-dir source under the manifest dir, LF-normalizes
text bytes before hashing (a sha pinned on CRLF working bytes drift-refuses
on every fresh LF checkout; LEARNINGS 2026-08-06 [bench][git][windows]),
auto-includes .kicad_pro/.kicad_dru stem-siblings of any .kicad_pcb/
.kicad_sch source (a KiCad artifact fixture is the file PLUS its project
files; LEARNINGS 2026-08-06 [bench][kicad-cli]), and APPENDS the manifest
entry (comments preserved).  The owner's verdict rides provenance
(--grade/--note).  Scoring and --baseline stay separate invocations.
known_answer blocks are hand-authored manifest YAML (P8/P9 only), never
frozen.

CLI: bench.py --list
     bench.py --stage P6 --fixture pd_trigger_place [--artifact PATH]
              [--file name=PATH ...] [--baseline] [--compare [PATH]]
              [--work-dir DIR] [--render] [--tokens N] [--cost-usd X]
              [--out score.json]
     bench.py --stage E2E --fixture e2e_<brief> [--artifact <workspace>]
     bench.py --freeze --stage P6 --fixture <new_id> --board <b>
              --from name=PATH [--from-dir name=PATH] [--grade TEXT]
              [--note TEXT] [--freeze-args JSON]
     bench.py --freeze --stage P1 --fixture <id> --board <b>
              --from task=<ws>/research/tasks/<t>.json
              --from-dir research=<ws>/research --freeze-args '{"task": "<t>"}'
              --grade "<owner verdict on the extraction>"
     bench.py --corpus WORK [--boards A,B] [--rerun A,B] [--boards-root DIR]
     bench.py --scorecard WS [WS ...] | --scorecard all [--boards-root DIR]
              [--record] [--top N]
     bench.py --scorecard-report MD
     bench.py --scorecard-corpus [ID ...] [--cache DIR] [--record] [--top N]

--corpus is the one mode that DOES run the live pipeline: place_seed,
place_anneal and route_auto (Freerouting) on a copy of every board in the
boards repo, one at a time, each writing WORK/<board>/result.json as it
finishes; a board with a result.json is skipped unless --rerun names it
(lib/benchcorpus.py has the steps; docs/placement-benchmark.md is the
report).  Its completion and runtime numbers are informational, never a
composite.

--scorecard scores finished board workspaces with no brief (docs/design-
evals.md is the contract, lib/evalcard.py the code): it re-runs every
offline verify check (verify_all.CHECKS) on the board, reads its recorded
ERC/DRC/DFM gate verdicts (state.json, reports/gate-*.json), and returns
per board five area scores (schematic, layout, signal_integrity, power,
manufacturing), a composite and the findings ranked most-likely-real first, each with a `fix` line;
plus a suite score with 95 % cluster-bootstrap intervals.  `all` means
every board at phase P9 or later in the boards repo.  Checks run 2 at a
time.  --record appends one line per board (scores, counts and the top
--top findings, not every finding) and one for the suite to
results/scorecard.jsonl (HWDE_RESULTS_ROOT overrides the root).
--scorecard-report MD rewrites the markdown between the SCORECARD markers in
MD (docs/design-evals.md) from the last recorded suite in that store.

--scorecard-corpus scores the human-made board corpus (human_corpus.py,
docs/human-corpus.md) the same way, but runs nothing: it reads the
verify_all and dfm_check results `human_corpus.py run` left in
CACHE/runs/<id>/result.json (CACHE as human_corpus.py: --cache, else
HWDE_HUMAN_CORPUS_CACHE, else ~/.cache/hwde-human-corpus) for every
manifest board, or the ids given. These are bare KiCad boards with no
state.json, so the ERC/DRC gate column is left out rather than scored as
missing; dfm_check's fresh findings count under check `dfm_check`. A board
with no result.json, or whose verify_all wrote no summary, is listed under
`unscored` and left out of the suite. The suite's bootstrap clusters boards
by GitHub repo, so revisions of one design count once. --record appends to
results/corpus-scorecard.jsonl, a store of its own.

Exit 0 scored (no regression), 1 known-answer miss or composite regression,
2 error/drifted fixture/missing toolchain for a live-only stage.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
for p in (SCRIPTS, SCRIPTS / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import benchcorpus  # noqa: E402
import benchlib  # noqa: E402
import checklib  # noqa: E402
import evalcard  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "bench"

# stage -> {title, primary artifact key (--artifact target), live legs}
STAGES = {
    "P1": {"title": "research (record extraction vs graded fixture)", "primary": "research", "live": "none"},
    "P2": {"title": "architecture (constraints vs netlist)", "primary": "constraints", "live": "none"},
    "P3": {"title": "library sanitise (fpfix vs real DRC)", "primary": "fp_lib", "live": "required"},
    "P4": {"title": "schematic", "primary": "sch", "live": "optional"},
    "P5": {"title": "board_init", "primary": "netlist", "live": "required"},
    "P6": {"title": "place", "primary": "pcb", "live": "none"},
    "P7": {"title": "route (frozen board)", "primary": "pcb", "live": "optional"},
    "P8": {"title": "verify", "primary": "pcb", "live": "none"},
    "P9": {"title": "dfm", "primary": "pcb", "live": "none"},
    "P10": {"title": "order-dryrun", "primary": "pcb", "live": "none"},
    "E2E": {"title": "end to end (finished workspace vs a brief's hidden bounds)", "primary": "workspace", "live": "optional"},
}


def _stage_key(s: str):
    """P1..P10 in number order, then the E2E stage."""
    return (0, int(s[1:])) if s[0] == "P" and s[1:].isdigit() else (1, 0)


def _sev(violations: list[dict]) -> tuple[int, int]:
    e = sum(1 for v in violations if v.get("severity") == "error")
    w = sum(1 for v in violations if v.get("severity") == "warning")
    return e, w


def _kinds(violations: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in violations:
        k = v.get("kind") or v.get("check") or "?"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def _find_cli():
    import env
    try:
        return env.find_kicad_cli()
    except Exception:
        return None


def _cli_version(cli) -> str | None:
    try:
        r = subprocess.run([str(cli), "version"], capture_output=True,
                           text=True, timeout=30)
        return (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 \
            else None
    except OSError:
        return None


def _require_pinned_siblings(ctx, artifact: Path):
    """A live ERC/DRC leg reads <stem>.kicad_pro/.kicad_dru SIBLINGS of the
    artifact (LEARNINGS 2026-08-06 [bench][kicad-cli]).  If such a sibling
    exists but is not sha-pinned in the manifest, the freeze is a lie -
    refuse rather than score against unpinned config.  Skipped for
    overridden artifacts: a tuning-loop candidate brings its own copies of
    the fixture's project files (see --artifact help)."""
    primary = None
    for name, path in ctx["files"].items():
        if Path(path) == Path(artifact):
            primary = name
    if primary in ctx["overridden"]:
        return
    pinned = {(benchlib.repo_root() / rec["path"]).resolve()
              for rec in (ctx["entry"].get("files") or {}).values()}
    for ext in (".kicad_pro", ".kicad_dru"):
        sib = Path(artifact).with_suffix(ext)
        if sib.exists() and sib.resolve() not in pinned:
            raise CheckError(
                f"unpinned sibling project file feeds this live leg: {sib} - "
                "pin it in the stage-fixture manifest (it carries severities/"
                "netclasses/custom rules that change the score)")


def _render_pcb(cli, pcb: Path, work: Path, renders: list):
    import kc
    out = work / f"{pcb.stem}_top.png"
    r = kc.render_png(cli, pcb, out, side="top", width=1600, height=900)
    if r.get("status") == "pass":
        renders.append(str(out))


# ------------------------------------------------------------------ scorers
# Each returns (metrics, metrics_live, penalties, ka_violations)
# ka_violations: findings list the fixture's known_answer (if any) is matched
# against.  metrics_live is None when the leg did not run.


def score_p1(ctx):
    """Research quality (U15): the fixture's `research` dir is a frozen
    research root (tasks/, records/, sources/, checklists/) and args.task
    names the task; researchlib.assess supplies metrics + penalty terms.
    A tuning-loop candidate is a whole research dir (--artifact <ws>/
    research) produced by a fresh researcher run on the same task."""
    import researchlib
    root = ctx["files"]["research"]
    task_id = (ctx["args"] or {}).get("task")
    if not task_id:
        raise CheckError("P1 fixture needs args.task (the research task id "
                         "under research/tasks/)")
    res = researchlib.assess(Path(root), task_id)
    return res["metrics"], None, res["penalties"], []


def score_p2(ctx):
    import netlist_audit
    cons_p, net_p = ctx["files"]["constraints"], ctx["files"]["netlist"]
    payload, _ = netlist_audit.run(["--netlist", str(net_p),
                                    "--constraints", str(cons_p)])
    errors, warnings = _sev(payload["violations"])
    # missing_ref already scores through placement_refs_missing (weight 10);
    # do not double-count the same defect in audit_errors (weight 15)
    errors -= sum(1 for x in payload["violations"]
                  if x.get("severity") == "error"
                  and x.get("kind") == "missing_ref")
    parsed = netlist_audit.parse_netlist(net_p)
    cons = json.loads(cons_p.read_text(encoding="utf-8"))
    missing = benchlib.placement_refs_missing(cons, parsed["components"])

    stackup = (ctx["args"] or {}).get("stackup")
    stackup_ok = None
    if stackup:
        import yaml
        table = yaml.safe_load(
            (SCRIPTS.parent / "reference" / "stackups.yaml")
            .read_text(encoding="utf-8"))
        rec = (table.get("stackups") or {}).get(stackup)
        stackup_ok = bool(rec) and rec.get("available", True) is not False

    metrics = {"nets": payload.get("nets"),
               "components": payload.get("components"),
               "audit_errors": errors, "audit_warnings": warnings,
               "by_kind": _kinds(payload["violations"]),
               "placement_refs_missing": missing,
               "stackup": stackup, "stackup_ok": stackup_ok}
    penalties = {"audit_errors": errors, "audit_warnings": warnings,
                 "placement_refs_missing": len(missing),
                 "stackup_bad": 0 if stackup_ok in (True, None) else 1}
    return metrics, None, penalties, payload["violations"]


def score_p3(ctx):
    """Library sanitise vs REAL DRC (T6 P3-BENCH): copy the frozen pristine
    pull set to a scratch dir, measure it with fpfix.scratch_drc (the only
    honest silk oracle - geometry checkers and KiCad DRC disagree), run the
    pull-time repair pipeline (fpfix.fix_lib + refdes norm), measure again.
    The fixture's args pin the expected BEFORE profile: drift means either a
    fixture edit (refused upstream by sha) or an fpfix behavior change that
    must re-record its baseline in the same commit."""
    import fpfix
    import lib_pull
    src = Path(ctx["files"]["fp_lib"])
    work_lib = ctx["work"] / "bench.pretty"
    if work_lib.exists():
        shutil.rmtree(work_lib)
    shutil.copytree(src, work_lib)

    before = fpfix.scratch_drc(work_lib)
    fix = fpfix.fix_lib(work_lib)
    norm = lib_pull._refdes_norm(work_lib, dry_run=False)
    after = fpfix.scratch_drc(work_lib)

    expect = (ctx["args"] or {}).get("expect_before_by_check") or {}
    drift = sum(abs(int(before["by_check"].get(k, 0)) - int(expect.get(k, 0)))
                for k in set(before["by_check"]) | set(expect)) if expect else 0
    missing = sorted(set(before.get("missing") or [])
                     | set(after.get("missing") or []))

    metrics = {"footprints": fix["footprints"],
               "fix_changed": fix["changed"], "fix_actions": fix["actions"],
               "fix_residue": fix["residue"],
               "refdes_changed": norm["changed"],
               "refdes_skipped": norm["skipped"]}
    live = {"before_total": before["counts"]["total"],
            "before_by_check": dict(sorted(before["by_check"].items())),
            "after_total": after["counts"]["total"],
            "after_by_check": dict(sorted(after["by_check"].items())),
            "missing_footprints": missing,
            "kicad_version": _cli_version(ctx["cli"])}
    penalties = {"after_violations": live["after_total"],
                 "residue": fix["residue"],
                 "missing_footprints": len(missing),
                 "before_drift": drift}
    return metrics, live, penalties, []


def score_p4(ctx):
    sch = ctx["files"]["sch"]
    # multi-sheet plumbing (T6 P4-3): score every pinned sheet - root 'sch'
    # plus any 'sch_*' children (or an explicit args.sheets order).  The live
    # ERC/netlist legs stay root-only: kicad-cli walks the hierarchy itself,
    # and the pinned children sit in the same fixture dir so child refs
    # resolve.
    names = (ctx["args"] or {}).get("sheets") or \
        (["sch"] + sorted(n for n in ctx["files"] if n.startswith("sch_")))
    metrics = benchlib.sch_metrics([ctx["files"][n] for n in names])
    penalties = {
        "wire_crossings": metrics["wire_crossings"],
        "label_collisions": metrics["label_collisions"],
        "refdes_overlaps": metrics["refdes_overlaps"],
        "sheet_balance_excess": max(0.0, metrics["sheet_balance"] - 2.0),
    }
    live = None
    if ctx["cli"]:
        import kc
        import netlist_audit
        _require_pinned_siblings(ctx, sch)
        erc = kc.run_erc(ctx["cli"], sch)
        live = {"erc_errors": erc["counts"]["by_severity"].get("error", 0),
                "erc_warnings": erc["counts"]["by_severity"].get("warning", 0),
                "kicad_version": erc.get("kicad_version")}
        golden = ctx["files"].get("golden_net")
        if golden:
            exported = ctx["work"] / "exported.net"
            r = kc.export_netlist(ctx["cli"], sch, exported)
            if r.get("status") != "pass":
                raise CheckError(f"netlist export failed: {r.get('stderr_tail')}")
            cmp_payload, _ = netlist_audit.run(["--netlist", str(exported),
                                                "--compare", str(golden)])
            live["netlist_identical"] = bool(cmp_payload.get("identical"))
            live["netlist_diffs"] = cmp_payload["counts"]["total"]
        penalties["erc_errors"] = live["erc_errors"]
        penalties["erc_warnings"] = live["erc_warnings"]
        penalties["netlist_diffs"] = live.get("netlist_diffs", 0)
        if ctx["render"]:
            out = ctx["work"] / f"{sch.stem}.pdf"
            r = kc.export_sch_pdf(ctx["cli"], sch, out)
            if r.get("status") == "pass":
                ctx["renders"].append(str(out))
    return metrics, live, penalties, []


def score_p5(ctx):
    if not ctx["cli"]:
        raise CheckError("P5 board_init needs live kicad-cli + bundled python "
                         "(env.py); no offline leg exists for this stage")
    args = ctx["args"] or {}
    out_dir = ctx["work"] / "board_init"
    report = ctx["work"] / "board_init_report.json"
    cmd = [sys.executable, str(SCRIPTS / "board_init.py"),
           "--netlist", str(ctx["files"]["netlist"]),
           "--name", args.get("name", "bench"),
           "--out", str(out_dir),
           "--layers", str(args.get("layers", 2)),
           "--out-report", str(report)]
    if args.get("stackup"):
        cmd += ["--stackup", args["stackup"]]
    if "fp_lib" in ctx["files"]:
        cmd += ["--fp-lib", str(ctx["files"]["fp_lib"])]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if not report.is_file():
        raise CheckError(f"board_init emitted no report (rc {r.returncode}): "
                         f"{(r.stdout or r.stderr)[-400:]}")
    rep = json.loads(report.read_text(encoding="utf-8"))
    if rep.get("status") == "error":
        raise CheckError(f"board_init error: {rep.get('error', '?')[:400]}")
    sc = rep.get("self_check") or {}
    live = {"kicad_version": _cli_version(ctx["cli"]),
            "components": rep.get("components"), "nets": rep.get("nets"),
            "fab_profile": rep.get("fab_profile"),
            "setup_violations": len(sc.get("setup_violations") or []),
            "transient_silk": len(sc.get("transient_silk") or []),
            "parity_count": sc.get("parity_count"),
            "clean": bool(sc.get("clean"))}
    penalties = {"setup_violations": live["setup_violations"],
                 "not_clean": 0 if live["clean"] else 1,
                 "transient_silk": live["transient_silk"]}

    # rules_gen leg (T6 P5-2): the T1 surface - per-current netclass split,
    # IPC-2152 widths, capability class, ERROR-severity fab floors written
    # into the .kicad_pro - becomes baseline-tracked.  Pure computation
    # (zero declared noise); fabfloors.check_pro re-asserts the written pro
    # INDEPENDENTLY of rules_gen's own internal assertion.
    metrics = {}
    if "constraints" in ctx["files"]:
        import fabfloors
        rg_out = ctx["work"] / "rules_gen_report.json"
        pro = next(out_dir.glob("*.kicad_pro"), None)
        if pro is None:
            raise CheckError("board_init left no .kicad_pro for the "
                             "rules_gen leg")
        cmd = [sys.executable, str(SCRIPTS / "rules_gen.py"),
               "--constraints", str(ctx["files"]["constraints"]),
               "--layers", str(args.get("layers", 2)),
               "--out-dru", str(ctx["work"] / "bench.kicad_dru"),
               "--pro", str(pro), "--out", str(rg_out)]
        if args.get("stackup"):
            cmd += ["--stackup", args["stackup"]]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if not rg_out.is_file():
            raise CheckError(f"rules_gen emitted no report (rc {r.returncode}): "
                             f"{(r.stdout or r.stderr)[-400:]}")
        rg = json.loads(rg_out.read_text(encoding="utf-8"))
        if rg.get("status") != "pass":
            raise CheckError(f"rules_gen error: {str(rg.get('error', '?'))[:400]}")
        cls, cap = fabfloors.profile(int(args.get("layers", 2)),
                                     float(rg["copper_oz"]))
        pro_floor_failures = len(fabfloors.check_pro(
            json.loads(pro.read_text(encoding="utf-8")), cap))
        metrics = {"capability_class": rg["capability_class"],
                   "netclass_split": sorted(rg["classes"]),
                   "power_widths_mm": {p["net"]: p.get("class_width_mm")
                                       for p in rg.get("power") or []},
                   "rule_count": rg["rule_count"],
                   "pro_floor_failures": pro_floor_failures}
        penalties["pro_floor_failures"] = pro_floor_failures

    if ctx["render"]:
        pcb = next(out_dir.glob("*.kicad_pcb"), None)
        if pcb:
            _render_pcb(ctx["cli"], pcb, ctx["work"], ctx["renders"])
    return metrics, live, penalties, []


def score_p6(ctx):
    import place_metrics
    argv = ["--pcb", str(ctx["files"]["pcb"])]
    if "constraints" in ctx["files"]:
        argv += ["--constraints", str(ctx["files"]["constraints"])]
    if "decoupling" in ctx["files"]:
        argv += ["--decoupling", str(ctx["files"]["decoupling"])]
    payload, _ = place_metrics.run(argv)
    m = payload["metrics"]
    # decap gradient (T6 P6A-1): caps the sidecar EXPLICITLY classes "bulk"
    # have no distance requirement (bulk-at-entry is a design position, e.g.
    # pd-trigger's VBUS caps at the connector).  The exemption requires the
    # explicit class - check_decoupling derives "bulk" from value alone for
    # unclassed 1 uF+ caps and those must still count (C5 caveat).
    bulk: set = set()
    if "decoupling" in ctx["files"]:
        dec = json.loads(Path(ctx["files"]["decoupling"])
                         .read_text(encoding="utf-8"))
        bulk = {a.get("cap") for a in dec.get("associations", [])
                if a.get("class") == "bulk"}
    dists = [(f.get("manhattan_mm") or 0) for f in m.get("decoupling") or []
             if f.get("cap") not in bulk]
    decap_worst = max(dists) if dists else 0.0
    metrics = {"footprints": m["counts"]["footprints"],
               "nets": m["counts"]["nets"],
               "hpwl_total_mm": m["hpwl"]["total_mm"],
               "crossings": m["crossings"]["count"],
               "crossings_signal": m["crossings_signal"]["count"],
               "congestion_max": m["congestion"]["max"],
               "congestion_signal_max": m["congestion_signal_max"],
               "congestion_mean_nonzero": m["congestion"]["mean_nonzero"],
               "violations_total": payload["counts"]["total"],
               "by_kind": _kinds(payload["violations"]),
               "decap_worst_mm": decap_worst,
               "utilization": m["utilization"]["ratio"]}
    # penalty KEY names unchanged (benchlib.WEIGHTS untouched); the crossing/
    # congestion VALUES are the signal variants - gnd-class flight lines ride
    # planes and the annealer is right to ignore them, so scoring them gave
    # the tuning loop a gradient no placement change should chase (T6 P6A-1)
    penalties = {"hpwl_total_mm": metrics["hpwl_total_mm"],
                 "crossings": metrics["crossings_signal"],
                 "congestion_max": metrics["congestion_signal_max"],
                 "legality_violations": metrics["violations_total"],
                 "decap_worst_mm": decap_worst}
    if ctx["render"] and ctx["cli"]:
        _render_pcb(ctx["cli"], ctx["files"]["pcb"], ctx["work"], ctx["renders"])
    return metrics, None, penalties, payload["violations"]


def score_p7(ctx):
    import geom
    pcb = ctx["files"]["pcb"]
    bg = geom.BoardGeom.from_file(pcb)
    track_mm = round(sum(t.length for t in bg.tracks_of()), 2)
    metrics = {"track_mm": track_mm,
               "track_count": len(bg.tracks_of()),
               "via_count": len(bg.vias_of())}
    penalties = {"track_mm": track_mm, "via_count": metrics["via_count"],
                 "incompletion_pct": 0.0, "drc_errors": 0, "drc_warnings": 0}
    live = None
    ka: list[dict] = []
    if ctx["cli"]:
        import kc
        import route_auto
        _require_pinned_siblings(ctx, pcb)
        drc = kc.run_drc(ctx["cli"], pcb, parity=False, all_track_errors=True)
        routable = route_auto._routable_nets(bg)
        unrouted = route_auto._drc_unrouted(drc)
        completion = 1.0 if not routable else \
            round(1 - len(unrouted) / len(routable), 4)
        live = {"drc_errors": drc["counts"]["by_severity"].get("error", 0),
                "drc_warnings": drc["counts"]["by_severity"].get("warning", 0),
                "completion": completion,
                "routable_nets": len(routable),
                "unrouted_nets": sorted(unrouted),
                "kicad_version": drc.get("kicad_version")}
        penalties["incompletion_pct"] = round((1 - completion) * 100, 4)
        penalties["drc_errors"] = live["drc_errors"]
        penalties["drc_warnings"] = live["drc_warnings"]
        ka = drc["violations"]
        if ctx["render"]:
            _render_pcb(ctx["cli"], pcb, ctx["work"], ctx["renders"])
    return metrics, live, penalties, ka


def _si_scalars(reports_dir: Path) -> dict:
    """Derived-SI scalars from the per-check reports (defensive: absent
    checks/facts yield nulls, never a crash)."""
    out = {"decap_worst_loop_nh": None, "decap_worst_manhattan_mm": None,
           "diffpair_worst_skew_ps": None, "diffpair_worst_uncoupled_mm": None,
           "return_corridor_voids": None}

    def _load(name):
        p = reports_dir / f"{name}.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return None

    dec = _load("check_decoupling")
    if dec and dec.get("checked"):
        loops = [f.get("loop_nh") for f in dec["checked"] if f.get("loop_nh") is not None]
        dists = [f.get("manhattan_mm") for f in dec["checked"] if f.get("manhattan_mm") is not None]
        out["decap_worst_loop_nh"] = max(loops) if loops else None
        out["decap_worst_manhattan_mm"] = max(dists) if dists else None
    dp = _load("check_diffpair")
    if dp and dp.get("checked"):
        skews = [f.get("skew_ps") for f in dp["checked"] if f.get("skew_ps") is not None]
        unc = [max(f.get("uncoupled_p_mm") or 0, f.get("uncoupled_n_mm") or 0)
               for f in dp["checked"]]
        out["diffpair_worst_skew_ps"] = max(skews) if skews else None
        out["diffpair_worst_uncoupled_mm"] = max(unc) if unc else None
    rp = _load("check_return_path")
    if rp is not None:
        out["return_corridor_voids"] = sum(
            1 for v in rp.get("violations") or [] if v.get("kind") == "corridor_void")
    return out


def score_p8(ctx):
    import verify_all
    reports = ctx["work"] / "checks"
    argv = ["--pcb", str(ctx["files"]["pcb"]),
            "--reports-dir", str(reports),
            "--out", str(ctx["work"] / "verify_summary.json")]
    if "constraints" in ctx["files"]:
        argv += ["--constraints", str(ctx["files"]["constraints"])]
    if "decoupling" in ctx["files"]:
        argv += ["--decoupling", str(ctx["files"]["decoupling"])]
    payload, _ = verify_all.run(argv)
    bad = [n for n, c in payload["checks"].items() if c["status"] == "error"]
    if bad:
        raise CheckError(f"verify sub-checks errored: {bad}")
    errors, warnings = _sev(payload["violations"])
    metrics = {"errors": errors, "warnings": warnings,
               "by_check": payload["counts"].get("by_check", {}),
               "by_kind": _kinds(payload["violations"]),
               "si": _si_scalars(reports)}
    penalties = {"errors": errors, "warnings": warnings,
                 "known_answer_missed": 0, "forbidden_errors": 0}
    if ctx["render"] and ctx["cli"]:
        _render_pcb(ctx["cli"], ctx["files"]["pcb"], ctx["work"], ctx["renders"])
    return metrics, None, penalties, payload["violations"]


def score_p9(ctx):
    import dfm_check
    args = ctx["args"] or {}
    payload = dfm_check.run(Path(ctx["files"]["pcb"]),
                            fab_dir=Path(ctx["files"]["gerbers"]),
                            copper_oz=float(args.get("copper_oz", 1.0)),
                            netlist=Path(ctx["files"]["netlist"]))
    errors, warnings = _sev(payload["violations"])
    metrics = {"errors": errors, "warnings": warnings,
               "by_kind": _kinds(payload["violations"]),
               "polarity": (payload.get("polarity") or {}).get("status"),
               "capability_key": payload.get("capability_key")}
    penalties = {"errors": errors, "warnings": warnings,
                 "known_answer_missed": 0, "forbidden_errors": 0}
    if ctx["render"] and ctx["cli"]:
        _render_pcb(ctx["cli"], ctx["files"]["pcb"], ctx["work"], ctx["renders"])
    return metrics, None, penalties, payload["violations"]


def score_p10(ctx):
    import fabhash
    import order_quote
    import order_submit
    args = ctx["args"] or {}
    work_fab = ctx["work"] / "fab"
    if work_fab.exists():
        shutil.rmtree(work_fab)
    shutil.copytree(ctx["files"]["fab"], work_fab)

    quote_out = ctx["work"] / "quote.json"
    qtys = [int(q) for q in str(args.get("qty_matrix", "5,10,30")).split(",")]
    q_payload = order_quote.run(Path(ctx["files"]["pcb"]), qtys,
                                ["HASL"], ["green"])
    quote_out.write_text(json.dumps(q_payload, indent=1), encoding="utf-8")
    s_payload = order_submit.run(Path(ctx["files"]["pcb"]), work_fab,
                                 quote=quote_out,
                                 qty=int(args.get("qty", 5)))

    zips = sorted(work_fab.glob("*_gerbers.zip"))
    dh = dh2 = None
    if zips:
        dh = fabhash.design_hash(zips[0])
        dh2 = fabhash.design_hash(zips[0])
    cheapest = (q_payload.get("cheapest") or {})
    metrics = {"submit_status": s_payload.get("status"),
               "missing": len(s_payload.get("missing") or []),
               "quote_cheapest_total": cheapest.get("total"),
               "quote_cheapest_qty": cheapest.get("qty"),
               "spec": q_payload.get("spec"),
               "design_sha256": dh, "hash_stable": dh == dh2 and dh is not None}
    penalties = {"not_ready": 0 if s_payload.get("status") == "ready_for_human" else 1,
                 "missing_items": metrics["missing"]}
    return metrics, None, penalties, []


def score_e2e(ctx):
    """E2E (backlog item 1, reference/e2e-scoring.md): a finished board
    workspace against a held-out brief's hidden bounds.  The workspace is
    --artifact; a calibration fixture (args.calibration_board) falls back to
    that shipped board in the boards repo, and a fixture with neither scores
    what an absent board earns."""
    import e2elib
    import env
    import geom
    bounds = e2elib.load_bounds(ctx["files"]["bounds"])
    ws = ctx["files"].get("workspace")
    cal = (ctx["args"] or {}).get("calibration_board")
    if ws is None and cal:
        ws = env.boards_root() / cal
        if not ws.is_dir():
            raise CheckError(f"calibration board {cal} not under "
                             f"{env.boards_root()} (clone the boards repo "
                             "or pass --artifact)")
    art = e2elib.resolve_workspace(ws) if ws else \
        dict.fromkeys(("netlist", "pcb", "sch", "decoupling", "parts"))
    parsed = e2elib.simlib.parse_netlist(art["netlist"]) \
        if art["netlist"] else None
    bg = geom.BoardGeom.from_file(art["pcb"]) if art["pcb"] else None
    nets, netmap = e2elib.check_nets(bounds, parsed)
    cost, bom = e2elib.check_cost(bounds, art["parts"])
    offline = (nets + e2elib.check_parts(bounds, parsed)
               + e2elib.check_area(bounds, bg)
               + e2elib.check_placement(bounds, art["pcb"], art["decoupling"])
               + cost)
    metrics = {"workspace": Path(ws).name if ws else None,
               "artifacts": sorted(k for k, v in art.items() if v),
               "net_map": netmap, "bom": bom, "checks": offline}
    live = None
    if ctx["cli"]:
        import kc
        import route_auto
        # kicad-cli writes a .kicad_prl beside what it opens: the live legs
        # run on a copy so a scored workspace (or a pinned one) never moves
        for key in ("sch", "pcb"):
            if art[key]:
                live_dir = ctx["work"] / "e2e_live" / art[key].parent.name
                shutil.copytree(art[key].parent, live_dir, dirs_exist_ok=True)
                art[key] = live_dir / art[key].name
        checks = []
        if art["sch"]:
            erc = kc.run_erc(ctx["cli"], art["sch"])
            n = erc["counts"]["by_severity"].get("error", 0)
            checks.append(e2elib._check("erc:errors", "electrical",
                                        e2elib.le_score(n, 0), value=n, bound=0))
        else:
            checks.append(e2elib._check("erc:errors", "electrical", 0.0,
                                        note="no schematic in the workspace"))
        if art["pcb"]:
            drc = kc.run_drc(ctx["cli"], art["pcb"], parity=False,
                             all_track_errors=True)
            n = drc["counts"]["by_severity"].get("error", 0)
            routable = route_auto._routable_nets(bg)
            unrouted = route_auto._drc_unrouted(drc)
            done = 1.0 if not routable else 1 - len(unrouted) / len(routable)
            checks += [e2elib._check("layout:drc", "layout",
                                     e2elib.le_score(n, 0), value=n, bound=0),
                       e2elib._check("layout:completion", "layout", done,
                                     value=sorted(unrouted), bound=[])]
            if ctx["render"]:
                _render_pcb(ctx["cli"], art["pcb"], ctx["work"], ctx["renders"])
        else:
            checks += [e2elib._check(i, "layout", 0.0,
                                     note="no board in the workspace")
                       for i in ("layout:drc", "layout:completion")]
        dll = env.find_ngspice_dll()
        checks += e2elib.check_spice(bounds, parsed, netmap, ctx["work"], dll)
        live = {"checks": checks, "ngspice": dll is not None,
                "kicad_version": _cli_version(ctx["cli"])}
    cats = e2elib.category_scores(offline + (live or {}).get("checks", []))
    ctx["extra"] = {"categories": cats,
                    "eebench_equiv": round(100 * (0.65 * cats["electrical"]
                                                  + 0.35 * cats["cost"]), 2)}
    penalties = {f"{c}_shortfall": round(1 - v, 4) for c, v in cats.items()}
    return metrics, live, penalties, []


SCORERS = {"P1": score_p1,
           "P2": score_p2, "P3": score_p3, "P4": score_p4, "P5": score_p5,
           "P6": score_p6, "P7": score_p7, "P8": score_p8, "P9": score_p9,
           "P10": score_p10, "E2E": score_e2e}


# ---------------------------------------------------------------- baselines

def baseline_path(manifest_path: Path, fixture: str) -> Path:
    return manifest_path.parent / "baselines" / f"{fixture}.score.json"


def _flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k in sorted(obj):
            out.update(_flatten(obj[k], f"{prefix}{k}."))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}{i}."))
    else:
        out[prefix[:-1]] = obj
    return out


def compare_to_baseline(payload: dict, base: dict) -> dict:
    if base.get("composite_inputs") != payload.get("composite_inputs"):
        raise CheckError(
            f"composite_inputs mismatch: baseline {base.get('composite_inputs')} "
            f"vs current {payload.get('composite_inputs')} - rerun with the "
            "same toolchain availability before comparing")
    bv = (base.get("metrics_live") or {}).get("kicad_version")
    cv = (payload.get("metrics_live") or {}).get("kicad_version")
    if bv and cv and bv != cv:
        raise CheckError(
            f"kicad toolchain changed: baseline {bv} vs current {cv} - "
            "scores across toolchains are not comparable; re-record the "
            "baselines deliberately")
    diffs = []
    for section in ("metrics", "metrics_live", "penalties"):
        a = _flatten(base.get(section) or {})
        b = _flatten(payload.get(section) or {})
        for key in sorted(set(a) | set(b)):
            if key == "kicad_version":
                continue
            if a.get(key) != b.get(key):
                diffs.append({"metric": f"{section}.{key}",
                              "baseline": a.get(key), "current": b.get(key)})
    delta = round(payload["composite"] - base["composite"], 2)
    return {"composite": base["composite"], "delta": delta,
            "regressed": delta < 0, "metric_diffs": diffs}


# ----------------------------------------------------------- fixture capture

_ID_RE = r"[a-z][a-z0-9_]*"


def _freeze_rel(p: Path) -> str:
    """Manifest path for a frozen file: repo-relative posix normally; absolute
    only when the manifest itself lives outside the repo (test manifests -
    benchlib joins root/path, and pathlib keeps an absolute right side)."""
    root = benchlib.repo_root().resolve()
    rp = p.resolve()
    try:
        return rp.relative_to(root).as_posix()
    except ValueError:
        return rp.as_posix()


def _lf_bytes(data: bytes) -> bytes:
    """LF-normalize text payloads; binaries (NUL byte) pass through. The pin
    must equal what a fresh clone checks out (.gitattributes: eol=lf)."""
    return data if b"\x00" in data else data.replace(b"\r\n", b"\n")


def _parse_sources(pairs, what: str, must: str) -> dict[str, Path]:
    import re
    out: dict[str, Path] = {}
    for spec in pairs or []:
        name, _, path = spec.partition("=")
        if not name or not path:
            raise CheckError(f"{what} wants NAME=PATH, got '{spec}'")
        if not re.fullmatch(_ID_RE, name):
            raise CheckError(f"{what} name must match {_ID_RE}: {name!r}")
        if name in out:
            raise CheckError(f"duplicate {what} name {name!r}")
        p = Path(path)
        ok = p.is_file() if must == "file" else p.is_dir()
        if not ok:
            raise CheckError(f"{what} {name}: no such {must}: {path}")
        out[name] = p
    return out


def do_freeze(args, manifest_path: Path) -> dict:
    """Capture a NEW graded fixture: copy sources under the manifest dir,
    LF-normalize, sha-pin, append the manifest entry, self-verify."""
    import re
    import yaml

    if args.baseline or args.compare or args.artifact or args.file \
            or args.render or args.work_dir:
        raise CheckError("--freeze only captures; score/--baseline the new "
                         "fixture in a separate invocation")
    if not manifest_path.is_file():
        raise CheckError(f"no manifest at {manifest_path} - freeze appends to "
                         "an existing fixtures table")
    if not re.fullmatch(_ID_RE, args.fixture):
        raise CheckError(f"fixture id must match {_ID_RE}: {args.fixture!r}")
    manifest = benchlib.load_manifest(manifest_path)
    if args.fixture in (manifest.get("fixtures") or {}):
        raise CheckError(f"fixture '{args.fixture}' already exists - freeze "
                         "records NEW graded fixtures; re-grading an existing "
                         "one is a --baseline decision")
    if not args.board:
        raise CheckError("--freeze needs --board (the manifest entry names it)")
    for label, val in (("--board", args.board), ("--grade", args.grade),
                       ("--note", args.note)):
        if val and not val.isascii():
            raise CheckError(f"{label} must be ASCII")

    files = _parse_sources(args.from_files, "--from", "file")
    dirs = _parse_sources(args.from_dirs, "--from-dir", "dir")
    both = set(files) & set(dirs)
    if both:
        raise CheckError(f"names given as both --from and --from-dir: "
                         f"{sorted(both)}")
    if not files:
        raise CheckError("--freeze needs at least one --from NAME=PATH")

    # Stem-sibling auto-include (LEARNINGS 2026-08-06 [bench][kicad-cli]): a
    # KiCad artifact judged without its .kicad_pro/.kicad_dru scores under
    # KiCad DEFAULTS - netclasses, custom rules and severities all live there.
    for name, p in list(files.items()):
        if p.suffix not in (".kicad_pcb", ".kicad_sch"):
            continue
        for ext, auto in ((".kicad_pro", "pro"), (".kicad_dru", "dru")):
            sib = p.with_suffix(ext)
            if not sib.is_file():
                continue
            if any(q.resolve() == sib.resolve() for q in files.values()):
                continue
            if auto in files or auto in dirs:
                raise CheckError(
                    f"cannot auto-add sibling {sib.name}: entry name "
                    f"{auto!r} is taken by a different file - pass the "
                    "sibling explicitly")
            files[auto] = sib

    primary = STAGES[args.stage]["primary"]
    if primary not in files and primary not in dirs:
        raise CheckError(f"stage {args.stage} scores the {primary!r} entry - "
                         f"include --from {primary}=PATH (or --from-dir)")

    fixture_args = None
    if args.freeze_args:
        fixture_args = json.loads(args.freeze_args)
        if not isinstance(fixture_args, dict):
            raise CheckError("--freeze-args must be a JSON object")

    dest = manifest_path.parent / args.fixture
    if dest.exists() and any(dest.iterdir()):
        raise CheckError(f"destination {dest} exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)

    # Copies keep the SOURCE basename: kicad-cli resolves project files as
    # siblings BY STEM, so renaming the artifact orphans them.
    by_base: dict[str, str] = {}
    for name, p in files.items():
        if p.name in by_base:
            raise CheckError(
                f"--from {name} and {by_base[p.name]} share basename "
                f"{p.name!r} - freeze keeps original stems; rename a source")
        by_base[p.name] = name

    entry: dict = {"stage": args.stage, "board": args.board}
    prov = {"source": "; ".join(str(p) for p in
                                [*files.values(), *dirs.values()]),
            "method": "copy", "captured": time.strftime("%Y-%m-%d")}
    if args.grade:
        prov["grade"] = args.grade
    if args.note:
        prov["note"] = args.note
    entry["provenance"] = prov
    entry["files"] = {}
    for name, p in files.items():
        dst = dest / p.name
        dst.write_bytes(_lf_bytes(p.read_bytes()))
        entry["files"][name] = {"path": _freeze_rel(dst),
                                "sha256": benchlib.file_sha256(dst)}
    if dirs:
        entry["dirs"] = {}
        for name, p in dirs.items():
            dst = dest / name
            shutil.copytree(p, dst)
            for f in sorted(q for q in dst.rglob("*") if q.is_file()):
                f.write_bytes(_lf_bytes(f.read_bytes()))
            entry["dirs"][name] = {"path": _freeze_rel(dst),
                                   "sha256": benchlib.dir_sha256(dst)}
    if fixture_args:
        entry["args"] = fixture_args

    # Append (never re-emit: the manifest's header comments must survive).
    block = yaml.safe_dump({args.fixture: entry}, sort_keys=False,
                           default_flow_style=False, width=96,
                           allow_unicode=False)
    text = manifest_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    text += "".join(f"  {line}\n" if line.strip() else "\n"
                    for line in block.splitlines())
    manifest_path.write_text(text, encoding="utf-8", newline="\n")

    redo = benchlib.load_manifest(manifest_path)
    if args.fixture not in redo["fixtures"]:
        raise CheckError("manifest append did not round-trip - an inline "
                         "`fixtures: {}` mapping cannot be appended to; use "
                         "block style")
    drift = benchlib.verify_fixture(redo["fixtures"][args.fixture])
    if drift:
        raise CheckError("freeze self-check failed: " + "; ".join(drift))

    return {"script": SCRIPT, "status": "pass", "froze": args.fixture,
            "stage": args.stage, "board": args.board,
            "dir": _freeze_rel(dest), "manifest": str(manifest_path),
            "files": entry["files"], "dirs": entry.get("dirs") or {},
            "next": f"bench.py --stage {args.stage} --fixture "
                    f"{args.fixture} --baseline"}


# --------------------------------------------------------------------- main

def do_corpus(args) -> dict:
    import env
    root = Path(args.boards_root) if args.boards_root else env.boards_root()
    if not root.is_dir():
        raise CheckError(f"no boards repo at {root}")
    cli = env.find_kicad_cli()
    bundled = env.find_kicad_python(cli) if cli else None
    if bundled is None:
        raise CheckError("--corpus needs kicad-cli and its bundled python "
                         "(SWIG pcbnew) to strip copper")
    split = lambda v: [x for x in (v or "").split(",") if x]  # noqa: E731
    summary = benchcorpus.run_corpus(
        root, Path(args.corpus), boards=split(args.boards),
        rerun=split(args.rerun), scripts=SCRIPTS, venv_py=sys.executable,
        bundled_py=str(bundled))
    return {"script": SCRIPT, "status": "pass", "corpus": summary}


def _scorecard_workspaces(args) -> list[Path]:
    import env
    import score_checks
    if args.scorecard == ["all"]:
        root = Path(args.boards_root) if args.boards_root else env.boards_root()
        jobs, why = score_checks.board_jobs(root)
        if why:
            raise CheckError(why)
        return [Path(j["inputs"]["pcb"]).parents[1] for j in jobs]
    if args.boards_root:
        raise CheckError("--boards-root only goes with --scorecard all")
    out = []
    for w in args.scorecard:
        ws = Path(w)
        if not (ws / "kicad").is_dir():
            raise CheckError(f"{ws} is not a board workspace (no kicad/)")
        out.append(ws)
    return out


def scorecard_one(ws: Path, triage: list, prec: dict, modes: dict,
                  top: int, n_jobs: int = 2) -> dict:
    """Score one finished workspace: fresh offline checks + recorded gates."""
    import concurrent.futures
    import gate
    import score_checks
    import verify_all
    pcbs = sorted((ws / "kicad").glob("*.kicad_pcb"))
    if len(pcbs) != 1:
        raise CheckError(f"{ws}: expected one kicad/*.kicad_pcb, found {len(pcbs)}")
    inputs = {"pcb": str(pcbs[0]), **score_checks.sidecars(ws / "kicad")}
    if (ws / "parts").is_dir():
        inputs["parts"] = str(ws / "parts")
    waivers = score_checks.load_waivers(ws)
    calls = [(c["name"], score_checks.check_argv(c, inputs)) for c in verify_all.CHECKS]
    run_ = [(n, a) for n, a in calls if a is not None]
    with concurrent.futures.ProcessPoolExecutor(max_workers=n_jobs) as ex:
        res = list(ex.map(score_checks._run_check, [n for n, _ in run_],
                          [a for _, a in run_]))
    findings, errors = [], []
    for (name, _), r in zip(run_, res):
        if r["status"] == "error":
            errors.append({"check": name, "error": r.get("error")})
            continue
        for v in r["violations"]:
            if (v.get("source") or v.get("check")) != name:
                continue
            verdict = next((t["verdict"] for t in triage if t["board"] == ws.name
                            and score_checks.triage_matches(t, name, v)), None)
            if verdict is None:
                verdict = "waived" if any(gate.waiver_matches(w, v) for w in waivers) \
                    else "untriaged"
            findings.append(evalcard.finding(name, v, verdict, prec.get(name), modes))
    for v in evalcard.gate_findings(ws):
        findings.append(evalcard.finding(v["check"], v, "untriaged",
                                         prec.get(v["check"]), modes))
    info = {"skipped_checks": [n for n, a in calls if a is None],
            "check_errors": errors}
    return evalcard.board_card(ws.name, findings, info, top=top)


REPORT_BEGIN = "<!-- SCORECARD:BEGIN (bench.py --scorecard-report writes this) -->"
REPORT_END = "<!-- SCORECARD:END -->"


def do_scorecard_report(md: Path) -> dict:
    try:
        table = evalcard.report_md(evalcard.read_store("scorecard"))
    except ValueError as exc:
        raise CheckError(str(exc)) from None
    text = md.read_text(encoding="utf-8")
    if REPORT_BEGIN not in text or REPORT_END not in text:
        raise CheckError(f"{md} has no SCORECARD markers")
    head, rest = text.split(REPORT_BEGIN, 1)
    tail = rest.split(REPORT_END, 1)[1]
    md.write_text(f"{head}{REPORT_BEGIN}\n{table}{REPORT_END}{tail}",
                  encoding="utf-8")
    return {"script": SCRIPT, "status": "pass", "wrote": str(md)}


def do_scorecard(args) -> dict:
    import score_checks
    wss = _scorecard_workspaces(args)
    triage = score_checks.load_triage(score_checks.TRIAGE)
    prec = evalcard.precisions()
    modes = evalcard.load_failure_modes(evalcard.REPO / "docs" / "failure-modes.yaml")
    cards = [scorecard_one(ws, triage, prec, modes, args.top) for ws in wss]
    suite = evalcard.suite_score(cards)
    if args.record:
        for c in cards:
            evalcard.append("scorecard", {**evalcard.run_key(c["board"]),
                                          "kind": "board",
                                          **{k: v for k, v in c.items()
                                             if k != "findings"}})
        evalcard.append("scorecard", {**evalcard.run_key("suite:" + ",".join(
            sorted(c["board"] for c in cards))), "kind": "suite", **suite})
    for c in cards:
        del c["findings"]   # the ranked top list is the readable part
    return {"script": SCRIPT, "status": "pass", "suite": suite, "boards": cards,
            "precision_source": str(evalcard.SCORECARD_HISTORY.name)
            if prec else None}


def corpus_card(b: dict, res: dict, triage: list, prec: dict, modes: dict,
                top: int) -> dict:
    """Score one corpus board from its recorded verify_all + dfm_check run."""
    import score_checks
    summary = res.get("verify_summary") or {}
    raw = [(v.get("source") or v.get("check"), v)
           for v in summary.get("violations") or []]
    raw += [("dfm_check", v) for v in (res.get("dfm_report") or {}).get("violations") or []]
    findings = []
    for name, v in raw:
        verdict = next((t["verdict"] for t in triage if t["board"] == b["id"]
                        and score_checks.triage_matches(t, name, v)), None)
        findings.append(evalcard.finding(name, v, verdict or "untriaged",
                                         prec.get(name), modes))
    checks = summary.get("checks") or {}
    ran = {n for n, c in checks.items()
           if isinstance(c, dict) and c.get("status") in ("pass", "violations")}
    if res.get("dfm_report"):
        ran.add("dfm_check")
    info = {"gates": "excluded (bare board: ERC/DRC never ran)",
            "skipped_checks": sorted(n for n, c in checks.items()
                                     if isinstance(c, dict) and c.get("status") == "skipped"),
            "check_errors": sorted(n for n, c in checks.items()
                                   if isinstance(c, dict) and c.get("status") == "error"),
            "dfm": (res.get("dfm_report") or {}).get("status") or "no report"}
    card = evalcard.board_card(b["id"], findings, info, top=top, scored_areas={
        evalcard.CHECK_AREA.get(n, "layout") for n in ran})
    card.update({"domain": b.get("domain"), "layers": b.get("layers"),
                 "outcome": (b.get("outcome") or {}).get("label") or "",
                 "repo": b.get("url"), "commit": b.get("commit"),
                 "pcb": b.get("pcb"),
                 "by_failure_mode": evalcard._count(
                     [f for f in findings if f["failure_mode"]], "failure_mode")})
    return card


def do_scorecard_corpus(args) -> dict:
    import human_corpus
    import score_checks
    try:   # human_corpus reports a bad id or cache as SystemExit
        boards = human_corpus.select(human_corpus.load(human_corpus.MANIFEST),
                                     args.scorecard_corpus)
        cache = human_corpus.cache_root(args.cache)
    except SystemExit as exc:
        raise CheckError(str(exc)) from None
    triage = score_checks.load_triage(score_checks.TRIAGE)
    prec = evalcard.precisions()
    modes = evalcard.load_failure_modes(evalcard.REPO / "docs" / "failure-modes.yaml")
    cards, unscored = [], []
    for b in boards:
        res = human_corpus.read_json(cache / "runs" / b["id"] / "result.json")
        if not res:
            unscored.append({"board": b["id"], "why": "no result.json; run human_corpus.py run"})
        elif not res.get("verify_summary"):
            unscored.append({"board": b["id"], "why": "verify_all wrote no summary (exit "
                             f"{(res.get('verify_all') or {}).get('exit')})"})
        else:
            cards.append(corpus_card(b, res, triage, prec, modes, args.top))
    if not cards:
        raise CheckError(f"no corpus board has a recorded run under {cache}/runs")
    suite = evalcard.suite_score(cards, cluster_of=lambda c: c.get("repo") or c["board"])
    if args.record:
        for c in cards:
            evalcard.append("corpus-scorecard", {**evalcard.run_key(c["board"]),
                                                 "kind": "board",
                                                 **{k: v for k, v in c.items()
                                                    if k != "findings"}})
        evalcard.append("corpus-scorecard", {**evalcard.run_key("suite:" + ",".join(
            sorted(c["board"] for c in cards))), "kind": "suite",
            "unscored": unscored, **suite})
    for c in cards:
        del c["findings"]
    return {"script": SCRIPT, "status": "pass", "suite": suite, "boards": cards,
            "unscored": unscored, "cache": str(cache),
            "precision_source": str(evalcard.SCORECARD_HISTORY.name) if prec else None}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="list stages and their fixtures")
    ap.add_argument("--stage", choices=sorted(STAGES, key=_stage_key))
    ap.add_argument("--fixture")
    ap.add_argument("--manifest", help="override the stage-fixture manifest")
    ap.add_argument("--artifact", help="score this file as the stage's primary "
                    "artifact instead of the frozen one (tuning-loop "
                    "candidate). For live ERC/DRC legs put copies of the "
                    "fixture's .kicad_pro/.kicad_dru next to the candidate - "
                    "they carry severities/netclasses the score depends on. "
                    "On a hierarchical P4 fixture this overrides only the "
                    "ROOT sheet; child-sheet overrides (--file sch_*=...) are "
                    "judge-side and refused under --compare, so hierarchy "
                    "tuning on children scores without --compare")
    ap.add_argument("--file", action="append", default=None, metavar="NAME=PATH",
                    help="override any manifest file entry by name")
    ap.add_argument("--baseline", action="store_true",
                    help="write baselines/<fixture>.score.json")
    ap.add_argument("--compare", nargs="?", const="auto", default=None,
                    help="compare against a baseline score.json (default: the "
                    "committed one)")
    ap.add_argument("--work-dir", help="keep intermediates here (default: temp)")
    ap.add_argument("--render", action="store_true",
                    help="emit render(s) into the work dir (live kicad only)")
    ap.add_argument("--tokens", type=int)
    ap.add_argument("--cost-usd", type=float)
    ap.add_argument("--freeze", action="store_true",
                    help="capture a NEW graded fixture: copy --from/--from-dir "
                    "sources under the manifest dir, LF-normalize text, "
                    "sha-pin, append the manifest entry (then --baseline it "
                    "in a separate invocation)")
    ap.add_argument("--board", help="--freeze: board name for the entry")
    ap.add_argument("--from", dest="from_files", action="append",
                    metavar="NAME=PATH",
                    help="--freeze: a file to pin (repeatable); .kicad_pcb/"
                    ".kicad_sch bring stem-matched .kicad_pro/.kicad_dru "
                    "siblings along automatically")
    ap.add_argument("--from-dir", dest="from_dirs", action="append",
                    metavar="NAME=PATH",
                    help="--freeze: a directory to pin (repeatable)")
    ap.add_argument("--grade", help="--freeze: the owner verdict this fixture "
                    "records (ASCII; provenance.grade)")
    ap.add_argument("--note", help="--freeze: provenance note (ASCII)")
    ap.add_argument("--freeze-args",
                    help="--freeze: JSON object stored as the entry's args "
                    "(e.g. '{\"copper_oz\": 2.0}')")
    ap.add_argument("--corpus", metavar="WORK",
                    help="run the placement benchmark on copies of every "
                    "board in the boards repo, results under WORK")
    ap.add_argument("--boards", help="--corpus: only these boards (comma list)")
    ap.add_argument("--rerun", help="--corpus: re-run these boards even if "
                    "they have a result.json (comma list)")
    ap.add_argument("--boards-root", help="--corpus / --scorecard all: boards "
                    "repo (default env.boards_root())")
    ap.add_argument("--scorecard", nargs="+", metavar="WS",
                    help="score finished board workspaces (or 'all')")
    ap.add_argument("--scorecard-corpus", nargs="*", metavar="ID",
                    help="score the human board corpus from its recorded "
                    "runs (every board, or these ids)")
    ap.add_argument("--cache", help="--scorecard-corpus: human_corpus.py's "
                    "cache (default ~/.cache/hwde-human-corpus)")
    ap.add_argument("--record", action="store_true",
                    help="--scorecard / --scorecard-corpus: append the results to results/")
    ap.add_argument("--top", type=int, default=10,
                    help="--scorecard / --scorecard-corpus: findings listed per board (default 10)")
    ap.add_argument("--scorecard-report", metavar="MD",
                    help="rewrite MD's scorecard table from results/")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    if args.scorecard_report:
        return do_scorecard_report(Path(args.scorecard_report)), args.out
    if args.scorecard_corpus is not None:
        if args.scorecard:
            raise CheckError("--scorecard and --scorecard-corpus are separate modes")
        return do_scorecard_corpus(args), args.out
    if args.cache:
        raise CheckError("--cache only makes sense with --scorecard-corpus")
    if args.scorecard:
        return do_scorecard(args), args.out
    for flag, val in (("--record", args.record),):
        if val:
            raise CheckError(f"{flag} only makes sense with --scorecard")
    if args.corpus:
        return do_corpus(args), args.out
    for flag, val in (("--boards", args.boards), ("--rerun", args.rerun),
                      ("--boards-root", args.boards_root)):
        if val:
            raise CheckError(f"{flag} only makes sense with --corpus")

    manifest_path = Path(args.manifest) if args.manifest else benchlib.default_manifest()

    if args.list:
        manifest = benchlib.load_manifest(manifest_path) \
            if manifest_path.is_file() else {"fixtures": {}}
        stages = {}
        for s, spec in STAGES.items():
            fixtures = sorted(f for f, e in manifest["fixtures"].items()
                              if e.get("stage") == s)
            stages[s] = {"title": spec["title"], "primary": spec["primary"],
                         "live": spec["live"], "fixtures": fixtures}
        return {"script": SCRIPT, "status": "pass", "stages": stages}, args.out

    if not args.stage or not args.fixture:
        raise CheckError("--stage and --fixture are required (or --list)")

    if args.freeze:
        return do_freeze(args, manifest_path), args.out
    for flag, val in (("--board", args.board), ("--from", args.from_files),
                      ("--from-dir", args.from_dirs), ("--grade", args.grade),
                      ("--note", args.note),
                      ("--freeze-args", args.freeze_args)):
        if val:
            raise CheckError(f"{flag} only makes sense with --freeze")

    manifest = benchlib.load_manifest(manifest_path)
    entry = benchlib.fixture_entry(manifest, args.fixture)
    if entry.get("stage") != args.stage:
        raise CheckError(f"fixture '{args.fixture}' is a {entry.get('stage')} "
                         f"fixture, not {args.stage}")

    overrides: dict[str, Path] = {}
    if args.artifact:
        overrides[STAGES[args.stage]["primary"]] = Path(args.artifact)
    for spec in args.file or []:
        name, _, path = spec.partition("=")
        if not path:
            raise CheckError(f"--file wants NAME=PATH, got '{spec}'")
        overrides[name] = Path(path)
    if overrides and args.baseline:
        raise CheckError("--baseline records the FROZEN fixture; it cannot be "
                         "combined with --artifact/--file overrides")
    valid = set(entry.get("files") or {}) | set(entry.get("dirs") or {}) \
        | {STAGES[args.stage]["primary"]}
    unknown_names = sorted(set(overrides) - valid)
    if unknown_names:
        raise CheckError(f"--file names not in this fixture: {unknown_names} "
                         f"(have: {sorted(valid)})")
    judge_side = sorted(set(overrides) - {STAGES[args.stage]["primary"]})
    if args.compare and judge_side:
        raise CheckError("--compare only supports overriding the stage's "
                         f"primary artifact; judge-side overrides {judge_side} "
                         "would invalidate the baseline comparison")
    if args.render and not args.work_dir:
        raise CheckError("--render requires --work-dir (renders written to "
                         "the default temp dir would be deleted on exit)")

    pinned = {k: v for k, v in entry.items() if k in ("files", "dirs")}
    check_entry = {kind: {n: r for n, r in (pinned.get(kind) or {}).items()
                          if n not in overrides}
                   for kind in ("files", "dirs")}
    drift = benchlib.verify_fixture(check_entry)
    if drift:
        raise CheckError("fixture drifted - refusing to score: "
                         + "; ".join(drift))

    files = benchlib.fixture_paths(entry)
    for name, path in overrides.items():
        if not path.exists():
            raise CheckError(f"override {name}={path} does not exist")
        files[name] = path.resolve()

    if args.work_dir:
        work = Path(args.work_dir)
        work.mkdir(parents=True, exist_ok=True)
        tmp = None
    else:
        import tempfile
        tmp = tempfile.TemporaryDirectory(prefix="bench_")
        work = Path(tmp.name)

    try:
        cli = _find_cli()
        if STAGES[args.stage]["live"] == "required" and cli is None:
            raise CheckError(f"stage {args.stage} needs kicad-cli (pinned via "
                             "lib/env.py) and none resolved")
        ctx = {"entry": entry, "files": files, "args": entry.get("args"),
               "work": work, "cli": cli, "render": args.render, "renders": [],
               "overridden": set(overrides)}
        t0 = time.perf_counter()
        metrics, live, penalties, ka_violations = SCORERS[args.stage](ctx)
        wall_s = round(time.perf_counter() - t0, 2)

        known = entry.get("known_answer")
        ka = None
        if known:
            if not {"known_answer_missed", "forbidden_errors"} \
                    <= set(benchlib.WEIGHTS[args.stage]):
                raise CheckError(
                    f"known_answer is only scoreable on stages whose weight "
                    f"table has known_answer_missed/forbidden_errors (P8/P9); "
                    f"'{args.fixture}' is {args.stage}")
            ka = benchlib.match_known_answer(known, ka_violations)
            penalties["known_answer_missed"] = len(ka["missed"])
            penalties["forbidden_errors"] = ka["forbidden_errors"]

        composite = benchlib.composite(args.stage, penalties)
        composite_inputs = "full" if (live is not None or
                                      STAGES[args.stage]["live"] == "none") \
            else "offline"

        payload = {
            "script": SCRIPT, "status": "pass",
            "stage": args.stage, "fixture": args.fixture,
            "board": entry.get("board"),
            "artifact": str(files.get(STAGES[args.stage]["primary"], "")),
            "overridden": sorted(overrides),
            "metrics": metrics, "metrics_live": live,
            "penalties": {k: round(float(v), 4) for k, v in penalties.items()},
            "composite": composite, "composite_inputs": composite_inputs,
            "known_answer": ka,
            "e2e": ctx.get("extra"),
            "informational": {"wall_s": wall_s, "tokens": args.tokens,
                              "cost_usd": args.cost_usd,
                              "renders": ctx["renders"]},
        }
        if ka and ka["status"] != "ok":
            payload["status"] = "violations"

        if args.compare:
            bpath = baseline_path(manifest_path, args.fixture) \
                if args.compare == "auto" else Path(args.compare)
            if not bpath.is_file():
                raise CheckError(f"no baseline at {bpath} (run --baseline first)")
            base = json.loads(bpath.read_text(encoding="utf-8"))
            payload["baseline"] = {"path": str(bpath),
                                   **compare_to_baseline(payload, base)}
            if payload["baseline"]["regressed"]:
                payload["status"] = "violations"

        if args.baseline:
            bpath = baseline_path(manifest_path, args.fixture)
            bpath.parent.mkdir(parents=True, exist_ok=True)
            keep = {k: payload[k] for k in
                    ("script", "stage", "fixture", "board", "metrics",
                     "metrics_live", "penalties", "composite",
                     "composite_inputs", "known_answer", "e2e")}
            keep["_baseline"] = {"created": time.strftime("%Y-%m-%d"),
                                 "kicad_version":
                                     (live or {}).get("kicad_version")
                                     if isinstance(live, dict) else None}
            bpath.write_text(json.dumps(keep, indent=1, sort_keys=False,
                                        ensure_ascii=True) + "\n",
                             encoding="utf-8")
            payload["baseline_written"] = str(bpath)

        return payload, args.out
    finally:
        if tmp is not None:
            tmp.cleanup()


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
