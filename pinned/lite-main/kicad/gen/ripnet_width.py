"""Remove a net's tracks/vias narrower than a width (rip a too-thin reroute).
    KiCad's python: kicad/gen/ripnet_width.py kicad/lite-main.kicad_pcb NET MAX_WIDTH_MM"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
net, wmax = sys.argv[2], pcbnew.FromMM(float(sys.argv[3]))
gone = [t for t in b.GetTracks() if t.GetNetname() == net and
        (t.GetWidth() <= wmax if t.GetClass() != "PCB_VIA" else False)]
for t in gone:
    b.Remove(t)
b.Save(sys.argv[1])
print(net, "removed", len(gone))
