"""core: a same-net copper patch with a via array, to carry power current
between a pad and the inner planes (review-2 POWER-1: >= 6 x 0.3 mm vias at
every layer change of the 5 A path).

Adds an F.Cu zone (the patch) of NET over a rectangle, priority above the
GND pour, solid pad connections; then drops up to N vias inside it where they
clear other-net copper on every layer by CLR and every hole by the 0.5 mm
hole-to-hole floor. The zone fill joins the pad and the vias.

  KiCad's python: kicad/gen/via_array.py PCB NET X0 Y0 X1 Y1 N [--no-zone] [--layer F.Cu]
"""
import math
import sys

import pcbnew

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = [a for a in sys.argv[1:] if a.startswith("--")]
pcb, net = args[0], args[1]
x0, y0, x1, y1 = (float(v) for v in args[2:6])
want = int(args[6])
layer_name = "F.Cu"
for f in flags:
    if f.startswith("--layer="):
        layer_name = f.split("=", 1)[1]
VIA_D, VIA_H, CLR, H2H, PITCH = 0.6, 0.3, 0.2, 0.5, 0.2
MM = pcbnew.FromMM

b = pcbnew.LoadBoard(pcb)
ni = b.FindNet(net)
layer = b.GetLayerID(layer_name)

if "--no-zone" not in flags:
    z = pcbnew.ZONE(b)
    z.SetNet(ni)
    z.SetLayer(layer)
    z.SetAssignedPriority(5)
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    z.SetLocalClearance(MM(0.2))
    z.SetMinThickness(MM(0.25))
    z.SetZoneName(f"PATCH_{net}")
    o = z.Outline()
    o.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        o.Append(MM(x), MM(y))
    b.Add(z)

# obstacles: other-net copper on every copper layer, every hole
obst = []      # (kind, geom..., net)
holes = []
for t in b.GetTracks():
    if t.GetClass() == "PCB_VIA":
        p = t.GetPosition()
        holes.append((pcbnew.ToMM(p.x), pcbnew.ToMM(p.y), pcbnew.ToMM(t.GetDrill()) / 2))
        if t.GetNetname() != net:
            obst.append(("c", pcbnew.ToMM(p.x), pcbnew.ToMM(p.y), pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2))
    elif t.GetNetname() != net:
        s, e = t.GetStart(), t.GetEnd()
        obst.append(("s", pcbnew.ToMM(s.x), pcbnew.ToMM(s.y), pcbnew.ToMM(e.x), pcbnew.ToMM(e.y),
                     pcbnew.ToMM(t.GetWidth()) / 2))
for fp in b.GetFootprints():
    for p in fp.Pads():
        if p.GetDrillSize().x > 0:
            c = p.GetPosition()
            holes.append((pcbnew.ToMM(c.x), pcbnew.ToMM(c.y), pcbnew.ToMM(max(p.GetDrillSize().x, p.GetDrillSize().y)) / 2))
        # every pad is an obstacle: other nets for clearance, same-net SMD
        # pads too (no via-in-pad: open vias wick the solder away)
        if p.GetNetname() != net or not p.GetNetname() or p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
            bb = p.GetBoundingBox()
            obst.append(("r", pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()), pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())))
eb = b.GetBoardEdgesBoundingBox()
E = (pcbnew.ToMM(eb.GetX()) + 0.6, pcbnew.ToMM(eb.GetY()) + 0.6, pcbnew.ToMM(eb.GetRight()) - 0.6, pcbnew.ToMM(eb.GetBottom()) - 0.6)


def seg_d(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def ok(x, y):
    if not (E[0] <= x <= E[2] and E[1] <= y <= E[3]):
        return False
    r = VIA_D / 2
    for o in obst:
        if o[0] == "c" and math.hypot(x - o[1], y - o[2]) < o[3] + r + CLR:
            return False
        if o[0] == "s" and seg_d(x, y, o[1], o[2], o[3], o[4]) < o[5] + r + CLR:
            return False
        if o[0] == "r" and (o[1] - r - CLR < x < o[3] + r + CLR and o[2] - r - CLR < y < o[4] + r + CLR):
            return False
    for hx, hy, hr in holes:
        if math.hypot(x - hx, y - hy) - hr - VIA_H / 2 < H2H:
            return False
    return True


placed = []
m = VIA_D / 2 + 0.1
gx = [x0 + m + i * PITCH for i in range(int((x1 - x0 - 2 * m) / PITCH) + 1)]
gy = [y0 + m + j * PITCH for j in range(int((y1 - y0 - 2 * m) / PITCH) + 1)]
for y in gy:
    for x in gx:
        if len(placed) >= want:
            break
        if ok(x, y):
            v = pcbnew.PCB_VIA(b)
            v.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
            v.SetWidth(MM(VIA_D))
            v.SetDrill(MM(VIA_H))
            v.SetNet(ni)
            b.Add(v)
            placed.append((round(x, 2), round(y, 2)))
            holes.append((x, y, VIA_H / 2))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print(net, "vias", len(placed), "of", want, placed)
