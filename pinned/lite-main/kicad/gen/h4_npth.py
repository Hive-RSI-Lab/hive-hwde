"""Make mounting hole H4 a bare NPTH with no copper, and drop its GND via. H4
sits in the antenna's 15 mm side clearance (check_antenna), and the plated ring
was the only copper left there once the keep-out is widened (gen/keepout.py).
The hole stays where it is, so the box still fits. Refill after.
    KiCad's python: kicad/gen/h4_npth.py kicad/lite-main.kicad_pcb
"""
import os
import sys

import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
old = b.FindFootprintByReference("H4")
pos = old.GetPosition()
share = os.environ.get("KICAD_STOCK_FP") or os.path.join(
    os.path.dirname(os.path.realpath(sys.executable)), "..", "usr", "share", "kicad", "footprints")
new = pcbnew.FootprintLoad(os.path.join(share, "MountingHole.pretty"), "MountingHole_3.2mm_M3")
new.SetReference("H4")
new.SetValue("MountingHole_3.2mm_M3")
new.SetPosition(pos)
new.Reference().SetVisible(old.Reference().IsVisible())
new.SetAttributes(old.GetAttributes())
# the stock pad lists *.Cu though an NPTH hole carries none; check_antenna reads
# that as copper, so leave only the mask layers
masks = pcbnew.LSET()
masks.AddLayer(pcbnew.F_Mask)
masks.AddLayer(pcbnew.B_Mask)
for pad in new.Pads():
    pad.SetLayerSet(masks)
b.Remove(old)
b.Add(new)
c = pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)
gone = 0
for t in list(b.GetTracks()):
    p = t.GetPosition()
    if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND" and \
            abs(pcbnew.ToMM(p.x) - c[0]) < 3.5 and abs(pcbnew.ToMM(p.y) - c[1]) < 3.5:
        b.Remove(t)
        gone += 1
b.Save(sys.argv[1])
print("H4 now", new.GetFPIDAsString(), "vias removed", gone)
