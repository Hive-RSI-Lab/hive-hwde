"""core: delete footprint silk graphics of the given parts that run within
0.25 mm of the board edge or past it (through-hole parts whose bodies
overhang the edge on purpose, J1 and U18).
  KiCad's python: kicad/gen/silk_edge.py PCB REF [REF ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
eb = b.GetBoardEdgesBoundingBox()
m = pcbnew.FromMM(0.25)
x0, y0, x1, y1 = eb.GetX() + m, eb.GetY() + m, eb.GetRight() - m, eb.GetBottom() - m
drop = []
for ref in sys.argv[2:]:
    fp = b.FindFootprintByReference(ref)
    for g in list(fp.GraphicalItems()):
        if g.GetLayer() != pcbnew.F_SilkS:
            continue
        bb = g.GetBoundingBox()
        if bb.GetX() < x0 or bb.GetY() < y0 or bb.GetRight() > x1 or bb.GetBottom() > y1:
            drop.append((fp, g, ref))
for fp, g, ref in drop:
    fp.Remove(g)
    print(ref, "edge silk removed")
b.Save(sys.argv[1])
