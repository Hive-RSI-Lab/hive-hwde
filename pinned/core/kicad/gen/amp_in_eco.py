"""core: TPA3220 input caps at the IN pins (review-3 R3-LAYOUT-1).

The DC-blocking caps (C218/C220 for U15, C316/C318 for U16) sat at the far
end of the run, so the high-impedance IN_P nodes ran 13-56 mm, single-ended,
over the PVDD plane and past U16's OUT/BST pads. Each cap moves under its
IN pin; the old IN_P copper becomes the op-amp-driven AMP_IN_L/R feed (it
already runs from the old cap spot to the amp): its last few mm at the pin
are cut, it is re-netted, and the old cap spot is bridged. The top side
under the signal rows is full, so the caps go on B.Cu under their pins. U15 only: its IN_P runs were the 45-56 mm ones; U16's
(C316/C318, 10-13 mm, routed under its own body) are left for a later pass,
the area under U16's signal row has no B.Cu room without a wider re-route.
Run under KiCad's python, after amp_eco.py:
  python3.11 kicad/gen/amp_in_eco.py
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("MR_LAYERS", "F.Cu,B.Cu")
os.environ.setdefault("MR_VIA_D", "0.45")     # the board's small via: room among the fan-outs
os.environ.setdefault("MR_VIA_H", "0.2")
import miniroute as mr  # noqa: E402

MM = 1e6
HERE = Path(__file__).resolve().parent
# cap: (IC-side net, amp, IN pin, feed net or None for an IN_M cap)
CAPS = {"C218": ("/amp_a/A_IN1P", "U15", "8", "/AMP_IN_L"),
        "C220": ("/amp_a/A_IN2P", "U15", "15", "/AMP_IN_R")}
# mm of the old IN_P route cut back from the pin (past the amp body)
CUT = {"C218": 4.5, "C220": 10.0, "C316": 4.5, "C318": 6.0}


def V(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


def mm(v):
    return (v.x / MM, v.y / MM)


def ends(t):
    return [mm(t.GetPosition())] if t.GetClass() == "PCB_VIA" else [mm(t.GetStart()), mm(t.GetEnd())]


b = pcbnew.LoadBoard(mr.PCB)


def pin_xy(u, pin):
    return mm([q for q in b.FindFootprintByReference(u).Pads() if q.GetNumber() == pin][0].GetPosition())


def padn(ref, net):
    return [q for q in b.FindFootprintByReference(ref).Pads() if q.GetNetname() == net][0]


tails = {}
for c, (n, u, pin, feed) in CAPS.items():
    px, py = pin_xy(u, pin)
    items = [t for t in b.GetTracks() if t.GetNetname() == n]
    if feed is None:
        for t in items:
            b.Delete(t)
        continue
    old = {k: mm(padn(c, k).GetPosition()) for k in (n, feed)}
    for t in items:
        if all(math.hypot(x - px, y - py) < CUT[c] for x, y in ends(t)):
            b.Delete(t)
    keep = [t for t in b.GetTracks() if t.GetNetname() == n]
    for t in keep:
        t.SetNet(b.FindNet(feed))
    # the old cap spot, bridged
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(V(*old[n])); t.SetEnd(V(*old[feed])); t.SetWidth(int(0.25 * MM))
    t.SetLayer(pcbnew.F_Cu); t.SetNet(b.FindNet(feed)); b.Add(t)
    # the cut end: the kept track end nearest the pin
    best = min((e for t in keep if t.GetClass() != "PCB_VIA" for e in ends(t)),
               key=lambda e: math.hypot(e[0] - px, e[1] - py))
    lay = [t.GetLayer() for t in keep if t.GetClass() != "PCB_VIA" and best in ends(t)][0]
    tails[c] = (best, lay)
    print(c, "tail", best, b.GetLayerName(lay), f"{math.hypot(best[0] - px, best[1] - py):.1f} mm from pin")
spec = {}
for c, (n, u, pin, feed) in CAPS.items():
    x, y = pin_xy(u, pin)
    spec[c] = {"near": [x, y + 3.6], "deg": 90, "nets": [n] + ([feed] if feed else []), "side": "back"}
b.Save(mr.PCB)
sp = "/tmp/amp_in_spec.json"
json.dump(spec, open(sp, "w"))
subprocess.run([sys.executable, str(HERE / "find_spots.py"), mr.PCB, sp, "/tmp/amp_in_place.json"], check=True)
place = json.load(open("/tmp/amp_in_place.json"))

b = pcbnew.LoadBoard(mr.PCB)
for c, p in place.items():
    n, u, pin, feed = CAPS[c]
    fp = b.FindFootprintByReference(c)
    if not fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    fp.SetPosition(V(p["x"], p["y"]))
    px, py = pin_xy(u, pin)
    best = None
    for deg in (0, 90, 180, 270):   # the IN pad nearest the pin
        fp.SetOrientationDegrees(deg)
        x, y = mm(padn(c, n).GetPosition())
        d = math.hypot(x - px, y - py - 1.5)
        if best is None or d < best[0]:
            best = (d, deg)
    fp.SetOrientationDegrees(best[1])
    fp.Reference().SetVisible(False)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)

b = pcbnew.LoadBoard(mr.PCB)
r = mr.Router(b)


def pad(ref, net):
    return mm([q for q in b.FindFootprintByReference(ref).Pads() if q.GetNetname() == net][0].GetPosition())


out = []
for c, (n, u, pin, feed) in CAPS.items():
    px, py = pin_xy(u, pin)
    n_ = r.route(n, pad(c, n), (px, py + 0.6), (1,), (0,), margin=4.0, width=0.25)
    if n_ is None:     # the fan-outs box the pin's outer end in: take its inner end
        r.refresh()
        n_ = r.route(n, pad(c, n), (px, py - 0.6), (1,), (0,), margin=5.0, width=0.25)
    out.append((c, n, n_))
    r.refresh()
for c, (n, u, pin, feed) in CAPS.items():
    if feed:
        (x, y), lay = tails[c]
        k = 0 if lay == pcbnew.F_Cu else 1
        out.append((c, feed, r.route(feed, (x, y), pad(c, feed), (k,), (1,), margin=8.0, width=0.25)))
        r.refresh()


def fix45(nets):
    """Split a segment off the 45-degree grid (a router end snapped onto a pad
    centre) into a straight run and a 45-degree run (check_route_style)."""
    k = 0
    for t in list(b.GetTracks()):
        if t.GetClass() == "PCB_VIA" or t.GetNetname() not in nets:
            continue
        (x1, y1), (x2, y2) = mm(t.GetStart()), mm(t.GetEnd())
        dx, dy = x2 - x1, y2 - y1
        if min(abs(dx), abs(dy)) < 0.002 or abs(abs(dx) - abs(dy)) < 0.002:
            continue
        if abs(dx) > abs(dy):
            m = (x1 + math.copysign(abs(dx) - abs(dy), dx), y1)
        else:
            m = (x1, y1 + math.copysign(abs(dy) - abs(dx), dy))
        u = pcbnew.PCB_TRACK(b)
        u.SetStart(V(*m)); u.SetEnd(t.GetEnd()); u.SetWidth(t.GetWidth())
        u.SetLayer(t.GetLayer()); u.SetNet(t.GetNet()); b.Add(u)
        t.SetEnd(V(*m))
        k += 1
    return k


print("off-grid split", fix45({n for n, *_ in CAPS.values()} | {f for *_, f in CAPS.values() if f}
                              | {"PVDD", "/AMP_OSCM", "/amp_a/A_AVDD", "/amp_a/A_OUT1M", "/amp_a/A_OUT2P",
                                 "/amp_b/B_OUT1M", "/amp_b/B_OUT2P"}))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
print(out)
