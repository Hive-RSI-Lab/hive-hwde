"""Remove the via or track end of NET at a point (a DRC dangling item).
    KiCad's python: kicad/gen/rip_at.py kicad/lite-main.kicad_pcb NET X Y [NET X Y ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
a = sys.argv[2:]
tracks = list(b.GetTracks())
gone = []
for i in range(0, len(a), 3):
    net, p = a[i], pcbnew.VECTOR2I(pcbnew.FromMM(float(a[i + 1])), pcbnew.FromMM(float(a[i + 2])))
    for t in tracks:
        if t.GetNetname() != net or t in gone:
            continue
        hit = (t.GetPosition() - p).EuclideanNorm() < 20000 if t.GetClass() == "PCB_VIA" else \
            t.HitTest(p, 20000)
        if hit:
            gone.append(t)
for t in gone:
    b.Remove(t)
n = len(gone)
b.Save(sys.argv[1])
print("removed", n)
