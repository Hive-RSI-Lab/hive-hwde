"""core: rip a net's tracks on one layer (and vias left without a job are
cleaned by rip_dangling.py), e.g. signals laid on the In1 GND plane.
  KiCad's python: kicad/gen/rip_layer.py PCB LAYER NET [NET ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
L = b.GetLayerID(sys.argv[2])
nets = set(sys.argv[3:])
gone = [t for t in b.GetTracks() if t.GetClass() != "PCB_VIA" and t.GetLayer() == L and t.GetNetname() in nets]
for t in gone:
    b.Remove(t)
b.Save(sys.argv[1])
print("removed", len(gone))
