"""Add the ESP32 antenna keep-out (no copper, tracks, vias or pours on either
layer) as a KiCad rule area. hwde's constraints.json keepouts steer placement
only and no hwde script draws a rule area, so this one does (snag logged in
the README). Idempotent: an existing area named ANT_KEEPOUT is replaced.

    KiCad's python: kicad/gen/keepout.py kicad/lite-main.kicad_pcb
"""
import json
import sys
from pathlib import Path

import pcbnew

pcb = sys.argv[1]
ws = Path(pcb).resolve().parents[1]
x0, y0 = json.loads((ws / "reports" / "board-init.json").read_text())["outline_origin"]
# antenna end of the WROVER-E sits on the left edge (u 0.6 .. 7.0) between
# v 57.7 and 78.3; keep a margin around it (Espressif: no copper under the
# antenna, board edge or 15 mm clear around it)
U1, V1, U2, V2 = 0.0, 55.5, 7.2, 80.5
b = pcbnew.LoadBoard(pcb)
for z in list(b.Zones()):
    if z.GetIsRuleArea() and z.GetZoneName() == "ANT_KEEPOUT":
        b.Remove(z)
z = pcbnew.ZONE(b)
z.SetIsRuleArea(True)
z.SetZoneName("ANT_KEEPOUT")
ls = pcbnew.LSET()
ls.AddLayer(pcbnew.F_Cu)
ls.AddLayer(pcbnew.B_Cu)
z.SetLayerSet(ls)
z.SetDoNotAllowTracks(True)
z.SetDoNotAllowVias(True)
z.SetDoNotAllowZoneFills(True)
z.SetDoNotAllowPads(False)
z.SetDoNotAllowFootprints(False)
o = z.Outline()
o.NewOutline()
for u, v in ((U1, V1), (U2, V1), (U2, V2), (U1, V2)):
    o.Append(pcbnew.FromMM(x0 + u), pcbnew.FromMM(y0 + v))
b.Add(z)

# Freerouting does not keep clear of NPTH holes (it routed within 0.16 mm of
# the RCA locating pegs), so each NPTH hole gets a small no-track/no-via area.
for z in list(b.Zones()):
    if z.GetIsRuleArea() and z.GetZoneName().startswith("NPTH_KEEPOUT"):
        b.Remove(z)
n = 0
for fp in b.GetFootprints():
    for pad in fp.Pads():
        if pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
            continue
        c = pad.GetPosition()
        r = int(max(pad.GetDrillSize().x, pad.GetDrillSize().y) / 2 + pcbnew.FromMM(0.4))
        k = pcbnew.ZONE(b)
        k.SetIsRuleArea(True)
        k.SetZoneName(f"NPTH_KEEPOUT_{fp.GetReference()}_{n}")
        k.SetLayerSet(ls)
        k.SetDoNotAllowTracks(True)
        k.SetDoNotAllowVias(True)
        k.SetDoNotAllowZoneFills(False)
        k.SetDoNotAllowPads(False)
        k.SetDoNotAllowFootprints(False)
        ko = k.Outline()
        ko.NewOutline()
        for dx, dy in ((-r, -r), (r, -r), (r, r), (-r, r)):
            ko.Append(c.x + dx, c.y + dy)
        b.Add(k)
        n += 1
b.Save(pcb)
print(json.dumps({"keepout": [x0 + U1, y0 + V1, x0 + U2, y0 + V2], "npth_keepouts": n}))
