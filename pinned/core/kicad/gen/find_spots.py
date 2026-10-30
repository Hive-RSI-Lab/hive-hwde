"""Find free spots for parts added after routing, for board_update --placements.

For each new part (in order), search outward from a target point for a spot
where its courtyard, grown by a margin, touches no other courtyard, no top-side
track or via, no hole, no rule area and stays inside the board. Each placed
part becomes an obstacle for the next. Writes the placements JSON.

    KiCad's python: kicad/gen/find_spots.py kicad/lite-main.kicad_pcb SPEC.json OUT.json
SPEC: {"REF": {"near": [x, y], "size": [w, h], "deg": 0}, ...} in board mm
(a part already on the board uses its own courtyard; "size" is then optional)
(courtyard size at deg 0; absolute KiCad coordinates); "nets": [...] lists the
part's own nets, whose tracks it may sit on.
"""
import json
import math
import sys

import pcbnew

pcb, spec_path, out_path = sys.argv[1:4]
spec = json.load(open(spec_path))
b = pcbnew.LoadBoard(pcb)
MM = pcbnew.ToMM
MARGIN = 0.25          # courtyard-to-courtyard
COPPER = 0.35          # body edge to a top track/via/pad of another net

rects = []             # (x0, y0, x1, y1) keep-outs
for fp in b.GetFootprints():
    if fp.GetReference() in spec:
        continue
    fp.BuildCourtyardCaches()
    cy = fp.GetCourtyard(pcbnew.F_CrtYd)
    if cy.OutlineCount():
        bb = cy.BBox()
    else:
        bb = fp.GetBoundingBox(False)
    rects.append((MM(bb.GetX()), MM(bb.GetY()), MM(bb.GetRight()), MM(bb.GetBottom())))
    for p in fp.Pads():
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            pb = p.GetBoundingBox()
            rects.append((MM(pb.GetX()), MM(pb.GetY()), MM(pb.GetRight()), MM(pb.GetBottom())))
segs = []
for t in b.GetTracks():
    if t.GetClass() == "PCB_VIA":
        c, r = t.GetPosition(), MM(t.GetWidth(pcbnew.F_Cu)) / 2
        segs.append((MM(c.x), MM(c.y), MM(c.x), MM(c.y), r, t.GetNetname()))
    elif t.GetLayer() == pcbnew.F_Cu:
        s, e = t.GetStart(), t.GetEnd()
        segs.append((MM(s.x), MM(s.y), MM(e.x), MM(e.y), MM(t.GetWidth()) / 2, t.GetNetname()))
for z in b.Zones():
    if z.GetIsRuleArea():
        bb = z.GetBoundingBox()
        rects.append((MM(bb.GetX()), MM(bb.GetY()), MM(bb.GetRight()), MM(bb.GetBottom())))
eb = b.GetBoardEdgesBoundingBox()
EX0, EY0, EX1, EY1 = MM(eb.GetX()) + 1, MM(eb.GetY()) + 1, MM(eb.GetRight()) - 1, MM(eb.GetBottom()) - 1


def seg_rect_dist(sx, sy, ex, ey, x0, y0, x1, y1):
    """Distance from a segment to an axis-aligned rectangle (sampled)."""
    n = max(2, int(math.hypot(ex - sx, ey - sy) / 0.1))
    best = 1e9
    for i in range(n + 1):
        px, py = sx + (ex - sx) * i / n, sy + (ey - sy) * i / n
        dx = max(x0 - px, 0, px - x1)
        dy = max(y0 - py, 0, py - y1)
        best = min(best, math.hypot(dx, dy))
    return best


def free(x0, y0, x1, y1, ok_nets=()):
    if x0 < EX0 or y0 < EY0 or x1 > EX1 or y1 > EY1:
        return False
    for (a, bb, c, d) in rects:
        if x0 < c + MARGIN and x1 > a - MARGIN and y0 < d + MARGIN and y1 > bb - MARGIN:
            return False
    for (sx, sy, ex, ey, r, net) in segs:
        if net in ok_nets:
            continue
        if min(sx, ex) - r - COPPER > x1 or max(sx, ex) + r + COPPER < x0:
            continue
        if min(sy, ey) - r - COPPER > y1 or max(sy, ey) + r + COPPER < y0:
            continue
        if seg_rect_dist(sx, sy, ex, ey, x0, y0, x1, y1) < r + COPPER:
            return False
    return True


out = {}
onboard = {}
for fp in b.GetFootprints():
    if fp.GetReference() in spec:
        fp.BuildCourtyardCaches()
        cy = fp.GetCourtyard(pcbnew.F_CrtYd)
        bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
        dw, dh = MM(bb.GetWidth()), MM(bb.GetHeight())
        if round(fp.GetOrientationDegrees()) % 180:
            dw, dh = dh, dw          # back to the deg-0 frame
        onboard[fp.GetReference()] = (dw, dh)
for ref, s in spec.items():
    nx, ny = s["near"]
    w, h = onboard.get(ref, s.get("size"))
    deg = s.get("deg", 0)
    found = None
    for k in range(0, 400):
        rad = k * 0.25
        steps = max(1, int(2 * math.pi * rad / 0.25))
        for i in range(steps):
            a = 2 * math.pi * i / steps
            cx, cy = round(nx + rad * math.cos(a), 2), round(ny + rad * math.sin(a), 2)
            for dg in (deg, (deg + 90) % 180):
                d = (dg, w, h) if dg % 180 == 0 else (dg, h, w)
                x0, y0 = cx - d[1] / 2, cy - d[2] / 2
                if free(x0, y0, x0 + d[1], y0 + d[2], s.get("nets", ())):
                    found = (cx, cy, d[0], x0, y0, x0 + d[1], y0 + d[2])
                    break
            if found:
                break
        if found:
            break
    if not found:
        sys.exit(f"{ref}: no free spot near {nx},{ny}")
    cx, cy, dg, *r = found
    rects.append(tuple(r))
    out[ref] = {"x": cx, "y": cy, "deg": dg, "side": "front"}
    print(ref, out[ref], f"{math.hypot(cx - nx, cy - ny):.1f} mm from target")
json.dump(out, open(out_path, "w"), indent=1)
