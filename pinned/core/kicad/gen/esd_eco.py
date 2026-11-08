"""core: ESD clamps at the USB-C CC pins and the two RCA jacks (issue #133).

board_update placed D6 and D9 (NUP2105L, CC1 and CC2), D7 (PESD5V0S2BT, sub out) and D8
(PESD5V0S2BT, line in) on B.Cu next to their connectors at the spots
esd_spots.py found (the top side there is full; reports/board-update-esd.json). This routes each clamp's
line pads straight to the connector's through-hole pins and drops a ground
via at each clamp's common pad (TI SLVA680 2.4). The existing routes from the
pins to the ICs are untouched. Run under KiCad's python, after board_update:
  python3.11 kicad/gen/esd_eco.py
"""
import math
import os
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("MR_LAYERS", "F.Cu,B.Cu")
os.environ.setdefault("MR_VIA_D", "0.45")
os.environ.setdefault("MR_VIA_H", "0.2")
import miniroute as mr  # noqa: E402

MM = 1e6
W = 0.25
# clamp: (connector, [(clamp pad, connector pin)], GND pad)
# D6's pad 1 lands on the CC1 run on B.Cu just past J1's pin field (A5 is
# boxed in by the B row): nothing to route. D9 takes CC2 at B5.
CLAMPS = {"D6": ("J1", [], "3"),
          "D9": ("J1", [("2", "B5")], "3"),
          "D7": ("J4", [("1", "1"), ("2", "3")], "3"),
          "D8": ("J5", [], "3")}
# D8 is placed by hand (esd_placements.json, not esd_spots.py): the LIN_L run
# leaves pin 1 along y 137.15 and turns down-left at x 125.55, so between the
# jack's pins it splits pin 3 (above it) from anything below. D8 sits above
# the bend: pad 2 runs 1.4 mm into pin 3's oval pad, pad 1 meets the
# LIN_L diagonal at 45 deg (1.3 mm). Straight stubs, no vias.
STUBS = [("/linein/LIN_R", (123.65, 135.35), (122.55, 135.35)),
         ("/linein/LIN_R", (122.55, 135.35), (122.3, 135.6)),   # into the oval pad
         ("/linein/LIN_L", (123.65, 137.25), (124.55, 138.15))]
# the unused channels of the two CC clamps carry no net, like J1's A8/B8
NO_NET = [("D6", "2"), ("D9", "1")]

b = pcbnew.LoadBoard(mr.PCB)
r = mr.Router(b)


def pad(ref, num, toward=None):
    """A pad's net, its centre and, for a through-hole pin, a point just past
    the router's hole keep-out (drill/2 + 0.27 + w/2) toward `toward`: the
    route ends there and one straight segment finishes into the pad."""
    q = [p for p in b.FindFootprintByReference(ref).Pads() if p.GetNumber() == num][0]
    v = q.GetPosition()
    x, y = v.x / MM, v.y / MM
    if toward and q.HasHole():
        dx, dy = toward[0] - x, toward[1] - y
        k = math.hypot(dx, dy)
        out = max(q.GetDrillSize().x, q.GetDrillSize().y) / MM / 2 + 0.27 + W / 2 + 0.05
        return q.GetNetname(), (x, y), (x + dx / k * out, y + dy / k * out)
    return q.GetNetname(), (x, y), (x, y)


def seg(net, a, z, layer):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(mr.V(*a)); t.SetEnd(mr.V(*z)); t.SetWidth(int(W * MM))
    t.SetLayer(layer); t.SetNet(b.FindNet(net)); b.Add(t)


for net, a, z in STUBS:
    seg(net, a, z, pcbnew.B_Cu)
for ref, num in NO_NET:
    [q for q in b.FindFootprintByReference(ref).Pads() if q.GetNumber() == num][0].SetNetCode(0)
for ref in CLAMPS:
    b.FindFootprintByReference(ref).Reference().SetVisible(False)
r.refresh()
out = []
for d, (j, lines, g) in CLAMPS.items():
    for dp, jp in lines:
        net, a, _ = pad(d, dp)
        _, zc, z = (None, jp, jp) if isinstance(jp, tuple) else pad(j, jp, toward=a)
        # B.Cu: the clamp is on the back, the pin is through-hole
        n = r.route(net, a, z, (1,), (1,), margin=3.0, width=W)
        if n is not None and zc != z:
            seg(net, z, zc, pcbnew.B_Cu)
        out.append((d, dp, net, n))
        r.refresh()
    net, a, _ = pad(d, g)
    out.append((d, g, net, r.route_to_via(net, a, layer=1, margin=2.0, width=0.4)))
    r.refresh()


def fix45(nets):
    """Split a segment off the 45-degree grid (a router end snapped onto a pad
    centre) into a straight run and a 45-degree run (check_route_style), as
    amp_in_eco.py does."""
    k = 0
    for t in list(b.GetTracks()):
        if t.GetClass() == "PCB_VIA" or t.GetNetname() not in nets:
            continue
        (x1, y1), (x2, y2) = [(v.x / MM, v.y / MM) for v in (t.GetStart(), t.GetEnd())]
        dx, dy = x2 - x1, y2 - y1
        if min(abs(dx), abs(dy)) < 0.002 or abs(abs(dx) - abs(dy)) < 0.002:
            continue
        if abs(dx) > abs(dy):
            m = (x1 + math.copysign(abs(dx) - abs(dy), dx), y1)
        else:
            m = (x1, y1 + math.copysign(abs(dy) - abs(dx), dy))
        u = pcbnew.PCB_TRACK(b)
        u.SetStart(mr.V(*m)); u.SetEnd(t.GetEnd()); u.SetWidth(t.GetWidth())
        u.SetLayer(t.GetLayer()); u.SetNet(t.GetNet()); b.Add(u)
        t.SetEnd(mr.V(*m))
        k += 1
    return k


print("off-grid split", fix45({o[2] for o in out} | {n for n, _, _ in STUBS}))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
for o in out:
    print(*o)
if any(o[3] is None for o in out):
    sys.exit("some clamp connection did not route")
