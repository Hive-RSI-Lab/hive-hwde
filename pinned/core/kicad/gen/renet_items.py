"""core: move given tracks/vias (by UUID prefix) onto another net.
  KiCad's python: kicad/gen/renet_items.py PCB NET UUID [UUID ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
ni = b.FindNet(sys.argv[2])
want = sys.argv[3:]
n = 0
for t in b.GetTracks():
    if any(t.m_Uuid.AsString().startswith(w) for w in want):
        t.SetNet(ni)
        n += 1
b.Save(sys.argv[1])
print(n, "items ->", sys.argv[2])
