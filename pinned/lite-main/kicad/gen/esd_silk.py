"""Silk after the ESD clamps (issue #133): run after esd_eco.py.
    KiCad's python: kicad/gen/esd_silk.py kicad/lite-main.kicad_pcb

D3-D8 (DFN1006) have no room for a legible refdes: theirs go to F.Fab, as
R1's, whose spot D8 now takes. J1, J3 and C47 keep theirs, moved off the
clamps' pads.
"""
import sys

import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
MOVE = {"J1": (81.2, 63.3), "J3": (64.65, 77.0), "C47": (58.9, 80.9)}
FAB = {"D3", "D4", "D5", "D6", "D7", "D8", "R1"}
for fp in b.GetFootprints():
    ref = fp.GetReference()
    t = fp.Reference()
    if ref in FAB:
        t.SetLayer(pcbnew.F_Fab)
    elif ref in MOVE:
        x, y = MOVE[ref]
        t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
b.Save(sys.argv[1])
