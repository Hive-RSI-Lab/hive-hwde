"""core: replace a PATCH_<net> zone's outline (the via_array patch) with a polygon.
  KiCad's python: kicad/gen/patch_outline.py PCB NET X0 Y0 X1 Y1 x,y x,y ...
  (the patch whose bbox matches X0 Y0 X1 Y1 within 0.05 mm gets the new outline)"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
net = sys.argv[2]
box = [float(v) for v in sys.argv[3:7]]
pts = [tuple(map(float, s.split(","))) for s in sys.argv[7:]]
for z in b.Zones():
    if z.GetZoneName() != f"PATCH_{net}":
        continue
    bb = z.GetBoundingBox()
    got = [pcbnew.ToMM(v) for v in (bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom())]
    if all(abs(a - c) < 0.05 for a, c in zip(got, box)):
        o = z.Outline()
        o.RemoveAllContours()
        o.NewOutline()
        for x, y in pts:
            o.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        print("patch", net, "reshaped")
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
