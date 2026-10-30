"""core: the outer GND pours drop fill islands that nothing connects (pad-less
fragments boxed in by tracks), then refill.
  KiCad's python: kicad/gen/gnd_islands_remove.py PCB"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
n = 0
for z in b.Zones():
    if z.GetNetname() == "GND" and not z.GetIsRuleArea():
        print(z.GetLayerName() if hasattr(z, "GetLayerName") else "", "island mode was", z.GetIslandRemovalMode())
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        n += 1
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
print("GND zones set to remove islands:", n)
