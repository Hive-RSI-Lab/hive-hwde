"""core: remove via_array patch zones of a net inside a box (keeps the vias).
  KiCad's python: kicad/gen/drop_patch.py PCB NET X0 Y0 X1 Y1"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
net = sys.argv[2]
x0, y0, x1, y1 = (pcbnew.FromMM(float(v)) for v in sys.argv[3:7])
gone = []
for z in b.Zones():
    bb = z.GetBoundingBox()
    if z.GetZoneName() == f"PATCH_{net}" and bb.GetX() >= x0 and bb.GetRight() <= x1 and bb.GetY() >= y0 and bb.GetBottom() <= y1:
        gone.append(z)
for z in gone:
    b.Remove(z)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
print("patches removed", len(gone))
