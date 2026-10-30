"""J1 shell pads join the GND pours solidly (no thermal spokes): the HRO
shell slots cut the B.Cu pour into an island the spokes couldn't reach.
    KiCad's python: kicad/gen/j1_shell_solid.py kicad/lite-main.kicad_pcb"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
for p in b.FindFootprintByReference("J1").Pads():
    if p.GetNumber() in ("1", "2", "3", "4"):
        p.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
print("J1 shell pads: solid zone connection")
