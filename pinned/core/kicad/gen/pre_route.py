"""core: pre-route copper the hwde scripts don't place (KiCad python).

- Makes the outer GND pours connect pads solid. With thermal spokes, dense
  GND pads came out "starved".
- Adds the vias from power_zones.py: the speaker via arrays, the PVDD finger
  taps and the GND tap under each amp. They go in before Freerouting so it
  routes around them.

  ~/.cache/hwde/bin/python3 kicad/gen/pre_route.py
"""
import json
from pathlib import Path

import pcbnew

B = Path(__file__).resolve().parents[2]
pcb = str(B / "kicad" / "core.kicad_pcb")
board = pcbnew.LoadBoard(pcb)
z = 0
for zone in board.Zones():
    if zone.GetNetname() == "GND" and zone.GetLayerName() in ("F.Cu", "B.Cu"):
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        z += 1
ops = json.loads((B / "anneal" / "power_vias.ops.json").read_text())["ops"]
for op in ops:
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(int(op["at"][0] * 1e6), int(op["at"][1] * 1e6)))
    v.SetWidth(int(op["size"] * 1e6))
    v.SetDrill(int(op["drill"] * 1e6))
    v.SetNet(board.FindNet(op["net"]))
    board.Add(v)
# Rule areas over the fan-in pours: Freerouting must not thread other nets
# through the amp output channels, or the pours split. The pin row itself
# (v > 36.2) stays open so the pins can be reached from below.
import sys
sys.path.insert(0, str(B / "kicad" / "gen"))
import power_zones as pz
X0, Y0 = pz.X0, pz.Y0
ka = 0


def keepout(layer, u1, v1, u2, v2, vias):
    global ka
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(True)
    z.SetDoNotAllowVias(vias)
    z.SetDoNotAllowZoneFills(False)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowFootprints(False)
    z.SetLayer(board.GetLayerID(layer))
    ol = z.Outline()
    ol.NewOutline()
    for (x, y) in ((u1, v1), (u2, v1), (u2, v2), (u1, v2)):
        ol.Append(int((X0 + x) * 1e6), int((Y0 + y) * 1e6))
    board.Add(z)
    ka += 1


for du in (0.0, pz.DU):
    for key, (layer, rects) in pz.ZONE.items():
        patch = key in ("SPK_L+", "SPK_R-") or key.endswith("/B")
        for (u1, v1, u2, v2) in rects:
            v2c = min(v2, 36.2)
            if v2c - v1 > 0.3:
                keepout(layer, u1 + du, v1, u2 + du, v2c, vias=not patch)
    for (u1, v1, u2, v2) in pz.ZONE_PVDD:
        v2c = min(v2, 36.2)
        if v2c - v1 > 0.3:
            keepout("F.Cu", u1 + du, v1, u2 + du, v2c, vias=False)
board.Save(pcb)
print(json.dumps({"gnd_zones_solid": z, "vias": len(ops), "keepouts": ka}))
