"""core: tie every GND pour island on the outer layers to the In1 plane with a via.

After an ECO's new tracks cut the outer GND pours, some fragments (with part
GND pads on them) no longer reach the planes. For each filled polygon of the
NET's zones that isn't joined to the biggest copper group, place one via (two
if the island is large) at an interior point at least a via radius plus 0.1 mm
inside its outline and 0.5 mm hole-to-hole from every hole.

  KiCad's python: kicad/gen/island_vias.py PCB [NET]
"""
import math
import sys

import pcbnew

pcb = sys.argv[1]
net = sys.argv[2] if len(sys.argv) > 2 else "GND"
VIA_D, VIA_H, H2H = 0.6, 0.3, 0.5
b = pcbnew.LoadBoard(pcb)
ni = b.FindNet(net)
polys = []
for z in b.Zones():
    if z.GetNetname() != net or z.GetIsRuleArea():
        continue
    for L in z.GetLayerSet().Seq():
        f = z.GetFilledPolysList(L)
        for i in range(f.OutlineCount()):
            polys.append((L, f.Outline(i), f, i))
par = list(range(len(polys)))


def find(a):
    while par[a] != a:
        par[a] = par[par[a]]
        a = par[a]
    return a


items = []
holes = []
for t in b.GetTracks():
    if t.GetClass() == "PCB_VIA":
        p = t.GetPosition()
        holes.append((p.x, p.y, t.GetDrill() / 2))
        if t.GetNetname() == net:
            items.append([(p, None)])
    elif t.GetNetname() == net:
        items.append([(t.GetStart(), t.GetLayer()), (t.GetEnd(), t.GetLayer())])
for fp in b.GetFootprints():
    for p in fp.Pads():
        if p.GetDrillSize().x > 0:
            c = p.GetPosition()
            holes.append((c.x, c.y, max(p.GetDrillSize().x, p.GetDrillSize().y) / 2))
        if p.GetNetname() == net:
            th = p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
            items.append([(p.GetPosition(), None if th else p.GetLayer())])
for it in items:
    hits = [k for k, (L, ol, _, _) in enumerate(polys) for (pt, lay) in it
            if (lay is None or lay == L) and ol.PointInside(pt)]
    for h in hits[1:]:
        par[find(h)] = find(hits[0])
size = {}
for k, (L, ol, _, _) in enumerate(polys):
    size[find(k)] = size.get(find(k), 0) + abs(ol.Area())
main = max(size, key=size.get)
obst = []
for t in b.GetTracks():
    if t.GetNetname() == net:
        continue
    if t.GetClass() == "PCB_VIA":
        p = t.GetPosition()
        obst.append(("c", p.x, p.y, p.x, p.y, t.GetWidth(pcbnew.F_Cu) / 2))
    else:
        s, e = t.GetStart(), t.GetEnd()
        obst.append(("s", s.x, s.y, e.x, e.y, t.GetWidth() / 2))
for fp in b.GetFootprints():
    for p in fp.Pads():
        if p.GetNetname() != net:
            bb = p.GetBoundingBox()
            obst.append(("r", bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom(), 0))
CLRN = pcbnew.FromMM(VIA_D / 2 + 0.2)


def seg_d(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    u = 0 if L2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - u * dx, py - ay - u * dy)


def clear(x, y):
    for o in obst:
        if o[0] == "r":
            if o[1] - CLRN < x < o[3] + CLRN and o[2] - CLRN < y < o[4] + CLRN:
                return False
        elif seg_d(x, y, o[1], o[2], o[3], o[4]) < o[5] + CLRN:
            return False
    return True


inner = [b.GetLayerID("In1.Cu")]
r_in = pcbnew.FromMM(VIA_D / 2 + 0.1)
added = 0
for k, (L, ol, f, idx) in enumerate(polys):
    if find(k) == main or L not in (pcbnew.F_Cu, pcbnew.B_Cu):
        continue
    bb = ol.BBox()
    step = pcbnew.FromMM(0.2)
    best = None
    y = bb.GetY()
    while y <= bb.GetBottom() and best is None:
        x = bb.GetX()
        while x <= bb.GetRight():
            p = pcbnew.VECTOR2I(int(x), int(y))
            if ol.PointInside(p) and all(ol.PointInside(pcbnew.VECTOR2I(int(x + r_in * math.cos(a)), int(y + r_in * math.sin(a))))
                                         for a in [i * math.pi / 4 for i in range(8)]):
                if clear(x, y) and all(math.hypot(x - hx, y - hy) - hr - pcbnew.FromMM(VIA_H / 2) >= pcbnew.FromMM(H2H) for hx, hy, hr in holes):
                    best = p
                    break
            x += step
        y += step
    if best is None:
        print("island at", [round(pcbnew.ToMM(v), 2) for v in (bb.GetX(), bb.GetY())], b.GetLayerName(L), "no room for a via")
        continue
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(best)
    v.SetWidth(pcbnew.FromMM(VIA_D))
    v.SetDrill(pcbnew.FromMM(VIA_H))
    v.SetNet(ni)
    b.Add(v)
    holes.append((best.x, best.y, pcbnew.FromMM(VIA_H / 2)))
    # this island now joins the planes: merge it so it isn't visited again
    par[find(k)] = main
    added += 1
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print(net, "island vias", added)
