"""board_swig.py - SWIG worker for board_init.py / board_edit.py. BUNDLED
python only.

Runs under KiCad's bundled python.exe (the only interpreter with `pcbnew`),
launched as a subprocess by the venv driver. Consumes a JSON job on argv; the
`verb` key selects the operation (absent = "build", board_init's original job
shape).

Every job runs under safelib.board_locks(job): an OS writer lock on the
"board" and "out" .kicad_pcb (<pcb>.lock), held from LoadBoard to Save;
a second worker on the same board waits, then refuses (LockBusy).

verb "build" (board_init, SPEC P5): places every footprint from a netlist
netmap onto a fresh board, assigns pad nets, spreads parts on a shelf grid (no
courtyard overlaps), draws the outline and mounting holes, and saves an
UNFILLED board. Mirrors the corpus builder tests/golden/generators/pcb_build.py;
zone fill (if any) is a later kicad-cli step - pcbnew.ZONE_FILLER segfaults
headless (LEARNINGS [swig]).

  out            output .kicad_pcb path
  layers         2 | 4
  components     [{ref, value, fp:"Lib:Name"}, ...]
  netmap         {"REF.PAD": "netname", ...}
  skip_unconnected_nets  bool - leave pads on `unconnected-*` pseudo-nets NETLESS
                 (board_init sets it after measuring what parity wants)
  fp_paths       [dir, ...] searched for "<Lib>.pretty/<Name>.kicad_mod"
  margin         gap between packed parts + border to outline (default 5.0)
  outline        {mode:"auto"} | {mode:"fixed", w, h}
  corner_radius  mm; 0 = square; null/absent = AUTO_CORNER_RADIUS, capped at
                 a tenth of the shorter side so a tiny board keeps its area
  mounting_holes {count: 0..4 | "auto", fp:"Lib:Name", inset, cu_edge} | null
                 "auto" = 4 when the shorter side >= AUTO_HOLES_MIN_SIDE, else
                 none. A hole with plated copper (the *_Pad_Via default) sits
                 at least its copper radius + cu_edge in from each edge, the
                 auto outline widens its border so corner parts clear the
                 hole's courtyard (a fixed outline is not widened, so there
                 "auto" gives none unless the parts already sit that border
                 in from every edge), and its pads join the ground net
                 (GND_NET_RE) so the ring is stitched, not floating.

verb "set_outline" (board_edit, U17): REPLACES the Edge.Cuts graphics of an
EXISTING board with a new rectangle (optionally rounded / notched), touching
nothing else. Same draw_outline() as "build", so an edited outline and an
initialized one are the same geometry by construction.

  board          input .kicad_pcb (the driver stages a copy)
  out            output .kicad_pcb path
  rect           [x1, y1, x2, y2] absolute mm
  corner_radius  mm (0 = square)
  cutouts        [{x, y, w, h}, ...] relative to rect's top-left, edge notches

Result JSON to stdout: {status, out, ...verb fields..., notes}. Exit 0 ok,
2 error.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import safelib  # lib/ is sys.path[0]: the worker runs as lib/board_swig.py

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pcbnew  # noqa: E402  (bundled python only)

# KiCad's own path-variable family (see intake.py's _KICAD_VAR_RE / kind
# "env"): on the no-container Linux host these are real OS env vars, exported
# by ~/.local/kicad10/env.sh, and the ONLY way to find the footprint library -
# sys.executable there is a wrapper that execs the system python, so it is not
# inside the KiCad install. Highest major version first; first that names an
# existing dir wins. Falls back to the sys.executable-derived layout, which is
# right when sys.executable IS the KiCad install (container bundled python,
# Windows kicad-cli.exe's python.exe).
_FP_ENV_RE = re.compile(r"^KICAD(\d*)_FOOTPRINT_DIR$")


def resolve_default_fp_root(environ: dict | None = None,
                            executable: str | None = None) -> Path:
    environ = os.environ if environ is None else environ
    executable = sys.executable if executable is None else executable
    hits = []
    for k, v in environ.items():
        m = _FP_ENV_RE.match(k)
        if m and v:
            hits.append((int(m.group(1)) if m.group(1) else -1, v))
    for _, v in sorted(hits, key=lambda kv: -kv[0]):
        p = Path(v)
        if p.is_dir():
            return p
    return Path(executable).parents[1] / "share" / "kicad" / "footprints"


DEFAULT_FP_ROOT = resolve_default_fp_root()


def mm(x: float, y: float) -> "pcbnew.VECTOR2I":
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


EDGE_W = 0.1  # mm, Edge.Cuts line width
# Owner, #ai-ee 1790534754.490679: "it would be nice if PCBs got nice mounting
# holes with exposed copper and vias in them and rounded edges" - so these are
# the defaults for a new board, and an explicit caller value always wins.
AUTO_CORNER_RADIUS = 2.0    # mm, a new board's corners unless the caller says
AUTO_HOLES_MIN_SIDE = 30.0  # mm, shorter side from which "auto" gives 4 holes
HOLE_COURTYARD_GAP = 0.25   # mm, corner part courtyard to hole courtyard
GND_NET_RE = re.compile(r"(^|/)(GND|DGND|GNDD|PGND)$", re.IGNORECASE)
_SQRT_HALF = 0.7071067811865476


def _edge(board, shape_t):
    s = pcbnew.PCB_SHAPE(board, shape_t)
    s.SetLayer(pcbnew.Edge_Cuts)
    s.SetWidth(pcbnew.FromMM(EDGE_W))
    return s


_CUT_TOL = 0.001  # mm; a cutout within this of an edge is an edge notch


def _classify_cutouts(cuts, x1, y1, x2, y2, r, notes):
    """Split cutouts into per-edge notches and interior windows.

    An edge notch reshapes the perimeter; an interior window is its own closed
    Edge.Cuts loop (KiCad reads inner loops as holes). A notch that would eat
    into a rounded corner is rejected rather than silently drawn, because the
    resulting self-intersecting outline fails polygonize downstream instead of
    erroring at the source.
    """
    per_edge = {"top": [], "right": [], "bottom": [], "left": []}
    interior = []
    for c in cuts or []:
        a, b = float(c["x"]), float(c["y"])
        w, h = float(c["w"]), float(c["h"])
        cx1, cy1, cx2, cy2 = x1 + a, y1 + b, x1 + a + w, y1 + b + h
        if not (x1 - _CUT_TOL <= cx1 < cx2 <= x2 + _CUT_TOL
                and y1 - _CUT_TOL <= cy1 < cy2 <= y2 + _CUT_TOL):
            notes.append(f"cutout {a},{b},{w},{h} lies outside the outline - skipped")
            continue
        side = None
        if abs(cy1 - y1) <= _CUT_TOL:
            side, span, depth = "top", (cx1, cx2), cy2 - y1
        elif abs(cy2 - y2) <= _CUT_TOL:
            side, span, depth = "bottom", (cx1, cx2), y2 - cy1
        elif abs(cx1 - x1) <= _CUT_TOL:
            side, span, depth = "left", (cy1, cy2), cx2 - x1
        elif abs(cx2 - x2) <= _CUT_TOL:
            side, span, depth = "right", (cy1, cy2), x2 - cx1
        if side is None:
            # An interior window is emitted as an inner Edge.Cuts loop, but
            # geom._parse_outline RETURNS on the first gr_rect it finds on
            # Edge.Cuts - so the window silently becomes the board outline
            # (measured: a 10x10 window on a 100x80 board parsed as area 100).
            # Every downstream consumer then sees a 10x10 board. Refuse rather
            # than emit something that mis-parses this badly.
            raise RuntimeError(
                f"interior cutout {a},{b},{w},{h} is not supported - it must "
                f"touch an outline edge to become a notch. Interior windows "
                f"mis-parse as the board outline downstream.")
        lo, hi = (x1 + r, x2 - r) if side in ("top", "bottom") else (y1 + r, y2 - r)
        if span[0] < lo - _CUT_TOL or span[1] > hi + _CUT_TOL:
            notes.append(f"cutout {a},{b},{w},{h} on the {side} edge overlaps a "
                         f"corner radius ({r} mm) - skipped; move it inboard or "
                         f"reduce --corner-radius")
            continue
        per_edge[side].append((span[0], span[1], depth))
    return per_edge, interior


def _edge_path(start_u, end_u, base_v, inward, notches, horizontal):
    """Points along one outline edge, detouring around each notch on it."""
    fwd = end_u >= start_u
    pts_u_v = [(start_u, base_v)]
    for lo, hi, depth in sorted(notches, key=lambda n: n[0], reverse=not fwd):
        near, far = (lo, hi) if fwd else (hi, lo)
        vin = base_v + inward * depth
        pts_u_v += [(near, base_v), (near, vin), (far, vin), (far, base_v)]
    pts_u_v.append((end_u, base_v))
    return [(u, v) if horizontal else (v, u) for u, v in pts_u_v]


def draw_outline(board, x1, y1, x2, y2, radius, notes, cutouts=None):
    """Draw the Edge.Cuts outline; return the effective corner radius (mm).

    radius <= 0 with no edge notches keeps the historical single SHAPE_T_RECT.
    Otherwise the outline is an explicit closed loop of segments plus 4 quarter
    arcs: pcbnew has no filleted-rect primitive, and geom.py / gerblib both
    consume gr_line and gr_arc on Edge.Cuts, so the loop is equivalent for every
    downstream consumer (DRC, planes_gen, gerber export, order_quote).

    Arcs are set via SetArcGeometry(start, mid, end) - the 3-point form, which
    avoids the start/end/center winding ambiguity that bites on mirrored axes
    (KiCad y grows downward).

    `cutouts` are {x,y,w,h} dicts RELATIVE to the outline's top-left corner and
    MUST touch an outline edge, becoming a notch in the perimeter. Interior
    windows are rejected - see _classify_cutouts.
    """
    r = max(0.0, float(radius))
    if r > 0:
        rmax = min(x2 - x1, y2 - y1) / 2.0 - EDGE_W
        if r > rmax:
            notes.append(f"corner radius {radius} clamped to {round(rmax, 3)} mm "
                         f"(half the shorter outline side)")
            r = max(rmax, 0.0)
        if r <= 0:
            notes.append("outline too small for a corner radius - drawn square")

    per_edge, interior = _classify_cutouts(cutouts, x1, y1, x2, y2, r, notes)
    has_notch = any(per_edge.values())

    if r <= 0 and not has_notch:
        rect = _edge(board, pcbnew.SHAPE_T_RECT)
        rect.SetStart(mm(x1, y1))
        rect.SetEnd(mm(x2, y2))
        board.Add(rect)
    else:
        #        start_u  end_u   base_v  inward  side      horizontal
        edges = ((x1 + r, x2 - r, y1, +1, "top", True),
                 (y1 + r, y2 - r, x2, -1, "right", False),
                 (x2 - r, x1 + r, y2, -1, "bottom", True),
                 (y2 - r, y1 + r, x1, +1, "left", False))
        for su, eu, bv, inward, side, horiz in edges:
            pts = _edge_path(su, eu, bv, inward, per_edge[side], horiz)
            for (sx, sy), (ex, ey) in zip(pts, pts[1:]):
                if abs(sx - ex) < 1e-9 and abs(sy - ey) < 1e-9:
                    continue
                seg = _edge(board, pcbnew.SHAPE_T_SEGMENT)
                seg.SetStart(mm(sx, sy))
                seg.SetEnd(mm(ex, ey))
                board.Add(seg)
        if r > 0:
            # (corner centre, outward diagonal), walking the same direction
            for cx, cy, dx, dy, sx, sy, ex, ey in (
                    (x1 + r, y1 + r, -1, -1, x1, y1 + r, x1 + r, y1),
                    (x2 - r, y1 + r, +1, -1, x2 - r, y1, x2, y1 + r),
                    (x2 - r, y2 - r, +1, +1, x2, y2 - r, x2 - r, y2),
                    (x1 + r, y2 - r, -1, +1, x1 + r, y2, x1, y2 - r)):
                arc = _edge(board, pcbnew.SHAPE_T_ARC)
                arc.SetArcGeometry(
                    mm(sx, sy),
                    mm(cx + dx * r * _SQRT_HALF, cy + dy * r * _SQRT_HALF),
                    mm(ex, ey))
                board.Add(arc)

    assert not interior  # _classify_cutouts raises; kept as a tripwire
    return r


def load_fp(fpid: str, fp_paths: list[Path]):
    lib, name = fpid.split(":", 1)
    for root in fp_paths:
        cand = root / f"{lib}.pretty"
        if cand.is_dir():
            fp = pcbnew.FootprintLoad(str(cand), name)
            if fp is not None:
                return fp
    return None


def hole_geometry(fp) -> tuple[float, float]:
    """(copper_r, courtyard_r) in mm about the footprint origin. copper_r
    counts PLATED pads only, so a bare NPTH hole has 0.0 - its courtyard may
    overhang the edge as it always has, but a copper ring may not."""
    org = fp.GetPosition()
    cu = 0.0
    for pad in fp.Pads():
        if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            continue
        bb = pad.GetBoundingBox()
        d = pcbnew.ToMM((pad.GetPosition() - org).EuclideanNorm())
        cu = max(cu, d + pcbnew.ToMM(max(bb.GetWidth(), bb.GetHeight())) / 2.0)
    fp.BuildCourtyardCaches()
    crt = fp.GetCourtyard(pcbnew.F_CrtYd)
    cr = cu
    if crt.OutlineCount():
        cb = crt.BBox()
        cr = pcbnew.ToMM(max(cb.GetWidth(), cb.GetHeight())) / 2.0
    return cu, cr


def ground_net(nets: dict):
    """The board's ground net for mounting-hole copper: GND itself, else the
    first name GND_NET_RE matches, else None (the ring stays netless)."""
    if "GND" in nets:
        return nets["GND"]
    for name in sorted(nets):
        if GND_NET_RE.search(name):
            return nets[name]
    return None


def build(job: dict) -> dict:
    notes: list[str] = []
    fp_paths = [Path(p) for p in job.get("fp_paths", [])] + [DEFAULT_FP_ROOT]
    fp_paths = [p for p in fp_paths if p.is_dir()]

    board = pcbnew.CreateEmptyBoard()
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(int(job["layers"]))
    # JLC-compatible DRC minimums (the .kicad_pro / .kicad_dru refine these).
    ds.m_TrackMinWidth = pcbnew.FromMM(0.1)
    ds.m_ViasMinSize = pcbnew.FromMM(0.4)
    ds.m_MinThroughDrill = pcbnew.FromMM(0.2)
    ds.m_MinClearance = pcbnew.FromMM(0.1)

    nets: dict[str, "pcbnew.NETINFO_ITEM"] = {}

    def net_of(name: str):
        if name not in nets:
            item = pcbnew.NETINFO_ITEM(board, name)
            board.Add(item)
            nets[name] = item
        return nets[name]

    netmap = job["netmap"]
    skip_unconnected = bool(job.get("skip_unconnected_nets"))
    placed = []
    boxes = []  # (fp, w, h)
    for comp in job["components"]:
        fp = load_fp(comp["fp"], fp_paths)
        if fp is None:
            raise RuntimeError(f"footprint not found: {comp['fp']} (ref {comp['ref']})")
        lib, name = comp["fp"].split(":", 1)
        try:
            fp.SetFPID(pcbnew.LIB_ID(lib, name))
        except Exception as exc:
            notes.append(f"SetFPID {comp['ref']}: {exc}")
        fp.SetReference(comp["ref"])
        fp.SetValue(comp.get("value", ""))
        # Custom symbol fields (LCSC, MPN, ...) must exist on the footprint or
        # `drc --schematic-parity` warns footprint_symbol_field_mismatch.
        for fname, fval in (comp.get("fields") or {}).items():
            fp.SetField(fname, fval)
        for field in fp.GetFields():
            if field.GetName() in (comp.get("fields") or {}):
                field.SetVisible(False)  # metadata, not board art
        # KiCad's native do-not-populate flag is a SYMBOL attribute that must be
        # mirrored on the footprint, else `drc --schematic-parity` reports
        # footprint_symbol_mismatch "'Do not populate' settings differ". Only
        # FP_DNP is set: in_bom / on_board stay whatever the symbol said, and
        # flipping exclude_from_bom here would just trade one mismatch for
        # another.
        if comp.get("dnp"):
            fp.SetAttributes(fp.GetAttributes() | getattr(pcbnew, "FP_DNP", 0))
        board.Add(fp)
        for pad in fp.Pads():
            want = netmap.get(f"{comp['ref']}.{pad.GetNumber()}")
            # `unconnected-(...)` is a name the NETLIST EXPORTER invents for a
            # pin the schematic leaves unconnected, and whether the PCB must
            # carry it is NOT uniform: a flat schematic wants it, a hierarchical
            # one rejects it - both measured on KiCad 10.0.5, see
            # board_init._rejects_unconnected_nets. board_init decides by asking
            # the real parity checker and re-runs this worker with
            # skip_unconnected_nets on the rejection signature; do not guess the
            # rule here.
            if want and not (skip_unconnected
                             and want.startswith("unconnected-")):
                pad.SetNet(net_of(want))
        fp.BuildCourtyardCaches()
        bb = fp.GetBoundingBox(False, False)  # copper+courtyard, no text
        boxes.append((fp, pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())))
        placed.append(comp["ref"])

    # ---- shelf-pack parts so no courtyards overlap --------------------
    margin = float(job.get("margin", 5.0))
    x0 = y0 = 15.0
    n = max(len(boxes), 1)
    total_row_len = sum(w for _, w, _ in boxes) + margin * n
    largest_w = max((w for _, w, _ in boxes), default=30.0)
    rows = max(1, round(n ** 0.5))              # ~square arrangement
    row_limit = max(largest_w, total_row_len / rows)
    x = x0
    y = y0
    row_h = 0.0
    for fp, w, h in boxes:
        if x > x0 and x + w > x0 + row_limit:
            x = x0
            y += row_h + margin
            row_h = 0.0
        bb = fp.GetBoundingBox(False, False)
        ox, oy = pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY())
        px, py = pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y)
        fp.SetPosition(mm(px + (x - ox), py + (y - oy)))
        x += w + margin
        row_h = max(row_h, h)

    # component bounding box after placement
    board.BuildListOfNets()
    comp_bb = pcbnew.BOX2I()
    for fp, _, _ in boxes:
        comp_bb.Merge(fp.GetBoundingBox(False, False))
    cx1, cy1 = pcbnew.ToMM(comp_bb.GetX()), pcbnew.ToMM(comp_bb.GetY())
    cx2 = pcbnew.ToMM(comp_bb.GetRight())
    cy2 = pcbnew.ToMM(comp_bb.GetBottom())

    # ---- mounting-hole geometry (decides the inset and the border) -----
    mh = job.get("mounting_holes")
    mh_count = mh.get("count", 0) if mh else 0
    hole_fpid = (mh or {}).get("fp", "MountingHole:MountingHole_3.2mm_M3")
    hole_cu = hole_crt = 0.0
    mh_inset = float((mh or {}).get("inset", margin / 2.0))
    border = margin
    if mh_count:
        probe = load_fp(hole_fpid, fp_paths)
        if probe is not None:
            hole_cu, hole_crt = hole_geometry(probe)
        if hole_cu > 0:
            # the copper ring must clear the edge by the fab's copper-to-edge
            # floor (+0.05 so it is not measured on the limit)
            need = hole_cu + float(mh.get("cu_edge", 0.3)) + 0.05
            if need > mh_inset:
                mh_inset = round(need, 3)
        # parts are packed `margin` in from the outline; the corner part's
        # courtyard corner is (border - inset) * sqrt2 from the hole centre
        border = max(margin, mh_inset
                     + (hole_crt + HOLE_COURTYARD_GAP) * _SQRT_HALF)

    # ---- outline ------------------------------------------------------
    ol = job.get("outline", {"mode": "auto"})
    if ol.get("mode") == "fixed":
        bw, bh = float(ol["w"]), float(ol["h"])
        ex1 = cx1 - (bw - (cx2 - cx1)) / 2.0
        ey1 = cy1 - (bh - (cy2 - cy1)) / 2.0
        ex2, ey2 = ex1 + bw, ey1 + bh
    else:
        ex1, ey1 = cx1 - border, cy1 - border
        ex2, ey2 = cx2 + border, cy2 + border
    short_side = min(ex2 - ex1, ey2 - ey1)
    if mh_count == "auto":
        mh_count = 4 if short_side >= AUTO_HOLES_MIN_SIDE else 0
        notes.append(f"mounting holes auto: {mh_count} (shorter side "
                     f"{round(short_side, 1)} mm, 4 from "
                     f"{AUTO_HOLES_MIN_SIDE} mm)")
        # A fixed outline is not widened for the holes, so "auto" only adds
        # them where the parts already sit `border` in from every edge;
        # otherwise a corner hole would land on a corner part's courtyard.
        gap = min(cx1 - ex1, cy1 - ey1, ex2 - cx2, ey2 - cy2)
        if mh_count and ol.get("mode") == "fixed" and gap < border:
            mh_count = 0
            notes.append(f"mounting holes auto: 0 - the fixed outline leaves "
                         f"{round(gap, 2)} mm between the parts and an edge, "
                         f"and a corner hole needs {round(border, 2)} mm")
    mh_count = int(mh_count)
    # A corner radius larger than the mounting-hole inset would leave the hole
    # inside the rounded-away quadrant. Shrink the radius rather than move the
    # hole: parts are already packed around the holes at this inset, so moving a
    # hole inward collides with the shelf grid (H1 into C1's courtyard).
    req_r = job.get("corner_radius")
    if req_r is None:
        req_r = min(AUTO_CORNER_RADIUS, round(short_side / 10.0, 2))
    req_r = float(req_r)
    if req_r > 0 and mh_count > 0:
        if req_r > mh_inset:
            notes.append(f"corner radius {req_r} clamped to the mounting-hole "
                         f"inset {mh_inset} mm - raise --margin for a larger "
                         f"radius")
            req_r = mh_inset
    cutouts = job.get("cutouts") or []
    corner_r = draw_outline(board, ex1, ey1, ex2, ey2, req_r, notes,
                            cutouts=cutouts)
    # absolute cutout rects, for callers translating keepouts into board space
    cut_abs = [[round(ex1 + float(c["x"]), 3), round(ey1 + float(c["y"]), 3),
                round(ex1 + float(c["x"]) + float(c["w"]), 3),
                round(ey1 + float(c["y"]) + float(c["h"]), 3)] for c in cutouts]

    # ---- mounting holes at outline corners ----------------------------
    holes = 0
    hole_net = ground_net(nets) if hole_cu > 0 else None
    if mh_count > 0:
        inset = mh_inset
        fpid = hole_fpid
        corners = [(ex1 + inset, ey1 + inset), (ex2 - inset, ey1 + inset),
                   (ex2 - inset, ey2 - inset), (ex1 + inset, ey2 - inset)]
        for i, (hx, hy) in enumerate(corners[:mh_count]):
            # a plated ring must also keep cu_edge off a notch's edges
            keep = hole_cu + float(mh.get("cu_edge", 0.3)) if hole_cu else 0.0
            if any(math.hypot(max(cx1 - hx, 0.0, hx - cx2),
                              max(cy1 - hy, 0.0, hy - cy2)) <= keep
                   for cx1, cy1, cx2, cy2 in cut_abs):
                notes.append(f"mounting hole {i + 1} at ({round(hx, 2)},"
                             f"{round(hy, 2)}) falls in or against a cutout "
                             f"- skipped")
                continue
            hole = load_fp(fpid, fp_paths)
            if hole is None:
                notes.append(f"mounting-hole fp not found: {fpid}")
                break
            lib, name = fpid.split(":", 1)
            try:
                hole.SetFPID(pcbnew.LIB_ID(lib, name))
            except Exception:
                pass
            hole.SetReference(f"H{i + 1}")
            # board_only: mechanical, not in the schematic -> parity must ignore
            # it (else every hole is an "extra_footprint" warning).
            attrs = hole.GetAttributes()
            for flag in ("FP_BOARD_ONLY", "FP_EXCLUDE_FROM_POS_FILES",
                         "FP_EXCLUDE_FROM_BOM"):
                attrs |= getattr(pcbnew, flag, 0)
            hole.SetAttributes(attrs)
            board.Add(hole)
            hole.SetPosition(mm(hx, hy))
            if hole_net is not None:
                for pad in hole.Pads():
                    if pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                        pad.SetNet(hole_net)
            holes += 1
        if hole_cu > 0 and hole_net is None and holes:
            notes.append(f"no ground net found for the {fpid} copper - "
                         f"the mounting-hole rings are left netless")

    out = Path(job["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    if not board.Save(str(out)):
        raise RuntimeError(f"board.Save failed: {out}")
    return {
        "status": "pass", "out": str(out), "placed": placed,
        "nets": len(nets), "bbox": [round(ex1, 3), round(ey1, 3),
                                    round(ex2, 3), round(ey2, 3)],
        "corner_radius": round(corner_r, 3),
        "outline_origin": [round(ex1, 3), round(ey1, 3)],
        "cutouts": cut_abs,
        "mounting_holes": holes,
        "mounting_hole_inset": round(mh_inset, 3) if holes else None,
        "mounting_hole_net": (hole_net.GetNetname()
                              if hole_net is not None and holes else None),
        "notes": notes,
    }


def set_outline(job: dict) -> dict:
    """Replace the board's Edge.Cuts graphics with a new outline (U17).

    Board-level PCB_SHAPEs on Edge.Cuts are removed and redrawn; footprint
    graphics, copper, zones and text are untouched. RemoveNative, not Remove:
    Remove() hands ownership to python and a collected proxy turns
    board.Drawings() into a bare SwigPyObject (LEARNINGS [place_edit][kicad]).
    """
    board = pcbnew.LoadBoard(job["board"])
    removed = 0
    for d in list(board.GetDrawings()):
        if isinstance(d, pcbnew.PCB_SHAPE) and d.GetLayer() == pcbnew.Edge_Cuts:
            board.RemoveNative(d)
            removed += 1
    notes: list[str] = []
    x1, y1, x2, y2 = (float(v) for v in job["rect"])
    cutouts = job.get("cutouts") or []
    r = draw_outline(board, x1, y1, x2, y2,
                     float(job.get("corner_radius") or 0.0), notes,
                     cutouts=cutouts)
    out = Path(job["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    if not board.Save(str(out)):
        raise RuntimeError(f"board.Save failed: {out}")
    return {
        "status": "pass", "out": str(out), "removed_edge_items": removed,
        "bbox": [round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3)],
        "corner_radius": round(r, 3),
        "cutouts": [[round(x1 + float(c["x"]), 3), round(y1 + float(c["y"]), 3),
                     round(x1 + float(c["x"]) + float(c["w"]), 3),
                     round(y1 + float(c["y"]) + float(c["h"]), 3)]
                    for c in cutouts],
        "notes": notes,
    }


VERBS = {"build": build, "set_outline": set_outline}


def main() -> int:
    try:
        job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        verb = job.get("verb", "build")
        if verb not in VERBS:
            raise RuntimeError(f"unknown verb {verb!r} "
                               f"(have: {', '.join(sorted(VERBS))})")
        with safelib.board_locks(job):
            result = VERBS[verb](job)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        import traceback
        print(json.dumps({"status": "error", "error": str(exc),
                          "trace": traceback.format_exc()}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
