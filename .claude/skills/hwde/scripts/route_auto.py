"""route_auto - autoroute the remainder via Freerouting (S11, SPEC P7.3).

Flow (all on a STAGED copy in the work dir; the real board is swapped only on
success): refill zones (a stale/unfilled pour exports wrong) -> Specctra DSN
export via the SWIG worker (wx-suppressed; plane layers marked LT_POWER so the
DSN carries "(type power)") -> Freerouting CLI over an escalation ladder
(the DSN first carries each net's .kicad_dru width/clearance floor as its own
class, routelib.dsn_apply_net_rules, and each pre-routed wire chain as one
path, routelib.dsn_merge_wires, as split wires overflow Freerouting's stack;
routelib.DEFAULT_LADDER; deterministic flags, per-rung process timeout, score
logging) -> import the best rung's SES -> refill (imported tracks stale every
pour they cross - S11-verified: 33 clearance violations before refill, 0
after) -> final `kicad-cli pcb drc --schematic-parity --all-track-errors`.

Existing copper is protected by construction: the DSN exporter emits existing
tracks/vias as guide wires which Freerouting echoes back "(type protect)" -
pre-routed critical nets (route_critical.py) survive without an explicit lock.
Zones export as "(plane NET ...)"; Freerouting connects pads to planes itself
(including dropping stitch vias to reach an inner/back plane).

Freerouting's own success signal is NEVER trusted (LEARNINGS [freerouting]):
completion comes from the log's unrouted counts and the gate is kicad-cli DRC.

Contract:
  route_auto.py --pcb B.kicad_pcb [--exclude-classes Power,...]
      [--power-layers auto|"In1.Cu,In2.Cu"|none] [--max-rungs N]
      [--timeout-s S] [--work-dir DIR] [--out-report r.json]
      [--probe [--probe-passes N]]
  exit 0 = 100% routed AND DRC clean; 1 = incomplete or violations
  (board still updated - the fix loop owns the remainder); 2 = error
  (original board untouched).

--probe: capped-effort routability probe (S10 place_anneal feedback): stage,
export, ONE short Freerouting run; report {completion} and touch nothing.
route_probe() is the importable form.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import kc  # noqa: E402
import route_edit  # noqa: E402  (finish pass: rip sliver vias by uuid)
import routelib  # noqa: E402
from checklib import CheckError  # noqa: E402


def _tools():
    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    java = env.find_java()
    jar = env.find_freerouting_jar()
    missing = [n for n, v in (("kicad-cli", cli), ("bundled python", bp),
                              ("java 25", java), ("freerouting jar", jar))
               if not v]
    if missing:
        raise CheckError("missing tools: " + ", ".join(missing)
                         + " (check_env.py --full has remediation)")
    return cli, bp, java[0], jar


def _stage_board(pcb: Path, work: Path) -> Path:
    """Copy the board + every same-stem sidecar (pro/prl/dru/sch) to work."""
    work.mkdir(parents=True, exist_ok=True)
    staged = work / pcb.name
    for src in pcb.parent.glob(pcb.stem + ".*"):
        if src.is_file() and not src.name.endswith(".lck"):
            shutil.copy2(src, work / src.name)
    if not staged.is_file():
        raise CheckError(f"staging failed: {staged}")
    return staged


def _dsn_net_rules(staged: Path, dsn: Path) -> list[str]:
    """Write the staged board's per-net .kicad_dru track_width/clearance
    floors into the exported DSN; returns the nets given their own class."""
    import route_critical as rc
    dru = staged.with_suffix(".kicad_dru")
    text, moved = routelib.dsn_apply_net_rules(
        dsn.read_text(encoding="utf-8"),
        rc.dru_net_floors(dru, "track_width"),
        rc.dru_net_floors(dru, "clearance"))
    if moved:
        dsn.write_text(text, encoding="utf-8")
    return moved


def _dsn_merge_wires(dsn: Path) -> int:
    """Join the exported DSN's split pre-routed wires (Freerouting 2.2.4
    overflows its stack on them); returns the wires folded together."""
    text, merged = routelib.dsn_merge_wires(dsn.read_text(encoding="utf-8"))
    if merged:
        dsn.write_text(text, encoding="utf-8")
    return merged


def _auto_power_layers(bg: geom.BoardGeom) -> list[str]:
    """Inner copper layers that carry a zone fill and no tracks -> planes."""
    out = []
    for layer in bg.copper_layers[1:-1]:
        has_fill = any(z.fills.get(layer) for z in bg.zones_of())
        has_tracks = bool(bg.tracks_of(layer=layer))
        if has_fill and not has_tracks:
            out.append(layer)
    return out


def _routable_nets(bg: geom.BoardGeom) -> set[str]:
    """Nets with >= 2 pads (the autorouter's actual workload)."""
    counts: dict[str, int] = {}
    for p in bg.pads_of():
        if p.net:
            counts[p.net] = counts.get(p.net, 0) + 1
    return {n for n, c in counts.items() if c >= 2}


def _drc_unrouted(report: dict) -> list[str]:
    nets = {v.get("net") for v in report["violations"]
            if v.get("source") == "unconnected"}
    return sorted(n for n in nets if n)


_fresh_work_dir = routelib.fresh_work_dir
_swap_in = routelib.swap_in


def _placement_adjust_request(bg: geom.BoardGeom, unrouted: list[str],
                              rungs_tried: int) -> dict:
    """The P7 -> P6 backward edge (SPEC P7.5): a machine-readable placement
    micro-adjust request for the nets Freerouting could not complete. The
    orchestrator (S13) hands this to the placement agent, which may spread
    the named clusters / free the region and re-run P6 stage 3 - the ONLY
    sanctioned backward edge in the pipeline."""
    refs: set[str] = set()
    xs: list[float] = []
    ys: list[float] = []
    for net in unrouted:
        for p in bg.pads_of(net=net):
            refs.add(p.ref)
            xs.append(p.center[0])
            ys.append(p.center[1])
    region = [round(min(xs), 3), round(min(ys), 3),
              round(max(xs), 3), round(max(ys), 3)] if xs else None
    return {
        "request": "placement_adjust",
        "nets": unrouted,
        "refs": sorted(refs),
        "region": region,
        "reason": f"unrouted after {rungs_tried} freerouting rungs",
        "suggestions": [
            "spread the named clusters apart / off the congested region",
            "clear a ~3 mm escape ring around the anchor IC",
            "consider more board area or more layers if congestion persists",
        ],
    }


def _krt_finish(cli: Path, staged: Path, work: Path, drc: dict, *,
                refill: bool, parity: bool, timeout_s: int) -> dict | None:
    """Deterministic post-Freerouting mop-up via the vendored
    KiCadRoutingTools (S11 acceptance: FR leaves 0.5 mm-pitch LQFP GND pins
    unconnected - no fanout capability - and drops the odd via a few 10s of
    microns inside the hole-clearance floor). Steps, all on the staged board
    with a backup for full revert:
      1. remove vias implicated in hole_clearance/clearance DRC ERRORS
         (route_edit remove by uuid from the report's raw items);
      2. run KRT route.py per affected net (grid 0.05, board floors,
         --no-fix-drc-settings) - KRT only routes what is disconnected;
      3. refill + re-DRC; keep only if strictly better (fewer errors +
         no new error types), else restore the backup.
    Returns facts (kept, nets, ripped_vias, drc) or None when KRT is absent
    or there is nothing to fix."""
    unrouted = _drc_unrouted(drc)
    via_re = re.compile(r"^Via \[([^\]]+)\]")
    rip = []            # (uuid, net)
    for v in drc["violations"]:
        if v.get("source") != "drc" or v.get("severity") != "error":
            continue
        if v.get("check") not in ("hole_clearance", "clearance"):
            continue
        for item in v.get("items", []):
            m = via_re.match(item.get("msg", ""))
            if m and item.get("uuid"):
                rip.append((item["uuid"], m.group(1)))
    nets = sorted(set(unrouted) | {n for _, n in rip})
    if not nets:
        return None
    krt = env.find_krt()
    if krt is None:
        return {"kept": False, "nets": nets,
                "note": "KRT not vendored; finish pass skipped"}
    import route_critical as rc

    backup = work / ("prefinish_" + staged.name)
    shutil.copy2(staged, backup)
    try:
        if rip:
            route_edit.apply_ops(staged, [
                {"op": "remove", "uuid": u} for u, _ in rip])
        floors = rc.grading_floors(work / (staged.stem + ".kicad_pro"))
        fab = rc.write_fab_overrides(work, floors)
        # one batched invocation - KRT only routes what is disconnected, and
        # per-net runs pay the full startup/parse cost ~40x on a fallback
        out = work / "krt_finish.kicad_pcb"
        rc.run_krt(krt, "route.py", staged, out, ["--nets", *nets],
                   floors, fab, 0.05, timeout_s)
        os.replace(out, staged)
        # KRT can leave sub-grid crumbs (e.g. a 0.017 mm dangling stub) that
        # KiCad flags track_dangling but sit below route_cleanup's touch
        # tolerance. Removal is connectivity-safe: neighbouring round caps
        # overlap far beyond the crumb length.
        crumb_ops = [
            {"op": "remove", "uuid": m.group(1)}
            for m in rc._SEG_BLOCK_RE.finditer(
                staged.read_text(encoding="utf-8"))
            if _seg_len(m.group(0)) < 0.05]
        if crumb_ops:
            route_edit.apply_ops(staged, crumb_ops)
        if refill:
            kc.run_drc(cli, staged, refill=True, save_board=True)
        after = kc.run_drc(cli, staged, parity=parity, all_track_errors=True)
        b_err = {k: v for k, v in _err_counts(drc).items()}
        a_err = _err_counts(after)
        better = (sum(a_err.values()) < sum(b_err.values())
                  and not (set(a_err) - set(b_err)))
        if not better:
            shutil.copy2(backup, staged)
            return {"kept": False, "nets": nets,
                    "ripped_vias": len(rip),
                    "errors_before": b_err, "errors_after": a_err}
        return {"kept": True, "nets": nets, "ripped_vias": len(rip),
                "errors_before": b_err, "errors_after": a_err, "drc": after}
    except CheckError as exc:
        shutil.copy2(backup, staged)
        return {"kept": False, "nets": nets, "error": str(exc)[:300]}


def _seg_len(block: str) -> float:
    import route_critical as rc
    try:
        sx, sy = map(float, rc._SEG_FIELD_RES["start"].search(block).groups())
        ex, ey = map(float, rc._SEG_FIELD_RES["end"].search(block).groups())
    except AttributeError:
        return float("inf")
    return ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5


def _err_counts(report: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in report["violations"]:
        if v.get("severity") == "error":
            key = f"{v.get('source')}:{v.get('check')}"
            out[key] = out.get(key, 0) + 1
    return out


def route_probe(pcb: Path, *, passes: int = 4, timeout_s: int = 180,
                work_dir: Path | None = None) -> dict:
    """Capped-effort routability probe. Never modifies the board.

    Returns {"completion": float|None, "unrouted": int|None, ...}. For
    place_anneal wiring see place_anneal.anneal(route_probe=...) - S10.
    """
    pcb = Path(pcb).resolve()
    cli, bp, java, jar = _tools()
    # .resolve(): Freerouting runs with cwd=dsn.parent and KRT with
    # cwd=<plugins dir>, so relative work paths break silently
    # (LEARNINGS 1318; same fix as route_critical).
    work = _fresh_work_dir(Path(work_dir).resolve() if work_dir
                           else pcb.parent / "route_probe")
    staged = _stage_board(pcb, work)
    bg = geom.BoardGeom.from_file(staged)
    if bg.zones_of() and any(not z.filled for z in bg.zones_of()):
        kc.run_drc(cli, staged, refill=True, save_board=True)
        bg = geom.BoardGeom.from_file(staged)  # power layers need fresh fills
    dsn, ses = work / "probe.dsn", work / "probe.ses"
    routelib.run_worker(bp, {
        "verb": "export_dsn", "board": str(staged), "dsn": str(dsn),
        "layer_types": {ly: "power" for ly in _auto_power_layers(bg)}}, work)
    _dsn_net_rules(staged, dsn)
    merged = _dsn_merge_wires(dsn)
    facts = routelib.run_freerouting(
        java, jar, dsn, ses, rung={"mp": passes}, timeout=timeout_s,
        log_file=work / "probe.log")
    facts["completion"] = routelib.completion_fraction(facts)
    facts["dsn_wires_merged"] = merged
    return facts


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--exclude-classes", default=None,
                    help="net classes Freerouting must not route (-inc)")
    ap.add_argument("--power-layers", default="auto",
                    help='"auto" | none | comma list, e.g. "In1.Cu,In2.Cu"')
    ap.add_argument("--max-rungs", type=int, default=len(routelib.DEFAULT_LADDER))
    ap.add_argument("--timeout-s", type=int, default=3600,
                    help="per-rung Freerouting HARD wall-clock cap")
    ap.add_argument("--stall-s", type=int, default=900,
                    help="kill a rung with no output/.ses growth this long "
                         "(a 4-layer pass takes ~5 min)")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--out-report", default=None)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--probe-passes", type=int, default=4)
    ap.add_argument("--no-krt-finish", action="store_true",
                    help="skip the KRT mop-up pass after the ladder")
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")

    if args.probe:
        facts = route_probe(pcb, passes=args.probe_passes,
                            timeout_s=min(args.timeout_s, 600),
                            work_dir=Path(args.work_dir) if args.work_dir else None)
        payload = {"script": "route_auto", "status": "pass", "board": str(pcb),
                   "probe": True,
                   "facts": {k: facts.get(k) for k in
                             ("completion", "unrouted", "started_unrouted",
                              "final_score", "session_completed", "timed_out")}}
        return payload, args.out_report

    cli, bp, java, jar = _tools()
    # .resolve(): see route_probe - relative work paths break KRT/Freerouting
    work = _fresh_work_dir(Path(args.work_dir).resolve() if args.work_dir
                           else pcb.parent / "route")
    staged = _stage_board(pcb, work)

    # 1. fresh fills before export (unfilled/stale pours export wrong),
    #    THEN parse - power-layer auto-detection and routable-net counting
    #    must see the refilled state (review finding).
    has_zones = bool(geom.BoardGeom.from_file(staged).zones_of())
    if has_zones:
        kc.run_drc(cli, staged, refill=True, save_board=True)
    bg = geom.BoardGeom.from_file(staged)
    routable = _routable_nets(bg)
    tracks_before = len(bg.tracks_of())

    # 2. DSN export (plane layers marked power)
    if args.power_layers == "auto":
        power_layers = _auto_power_layers(bg)
    elif args.power_layers in ("none", ""):
        power_layers = []
    else:
        power_layers = [s.strip() for s in args.power_layers.split(",") if s.strip()]
    dsn = work / (pcb.stem + ".dsn")
    routelib.run_worker(bp, {
        "verb": "export_dsn", "board": str(staged), "dsn": str(dsn),
        "layer_types": {ly: "power" for ly in power_layers}}, work)
    dsn_net_rules = _dsn_net_rules(staged, dsn)
    dsn_wires_merged = _dsn_merge_wires(dsn)

    # 3. Freerouting escalation ladder
    ladder = routelib.DEFAULT_LADDER[:max(1, args.max_rungs)]
    rungs = []
    candidates = []  # (sort key, rung index, ses_path, facts)
    for i, rung in enumerate(ladder, 1):
        rung = dict(rung)
        if args.exclude_classes:
            rung["inc"] = args.exclude_classes
        ses = work / f"rung{i}.ses"
        facts = routelib.run_freerouting(
            java, jar, dsn, ses, rung=rung, timeout=args.timeout_s,
            stall_s=args.stall_s,
            log_file=work / f"rung{i}.log")
        entry = {"rung": i, "options": rung,
                 "unrouted": facts.get("unrouted"),
                 "started_unrouted": facts.get("started_unrouted"),
                 "final_score": facts.get("final_score"),
                 "passes": len(facts.get("passes", [])),
                 "timed_out": facts.get("timed_out"),
                 "kill_reason": facts.get("kill_reason"),
                 "ses_written": facts.get("ses_written")}
        rungs.append(entry)
        if facts.get("ses_written") and facts.get("unrouted") is not None:
            candidates.append(((facts["unrouted"],
                                -(facts.get("final_score") or 0.0)), i, ses,
                               facts))
            if facts["unrouted"] == 0:
                break
        if facts.get("timed_out") and not facts.get("passes"):
            # The DSN wedges Freerouting's reader (S11-verified: KRT guide
            # wires can drive PolylineTrace.combine into infinite recursion
            # before pass 1). Every rung parses the same DSN - skip the rest.
            entry["wedged"] = True
            break

    sch = work / (pcb.stem + ".kicad_sch")
    imported = None
    best_i = None
    best_facts: dict = {}
    fr_ok = False
    # 4. SES import - best rung first, next-best on a truncated/corrupt SES
    for key, i, ses, facts in sorted(candidates):
        try:
            imported = routelib.run_worker(bp, {
                "verb": "import_ses", "board": str(staged), "ses": str(ses),
                "out": str(staged)}, work, timeout=600)
            best_i, best_facts, fr_ok = i, facts, True
            break
        except CheckError:
            for e in rungs:
                if e["rung"] == i:
                    e["ses_import_failed"] = True
    if not fr_ok and (args.no_krt_finish or env.find_krt() is None):
        raise CheckError("no Freerouting rung produced an importable session "
                         f"and the KRT fallback is unavailable; see {work} "
                         "logs (board untouched)")

    # 4b. dedup: the SES echoes pre-session guide-wire copper (critical-net
    # trunks) back through ImportSpecctraSES as EXACT same-net duplicates -
    # invisible to DRC/gerbers (S14: run (a) shipped 45 echoed segments).
    dedup_facts = {"removed": 0}
    if fr_ok:
        dedup_facts = routelib.run_worker(bp, {
            "verb": "dedup_copper", "board": str(staged),
            "out": str(staged)}, work, timeout=300)

    # 5. refill (post-SES fills are stale) + DRC of the current state
    if has_zones:
        kc.run_drc(cli, staged, refill=True, save_board=True)
    drc = kc.run_drc(cli, staged, parity=sch.is_file(), all_track_errors=True)

    # 5b. KRT pass: with a Freerouting result this is the mop-up (fine-pitch
    # pads FR cannot fan out, sliver vias inside the hole-clearance floor);
    # with NO usable Freerouting result it is the FALLBACK autorouter for
    # the whole remainder. Kept only if the DRC strictly improves.
    finish_facts = None
    if not args.no_krt_finish:
        finish_facts = _krt_finish(cli, staged, work, drc,
                                   refill=has_zones,
                                   parity=sch.is_file(),
                                   timeout_s=min(args.timeout_s, 600))
        if finish_facts and finish_facts.get("kept"):
            drc = finish_facts.pop("drc")
    if not fr_ok and not (finish_facts and finish_facts.get("kept")):
        raise CheckError("Freerouting produced nothing usable and the KRT "
                         f"fallback did not improve the board; see {work} "
                         "(board untouched)")

    # 6. sanity + swap the routed board in
    bg2 = geom.BoardGeom.from_file(staged)
    tracks_after = len(bg2.tracks_of())
    if fr_ok and tracks_after <= tracks_before \
            and best_facts.get("started_unrouted"):
        raise CheckError("SES import added no tracks (board untouched); "
                         f"see {work}")
    _swap_in(staged, pcb)

    unrouted_nets = _drc_unrouted(drc)
    completion = (1.0 - len(unrouted_nets) / len(routable)) if routable else 1.0
    fully_routed = not unrouted_nets
    clean = drc["counts"]["total"] == 0
    status = "pass" if (fully_routed and clean) else "violations"
    adjust_request = None
    if unrouted_nets:
        adjust_request = _placement_adjust_request(bg2, unrouted_nets,
                                                   len(rungs))
    payload = {
        "script": "route_auto", "status": status, "board": str(pcb),
        "counts": drc["counts"], "violations": drc["violations"],
        "facts": {
            "completion": round(completion, 4),
            "routable_nets": len(routable),
            "unrouted_nets": unrouted_nets,
            "tracks_before": tracks_before, "tracks_after": tracks_after,
            "ses_items_added": (imported["tracks_after"]
                                - imported["tracks_before"])
            if imported else 0,
            "best_rung": best_i, "rungs": rungs,
            "fr_completion": routelib.completion_fraction(best_facts)
            if fr_ok else None,
            "power_layers": power_layers,
            "dsn_net_rules": dsn_net_rules,
            "dsn_wires_merged": dsn_wires_merged,
            "krt_finish": finish_facts,
            "ses_echo_dups_removed": dedup_facts.get("removed", 0),
            "work_dir": str(work),
        },
    }
    if adjust_request:
        payload["facts"]["placement_adjust_request"] = adjust_request
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("route_auto", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
