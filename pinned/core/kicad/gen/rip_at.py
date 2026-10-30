"""Remove the via or track end of NET at a point (a DRC dangling item);
with --any, also a track that merely passes through the point.
    KiCad's python: kicad/gen/rip_at.py kicad/lite-main.kicad_pcb NET X Y [NET X Y ...]"""
import sys
import pcbnew
ANY = "--any" in sys.argv
argv = [x for x in sys.argv if x != "--any"]
b = pcbnew.LoadBoard(argv[1])
a = argv[2:]
tracks = list(b.GetTracks())
gone = []
for i in range(0, len(a), 3):
    net, p = a[i], pcbnew.VECTOR2I(pcbnew.FromMM(float(a[i + 1])), pcbnew.FromMM(float(a[i + 2])))
    for t in tracks:
        if t.GetNetname() != net or t in gone:
            continue
        hit = (t.GetPosition() - p).EuclideanNorm() < 20000 if t.GetClass() == "PCB_VIA" else \
            (min((t.GetStart() - p).EuclideanNorm(), (t.GetEnd() - p).EuclideanNorm()) < 20000 or
             (ANY and t.HitTest(p, 20000)))
        if hit:
            gone.append(t)
for t in gone:
    b.Remove(t)
n = len(gone)
b.Save(argv[1])
print("removed", n)
