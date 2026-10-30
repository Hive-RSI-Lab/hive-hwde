"""Drop footprint silk segments that cross a pad's mask opening, and hide
reference texts that sit on a pad (review-2 ECO silk tidy).
    KiCad's python: kicad/gen/silk_clear.py PCB REF[,REF...] [HIDE_REF,...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
pads = [p for fp in b.GetFootprints() for p in fp.Pads()]
drop = []
for ref in sys.argv[2].split(","):
    fp = b.FindFootprintByReference(ref)
    fpads = [p.GetBoundingBox() for p in fp.Pads()]
    for g in list(fp.GraphicalItems()):
        if g.GetLayer() != pcbnew.F_SilkS:
            continue
        bb = g.GetBoundingBox()
        bb.Inflate(pcbnew.FromMM(0.1))
        if any(bb.Intersects(pb) for pb in fpads):
            drop.append((fp, g, ref))
for fp, g, ref in drop:
    fp.Remove(g)
    print(ref, "silk segment removed")
for ref in (sys.argv[3].split(",") if len(sys.argv) > 3 else []):
    b.FindFootprintByReference(ref).Reference().SetVisible(False)
    print(ref, "reference hidden (assembly drawing keeps it)")
b.Save(sys.argv[1])
