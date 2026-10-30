"""core: last resort for stranded GND pads. Maze-route (miniroute) from the
pad to the nearest few GND vias that reach the plane."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import miniroute as mr
import pcbnew
b = pcbnew.LoadBoard(mr.PCB)
r = mr.Router(b)
exec(open(Path(__file__).with_name("gnd_pad_taps.py")).read().split("def connected_targets")[0].split("b = pcbnew.LoadBoard(pcb)")[1].replace("b.", "b."), globals())
done = []
for pad, lay in stranded():
    c = pad.GetPosition()
    vias = sorted([t.GetPosition() for t in b.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"],
                  key=lambda p: (p.x - c.x) ** 2 + (p.y - c.y) ** 2)[:6]
    k = 0 if b.GetLayerName(lay) == "F.Cu" else 1
    for v in vias:
        n = r.route("GND", (c.x / 1e6, c.y / 1e6), (v.x / 1e6, v.y / 1e6), (k,), (0, 1), margin=3.0, width=0.25)
        if n:
            done.append(f"{pad.GetParentFootprint().GetReference()}.{pad.GetNumber()}")
            break
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
print(json.dumps({"gnd_maze_taps": done}))
