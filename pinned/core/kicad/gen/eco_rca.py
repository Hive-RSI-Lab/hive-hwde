"""RCA jacks J4/J5 (XDJK-0271-1650) get the library's holes (review-3 R3-LAYOUT-12).

Round 1 (DFM-1/DFM-2) fixed the library footprint (lib/aiee.pretty: pins 1/3
as plated 1.6 x 2.4 mm slots at y -1.55, the snap-in legs' 2.0 x 3.0 mm NPTH
at (+-7.65, +1.60)) but the instances on core's routed board kept the old
pattern: three round 1.6 mm holes and no leg holes, so the jack can't be
inserted. This puts the library's pads on J4/J5 in place; tracks still end
inside the grown pads 1/3, and pad 2 doesn't move.

    KiCad's python: kicad/gen/eco_rca.py kicad/core.kicad_pcb J4, then the same
    with J5 (the holes are redone each run, idempotent; the courtyard is per part)
"""
import sys

import pcbnew

pcb = sys.argv[1]
M = pcbnew.FromMM
SLOT_Y = -1.55
SLOT = (1.6, 2.4)
SLOT_PAD = (2.5, 3.3)
LEG = (2.0, 3.0)
LEGS = ((-7.65, 1.60), (7.65, 1.60))

b = pcbnew.LoadBoard(pcb)
for ref in ("J4", "J5"):
    fp = b.FindFootprintByReference(ref)
    for p in list(fp.Pads()):
        if p.GetNumber() in ("1", "3"):
            off = p.GetFPRelativePosition()
            p.SetFPRelativePosition(pcbnew.VECTOR2I(off.x, M(SLOT_Y)))
            p.SetShape(pcbnew.PAD_SHAPE_OVAL)
            p.SetSize(pcbnew.VECTOR2I(M(SLOT_PAD[0]), M(SLOT_PAD[1])))
            p.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
            p.SetDrillSize(pcbnew.VECTOR2I(M(SLOT[0]), M(SLOT[1])))
        elif p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            fp.Remove(p)
    for x, y in LEGS:
        p = pcbnew.PAD(fp)
        p.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        p.SetShape(pcbnew.PAD_SHAPE_OVAL)
        p.SetSize(pcbnew.VECTOR2I(M(LEG[0]), M(LEG[1])))
        p.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
        p.SetDrillSize(pcbnew.VECTOR2I(M(LEG[0]), M(LEG[1])))
        p.SetLayerSet(pcbnew.PAD.UnplatedHoleMask())
        p.SetNumber("")
        fp.Add(p)
        p.SetFPRelativePosition(pcbnew.VECTOR2I(M(x), M(y)))
    print(ref, "holes from the library")
b.Save(pcb)

# The legs at +-7.65 (2.0 x 3.0 at y +1.60) reach x +-8.65: the courtyard
# steps out to +-8.75 beside them only (hwde verify check_courtyard), so it
# stays clear of J11's beside J4
# and to +12.3 beside the side ear (rca_ear_eco.py)
OUTLINE = [(-8, -3.45), (8, -3.45), (8, -2.3), (12.3, -2.3), (12.3, 10.3), (8, 10.3), (8, 21.8), (-8, 21.8),
           (-8, 10.3), (-8.9, 10.3), (-8.9, -2.3), (-8, -2.3)]
# one part per run (kicad/gen/eco_rca.py PCB J4, then J5): after one
# footprint's courtyard is replaced, KiCad's python hands back stale handles
for ref in sys.argv[2:3] or ("J4",):
    b = pcbnew.LoadBoard(pcb)
    fp = b.FindFootprintByReference(ref)
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
    print(ref, "courtyard holds the legs")
    b.Save(pcb)
