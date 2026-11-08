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
# v 57.7 and 78.3. Espressif: no copper under the antenna and 15 mm clear along
# the edge either side, so the area runs from 15 mm above it down to the bottom
# edge (v 90.1, 11.8 mm below it). H4 sits in that strip: gen/h4_npth.py makes
# it a bare hole.
U1, V1, U2, V2 = 0.0, 42.5, 7.2, 90.1
b = pcbnew.LoadBoard(pcb)
# On KiCad 10, b.Remove(zone) leaves later board lists handing back bare SWIG
# objects, so read the NPTH pads first and remove the old areas once, before
# adding any.
npth = []
for fp in b.GetFootprints():
    for pad in fp.Pads():
        if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            c = pad.GetPosition()
            r = int(max(pad.GetDrillSize().x, pad.GetDrillSize().y) / 2 + pcbnew.FromMM(0.4))
            npth.append((fp.GetReference(), c.x, c.y, r))
for z in list(b.Zones()):
    if z.GetIsRuleArea() and z.GetZoneName().startswith(("ANT_KEEPOUT", "NPTH_KEEPOUT")):
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
o = pcbnew.SHAPE_POLY_SET()
o.NewOutline()
for u, v in ((U1, V1), (U2, V1), (U2, V2), (U1, V2)):
    o.Append(pcbnew.FromMM(x0 + u), pcbnew.FromMM(y0 + v))
z.SetOutline(o)
b.Add(z)

# Freerouting does not keep clear of NPTH holes (it routed within 0.16 mm of
# the RCA locating pegs), so each NPTH hole gets a small no-track/no-via area.
for n, (ref, cx, cy, r) in enumerate(npth):
    k = pcbnew.ZONE(b)
    k.SetIsRuleArea(True)
    k.SetZoneName(f"NPTH_KEEPOUT_{ref}_{n}")
    k.SetLayerSet(ls)
    k.SetDoNotAllowTracks(True)
    k.SetDoNotAllowVias(True)
    k.SetDoNotAllowZoneFills(False)
    k.SetDoNotAllowPads(False)
    k.SetDoNotAllowFootprints(False)
    ko = pcbnew.SHAPE_POLY_SET()
    ko.NewOutline()
    for dx, dy in ((-r, -r), (r, -r), (r, r), (-r, r)):
        ko.Append(cx + dx, cy + dy)
    k.SetOutline(ko)
    b.Add(k)
b.Save(pcb)
print(json.dumps({"keepout": [x0 + U1, y0 + V1, x0 + U2, y0 + V2], "npth_keepouts": len(npth)}))
