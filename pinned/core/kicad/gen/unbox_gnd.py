"""core: free a boxed-in GND pad. Rip the other-net tracks (not vias) within
0.7 mm of it, drop a GND via from the pad, then re-route the ripped nets with
miniroute. Prints the ripped nets; the caller checks DRC and reverts if it is
worse.
  ~/.cache/hwde/bin/python3 kicad/gen/unbox_gnd.py REF PAD"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import miniroute as mr
import pcbnew
b = pcbnew.LoadBoard(mr.PCB)
fp = b.FindFootprintByReference(sys.argv[1])
pad = [p for p in fp.Pads() if p.GetNumber() == sys.argv[2]][0]
c = pad.GetPosition()
gone = [t for t in b.GetTracks() if t.GetClass() == "PCB_TRACK" and t.GetNetname() not in ("GND", "")
        and t.GetLayer() == pad.GetLayer() and t.HitTest(c, int(0.9e6))]
ripped = {}
for t in gone:
    ripped.setdefault(t.GetNetname(), []).append(((t.GetStart().x, t.GetStart().y), (t.GetEnd().x, t.GetEnd().y), t.GetWidth(), t.GetLayer()))
    b.Remove(t)
r = mr.Router(b)
ok = r.route_to_via("GND", (c.x / 1e6, c.y / 1e6), layer=0, margin=3.0)
# re-route each ripped net between the loose ends the rip left behind
fixed = []
for net, segs in ripped.items():
    ends = {}
    for s0, s1, w, l in segs:
        for e in (s0, s1):
            ends[e] = ends.get(e, 0) + 1
    loose = [e for e, n in ends.items() if n == 1]
    # an end that sat inside a ripped run is shared (count 2); route the
    # outer pair
    if len(loose) >= 2:
        a, z = loose[0], loose[-1]
        n = r.route(net, (a[0] / 1e6, a[1] / 1e6), (z[0] / 1e6, z[1] / 1e6), (0,), (0,), margin=4.0)
        fixed.append((net, n))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
print(json.dumps({"via": bool(ok), "ripped": list(ripped), "rerouted": fixed}))
