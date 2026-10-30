"""Set the F.Cu GND pour's pad connection to solid (board-local, after planes_gen).

The 0.5 mm FFC/FPC sockets' GND pins can't take two thermal spokes, so DRC
reports starved_thermal on a thermal-relief top pour; a .kicad_dru
zone_connection rule did not take effect in kicad-cli 10.0.6. Run with
KiCad's python: python3 kicad/gen/zone_solid.py kicad/ultra-front.kicad_pcb
"""
import sys
import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
n = 0
for z in b.Zones():
    if not z.GetIsRuleArea() and z.GetNetname() == "GND" and z.GetLayer() == pcbnew.F_Cu:
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        n += 1
b.Save(sys.argv[1])
print("solid", n)
