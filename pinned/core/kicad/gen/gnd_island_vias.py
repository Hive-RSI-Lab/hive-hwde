"""core: tie isolated GND pour fragments on F.Cu/B.Cu down to the In1 plane.

The router leaves GND fill fragments boxed in by tracks, each holding a GND
pad, with no via to the plane. For every fragment that holds no GND via or
through-hole GND pad, this puts one 0.7/0.3 mm via at the first grid point
that's 0.4 mm inside the fragment, clear of other-net copper on every layer,
and 0.5 mm hole-to-hole from every other hole. Then it refills.

  ~/.cache/hwde/bin/python3 kicad/gen/gnd_island_vias.py
"""
import json
from pathlib import Path
import pcbnew

B = Path(__file__).resolve().parents[2]
pcb = str(B / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
MM = 1e6
V = lambda x, y: pcbnew.VECTOR2I(int(x), int(y))
gnd = b.FindNet("GND")
tracks = list(b.GetTracks())
pads = [p for fp in b.GetFootprints() for p in fp.Pads()]
holes = [(t.GetPosition(), t.GetDrill() / 2) for t in tracks if t.GetClass() == "PCB_VIA"]
holes += [(p.GetPosition(), max(p.GetDrillSize().x, p.GetDrillSize().y) / 2)
          for p in pads if p.GetDrillSize().x > 0]
gnd_through = [t.GetPosition() for t in tracks if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"]
gnd_through += [p.GetPosition() for p in pads if p.GetNetname() == "GND" and p.GetDrillSize().x > 0]


def clear(pt):
    for t in tracks:
        if t.GetNetname() != "GND" and t.HitTest(pt, int(0.35 * MM + 0.13 * MM + t.GetWidth() / 2 - t.GetWidth() / 2)):
            return False
    for p in pads:
        if p.GetNetname() != "GND" and p.HitTest(pt, int(0.48 * MM)):
            return False
    for (h, r) in holes:
        if (h.x - pt.x) ** 2 + (h.y - pt.y) ** 2 < (0.15 * MM + r + 0.5 * MM) ** 2:
            return False
    for z in b.Zones():
        if z.GetNetname() not in ("GND",) and not z.GetIsRuleArea():
            pass  # inner/outer pours of other nets refill around the via
    return True


added = 0
for z in b.Zones():
    if z.GetNetname() != "GND":
        continue
    for lay in z.GetLayerSet().Seq():
        if b.GetLayerName(lay) not in ("F.Cu", "B.Cu"):
            continue
        polys = z.GetFilledPolysList(lay)
        for i in range(polys.OutlineCount()):
            if any(polys.Contains(p, i) for p in gnd_through):
                continue
            bb = polys.Outline(i).BBox()
            step = int(0.25 * MM)
            done = False
            y = bb.GetTop()
            while y <= bb.GetBottom() and not done:
                x = bb.GetLeft()
                while x <= bb.GetRight() and not done:
                    pt = V(x, y)
                    m = int(0.4 * MM)
                    if all(polys.Contains(V(x + dx, y + dy), i) for dx, dy in
                           ((0, 0), (m, 0), (-m, 0), (0, m), (0, -m))) and clear(pt):
                        v = pcbnew.PCB_VIA(b)
                        v.SetPosition(pt); v.SetWidth(int(0.7 * MM)); v.SetDrill(int(0.3 * MM))
                        v.SetNet(gnd); b.Add(v); tracks.append(v)
                        holes.append((pt, 0.15 * MM)); gnd_through.append(pt)
                        added += 1; done = True
                    x += step
                y += step
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print(json.dumps({"gnd_island_vias": added}))
