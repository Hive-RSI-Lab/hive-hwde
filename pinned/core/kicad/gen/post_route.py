"""core: after route_auto, delete the track segments that the KRT finish pass
laid on power nets below their width rule (KRT routes at its own default
width). The fan-in pours and planes carry those connections.

  ~/.cache/hwde/bin/python3 kicad/gen/post_route.py reports/drc_routed.json
"""
import json
import sys
from pathlib import Path

import pcbnew

B = Path(__file__).resolve().parents[2]
pcb = str(B / "kicad" / "core.kicad_pcb")
board = pcbnew.LoadBoard(pcb)
rep = json.loads(Path(sys.argv[1]).read_text())
narrow = {it.get("uuid") for v in rep["failing"] if v["check"] == "track_width"
          for it in v.get("items", [])}
gone = [t for t in board.GetTracks()
        if t.GetClass() == "PCB_TRACK" and t.m_Uuid.AsString() in narrow]
for t in gone:
    board.Remove(t)
board.Save(pcb)
print(json.dumps({"removed_narrow": len(gone)}))
