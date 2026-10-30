"""Remove tracks/vias by UUID (rip-up before a miniroute retry).
    KiCad's python: kicad/gen/rip.py kicad/lite-main.kicad_pcb UUID [UUID ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
want = set(sys.argv[2:])
gone = [t for t in b.GetTracks() if t.m_Uuid.AsString() in want]
for t in gone:
    b.Remove(t)
b.Save(sys.argv[1])
print("removed", len(gone))
