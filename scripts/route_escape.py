#!/usr/bin/env python
"""route_escape - BGA escape (dog-bone fan-out) before the other routers (#325).

Freerouting does poorly at fine-pitch BGA escapes, so P7 fans each ball grid
out first, with KRT's bga_fanout (tools/krt via env.find_krt), and leaves
every escaped ball as a short stub past the field for route_critical /
route_auto to pick up. Pre-routed copper survives them by construction (the
DSN exporter emits it as protected wires, see route_auto.py).

Per BGA (lib/bgalib.find_bgas: geometric grid detection, not the name):
  1. escape geometry from the board's FAB floors (bgalib.escape_params):
     via = max(min via, drill + 2 annular) - a guard for a fab row whose
     floor via breaks its own via ring (JLC 6L did, #443: fixed to 0.1);
     clearance = max(min clearance, hole clearance - ring). The netclass
     clearance is usually wider than a 0.8 mm field allows, so a
     courtyard-scoped DRU rule (bgalib.dru_rule; rules_gen.py writes the
     same rule) carries the escape clearance;
  2. KRT bga_fanout, dog-bone when the gap/street budget fits, else the
     channel engine (sparse fields escape between NC balls);
  3. via-in-pad is a paid fab option (filled and capped vias): KRT's
     dog-bone falls back to it for a ball with no free gap site, without
     checking the fallback against the gap vias already placed (0.57 mm
     apart, hole_to_hole + clearance errors on linux-computer). Those
     balls' escapes are removed unless --allow-via-in-pad;
  4. optional cap nudge (KRT place_fanout_clearance) for decouplers whose
     pads a via landed on (the back-side decouplers place_seed puts under
     the field). Moves are reported per ref;
  5. kicad-cli DRC against the board's real rules; the nets whose new
     copper is in a new error are ripped (route_critical's grading), up to
     ESCAPE_RIP_ROUNDS times. The original board is replaced only when no
     new DRC error type remains.

Diff pairs (#529): every pair of constraints.json (route_critical.diff_specs:
explicit diff_pairs plus pairs discovered on high_speed names) with both
balls in one field goes to KRT as --diff-pairs, so KRT escapes it COUPLED
where it fits (dog-bone vias, converge to the pair pitch, parallel past the
boundary) and single-ended where it does not. --diff-pair-gap is the pair's
own edge gap on the first inner escape layer from rules_gen's per-layer
rules (zrules), the smallest when pairs differ. KRT escapes a coupled pair
toward the NEARER field edge, not toward its partner part. --pairs-first
runs a pairs-only pass before the rest so pair balls take the dog-bone gap
sites first (linux-computer U200: 3/3 pairs coupled instead of 1/3, but 15
single balls left instead of 1), so it is opt-in.

DRC rip per field (#529): a net whose new copper is in a new DRC error loses
its escape only at the field(s) within FIELD_REACH_MM of the error, so a pair
escaped at two BGAs that grazes a resistor beside one keeps the other.
left_for_routers is per field (a net escaped at U200 but not at U100 is left
at U100).

Power and ground balls are not fanned (KRT treats them as plane balls: they
want one via straight to their plane - stitch_vias' job).

Facts: per BGA the geometry, budget, method, requested / escaped / kept
nets, the balls left for the routers, via-in-pad stripped, caps moved; the
DRU rules written; the DRC delta.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from shapely.geometry import Point, box  # noqa: E402

import bgalib  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402
import kc  # noqa: E402
import route_critical as rc  # noqa: E402  (KRT staging, floors, DRC delta, rip)
import routelib  # noqa: E402
import zrules  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "route_escape"
ESCAPE_RIP_ROUNDS = 3
PLANE_COVER = 0.5          # an inner zone over >= this share of the field is a plane
DEFAULT_GRID = 0.05        # mm - KRT stub-end snap; 0.1 shifts 0.8 mm-pitch stubs


# ------------------------------------------------------------ pure helpers

def escape_floors(pro_path: Path | None) -> dict:
    """The FAB floors escape geometry is built from (.kicad_pro
    board.design_settings.rules, written by fabfloors), not the netclass
    clearance grading_floors reports. KiCad stock values when absent."""
    fl = {"track_width": 0.2, "clearance": 0.2, "via_diameter": 0.5,
          "via_drill": 0.3, "annular": 0.0,
          "hole_clearance": bgalib.KICAD_HOLE_CLEARANCE_MM,
          "edge_clearance": 0.5, "hole_to_hole": 0.25}
    if pro_path is None or not Path(pro_path).is_file():
        return fl
    try:
        proj = json.loads(Path(pro_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fl
    rules = proj.get("board", {}).get("design_settings", {}).get("rules", {})
    for key, rk in (("track_width", "min_track_width"),
                    ("clearance", "min_clearance"),
                    ("via_diameter", "min_via_diameter"),
                    ("via_drill", "min_through_hole_diameter"),
                    ("annular", "min_via_annular_width"),
                    ("hole_clearance", "min_hole_clearance"),
                    ("hole_to_hole", "min_hole_to_hole")):
        if isinstance(rules.get(rk), (int, float)) and rules[rk] > 0:
            fl[key] = float(rules[rk])
    edge = rules.get("min_copper_edge_clearance") or rules.get("min_copper_to_edge")
    if edge:
        fl["edge_clearance"] = float(edge)
    return fl


def escape_layers(bg, bga: bgalib.Bga, explicit: list[str] | None = None
                  ) -> tuple[list[str], list[str]]:
    """(layers to escape on, inner plane layers left out). An inner layer
    is a plane under this field when one zone covers >= PLANE_COVER of the
    ball bbox; outer pours are fills, not planes, and stay usable."""
    if explicit:
        return [ly for ly in explicit if ly in bg.copper_layers], []
    field = box(*bga.bbox)
    planes = []
    for ly in bg.copper_layers[1:-1]:
        for z in bg.zones_of(layer=ly):
            # an unfilled inner zone at P7 is a planes_gen plane: count it
            if not z.filled or (field.area and z.fill_on(ly).intersection(
                    field).area >= PLANE_COVER * field.area):
                planes.append(ly)
                break
    return [ly for ly in bg.copper_layers if ly not in planes], planes


def via_in_pad_nets(bg, bga: bgalib.Bga, new_uuids: set[str]) -> dict[str, int]:
    """net -> count of NEW vias whose centre lies inside one of the BGA's
    own pads on that net (KRT's via-in-pad fallback)."""
    pads = [p for p in bg.pads_of(ref=bga.ref) if p.net]
    out: dict[str, int] = {}
    for v in bg.vias_of():
        if not v.uuid or v.uuid not in new_uuids:
            continue
        pt = Point(v.at)
        for p in pads:
            if p.net == v.net and p.poly.buffer(1e-4).contains(pt):
                out[v.net] = out.get(v.net, 0) + 1
                break
    return out


FIELD_REACH_MM = 3.0      # an escape's copper lies within this of its ball bbox


def field_region(bga: bgalib.Bga):
    return box(*bga.bbox).buffer(FIELD_REACH_MM)


def error_fields(before: dict, after: dict, nets: set[str],
                 bgas: list) -> dict[str, set[str] | None]:
    """net -> refs of the ball fields whose escape of that net is in a new
    DRC error (the error position within FIELD_REACH_MM of the field), or
    None when an error lies near no field (rip the net everywhere). A pair
    escaped at two BGAs that grazes a resistor beside one keeps its escape
    at the other (#529: SDQS1 at U100 ripped its clean U200 escape)."""
    grown = set(rc.drc_delta(before, after)["new"])
    regions = {b.ref: field_region(b) for b in bgas}
    out: dict[str, set[str] | None] = {}
    for v in after["violations"]:
        if v.get("severity") != "error" or v.get("check") not in grown:
            continue
        for item in v.get("items", []):
            m = rc._ITEM_NET_RE.match(item.get("msg", ""))
            if not m or m.group(1) not in nets:
                continue
            net, pos = m.group(1), item.get("pos")
            refs = ({r for r, g in regions.items() if g.contains(Point(pos))}
                    if pos else set())
            if not refs or out.get(net, set()) is None:
                out[net] = None
            else:
                out.setdefault(net, set()).update(refs)
    for net in nets:
        out.setdefault(net, None)
    return out


def rip_in_fields(staged: Path, old_uuids: set[str],
                  where: dict[str, set[str] | None], bgas: list) -> int:
    """Remove the new copper of each net, only inside the named fields'
    regions (everywhere when None)."""
    regions = {b.ref: field_region(b) for b in bgas}
    bg = geom.BoardGeom.from_file(staged)
    ops = []
    for x in list(bg.tracks_of()) + list(bg.vias_of()):
        if not x.uuid or x.uuid in old_uuids or x.net not in where:
            continue
        refs = where[x.net]
        if refs is None or any(regions[r].intersects(x.poly) for r in refs):
            ops.append({"op": "remove", "uuid": x.uuid})
    if ops:
        rc.route_edit.apply_ops(staged, ops)
    return len(ops)


def nets_with_new_copper(bg, old_uuids: set[str]) -> set[str]:
    return {x.net for x in list(bg.tracks_of()) + list(bg.vias_of())
            if x.net and x.uuid and x.uuid not in old_uuids}


def fab_overrides(work: Path, ep: dict, floors: dict) -> Path:
    """Pin KRT's fab floor to the escape geometry (an overrides file also
    stops KRT escalating to its paid 'advanced' tier)."""
    p = work / "escape_fab_overrides.txt"
    p.write_text(
        f"clearance = {ep['clearance']}\n"
        f"track_width = {ep['track_width']}\n"
        f"via_diameter = {ep['via_size']}\n"
        f"via_drill = {ep['via_drill']}\n"
        f"hole_to_hole = {floors['hole_to_hole']}\n"
        f"board_edge = {floors['edge_clearance']}\n", encoding="utf-8")
    return p


def escape_pairs(bga: bgalib.Bga, specs: list[dict], geo: dict,
                 layers: list[str]) -> tuple[list[list[str]], float | None]:
    """([[p, n]...] of the pairs with both balls in this field, the edge
    gap to escape them at). The gap is the pair's rules_gen gap on the first
    inner escape layer (zrules geometry); None (KRT's default) when no pair
    has one."""
    balls = set(bga.nets.values())
    pairs = [[s["p"], s["n"]] for s in specs
             if s.get("p") in balls and s.get("n") in balls]
    inner = layers[1:-1]
    gaps = []
    for p, _n in pairs:
        for ly in inner:
            g = (geo.get(p) or {}).get(ly) or {}
            if "gap_mm" in g:
                gaps.append(float(g["gap_mm"]))
                break
    return pairs, (round(min(gaps), 4) if gaps else None)


def fanout_args(bga: bgalib.Bga, ep: dict, layers: list[str], fab: Path,
                grid_step: float, method: str | None = None,
                pairs: list[list[str]] | None = None,
                pair_gap: float | None = None) -> list[str]:
    extra: list[str] = []
    if pairs:
        extra += ["--diff-pairs", *[x for pr in pairs for x in pr]]
        if pair_gap:
            extra += ["--diff-pair-gap", f"{pair_gap:g}"]
    return extra + ["--component", bga.ref, "--layers", *layers,
            "--escape-method", method or ep["method"],
            "--track-width", str(ep["track_width"]),
            "--clearance", str(ep["clearance"]),
            "--via-size", str(ep["via_size"]),
            "--via-drill", str(ep["via_drill"]),
            "--grid-step", str(grid_step),
            "--fab-overrides", str(fab),
            "--check-for-previous", "--no-fix-drc-settings"]


def footprint_moves(before, after, refs: set[str] | None = None) -> list[dict]:
    out = []
    for ref, fa in sorted(after.footprints.items()):
        fb = before.footprints.get(ref)
        if not fb or (refs is not None and ref not in refs):
            continue
        (x0, y0), (x1, y1) = fb.get("at", (0, 0)), fa.get("at", (0, 0))
        a0, a1 = fb.get("angle", 0.0), fa.get("angle", 0.0)
        if abs(x1 - x0) > 1e-4 or abs(y1 - y0) > 1e-4 or abs(a1 - a0) > 1e-3:
            out.append({"ref": ref, "dx_mm": round(x1 - x0, 4),
                        "dy_mm": round(y1 - y0, 4),
                        "rot_deg": round(a1 - a0, 3)})
    return out


# ------------------------------------------------------------ KRT steps

def _run(krt: Path, script: str, args: list[str], timeout_s: int,
         log: Path) -> tuple[int, str]:
    import subprocess
    cmd = [sys.executable, script, *args]
    try:
        cp = subprocess.run(cmd, cwd=str(krt), capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"KRT {script} timed out after {timeout_s}s") from exc
    out = (cp.stdout or "") + "\n" + (cp.stderr or "")
    log.write_text(out, encoding="utf-8")
    return cp.returncode, cp.stdout or ""


def _keep_sidecars(staged: Path, pristine: dict[str, bytes]) -> None:
    """KRT steps may rewrite the staged .kicad_pro (DRC floors); the board's
    own project + rules are the grading truth, so put them back."""
    for name, data in pristine.items():
        (staged.parent / name).write_bytes(data)


def fan_one(ctx: dict, bga: bgalib.Bga, ep: dict, layers: list[str],
            method: str | None = None, nets: list[str] | None = None,
            pairs: list[list[str]] | None = None,
            pair_gap: float | None = None, tag: str = "") -> dict:
    staged, work, krt = ctx["staged"], ctx["work"], ctx["krt"]
    out = work / (f"{staged.stem}.{bga.ref}.{method or ep['method']}"
                  f"{'.' + tag if tag else ''}.kicad_pcb")
    fab = fab_overrides(work, ep, ctx["floors"])
    rcode, stdout = _run(krt, "bga_fanout.py",
                         [str(staged), "--output", str(out),
                          *fanout_args(bga, ep, layers, fab, ctx["grid_step"],
                                       method, pairs, pair_gap)]
                         + (["--nets", *nets] if nets else []),
                         ctx["timeout_s"], out.with_suffix(".log"))
    summary = rc.parse_summary(stdout)
    if summary is None or not out.is_file():
        tail = stdout.strip()[-400:]
        raise CheckError(f"KRT bga_fanout on {bga.ref} produced no "
                         f"JSON_SUMMARY (rc={rcode}): {tail}")
    shutil.copy2(out, staged)
    _keep_sidecars(staged, ctx["pristine"])
    return summary


def nudge_caps(ctx: dict, clearance: float) -> list[dict]:
    staged, work, krt = ctx["staged"], ctx["work"], ctx["krt"]
    before = geom.BoardGeom.from_file(staged)
    out = work / f"{staged.stem}.capclear.kicad_pcb"
    _run(krt, "place_fanout_clearance.py",
         [str(staged), str(out), "--clearance", str(clearance),
          "--board-edge-clearance", str(ctx["floors"]["edge_clearance"])],
         ctx["timeout_s"], out.with_suffix(".log"))
    if not out.is_file():
        return []
    shutil.copy2(out, staged)
    _keep_sidecars(staged, ctx["pristine"])
    return footprint_moves(before, geom.BoardGeom.from_file(staged))


# ------------------------------------------------------------ CLI

def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--refs", default=None,
                    help="comma list of BGA refs (default: every ball grid)")
    ap.add_argument("--layers", default=None,
                    help="comma list of escape layers (default: every copper "
                         "layer that is not an inner plane under the field)")
    ap.add_argument("--method", choices=["auto", "dogbone", "channel",
                                         "underpad"], default=None,
                    help="KRT engine (default: dogbone when the budget fits, "
                         "else channel)")
    ap.add_argument("--allow-via-in-pad", action="store_true",
                    help="keep KRT's via-in-pad fallbacks (a paid fab option: "
                         "filled, capped vias)")
    ap.add_argument("--no-cap-nudge", action="store_true",
                    help="never move decouplers; balls whose via lands on one "
                         "are ripped instead")
    ap.add_argument("--constraints", default=None,
                    help="constraints.json for the diff pairs escaped coupled "
                         "(default: next to the board)")
    ap.add_argument("--no-pairs", action="store_true",
                    help="escape diff-pair balls single-ended like any other")
    ap.add_argument("--pairs-first", action="store_true",
                    help="a pairs-only KRT pass before the rest, so pair balls "
                         "take the gap sites first (more pairs coupled, often "
                         "fewer single balls escaped)")
    ap.add_argument("--plan", action="store_true",
                    help="no routing: report the BGAs, geometry and budget")
    ap.add_argument("--grid-step", type=float, default=DEFAULT_GRID)
    ap.add_argument("--timeout-s", type=int, default=900,
                    help="per-KRT-invocation process timeout")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    pro = pcb.with_suffix(".kicad_pro")
    floors = escape_floors(pro if pro.is_file() else None)
    bg0 = geom.BoardGeom.from_file(pcb)
    refs = [r.strip() for r in args.refs.split(",")] if args.refs else None
    bgas = bgalib.find_bgas(bg0, refs)
    if refs:
        missing = sorted(set(refs) - {b.ref for b in bgas})
        if missing:
            raise CheckError(f"not ball grids on this board: {missing}")
    explicit_layers = [x.strip() for x in args.layers.split(",")] \
        if args.layers else None
    specs: list[dict] = []
    if not args.no_pairs:
        cons = rc._sidecar(pcb, args.constraints, "constraints.json")
        if cons:
            specs = rc.diff_specs(checklib.load_json(cons, "constraints"),
                                  bg0.nets)
    geo = zrules.board_layer_geometry(pcb)
    plans = []
    for b in bgas:
        ep = bgalib.escape_params(b, floors)
        if args.method:
            ep["method"] = args.method
        layers, planes = escape_layers(bg0, b, explicit_layers)
        pairs, gap = escape_pairs(b, specs, geo, layers)
        plans.append({"bga": b, "ep": ep, "layers": layers, "planes": planes,
                      "pairs": pairs, "pair_gap": gap})

    def _plan_fact(p):
        return {**p["bga"].as_dict(), **{k: p["ep"][k] for k in (
            "via_size", "via_drill", "track_width", "clearance", "method",
            "why", "budget")}, "layers": p["layers"],
            "plane_layers": p["planes"],
            **({"pairs": p["pairs"], "pair_gap_mm": p["pair_gap"]}
               if p["pairs"] else {})}

    if args.plan or not bgas:
        payload = checklib.report(SCRIPT, str(pcb), [], facts={
            "bgas": [_plan_fact(p) for p in plans], "floors": floors,
            "board_updated": False,
            "note": "plan only" if bgas else "no ball grid on this board"})
        return payload, args.out_report

    cli, krt = rc._tools()
    work = (Path(args.work_dir).resolve() if args.work_dir
            else pcb.parent / "route_escape")
    routelib.fresh_work_dir(work)
    staged = rc._stage_board(pcb, work)
    dru = staged.with_suffix(".kicad_dru")
    dru_text = dru.read_text(encoding="utf-8") if dru.is_file() else ""
    dru_text, rules_changed = bgalib.ensure_rules(
        dru_text, [(p["bga"].ref, p["ep"]["clearance"]) for p in plans])
    if rules_changed:
        dru.write_text(dru_text, encoding="utf-8")
    pristine = {f.name: f.read_bytes() for f in work.iterdir()
                if f.is_file() and f.suffix in (".kicad_pro", ".kicad_dru")
                and f.stem == staged.stem}
    ctx = {"staged": staged, "work": work, "krt": krt, "floors": floors,
           "grid_step": args.grid_step, "timeout_s": args.timeout_s,
           "pristine": pristine}

    if bg0.zones_of():
        kc.run_drc(cli, staged, refill=True, save_board=True)
    before = kc.run_drc(cli, staged, all_track_errors=True)
    old_uuids = rc._copper_uuids(staged)

    results = []
    for p in plans:
        b, ep = p["bga"], p["ep"]
        first = None
        if p["pairs"] and args.pairs_first:
            # pairs first: their balls claim the dog-bone gap sites before
            # the single balls do (a full field otherwise leaves a deep pair
            # only via-in-pad, which is stripped)
            first = fan_one(ctx, b, ep, p["layers"],
                            nets=[x for pr in p["pairs"] for x in pr],
                            pairs=p["pairs"], pair_gap=p["pair_gap"],
                            tag="pairs")
        summary = fan_one(ctx, b, ep, p["layers"], pairs=p["pairs"],
                          pair_gap=p["pair_gap"])
        keys = ("requested", "escaped", "failed", "unescaped_nets",
                "drc_grazes", "deferred_fanout_nets")
        r = {**_plan_fact(p), "krt": rc._slim(summary, keys),
             **({"krt_pairs": rc._slim(first, keys)} if first else {})}
        bg = geom.BoardGeom.from_file(staged)
        new = {x.uuid for x in list(bg.tracks_of()) + list(bg.vias_of())
               if x.uuid and x.uuid not in old_uuids}
        vip = via_in_pad_nets(bg, b, new)
        r["via_in_pad"] = {"nets": len(vip), "vias": sum(vip.values()),
                           "kept": bool(args.allow_via_in_pad)}
        if vip and not args.allow_via_in_pad:
            r["via_in_pad"]["items_removed"] = rc.rip_new_copper(
                staged, old_uuids, set(vip))
        r["_vip"] = set(vip)
        results.append(r)

    moved = []
    if not args.no_cap_nudge:
        clr = min(p["ep"]["clearance"] for p in plans)
        moved = nudge_caps(ctx, clr)

    # grade against the board's real rules; rip what broke them
    rounds = []
    for _ in range(ESCAPE_RIP_ROUNDS):
        if bg0.zones_of():
            kc.run_drc(cli, staged, refill=True, save_board=True)
        after = kc.run_drc(cli, staged, all_track_errors=True)
        delta = rc.drc_delta(before, after)
        if not delta["new"]:
            break
        mine = nets_with_new_copper(geom.BoardGeom.from_file(staged), old_uuids)
        bad = rc.nets_in_new_errors(before, after, mine)
        if not bad:
            break
        where = error_fields(before, after, bad, [p["bga"] for p in plans])
        rounds.append({"nets": sorted(bad), "new_errors": delta["new"],
                       "fields": {n: sorted(f) if f else "all"
                                  for n, f in sorted(where.items())},
                       "items_removed": rip_in_fields(staged, old_uuids, where,
                                                      [p["bga"] for p in plans])})
    else:
        if bg0.zones_of():
            kc.run_drc(cli, staged, refill=True, save_board=True)
        after = kc.run_drc(cli, staged, all_track_errors=True)
        delta = rc.drc_delta(before, after)
    delta["rip_rounds"] = rounds

    bg1 = geom.BoardGeom.from_file(staged)
    kept_nets = nets_with_new_copper(bg1, old_uuids)
    new_items = [x for x in list(bg1.tracks_of()) + list(bg1.vias_of())
                 if x.net and x.uuid and x.uuid not in old_uuids]
    violations = []
    for r, p in zip(results, plans):
        own = set(p["bga"].nets.values())
        vip = r.pop("_vip")
        ref = p["bga"].ref
        region = field_region(p["bga"])
        kept_here = {x.net for x in new_items if x.net in own
                     and region.intersects(x.poly)}
        ripped_here = {n for rd in rounds for n, f in rd["fields"].items()
                       if f == "all" or ref in f}
        r["kept_nets"] = len(kept_here)
        r["ripped_by_drc"] = sorted(ripped_here & own)
        ripped_all = ripped_here
        lost = set(r["krt"].get("unescaped_nets", []) or [])
        left = sorted((lost | (ripped_all & own)
                       | (set() if args.allow_via_in_pad else vip))
                      - kept_here)
        r["left_for_routers"] = left
        if left:
            violations.append(violation(
                SCRIPT, "warning", None, None, None, [r["ref"]],
                f"{r['ref']}: {len(left)} ball net(s) not escaped (KRT "
                f"dropped {len(lost)}, via-in-pad stripped "
                f"{0 if args.allow_via_in_pad else len(vip)}, DRC ripped "
                f"{len(r['ripped_by_drc'])}); left for route_critical / "
                "route_auto", SCRIPT, kind="bga_escape_incomplete",
                nets=left[:40]))
    if delta["new"]:
        raise CheckError(
            "escape left new DRC errors after ripping (original board "
            f"untouched): {delta['new']}; work dir kept at {work}")

    board_updated = bool(kept_nets) or bool(moved)
    if board_updated:
        routelib.swap_in(staged, pcb)
        if rules_changed:
            shutil.copy2(dru, pcb.with_suffix(".kicad_dru"))

    payload = checklib.report(SCRIPT, str(pcb), violations, facts={
        "bgas": results,
        "caps_moved": moved,
        "dru_rules": rules_changed if board_updated else [],
        "drc_delta": delta,
        "floors": floors,
        "board_updated": board_updated,
        "work_dir": str(work),
    })
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
