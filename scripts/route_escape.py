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

Via-in-pad at 0.8 mm (#552): every KRT run goes through lib/krt_launch.py
--hole-to-hole <board floor>, since KRT checks drills against its own 0.20
constant and placed dog-bone gap vias 0.566 mm from a via-in-pad (0.366
hole to hole against JLC 6L's 0.5). KRT still picks gap sites before it
falls back to via-in-pad, so with --allow-via-in-pad each field's NEW
in-pad vias are checked (vip_conflicts) against every via (hole to hole,
copper) and every foreign track (a floor-size 0.45 via bulges past a 0.32
pad onto stubs KRT threaded past the pad); a clashing in-pad ball loses its
escape and its neighbour keeps a clean one (DRC alone rips both). A ball
net with a .kicad_dru track-width rule wider than the escape track
(VBUS_J1's 0.32 neck) is not given to KRT (held_wide); left for the
routers. The 150+ track_dangling warnings after an escape are the kept
stubs themselves (open until routed), not rip leftovers.

--toward-partner (#547, opt-in): the constraints' diff-pair and
length_groups nets escape out of the field sides facing the nearest pad of
the same net on another part (partner_sides; krt_launch --escape-open
re-compiles KRT's dog-bone engine with a per-net goal). It costs gap sites:
a closed side sends more balls to via-in-pad. On linux-computer it is worse
than the default (U100's DDR balls are in its bottom-right corner, U200 is
up-left: forcing 48 nets out left/up loses most of them); it pays where a
field's balls already face their partner.

HDI builds (#477): on a board whose stack is an HDI build of stackups.yaml,
every field escapes through lib/hdilib.py instead of KRT: a microvia in the
pad, stacked and buried down to the next free layer (--hdi auto; --hdi off
keeps KRT's through-via dog-bones; a standard build never gets them). The
routers' rules come with it (hdilib via_type_rules, field_rules), and a power
ball whose net has an inner plane gets a stack to that plane.

Power and ground balls are not fanned (KRT treats them as plane balls: they
want one via straight to their plane - stitch_vias' job).

Power first (#923 point 1): before any field is escaped, the SMD pads of
the constraints' inner-plane power nets on the other parts (a PMIC's supply
pins, a regulator's output) get their plane vias (stitch_vias --nets power
--pads-only, ball-grid parts skipped). Escaped and routed signal tracks
otherwise take the room beside those pins first, and the stitch step that
runs after route_critical finds no spot: on a 0.5 mm SoC board close then
stalled at 125 opens until the signals around the PMIC and the USB-C power
pins were ripped by hand. The vias are graded by DRC on their own and
dropped as a set if they add an error type. --no-power-first skips it.

Pad fanout (#929): --pads U5.3,U5.7,L1.1 fans out named pads of any part
(QFN and SOT power pins, inductor and regulator pads) instead of ball grids:
a via beside each pad that clears every other net's copper and hole, and a
stub to it on the pad's layer, or a short join to the net's own copper when
no spot clears (lib/padfanout.py). The ops go through route_edit
(atomic, verified); --plan only reports them, --out-ops writes them.
--fan-width and --fan-via SIZE/DRILL override the net's width and the
board's common via. KRT is not used.

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
import hdilib  # noqa: E402
import kc  # noqa: E402
import route_critical as rc  # noqa: E402  (KRT staging, floors, DRC delta, rip)
import routelib  # noqa: E402
import stitch_vias  # noqa: E402
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


SIDE_NORMALS = {"left": (-1.0, 0.0), "right": (1.0, 0.0),
                "up": (0.0, -1.0), "down": (0.0, 1.0)}
OPEN_COS = 0.38            # a side opens when the partner lies within ~67 deg of it


def open_sides(dx: float, dy: float) -> tuple[str, ...]:
    """Field sides (board axes, up = -y) facing a partner at (dx, dy) from
    the ball: one side within ~22 deg of an axis, else the two around it."""
    d = (dx * dx + dy * dy) ** 0.5
    if d < 1e-9:
        return ()
    return tuple(s for s, (nx, ny) in SIDE_NORMALS.items()
                 if (nx * dx + ny * dy) / d >= OPEN_COS)


def partner_sides(bg, bga: bgalib.Bga, nets) -> dict[str, tuple[str, ...]]:
    """net -> the field sides facing that net's nearest pad on another part
    (#547). Nets with no pad off this BGA are left out."""
    own: dict[str, list] = {}
    for pd in bg.pads_of(ref=bga.ref):
        if pd.net in nets:
            own.setdefault(pd.net, []).append(pd.center)
    out: dict[str, tuple[str, ...]] = {}
    for net in sorted(own):
        pts = own[net]
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        far = [pd.center for pd in bg.pads_of(net=net) if pd.ref != bga.ref]
        if not far:
            continue
        tx, ty = min(far, key=lambda q: (q[0] - cx) ** 2 + (q[1] - cy) ** 2)
        sides = open_sides(tx - cx, ty - cy)
        if sides:
            out[net] = sides
    return out


def toward_fact(sides: dict[str, tuple[str, ...]]) -> dict:
    """{"left+up": n nets, ...} for the report."""
    out: dict[str, int] = {}
    for s in sides.values():
        k = "+".join(s)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def toward_nets(cons: dict, board_nets, specs: list[dict]) -> list[str]:
    """The nets --toward-partner steers: every diff pair's P/N and every
    length_groups member/ref net (check_length_groups' matching)."""
    import check_length_groups as clg

    out = {x for s in specs for x in (s.get("p"), s.get("n")) if x}
    for g in cons.get("length_groups") or []:
        if not isinstance(g, dict):
            continue
        for key in ("nets", "ref_nets"):
            found, _ = clg.match_nets(g.get(key) or [], board_nets)
            out.update(found)
    return sorted(out)


def fanout_nets(bga: bgalib.Bga, wide: dict[str, float], track: float,
                plane_min: int = 6) -> tuple[list[str], list[str]]:
    """(nets KRT may fan out, nets held back because a .kicad_dru width rule
    is wider than the escape track). KRT fans every net it is given at one
    width (VBUS_J1 at U100: 0.089 mm under a 0.32 mm neck rule, #552), so a
    wide net is left for the routers. KRT's own plane-ball rule (>= plane_min
    balls on the part) is kept, since a --nets list switches it off."""
    count: dict[str, int] = {}
    for n in bga.nets.values():
        if n:
            count[n] = count.get(n, 0) + 1
    held = sorted(n for n in count if wide.get(n, 0.0) > track + 1e-6
                  and count[n] < plane_min)
    ok = sorted(n for n in count if count[n] < plane_min and n not in held)
    return ok, held


def vip_conflicts(bg, bga: bgalib.Bga, new_uuids: set[str],
                  hole_to_hole: float, clearance: float) -> set[str]:
    """Nets whose NEW via-in-pad (a via centred in its own ball's pad)
    breaks hole-to-hole or copper clearance against any other via, or
    copper clearance against a foreign track (#552).
    KRT picks dog-bone gap sites before it falls back to via-in-pad and never
    re-checks the fallback against the gap vias around it: at 0.8 mm pitch a
    diagonal gap via sits 0.566 mm from the pad centre, inside any 0.5 mm
    hole-to-hole floor. Dropping the in-pad ball keeps its neighbour's
    clean gap escape (DRC alone rips both nets)."""
    pads = [pd for pd in bg.pads_of(ref=bga.ref) if pd.net]
    vias = list(bg.vias_of())
    region = field_region(bga)
    near = [v for v in vias if region.contains(Point(v.at))]
    inpad = []
    for v in near:
        if not v.uuid or v.uuid not in new_uuids:
            continue
        pt = Point(v.at)
        if any(pd.net == v.net and pd.poly.buffer(1e-4).contains(pt)
               for pd in pads):
            inpad.append(v)
    tracks = [x for x in bg.tracks_of() if x.net and region.intersects(x.poly)]
    out: set[str] = set()
    for v in inpad:
        for o in near:
            if o is v:
                continue
            d = ((o.at[0] - v.at[0]) ** 2 + (o.at[1] - v.at[1]) ** 2) ** 0.5
            if d < (o.drill + v.drill) / 2 + hole_to_hole - 1e-6 or (
                    o.net != v.net
                    and d < (o.diameter + v.diameter) / 2 + clearance - 1e-6):
                out.add(v.net)
                break
        else:
            # a floor-size via bulging past a small pad grazes the stubs KRT
            # threaded past the PAD (#552: 0.45 vias in 0.32 pads)
            reach = v.poly.buffer(clearance - 1e-6)
            if any(x.net != v.net and v.spans(x.layer)
                   and reach.intersects(x.poly) for x in tracks):
                out.add(v.net)
    return out


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


# ------------------------------------------------------------ HDI builds

def hdi_geometry(pitch: float, pad: float, caps: dict) -> tuple[float, float]:
    """(track, clearance) in the field: JLC's HDI floors (3 mil / 3 mil). They
    are what fits between via pads at 0.5 mm, and the field keeps them for
    every pitch so one rule covers it."""
    return (float(caps["min_trace_width_mm"]), float(caps["min_clearance_mm"]))


def planes_of(bg, bga: bgalib.Bga, planes: list[str]) -> dict[str, str]:
    """net -> the nearest inner plane under the field carrying it."""
    cu = bg.copper_layers
    start = cu.index(bga.layer)
    out: dict[str, str] = {}
    for ly in sorted(planes, key=lambda x: abs(cu.index(x) - start)):
        for z in bg.zones_of(layer=ly):
            if z.net and z.net not in out:
                out[z.net] = ly
    return out


def hdi_plan(bg, p: dict, spans: list, caps: dict, floors: dict,
             wide: dict[str, float]) -> hdilib.FieldPlan:
    b = p["bga"]
    track, clearance = hdi_geometry(b.pitch, b.pad_d, caps)
    fp = bg.footprints.get(b.ref, {})
    balls = bgalib._ball_pads([x for x in bg.pads_of(ref=b.ref)
                               if x.kind == "smd" and len(x.layers) == 1])
    _, held = fanout_nets(b, wide, track, plane_min=10 ** 6)
    region = field_region(b)
    return hdilib.plan_field(
        b, balls, fp.get("at"), fp.get("angle", 0.0) or 0.0, bg.copper_layers,
        spans, p["layers"], track=track, clearance=clearance,
        hole_clearance=float(caps["hole_to_trace_mm"]),
        plane_of=planes_of(bg, b, p["planes"]), held=set(held),
        existing=hdilib.obstacles_from_board(bg, region, b.ref))


def hdi_result(plan: hdilib.FieldPlan) -> dict:
    """The per-field record route_escape reports, in KRT's terms."""
    want = {b.net for b in plan.balls if b.net and b.role == "signal"}
    return {"requested": len(want), "escaped": len(want) - len(plan.left),
            "failed": len(plan.left), "unescaped_nets": plan.left}


# ------------------------------------------------------------ KRT steps

def power_first_ops(staged: Path, cons: Path | None,
                    skip_refs: list[str]) -> tuple[list[dict], dict]:
    """Plane vias for the constraints' inner-plane power nets' SMD pads,
    skipping skip_refs (the ball grids: their gap sites are the escape's).
    Returns (route_edit ops, fact); never touches the board."""
    if cons is None:
        return [], {"placed": 0, "note": "no constraints.json"}
    argv = ["--pcb", str(staged), "--constraints", str(cons), "--dry-run",
            "--nets", "power", "--pads-only"]
    if skip_refs:
        argv += ["--skip-refs", ",".join(sorted(skip_refs))]
    payload, _ = stitch_vias.run(argv)
    ops = payload["ops"]
    nets = payload.get("nets") or {}
    fact = {"nets": list(nets),
            "placed": sum(1 for o in ops if o["op"] == "add_via"),
            "requested": payload["requested"],
            "no_spot": sorted(f"{s['ref']}" for s in payload["skipped"]
                              if s["reason"] == "no_clear_spot")}
    if not nets:
        fact["note"] = "no power net with an inner plane"
    return ops, fact


def _run(krt: Path, script: str, args: list[str], timeout_s: int,
         log: Path, launch: list[str] | None = None) -> tuple[int, str]:
    """KRT script through lib/krt_launch.py (hwde's KRT patches, #552/#547)."""
    import subprocess
    cmd = [sys.executable, str(SCRIPTS / "lib" / "krt_launch.py"),
           *(launch or []), script, *args]
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
            pair_gap: float | None = None, tag: str = "",
            open_to: dict[str, tuple[str, ...]] | None = None) -> dict:
    staged, work, krt = ctx["staged"], ctx["work"], ctx["krt"]
    out = work / (f"{staged.stem}.{bga.ref}.{method or ep['method']}"
                  f"{'.' + tag if tag else ''}.kicad_pcb")
    fab = fab_overrides(work, ep, ctx["floors"])
    launch = ["--hole-to-hole", f"{ctx['floors']['hole_to_hole']:g}"]
    if open_to:
        plan = work / f"{out.stem}.open.json"
        plan.write_text(json.dumps({"open": {n: list(s) for n, s in
                                             sorted(open_to.items())}},
                                   indent=1), encoding="utf-8")
        launch += ["--escape-open", str(plan)]
    rcode, stdout = _run(krt, "bga_fanout.py",
                         [str(staged), "--output", str(out),
                          *fanout_args(bga, ep, layers, fab, ctx["grid_step"],
                                       method, pairs, pair_gap)]
                         + (["--nets", *nets] if nets else []),
                         ctx["timeout_s"], out.with_suffix(".log"), launch)
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
    ap.add_argument("--toward-partner", action="store_true",
                    help="escape the constraints' diff-pair and length_groups "
                         "nets out of the field sides facing the part each "
                         "one goes to (#547)")
    ap.add_argument("--hdi", choices=["auto", "on", "off"], default="auto",
                    help="auto (default): microvia-in-pad escape when the "
                         "board's stack is an HDI build; on: refuse a board "
                         "that isn't; off: KRT's through-via dog-bones")
    ap.add_argument("--stackup", default=None,
                    help="stackups.yaml entry (default: the one the board's "
                         "stack block matches)")
    ap.add_argument("--no-power-first", action="store_true",
                    help="skip the power pads' plane vias placed before "
                         "the escape (#923)")
    ap.add_argument("--plan", action="store_true",
                    help="no routing: report the BGAs, geometry and budget")
    ap.add_argument("--grid-step", type=float, default=DEFAULT_GRID)
    ap.add_argument("--timeout-s", type=int, default=900,
                    help="per-KRT-invocation process timeout")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--out-report", default=None)
    ap.add_argument("--pads", default=None,
                    help="pad fanout (#929): comma list of REF.PAD to give "
                         "a via and stub each, not a BGA escape")
    ap.add_argument("--fan-width", type=float, default=None,
                    help="--pads: stub width (default: the net's width)")
    ap.add_argument("--fan-via", default=None,
                    help="--pads: via SIZE/DRILL in mm (default: the "
                         "board's most common through via)")
    ap.add_argument("--out-ops", default=None,
                    help="--pads: write the route_edit ops here")
    args = ap.parse_args(argv)
    if args.pads:
        return pad_fanout(args)

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
    cons = rc._sidecar(pcb, args.constraints, "constraints.json")
    cons_data = checklib.load_json(cons, "constraints") if cons else {}
    if cons_data:
        all_specs = rc.diff_specs(cons_data, bg0.nets)
        if not args.no_pairs:
            specs = all_specs
    else:
        all_specs = []
    if args.toward_partner and not cons_data:
        raise CheckError("--toward-partner needs constraints.json (its diff "
                         "pairs and length_groups name the nets)")
    geo = zrules.board_layer_geometry(pcb)
    build, entry = (None, None) if args.hdi == "off" \
        else hdilib.board_build(bg0, args.stackup)
    if args.hdi == "on" and not build:
        raise CheckError("--hdi on: this board's stack is not an HDI build "
                         "of stackups.yaml, which drills through vias only")
    hdi_caps = hdilib.load_hdi_caps()
    spans = hdilib.spans_of(entry, bg0.copper_layers, caps=hdi_caps) if build else []
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
               if p["pairs"] else {}),
            **({"hdi_build": build} if build else {})}

    if args.plan or not bgas:
        facts = [_plan_fact(p) for p in plans]
        if build:
            for f, p in zip(facts, plans):
                pl = hdi_plan(bg0, p, spans, hdi_caps, floors, {})
                f["method"] = "hdi"
                f["hdi"] = pl.fact()
        payload = checklib.report(SCRIPT, str(pcb), [], facts={
            "bgas": facts, "floors": floors,
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
    power_first: dict = {"placed": 0, "note": "--no-power-first"}
    if not args.no_power_first:
        all_bgas = sorted(b.ref for b in bgalib.find_bgas(bg0, None))
        pf_ops, power_first = power_first_ops(staged, cons, all_bgas)
        if pf_ops:
            saved = staged.read_bytes()
            rc.route_edit.apply_ops(staged, pf_ops)
            if bg0.zones_of():
                kc.run_drc(cli, staged, refill=True, save_board=True)
            pf_after = kc.run_drc(cli, staged, all_track_errors=True)
            pf_new = rc.drc_delta(before, pf_after)["new"]
            if pf_new:     # drop the set; the escape runs as before
                staged.write_bytes(saved)
                power_first.update(placed=0, dropped_for_drc=pf_new)
            else:
                before = pf_after
    old_uuids = rc._copper_uuids(staged)

    wide = rc.dru_net_floors(dru, "track_width")
    toward = set(toward_nets(cons_data, bg0.nets, all_specs)) \
        if args.toward_partner else set()
    keys = ("requested", "escaped", "failed", "unescaped_nets",
            "drc_grazes", "deferred_fanout_nets")
    results = []
    for p in plans:
        b, ep = p["bga"], p["ep"]
        if build:
            bg_now = geom.BoardGeom.from_file(staged)
            pl = hdi_plan(bg_now, p, spans, hdi_caps, floors, wide)
            if pl.ops:
                rc.route_edit.apply_ops(staged, pl.ops)
            results.append({
                **_plan_fact(p), "method": "hdi", "hdi": pl.fact(),
                "krt": hdi_result(pl),
                **({"held_wide": pl.held} if pl.held else {}),
                "via_in_pad": {"nets": 0, "vias": 0, "kept": True},
                "_vip": set(), "_lost": set(pl.held)})
            continue
        allowed, held = fanout_nets(b, wide, ep["track_width"])
        only = allowed if held else None    # None: KRT's own net choice
        aset = set(allowed)
        first = None
        sides = (partner_sides(bg0, b, toward & aset) if toward else {})
        if p["pairs"] and args.pairs_first:
            # pairs first: their balls claim the dog-bone gap sites before
            # the single balls do (a full field otherwise leaves a deep pair
            # only via-in-pad, which is stripped)
            first = fan_one(ctx, b, ep, p["layers"],
                            nets=[x for pr in p["pairs"] for x in pr
                                  if x in aset],
                            pairs=p["pairs"], pair_gap=p["pair_gap"],
                            tag="pairs")
        summary = fan_one(ctx, b, ep, p["layers"], pairs=p["pairs"],
                          pair_gap=p["pair_gap"], nets=only, open_to=sides)
        r = {**_plan_fact(p), "krt": rc._slim(summary, keys),
             **({"krt_pairs": rc._slim(first, keys)} if first else {}),
             **({"toward_partner": toward_fact(sides)} if sides else {}),
             **({"held_wide": held} if held else {})}
        bg = geom.BoardGeom.from_file(staged)
        new = {x.uuid for x in list(bg.tracks_of()) + list(bg.vias_of())
               if x.uuid and x.uuid not in old_uuids}
        vip = via_in_pad_nets(bg, b, new)
        r["via_in_pad"] = {"nets": len(vip), "vias": sum(vip.values()),
                           "kept": bool(args.allow_via_in_pad)}
        if vip and not args.allow_via_in_pad:
            r["via_in_pad"]["items_removed"] = rc.rip_new_copper(
                staged, old_uuids, set(vip))
        elif vip:
            clash = vip_conflicts(bg, b, new, floors["hole_to_hole"],
                                  ep["clearance"])
            if clash:
                r["via_in_pad"]["clashing"] = sorted(clash)
                r["via_in_pad"]["items_removed"] = rip_in_fields(
                    staged, old_uuids, {n: {b.ref} for n in clash}, [b])
        r["_vip"] = set(vip) if not args.allow_via_in_pad else set()
        r["_lost"] = set(held) | set(r["via_in_pad"].get("clashing", []))
        results.append(r)

    moved = []
    if not args.no_cap_nudge and not build:
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
        left = sorted((lost | (ripped_all & own) | vip | r.pop("_lost"))
                      - kept_here)
        r["left_for_routers"] = left
        if left:
            violations.append(violation(
                SCRIPT, "warning", None, None, None, [r["ref"]],
                f"{r['ref']}: {len(left)} ball net(s) not escaped (KRT "
                f"dropped {len(lost)}, via-in-pad stripped {len(vip)}, "
                f"via-in-pad clashing "
                f"{len(r['via_in_pad'].get('clashing', []))}, wide nets held "
                f"{len(r.get('held_wide', []))}, DRC ripped "
                f"{len(r['ripped_by_drc'])}); left for route_critical / "
                "route_auto", SCRIPT, kind="bga_escape_incomplete",
                nets=left[:40]))
    if delta["new"]:
        raise CheckError(
            "escape left new DRC errors after ripping (original board "
            f"untouched): {delta['new']}; work dir kept at {work}")

    board_updated = (bool(kept_nets) or bool(moved)
                     or bool(power_first.get("placed")))
    if board_updated:
        routelib.swap_in(staged, pcb)
        if rules_changed:
            shutil.copy2(dru, pcb.with_suffix(".kicad_dru"))

    payload = checklib.report(SCRIPT, str(pcb), violations, facts={
        "bgas": results,
        "caps_moved": moved,
        "power_first": power_first,
        "dru_rules": rules_changed if board_updated else [],
        "drc_delta": delta,
        "floors": floors,
        "board_updated": board_updated,
        "work_dir": str(work),
    })
    return payload, args.out_report


def pad_fanout(args):
    """--pads: lib/padfanout's vias and stubs, applied with route_edit."""
    import padfanout
    import route_edit
    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    via = None
    if args.fan_via:
        try:
            size, drill = (float(x) for x in args.fan_via.split("/"))
        except ValueError as e:
            raise CheckError("--fan-via wants SIZE/DRILL, e.g. 0.45/0.2") from e
        via = (size, drill)
    if args.fan_width is not None and args.fan_width <= 0:
        raise CheckError("--fan-width must be positive")
    bg = geom.BoardGeom.from_file(pcb)
    specs = [s.strip() for s in args.pads.split(",") if s.strip()]
    ops, rows = padfanout.Fanout(bg, width=args.fan_width, via=via).fan(specs)
    if args.out_ops:
        Path(args.out_ops).write_text(json.dumps(
            {"version": 1, "ops": ops}, indent=1) + "\n", encoding="utf-8")
    applied = bool(ops) and not args.plan
    if applied:
        route_edit.apply_ops(pcb, ops)
    violations = [violation(
        SCRIPT, "warning", None, None, None, [r["pad"].split(".")[0]],
        f"{r['pad']} ({r['net'] or 'no net'}): {r['result']}", SCRIPT,
        kind="pad_fanout_incomplete", nets=[r["net"]] if r["net"] else [])
        for r in rows if r["result"] in ("no spot", "no net",
                                         "no copper layer")]
    payload = checklib.report(SCRIPT, str(pcb), violations, facts={
        "pads": rows, "ops": len(ops), "board_updated": applied})
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
