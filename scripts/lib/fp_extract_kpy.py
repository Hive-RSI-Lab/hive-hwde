"""Run by KiCad's bundled python (pcbnew): write every footprint embedded in a
board to a .pretty library, unplaced (front side, 0 deg, at the origin), one
per footprint name, and relink the board's footprints to libname:name (the
name KiCad's schematic importers put in Footprint fields) and save it.
argv: board.kicad_pcb out.pretty libname. Prints JSON:
{"written": [...], "variants": {name: [refs whose copper differs from the
 first: saved and relinked as <name>_<ref>]},
 "pad_copper": {"converted": n, "left": [...]}}.

Package copper first: Eagle merges a copper polygon drawn in a package with
the pad it touches (SparkFun's castellated header: a rectangle beside each
half-hole; a WSON's centre pad). KiCad's importer keeps it as a footprint
graphic with no net, so DRC calls every track reaching that pad a short. A
copper polygon overlapping exactly one pad number becomes an extra SMD pad of
that number (same net); a non-rectangular one is listed under "left".
eda_io import calls it so an imported board's parts have a library to link to.
"""
import json
import os
import sys

import pcbnew

board_path, pretty, libname = sys.argv[1], sys.argv[2], sys.argv[3]
# argv[4], optional: a JSON file of Eagle slots (edasrc.Source.slots), "-"
# for none; argv[5], optional: the source format
slots = json.load(open(sys.argv[4])) if len(sys.argv) > 4 and sys.argv[4] != "-" else []
board = pcbnew.LoadBoard(board_path)
os.makedirs(pretty, exist_ok=True)
io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
seen, variants, written = {}, {}, []
converted, left, slotted = 0, [], []
MM = 1_000_000


def apply_slots(board):
    """Eagle Milling wires through a drilled pad -> the pad's oblong drill.
    Package coordinates are Eagle's (y up); KiCad's footprint frame is y down."""
    import math
    for sl in slots:
        fp = board.FindFootprintByReference(sl["ref"])
        pad = next((p for p in fp.Pads() if p.GetNumber() == sl["pad"]), None) if fp else None
        if pad is None:
            left.append(f"{sl['ref']}.{sl['pad']}: slot with no pad to put it on")
            continue
        dx, dy = sl["x2"] - sl["x1"], -(sl["y2"] - sl["y1"])
        length = math.hypot(dx, dy) + sl["w"]
        cx, cy = (sl["x1"] + sl["x2"]) / 2, -(sl["y1"] + sl["y2"]) / 2
        local = pad.GetFPRelativePosition()
        if math.hypot(local.x / MM - cx, local.y / MM - cy) > 0.05:
            left.append(f"{sl['ref']}.{sl['pad']}: slot centre is off the pad's; not applied")
            continue
        # drill size is in the pad's own frame
        rel = math.radians(pad.GetFPRelativeOrientation().AsDegrees())
        along_x = abs(math.cos(math.atan2(-dy, dx) + rel)) >= abs(math.sin(math.atan2(-dy, dx) + rel))
        w, l = int(round(sl["w"] * MM)), int(round(length * MM))
        pad.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
        pad.SetDrillSize(pcbnew.VECTOR2I(l, w) if along_x else pcbnew.VECTOR2I(w, l))
        slotted.append(f"{sl['ref']}.{sl['pad']} {sl['w']:.2f}x{length:.2f}")


def plan_pad_copper(fp):
    """Copper polygons in a footprint -> what to do with each. Reads only:
    SWIG's proxies (PADS iteration, BOX2I) go bad after a run of edits to a
    footprint in KiCad 10.0.6, so every read happens before any edit."""
    plan = []
    pads = list(fp.Pads())
    for item in list(fp.GraphicalItems()):
        sh = item.Cast()
        if not isinstance(sh, pcbnew.PCB_SHAPE) or not sh.IsOnCopperLayer():
            continue
        layer = sh.GetLayer()
        box = sh.GetBoundingBox()
        bx = (box.GetX(), box.GetY(), box.GetRight(), box.GetBottom())
        hits = {}
        for p in pads:
            pb = p.GetBoundingBox()
            if (p.IsOnLayer(layer) and p.GetNumber() and pb.GetX() <= bx[2] and bx[0] <= pb.GetRight()
                    and pb.GetY() <= bx[3] and bx[1] <= pb.GetBottom()):
                hits[p.GetNumber()] = p
        where = f"{fp.GetReference()} {pcbnew.BOARD.GetStandardLayerName(layer)}"
        # one pad, or several that are one net (a castellation's hole + its SMD half)
        nets = {p.GetNetCode() for p in hits.values()}
        if not hits or (len(hits) > 1 and (len(nets) != 1 or 0 in nets)):
            left.append(f"{where}: copper shape over {len(hits)} pads")
            continue
        src = hits[sorted(hits)[0]]
        pts = None
        if sh.GetShape() == pcbnew.SHAPE_T_POLY:
            outline = sh.GetPolyShape().Outline(0)
            pts = [(outline.CPoint(i).x, outline.CPoint(i).y) for i in range(outline.PointCount())]
        elif sh.GetShape() == pcbnew.SHAPE_T_RECTANGLE:
            pts = [bx[:2], bx[2:]]
        xs = sorted({q[0] for q in pts or []})
        ys = sorted({q[1] for q in pts or []})
        if pts and len(xs) == 2 and len(ys) == 2:
            plan.append(("rect", sh, src, layer, xs, ys))
        elif src.HasHole() and len(hits) == 1:
            # a ring round a drilled pad (a slot's annulus): the pad's own
            # copper, grown to the ring's extent as an oval
            rel = round(src.GetFPRelativeOrientation().AsDegrees()) % 180
            bw, bh = bx[2] - bx[0], bx[3] - bx[1]
            size = src.GetSize(pcbnew.PADSTACK.ALL_LAYERS)
            plan.append(("ring", sh, src, max(size.x, bh if rel == 90 else bw),
                         max(size.y, bw if rel == 90 else bh)))
        elif pts and len(pts) >= 3:
            # any other outline (SparkFun's rounded castellation half-pads):
            # a custom pad, the outline as its primitive
            plan.append(("poly", sh, src, layer, pts, bx))
        else:
            left.append(f"{where}: copper shape with no outline to turn into a pad")
    return plan


OUTLINE = None


def clip_to_board(pts):
    """pts (board coords) cut to the board outline: a castellation's copper
    overhangs the edge in Eagle and the router takes the overhang off, so
    the clipped shape is what gets made (and what DRC can pass)."""
    global OUTLINE
    if OUTLINE is None:
        OUTLINE = pcbnew.SHAPE_POLY_SET()
        if not board.GetBoardPolygonOutlines(OUTLINE, True):
            OUTLINE = False
        else:
            # 1 um inside: copper exactly on the outline is a collision to
            # KiCad's DRC whatever the rules say; 1 um changes no Gerber
            OUTLINE.Inflate(-1000, pcbnew.CORNER_STRATEGY_ALLOW_ACUTE_CORNERS, 100)
    if not OUTLINE:
        return pts
    sp = pcbnew.SHAPE_POLY_SET()
    sp.NewOutline()
    for x, y in pts:
        sp.Append(int(x), int(y))
    sp.BooleanIntersection(OUTLINE)
    if sp.OutlineCount() != 1:
        return pts
    o = sp.Outline(0)
    return [(o.CPoint(i).x, o.CPoint(i).y) for i in range(o.PointCount())]


def apply_pad_copper(fp, plan):
    global converted
    clip = fp.GetReference() in castellated
    grown = {}
    for step in plan:
        if step[0] == "ring":
            _, sh, src, w, h = step
            old = grown.get(id(src), (0, 0))
            grown[id(src)] = (max(old[0], w), max(old[1], h))
            src.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_OVAL)
            src.SetSize(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.VECTOR2I(*grown[id(src)]))
            fp.Remove(sh)
            converted += 1
            continue
        if step[0] == "rect":  # one path for all: built in the footprint frame
            _, sh, src, layer, xs, ys = step
            step = ("poly", sh, src, layer, [(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])], None)
        if step[0] == "poly":
            _, sh, src, layer, pts, _ = step
            if clip:
                pts = clip_to_board(pts)
            # built in the footprint's own frame (relative orientation 0), so
            # two placements of one footprint at different rotations come out
            # the same and match their library copy
            import math
            org = fp.GetPosition()
            a = -math.radians(fp.GetOrientationDegrees())
            ca, sa = math.cos(a), math.sin(a)
            loc = [((x - org.x) * ca + (y - org.y) * sa, -(x - org.x) * sa + (y - org.y) * ca)
                   for x, y in pts]
            xs2, ys2 = [q[0] for q in loc], [q[1] for q in loc]
            cx, cy = round((min(xs2) + max(xs2)) / 2), round((min(ys2) + max(ys2)) / 2)
            pad = pcbnew.PAD(fp)
            pad.SetNumber(src.GetNumber())
            pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            ls = pcbnew.LSET()
            ls.AddLayer(layer)
            pad.SetLayerSet(ls)
            pad.SetFPRelativePosition(pcbnew.VECTOR2I(cx, cy))
            pad.SetFPRelativeOrientation(pcbnew.EDA_ANGLE(0, pcbnew.DEGREES_T))
            pad.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_CUSTOM)
            pad.SetAnchorPadShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetSize(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.VECTOR2I(100000, 100000))
            vv = pcbnew.VECTOR_VECTOR2I()
            for x, y in loc:  # primitives are in the pad's own frame
                vv.append(pcbnew.VECTOR2I(round(x - cx), round(y - cy)))
            pad.AddPrimitivePoly(pcbnew.PADSTACK.ALL_LAYERS, vv, 0, True)
            pad.SetNetCode(src.GetNetCode())
            fp.Remove(sh)
            fp.Add(pad)
            pad.thisown = False
            converted += 1
            continue
        _, sh, src, layer, xs, ys = step
        pad = pcbnew.PAD(fp)
        pad.SetNumber(src.GetNumber())
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET()
        ls.AddLayer(layer)
        pad.SetLayerSet(ls)
        pad.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_RECTANGLE)
        pad.SetSize(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.VECTOR2I(xs[1] - xs[0], ys[1] - ys[0]))
        pad.SetPosition(pcbnew.VECTOR2I((xs[0] + xs[1]) // 2, (ys[0] + ys[1]) // 2))
        pad.SetOrientationDegrees(0)
        pad.SetNetCode(src.GetNetCode())
        fp.Remove(sh)
        fp.Add(pad)
        pad.thisown = False  # the footprint owns it now; else Python frees it
        converted += 1


# An Eagle <hole> is a bare drill; KiCad's importer gives it an NPTH pad
# wider than the drill on every copper layer (0.80 on a 0.65 hole), copper
# the source never had and DRC holds tracks off. argv[5] == "eagle" trims
# them to the drill.
trimmed = []
if len(sys.argv) > 5 and sys.argv[5] == "eagle":
    for fp in board.GetFootprints():
        for p in list(fp.Pads()):
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                d = p.GetDrillSize()
                sz = p.GetSize(pcbnew.PADSTACK.ALL_LAYERS)
                if sz.x > d.x or sz.y > d.y:
                    trimmed.append((p, pcbnew.VECTOR2I(d.x, d.y), fp.GetReference()))
    for p, d, _ in trimmed:
        p.SetSize(pcbnew.PADSTACK.ALL_LAYERS, d)

# Castellations: a plated hole whose centre sits on the board outline. Marked
# with KiCad's castellated property (fab outputs and DRC know it); the refs
# go back to eda_io, which lets that footprint's pads reach the edge.
castellated = {}
edges = []
for d in board.GetDrawings():
    if d.GetLayer() == pcbnew.Edge_Cuts and d.GetClass() == "PCB_SHAPE":
        sh = d.Cast()
        if sh.GetShape() == pcbnew.SHAPE_T_SEGMENT:
            a, b = sh.GetStart(), sh.GetEnd()
            edges.append((a.x, a.y, b.x, b.y))


def on_edge(x, y, tol):
    for ax, ay, bx, by in edges:
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        t = 0 if ll == 0 else max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / ll))
        if ((ax + t * dx - x) ** 2 + (ay + t * dy - y) ** 2) ** 0.5 <= tol:
            return True
    return False


for fp in board.GetFootprints():
    for p in list(fp.Pads()):
        if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and p.HasHole():
            c = p.GetPosition()
            if on_edge(c.x, c.y, max(50_000, p.GetDrillSize().x // 4)):
                p.SetProperty(pcbnew.PAD_PROP_CASTELLATED)
                castellated.setdefault(fp.GetReference(), 0)
                castellated[fp.GetReference()] += 1

# Placement type: Eagle has none and KiCad's importer leaves every footprint
# unspecified; check_fp_attr (and the CPL) need it. From the pads: SMD pads
# -> smd (THT legs only anchor it), only plated holes -> through_hole. A
# castellated footprint is the board's own edge, not a part: out of the BOM
# and the placement file.
attrs = {"smd": 0, "through_hole": 0, "not_placed": []}
for fp in board.GetFootprints():
    a = fp.GetAttributes()
    if a & (pcbnew.FP_SMD | pcbnew.FP_THROUGH_HOLE) or fp.IsBoardOnly():
        continue
    kinds = {p.GetAttribute() for p in fp.Pads()}
    if not kinds:
        continue
    ref = fp.GetReference()
    if ref in castellated:
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)
        attrs["not_placed"].append(ref)
    if pcbnew.PAD_ATTRIB_SMD in kinds:
        fp.SetAttributes(a | pcbnew.FP_SMD)
        attrs["smd"] += 1
    elif pcbnew.PAD_ATTRIB_PTH in kinds:
        fp.SetAttributes(a | pcbnew.FP_THROUGH_HOLE)
        attrs["through_hole"] += 1

plans = [(fp, plan_pad_copper(fp)) for fp in board.GetFootprints()]
apply_slots(board)
for fp, plan in plans:
    apply_pad_copper(fp, plan)


def shape(fp):
    """Pads as (number, size, shape, local position): enough to tell two
    footprints with one name but different copper apart."""
    out = []
    for p in fp.Pads():
        pos = p.GetFPRelativePosition()
        bb = p.GetBoundingBox()  # a custom pad's outline, not just its anchor
        out.append((p.GetNumber(), p.GetSize().x, p.GetSize().y, p.GetShape(),
                    round(pos.x / 1000), round(pos.y / 1000),
                    round(bb.GetWidth() / 1000), round(bb.GetHeight() / 1000)))
    return sorted(out)


for fp in board.GetFootprints():
    name = fp.GetFPID().GetLibItemName().wx_str()
    if not name:
        continue
    c = pcbnew.FOOTPRINT(fp)
    if c.IsFlipped():
        c.Flip(c.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
    c.SetOrientationDegrees(0)
    c.SetPosition(pcbnew.VECTOR2I(0, 0))
    sig = shape(c)
    fp.SetFPID(pcbnew.LIB_ID(libname, name))
    if name in seen and seen[name] == sig:
        continue
    if name in seen:
        # same name, different copper (a castellation clipped on the other
        # board edge): its own library footprint, so the board matches it
        variants.setdefault(name, []).append(fp.GetReference())
        name = f"{name}_{fp.GetReference()}"
        fp.SetFPID(pcbnew.LIB_ID(libname, name))
    seen[name] = sig
    c.SetFPID(pcbnew.LIB_ID(libname, name))
    c.SetReference("REF**")
    io.FootprintSave(pretty, c)
    written.append(name)
board.Save(board_path)
print(json.dumps({"written": sorted(written), "variants": variants,
                  "pad_copper": {"converted": converted, "left": left},
                  "slots": slotted, "npth_trimmed": [r for _, _, r in trimmed],
                  "castellated": castellated, "attrs": attrs}))
