"""core: rotate a placed part on the routed board by DEG and rip the copper
that touched its pads (miniroute re-routes it).
  ~/.cache/hwde/bin/python3 kicad/gen/turn.py REF DEG"""
import sys
from pathlib import Path
import pcbnew
pcb = str(Path(__file__).resolve().parents[2] / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
fp = b.FindFootprintByReference(sys.argv[1])
pads = list(fp.Pads())
gone = []
for t in b.GetTracks():
    for p in pads:
        if t.GetNetname() == p.GetNetname() and p.GetNetname() != "GND" and \
                (p.HitTest(t.GetStart()) or (t.GetClass() != "PCB_VIA" and p.HitTest(t.GetEnd()))):
            gone.append(t); break
for t in gone:
    b.Remove(t)
fp.SetOrientationDegrees(fp.GetOrientationDegrees() + float(sys.argv[2]))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print("ripped", len(gone))
