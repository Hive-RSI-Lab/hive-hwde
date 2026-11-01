#!/usr/bin/env python
"""dfm_check.py - gerber-level DFM against jlc_capabilities.yaml (SPEC P9).

The INDEPENDENT second geometry path. Every other check in the pipeline reads
the .kicad_pcb through geom.py; this one reads the files we actually ship - the
exported gerbers and Excellon drill, through gerbonara (lib/gerblib.py). Two
parsers over two formats means an export-stage defect shows up as a
disagreement instead of being invisible to a board-file-only checker.

Checks (all thresholds from reference/jlc_capabilities.yaml, keyed
<layers>layer_<oz>oz):

  copper   min_trace_width, min_clearance (gap between distinct copper
           islands - gerbers carry no nets, so "touching = one conductor",
           which is exactly what the fab's own DFM engine sees),
           min_copper_to_edge
  drill    min_hole_diameter, min_hole_to_hole, min_hole_to_edge,
           annular ring (hole vs the pad copper around it). Castellated
           holes (pad_prop_castellated) are exempt from hole/copper-to-edge
           and the ring check - they sit on the edge by design - and are
           checked by the castellation family instead
  castellation  lib/castellation.py over the BOARD file against the
           castellated: block of jlc_capabilities.yaml: drill, hole-to-hole,
           ring, inward pad extension, hole centre on the outline, distance
           to other edges and corners, board size and thickness, and a
           plated hole the outline cuts without the castellated property
  mask     silk_over_pad (silk ink inside a mask opening = unprintable/
           unsolderable), solder-mask dam between openings
  silk     min silk stroke width
  paste    paste aperture with NO mask opening (tented SMD pad - the stencil
           deposits paste on mask and the part cannot be soldered; a classic
           wrong-layer/footprint export defect)
  assembly CPL polarity: board pad->net vs schematic pin->net. KiCad's own
           --schematic-parity is net-level and does NOT catch a polarized part
           mounted backwards (LEARNINGS/V9); comparing per PAD NUMBER does, and
           the pad geometry gives the apparent rotation delta.
  placement CPL rotation per part vs its OWN LCSC footprint model
           (cpl_verify.py): pin 1 must land on pad 1 and a diode/LED/polar
           cap's cathode/+ on the board's, with the shipped CPL (fab dir's
           CPL.csv, else this run's). A wrong part, and a part with no model
           or no fit, is an ERROR (cpl_rotation / cpl_no_model); a model
           centre off the board's by > 0.5 mm is a warning (cpl_offset). A
           model fetch that FAILED (EasyEDA 403/429 rate limit, other HTTP
           status, network) is one finding for the run, cpl_fetch_failed,
           listing the parts and the reason - never cpl_no_model. When it is
           the run's only error the run is UNVERIFIED, not failed: status
           error, exit 2, `unverified` naming the parts; rerun once EasyEDA
           answers. Models are cached in the board (<parts.json dir>/easyeda,
           env.easyeda_cache) as they are fetched - commit that dir and the
           board's dfm never asks EasyEDA again.
           Runs with --parts. The fab step's image pass (cpl_render.py + a
           vision agent -> cpl_visual.json beside the CPL, or --visual) sits
           beside it: a part the two disagree on, or one the image pass left
           out, is an ERROR (cpl_visual_disagree); no image pass at all is a
           coverage hole (family placement_visual) that --strict refuses.
  zone_fill  the BOARD's saved zone fills against a fresh refill: a board
           saved without a refill passes DRC (it checks the netclass floor,
           not the zone's rule) and the Gerbers carry the stale copper
           (FAB-27, hwde-led-test DFM-1: 0.1275 mm vs 0.5 mm).
           A gap under the zone's clearance is only a candidate (a project
           rule can set a smaller one): it is confirmed on a refilled copy
           (kicad-cli, ~10 s, only when there are candidates), stale where
           the fresh fill sits further from the item. NPTH holes are left
           out (KiCad clears them by hole clearance). ERROR
           (dfm_stale_zone_fill); refill before export. No refill possible
           = a coverage hole (zone_fill skipped), never a pass
  netlist  Valor/CAM350 netlist compare, what a fab's E-test does: the
           shipped copper's islands, joined through plated holes and slots
           (blind/buried by their drill file's span), against the IPC-D-356
           netlist (the package's `<name>.ipc`, else kicad-cli's from the
           board; lib/ipcd356.py). One island holding two nets = ERROR
           dfm_net_short (net ties exempt); one net in two islands, or a
           test point on no copper = ERROR dfm_net_open; a D-356 name that
           stands for two board nets (kicad-cli truncates to 14 chars) =
           WARNING dfm_netlist_name_clash
  release  gerber layer completeness (against the BOARD's declared layer set,
           never the files being audited - a package that dropped a layer must
           read as incomplete, not as a smaller board), drill validity,
           closed Edge.Cuts outline, and the ASSEMBLY-CLASS legs: an
           `smt_placed`/`tht` part with no LCSC number, a part classed
           `smt_placed`/`tht` with no placement, a declared populate quantity the classes
           contradict, and a shipped BOM/CPL that lists a part the declared
           variant does not place (codex C9 - rf-de-20m's nine DNP sites)

The capability key (<layers>layer_<oz>oz) is derived from the BOARD: layer
count from its (layers ...) block, copper weight from its (stackup ...) block
(0.035 mm = 1 oz), unless --copper-oz pins it explicitly.

Severity policy: a JLCPCB manufacturing minimum that is actually violated is an
ERROR. Where the row carries JLC's absolute floor apart from the value a design
is held to (abs_min_<key> vs min_<key>, #554: via drill/diameter/ring, hole
size, copper to edge, pad and via hole-to-hole), only a value under the
absolute floor is an error; one between the two is ONE warning per kind
(`<kind>_preferred`, listing every place), and a via size JLC builds for
more money is a warning `dfm_via_cost_tier` per size, naming the tier
(`via` family: board vias against min_via_drill/min_via_diameter). Advisory classes that legitimate boards routinely trip - silk stroke
width (KiCad's default 0.12 mm silk prints fine at JLC) and mask dams - are
WARNINGS, so they are reported without failing the gate (the fp_verify /
netlist_audit precedent).

Emits the S2 normalized violation schema via checklib; exit 0/1/2.

CLI:
  dfm_check.py --pcb board.kicad_pcb [--fab-dir DIR] [--copper-oz N]
               [--schematic s.kicad_sch | --netlist b.net | --no-polarity]
               [--parts parts.json] [--capabilities cap.yaml] [--out r.json]
               [--cpl CPL.csv] [--visual cpl_visual.json]
               [--easyeda-cache DIR] [--offline]
"""
from __future__ import annotations

import argparse
import json
import csv
import math
import re
import sys
import tempfile
import warnings
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import yaml  # noqa: E402
from shapely.geometry import Point  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402

import castellation  # noqa: E402
import fabmaterial  # noqa: E402
import checklib  # noqa: E402
import gerblib  # noqa: E402
import geom  # noqa: E402
from checklib import CheckError  # noqa: E402

SOURCE = "check.dfm"
CHECK = "dfm"
CAPABILITIES = SCRIPTS.parent / "reference" / "jlc_capabilities.yaml"

# Two copper features closer than this are numerically touching (one conductor),
# not a clearance violation. Far below any fab capability, far above float noise.
EPS_TOUCH = 1e-4
# Ignore silk/mask overlap smaller than this - antialiasing-scale slivers.
MIN_SILK_OVERLAP_MM2 = 1e-3
# Below this area a silk-over-opening sliver is a cosmetic the fab auto-clips
# (library body outlines kiss their own mask openings by microns on EasyEDA
# footprints - S14 run (a) measured 0.0036 mm2 slivers on a KiCad-DRC-clean
# board). At/above it, real ink lands on solder surface -> error (the S1
# silk-over-pad mutant measures 0.344 mm2 and must stay an error).
SILK_OVERLAP_ERROR_MM2 = 0.05
# Slack for measurements taken off tessellated arc geometry (gerblib flattens
# arcs to 1 um). A board built EXACTLY at a fab minimum - blinky2's 0.6 mm vias
# on a 0.3 mm drill are exactly the 0.15 mm annular floor - must not be failed
# by micron-scale flattening error. Two orders of magnitude below the smallest
# meaningful DFM delta (~0.01 mm), twice the flattening bound.
GEOM_TOL_MM = 2e-3


def _below(value: float, limit: float, tol: float = GEOM_TOL_MM) -> bool:
    """Is `value` genuinely under `limit` (not just tessellation noise)?"""
    return value + tol < limit


# ------------------------------------------------------------------ rules

def load_capabilities(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CheckError(f"cannot read capabilities {path}: {exc}") from exc
    if not isinstance(data, dict) or "design_rules" not in data:
        raise CheckError(f"{path} has no design_rules block")
    return data


def rule_key(layers: int, copper_oz: float, material: str | None = None) -> str:
    oz = f"{copper_oz:g}"
    suffix = ("" if not material or material == fabmaterial.FR4
              else f"_{material}")
    return f"{layers}layer_{oz}oz{suffix}"


def pick_rules(caps: dict, layers: int, copper_oz: float,
               material: str | None = None) -> tuple[str, dict]:
    key = rule_key(layers, copper_oz, material)
    rules = caps["design_rules"].get(key)
    if rules is None:
        avail = ", ".join(sorted(caps["design_rules"]))
        raise CheckError(f"no capability entry '{key}' (have: {avail})")
    return key, rules


def _sexp_block(text: str, start: int) -> str:
    """The balanced (...) block whose opening paren is at/after `start`."""
    depth = 0
    for j in range(start, len(text)):
        c = text[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return text[start:]


def derive_copper_oz(pcb: Path) -> tuple[float, str]:
    """Outer copper weight from the board's own (stackup ...) block.

    -> (oz, source) with source "stackup" (derived) or "default" (no stackup
    block / no copper-layer thickness: today's 1 oz behavior). The gate calls
    run() with no copper_oz, so without this every board was checked against
    the 1 oz floors - a 2 oz board (6 mil min trace) judged at 5 mil is a
    latent false-pass. 0.035 mm = 1 oz; rounded to the nearest half-oz so a
    nonstandard thickness still lands on a capability-table key.
    """
    try:
        text = pcb.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 1.0, "default"
    m = re.search(r"\(stackup\b", text)
    if m is None:
        return 1.0, "default"
    block = _sexp_block(text, m.start())
    coppers: list[tuple[str, float]] = []
    for lm in re.finditer(r'\(layer\s+"([^"]+)"', block):
        sub = _sexp_block(block, lm.start())
        if not re.search(r'\(type\s+"copper"\)', sub):
            continue
        tm = re.search(r"\(thickness\s+([0-9.]+)", sub)
        if tm:
            coppers.append((lm.group(1), float(tm.group(1))))
    if not coppers:
        return 1.0, "default"
    # F.Cu is the outer weight (capability keys are outer-copper keyed);
    # stackup blocks list layers top->bottom, so fall back to the first.
    outer = dict(coppers).get("F.Cu", coppers[0][1])
    oz = round(outer / 0.035 * 2) / 2
    if oz <= 0:
        return 1.0, "default"
    return oz, "stackup"


# ------------------------------------------------------------ copper checks

def _mid(ls):
    p = ls.interpolate(0.5, normalized=True)
    return (p.x, p.y)


def check_trace_width(fab, rules, vios: list) -> None:
    lo = rules.get("min_trace_width_mm")
    if lo is None:
        return
    for name in fab.copper_layer_names():
        lg = fab.copper(name)
        if lg is None:
            continue
        for ls, w in lg.trace_lines:
            if w + 1e-9 < lo:
                vios.append(checklib.violation(
                    CHECK, "error", _mid(ls), name, None, [],
                    f"trace width {w:.4f} mm below JLC minimum {lo} mm "
                    f"on {name}", SOURCE, kind="dfm_trace_width",
                    width_mm=checklib.rnd(w), min_mm=lo))


def _pairs_within(polys, limit):
    """Yield (i, j) index pairs whose geometries are within `limit`."""
    if len(polys) < 2:
        return
    tree = STRtree(polys)
    seen = set()
    for i, g in enumerate(polys):
        for j in tree.query(g.buffer(limit)):
            j = int(j)
            if j <= i:
                continue
            key = (i, j)
            if key in seen:
                continue
            seen.add(key)
            yield i, j


def check_clearance(fab, rules, vios: list) -> None:
    lo = rules.get("min_clearance_mm")
    if lo is None:
        return
    for name in fab.copper_layer_names():
        lg = fab.copper(name)
        if lg is None:
            continue
        comps = lg.components()
        for i, j in _pairs_within(comps, lo):
            d = comps[i].distance(comps[j])
            if d <= EPS_TOUCH or not _below(d, lo):
                continue
            a, b = comps[i], comps[j]
            p1, p2 = a.exterior.interpolate(a.exterior.project(
                Point(b.centroid))), b.centroid
            vios.append(checklib.violation(
                CHECK, "error", (p1.x, p1.y), name, None, [],
                f"copper clearance {d:.4f} mm below JLC minimum {lo} mm "
                f"on {name}", SOURCE, kind="dfm_clearance",
                clearance_mm=checklib.rnd(d), min_mm=lo))


# A refill that moves a fill this much further from an item (mm) means the
# saved fill was stale; a fill KiCad would leave where it is, is not.
ZONE_FILL_MOVE_MM = 0.01


_FRESH: dict = {}


def _refilled(pcb: Path):
    """A refilled copy's geometry, kept per process by the board's and its
    rules' content (a refill is a kicad-cli run, ~5-15 s)."""
    import hashlib
    h = hashlib.sha256()
    for ext in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
        side = pcb.with_suffix(ext)
        h.update(ext.encode() + (side.read_bytes() if side.exists() else b""))
    key = h.hexdigest()
    if key not in _FRESH:
        tmp = geom._refill_copy(pcb)
        try:
            _FRESH[key] = geom.BoardGeom.from_file(tmp)
        finally:
            geom._cleanup(tmp)
    return _FRESH[key]


def _zone_fill_candidates(bg) -> list:
    """Every other-net pad, track or via nearer a zone's saved fill than the
    zone's own clearance: (zone, layer, gap, what, refs, net, poly). Only a
    candidate: a project rule (.kicad_dru, netclass) can set a smaller one,
    and then a refill leaves the same gap (the hi-fi boards' 0.10 mm)."""
    out = []
    for z in bg.zones_of():
        if not z.clearance:
            continue
        for layer, polys in z.fills.items():
            if not polys:
                continue
            fill = geom._union(polys)
            items = [(f"{q.ref}.{q.number}", [q.ref], q.net, q.poly)
                     for q in bg.pads_of(layer=layer)
                     if q.kind != "np_thru_hole"]
            items += [("track", [], t.net, t.poly)
                      for t in bg.tracks_of(layer=layer)]
            items += [("via", [], v.net, v.poly)
                      for v in bg.vias_of(layer=layer)]
            for what, refs, net, poly in items:
                if z.net and net == z.net:
                    continue
                d = fill.distance(poly)
                if _below(d, z.clearance):
                    out.append((z, layer, d, what, refs, net, poly))
    return out


def check_zone_fill(bg, vios: list, fresh=None) -> str | None:
    """FAB-27: a saved zone fill that a refill would move. Candidates come
    from the zone's own clearance (cheap, no KiCad); each is confirmed
    against a refilled copy (kicad-cli, the board's .kicad_pro/.kicad_dru
    beside it) - stale where the fresh fill sits further from the item.
    `fresh`: the refilled board's BoardGeom (tests); else one is made.
    Returns why the check could not run, or None."""
    cands = _zone_fill_candidates(bg)
    if not cands:
        return None
    if fresh is None:
        try:
            fresh = _refilled(bg.path)
        except Exception as e:  # no kicad-cli, a refill that fails
            return f"could not refill a copy to confirm {len(cands)} " \
                   f"candidate(s): {e}"
    worst: dict = {}
    fills: dict = {}
    for z, layer, d, what, refs, net, poly in cands:
        if (z.net, layer) not in fills:
            fills[z.net, layer] = fresh.zone_fill(z.net, layer)
        f = fills[z.net, layer].distance(poly)
        if f - d < ZONE_FILL_MOVE_MM:
            continue
        key = (z.zone_id, layer)
        if key not in worst or d < worst[key][1]:
            worst[key] = (z, d, f, what, refs, net, poly, layer)
    for z, d, f, what, refs, net, poly, layer in worst.values():
        c = poly.centroid
        vios.append(checklib.violation(
            CHECK, "error", (c.x, c.y), layer, z.net, refs,
            f"{z.net or 'no-net'} zone fill on {layer} is {d:.4f} mm "
            f"from {what} ({net or 'no net'}) where a refill leaves "
            f"{f:.4f} mm (zone clearance {z.clearance} mm): the saved "
            f"fill is stale - refill zones (kicad-cli pcb drc "
            f"--refill-zones --save-board) and re-export the Gerbers",
            SOURCE, kind="dfm_stale_zone_fill",
            clearance_mm=checklib.rnd(d), min_mm=checklib.rnd(f)))
    return None


def _floors(rules, key: str) -> tuple[float | None, float | None]:
    """(fail-below, warn-below) for a capability key: JLC's absolute floor
    (abs_<key>) and the design value (<key>); no absolute floor = the design
    value is the fail line and there is no warning band."""
    lo = rules.get(key)
    hard = rules.get("abs_" + key, lo)
    return hard, lo


def _band_warning(vios: list, band: list, kind: str, what: str,
                  hard: float, lo: float, unit: str = "distance_mm") -> None:
    """One warning for every value in [hard, lo): fab-buildable, under the
    floor a design is held to. band: [(value, pos, layer)]."""
    if not band:
        return
    band.sort(key=lambda b: b[0])
    worst, pos, layer = band[0]
    msg = (f"{len(band)} {what} between JLC's minimum {hard} mm and the "
           f"{lo} mm a design is held to (worst {worst:.4f} mm): JLC builds "
           "it; keep it only on purpose")
    v = checklib.violation(CHECK, "warning", pos, layer, None, [], msg,
                           SOURCE, kind=kind + "_preferred", count=len(band),
                           min_mm=hard, preferred_mm=lo,
                           **{unit: checklib.rnd(worst)})
    v["items"] = [{"msg": f"{what} {b[0]:.4f} mm",
                   "pos": [checklib.rnd(b[1][0]), checklib.rnd(b[1][1])]}
                  for b in band]
    vios.append(v)


def _castellated_at(cast, x: float, y: float, r: float = 0.0) -> bool:
    """True when (x, y) is a castellated hole (cast: castellation.holes())."""
    return any(math.hypot(x - cx, y - cy) <= max(r, d / 2.0)
               for cx, cy, d in cast)


def check_copper_to_edge(fab, rules, vios: list, cast=()) -> None:
    hard, lo = _floors(rules, "min_copper_to_edge_mm")
    outline = fab.outline
    if lo is None or outline.is_empty:
        return
    band: list = []
    edge = outline.exterior
    disks = [Point(cx, cy).buffer(d / 2.0) for cx, cy, d in cast]
    for name in fab.copper_layer_names():
        lg = fab.copper(name)
        if lg is None:
            continue
        for comp in lg.components():
            if any(comp.intersects(k) for k in disks):
                continue  # castellated pad: on the edge by design
            d = comp.distance(edge)
            # Copper crossing the outline reads as distance 0; both cases are
            # the same defect (copper too close to / past the board edge).
            p = comp.centroid
            if _below(d, hard):
                vios.append(checklib.violation(
                    CHECK, "error", (p.x, p.y), name, None, [],
                    f"copper {d:.4f} mm from board edge, JLC minimum {hard} "
                    f"mm on {name}", SOURCE, kind="dfm_copper_to_edge",
                    distance_mm=checklib.rnd(d), min_mm=hard))
            elif _below(d, lo):
                band.append((d, (p.x, p.y), name))
    _band_warning(vios, band, "dfm_copper_to_edge",
                  "copper features to the routed edge", hard, lo)


# ------------------------------------------------------------- drill checks

def check_holes(fab, rules, vios: list, cast=(), bg=None,
                cast_hh: float | None = None) -> None:
    is_via = _via_matcher(bg)
    holes = fab.holes
    hard_d, lo_d = _floors(rules, "min_hole_diameter_mm")
    if lo_d is not None:
        band: list = []
        for h in holes:
            if _below(h.diameter, hard_d):
                vios.append(checklib.violation(
                    CHECK, "error", (h.x, h.y), None, None, [],
                    f"drill {h.diameter:.4f} mm below JLC minimum {hard_d} "
                    "mm", SOURCE, kind="dfm_hole_size",
                    diameter_mm=checklib.rnd(h.diameter), min_mm=hard_d))
            elif _below(h.diameter, lo_d):
                band.append((h.diameter, (h.x, h.y), None))
        _band_warning(vios, band, "dfm_hole_size", "drills", hard_d, lo_d,
                      unit="diameter_mm")
    lo_np = rules.get("min_npth_diameter_mm")
    if lo_np is not None:
        for h in holes:
            if not h.plated and _below(h.diameter, lo_np):
                vios.append(checklib.violation(
                    CHECK, "error", (h.x, h.y), None, None, [],
                    f"non-plated drill {h.diameter:.4f} mm below JLC minimum "
                    f"{lo_np} mm", SOURCE, kind="dfm_hole_size",
                    diameter_mm=checklib.rnd(h.diameter), min_mm=lo_np,
                    plated=False))

    # JLC: 'Via hole-to-hole 0.2, pad hole-to-hole 0.45' (#554): a pair of
    # board vias gets the via figure, a pair with a pad hole the pad one, a
    # castellated hole and a plated hole the castellated block's (cast_hh);
    # two castellated holes are the castellation family's
    hard_hh, lo_hh = _floors(rules, "min_hole_to_hole_mm")
    hard_vv, lo_vv = _floors(rules, "min_via_hole_to_hole_mm")
    if lo_vv is None:
        hard_vv, lo_vv = hard_hh, lo_hh
    if lo_hh is not None and len(holes) > 1:
        disks = [Point(h.x, h.y).buffer(h.diameter / 2.0, quad_segs=16)
                 for h in holes]
        via = [is_via(h) for h in holes] if is_via else [False] * len(holes)
        band = {"via": [], "pad": [], "castellated": []}
        for i, j in _pairs_within(disks, max(lo_hh, lo_vv, cast_hh or 0.0)):
            ci = _castellated_at(cast, holes[i].x, holes[i].y)
            cj = _castellated_at(cast, holes[j].x, holes[j].y)
            if ci and cj:
                continue
            both = via[i] and via[j]
            hard, lo = (hard_vv, lo_vv) if both else (hard_hh, lo_hh)
            what = "via" if both else "pad"
            if (ci or cj) and cast_hh is not None:
                hard = lo = max(hard, cast_hh)
                what = "castellated"
            d = disks[i].distance(disks[j])
            if _below(d, hard):
                vios.append(checklib.violation(
                    CHECK, "error", (holes[i].x, holes[i].y), None, None, [],
                    f"{what} hole-to-hole {d:.4f} mm below JLC minimum "
                    f"{hard} mm", SOURCE, kind="dfm_hole_to_hole",
                    distance_mm=checklib.rnd(d), min_mm=hard, holes=what,
                    other=[checklib.rnd(holes[j].x), checklib.rnd(holes[j].y)]))
            elif _below(d, lo):
                band[what].append((d, (holes[i].x, holes[i].y), None))
        _band_warning(vios, band["via"], "dfm_via_hole_to_hole",
                      "via hole pairs", hard_vv, lo_vv)
        _band_warning(vios, band["pad"], "dfm_hole_to_hole",
                      "pad hole pairs", hard_hh, lo_hh)

    lo_he = rules.get("min_hole_to_edge_mm")
    outline = fab.outline
    if lo_he is not None and not outline.is_empty:
        edge = outline.exterior
        for h in holes:
            if _castellated_at(cast, h.x, h.y):
                continue
            d = Point(h.x, h.y).buffer(h.diameter / 2.0).distance(edge)
            if _below(d, lo_he):
                vios.append(checklib.violation(
                    CHECK, "error", (h.x, h.y), None, None, [],
                    f"hole {d:.4f} mm from board edge, JLC minimum {lo_he} mm",
                    SOURCE, kind="dfm_hole_to_edge",
                    distance_mm=checklib.rnd(d), min_mm=lo_he))


def _via_holes(bg) -> list[tuple[float, float, float]]:
    """(x, y, drill) of every via on the board: the drill file has no
    via/component split gerbonara reads, the board does."""
    if bg is None:
        return []
    return [(v.at[0], v.at[1], v.drill) for v in bg.vias_of()]


def _via_matcher(bg):
    """-> is_via(hole) for the drill file's holes (None with no board)."""
    vias = _via_holes(bg)
    if not vias:
        return None
    vtree = STRtree([Point(x, y) for x, y, _ in vias])

    def is_via(h) -> bool:
        for idx in vtree.query(Point(h.x, h.y).buffer(0.01)):
            x, y, d = vias[int(idx)]
            if abs(x - h.x) < 0.01 and abs(y - h.y) < 0.01 \
                    and abs(d - h.diameter) < 0.02:
                return True
        return False
    return is_via


# JLC's via price tiers (pcb-capabilities, 2026-11-01): a hole under 0.2
# costs more; a 0.2/0.25 hole with a via diameter under 0.45 costs more
JLC_VIA_TIER = (0.2, 0.25, 0.45)


def check_via_sizes(rules, vios: list, bg) -> None:
    """Board vias against JLC's via floors and its price tiers (#554):
    under abs_min_via_drill/diameter is an error; '0.1/0.15 mm hole with any
    via diameter, and 0.2/0.25 mm hole with via diameter < 0.45 cost more'
    is one warning per via size naming the tier (the order needs that via
    option). Rows with no abs_ floor (other fabs) are not checked here."""
    if bg is None or "abs_min_via_drill_mm" not in rules:
        return
    hard_dr, lo_dr = _floors(rules, "min_via_drill_mm")
    hard_di, lo_di = _floors(rules, "min_via_diameter_mm")
    tier: dict = {}
    for v in bg.vias_of():
        if v.kind not in ("through", None, ""):
            continue
        dr, di = v.drill, v.diameter
        if _below(dr, hard_dr) or _below(di, hard_di):
            vios.append(checklib.violation(
                CHECK, "error", v.at, None, v.net, [],
                f"via {di:.3f}/{dr:.3f} mm under JLC's minimum via "
                f"{hard_di}/{hard_dr} mm", SOURCE, kind="dfm_via_size",
                diameter_mm=checklib.rnd(di), drill_mm=checklib.rnd(dr),
                min_mm=[hard_di, hard_dr]))
            continue
        t_drill, t_small, t_dia = JLC_VIA_TIER
        if _below(dr, t_drill):
            kind, why = "dfm_via_cost_tier", (
                f"a hole under {t_drill} mm costs more at JLC - pick the "
                "matching via option when ordering, or grow them")
        elif dr < t_small + GEOM_TOL_MM and _below(di, t_dia):
            kind, why = "dfm_via_cost_tier", (
                f"a {t_drill}/{t_small} mm hole with a via diameter under "
                f"{t_dia} mm costs more at JLC - pick the matching via "
                "option when ordering, or grow them")
        elif _below(dr, lo_dr) or _below(di, lo_di):
            kind, why = "dfm_via_size_preferred", (
                f"under the {lo_di}/{lo_dr} mm via a design is held to; "
                "JLC builds it at its standard price")
        else:
            continue
        tier.setdefault((kind, round(di, 3), round(dr, 3), why),
                        []).append(v)
    for (kind, di, dr, why), vs in sorted(tier.items()):
        v = checklib.violation(
            CHECK, "warning", vs[0].at, None, None, [],
            f"{len(vs)} via(s) {di}/{dr} mm: {why}", SOURCE,
            kind=kind, count=len(vs), diameter_mm=di, drill_mm=dr)
        v["items"] = [{"msg": f"via {di}/{dr} mm",
                       "pos": [checklib.rnd(x.at[0]), checklib.rnd(x.at[1])]}
                      for x in vs]
        vios.append(v)


def check_annular_ring(fab, rules, vios: list, cast=(), bg=None) -> None:
    """Radial copper around each plated hole, measured on the OUTER layers
    (where the ring is thinnest after registration). A via is held to the
    via ring (min_annular_ring_mm), every other plated hole to the PTH
    component ring (min_pth_annular_ring_mm; the via value when the vendor
    publishes only one) - JLC: 0.18 2-layer / 0.15 multilayer (#326)."""
    hard_via, lo_via = _floors(rules, "min_annular_ring_mm")
    lo_pth = rules.get("min_pth_annular_ring_mm", lo_via)
    if lo_via is None and lo_pth is None:
        return
    is_via = _via_matcher(bg) or (lambda h: False)
    band: list = []
    outer = [n for n in ("F.Cu", "B.Cu") if n in fab.copper_files]
    pads_by_layer = {}
    for name in outer:
        lg = fab.copper(name)
        if lg is None:
            continue
        # Flashes ONLY. A zone fill is a keyhole ring whose exterior runs right
        # past each via's antipad, so counting pours as "the pad around the
        # hole" measures the antipad gap and invents a ~1 mil ring shortfall.
        polys = lg.pads
        pads_by_layer[name] = (polys, STRtree(polys) if polys else None)

    for h in fab.holes:
        if not h.plated or _castellated_at(cast, h.x, h.y):
            continue  # a half-hole's ring is castellation's to measure
        c = Point(h.x, h.y)
        r = h.diameter / 2.0
        best = None
        for name, (polys, tree) in pads_by_layer.items():
            if tree is None:
                continue
            # ALL flashes containing the hole center, UNIONED: overlapping
            # copper only ever ADDS ring. Measuring against each flash alone
            # false-positived on vias deliberately tangent to the SMD pad
            # they stitch (S14: hole 0.055 mm inside a neighbour pad's edge
            # -> phantom -0.095 ring while the via's own pad gave 0.15).
            containing = [polys[int(idx)] for idx in tree.query(c)
                          if polys[int(idx)].contains(c)]
            if not containing:
                continue
            merged = containing[0] if len(containing) == 1 \
                else unary_union(containing)
            ring = merged.exterior.distance(c) - r
            if best is None or ring < best[0]:
                best = (ring, name)
        if best is None:
            continue  # no pad around it on an outer layer (NPTH / inner-only)
        ring, name = best
        via = is_via(h)
        lo = hard_via if via else lo_pth
        if via and lo is not None and not _below(ring, lo) \
                and _below(ring, lo_via):
            band.append((ring, (h.x, h.y), name))
            continue
        if lo is not None and _below(ring, lo):
            what = "via" if via else "PTH pad"
            vios.append(checklib.violation(
                CHECK, "error", (h.x, h.y), name, None, [],
                f"{what} annular ring {ring:.4f} mm below JLC minimum "
                f"{lo} mm", SOURCE, kind="dfm_annular_ring",
                ring_mm=checklib.rnd(ring), min_mm=lo, hole=what,
                drill_mm=checklib.rnd(h.diameter)))
    _band_warning(vios, band, "dfm_annular_ring", "via rings", hard_via,
                  lo_via, unit="ring_mm")


# -------------------------------------------------------- silk / mask checks

def check_silk(fab, rules, vios: list, bg=None) -> None:
    lo_w = rules.get("min_silk_width_mm")
    for side in ("F", "B"):
        lg = fab.silk(side)
        if lg is None:
            continue
        name = f"{side}.Silkscreen"
        if lo_w is not None:
            thin = [(ls, w) for ls, w in lg.trace_lines if w + 1e-9 < lo_w]
            if thin:
                narrowest = min(w for _, w in thin)
                vios.append(checklib.violation(
                    CHECK, "warning", _mid(thin[0][0]), name, None, [],
                    f"{len(thin)} silk strokes below JLC minimum width "
                    f"{lo_w} mm (narrowest {narrowest:.4f} mm) on {name}",
                    SOURCE, kind="dfm_silk_width",
                    width_mm=checklib.rnd(narrowest), min_mm=lo_w,
                    count=len(thin)))

        mask = fab.mask(side)
        if mask is None:
            continue
        openings = mask.union()
        silk = lg.union()
        if openings.is_empty or silk.is_empty:
            continue
        inter = silk.intersection(openings)
        if inter.is_empty:
            continue
        parts = list(inter.geoms) if inter.geom_type.startswith("Multi") \
            else [inter]
        for part in parts:
            if part.area < MIN_SILK_OVERLAP_MM2:
                continue
            p = part.representative_point()
            pos, refs = (p.x, p.y), []
            if bg is not None:
                hit = _pad_at(bg, p.x, p.y, side)
                if hit is not None:
                    refs = [hit.ref]
                    pos = hit.center
            sev = ("error" if part.area >= SILK_OVERLAP_ERROR_MM2
                   else "warning")
            note = ("" if sev == "error"
                    else " (sliver; fab auto-clips silk at mask openings)")
            vios.append(checklib.violation(
                CHECK, sev, pos, name, None, refs,
                f"silkscreen printed over a solder-mask opening "
                f"({part.area:.4f} mm2) on {name}"
                + (f" - pad of {refs[0]}" if refs else "") + note,
                SOURCE, kind="dfm_silk_over_pad",
                overlap_mm2=checklib.rnd(part.area),
                at=[checklib.rnd(p.x), checklib.rnd(p.y)]))


def _pad_at(bg, x: float, y: float, side: str):
    """The board pad whose copper covers (x, y) on that side, if any."""
    layer = "F.Cu" if side == "F" else "B.Cu"
    pt = Point(x, y)
    best = None
    for pad in bg.pads_of(layer=layer):
        try:
            poly = pad.poly
        except Exception:
            continue
        if poly.contains(pt) or poly.distance(pt) < 0.05:
            d = poly.distance(pt)
            if best is None or d < best[0]:
                best = (d, pad)
    return best[1] if best else None


def check_mask_dam(fab, rules, vios: list) -> None:
    lo = rules.get("min_solder_mask_dam_mm")
    if lo is None:
        return
    for side in ("F", "B"):
        mask = fab.mask(side)
        if mask is None:
            continue
        comps = mask.components()
        thin = []
        for i, j in _pairs_within(comps, lo):
            d = comps[i].distance(comps[j])
            if EPS_TOUCH < d and _below(d, lo):
                thin.append((d, comps[i].centroid))
        if thin:
            d, c = min(thin, key=lambda t: t[0])
            vios.append(checklib.violation(
                CHECK, "warning", (c.x, c.y), f"{side}.Mask", None, [],
                f"{len(thin)} solder-mask dams below {lo} mm "
                f"(narrowest {d:.4f} mm) on {side}.Mask", SOURCE,
                kind="dfm_mask_dam", dam_mm=checklib.rnd(d), min_mm=lo,
                count=len(thin)))


def check_pad_tented(fab, vios: list) -> None:
    """A solder-PASTE aperture whose pad has no solder-mask opening: the pad
    is tented under mask, the stencil deposits paste ON the mask, and the
    part cannot be soldered (JLCDFM's 'pad covered by solder mask' class -
    previously only discoverable at P10's API audit / human browser step).

    Gating on paste flashes (stencil aperture = assembly intent) keeps tented
    vias, paste-less test points and fiducials out of scope by construction:
    they have no paste aperture. Registration slop is irrelevant - the test
    is intersection-empty, not a clearance."""
    for side in ("F", "B"):
        paste = fab.paste(side)
        mask = fab.mask(side)
        if paste is None or mask is None:
            continue
        openings = mask.union()
        # One aperture per connected component: KiCad's RoundRect macro
        # decomposes into several primitives per flash, and reporting each
        # would turn one tented pad into nine violations.
        for comp in paste.components():
            if not comp.intersection(openings).is_empty:
                continue
            pt = comp.representative_point()
            vios.append(checklib.violation(
                CHECK, "error", (pt.x, pt.y), f"{side}.Paste", None, [],
                f"solder-paste aperture ({comp.area:.4f} mm2) with no "
                f"solder-mask opening on {side} - pad is tented and cannot "
                f"be assembled", SOURCE, kind="dfm_pad_tented",
                paste_mm2=checklib.rnd(comp.area)))


# ----------------------------------------------------------- CPL polarity

def _angle(cx, cy, x, y) -> float:
    return math.degrees(math.atan2(y - cy, x - cx)) % 360.0


def check_polarity(bg, netlist: dict, vios: list) -> dict:
    """Board pad->net vs schematic pin->net, PER PAD NUMBER.

    kicad's --schematic-parity compares net membership, so a polarized part
    rotated 180 deg with its pad nets swapped stays invisible to it (V9). Here
    a swap is a per-pad mismatch, and when the mismatched nets form a clean
    permutation of the expected ones the pad geometry yields the rotation the
    part is off by - which is what the CPL would ship.
    """
    expected: dict[tuple[str, str], str] = {}
    for name, members in netlist.get("nets", {}).items():
        for m in members:
            expected[(m["ref"], str(m["pin"]))] = name

    by_ref: dict[str, list] = {}
    for pad in bg.pads_of():
        if pad.net is None:
            continue
        by_ref.setdefault(pad.ref, []).append(pad)

    checked = 0
    for ref, pads in sorted(by_ref.items()):
        want = {p: n for (r, p), n in expected.items() if r == ref}
        if not want:
            continue  # not an assembled part (mounting hole, board_only)
        checked += 1
        got = {}
        for pad in pads:
            got.setdefault(pad.number, set()).add(pad.net)
        mism = [(num, sorted(nets)[0], want[num])
                for num, nets in sorted(got.items())
                if num in want and want[num] not in nets]
        if not mism:
            continue

        # Where does each expected net actually sit? A consistent non-identity
        # mapping = the part is rotated/mounted wrong, not miswired.
        pos = {}
        for pad in pads:
            pos.setdefault(pad.number, pad.center)
        cx = sum(p[0] for p in pos.values()) / len(pos)
        cy = sum(p[1] for p in pos.values()) / len(pos)
        deltas, permuted = [], True
        for num, _actual, wanted in mism:
            holder = [p for p in pads if p.net == wanted]
            if len(holder) != 1 or num not in pos:
                permuted = False
                break
            a = _angle(cx, cy, *pos[num])
            b = _angle(cx, cy, holder[0].center[0], holder[0].center[1])
            deltas.append((b - a) % 360.0)
        rot = None
        if permuted and deltas:
            spread = max(deltas) - min(deltas)
            if spread < 5.0 or spread > 355.0:
                rot = round(sum(deltas) / len(deltas), 1)

        detail = ", ".join(f"pad {n}: board '{a}' vs schematic '{w}'"
                           for n, a, w in mism)
        if rot is not None:
            msg = (f"{ref} pad nets are rotated {rot:g} deg from the schematic "
                   f"- the part would be assembled backwards ({detail})")
            kind = "cpl_polarity"
        else:
            msg = f"{ref} pad nets disagree with the schematic ({detail})"
            kind = "pad_net_mismatch"
        vios.append(checklib.violation(
            CHECK, "error", (cx, cy), None, None, [ref], msg, SOURCE,
            kind=kind, rotation_delta_deg=rot,
            mismatches=[{"pad": n, "board": a, "schematic": w}
                        for n, a, w in mism]))
    return {"refs_checked": checked}


# ------------------------------------------------------------ placement

def _shipped_file(fab_dir: Path | None, name: str) -> Path | None:
    """`name` in the fab dir (or its parent when --fab-dir is gerbers/)."""
    if fab_dir is None:
        return None
    for d in (fab_dir, fab_dir.parent):
        if (d / name).is_file():
            return d / name
    return None


def check_placement(pcb: Path, cpl: Path, parts: Path | None,
                    visual: Path | None, cache: Path | None, fetch: bool,
                    vios: list) -> dict:
    import cpl_verify
    rep = cpl_verify.verify(
        pcb, cpl_verify.read_cpl(cpl), cpl_verify.lcsc_map(pcb, parts),
        cache or cpl_verify.default_cache(parts), fetch=fetch)
    vis = (json.loads(visual.read_text(encoding="utf-8"))
           if visual is not None else None)
    rows = cpl_verify.merge_visual(rep["parts"], vis)
    unfetched = [r for r in rows if r["verdict"] == "fetch_failed"]
    if unfetched:
        # One finding for the run, not one per part: the cause is the
        # network or EasyEDA's rate limit, and so is the remedy.
        import easyeda
        why = easyeda.rate_limited() or "; ".join(sorted(
            {r.get("fetch_error", "") for r in unfetched} - {""}))
        refs = [r["ref"] for r in unfetched]
        vios.append(checklib.violation(
            CHECK, "error", None, None, None, refs,
            f"could not fetch the LCSC model for {len(refs)} part(s) "
            f"({why}), so their CPL rotation is unverified - not a pin-1 "
            f"result; rerun once EasyEDA answers (a rate limit clears in "
            f"minutes) to fill {rep['cache_dir']}", SOURCE,
            kind="cpl_fetch_failed", reason=why,
            rate_limited=bool(easyeda.rate_limited())))
    for r in rows:
        where = None
        if r["verdict"] == "fetch_failed":
            pass
        elif r["verdict"] in cpl_verify.FAIL_VERDICTS:
            kind = ("cpl_no_model" if r["verdict"] in
                    ("no_model", "no_fit", "bottom_unverified")
                    else "cpl_rotation")
            vios.append(checklib.violation(
                CHECK, "error", where, None, None, [r["ref"]],
                f"{r['ref']} {r['verdict']}: {r.get('why', '')}", SOURCE,
                kind=kind, verdict=r["verdict"], lcsc=r.get("lcsc"),
                cpl_rot=r.get("cpl_rot"), expected_rot=r.get("expected_rot")))
        elif r.get("offset_mm", 0) > cpl_verify.OFFSET_WARN_MM:
            vios.append(checklib.violation(
                CHECK, "warning", where, None, None, [r["ref"]],
                f"{r['ref']}: the LCSC model's pad centre sits "
                f"{r['offset_mm']} mm from the board's at the CPL position",
                SOURCE, kind="cpl_offset", offset_mm=r["offset_mm"]))
        if r["agreement"] in ("disagree", "missing", "unclear"):
            sev = "warning" if r["agreement"] == "unclear" else "error"
            vios.append(checklib.violation(
                CHECK, sev, where, None, None, [r["ref"]],
                f"{r['ref']}: script says {r['verdict']}, image pass says "
                f"{r['visual'] or 'nothing'} ({r['agreement']})", SOURCE,
                kind="cpl_visual_disagree", agreement=r["agreement"]))
    return {"cpl": str(cpl), "visual": str(visual) if visual else None,
            "n_checked": rep["n_checked"], "failed": rep["failed"],
            "fetch_failed": [r["ref"] for r in unfetched],
            "rate_limited": rep.get("rate_limited"),
            "parts": [{k: r.get(k) for k in
                       ("ref", "lcsc", "verdict", "basis", "board_rot",
                        "cpl_rot", "expected_rot", "offset_mm", "why",
                        "fetch_error",
                        "visual", "agreement")} for r in rows]}


# ------------------------------------------------------------ release gate

def check_outline(fab, vios: list) -> None:
    """An Edge.Cuts file that does not polygonize (open contour) silently
    yields an EMPTY outline, and both edge-distance checks early-return on
    it - so an unclosable outline used to disable copper-to-edge AND
    hole-to-edge without a trace. Make the silence an error."""
    if fab.edge_file is not None and fab.outline.is_empty:
        vios.append(checklib.violation(
            CHECK, "error", None, "Edge.Cuts", None, [],
            "Edge.Cuts present but does not form a closed outline - "
            "copper/hole edge-distance checks cannot run",
            SOURCE, kind="dfm_open_outline"))


def _shipped_designators(fab_dir: Path) -> dict[str, set[str]]:
    """{'BOM.csv': {refs}, 'CPL.csv': {refs}} for the files actually in the
    package (as opposed to the ones this run would generate)."""
    out: dict[str, set[str]] = {}
    for fname in ("BOM.csv", "CPL.csv"):
        path = fab_dir / fname
        if not path.is_file():
            continue
        refs: set[str] = set()
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    cell = (row.get("Designator") or "")
                    refs |= {d.strip() for d in cell.split(",") if d.strip()}
        except (OSError, csv.Error):
            continue
        out[fname] = refs
    return out


def check_release(fab, expected_cu: list[str], vios: list,
                  parts_report: dict | None, fab_dir: Path | None = None) -> dict:
    """Fab-release completeness (SPEC P8 table): every layer present, drill
    valid, BOM complete, and the shipped population equal to the declared one.

    `expected_cu` is the BOARD's declared copper layer set (fab_export.
    copper_layers, pure text scan) - never derived from the files being
    audited. Counting the package's own files was circular: a 4-layer export
    that dropped both inner gerbers read as a valid 2-layer package, no
    missing-layer error, and the 2-layer capability table applied."""
    facts = {}
    missing = []
    have = set(fab.copper_files)
    want_cu = set(expected_cu)
    for lyr in sorted(want_cu - have):
        missing.append(lyr)
    for side in ("F", "B"):
        if side not in fab.mask_files:
            missing.append(f"{side}.Mask")
        if side not in fab.silk_files:
            missing.append(f"{side}.Silkscreen")
    if fab.edge_file is None:
        missing.append("Edge.Cuts")
    if missing:
        msg = f"fab package is missing layer(s): {', '.join(missing)}"
        cu_missing = sorted(want_cu - have)
        if cu_missing:
            # Name both sets so a deliberately curated --layers export can be
            # recognized (and waived) by a human instead of guessed at.
            msg += (f" - board declares copper [{', '.join(expected_cu)}], "
                    f"package has [{', '.join(sorted(have))}]")
        vios.append(checklib.violation(
            CHECK, "error", None, None, None, [], msg,
            SOURCE, kind="dfm_missing_layer", layers=missing,
            expected_copper=list(expected_cu), found_copper=sorted(have)))
    facts["layers_present"] = sorted(have) + \
        [f"{s}.Mask" for s in sorted(fab.mask_files)] + \
        [f"{s}.Silkscreen" for s in sorted(fab.silk_files)]

    if not fab.drill_files:
        vios.append(checklib.violation(
            CHECK, "error", None, None, None, [],
            "fab package has no drill file", SOURCE, kind="dfm_no_drill"))
    facts["n_holes"] = len(fab.holes)

    if parts_report is not None:
        # Class-aware (U3/codex H1): a machine-placed part nobody can BUY is an
        # ERROR. A machine-placed part that is sourced but not from LCSC is a
        # WARNING - JLC cannot fit it, which only bites on a PCBA build (a
        # hand-built board legitimately buys from DigiKey). An off-board,
        # hand-installed, customer-supplied or DNP part needs no number at all:
        # recorded, not reported.
        missing_lcsc = parts_report.get("missing_lcsc") or []
        unsourced = parts_report.get("unsourced") or missing_lcsc
        off_lcsc = parts_report.get("off_lcsc") or []
        facts["missing_lcsc"] = missing_lcsc
        facts["unsourced"] = unsourced
        facts["off_lcsc"] = off_lcsc
        facts["missing_lcsc_unplaced"] = \
            parts_report.get("missing_lcsc_unplaced") or []
        facts["assembly_class_counts"] = parts_report.get("class_counts") or {}
        facts["not_placed"] = parts_report.get("not_placed") or []
        if unsourced:
            vios.append(checklib.violation(
                CHECK, "error", None, None, None, sorted(unsourced),
                f"{len(unsourced)} machine-placed part(s) have no LCSC number "
                f"and no distributor line in the BOM", SOURCE,
                kind="dfm_bom_incomplete", refs_missing=sorted(unsourced)))
        if off_lcsc:
            vios.append(checklib.violation(
                CHECK, "warning", None, None, None, sorted(off_lcsc),
                f"{len(off_lcsc)} machine-placed part(s) are sourced off LCSC "
                f"- JLC cannot fit them; supply them, substitute an LCSC part "
                f"or reclassify as hand_install", SOURCE,
                kind="dfm_bom_off_lcsc", refs_off_lcsc=sorted(off_lcsc)))
        for kind in ("unplaced_smt", "qty_mismatch"):
            for item in parts_report.get(kind) or []:
                refs = [item] if isinstance(item, str) else item.get("refs", [])
                vios.append(checklib.violation(
                    CHECK, "error", None, None, None, sorted(refs),
                    (f"{item} is classed for assembly (smt_placed/tht) "
                     f"but has no placement"
                     if kind == "unplaced_smt" else
                     f"{item.get('lcsc') or item.get('mpn')}: parts.json "
                     f"declares {item.get('declared_populated')} populated, "
                     f"the assembly classes give "
                     f"{item.get('derived_populated')}"),
                    SOURCE, kind=f"dfm_assembly_{kind}"))

        # C9: the shipped package must not tell the assembler to fit a part the
        # declared variant leaves out. rf-de-20m's nine DNP sites are the
        # known-answer - three of them undo the ZVS fix if populated.
        unplaced = {e["ref"] for e in (parts_report.get("not_placed") or [])}
        if unplaced and fab_dir is not None:
            for fname, refs in _shipped_designators(Path(fab_dir)).items():
                leaked = sorted(refs & unplaced)
                if leaked:
                    vios.append(checklib.violation(
                        CHECK, "error", None, None, None, leaked,
                        f"shipped {fname} lists {len(leaked)} part(s) the "
                        f"assembly classes exclude from placement: "
                        f"{', '.join(leaked)}", SOURCE,
                        kind="dfm_unplaced_in_package", file=fname))
    return facts


# ------------------------------------------------------------- netlist

def _shipped_netlist(gdir: Path) -> Path | None:
    for d in (gdir, gdir.parent):
        hits = sorted(p for p in d.glob("*") if p.is_file()
                      and p.suffix.lower() in (".ipc", ".d356"))
        if hits:
            return hits[0]
    return None


def check_netlist(fab, pcb: Path, bg, gdir: Path, tmp: Path,
                  vios: list) -> tuple[dict, str | None]:
    """Netlist compare (Valor/CAM350, a fab's E-test): the shipped copper's
    connectivity against the IPC-D-356 netlist, the package's own when it
    ships one, else kicad-cli's from the board. -> (facts, skip reason)."""
    import ipcd356
    src = _shipped_netlist(gdir)
    origin = "package"
    if src is None:
        origin = "board"
        try:
            import kc
            src = tmp / f"{pcb.stem}.ipc"
            res = kc.export_ipcd356(kc.resolve_cli(), pcb, src)
            if res.get("status") != "pass":
                return {}, ("kicad-cli ipcd356 export failed: "
                            f"{res.get('stderr_tail')}")
        except Exception as exc:  # noqa: BLE001 (no kicad-cli = coverage hole)
            return {}, f"no IPC-D-356 netlist ({type(exc).__name__}: {exc})"
    try:
        d356 = ipcd356.parse(src.read_text(encoding="utf-8", errors="replace"))
    except ipcd356.D356Error as exc:
        return {}, f"IPC-D-356 netlist unreadable: {exc}"
    clashes = ipcd356.resolve(d356, bg)
    ties = ipcd356.net_tie_classes(
        pcb.read_text(encoding="utf-8", errors="replace"), bg)
    res = ipcd356.compare(fab, d356, ties)
    for c in clashes:
        vios.append(checklib.violation(
            CHECK, "warning", None, None, None, [],
            f"IPC-D-356 net name {c['d356_net']!r} stands for "
            f"{len(c['board_nets'])} board nets ({', '.join(c['board_nets'])})"
            " - kicad-cli keeps the last 14 characters, so a tester reads "
            "them as one net and fails a good board; rename the nets",
            SOURCE, kind="dfm_netlist_name_clash", **c))
    for sh in res["shorts"]:
        x, y = sh["at"]
        near = " / ".join(f"{p['ref']}-{p['pin']}".rstrip("-")
                          for p in sh["near"])
        vios.append(checklib.violation(
            CHECK, "error", (x, y), sh["island"]["layer"], None,
            sorted({p["ref"] for p in sh["near"]} - {"VIA"}),
            f"short in the shipped copper: nets {', '.join(sh['nets'])} are "
            f"one conductor (nearest test points {near})", SOURCE,
            kind="dfm_net_short", nets=sh["nets"], points=sh["points"],
            island=sh["island"]))
    for op in res["opens"]:
        x, y = op["at"]
        if "no_copper" in op:
            tp = op["no_copper"]
            msg = (f"open in the shipped copper: net {op['net']} test point "
                   f"{tp['ref']}-{tp['pin']} lands on no copper")
            refs = [tp["ref"]]
        else:
            msg = (f"open in the shipped copper: net {op['net']} is "
                   f"{op['n_fragments']} pieces "
                   f"({'; '.join(', '.join(f[:4]) for f in op['fragments'][:4])})")
            refs = sorted({lbl.split("-")[0] for f in op["fragments"]
                           for lbl in f} - {"VIA"})
        vios.append(checklib.violation(
            CHECK, "error", (x, y), None, op["net"], refs, msg, SOURCE,
            kind="dfm_net_open", fragments=op["fragments"]))
    return {"netlist": {
        "source": origin, "file": src.name, "test_points": res["n_points"],
        "nets": res["n_nets"], "plated_holes": res["n_plated_holes"],
        "islands": res["n_islands"], "shorts": len(res["shorts"]),
        "opens": len(res["opens"]), "name_clashes": len(clashes)}}, None


# ------------------------------------------------------------------ driver

# Coverage families (U2, codex C7) -> the violation kinds they emit. A family
# that never ran lands in coverage.skipped_error; --strict turns any such
# hole into status "error" (the carrier's P9 "dfm pass with edge checks
# silently skipped" must read as a refusal, not a pass).
DFM_FAMILIES = {
    "copper": ("dfm_trace_width", "dfm_clearance"),
    "copper_to_edge": ("dfm_copper_to_edge",),
    "zone_fill": ("dfm_stale_zone_fill",),
    "drill": ("dfm_hole_size", "dfm_hole_to_hole", "dfm_annular_ring"),
    "hole_to_edge": ("dfm_hole_to_edge",),
    "silk": ("dfm_silk_width", "dfm_silk_over_pad", "dfm_mask_dam",
             "dfm_pad_tented"),
    "polarity": ("cpl_polarity",),
    "placement": ("cpl_rotation", "cpl_no_model", "cpl_fetch_failed",
                  "cpl_offset"),
    "placement_visual": ("cpl_visual_disagree",),
    "bom": ("dfm_bom_incomplete", "dfm_assembly_unplaced_smt",
            "dfm_assembly_qty_mismatch", "dfm_unplaced_in_package"),
    "release": ("dfm_open_outline", "dfm_missing_layer", "dfm_no_drill"),
    "castellation": castellation.KINDS + ("castellated_rules_unknown",),
    "netlist": ("dfm_net_short", "dfm_net_open", "dfm_netlist_name_clash"),
}


def _resolve_netlist(pcb: Path, schematic: Path | None,
                     netlist: Path | None, tmp: Path):
    """-> (parsed netlist dict | None, reason-if-skipped)."""
    import netlist_audit
    if netlist is not None:
        return netlist_audit.parse_netlist(netlist), None
    sch = schematic
    if sch is None:
        cand = pcb.with_suffix(".kicad_sch")
        sch = cand if cand.exists() else None
    if sch is None:
        return None, "no schematic or netlist available"
    import kc
    out = tmp / "polarity.net"
    res = kc.export_netlist(kc.resolve_cli(), Path(sch), out)
    if res.get("status") != "pass":
        return None, f"netlist export failed: {res.get('stderr') or ''}"[:200]
    return netlist_audit.parse_netlist(out), None


def run(pcb: Path, fab_dir: Path | None = None, copper_oz: float | None = None,
        capabilities: Path | None = None, schematic: Path | None = None,
        netlist: Path | None = None, polarity: bool = True,
        parts: Path | None = None, skip: tuple[str, ...] = (),
        strict: bool = False, cpl: Path | None = None,
        visual: Path | None = None, easyeda_cache: Path | None = None,
        fetch_models: bool | None = None,
        vendor: str | None = None) -> dict:
    if not pcb.exists():
        raise CheckError(f"board not found: {pcb}")
    fab_name = "JLC"
    if vendor:
        # a vendor profile (reference/vendors/<id>.yaml, docs/vendors.md):
        # its minimums replace JLC's; --capabilities still wins if both given
        import vendors
        try:
            prof = vendors.load(vendor)
        except vendors.VendorError as exc:
            raise CheckError(str(exc)) from exc
        capabilities = capabilities or Path(prof["capabilities_file"])
        if vendor != vendors.DEFAULT:
            fab_name = prof["meta"].get("name") or vendor
    caps = load_capabilities(capabilities or CAPABILITIES)
    if copper_oz is None:
        oz, oz_source = derive_copper_oz(pcb)
    else:
        oz, oz_source = float(copper_oz), "cli"

    # The BOARD's declared copper set (pure text scan) is the truth the
    # package is audited AGAINST - both for completeness and for the
    # capability key. Never count the files being checked.
    import fab_export
    expected_cu = fab_export.copper_layers(pcb)
    n_layers = len(expected_cu)

    with tempfile.TemporaryDirectory(prefix="aiee_dfm_") as td:
        tmp = Path(td)
        if fab_dir is None:
            man = fab_export.run(pcb, tmp / "fab", make_zip=False)
            gdir = Path(man["gerber_dir"])
        else:
            gdir = Path(fab_dir)
            if (gdir / "gerbers").is_dir():
                gdir = gdir / "gerbers"
        fab = gerblib.open_fab(gdir)
        if not fab.copper_files:
            raise CheckError(f"no copper gerbers found in {gdir}")
        # The laminate, from the board's own stackup block: a Rogers board
        # is checked against JLC's high-frequency floors, and a material
        # JLC doesn't build (RO4003C, a Rogers + FR-4 hybrid) is an error
        # here, at the package, not at the quote page.
        vios: list = []
        material = fabmaterial.board_family(pcb)
        mat_refusal = fabmaterial.not_offered(material)
        if mat_refusal:
            vios.append(checklib.violation(
                CHECK, "error", None, None, None, [],
                f"board laminate {material}: {mat_refusal}", SOURCE,
                kind="dfm_material_not_offered", material=material))
            rules_material = fabmaterial.FR4
        else:
            rules_material = material
        if oz_source == "stackup" and rule_key(
                n_layers, oz, rules_material) not in caps["design_rules"]:
            oz, oz_source = 1.0, "default"  # unknown key: today's behavior
        key, rules = pick_rules(caps, n_layers, oz, rules_material)

        bg = geom.load_board(pcb)
        facts: dict = {}
        if not mat_refusal and material != fabmaterial.FR4:
            # what the material's product allows, from the board alone
            # (finish/colour/thickness are order options: order_submit)
            spec = {"layers": n_layers, "copper_weight_oz": oz}
            if bg.outline is not None and not bg.outline.is_empty:
                x0, y0, x1, y1 = bg.outline.bounds
                spec.update(width_mm=x1 - x0, height_mm=y1 - y0)
            for msg in fabmaterial.order_problems(material, spec):
                vios.append(checklib.violation(
                    CHECK, "error", None, None, None, [], msg, SOURCE,
                    kind="dfm_material_option", material=material))
        cast = castellation.holes(bg)

        check_outline(fab, vios)
        if "copper" not in skip:
            check_trace_width(fab, rules, vios)
            check_clearance(fab, rules, vios)
            check_copper_to_edge(fab, rules, vios, cast)
        zone_fill_skipped = None
        if "zone_fill" not in skip:
            zone_fill_skipped = check_zone_fill(bg, vios)
        if "drill" not in skip:
            check_holes(fab, rules, vios, cast, bg=bg,
                        cast_hh=(caps.get("castellated") or {}).get(
                            "min_hole_to_hole_mm"))
            check_annular_ring(fab, rules, vios, cast, bg=bg)
            check_via_sizes(rules, vios, bg)
        if "castellation" not in skip and "castellated" not in caps:
            # a vendor that publishes no castellation rules: nothing to
            # measure against, so a castellated board can't pass there
            if cast:
                vios.append(checklib.violation(
                    CHECK, "error", None, None, None,
                    sorted(castellation.refs(pcb)),
                    f"the board has castellated pads and {fab_name} "
                    "publishes no castellation rules: ask them, or pick a "
                    "fab that does", SOURCE,
                    kind="castellated_rules_unknown"))
        elif "castellation" not in skip:
            cvios, cfacts = castellation.check(
                bg, castellation.load_rules(capabilities or CAPABILITIES),
                bg.stackup.total_thickness or None)
            vios.extend(cvios)
            facts.update(cfacts)
        if "silk" not in skip:
            check_silk(fab, rules, vios, bg=bg)
            check_mask_dam(fab, rules, vios)
            check_pad_tented(fab, vios)

        netlist_skipped = None
        if "netlist" in skip:
            netlist_skipped = "skipped via --skip"
        else:
            nfacts, netlist_skipped = check_netlist(fab, pcb, bg, gdir, tmp,
                                                    vios)
            facts.update(nfacts)

        parts_report = None
        if parts is not None:
            import bom_cpl
            parts_report = bom_cpl.run(pcb, tmp / "bom", parts_json=parts,
                                       stock=False)
        facts.update(check_release(fab, expected_cu, vios, parts_report,
                                   fab_dir=Path(fab_dir) if fab_dir else None))

        if parts_report is not None and "placement" not in skip:
            import cpl_verify
            fd = Path(fab_dir) if fab_dir else None
            cpl_path = (cpl or _shipped_file(fd, "CPL.csv")
                        or Path(parts_report["cpl"]))
            vis = visual or _shipped_file(fd, "cpl_visual.json")
            facts["placement"] = check_placement(
                pcb, Path(cpl_path), parts, vis, easyeda_cache,
                (not cpl_verify.offline()) if fetch_models is None
                else fetch_models, vios)

        if polarity:
            nl, reason = _resolve_netlist(pcb, schematic, netlist, tmp)
            if nl is None:
                facts["polarity"] = {"status": "skipped", "reason": reason}
            else:
                facts["polarity"] = {"status": "checked",
                                     **check_polarity(bg, nl, vios)}
        else:
            facts["polarity"] = {"status": "disabled"}

        facts.update({
            "capability_key": key,
            "material": material,
            "layer_count": n_layers,
            "copper_oz": oz,
            "copper_oz_source": oz_source,
            "gerber_dir": str(gdir),
            "min_trace_width_mm": min(
                (w for n in fab.copper_layer_names()
                 if (lg := fab.copper(n)) and lg.trace_widths
                 for w in lg.trace_widths), default=None),
            "min_hole_mm": min((h.diameter for h in fab.holes), default=None),
        })

        # U2 coverage (codex C7): record which families never ran and why.
        skipped_cov: dict[str, str] = {}
        for fam in ("copper", "drill", "silk", "castellation", "zone_fill"):
            if fam in skip:
                skipped_cov[fam] = "skipped via --skip"
        if zone_fill_skipped:
            skipped_cov["zone_fill"] = zone_fill_skipped
        if netlist_skipped:
            skipped_cov["netlist"] = netlist_skipped
        no_outline = fab.edge_file is None or fab.outline.is_empty
        edge_reason = "Edge.Cuts missing or does not form a closed outline"
        if no_outline or "copper" in skip:
            skipped_cov["copper_to_edge"] = (
                edge_reason if no_outline else "skipped via --skip")
        if no_outline or "drill" in skip:
            skipped_cov["hole_to_edge"] = (
                edge_reason if no_outline else "skipped via --skip")
        if facts["polarity"]["status"] != "checked":
            skipped_cov["polarity"] = (facts["polarity"].get("reason")
                                       or facts["polarity"]["status"])
        if parts_report is None:
            skipped_cov["bom"] = "no parts.json"
        if "placement" not in facts:
            skipped_cov["placement"] = ("skipped via --skip" if parts_report
                                        else "no parts.json")
        if not (facts.get("placement") or {}).get("visual"):
            skipped_cov["placement_visual"] = (
                "no cpl_visual.json - the fab step's image pass has not run")

    if fab_name != "JLC":
        for v in vios:
            msg = str(v.get("msg", ""))
            if "JLC minimum" in msg or "JLC's" in msg:
                v["msg"] = msg.replace("JLC minimum", f"{fab_name} minimum") \
                    .replace("JLC's", f"{fab_name}'s")
        facts["vendor"] = vendor
    payload = checklib.report("dfm_check", pcb, vios, **facts)
    # Warnings alone do not fail the gate (netlist_audit / fp_verify precedent):
    # KiCad's stock 0.12 mm silk and tight mask dams are advisory, not defects.
    payload.update(grade(vios))
    fam_of = {k: f for f, ks in DFM_FAMILIES.items() for k in ks}
    ran = [f for f in DFM_FAMILIES if f not in skipped_cov]
    failing = {fam_of.get(v.get("kind")) for v in vios
               if v.get("severity") == "error"}
    payload["coverage"] = {
        "strict": strict,
        "required": list(DFM_FAMILIES),
        "ran": ran,
        "passed": [f for f in ran if f not in failing],
        "failed": [f for f in ran if f in failing],
        "waived": [],
        "not_applicable": {},
        "skipped_error": skipped_cov,
    }
    if strict and skipped_cov:
        # A release DFM must not read "pass" (or even a graded "fail") when
        # sub-checks silently never ran - refuse instead (codex C7).
        payload["status"] = "error"
        payload["error"] = ("strict coverage failure - families never ran: "
                            + "; ".join(f"{k} ({v})"
                                        for k, v in skipped_cov.items()))
    return payload


def grade(vios: list) -> dict:
    """The run's status from its findings: pass, violations (an error), or
    error with `unverified` when the only errors are CPL models EasyEDA did
    not hand over (cpl_fetch_failed). Nothing failed then, some placements
    are unknown: a run that could not finish (exit 2), never a fail verdict
    on the board. Any real error alongside it is still `violations`."""
    errors = [v for v in vios if v["severity"] == "error"]
    if not errors:
        return {"status": "pass"}
    if any(v.get("kind") != "cpl_fetch_failed" for v in errors):
        return {"status": "violations"}
    v = errors[0]
    return {"status": "error",
            "unverified": {"family": "placement", "refs": v["refs"],
                           "reason": v.get("reason")},
            "error": f"placement unverified, not failed: {v['msg']}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True, help="input .kicad_pcb")
    ap.add_argument("--fab-dir", help="pre-exported fab dir (else export to a "
                                      "temp dir)")
    ap.add_argument("--copper-oz", type=float, default=None,
                    help="outer copper weight (capability table key); default: "
                         "auto from the board's stackup block, 1 oz if absent")
    ap.add_argument("--capabilities", help="override jlc_capabilities.yaml")
    ap.add_argument("--vendor", help="check against this vendor's minimums "
                    "(reference/vendors/<id>.yaml; default JLC's)")
    ap.add_argument("--schematic", help="schematic for the polarity oracle")
    ap.add_argument("--netlist", help="netlist for the polarity oracle")
    ap.add_argument("--no-polarity", action="store_true",
                    help="skip the CPL polarity check")
    ap.add_argument("--parts", help="parts.json (BOM completeness)")
    ap.add_argument("--cpl", help="CPL.csv to verify (default: the fab dir's, "
                    "else this run's)")
    ap.add_argument("--visual", help="cpl_visual.json from the image pass "
                    "(default: beside the CPL in the fab dir)")
    ap.add_argument("--easyeda-cache", help="LCSC footprint model cache")
    ap.add_argument("--offline", action="store_true",
                    help="never fetch LCSC models (uncached = cpl_no_model)")
    ap.add_argument("--skip", default="",
                    help="comma list of groups to skip: "
                         "copper,drill,silk,castellation,zone_fill,"
                         "netlist,placement")
    ap.add_argument("--strict", action="store_true",
                    help="release mode (codex C7): any sub-check family that "
                         "could not run (open outline, no netlist, no "
                         "parts.json, --skip) is a coverage failure - status "
                         "error / exit 2, never a pass")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    def _go():
        with warnings.catch_warnings():
            # gerbonara warns about kicad's G90-after-header drill dialect.
            warnings.simplefilter("ignore")
            rep = run(Path(args.pcb),
                      fab_dir=Path(args.fab_dir) if args.fab_dir else None,
                      copper_oz=args.copper_oz,
                      capabilities=Path(args.capabilities)
                      if args.capabilities else None,
                      schematic=Path(args.schematic) if args.schematic else None,
                      netlist=Path(args.netlist) if args.netlist else None,
                      polarity=not args.no_polarity,
                      parts=Path(args.parts) if args.parts else None,
                      skip=tuple(s for s in args.skip.split(",") if s),
                      strict=args.strict,
                      cpl=Path(args.cpl) if args.cpl else None,
                      visual=Path(args.visual) if args.visual else None,
                      easyeda_cache=Path(args.easyeda_cache)
                      if args.easyeda_cache else None,
                      fetch_models=False if args.offline else None,
                      vendor=args.vendor)
        return rep, args.out

    return checklib.cli_wrap("dfm_check", _go)


if __name__ == "__main__":
    raise SystemExit(main())
