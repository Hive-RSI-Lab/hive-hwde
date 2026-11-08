"""Move open vias off solder pads (check_wicking, hwde 0600487; Lite pre-order round).
    KiCad's python: kicad/gen/wick_fix.py kicad/lite-main.kicad_pcb

Three cases:
- DELETE: the pad already reaches another via of its net by track, so the
  in-pad via only steals solder.
- MOVE: search outward on a 25 um grid for the nearest spot with the via's
  copper at least DAM from every same-net SMD pad (so a tented mask dam stands
  between the barrel and the joint) and clear of everything else on both
  layers, then bridge old spot to new spot on F.Cu (and on B.Cu if a track
  ended there), as nudge_vias.py does. GND vias on the pour need no B stub.
- FIXED: ESP32's exposed pad, where the via has to land in the gaps of the
  paste/mask windows and not next to a pad.
Zones are not refilled here: run kicad-cli pcb drc --refill-zones --save-board.
Then kicad/gen/i2s_respace.py, kicad/gen/vbus_neck.py and kicad/gen/grid45.py (which puts these stubs on the 45-degree grid).
"""
import math
import sys

import pcbnew

MM = pcbnew.ToMM
DAM, CLR, HOLE = 0.10, 0.16, 0.50
VIA_R, DRILL_R = 0.30, 0.15
DELETE = [(51.1, 99.863), (71.1676, 87.98), (86.55, 99.6), (83.0, 92.95)]
#          C30.2           R10.2            C25.1          L5.2
# R10.2's via was also the one tie between the F.Cu island under R10 and a 12 x 37 mm
# B.Cu GND island: three stitch vias tie that island to the main F.Cu pour instead.
STITCH = [(73.0, 95.5), (72.3, 103.2), (68.4, 109.8)]
MOVE = [  # (x, y, F stub width): stubs follow the net's load
    (66.38, 93.0, 0.2),       # Q12.1 /MUTE_DET
    (64.9, 88.05, 0.2),       # Q13.2 /MUTE_S
    (62.94, 89.02, 0.2),      # Q13.3 /SUB_L
    (62.8, 91.15, 0.2),       # Q14.2 /MUTE_S
    (86.3412, 79.148, 0.2),   # C19.2 /PSW_G
    (105.446, 109.085, 0.25), # U8.14 GND
    (82.505, 72.223, 0.6),    # D1.2 GND
    (87.065, 111.323, 0.6), (87.065, 112.523, 0.6), (87.065, 113.723, 0.6),     # C58.2 GND
    (117.365, 110.843, 0.6), (117.365, 112.043, 0.6), (117.365, 113.243, 0.6),  # C59.2 GND
]
FIXED = [((38.259, 131.645), (38.05, 131.6)), ((40.644, 128.029), (40.80, 128.03))]  # U5.39

b = pcbnew.LoadBoard(sys.argv[1])


def segd(p, a, c):
    ax, ay, cx, cy = *a, *c
    dx, dy = cx - ax, cy - ay
    n = dx * dx + dy * dy
    t = 0 if n == 0 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / n))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def rectd(p, r):
    dx = max(r[0] - p[0], 0, p[0] - r[2])
    dy = max(r[1] - p[1], 0, p[1] - r[3])
    return math.hypot(dx, dy)


def xy(v):
    return (MM(v.x), MM(v.y))


# snapshot everything once; moved vias update it
vias, tracks, pads = [], [], []
for t in b.GetTracks():
    if t.GetClass() == "PCB_VIA":
        vias.append([xy(t.GetPosition()), t.GetNetname(), t])
    else:
        tracks.append((xy(t.GetStart()), xy(t.GetEnd()), MM(t.GetWidth()) / 2, t.GetNetname(), t.GetLayer(), t))
for f in b.GetFootprints():
    for p in f.Pads():
        if not p.IsOnCopperLayer():
            continue
        bb = p.GetBoundingBox()
        pads.append(((MM(bb.GetX()), MM(bb.GetY()), MM(bb.GetRight()), MM(bb.GetBottom())),
                     p.GetNetname(), p.IsOnLayer(pcbnew.F_Cu), p.IsOnLayer(pcbnew.B_Cu),
                     xy(p.GetPosition()) if p.HasHole() else None))
zones = []  # outlines of what a moved via must stay out of, as plain points (see below)
for z in b.Zones():
    rule = z.GetIsRuleArea()
    if rule and not z.GetDoNotAllowVias():
        continue
    if rule or z.GetNetname() != "GND":
        o = z.Outline().COutline(0)
        zones.append(([xy(o.CPoint(i)) for i in range(o.PointCount())], rule, z.GetNetname()))
removals, additions = [], []  # applied last: KiCad 10's python goes bad after a board edit


def inzone(c, net):
    for poly, rule, zn in zones:
        if not rule and zn == net:
            continue
        inside = False
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            if (y0 > c[1]) != (y1 > c[1]) and c[0] < x0 + (c[1] - y0) / (y1 - y0) * (x1 - x0):
                inside = not inside
        if inside:
            return True
    return False


def via_ok(c, net, skip):
    for pc, n, t in vias:
        if t is skip:
            continue
        d = math.hypot(c[0] - pc[0], c[1] - pc[1])
        if d < 2 * DRILL_R + HOLE or (n != net and d < 2 * VIA_R + CLR):
            return False
    for s, e, r, n, l, _ in tracks:
        if n != net and segd(c, s, e) < VIA_R + r + CLR:
            return False
    for r, n, onf, onb, hole in pads:
        d = rectd(c, r)
        if n == net:
            if hole is None and d < VIA_R + DAM:
                return False
        elif d < VIA_R + CLR:
            return False
        if hole and math.hypot(c[0] - hole[0], c[1] - hole[1]) < HOLE + 0.8:
            return False
    return not inzone(c, net)


def stub_ok(a, c, net, layer, w):
    n = max(2, int(math.hypot(c[0] - a[0], c[1] - a[1]) / 0.05))
    for i in range(n + 1):
        q = (a[0] + (c[0] - a[0]) * i / n, a[1] + (c[1] - a[1]) * i / n)
        for s, e, r, tn, l, _ in tracks:
            if tn != net and l == layer and segd(q, s, e) < w / 2 + r + CLR:
                return False
        for r, tn, onf, onb, _ in pads:
            on = onf if layer == pcbnew.F_Cu else onb
            if on and tn != net and rectd(q, r) < w / 2 + CLR:
                return False
        for pc, tn, _ in vias:
            if tn != net and math.hypot(q[0] - pc[0], q[1] - pc[1]) < w / 2 + VIA_R + CLR:
                return False
    return True


def find(old, net, skip, w, bstub):
    best = None
    for i in range(-100, 101):
        for j in range(-100, 101):
            c = (old[0] + i * 0.025, old[1] + j * 0.025)
            d = math.hypot(i * 0.025, j * 0.025)
            if d > 2.5 or (best and d >= best[0]):
                continue
            if via_ok(c, net, skip) and stub_ok(old, c, net, pcbnew.F_Cu, w) and (
                    not bstub or stub_ok(old, c, net, pcbnew.B_Cu, 0.2)):
                best = (d, c)
    return best


def add_track(a, c, w, layer, net):
    additions.append(("track", a, c, w, layer, net))
    tracks.append((a, c, w / 2, net, layer, None))


def add_via(c, net):
    additions.append(("via", c, net))
    vias.append([c, net, None])


def at(pos):
    for rec in vias:
        if math.hypot(rec[0][0] - pos[0], rec[0][1] - pos[1]) < 0.002:
            return rec
    raise SystemExit(f"no via at {pos}")


def b_ends(pos, net):
    return [t for t in tracks if t[3] == net and t[4] == pcbnew.B_Cu and
            (math.hypot(t[0][0] - pos[0], t[0][1] - pos[1]) < 0.002 or
             math.hypot(t[1][0] - pos[0], t[1][1] - pos[1]) < 0.002)]


for pos in DELETE:
    rec = at(pos)
    net = rec[1]
    for t in b_ends(pos, net):
        # a B.Cu spur that only served this via goes with it
        far = t[1] if math.hypot(t[0][0] - pos[0], t[0][1] - pos[1]) < 0.002 else t[0]
        if len([1 for r in vias if math.hypot(r[0][0] - far[0], r[0][1] - far[1]) < 0.002]):
            removals.append(t[5])
            tracks.remove(t)
    removals.append(rec[2])
    vias.remove(rec)
    print("deleted", pos, net)

# U7.21 /TAP_R_C has no free spot of its own: the corner of the QFN holds it
# between /TAP_L_C's via and track. Move /TAP_L_C's via out to (50.2, 93.4) and
# its F.Cu in along pin 20's row, which opens (51.0, 93.393) for /TAP_R_C.
for t in [t for t in tracks if t[3] == "/TAP_L_C" and (t[0][1] > 92.9 and t[1][1] > 92.9 or t[1] == (50.475, 93.4))]:
    removals.append(t[5])
    tracks.remove(t)
rec = at((50.475, 93.4))
removals.append(rec[2])
vias.remove(rec)
add_via((50.2, 93.4), "/TAP_L_C")
add_track((50.475, 92.8), (50.2, 93.4), 0.2, pcbnew.B_Cu, "/TAP_L_C")
add_track((50.2, 93.4), (50.4, 93.973), 0.2, pcbnew.F_Cu, "/TAP_L_C")
add_track((50.4, 93.973), (50.935, 93.973), 0.2, pcbnew.F_Cu, "/TAP_L_C")
rec = at((51.315, 93.393))
removals.append(rec[2])
vias.remove(rec)
add_via((51.0, 93.393), "/TAP_R_C")
add_track((51.315, 93.393), (51.0, 93.393), 0.2, pcbnew.F_Cu, "/TAP_R_C")
add_track((51.315, 93.393), (51.0, 93.393), 0.2, pcbnew.B_Cu, "/TAP_R_C")

for c in STITCH:
    add_via(c, "GND")

# L5.2's and C25.1's in-pad vias were the second of a pair (check_current: 0.8 A needs
# 2 vias): each gets its second via back on the +5V B.Cu track, off the pad.
add_via((84.5, 94.0), "+5V")
add_track((84.5, 94.0), (84.3, 93.2), 0.405, pcbnew.F_Cu, "+5V")
add_via((86.3, 100.85), "+5V")
add_track((86.3, 100.85), (86.6, 100.3), 0.405, pcbnew.F_Cu, "+5V")
add_track((85.9, 98.95), (86.3, 100.85), 0.405, pcbnew.B_Cu, "+5V")

for pos, new in FIXED:
    rec = at(pos)
    net = rec[1]
    removals.append(rec[2])
    vias.remove(rec)
    add_via(new, net)
    print("fixed", pos, "->", new, net)

for x, y, w in MOVE:
    pos = (x, y)
    rec = at(pos)
    net = rec[1]
    bs = bool(b_ends(pos, net))
    got = find(pos, net, rec[2], w, bs)
    if not got:
        print("NO SPOT", pos, net)
        continue
    d, c = got
    removals.append(rec[2])
    vias.remove(rec)
    add_via(c, net)
    add_track(pos, c, w, pcbnew.F_Cu, net)
    if bs:
        add_track(pos, c, 0.2, pcbnew.B_Cu, net)
    print(f"moved {pos} -> {c[0]:.3f},{c[1]:.3f} ({d:.2f} mm) {net} b_stub={bs}")
for it in removals:
    b.Remove(it)
V = lambda p: pcbnew.VECTOR2I(pcbnew.FromMM(p[0]), pcbnew.FromMM(p[1]))
for a in additions:
    if a[0] == "via":
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(V(a[1]))
        v.SetWidth(pcbnew.FromMM(0.6))
        v.SetDrill(pcbnew.FromMM(0.3))
        v.SetNet(b.FindNet(a[2]))
        b.Add(v)
    else:
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(a[1]))
        t.SetEnd(V(a[2]))
        t.SetWidth(pcbnew.FromMM(a[3]))
        t.SetLayer(a[4])
        t.SetNet(b.FindNet(a[5]))
        b.Add(t)
b.Save(sys.argv[1])
