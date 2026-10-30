"""core: route each bootstrap cap's OUTx pad to its amp OUTx pin (the pours
reach the pins from above; the caps sit below the package)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import miniroute as mr
import pcbnew
b = pcbnew.LoadBoard(mr.PCB)
r = mr.Router(b)
rep = json.loads((mr.B / "reports" / "drc_routed.json").read_text())
PIN = {"OUT1M": "35", "OUT1P": "39", "OUT2P": "32", "OUT2M": "28"}
out = []
for v in rep["failing"]:
    if v["check"] != "unconnected_items":
        continue
    net = v["net"]
    if not net.startswith("/amp_") or "_OUT" not in net:
        continue
    z = net.split("/")[1][-1].upper()
    amp = b.FindFootprintByReference("U15" if z == "A" else "U16")
    pin = [p for p in amp.Pads() if p.GetNumber() == PIN[net.split("_")[-1]]][0]
    pp = pin.GetPosition()
    cap = None
    for it in v["items"]:
        if it["msg"].startswith("Pad"):
            cap = it["pos"]
    if not cap:
        out.append((net, "no pad item")); continue
    # aim at the lower end of the pin (the package side), not the pour side
    tgt = (pp.x / 1e6, pp.y / 1e6 + 0.6)
    n = r.route(net, tuple(cap), tgt, (0,), (0,), margin=8.0)
    out.append((net, n))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
print(json.dumps(out))
