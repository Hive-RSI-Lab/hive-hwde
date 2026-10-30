"""core: stranded GND pads (isolated pour fragments) get a short track to the
nearest spot that can hold a via, plus the via down to In1."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import miniroute as mr
import pcbnew
b = pcbnew.LoadBoard(mr.PCB)
r = mr.Router(b)
src = open(Path(__file__).with_name("gnd_pad_taps.py")).read()
exec(src.split("b = pcbnew.LoadBoard(pcb)")[1].split("def connected_targets")[0], globals())
done = []
for pad, lay in stranded():
    c = pad.GetPosition()
    k = 0 if b.GetLayerName(lay) == "F.Cu" else 1
    if r.route_to_via("GND", (c.x / 1e6, c.y / 1e6), layer=k, margin=float(__import__("os").environ.get("MR_MARGIN", 3)), width=0.2):
        done.append(f"{pad.GetParentFootprint().GetReference()}.{pad.GetNumber()}")
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
print(json.dumps({"gnd_via_drops": done}))
