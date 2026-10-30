"""core: resize given pads of a placed footprint (review-2 DFM-6: USB4085
pins 0.65 -> 0.70 mm for JLC's 0.15 mm multilayer annulus).
  KiCad's python: kicad/gen/pad_size.py PCB REF SIZE_MM [PAD ...] (all round pads if none)"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
fp = b.FindFootprintByReference(sys.argv[2])
s = pcbnew.FromMM(float(sys.argv[3]))
want = set(sys.argv[4:])
n = 0
for p in fp.Pads():
    if (not want and p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE) or p.GetNumber() in want:
        p.SetSize(pcbnew.VECTOR2I(s, s))
        n += 1
b.Save(sys.argv[1])
print(sys.argv[2], n, "pads ->", sys.argv[3])
