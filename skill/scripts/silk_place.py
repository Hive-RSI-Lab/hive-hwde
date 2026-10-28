"""silk_place - refdes silk solver: pull labels to their parts, collision-free (T6 p1).

Owns the machine-verified greedy recipe that previously lived as prose
(LEARNINGS 2026-07-28 [placement][drc][silk]; re-proven at scale by the
lumina-carrier wo-silklegibility order, 116 refdes):

  1. candidates at BOTH text angles on ALL FOUR sides of the part
     (0 deg above/below + 90 deg left/right) plus the lib_refdes_norm
     default target (pad_top - 0.25 - text_height/2, local x 0) - the
     default IS the answer for most parts, deviate only on collision;
     plus the label's CURRENT spot (first, so a settled label wins ties);
  2. accept only spots check_silk passes, judged by check_silk's own
     functions and text model (rule_verdict: over_pad + attribution), so
     anything placed here passes check_silk; then score = (tier,
     min(clearance, 0.30), -own_off) where tier 2 = no other part nearer
     than the label's own pads, 1 = merely passes ("attribution beats
     closeness", LEARNINGS 2026-08-09 [silk_place][check_silk]);
  3. process the MOST CROWDED parts first (descending neighbour count
     within 4 mm) - largest-first orphans the boxed-in small caps. Every
     target's CURRENT label stays an obstacle until that target is
     decided, so an earlier label never lands on one that stays put.

Pad obstacles are the pads' MASK apertures (copper grown by the board's
pad_to_mask_clearance or a part's solder_mask_margin): KiCad's "silkscreen
clipped by solder mask" test measures silk against those. A label with no
spot that passes check_silk is reported unplaceable (residual, with the
fix: hide it and keep it on the fab layer, or shrink it) and left where it
is - never moved to a spot check_silk would flag. Each residual carries
`fix_ops`: ready place_edit set_text ops - `shrink` (to check_silk's 0.8 mm
floor, only when the label is larger; re-run silk_place on the ref after it)
and `hide` (the last resort, which check_silk lists under refdes_off_silk).

Text box: per-char advance 0.845*size + stroke, height size + stroke
(placelib.text_box, measured constants - 0.75 and 1.0 per char are both
wrong).  A refdes field's stored angle is ABSOLUTE board-frame; its stored
position is LOCAL - never add the footprint rotation to the angle.
min_silk_clearance is read from the live .kicad_pro (per-board values
differ; wo-silklegibility guidance).

Emits a place_edit-compatible {"version": 1, "ops": [move_text ...]} file
(absolute board coords) + a report {moved, residual[],
median_beyond_extent_mm before/after}.  --apply pushes the ops through
place_edit's atomic pipeline; --verify-drc then runs the REAL DRC and
reports silk-class findings (check_silk is lenient and never the oracle,
LEARNINGS 2026-07-27).  Cut from the full recipe (reported): no automatic
batch-bisect on a DRC regression - the report carries the findings and the
agent bisects.  Board-only refs (H*) and hidden texts are never touched.

Exit 0 all placed / 1 residuals or post-apply silk DRC findings / 2 error.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union
from shapely import STRtree, affinity

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import check_silk  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402
import placelib  # noqa: E402
from checklib import CheckError  # noqa: E402
from geom import _is_node, _kid, _kids, _nums, _pts, _rot, _strs, _tok  # noqa: E402

SCRIPT = "silk_place"
CLEAR_CAP = 0.30       # stop paying for clearance beyond this (recipe rule 2)
HARD_FLOOR = 0.02      # never accept a touching candidate even at min_clear 0
CROWD_RADIUS = 4.0     # neighbour radius for the crowded-first ordering
PUSHES = [i * 0.25 for i in range(13)]        # outward 0..3.0 mm
SLIDES = [0.0, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 2.0, -2.0]
SILK_LAYERS = {"F.SilkS", "B.SilkS"}


# ---------------------------------------------------------------- parsing

def _fp_abs(pos, deg, local):
    dx, dy = _rot(local[0], local[1], -deg)
    return (pos[0] + dx, pos[1] + dy)


def parse_board(pcb: Path):
    """-> (ref_texts {ref: {...}}, silk {side: [(geom, owner)]},
    mask_margin {ref: mm}).

    ref_texts: local position + ABSOLUTE angle + font of every Reference
    property; silk: footprint silk graphics + board gr_* silk items as
    absolute shapely geoms per side; mask_margin: how far each part's pad
    mask apertures grow past its copper (KiCad's clipped-by-mask test is
    silk vs those apertures)."""
    import sexpdata
    tree = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    ref_texts: dict[str, dict] = {}
    silk = {"front": [], "back": []}
    mask_margin: dict[str, float] = {}
    setup = _kid(tree, "setup")
    ptm = _kid(setup, "pad_to_mask_clearance") if setup is not None else None
    board_margin = _nums(ptm)[0] if ptm is not None and _nums(ptm) else 0.0

    def _silk_side(layer):
        return "front" if layer == "F.SilkS" else \
            "back" if layer == "B.SilkS" else None

    def _font_xy(node):
        """(size_x, size_y, thickness) - check_silk measures height off
        size_y, so the rule geometry needs both."""
        sx, sy, thick = 1.0, 1.0, 0.15
        eff = _kid(node, "effects")
        if eff is not None:
            font = _kid(eff, "font")
            if font is not None:
                s = _kid(font, "size")
                if s is not None:
                    ns = _nums(s)
                    if ns:
                        sx = ns[0]
                        sy = ns[1] if len(ns) > 1 else ns[0]
                t = _kid(font, "thickness")
                if t is not None:
                    nt = _nums(t)
                    if nt:
                        thick = nt[0]
        return sx, sy, thick

    def _font(node):
        sx, _, thick = _font_xy(node)
        return sx, thick

    def _hidden(node):
        # (hide yes) hides, (hide no) does not - test the VALUE (LEARNINGS)
        h = _kid(node, "hide")
        if h is None:
            return False
        vals = [_tok(t) for t in h[1:] if not _is_node(t)]
        return "no" not in vals

    def _graphic_geom(g, head):
        w = 0.12
        stroke = _kid(g, "stroke")
        if stroke is not None:
            wn = _kid(stroke, "width")
            if wn is not None and _nums(wn):
                w = _nums(wn)[0]
        try:
            if head in ("fp_line", "gr_line"):
                s, e = _nums(_kid(g, "start")), _nums(_kid(g, "end"))
                from shapely.geometry import LineString
                return LineString([(s[0], s[1]), (e[0], e[1])]).buffer(w / 2)
            if head in ("fp_rect", "gr_rect"):
                s, e = _nums(_kid(g, "start")), _nums(_kid(g, "end"))
                return box(min(s[0], e[0]), min(s[1], e[1]),
                           max(s[0], e[0]), max(s[1], e[1])) \
                    .exterior.buffer(w / 2)
            if head in ("fp_circle", "gr_circle"):
                c, e = _nums(_kid(g, "center")), _nums(_kid(g, "end"))
                r = math.hypot(e[0] - c[0], e[1] - c[1])
                return Point(c[0], c[1]).buffer(r + w / 2)
            if head in ("fp_poly", "gr_poly"):
                pts = _pts(_kid(g, "pts"))
                if len(pts) >= 3:
                    return Polygon(pts).buffer(w / 2)
            if head in ("fp_arc", "gr_arc"):
                import geom as _g
                s = _nums(_kid(g, "start"))
                m = _nums(_kid(g, "mid"))
                e = _nums(_kid(g, "end"))
                from shapely.geometry import LineString
                return LineString(_g._arc_points(
                    (s[0], s[1]), (m[0], m[1]), (e[0], e[1]))).buffer(w / 2)
        except (TypeError, IndexError):
            return None
        return None

    for fp in _kids(tree, "footprint"):
        at = _kid(fp, "at")
        nums = _nums(at) if at is not None else [0.0, 0.0]
        pos = (nums[0], nums[1])
        deg = nums[2] if len(nums) > 2 else 0.0
        ref = None
        for prop in _kids(fp, "property"):
            s = _strs(prop)
            if len(s) >= 2 and s[0] == "Reference":
                ref = s[1]
                pat = _kid(prop, "at")
                pn = _nums(pat) if pat is not None else [0.0, 0.0, 0.0]
                lay = _kid(prop, "layer")
                layer = _strs(lay)[0] if lay is not None and _strs(lay) \
                    else "F.SilkS"
                size, size_y, thick = _font_xy(prop)
                ref_texts[ref] = {
                    "local": (pn[0], pn[1]),
                    "deg": pn[2] if len(pn) > 2 else 0.0,   # ABSOLUTE
                    "layer": layer, "size": size, "size_y": size_y,
                    "thickness": thick, "hidden": _hidden(prop),
                }
                break
        # solder-mask aperture growth for this part's pads: the largest
        # footprint- or pad-level (solder_mask_margin) override, else the
        # board's (pad_to_mask_clearance) - conservative per part
        margins = [_nums(m)[0] for m in
                   [_kid(fp, "solder_mask_margin")]
                   + [_kid(p, "solder_mask_margin") for p in _kids(fp, "pad")]
                   if m is not None and _nums(m)]
        if ref is not None:
            mask_margin[ref] = max(margins) if margins else board_margin
        # footprint silk graphics -> absolute geoms
        for head in ("fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc"):
            for g in _kids(fp, head):
                lay = _kid(g, "layer")
                lname = _strs(lay)[0] if lay is not None and _strs(lay) else ""
                side = _silk_side(lname)
                if side is None:
                    continue
                geom_local = _graphic_geom(g, head)
                if geom_local is None or geom_local.is_empty:
                    continue
                geo = affinity.translate(
                    affinity.rotate(geom_local, -deg, origin=(0, 0)),
                    pos[0], pos[1])
                silk[side].append((geo, ref))
        # fp_text user items on silk (DIP-switch style markings)
        for t in _kids(fp, "fp_text"):
            s = _strs(t)
            if len(s) < 2:
                continue
            lay = _kid(t, "layer")
            lname = _strs(lay)[0] if lay is not None and _strs(lay) else ""
            side = _silk_side(lname)
            if side is None or _hidden(t):
                continue
            tat = _kid(t, "at")
            tn = _nums(tat) if tat is not None else [0.0, 0.0]
            tx, ty = _fp_abs(pos, deg, (tn[0], tn[1]))
            tdeg = tn[2] if len(tn) > 2 else 0.0    # absolute, like property
            size, thick = _font(t)
            silk[side].append(
                (placelib.text_box(s[1], size, thick, tx, ty, tdeg), ref))

    # board-frame silk items
    for head in ("gr_text",):
        for t in _kids(tree, head):
            s = _strs(t)
            lay = _kid(t, "layer")
            lname = _strs(lay)[0] if lay is not None and _strs(lay) else ""
            side = _silk_side(lname)
            if side is None or not s:
                continue
            tat = _kid(t, "at")
            tn = _nums(tat) if tat is not None else [0.0, 0.0]
            tdeg = tn[2] if len(tn) > 2 else 0.0
            size, thick = _font(t)
            silk[side].append(
                (placelib.text_box(s[0], size, thick, tn[0], tn[1], tdeg),
                 None))
    for head in ("gr_line", "gr_rect", "gr_circle", "gr_poly", "gr_arc"):
        for g in _kids(tree, head):
            lay = _kid(g, "layer")
            lname = _strs(lay)[0] if lay is not None and _strs(lay) else ""
            side = _silk_side(lname)
            if side is None:
                continue
            geo = _graphic_geom(g, head)
            if geo is not None and not geo.is_empty:
                silk[side].append((geo, None))
    return ref_texts, silk, mask_margin


# ---------------------------------------------------------------- geometry

def _pad_polys_abs(fp: placelib.Footprint):
    """Per-pad absolute polygons, per-pad rotation applied (bbox model)."""
    out = []
    for p in fp.pads:
        th = math.radians(p.rot)
        c, s = abs(math.cos(th)), abs(math.sin(th))
        hx = p.size[0] / 2 * c + p.size[1] / 2 * s
        hy = p.size[0] / 2 * s + p.size[1] / 2 * c
        b = box(p.local[0] - hx, p.local[1] - hy,
                p.local[0] + hx, p.local[1] + hy)
        out.append(affinity.translate(
            affinity.rotate(b, -fp.angle, origin=(0, 0)),
            fp.pos[0], fp.pos[1]))
    return out


def _current_box(ref, fp, info):
    x, y = _fp_abs(fp.pos, fp.angle, info["local"])
    return placelib.text_box(ref, info["size"], info["thickness"],
                             x, y, info["deg"])


def _pad_extent_abs(fp):
    polys = _pad_polys_abs(fp)
    return unary_union(polys) if polys else fp.extents_abs()


# ---------------------------------------------------------------- solver

def _candidates(fp, info, own_pads_local_top):
    """Deterministic candidate list [(x, y, deg)] - normalizer target first,
    then all four sides x slides x outward pushes."""
    h = info["size"] + info["thickness"]
    ext = fp.extents_abs()
    x0, y0, x1, y1 = ext.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cands = []
    if own_pads_local_top is not None:
        tx, ty = _fp_abs(fp.pos, fp.angle,
                         (0.0, own_pads_local_top - 0.25 - h / 2))
        cands.append((tx, ty, 0.0))
    for push in PUSHES:
        for slide in SLIDES:
            g = 0.1 + push
            cands.append((cx + slide, y0 - g - h / 2, 0.0))     # above
            cands.append((cx + slide, y1 + g + h / 2, 0.0))     # below
            cands.append((x0 - g - h / 2, cy + slide, 90.0))    # left
            cands.append((x1 + g + h / 2, cy + slide, 90.0))    # right
    return cands


def _clearance(boxp, obstacles, cap, tree=None):
    """min distance to obstacles (bounds-prefiltered, through `tree` - an
    STRtree over `obstacles` - when given); None on intersection."""
    bx0, by0, bx1, by1 = boxp.bounds
    if tree is not None:
        near = tree.query(box(bx0 - cap, by0 - cap, bx1 + cap, by1 + cap))
        obstacles = [obstacles[int(i)] for i in near]
    clear = cap
    for ob in obstacles:
        ox0, oy0, ox1, oy1 = ob.bounds
        if ox0 > bx1 + cap or ox1 < bx0 - cap \
                or oy0 > by1 + cap or oy1 < by0 - cap:
            continue
        if boxp.intersects(ob):
            return None
        d = boxp.distance(ob)
        if d < clear:
            clear = d
    return clear


UNPLACEABLE_FIX = ("hide this reference on silk and keep it on the fab "
                   "layer for assembly, or shrink the refdes text to 0.8 mm "
                   "(check_silk's legibility floor) and re-run - fix_ops "
                   "holds both as place_edit set_text ops")


def rule_verdict(ref, info, x, y, deg, side_pads, boxes):
    """check_silk's verdict on this refdes at (x, y, deg), from check_silk's
    own functions and text model -> (failure | None, own_off, attribution).

    Two rules: silk_over_pad (the label over a pad's mask opening) and
    silk_misattributed (too far from its own pads and too near another
    part's). A spot that fails either is never accepted, so anything this
    solver places passes check_silk."""
    g = check_silk.text_geom(ref, x, y, deg, info["size"],
                             info.get("size_y", info["size"]),
                             info["thickness"])
    att = check_silk.attribution(ref, g, boxes)
    own_off = att[0] if att is not None else 0.0
    pads, tree = side_pads
    for i in sorted(int(j) for j in tree.query(g)):
        hit, _ = check_silk.over_pad(g, pads[i])
        if hit:
            return (f"covers pad {pads[i].ref}.{pads[i].number} "
                    "(silk_over_pad)", own_off, att)
    if att is not None and att[3]:
        _, nearest_ref, nearest_d, _ = att
        return (f"{own_off:.2f} mm beyond its own pads and {nearest_d:.2f} "
                f"mm from {nearest_ref} (silk_misattributed)", own_off, att)
    return None, own_off, att


def _fix_ops(ref, info) -> dict:
    """place_edit set_text ops for an unplaceable label: `hide` always,
    `shrink` only while the label is above the silk size floor."""
    import place_edit
    min_size, _ = place_edit.silk_text_floor()
    out = {}
    if max(info["size"], info.get("size_y", info["size"])) > min_size + 1e-6:
        out["shrink"] = {"op": "set_text", "ref": ref, "field": "reference",
                         "size": min_size}
    out["hide"] = {"op": "set_text", "ref": ref, "field": "reference",
                   "hide": True}
    return out


def _unplaceable(ref, collision_free, nearest_fail, info=None):
    """Residual entry for a label with no spot that passes check_silk."""
    if not collision_free:
        why = ("no collision-free candidate within +3.0 mm push (channel "
               "narrower than the label)")
    else:
        why = (f"none of {collision_free} collision-free spots passes "
               f"check_silk; the closest is {nearest_fail[1]}")
    out = {"ref": ref, "reason": why, "unplaceable": True,
           "suggest": UNPLACEABLE_FIX}
    if info is not None:
        out["fix_ops"] = _fix_ops(ref, info)
    return out


def solve(pcb: Path, refs: list[str] | None, min_clear: float):
    """-> (ops, results, residual, skipped) - pure geometry, no board writes."""
    model = placelib.PlaceModel(pcb)
    ref_texts, silk, mask_margin = parse_board(pcb)
    outline = model.outline

    targets = []
    skipped = []
    for ref in sorted(model.footprints):
        fp = model.footprints[ref]
        info = ref_texts.get(ref)
        if refs is not None and ref not in refs:
            continue
        if info is None:
            skipped.append({"ref": ref, "reason": "no Reference property"})
            continue
        if info["hidden"]:
            skipped.append({"ref": ref, "reason": "hidden"})
            continue
        if "board_only" in fp.attrs:
            skipped.append({"ref": ref, "reason": "board_only"})
            continue
        if info["layer"] not in SILK_LAYERS:
            skipped.append({"ref": ref,
                            "reason": f"refdes on {info['layer']}, not silk"})
            continue
        targets.append(ref)

    # crowded-first ordering (recipe rule 3)
    ext_of = {r: model.footprints[r].extents_abs() for r in model.footprints}

    def crowd(ref):
        e = ext_of[ref]
        return sum(1 for o in model.footprints
                   if o != ref and e.distance(ext_of[o]) <= CROWD_RADIUS)

    targets.sort(key=lambda r: (-crowd(r), r))
    target_set = set(targets)

    # static obstacles per side: pad MASK APERTURES (through pads on both;
    # copper grown by the mask margin - what KiCad's "silkscreen clipped by
    # solder mask" test measures), silk graphics (a part's own geoms are
    # excluded per-candidate), non-target labels
    static = {"front": [], "back": []}
    own_geoms: dict[str, list] = {r: [] for r in model.footprints}
    for ref in sorted(model.footprints):
        fp = model.footprints[ref]
        grow = mask_margin.get(ref, 0.0)
        for i, pp in enumerate(_pad_polys_abs(fp)):
            if grow > 0:
                pp = pp.buffer(grow, join_style=2)
            through = fp.pads[i].through
            for side in ("front", "back"):
                if side == fp.side or through:
                    static[side].append((pp, ref))
            own_geoms[ref].append(pp)
    for side in ("front", "back"):
        for geo, owner in silk[side]:
            static[side].append((geo, owner))
            if owner in own_geoms:
                own_geoms[owner].append(geo)
    for ref, info in ref_texts.items():
        if ref in target_set or info["hidden"] or ref not in model.footprints:
            continue
        side = "front" if info["layer"].startswith("F.") else "back"
        static[side].append((_current_box(ref, model.footprints[ref], info),
                             ref))

    # check_silk's own view of the board: its pad shapes and pad-extent
    # boxes, so every candidate is judged by the checker's rules verbatim
    bg = geom.load_board(pcb)
    rule_boxes = check_silk.pad_extent_boxes(bg)
    rule_pads = {}
    for side, letter in (("front", "F"), ("back", "B")):
        pads = [p for p in bg.pads_of() if letter in check_silk.pad_side(p)]
        rule_pads[side] = (pads, STRtree([p.poly for p in pads]))

    ops, results, residual = [], [], []
    # every target's CURRENT label is an obstacle until that target is
    # decided, so an earlier label never lands on one that ends up staying
    pending = {r: _current_box(r, model.footprints[r], ref_texts[r])
               for r in targets}
    placed_boxes = {"front": [], "back": []}
    side_of = {r: "front" if ref_texts[r]["layer"].startswith("F.")
               else "back" for r in targets}
    for ref in targets:
        fp = model.footprints[ref]
        info = ref_texts[ref]
        side = side_of[ref]
        del pending[ref]
        obstacles = [g for g, owner in static[side] if owner != ref] \
            + own_geoms[ref] + placed_boxes[side] \
            + [b for r, b in pending.items() if side_of[r] == side]
        obstacle_tree = STRtree(obstacles)
        pad_top = min((p.local[1]
                       - (abs(math.sin(math.radians(p.rot))) * p.size[0]
                          + abs(math.cos(math.radians(p.rot))) * p.size[1]) / 2
                       for p in fp.pads), default=None)
        cur = _fp_abs(fp.pos, fp.angle, info["local"])
        best, best_score, nearest_fail = None, None, None
        collision_free = 0
        for (x, y, deg) in [(cur[0], cur[1], info["deg"])] \
                + _candidates(fp, info, pad_top):
            b = placelib.text_box(ref, info["size"], info["thickness"],
                                  x, y, deg)
            if not b.within(outline):
                continue
            clear = _clearance(b, obstacles, CLEAR_CAP, obstacle_tree)
            if clear is None or clear <= max(min_clear, HARD_FLOOR):
                continue
            collision_free += 1
            fail, own_off, att = rule_verdict(ref, info, x, y, deg,
                                              rule_pads[side], rule_boxes)
            if fail is not None:
                if nearest_fail is None or own_off < nearest_fail[0]:
                    nearest_fail = (own_off, fail)
                continue
            if att is None:              # padless part: closeness only
                own_off = b.distance(ext_of[ref])
            # a label no other part is nearer to outranks one that merely
            # passes; then clearance (capped), then closeness to own pads
            tier = 2 if att is None or att[1] is None else 1
            score = (tier, round(min(clear, CLEAR_CAP), 3),
                     -round(own_off, 3))
            if best_score is None or score > best_score:
                best, best_score = (x, y, deg, b), score
        if best is None:
            residual.append(_unplaceable(ref, collision_free, nearest_fail,
                                         info))
            # current box stays; count it as an obstacle for later labels
            placed_boxes[side].append(_current_box(ref, fp, info))
            continue
        x, y, deg, b = best
        placed_boxes[side].append(b)
        moved = (abs(cur[0] - x) > 1e-3 or abs(cur[1] - y) > 1e-3
                 or place_edit_angdiff(info["deg"], deg) > 0.05)
        if moved:
            ops.append({"op": "move_text", "ref": ref, "field": "reference",
                        "x": checklib.rnd(x), "y": checklib.rnd(y),
                        "deg": checklib.rnd(deg)})
        beyond = b.distance(_pad_extent_abs(fp))
        results.append({"ref": ref, "moved": moved,
                        "clearance_mm": best_score[1],
                        "beyond_extent_mm": checklib.rnd(beyond)})
    return ops, results, residual, skipped, model, ref_texts


def place_edit_angdiff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0            # text reads the same at 0/180
    return min(d, 180.0 - d)


def _median(vals):
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    mid = n // 2
    return checklib.rnd(vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2)


def _beyond_before(model, ref_texts, refs):
    out = []
    for ref in refs:
        fp = model.footprints[ref]
        info = ref_texts[ref]
        b = _current_box(ref, fp, info)
        out.append(b.distance(_pad_extent_abs(fp)))
    return out


def read_min_silk_clearance(pcb: Path) -> float:
    """Live value from the sibling .kicad_pro; 0.0 (the KiCad default) when
    absent - per-board values differ (wo-silklegibility guidance)."""
    pro = pcb.with_suffix(".kicad_pro")
    if not pro.is_file():
        return 0.0
    try:
        doc = json.loads(pro.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    return float((doc.get("board", {}).get("design_settings", {})
                  .get("rules", {}) or {}).get("min_silk_clearance", 0.0))


# ---------------------------------------------------------------- driver

def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--refs", default=None,
                    help="comma-separated refdes subset (default: every "
                         "visible non-board_only silk refdes)")
    ap.add_argument("--ops-out", default=None,
                    help="ops file path (default <board dir>/silk_ops.json)")
    ap.add_argument("--min-clearance", type=float, default=None,
                    help="override the .kicad_pro min_silk_clearance")
    ap.add_argument("--apply", action="store_true",
                    help="apply the ops via place_edit's atomic pipeline")
    ap.add_argument("--verify-drc", action="store_true",
                    help="after --apply, run the real DRC and report "
                         "silk-class findings (the authoritative oracle)")
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    if args.verify_drc and not args.apply:
        raise CheckError("--verify-drc needs --apply (it checks the board "
                         "the ops were applied to)")

    refs = [r.strip() for r in args.refs.split(",")] if args.refs else None
    min_clear = args.min_clearance if args.min_clearance is not None \
        else read_min_silk_clearance(pcb)

    ops, results, residual, skipped, model, ref_texts = \
        solve(pcb, refs, min_clear)

    before = _beyond_before(model, ref_texts, [r["ref"] for r in results])
    ops_path = Path(args.ops_out) if args.ops_out \
        else pcb.parent / "silk_ops.json"
    ops_path.write_text(json.dumps({"version": 1, "ops": ops}, indent=1),
                        encoding="utf-8")

    violations = [checklib.violation(
        SCRIPT, "warning", None, None, None, [r["ref"]],
        f"{r['ref']}: unplaceable - {r['reason']}; label left where it is. "
        f"Fix: {r['suggest']}", SCRIPT, kind="silk_residual",
        suggest=r["suggest"], fix_ops=r.get("fix_ops"))
        for r in residual]

    facts = {
        "targets": len(results) + len(residual),
        "moved": len(ops),
        "residual": residual,
        "unplaceable": [r["ref"] for r in residual],
        "skipped": skipped,
        "min_silk_clearance": min_clear,
        "median_beyond_extent_mm_before": _median(before),
        "median_beyond_extent_mm": _median(
            [r["beyond_extent_mm"] for r in results]),
        "ops_out": str(ops_path),
        "results": results,
    }

    if args.apply:
        if ops:
            import place_edit
            place_edit.apply_ops(pcb, ops)
        facts["applied"] = bool(ops)
        if args.verify_drc:
            import env
            import kc
            cli = env.find_kicad_cli()
            if cli is None:
                raise CheckError("--verify-drc needs kicad-cli (env.py)")
            drc = kc.run_drc(cli, pcb)
            silk_hits = [v for v in drc["violations"]
                         if "silk" in (v.get("check") or "")]
            facts["drc_silk_total"] = len(silk_hits)
            facts["drc_total"] = drc["counts"]["total"]
            for v in silk_hits:
                violations.append(checklib.violation(
                    SCRIPT, "error", tuple(v["pos"]) if v.get("pos") else None,
                    v.get("layer"), v.get("net"), v.get("refs"),
                    f"post-apply DRC: {v.get('msg')}", SCRIPT,
                    kind="silk_drc_regression"))

    payload = checklib.report(SCRIPT, str(pcb), violations, **facts)
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
