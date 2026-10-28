"""planes_gen - GND/power plane pours + thermal vias + refill (S11, SPEC P7.2).

Plans copper pours from constraints.json["planes"] (or sensible defaults),
creates them via the SWIG bundled-python worker (lib/route_swig.py add_zones -
zones are saved UNFILLED, ZONE_FILLER segfaults headless), drops thermal-via
grids into exposed pads that sit on a plane net (route_edit op list), refills
via `kicad-cli pcb drc --refill-zones --save-board`, then verifies every
planned zone actually filled (geom re-parse). Inner plane layers (In*.Cu) are
marked LT_POWER and SAVED with the board so route_auto's DSN export carries
"(type power)".

Contract:
  planes_gen.py --pcb B.kicad_pcb [--constraints c.json] [--out-report r.json]
      [--inset-mm 0.5] [--no-thermal-vias] [--ep-min-mm2 4.0]
      [--via-size 0.6] [--via-drill 0.3] [--via-pitch 1.2]
  exit 0 = all planned zones filled; 1 = a planned zone filled to ZERO area
  (violation kind "zone_unfilled" - board still updated, the fix loop owns
  it); 2 = error (original board untouched).

constraints.json["planes"] schema (this module is the owner):
  "planes": [
    {"net": "GND",            # required - net name on the board
     "layer": "In1.Cu",       # required - copper layer for the pour
     "region": null,          # optional [x1,y1,x2,y2] mm; null/absent =
                              #   board outline bbox inset by --inset-mm
     "priority": 0,           # optional; auto-raised so a smaller zone
                              #   overlapping a larger one on the same layer
                              #   wins (power island inside a pour)
     "min_island_mm2": null,  # optional island-removal area threshold
     "clearance": null,       # optional local zone clearance mm
     "min_width": 0.25,       # optional min fill width mm (corpus default)
     "connect": "thermal"},   # optional "solid" -> (connect_pads yes ...)
                              #   for high-current fan-in lobes (T6 P7B-2;
                              #   thermal relief starves a 5 A pad)
    ...]

When the key is absent, defaults are derived from the board:
  2-layer: one GND pour on B.Cu spanning the outline.
  4-layer: GND plane on In1.Cu + the dominant power net (most pads among
           constraints["power"] entries, else pad counts) on In2.Cu.
Either way, every constraints["high_speed"][*].reference net MUST end up
with a plane: a missing reference net gets a default full pour on the first
plane-candidate layer with no planned zone (noted in facts).

Atomicity: the board + same-stem sidecars are staged in a scratch dir inside
the board's directory; worker + route_edit + refill operate on the staged
copy; only the .kicad_pcb is os.replace()d back (board.Save drops a default
.kicad_pro sibling - the real one is never touched). Any failure = exit 2,
original untouched.

Idempotency: a planned zone whose (net, layer, region) is already covered
>= 80% by an existing fill is SKIPPED (status "existing" in facts.zones) -
re-adding it would duplicate the pour and KiCad 10 DRC flags overlapping
same-priority zones (zones_intersect) even for the SAME net (S11-verified).

Known limitation: zero-fill detection intersects the net's fill with the
planned region - a PRE-EXISTING same-net fill inside the region can mask a
new zone that filled to nothing (zone creation itself is count-verified).
"""
from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import kc  # noqa: E402
import route_edit  # noqa: E402
import routelib  # noqa: E402
from checklib import CheckError  # noqa: E402
from shapely.geometry import MultiPolygon, Point, Polygon, box  # noqa: E402

INSET_MM = 0.5          # default pour inset from the outline bbox
MIN_WIDTH_MM = 0.25     # zone min fill width (golden corpus min_thickness)
VIA_SIZE_MM = 0.6       # thermal via diameter
VIA_DRILL_MM = 0.3      # thermal via drill
VIA_PITCH_MM = 1.2      # thermal via grid pitch
VIA_MARGIN_MM = 0.05    # via annulus must stay this far inside the pad
EP_MIN_MM2 = 4.0        # exposed-pad heuristic floor (largest netted SMD pad)
FILL_EPS_MM2 = 0.01     # below this a planned zone counts as unfilled
EXISTING_COVER = 0.80   # existing-fill coverage above which an entry is skipped

_INNER_RE = re.compile(r"^In\d+\.Cu$")
_PLANE_KEYS = {"net", "layer", "region", "priority", "min_island_mm2",
               "clearance", "min_width", "connect"}
_CONNECT_VALUES = {"solid", "thermal"}
HOLE_EDGE_GAP = 0.2     # mm drill edge-to-edge floor (0.5 centre for 0.3s)
EP_OWN_ARRAY = 4        # existing thru-hole items in the land -> skip grid


# ------------------------------------------------------------------ planning

def _ref_nets(constraints: dict | None) -> list[str]:
    """Reference nets demanded by constraints["high_speed"] (str or {layer: net})."""
    out: list[str] = []
    for e in (constraints or {}).get("high_speed") or []:
        ref = e.get("reference") if isinstance(e, dict) else None
        vals = ([ref] if isinstance(ref, str)
                else list(ref.values()) if isinstance(ref, dict) else [])
        for v in vals:
            if isinstance(v, str) and v and v not in out:
                out.append(v)
    return out


def _pad_counts(bg: geom.BoardGeom) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in bg.pads_of():
        if p.net:
            counts[p.net] = counts.get(p.net, 0) + 1
    return counts


def dominant_power_net(bg: geom.BoardGeom, constraints: dict | None) -> str | None:
    """The power net with the most pads: candidates from constraints["power"],
    else every board net except GND / high-speed reference nets."""
    counts = _pad_counts(bg)
    cands = [e.get("net") for e in (constraints or {}).get("power") or []
             if isinstance(e, dict)]
    cands = [n for n in cands if n and counts.get(n, 0) >= 2]
    if not cands:
        skip = {"GND", *_ref_nets(constraints)}
        cands = [n for n, c in counts.items() if n not in skip and c >= 2]
    if not cands:
        return None
    return max(sorted(cands), key=lambda n: counts[n])


def _default_entries(bg: geom.BoardGeom,
                     constraints: dict | None) -> tuple[list[dict], list[str]]:
    layers = bg.copper_layers
    notes: list[str] = []
    if len(layers) >= 4:
        plan = [{"net": "GND", "layer": layers[1]}]
        pwr = dominant_power_net(bg, constraints)
        if pwr:
            plan.append({"net": pwr, "layer": layers[2]})
        else:
            plan.append({"net": "GND", "layer": layers[2]})
            notes.append(f"no power net found; {layers[2]} gets a second "
                         "GND plane")
    else:
        plan = [{"net": "GND", "layer": layers[-1]}]
    return plan, notes


def _plane_candidate_layers(bg: geom.BoardGeom) -> list[str]:
    """Preferred layers for an auto-added plane: inner first, then B.Cu."""
    inner = [l for l in bg.copper_layers if _INNER_RE.match(l)]
    return inner + [bg.copper_layers[-1]]


def _region_rect(entry: dict, bg: geom.BoardGeom, inset: float,
                 idx: int) -> tuple[float, float, float, float]:
    region = entry.get("region")
    if region is not None:
        if not (isinstance(region, (list, tuple)) and len(region) == 4
                and all(isinstance(v, (int, float)) for v in region)):
            raise CheckError(f"planes[{idx}]: region must be [x1,y1,x2,y2] mm")
        x1, y1, x2, y2 = (float(v) for v in region)
    else:
        if bg.outline.is_empty:
            raise CheckError("board has no Edge.Cuts outline - cannot derive "
                             "a default pour region")
        bx1, by1, bx2, by2 = bg.outline.bounds
        x1, y1 = bx1 + inset, by1 + inset
        x2, y2 = bx2 - inset, by2 - inset
    if not (x2 > x1 and y2 > y1):
        raise CheckError(f"planes[{idx}]: degenerate region "
                         f"[{x1}, {y1}, {x2}, {y2}]")
    return (round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3))


def _rect_area(z: dict) -> float:
    x1, y1, x2, y2 = z["_rect"]
    return (x2 - x1) * (y2 - y1)


def _rects_overlap(a, b) -> bool:
    # <= not <: rects that merely TOUCH along a shared edge still trip
    # KiCad 10 zones_intersect at equal priority (LEARNINGS 1327 (b), the
    # keepout-band pattern), so touching counts as conflicting; same-net
    # fills at distinct priorities still merge into one island.
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def assign_priorities(plan: list[dict]) -> None:
    """A smaller zone overlapping a larger one on the same layer needs a
    STRICTLY higher priority or the island never fills (KiCad fills the
    higher-priority zone first and the lower one keeps clear of it). ANY
    same-layer overlap - INCLUDING a shared edge - gets distinct
    priorities: KiCad 10 DRC flags same-priority overlapping/touching zones
    (zones_intersect) even for the same net.
    Explicit priorities are honored when they already satisfy that."""
    order = sorted(range(len(plan)), key=lambda i: (-_rect_area(plan[i]), i))
    done: list[int] = []
    for i in order:
        z = plan[i]
        floor = 0
        for j in done:  # every done entry has area >= z's (sort order)
            o = plan[j]
            if o["layer"] == z["layer"] and _rects_overlap(o["_rect"],
                                                           z["_rect"]):
                floor = max(floor, o["priority"] + 1)
        given = z.get("priority")
        z["priority"] = max(int(given) if given is not None else 0, floor)
        done.append(i)


def build_plan(bg: geom.BoardGeom, constraints: dict | None,
               inset: float = INSET_MM) -> tuple[list[dict], dict]:
    """-> (plan, meta). Plan entries carry net/layer/region/priority plus the
    resolved "_rect"; meta = {source, added_for_reference, notes}."""
    constraints = constraints or {}
    notes: list[str] = []
    if isinstance(constraints.get("planes"), list):
        plan = [dict(e) for e in constraints["planes"]]
        source = "constraints"
    else:
        plan, notes = _default_entries(bg, constraints)
        source = "defaults"

    # validation
    if not plan:
        raise CheckError("planes plan is empty")
    for i, z in enumerate(plan):
        if not isinstance(z, dict):
            raise CheckError(f"planes[{i}]: entries must be objects")
        unknown = z.keys() - _PLANE_KEYS
        if unknown:
            raise CheckError(f"planes[{i}]: unknown keys {sorted(unknown)}")
        net, layer = z.get("net"), z.get("layer")
        if not (isinstance(net, str) and net):
            raise CheckError(f"planes[{i}]: net must be a non-empty string")
        if layer not in bg.copper_layers:
            raise CheckError(f"planes[{i}]: layer {layer!r} is not a copper "
                             f"layer of this board {bg.copper_layers}")
        if net not in bg.nets:
            raise CheckError(f"planes[{i}]: net {net!r} not on board")
        if z.get("connect") is not None \
                and z["connect"] not in _CONNECT_VALUES:
            raise CheckError(f"planes[{i}]: connect must be one of "
                             f"{sorted(_CONNECT_VALUES)}, got "
                             f"{z['connect']!r}")

    # every high-speed reference net must end up with a plane
    added: list[dict] = []
    planned_nets = {z["net"] for z in plan}
    for net in _ref_nets(constraints):
        if net in planned_nets:
            continue
        if net not in bg.nets:
            raise CheckError(f"high_speed reference net {net!r} not on board")
        used = {z["layer"] for z in plan}
        layer = next((l for l in _plane_candidate_layers(bg) if l not in used),
                     None)
        if layer is None:
            layer = bg.copper_layers[-1]
            notes.append(f"no free plane layer for reference net {net}; "
                         f"overlapping pour added on {layer}")
        entry = {"net": net, "layer": layer}
        plan.append(entry)
        added.append({"net": net, "layer": layer})
        planned_nets.add(net)
        notes.append(f"added default pour for high-speed reference net "
                     f"{net} on {layer}")

    for i, z in enumerate(plan):
        z["_rect"] = _region_rect(z, bg, inset, i)
        cov = _existing_cover(bg, z["net"], z["layer"], z["_rect"])
        if cov >= EXISTING_COVER:
            z["_existing"] = round(cov, 3)
            notes.append(f"existing {z['net']} fill covers "
                         f"{cov:.0%} of the {z['layer']} region; zone not "
                         "re-added (re-run / pre-poured board)")
    assign_priorities(plan)
    return plan, {"source": source, "added_for_reference": added,
                  "notes": notes}


def _existing_cover(bg: geom.BoardGeom, net: str, layer: str, rect) -> float:
    """Fraction of the (region ^ outline) already covered by `net`'s fill."""
    fill = bg.zone_fill(net, layer)
    if fill.is_empty:
        return 0.0
    region = box(*rect)
    if not bg.outline.is_empty:
        region = region.intersection(bg.outline)
    if region.area <= FILL_EPS_MM2:
        return 0.0
    return fill.intersection(region).area / region.area


def worker_zones(plan: list[dict]) -> list[dict]:
    """Plan entries -> lib/route_swig.py add_zones zone dicts. The optional
    "connect": "solid" key (T6, P7B-2) makes the zone connect pads SOLID
    ((connect_pads yes ...)) instead of KiCad's default thermal relief -
    the scripted form of the pd-trigger pour fan-in hand patch (LEARNINGS
    791: thermal spokes would starve a 5 A pad), which router.md's
    never-hand-edit rule used to contradict."""
    return [{"net": z["net"], "layer": z["layer"], "rect": list(z["_rect"]),
             "priority": z["priority"],
             "min_island_mm2": z.get("min_island_mm2"),
             "clearance": z.get("clearance"),
             "min_width": z.get("min_width", MIN_WIDTH_MM),
             "connect": z.get("connect")}
            for z in plan]


def plane_layer_types(plan: list[dict]) -> dict[str, str]:
    """INNER plane layers -> "power" (outer pours stay signal)."""
    return {z["layer"]: "power" for z in plan if _INNER_RE.match(z["layer"])}


# ------------------------------------------------------------- thermal vias

def ep_pads(bg: geom.BoardGeom, nets: set[str],
            min_mm2: float = EP_MIN_MM2) -> list:
    """Exposed-pad candidates: per footprint, the largest NETTED SMD pad when
    its area >= min_mm2 AND its net is a plane net. Standard EP footprints
    carry no-net SMD sub-segments overlapping the EP - those are ignored
    entirely (prior-attempt fact). A footprint whose EP sits on a non-plane
    net is skipped wholesale."""
    by_ref: dict[str, list] = {}
    for p in bg.pads_of():
        by_ref.setdefault(p.ref, []).append(p)
    out = []
    for ref in sorted(by_ref):
        netted_smd = [p for p in by_ref[ref] if len(p.layers) == 1 and p.net]
        if not netted_smd:
            continue
        ep = max(netted_smd, key=lambda p: (p.size[0] * p.size[1], p.number))
        if ep.size[0] * ep.size[1] < min_mm2:
            continue
        if ep.net not in nets:
            continue  # EP belongs to another net -> skip the whole footprint
        out.append(ep)
    return out


def via_grid(poly: Polygon, *, size: float = VIA_SIZE_MM,
             pitch: float = VIA_PITCH_MM,
             margin: float = VIA_MARGIN_MM) -> list[tuple[float, float]]:
    """Grid points (pitch-spaced, centered on the polygon centroid) where a
    via of diameter `size` fits fully inside `poly`. Vias in the EP's own pad
    need no connecting track."""
    if poly.is_empty:
        return []
    inner = poly.buffer(-(size / 2.0 + margin))
    if inner.is_empty:
        return []
    cx, cy = poly.centroid.x, poly.centroid.y
    minx, miny, maxx, maxy = poly.bounds
    nx = int(math.ceil((maxx - minx) / (2.0 * pitch)))
    ny = int(math.ceil((maxy - miny) / (2.0 * pitch)))
    pts = []
    for j in range(-ny, ny + 1):
        for i in range(-nx, nx + 1):
            p = (cx + i * pitch, cy + j * pitch)
            if inner.covers(Point(p)):
                pts.append((round(p[0], 3), round(p[1], 3)))
    pts.sort()
    return pts


def _ep_existing_drills(bg: geom.BoardGeom, ep) -> list:
    """Drill extents already inside an EP's land: board vias whose centre
    lies in the pad plus same-footprint thru-hole pads (the U22 eFuse ships
    15 vias-in-pad in its OWN land - LEARNINGS 1327 (a); gridding on top
    produced 24 hole_to_hole + 2 holes_co_located)."""
    out = []
    for v in bg.vias_of():
        if ep.poly.covers(Point(v.at)):
            out.append(Point(v.at).buffer(max(v.drill, 0.3) / 2.0,
                                          quad_segs=8))
    for p in bg.pads_of(ref=ep.ref):
        if len(p.layers) > 1 and ep.poly.covers(Point(p.center)):
            dp = p.drill_poly
            out.append(dp if not dp.is_empty
                       else Point(p.center).buffer(0.15, quad_segs=8))
    return out


def thermal_via_ops(bg: geom.BoardGeom, plane_nets: set[str], *,
                    ep_min_mm2: float = EP_MIN_MM2, size: float = VIA_SIZE_MM,
                    drill: float = VIA_DRILL_MM,
                    pitch: float = VIA_PITCH_MM) -> tuple[list[dict], list[dict]]:
    """-> (route_edit add_via ops, per-pad facts). T6 (P7A-5): existing
    drills inside the land are respected - grid points inside the hole
    floor of any of them are dropped, and a land already carrying >=
    EP_OWN_ARRAY thru-hole items skips the grid entirely (the footprint
    ships its own via array)."""
    ops: list[dict] = []
    pads: list[dict] = []
    for ep in ep_pads(bg, plane_nets, ep_min_mm2):
        existing = _ep_existing_drills(bg, ep)
        if len(existing) >= EP_OWN_ARRAY:
            pads.append({"ref": ep.ref, "pad": ep.number, "net": ep.net,
                         "vias": 0,
                         "note": f"footprint ships its own via array "
                                 f"({len(existing)} thru-hole items in the "
                                 "land); grid skipped"})
            continue
        pts = via_grid(ep.poly, size=size, pitch=pitch)
        if existing:
            floor = HOLE_EDGE_GAP + drill / 2.0
            pts = [p for p in pts
                   if all(h.distance(Point(p)) >= floor - 1e-9
                          for h in existing)]
        if not pts:
            continue
        for x, y in pts:
            ops.append({"op": "add_via", "at": [x, y], "size": size,
                        "drill": drill, "net": ep.net})
        pads.append({"ref": ep.ref, "pad": ep.number, "net": ep.net,
                     "vias": len(pts)})
    return ops, pads


# ------------------------------------------------------------- verification

def _fill_in_region(bg: geom.BoardGeom, net: str, layer: str,
                    rect) -> tuple[float, int]:
    """(area mm2, island count) of `net`'s zone fill inside the region."""
    fill = bg.zone_fill(net, layer)
    if fill.is_empty:
        return 0.0, 0
    clipped = fill.intersection(box(*rect))
    if clipped.is_empty:
        return 0.0, 0
    if isinstance(clipped, Polygon):
        parts = [clipped]
    elif isinstance(clipped, MultiPolygon):
        parts = list(clipped.geoms)
    else:  # GeometryCollection from a boundary graze
        parts = [g for g in getattr(clipped, "geoms", [])
                 if isinstance(g, Polygon)]
    parts = [g for g in parts if g.area > FILL_EPS_MM2]
    return sum(g.area for g in parts), len(parts)


def verify_zones(after: geom.BoardGeom, plan: list[dict],
                 pre_counts: dict) -> tuple[list[dict], list[dict]]:
    """Count-check zone creation (hard error) + per-zone fill facts and
    zone_unfilled violations (exit-1 material). Entries skipped as
    "_existing" are fill-checked but not count-checked."""
    want: dict[tuple[str, str], int] = {}
    for z in plan:
        if z.get("_existing"):
            continue
        key = (z["net"], z["layer"])
        want[key] = want.get(key, 0) + 1
    for (net, layer), n in want.items():
        have = len(after.zones_of(net=net, layer=layer))
        if have < pre_counts.get((net, layer), 0) + n:
            raise CheckError(f"zone creation not persisted: expected "
                             f">={pre_counts.get((net, layer), 0) + n} zones "
                             f"for {net} on {layer}, found {have}")
    zone_facts: list[dict] = []
    violations: list[dict] = []
    for z in plan:
        area, islands = _fill_in_region(after, z["net"], z["layer"], z["_rect"])
        zone_facts.append({"net": z["net"], "layer": z["layer"],
                           "region": list(z["_rect"]),
                           "priority": z["priority"],
                           "status": "existing" if z.get("_existing")
                           else "added",
                           "area_mm2": round(area, 3), "islands": islands})
        if area < FILL_EPS_MM2:
            x1, y1, x2, y2 = z["_rect"]
            violations.append(checklib.violation(
                "planes_gen", "error", ((x1 + x2) / 2.0, (y1 + y2) / 2.0),
                z["layer"], z["net"], [],
                f"planned zone on {z['layer']} filled to zero area",
                "planes_gen", kind="zone_unfilled",
                region=list(z["_rect"])))
    return zone_facts, violations


# -------------------------------------------------------------------- driver

def _stage_board(pcb: Path, work: Path) -> Path:
    """Copy the board + every same-stem sidecar (pro/prl/dru/sch) into work -
    kicad-cli refill needs the project sidecars for correct rules."""
    staged = work / pcb.name
    for src in pcb.parent.glob(pcb.stem + ".*"):
        if src.is_file() and not src.name.endswith(".lck"):
            shutil.copy2(src, work / src.name)
    if not staged.is_file():
        raise CheckError(f"staging failed: {staged}")
    return staged


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (default: next to the board)")
    ap.add_argument("--inset-mm", type=float, default=INSET_MM,
                    help="default pour inset from the outline bbox")
    ap.add_argument("--no-thermal-vias", action="store_true",
                    help="skip the exposed-pad thermal via grids")
    ap.add_argument("--ep-min-mm2", type=float, default=EP_MIN_MM2,
                    help="exposed-pad area floor for thermal vias")
    ap.add_argument("--via-size", type=float, default=VIA_SIZE_MM)
    ap.add_argument("--via-drill", type=float, default=VIA_DRILL_MM)
    ap.add_argument("--via-pitch", type=float, default=VIA_PITCH_MM)
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    constraints = {}
    cpath = Path(args.constraints) if args.constraints \
        else pcb.parent / "constraints.json"
    if args.constraints or cpath.is_file():
        constraints = checklib.load_json(cpath, "constraints")

    bg = geom.BoardGeom.from_file(pcb)
    plan, meta = build_plan(bg, constraints, args.inset_mm)
    to_add = [z for z in plan if not z.get("_existing")]
    zones = worker_zones(to_add)
    layer_types = plane_layer_types(plan)
    plane_nets = {z["net"] for z in plan}
    pre_counts = {(n, l): len(bg.zones_of(net=n, layer=l))
                  for n, l in {(z["net"], z["layer"]) for z in to_add}}

    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    if bp is None:
        raise CheckError("KiCad bundled python not found (env.py)")

    stage = Path(tempfile.mkdtemp(prefix=".aiee_planes_", dir=pcb.parent))
    try:
        staged = _stage_board(pcb, stage)

        # 1. zones (saved unfilled; inner plane layer types saved too).
        # Runs even with an empty to-add list so layer types stay enforced
        # on a re-run.
        routelib.run_worker(bp, {"verb": "add_zones", "board": str(staged),
                                 "out": str(staged), "zones": zones,
                                 "layer_types": layer_types}, stage)

        # 2. thermal vias under exposed pads (atomic op list on the staged copy)
        via_ops: list[dict] = []
        via_pads: list[dict] = []
        if not args.no_thermal_vias:
            via_ops, via_pads = thermal_via_ops(
                bg, plane_nets, ep_min_mm2=args.ep_min_mm2,
                size=args.via_size, drill=args.via_drill,
                pitch=args.via_pitch)
            if via_ops:
                route_edit.apply_ops(staged, via_ops)

        # 3. refill (the pipeline's only headless fill path)
        kc.run_drc(cli, staged, refill=True, save_board=True)

        # 4. verify, then swap ONLY the .kicad_pcb back
        after = geom.BoardGeom.from_file(staged)
        zone_facts, violations = verify_zones(after, plan, pre_counts)
        os.replace(staged, pcb)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    facts = {
        "plan_source": meta["source"],
        "zones_added": len(to_add),
        "zones": zone_facts,
        "thermal_vias": len(via_ops),
        "thermal_pads": via_pads,
        "layer_types": layer_types,
        "added_for_reference": meta["added_for_reference"],
        "notes": meta["notes"],
    }
    payload = checklib.report("planes_gen", str(pcb), violations, facts=facts)
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("planes_gen", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
