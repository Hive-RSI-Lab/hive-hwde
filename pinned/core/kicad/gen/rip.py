"""core: remove tracks/vias by UUID (rip-up before a miniroute retry).
  ~/.cache/hwde/bin/python3 kicad/gen/rip.py UUID [UUID ...]"""
import sys
from pathlib import Path
import pcbnew
pcb = str(Path(__file__).resolve().parents[2] / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
want = set(sys.argv[1:])
gone = [t for t in b.GetTracks() if t.m_Uuid.AsString() in want]
for t in gone:
    b.Remove(t)
b.Save(pcb)
print("removed", len(gone))
