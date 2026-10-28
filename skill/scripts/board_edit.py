"""board_edit - edit an EXISTING board's Edge.Cuts outline (U17).

The pipeline could move a part (place_edit), edit copper (route_edit) and
add/swap/remove parts on a routed board (board_update) - but nothing could
edit the OUTLINE. It was written once at P5 by `board_init --outline` from the
netlist, so changing the board size meant a rebuild that threw placement and
routing away. That forced the final size to be guessed before placement
existed, and it blocked the canonical flow: place first, then shrink the board
to fit what the placement actually needs.

  board_edit.py --pcb B.kicad_pcb --outline 40x30 | fit | keep
                [--margin MM] [--anchor topleft|center]
                [--corner-radius R] [--cutout X,Y,W,H]...
                [--report-only] [--replace-shape] [--no-refill]
                [--workspace DIR | --no-record] [--out-report r.json]

  --outline WxH   resize to W x H mm, anchored at the current outline's
                  top-left corner (--anchor center keeps the centre instead)
  --outline fit   shrink (or grow) to the CONTENT bounding box + --margin:
                  every footprint courtyard, every piece of copper and every
                  keepout rule area, each clipped to the current outline so a
                  connector that already overhangs does not enlarge the board
  --outline keep  keep the bbox, change only the radius / notches

The outline is ABSOLUTE and rewritten wholesale (like place_edit's ops:
re-running the same command is a no-op), and it is drawn by the SAME
board_swig.draw_outline() board_init uses - an edited outline and an
initialized one are the same geometry by construction.

REFUSALS (exit 1, nothing applied; --report-only reports without applying):
a footprint courtyard, a copper item, a drill or a keepout rule area that the
new boundary would push outside or bring closer than the fab profile's
min_copper_to_edge / min_hole_to_edge. The comparison is BEFORE vs AFTER - a
part that already overhangs (a declared edge connector) is pre-existing and
reported as such; only issues this edit creates or worsens block it. The
report names them so the caller knows what must move first; nothing is ever
silently clipped.

Atomic + rollback (place_edit / board_update contract): the board and its
project sidecars are staged in a scratch dir INSIDE the board's directory, the
SWIG worker rewrites Edge.Cuts on the copy, the driver INDEPENDENTLY re-parses
it (outline is one closed face at the requested geometry; every track, via,
pad, footprint and zone outline identical to before), zones are refilled and
DRC must be no worse; only then does os.replace() swap it in. Any failure
leaves the original board byte-identical.

State: the edit records ITSELF as the `outline_change` class (U16's lesson -
a consequence that needs a second command is a consequence that gets
forgotten), into --workspace or the first parent of the board holding a
state.json. --no-record opts out; a board with no workspace above it (golden
corpus, scratch) records nothing and says so.

exit 0 applied (or a clean --report-only) / 1 refused: the new outline would
create violations, listed / 2 error (nothing applied).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from shapely.geometry import Point, box  # noqa: E402
from shapely.prepared import prep  # noqa: E402
import shapely  # noqa: E402

import kc  # noqa: E402
import checklib  # noqa: E402
import env  # noqa: E402
import fabfloors  # noqa: E402
import geom  # noqa: E402
import placelib  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402

WORKER = SCRIPTS / "lib" / "board_swig.py"
EDIT_CLASS = "outline_change"

POS_TOL = 1e-3          # mm - outline geometry verification
AREA_TOL = 0.05         # mm2 - "did the board grow anywhere" noise floor
GRID = 1e-3             # mm - target coordinates are rounded to this
EPS_AREA = 1e-6         # mm2 - shapely noise floor (placelib's convention)
# "measurably worse than before" thresholds. Target coordinates are rounded to
# the micron, so a pre-existing violation may shift by a fraction of one -
# that is arithmetic, not a regression.
WORSE_AREA = 1e-3       # mm2
WORSE_CLR = 1e-3        # mm
_QUAD = 32              # buffer smoothness for rounded-corner reconstruction


# --------------------------------------------------------------- outline shape

def rounded_box(x1: float, y1: float, x2: float, y2: float, r: float):
    """A rectangle with radius-r corners - erode/dilate, the same shape
    board_swig draws as 4 segments + 4 quarter arcs."""
    rect = box(x1, y1, x2, y2)
    if r <= 0:
        return rect
    return rect.buffer(-r, quad_segs=_QUAD).buffer(r, quad_segs=_QUAD)


def shape_deviation(face, want) -> float:
    """How far the parsed outline is from the shape we mean, in mm.

    Hausdorff, not area: geom samples each arc as 16 chords while
    `rounded_box` buffers at 32 per quadrant, so an AREA comparison scales
    with r^2 and would call a legitimately rounded board unrecognizable. The
    worst point-to-shape distance between the two is the chord sagitta
    (< 0.01 mm at any board radius); a notch or a chamfer deviates by its own
    millimetre-scale size."""
    return max(face.hausdorff_distance(want), want.hausdorff_distance(face))


def shape_tol(r: float) -> float:
    """Sagitta allowance for a radius-r arc sampled at 16 chords/quadrant."""
    return 0.05 + 0.005 * max(r, 0.0)


# The Edge.Cuts item inventories board_init can produce for a plain outline:
# one rectangle, four segments, or four segments + four corner arcs.
_PLAIN_ITEMS = ({"gr_rect": 1}, {"gr_line": 4}, {"gr_line": 4, "gr_arc": 4})


def describe_outline(faces: list, arc_radii: list[float] | None = None,
                     items: dict | None = None) -> dict:
    """The current outline as {bbox, w, h, corner_radius, faces, items,
    ideal, deviation_mm}.

    `corner_radius` comes from the Edge.Cuts ARCS themselves (exact, from
    their declared three points) - 4 equal arcs = a rounded rectangle. Boards
    with no arcs are square-cornered. `ideal` is False when the board is not a
    plain (rounded) rectangle - a notch, a chamfer, an arbitrary polygon,
    interior windows: board_edit rewrites Edge.Cuts wholesale, so a shape it
    cannot restate must never be silently replaced by a rectangle.

    The ITEM INVENTORY is part of that judgement, not a nicety: geom's parser
    returns on the first gr_rect on Edge.Cuts, so a second rect (an interior
    window - the exact shape board_init refuses to create) is invisible in the
    parsed outline and would be deleted without a word.
    """
    if not faces:
        raise CheckError("board has no closed Edge.Cuts outline - nothing to "
                         "edit (board_init draws the first one)")
    face = faces[0]
    x1, y1, x2, y2 = face.bounds
    radii = list(arc_radii or [])
    r = 0.0
    arcs_ok = not radii
    if radii:
        r = sum(radii) / len(radii)
        arcs_ok = len(radii) == 4 and max(radii) - min(radii) <= POS_TOL
    dev = shape_deviation(face, rounded_box(x1, y1, x2, y2, r))
    inv = dict(items) if items else None
    return {
        "bbox": [round(v, 3) for v in (x1, y1, x2, y2)],
        "w": round(x2 - x1, 3), "h": round(y2 - y1, 3),
        "corner_radius": round(r, 4),
        "faces": len(faces),
        "arcs": len(radii),
        "items": inv,
        "area_mm2": round(face.area, 3),
        "ideal": (arcs_ok and len(faces) == 1 and dev <= shape_tol(r)
                  and (inv is None or inv in _PLAIN_ITEMS)),
        "deviation_mm": round(dev, 4),
    }


def _snap_out(lo: float, hi: float) -> tuple[float, float]:
    """Round an interval OUTWARD to the 1 um grid - a fit margin is a floor,
    so rounding must never shave it."""
    return (math.floor(lo / GRID) * GRID, math.ceil(hi / GRID) * GRID)


def content_bounds(model: "placelib.PlaceModel", bg: "geom.BoardGeom",
                   clip) -> tuple[tuple[float, float, float, float], dict]:
    """Bounding box of everything that must stay ON the board, clipped to the
    current outline: footprint courtyards (movable AND board-only - a mounting
    hole hanging off the edge is a hole nobody drills), all copper, and every
    keepout rule area. Clipping is what makes `fit` safe: the result contains
    every part of every item that was inside, so `fit` cannot invent a new
    containment violation."""
    pieces = []
    counts = {"footprints": 0, "copper_items": 0, "rule_areas": 0}
    for f in model.footprints.values():
        pieces.append(f.extents_abs())
        counts["footprints"] += 1
    for item in list(bg.tracks_of()) + list(bg.vias_of()) + list(bg.pads_of()):
        pieces.append(item.poly)
        counts["copper_items"] += 1
    for ra in bg.rule_areas:
        pieces.append(ra["outline"])
        counts["rule_areas"] += 1
    inside = shapely.intersection(shapely.union_all(pieces), clip)
    if inside.is_empty:
        raise CheckError("nothing inside the current outline to fit around")
    return inside.bounds, counts


def target_rect(mode: str, spec: str, current: dict, model, bg,
                margin: float, anchor: str) -> tuple[list[float], dict]:
    """-> ([x1, y1, x2, y2], extra report fields) for the requested outline."""
    cx1, cy1, cx2, cy2 = current["bbox"]
    if mode == "keep":
        return [cx1, cy1, cx2, cy2], {}
    if mode == "fixed":
        m = re.fullmatch(r"\s*([\d.]+)\s*x\s*([\d.]+)\s*", spec)
        if not m:
            raise CheckError(f"bad --outline {spec!r} (use WxH, 'fit' or 'keep')")
        w, h = float(m.group(1)), float(m.group(2))
        if w <= 0 or h <= 0:
            raise CheckError(f"bad --outline {spec!r}: W and H must be > 0")
        if anchor == "center":
            mx, my = (cx1 + cx2) / 2.0, (cy1 + cy2) / 2.0
            rect = [mx - w / 2.0, my - h / 2.0, mx + w / 2.0, my + h / 2.0]
        else:
            rect = [cx1, cy1, cx1 + w, cy1 + h]
        return [round(v, 3) for v in rect], {"anchor": anchor}
    # fit
    if margin < 0:
        raise CheckError("--margin must be >= 0")
    (bx1, by1, bx2, by2), counts = content_bounds(
        model, bg, rounded_box(cx1, cy1, cx2, cy2, current["corner_radius"]))
    x1, x2 = _snap_out(bx1 - margin, bx2 + margin)
    y1, y2 = _snap_out(by1 - margin, by2 + margin)
    return [round(v, 3) for v in (x1, y1, x2, y2)], {
        "content_bbox": [round(v, 3) for v in (bx1, by1, bx2, by2)],
        "content_counts": counts, "margin": margin,
    }


# ------------------------------------------------------------------- fab floors

def fab_floors(pcb: Path) -> dict:
    """The edge-clearance floors of the capability profile this board will be
    judged against at P9 - same derivation as the dfm gate (layer count from
    the board's own (layers) block, copper weight from its (stackup))."""
    import dfm_check
    import fab_export
    layers = len(fab_export.copper_layers(pcb))
    oz, oz_source = dfm_check.derive_copper_oz(pcb)
    try:
        cls, cap = fabfloors.profile(layers, oz)
    except fabfloors.FabFloorError:
        if oz_source != "stackup":
            raise
        cls, cap = fabfloors.profile(layers, 1.0)   # dfm_check's fallback
        oz, oz_source = 1.0, "default"
    return {"profile": cls, "layers": layers, "copper_oz": oz,
            "copper_oz_source": oz_source,
            "min_copper_to_edge_mm": float(cap["min_copper_to_edge_mm"]),
            "min_hole_to_edge_mm": float(cap["min_hole_to_edge_mm"])}


# ---------------------------------------------------------------------- issues
# An issue is keyed so the SAME item can be compared before and after the
# resize; `metric` is the number that says how bad it is, `worse_when` which
# direction is worse. Only issues that are new - or measurably worse - block.

def _issue(kind: str, key: str, msg: str, metric: float, worse_when: str,
           refs=None, pos=None) -> dict:
    return {"kind": kind, "key": key, "msg": msg,
            "metric": checklib.rnd(metric), "worse_when": worse_when,
            "refs": refs or [], "pos": [checklib.rnd(pos[0]),
                                        checklib.rnd(pos[1])] if pos else None}


def outline_issues(model: "placelib.PlaceModel", bg: "geom.BoardGeom",
                   poly, floors: dict, edges_decl: dict) -> list[dict]:
    """Everything wrong with this board IF its outline were `poly`."""
    issues: list[dict] = []
    cu_floor = floors["min_copper_to_edge_mm"]
    hole_floor = floors["min_hole_to_edge_mm"]
    covers = prep(poly)
    inner_cu = prep(poly.buffer(-cu_floor, quad_segs=_QUAD))
    inner_hole = prep(poly.buffer(-hole_floor, quad_segs=_QUAD))
    ring = poly.exterior

    def clearance(g) -> float:
        return 0.0 if not covers.covers(g) else g.distance(ring)

    for ref in sorted(model.footprints):
        f = model.footprints[ref]
        ext = f.extents_abs()
        if covers.covers(ext):
            continue
        outside = ext.difference(poly).area
        if outside <= EPS_AREA:
            continue
        decl = edges_decl.get(ref)
        if decl and ext.intersection(poly).area >= placelib.ON_BOARD_MIN * ext.area:
            continue  # a declared edge part may overhang its own edge
        c = ext.centroid
        issues.append(_issue(
            "footprint_outside", ref,
            f"{ref} extends {outside:.2f} mm2 outside the outline",
            outside, "higher", refs=[ref], pos=(c.x, c.y)))

    for i, t in enumerate(bg.tracks_of()):
        if inner_cu.covers(t.poly):
            continue
        d = clearance(t.poly)
        c = t.shape.centroid
        issues.append(_issue(
            "copper_to_edge", t.uuid or f"track:{t.layer}:{i}",
            f"track on {t.layer} ({t.net or 'no net'}) is {d:.3f} mm from the "
            f"board edge, floor {cu_floor} mm", d, "lower", pos=(c.x, c.y)))
    for i, v in enumerate(bg.vias_of()):
        if not inner_cu.covers(v.poly):
            d = clearance(v.poly)
            issues.append(_issue(
                "copper_to_edge", v.uuid or f"via:{i}",
                f"via ({v.net or 'no net'}) is {d:.3f} mm from the board edge, "
                f"floor {cu_floor} mm", d, "lower", pos=v.at))
        hole = Point(v.at).buffer(v.drill / 2.0, quad_segs=_QUAD)
        if not inner_hole.covers(hole):
            d = clearance(hole)
            issues.append(_issue(
                "hole_to_edge", f"viadrill:{v.uuid or i}",
                f"via drill ({v.net or 'no net'}) is {d:.3f} mm from the board "
                f"edge, floor {hole_floor} mm", d, "lower", pos=v.at))
    for p in bg.pads_of():
        key = f"{p.ref}.{p.number}"
        if not inner_cu.covers(p.poly):
            d = clearance(p.poly)
            issues.append(_issue(
                "copper_to_edge", f"pad:{key}",
                f"pad {key} ({p.net or 'no net'}) is {d:.3f} mm from the board "
                f"edge, floor {cu_floor} mm", d, "lower", refs=[p.ref],
                pos=p.center))
        drill = p.drill_poly
        if not drill.is_empty and not inner_hole.covers(drill):
            d = clearance(drill)
            issues.append(_issue(
                "hole_to_edge", f"paddrill:{key}",
                f"pad {key} drill is {d:.3f} mm from the board edge, floor "
                f"{hole_floor} mm", d, "lower", refs=[p.ref], pos=p.center))

    for i, ra in enumerate(bg.rule_areas):
        g = ra["outline"]
        if covers.covers(g):
            continue
        clipped = g.difference(poly).area
        if clipped <= EPS_AREA:
            continue
        name = ra.get("name") or f"#{i}"
        c = g.centroid
        issues.append(_issue(
            "keepout_clipped", f"rulearea:{name}:{i}",
            f"keepout rule area '{name}' loses {clipped:.2f} mm2 outside the "
            f"outline", clipped, "higher", pos=(c.x, c.y)))
    return issues


def new_or_worse(before: list[dict], after: list[dict]) -> list[dict]:
    """Issues this edit CREATES or worsens. A pre-existing overhang is the
    board's business, not the resize's - but making it worse is."""
    was = {i["key"]: i for i in before}
    out = []
    for issue in after:
        prev = was.get(issue["key"])
        if prev is None:
            out.append(dict(issue, was=None))
            continue
        if issue["worse_when"] == "higher":
            worse = issue["metric"] > prev["metric"] + WORSE_AREA
        else:
            worse = issue["metric"] < prev["metric"] - WORSE_CLR
        if worse:
            out.append(dict(issue, was=prev["metric"]))
    return out


# ------------------------------------------------------------------ inventory

def copper_inventory(bg: "geom.BoardGeom", model: "placelib.PlaceModel") -> dict:
    """What must survive an outline edit untouched. Rounded to 1e-4 mm: the
    SWIG resave rewrites the whole file (and every uuid), so equality has to
    be on parsed geometry, never bytes."""
    r = lambda v: round(float(v), 4)  # noqa: E731
    return {
        "tracks": sorted(
            (t.net or "", t.layer, r(t.width),
             tuple(sorted((r(x), r(y)) for x, y in t.shape.coords)))
            for t in bg.tracks_of()),
        "vias": sorted((v.net or "", r(v.at[0]), r(v.at[1]),
                        r(v.diameter), r(v.drill)) for v in bg.vias_of()),
        "pads": sorted((p.ref, p.number, p.net or "", r(p.center[0]),
                        r(p.center[1]), r(p.angle)) for p in bg.pads_of()),
        "footprints": sorted((f.ref, r(f.pos[0]), r(f.pos[1]), r(f.angle),
                              f.side) for f in model.footprints.values()),
        "zones": sorted((z.net or "", tuple(z.layers)) for z in bg.zones_of()),
    }


def verify_applied(staged: Path, rect: list[float], radius: float,
                   want_poly, before_inv: dict,
                   removed: int | None = None,
                   expect_removed: int | None = None) -> list[str]:
    """Independent re-parse of the saved board (the driver never trusts the
    worker's own report): the outline is exactly the shape that was asked for
    and nothing else moved."""
    problems: list[str] = []
    if removed is not None and expect_removed is not None \
            and removed != expect_removed:
        problems.append(
            f"the worker removed {removed} Edge.Cuts item(s), the driver "
            f"counted {expect_removed} - something else was on that layer")
    bg = geom.BoardGeom.from_file(staged)
    model = placelib.PlaceModel(staged)
    got = describe_outline(bg.outline_faces, bg.outline_arc_radii,
                           bg.outline_items)
    if got["faces"] != 1:
        problems.append(f"outline parses as {got['faces']} closed faces")
    if any(abs(want - have) > POS_TOL
           for want, have in zip(rect, got["bbox"])):
        problems.append(f"outline bbox {got['bbox']} != {rect}")
    if abs(got["corner_radius"] - radius) > POS_TOL:
        problems.append(f"corner radius {got['corner_radius']} != {radius}")
    dev = shape_deviation(bg.outline, want_poly)
    if dev > shape_tol(radius):
        problems.append(f"outline shape is {dev:.3f} mm away from the one "
                        f"requested ({bg.outline.area:.2f} mm2 vs "
                        f"{want_poly.area:.2f} mm2)")
    after_inv = copper_inventory(bg, model)
    for kind in before_inv:
        if before_inv[kind] != after_inv[kind]:
            b, a = before_inv[kind], after_inv[kind]
            problems.append(
                f"{kind} changed ({len(b)} -> {len(a)}; "
                f"{len(set(map(str, b)) ^ set(map(str, a)))} differing entries)")
    return problems


# ----------------------------------------------------------------------- apply

def _dangling(report: dict) -> int:
    return sum(1 for v in report["violations"]
               if "dangling" in (v.get("check") or ""))


def _errors(report: dict) -> int:
    return sum(1 for v in report["violations"]
               if v.get("severity") == "error"
               and (v.get("source") or "") != "unconnected")


def apply_outline(pcb: Path, rect: list[float], radius: float,
                  cutouts: list[dict], want_poly, before_inv: dict,
                  has_zones: bool, refill: bool, warnings: list[str],
                  expect_removed: int | None = None) -> dict:
    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    if bp is None:
        raise CheckError("KiCad bundled python not found (env.py)")

    if refill and has_zones:
        # The DRC comparison is unrefilled-BEFORE vs refilled-AFTER (the
        # board_update asymmetry): with a stale fill on the way in, the delta
        # would measure the refill, not the outline. Refuse and say so.
        geom.load_board(pcb, refresh=True).assert_fresh()
    drc_before = kc.run_drc(cli, pcb) if refill else None
    stage = Path(tempfile.mkdtemp(prefix=".aiee_outline_", dir=pcb.parent))
    result: dict = {}
    try:
        staged = stage / pcb.name
        shutil.copy2(pcb, staged)
        for side in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
            sib = pcb.with_suffix(side)
            if sib.is_file():
                shutil.copy2(sib, stage / sib.name)  # DRC rules + severities
        job = {"verb": "set_outline", "board": str(staged), "out": str(staged),
               "rect": rect, "corner_radius": radius, "cutouts": cutouts}
        jf = stage / "job.json"
        jf.write_text(json.dumps(job), encoding="utf-8")
        cp = subprocess.run([str(bp), str(WORKER), str(jf)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=300)
        worker = _last_json(cp.stdout)
        if cp.returncode != 0 or not worker or worker.get("status") != "pass":
            detail = (worker or {}).get("error") \
                or (cp.stdout or cp.stderr or "").strip()[-400:]
            raise CheckError(f"board_swig set_outline failed: {detail} "
                             "(rolled back)")
        problems = verify_applied(staged, rect, radius, want_poly, before_inv,
                                  worker.get("removed_edge_items"),
                                  expect_removed)
        if problems:
            raise CheckError("post-apply verify failed (rolled back): "
                             + "; ".join(problems[:8]))
        result = {"worker": {k: worker[k] for k in
                             ("removed_edge_items", "bbox", "corner_radius",
                              "cutouts", "notes") if k in worker}}
        if refill:
            drc_after = kc.run_drc(cli, staged, refill=has_zones,
                                   save_board=has_zones)
            if _errors(drc_after) > _errors(drc_before) \
                    or _dangling(drc_after) > _dangling(drc_before):
                raise CheckError(
                    f"the new outline makes DRC worse (errors "
                    f"{_errors(drc_before)} -> {_errors(drc_after)}, dangling "
                    f"{_dangling(drc_before)} -> {_dangling(drc_after)}) - "
                    "rolled back")
            result["refilled"] = has_zones
            result["drc"] = {"before": drc_before["counts"],
                             "after": drc_after["counts"],
                             "errors_before": _errors(drc_before),
                             "errors_after": _errors(drc_after)}
        else:
            result["refilled"] = False
            if has_zones:
                warnings.append(
                    "--no-refill: the zone fills still clip to the OLD "
                    "outline - refill (kc.py drc --refill --save-board) "
                    "before the drc_routed gate, which refuses stale fills")
        os.replace(staged, pcb)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return result


def _last_json(text: str) -> dict | None:
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


# ------------------------------------------------------------------ state edit

def record_edit(pcb: Path, explicit_ws: str | None, note: str) -> dict:
    """Record the outline_change class in the workspace's state.json (U16
    pattern: the writer records its own consequence)."""
    try:
        ws = statelib.find_workspace(pcb, explicit_ws)
    except Exception as exc:  # noqa: BLE001 - an explicit bad --workspace
        return {"ok": False, "recorded": False, "reason": str(exc)}
    if ws is None:
        return {"ok": True, "recorded": False,
                "reason": "no state.json above the board - nothing to record "
                          "(corpus/scratch board)"}
    try:
        import state as state_mod
        st = state_mod.State.load(ws / "state.json")
        rec = st.apply_edit(EDIT_CLASS, refs=[], note=note)
        st.save()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return {"ok": False, "recorded": False,
                "workspace": str(ws).replace("\\", "/"),
                "reason": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "recorded": True,
            "workspace": str(ws).replace("\\", "/"), "class": EDIT_CLASS,
            "human_hold": rec["human_hold"], "gates": rec["gates"],
            "gates_marked": rec["gates_marked"],
            "stale_artifacts": rec["stale_artifacts"]}


# ------------------------------------------------------------------------- CLI

def parse_cutouts(specs: list[str]) -> list[dict]:
    cutouts = []
    for spec in specs:
        m = re.fullmatch(r"\s*([\d.]+),([\d.]+),([\d.]+),([\d.]+)\s*", spec)
        if not m:
            raise CheckError(f"bad --cutout {spec!r} (use X,Y,W,H in mm, "
                             "relative to the outline's top-left corner)")
        x, y, w, h = (float(g) for g in m.groups())
        if w <= 0 or h <= 0:
            raise CheckError(f"bad --cutout {spec!r}: W and H must be > 0")
        cutouts.append({"x": x, "y": y, "w": w, "h": h})
    return cutouts


_CUT_TOL = 0.001  # mm - board_swig._classify_cutouts' own edge tolerance


def validate_cutouts(rect: list[float], radius: float,
                     cutouts: list[dict]) -> None:
    """Refuse a notch board_swig would silently SKIP (or reject), before the
    board is touched: the worker only notes the skip, and the driver would
    then roll a legal edit back on a shape mismatch it cannot explain."""
    x1, y1, x2, y2 = rect
    for c in cutouts:
        a, b, w, h = c["x"], c["y"], c["w"], c["h"]
        cx1, cy1, cx2, cy2 = x1 + a, y1 + b, x1 + a + w, y1 + b + h
        where = f"cutout {a},{b},{w},{h}"
        if not (x1 - _CUT_TOL <= cx1 < cx2 <= x2 + _CUT_TOL
                and y1 - _CUT_TOL <= cy1 < cy2 <= y2 + _CUT_TOL):
            raise CheckError(f"{where} lies outside the new outline "
                             f"{[round(v, 3) for v in rect]}")
        if abs(cy1 - y1) <= _CUT_TOL:
            side, span = "top", (cx1, cx2)
        elif abs(cy2 - y2) <= _CUT_TOL:
            side, span = "bottom", (cx1, cx2)
        elif abs(cx1 - x1) <= _CUT_TOL:
            side, span = "left", (cy1, cy2)
        elif abs(cx2 - x2) <= _CUT_TOL:
            side, span = "right", (cy1, cy2)
        else:
            raise CheckError(
                f"{where} touches no outline edge - interior windows are not "
                f"supported (they mis-parse as the board outline downstream); "
                f"move it to an edge and it becomes a notch")
        lo, hi = (x1 + radius, x2 - radius) if side in ("top", "bottom") \
            else (y1 + radius, y2 - radius)
        if span[0] < lo - _CUT_TOL or span[1] > hi + _CUT_TOL:
            raise CheckError(
                f"{where} on the {side} edge runs into a corner radius "
                f"({radius} mm) - move it inboard or lower --corner-radius")


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--outline", required=True,
                    help="WxH in mm, 'fit' (content bbox + --margin) or "
                         "'keep' (same bbox, new radius/cutouts)")
    ap.add_argument("--margin", type=float, default=1.0,
                    help="--outline fit: gap from the content bbox to the new "
                         "edge (default 1.0 mm; must clear the fab profile's "
                         "copper-to-edge floor)")
    ap.add_argument("--anchor", choices=("topleft", "center"),
                    default="topleft",
                    help="--outline WxH: which point of the current outline "
                         "stays put (default topleft)")
    ap.add_argument("--corner-radius", type=float, default=None,
                    help="corner radius in mm (default: keep the board's "
                         "current radius)")
    ap.add_argument("--cutout", action="append", default=[], metavar="X,Y,W,H",
                    help="edge notch relative to the new outline's top-left "
                         "corner; repeatable. Must touch an edge - interior "
                         "windows mis-parse as the board outline downstream")
    ap.add_argument("--constraints", default=None,
                    help="constraints.json for declared edge parts (default: "
                         "the one beside the board, if any)")
    ap.add_argument("--report-only", action="store_true",
                    help="report the new outline and what it would break; "
                         "change nothing")
    ap.add_argument("--replace-shape", action="store_true",
                    help="the current outline is not a plain (rounded) "
                         "rectangle: replace it anyway with the one given "
                         "here - the difference is reported, never silent")
    ap.add_argument("--no-refill", action="store_true",
                    help="skip the zone refill and the DRC comparison (fast; "
                         "leaves fills clipped to the OLD outline)")
    ap.add_argument("--workspace", default=None,
                    help="workspace whose state.json records the edit "
                         "(default: the first parent of the board with one)")
    ap.add_argument("--no-record", action="store_true",
                    help="do not record the outline_change edit class")
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    pcb = pcb.resolve()

    bg = geom.load_board(pcb)
    model = placelib.PlaceModel(pcb)
    current = describe_outline(bg.outline_faces, bg.outline_arc_radii,
                               bg.outline_items)
    cutouts = parse_cutouts(args.cutout)
    radius = current["corner_radius"] if args.corner_radius is None \
        else float(args.corner_radius)
    if radius < 0:
        raise CheckError("--corner-radius must be >= 0")

    if not current["ideal"] and not args.replace_shape:
        raise CheckError(
            f"the current outline is not a plain (rounded) rectangle "
            f"({current['faces']} closed face(s), Edge.Cuts items "
            f"{current['items']}, {current['deviation_mm']} mm away from its "
            f"bbox at radius {current['corner_radius']} mm) "
            f"- board_edit rewrites Edge.Cuts WHOLESALE, so applying here "
            f"would drop whatever else it holds (notches, interior windows, a "
            f"second outline). Re-state the full shape with --cutout / "
            f"--corner-radius and pass --replace-shape to accept the loss.")

    spec = args.outline.strip()
    mode = "fit" if spec.lower() == "fit" else \
           "keep" if spec.lower() == "keep" else "fixed"
    rect, extra = target_rect(mode, spec, current, model, bg,
                              args.margin, args.anchor)

    # Clamp exactly where board_swig.draw_outline clamps, so the driver asks
    # for the shape the worker will actually draw (an unclamped request would
    # fail its own post-apply verify and roll back a legal edit).
    rmax = min(rect[2] - rect[0], rect[3] - rect[1]) / 2.0 - 0.1
    clamp_note = None
    if radius > max(rmax, 0.0):
        clamp_note = (f"corner radius {radius} clamped to {round(max(rmax, 0.0), 3)}"
                      f" mm (half the shorter side)")
        radius = max(rmax, 0.0)

    floors = fab_floors(pcb)
    if mode == "fit" and args.margin < floors["min_copper_to_edge_mm"]:
        extra["margin_warning"] = (
            f"--margin {args.margin} is below the profile's copper-to-edge "
            f"floor {floors['min_copper_to_edge_mm']} mm - copper on the "
            f"content bbox will refuse")

    edges_decl = {}
    cfile = Path(args.constraints) if args.constraints \
        else pcb.parent / "constraints.json"
    if cfile.is_file():
        cdata = checklib.load_json(cfile, "constraints")
        edges_decl = {e["ref"]: e for e in
                      ((cdata.get("placement") or {}).get("edges") or [])
                      if e.get("ref")}

    validate_cutouts(rect, radius, cutouts)
    new_poly = rounded_box(*rect, radius)
    if cutouts:
        for c in cutouts:
            new_poly = new_poly.difference(
                box(rect[0] + c["x"], rect[1] + c["y"],
                    rect[0] + c["x"] + c["w"], rect[1] + c["y"] + c["h"]))
    if new_poly.is_empty or new_poly.area <= EPS_AREA:
        raise CheckError("the requested outline has no area")
    if new_poly.geom_type != "Polygon":
        raise CheckError(
            f"the requested cutouts cut the board into "
            f"{len(new_poly.geoms)} separate pieces - that is a panel, not an "
            f"outline; make the notches shallower")

    old_poly = rounded_box(*current["bbox"], current["corner_radius"]) \
        if current["ideal"] else bg.outline
    before = outline_issues(model, bg, old_poly, floors, edges_decl)
    after = outline_issues(model, bg, new_poly, floors, edges_decl)
    blocking = new_or_worse(before, after)
    warnings: list[str] = []
    if clamp_note:
        warnings.append(clamp_note)
    if "margin_warning" in extra:
        warnings.append(extra.pop("margin_warning"))
    zones_out = [z for z in bg.zones_of()
                 if any(not new_poly.covers(p)
                        for polys in z.fills.values() for p in polys)]
    if zones_out:
        warnings.append(f"{len(zones_out)} zone fill(s) reach past the new "
                        f"edge - they re-clip at refill")
    # A zone's OUTLINE is a fixed polygon drawn at plane-generation time; it
    # does not follow the board edge outward. Growing the board therefore
    # leaves a pour that stops short of the new edge until planes_gen re-pours.
    if bg.zones_of() and new_poly.difference(old_poly).area > AREA_TOL:
        warnings.append(
            "the board grew: zone OUTLINES do not follow the edge, so the "
            "pours still stop at the old boundary - re-run planes_gen (and "
            "stitch_vias) if they should reach the new one")

    payload = {
        "script": "board_edit", "status": "pass", "board": str(args.pcb),
        "mode": mode, "applied": False,
        "outline": {"before": current,
                    "after": {"bbox": rect,
                              "w": round(rect[2] - rect[0], 3),
                              "h": round(rect[3] - rect[1], 3),
                              "corner_radius": round(radius, 3),
                              "cutouts": cutouts,
                              "area_mm2": round(new_poly.area, 3)}},
        "fab_floors": floors,
        "blocking": blocking,
        "preexisting": before,
        "warnings": warnings,
        "edit_class": EDIT_CLASS,
        "gates_to_rerun": sorted(
            statelib.load_map()["edit_classes"][EDIT_CLASS]["gates"]),
        "human_hold": statelib.load_map()["edit_classes"][EDIT_CLASS]["human_hold"],
    }
    payload["outline"]["after"].update(extra)

    if blocking:
        by_kind: dict[str, int] = {}
        refs: set[str] = set()
        for b in blocking:
            by_kind[b["kind"]] = by_kind.get(b["kind"], 0) + 1
            refs.update(b["refs"])
        payload["blocking_summary"] = {"total": len(blocking),
                                       "by_kind": by_kind,
                                       "refs": sorted(refs)}
        payload["status"] = "violations"
        payload["refused"] = (
            f"{len(blocking)} item(s) the new outline would push outside the "
            f"board or bring under the fab edge clearance - move them first "
            f"(nothing was applied)")
        return payload, args.out_report
    if args.report_only:
        return payload, args.out_report

    before_inv = copper_inventory(bg, model)
    payload.update(apply_outline(pcb, rect, radius, cutouts, new_poly,
                                 before_inv, bool(bg.zones_of()),
                                 not args.no_refill, warnings,
                                 sum(bg.outline_items.values())))
    payload["applied"] = True
    if not args.no_record:
        rec = record_edit(pcb, args.workspace,
                          f"outline -> {payload['outline']['after']['w']}x"
                          f"{payload['outline']['after']['h']} mm ({mode})")
        payload["record"] = rec
        if not rec["ok"]:
            payload["status"] = "error"
            payload["error"] = (
                f"the outline IS applied but recording the {EDIT_CLASS} edit "
                f"failed ({rec['reason']}) - record it by hand: state.py edit "
                f"--workspace <ws> --class {EDIT_CLASS}")
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("board_edit", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
