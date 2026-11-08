"""core: U19 (TOSLINK, PLR237/T8BK) gets its two locating-post holes (KiCad python).
- The T8BK has two 1.70 mm snap posts 5.60 apart, 2.61 beyond the NC row
  (Everlight DPL-0000262/263 Rev.1 p5, "PCB Layout for Electrical Circuit").
  lib/aiee.pretty's footprint has them (review-2 DFM-12), but the routed
  board kept the old T10BK copy without them, so the part couldn't seat.
  Adds them as NPTH 1.70 at (+-2.80, +3.92), as in the library footprint.
- Moves the GND stitching via that sat inside the -x post hole 1.9 mm along x,
  between the posts.
- Refills the zones.
"""
from pathlib import Path
import pcbnew
pcb = str(Path(__file__).resolve().parents[2] / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
mm = pcbnew.FromMM
u = b.FindFootprintByReference("U19")
if not any(p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH for p in u.Pads()):
    for x in (-2.80, 2.80):
        p = pcbnew.PAD(u)
        p.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        p.SetSize(pcbnew.VECTOR2I(mm(1.70), mm(1.70)))
        p.SetDrillSize(pcbnew.VECTOR2I(mm(1.70), mm(1.70)))
        p.SetLayerSet(pcbnew.PAD.UnplatedHoleMask())
        p.SetNumber("")
        u.Add(p)
        p.SetFPRelativePosition(pcbnew.VECTOR2I(mm(x), mm(3.92)))
for t in b.GetTracks():
    if t.GetClass() == "PCB_VIA" and t.GetPosition() == pcbnew.VECTOR2I(mm(137.087), mm(127.197)):
        t.SetPosition(pcbnew.VECTOR2I(mm(139.087), mm(127.197)))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
