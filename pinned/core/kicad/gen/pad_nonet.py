"""core: clear the net of no-connect pads (the schematic's NC pins have no
net on the board, or KiCad's parity check reports a conflict).
  KiCad's python: kicad/gen/pad_nonet.py PCB REF.PAD [REF.PAD ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
for spec in sys.argv[2:]:
    ref, num = spec.split(".")
    p = b.FindFootprintByReference(ref).FindPadByNumber(num)
    p.SetNetCode(0)
b.Save(sys.argv[1])
print("cleared", len(sys.argv) - 2)
