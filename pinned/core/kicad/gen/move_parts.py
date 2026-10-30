"""Move unrouted parts to positions from a placements JSON (find_spots.py output).

    KiCad's python: kicad/gen/move_parts.py kicad/lite-main.kicad_pcb placements.json
"""
import json
import sys

import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
for ref, p in json.load(open(sys.argv[2])).items():
    fp = b.FindFootprintByReference(ref)
    fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(p["x"]), pcbnew.FromMM(p["y"])))
    fp.SetOrientationDegrees(p.get("deg", 0))
    print(ref, "->", p)
b.Save(sys.argv[1])
