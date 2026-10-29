"""core: for each unconnected pad of the given nets (from the DRC report),
maze-route the pad to the nearest few vias of its own net.
  ~/.cache/hwde/bin/python3 kicad/gen/via_taps.py NET[,NET...]"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import miniroute as mr
import pcbnew
b = pcbnew.LoadBoard(mr.PCB)
r = mr.Router(b)
nets = set(sys.argv[1].split(","))
rep = json.loads((mr.B / "reports" / "drc_routed.json").read_text())
done, seen = [], set()
for v in rep["failing"]:
    if v["check"] != "unconnected_items" or v.get("net") not in nets:
        continue
    for it in v["items"]:
        if not it["msg"].startswith("Pad") or tuple(it["pos"]) in seen:
            continue
        seen.add(tuple(it["pos"]))
        x, y = it["pos"]
        lay = (0,) if " on F.Cu" in it["msg"] else (0, 1)
        vias = sorted([t.GetPosition() for t in b.GetTracks()
                       if t.GetClass() == "PCB_VIA" and t.GetNetname() == v["net"]],
                      key=lambda p: (p.x / 1e6 - x) ** 2 + (p.y / 1e6 - y) ** 2)[:6]
        for q in vias:
            if r.route(v["net"], (x, y), (q.x / 1e6, q.y / 1e6), lay, (0, 1), margin=float(os.environ.get("MR_MARGIN", 4))):
                done.append((v["net"], it["msg"][:30]))
                break
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
print(json.dumps({"taps": done}))
