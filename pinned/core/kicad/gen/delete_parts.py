"""core: delete footprints from the board (their copper is cleaned by
rip_dangling.py with a net filter afterwards).
  KiCad's python: kicad/gen/delete_parts.py PCB REF [REF ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
fps = [b.FindFootprintByReference(r) for r in sys.argv[2:]]
for fp in fps:
    if fp:
        b.Remove(fp)
b.Save(sys.argv[1])
print("deleted", len([f for f in fps if f]))
