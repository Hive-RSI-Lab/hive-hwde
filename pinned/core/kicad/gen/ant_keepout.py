"""core: the ESP32-WROVER-E antenna keep-out (review-2 DIGITAL-19): no copper,
tracks or vias on any layer under and around the module's PCB antenna, which
sits over the board's right edge. Replaces an existing ANT_KEEPOUT.
  KiCad's python: kicad/gen/ant_keepout.py PCB [X0 Y0 X1 Y1]"""
import sys
import pcbnew
pcb = sys.argv[1]
x0, y0, x1, y1 = (float(v) for v in (sys.argv[2:6] if len(sys.argv) > 5 else (148.9, 189.6, 154.64, 213.5)))
b = pcbnew.LoadBoard(pcb)
old = [z for z in b.Zones() if z.GetIsRuleArea() and z.GetZoneName() == "ANT_KEEPOUT"]
for z in old:
    b.Remove(z)
z = pcbnew.ZONE(b)
z.SetIsRuleArea(True)
z.SetZoneName("ANT_KEEPOUT")
ls = pcbnew.LSET()
for name in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"):
    ls.AddLayer(b.GetLayerID(name))
z.SetLayerSet(ls)
z.SetDoNotAllowTracks(True)
z.SetDoNotAllowVias(True)
z.SetDoNotAllowZoneFills(True)
z.SetDoNotAllowPads(False)
z.SetDoNotAllowFootprints(False)
o = z.Outline()
o.NewOutline()
for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
    o.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
b.Add(z)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print("ANT_KEEPOUT", x0, y0, x1, y1)
