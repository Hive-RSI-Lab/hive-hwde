"""core: no silk on the banana sockets J20-J27, as on Lite (KiCad python).

The EasyEDA footprint's silk outline runs out past the nose onto the board
edge (silk_edge_clearance) and its references crowd the snubber row
(silk_overlap); the sockets' own bodies cover the board there anyway. The
outline goes and the references move to F.Fab.

    python3.11 kicad/gen/banana_silk.py kicad/core.kicad_pcb
"""
import sys

import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
gone = []
for fp in b.GetFootprints():
    if fp.GetReference() not in {f"J{20 + i}" for i in range(8)}:
        continue
    for g in fp.GraphicalItems():
        if g.GetLayer() == pcbnew.F_SilkS and g.GetClass() != "PCB_FIELD":
            gone.append((fp, g))
    fp.Reference().SetLayer(pcbnew.F_Fab)
for fp, g in gone:
    fp.Remove(g)
b.Save(sys.argv[1])
print(len(gone), "silk items removed")
