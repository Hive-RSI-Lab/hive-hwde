"""core: GND pours drop unconnected islands (KiCad 'remove islands: always')
and refill. The router leaves dozens of isolated GND fill slivers between
tracks; they carry nothing and read as unconnected items.

  ~/.cache/hwde/bin/python3 kicad/gen/gnd_islands.py
"""
from pathlib import Path
import pcbnew
B = Path(__file__).resolve().parents[2]
pcb = str(B / "kicad" / "core.kicad_pcb")
board = pcbnew.LoadBoard(pcb)
n = 0
for z in board.Zones():
    if z.GetNetname() == "GND":
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        n += 1
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.Save(pcb)
print("gnd zones", n)
