#!/usr/bin/env python
"""route_teardrops - teardrops on vias and plated-hole pads after routing
(#480, SPEC P7).

Runs after the routing chain (route_finish / cleanup snap / route_tune),
before the drc_routed gate. KiCad 8+ teardrops are a per-item setting that
every zone refill turns into teardrop zones (lib/teardroplib.py), so this
step:

  1. reads the board (lib/teardroplib): every via, PTH pad (and SMD pad when
     the scope says so, never under a part finer than fine_pitch_mm) on a
     matching net with at least one track join that no same-net pour
     already holds;
  2. leaves items already enabled alone (`already`), so a second run adds
     nothing and leaves the file untouched (idempotent);
  3. on a staged copy: DRC refilled (the baseline), enable the rest through
     the SWIG worker (lib/teardrop_swig.py), DRC refilled again (KiCad makes
     the zones and saves);
  4. keeps the result only when no DRC type grew (refilled vs refilled) and
     every enabled item got its drops; otherwise the board is left as it
     was and the run reports `teardrop_regression` (exit 1).

On by constraints `teardrops.enabled`, or when teardrops are required (a
rule area named FLEX_*, or `teardrops.required`); otherwise a no-op that
says so (`--force` runs it anyway). dfm_check's `teardrop` family holds
the result: a ring under fab abs min + drill wander with no drop
(dfm_ring_breakout), and on a flex board any join with none
(dfm_teardrop_missing).

Contract:
  route_teardrops.py --pcb B.kicad_pcb [--constraints constraints.json]
                     [--force] [--dry-run] [--out-report r.json]
  JSON to stdout or --out-report; exit 0 pass / 1 violations / 2 error.
  facts: enabled_items {via, pth, smd}, already, drops_before, drops_after,
  drops_wanted, missing (items still short of drops), drc_before/after.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import teardroplib as tdl  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "route_teardrops"
WORKER = SCRIPTS / "lib" / "teardrop_swig.py"


def _drc_types(rep: dict) -> dict:
    out: dict[str, int] = {}
    for v in rep["violations"]:
        if v.get("severity") == "ignore":
            continue
        k = f"{v.get('source')}:{v.get('check')}"
        out[k] = out.get(k, 0) + 1
    return out


def _grown(before: dict, after: dict) -> dict:
    return {k: [before.get(k, 0), n] for k, n in sorted(after.items())
            if n > before.get(k, 0)}


def _stage_project(pcb: Path, staged: Path) -> None:
    for ext in (".kicad_pro", ".kicad_dru"):
        side = pcb.with_suffix(ext)
        if side.is_file():
            shutil.copy2(side, staged.with_suffix(ext))


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", help="constraints.json (teardrops "
                    "block); default: the one beside the board")
    ap.add_argument("--force", action="store_true",
                    help="run even when constraints do not enable teardrops")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be enabled; board untouched")
    ap.add_argument("--out-report")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    cpath = Path(args.constraints) if args.constraints else \
        pcb.parent / "constraints.json"
    cfg = tdl.config(tdl.load_constraints(cpath))
    board = tdl.read(pcb, cfg)
    req = tdl.required(cfg, board.flex)
    todo, done = tdl.select(board, cfg)
    facts = {
        "required": req, "flex_areas": board.flex,
        "enabled_by": ("constraints" if cfg.get("enabled") else
                       "required" if req else
                       "force" if args.force else None),
        "already": len(done), "drops_before": board.drop_count,
        "planned": [it.name for it in todo],
    }
    if not facts["enabled_by"]:
        facts["skipped"] = ("teardrops not enabled (constraints "
                            "teardrops.enabled) and not required (no FLEX_* "
                            "rule area)")
        return checklib.report(SCRIPT, pcb, [], **facts), args.out_report
    if args.dry_run or not todo:
        facts["drops_after"] = board.drop_count
        facts["enabled_items"] = {}
        return checklib.report(SCRIPT, pcb, [], **facts), args.out_report

    import env
    import kc
    import routelib
    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    if bp is None:
        raise CheckError("kicad-cli / KiCad bundled python not found (env.py)")
    stage = Path(tempfile.mkdtemp(prefix=".aiee_td_", dir=pcb.parent))
    vios: list[dict] = []
    try:
        staged = stage / pcb.name
        shutil.copy2(pcb, staged)
        _stage_project(pcb, staged)
        before = _drc_types(kc.run_drc(cli, staged, all_track_errors=True,
                                       refill=True, save_board=True))
        # the baseline refill may itself move drops of already-enabled items
        board0 = tdl.read(staged, cfg)
        todo, done = tdl.select(board0, cfg)
        job = {"verb": "enable", "board": str(staged), "out": str(staged),
               "vias": [it.uuid for it in todo if it.kind == "via"],
               "pads": [[it.ref, it.number, it.at[0], it.at[1]]
                        for it in todo if it.kind != "via"],
               "params": {k: cfg[k] for k in (
                   "length_ratio", "max_length_mm", "width_ratio",
                   "max_width_mm", "filter_ratio", "curved")}}
        res = routelib.run_worker(bp, job, stage, worker=WORKER)
        after = _drc_types(kc.run_drc(cli, staged, all_track_errors=True,
                                      refill=True, save_board=True))
        board1 = tdl.read(staged, cfg)
        names = {it.name for it in todo}
        short = [{"item": it.name, "layers": it.missing()}
                 for it in board1.items if it.name in names and it.missing()]
        grown = _grown(before, after)
        facts.update({
            "enabled_items": {k: sum(1 for it in todo if it.kind == k)
                              for k in ("via", "pth", "smd")},
            "worker": {"vias": res.get("vias"), "pads": res.get("pads")},
            "drops_after": board1.drop_count,
            "drops_wanted": sum(it.wanted() for it in todo),
            "missing": short,
            "drc_before": before, "drc_after": after,
        })
        if grown:
            vios.append(checklib.violation(
                SCRIPT, "error", None, None, None, [],
                f"DRC grew after teardrops (refilled vs refilled): {grown}; "
                "board left as it was", SCRIPT, kind="teardrop_regression",
                grown=grown))
        else:
            shutil.copy2(staged, pcb)
            facts["written"] = True
        for s in short:
            vios.append(checklib.violation(
                SCRIPT, "warning", None, None, None, [],
                f"{s['item']}: KiCad made no teardrop on {s['layers']} "
                "(track too wide for the pad, or no room)", SCRIPT,
                kind="teardrop_not_made", item=s["item"]))
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return checklib.report(SCRIPT, pcb, vios, **facts), args.out_report


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
