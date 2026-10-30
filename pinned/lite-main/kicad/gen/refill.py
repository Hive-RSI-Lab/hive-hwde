"""Refill every zone (after route_edit ops, which don't).
    KiCad's python: kicad/gen/refill.py kicad/lite-main.kicad_pcb"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
