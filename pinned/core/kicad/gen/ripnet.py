"""core: remove every track/via of the given nets (rip-up for a reroute).
  ~/.cache/hwde/bin/python3 kicad/gen/ripnet.py NET [NET ...]"""
import sys
from pathlib import Path
import pcbnew
pcb = str(Path(__file__).resolve().parents[2] / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
nets = set(sys.argv[1:])
gone = [t for t in b.GetTracks() if t.GetNetname() in nets]
for t in gone:
    b.Remove(t)
b.Save(pcb)
print("removed", len(gone))
