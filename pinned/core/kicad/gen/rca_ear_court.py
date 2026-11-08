"""core: J4/J5's courtyard holds the side ear. One part per run (eco_rca.py's note on stale handles).

    python3.11 kicad/gen/rca_ear_court.py kicad/core.kicad_pcb J4, then the same with J5
"""
import sys

import pcbnew

M = pcbnew.FromMM
# from the 3D model, vendor drawing A-AV2-7A1: flange and legs +-8.85 up to z 2.1, local y -2.25..10.25; ear to +12.3, y 0.72..10.25
OUTLINE = [(-8, -3.45), (8, -3.45), (8, -2.3), (12.3, -2.3), (12.3, 10.3), (8, 10.3), (8, 21.8), (-8, 21.8),
           (-8, 10.3), (-8.9, 10.3), (-8.9, -2.3), (-8, -2.3)]
pcb, ref = sys.argv[1:3]
b = pcbnew.LoadBoard(pcb)
fp = next(f for f in b.GetFootprints() if f.GetReference() == ref)
for g in list(fp.GraphicalItems()):
    if g.GetLayer() == pcbnew.F_CrtYd:
        fp.Remove(g)
x0, y0 = fp.GetX(), fp.GetY()
flip = -1 if abs(fp.GetOrientationDegrees()) == 180 else 1
pts = [pcbnew.VECTOR2I(x0 + M(flip * x), y0 + M(flip * y)) for x, y in OUTLINE]
for a, c in zip(pts, pts[1:] + pts[:1]):
    sh = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
    sh.SetLayer(pcbnew.F_CrtYd)
    sh.SetWidth(M(0.05))
    sh.SetStart(a)
    sh.SetEnd(c)
    fp.Add(sh)
b.Save(pcb)
print(ref, "courtyard holds the ear")
