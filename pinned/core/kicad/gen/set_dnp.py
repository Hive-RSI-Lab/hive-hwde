"""core: mark footprints do-not-populate on the board (Ultra-only parts, so
the board matches the schematic's DNP flags).
  KiCad's python: kicad/gen/set_dnp.py PCB REF [REF ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
for ref in sys.argv[2:]:
    fp = b.FindFootprintByReference(ref)
    fp.SetDNP(True)
b.Save(sys.argv[1])
print("dnp:", " ".join(sys.argv[2:]))
