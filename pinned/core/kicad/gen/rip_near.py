"""core: rip NET's tracks/vias within R mm of a part's pads.
  ~/.cache/hwde/bin/python3 kicad/gen/rip_near.py REF NET R"""
import sys
from pathlib import Path
import pcbnew
pcb = str(Path(__file__).resolve().parents[2] / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
fp = b.FindFootprintByReference(sys.argv[1]); net = sys.argv[2]; r = int(float(sys.argv[3]) * 1e6)
gone = [t for t in b.GetTracks() if t.GetNetname() == net and any(
        p.HitTest(t.GetStart(), r) or (t.GetClass() != "PCB_VIA" and p.HitTest(t.GetEnd(), r)) for p in fp.Pads())]
for t in gone:
    b.Remove(t)
b.Save(pcb)
print("removed", len(gone))
