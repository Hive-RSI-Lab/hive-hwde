"""RCA jack (XDJK-0271-1650) holes that fit either reading of its drawing.

review-2 DFM-2 (2026-10-29): the EasyEDA land pattern puts signal pins 1 and 3
on rows 0.8 mm apart (y -1.95 / -1.15); Lian Xin's drawing A-AV2-7A1 reads as
one row. The pins are 1.0 x 0.3 mm (diagonal 1.04), so a 1.6 mm round hole
can't take an 0.8 mm error. Pins 1 and 3 become plated 1.6 x 2.4 mm slots
along y centred between the two readings (y -1.55), each covering both
positions with 0.28 mm to spare at the pin corners; the snap-in legs' holes
grow to the drawing's 2.0 x 3.0 mm. Applied to the library footprint (text) and
to J2/J3 on the routed board; tracks still end inside the grown pads.

    KiCad's python: kicad/gen/eco_rca.py kicad/lite-main.kicad_pcb
"""
import re
import sys
from pathlib import Path

import pcbnew

pcb = sys.argv[1]
ws = Path(pcb).resolve().parents[1]
mod = ws / "lib" / "aiee.pretty" / "CONN-TH_XDJK-0271-1650.kicad_mod"

SLOT_Y = -1.55
SLOT = (1.6, 2.4)       # drill, x by y
SLOT_PAD = (2.5, 3.3)   # 0.45 mm annulus
LEG = (2.0, 3.0)

t = mod.read_text()
t = re.sub(r'\(pad 1 thru_hole circle \(at -4\.00 -1\.95 0\.00\) \(size 2\.500 2\.500\)(.*?)\(drill 1\.599996\)\)',
           r'(pad 1 thru_hole oval (at -4.00 -1.55 0.00) (size 2.500 3.300)\1(drill oval 1.600 2.400))', t)
t = re.sub(r'\(pad 3 thru_hole circle \(at 4\.00 -1\.15 0\.00\) \(size 2\.500 2\.500\)(.*?)\(drill 1\.599996\)\)',
           r'(pad 3 thru_hole oval (at 4.00 -1.55 0.00) (size 2.500 3.300)\1(drill oval 1.600 2.400))', t)
t = t.replace("(size 1.700 2.400) (drill oval 1.700 2.400)", "(size 2.000 3.000) (drill oval 2.000 3.000)")
mod.write_text(t)

b = pcbnew.LoadBoard(pcb)
for ref in ("J2", "J3"):
    fp = b.FindFootprintByReference(ref)
    for p in fp.Pads():
        n = p.GetNumber()
        if n in ("1", "3"):
            off = p.GetFPRelativePosition()
            p.SetFPRelativePosition(pcbnew.VECTOR2I(off.x, pcbnew.FromMM(SLOT_Y)))
            p.SetShape(pcbnew.PAD_SHAPE_OVAL)
            p.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(SLOT_PAD[0]), pcbnew.FromMM(SLOT_PAD[1])))
            p.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
            p.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(SLOT[0]), pcbnew.FromMM(SLOT[1])))
        elif p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            p.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(LEG[0]), pcbnew.FromMM(LEG[1])))
            p.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(LEG[0]), pcbnew.FromMM(LEG[1])))
    print(ref, "holes updated")
b.Save(pcb)
