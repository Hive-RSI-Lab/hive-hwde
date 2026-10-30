"""core: rip tracks/vias of the given nets with any end inside a box.
  KiCad's python: kicad/gen/rip_region.py PCB X0 Y0 X1 Y1 NET [NET ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
x0, y0, x1, y1 = (pcbnew.FromMM(float(v)) for v in sys.argv[2:6])
nets = set(sys.argv[6:])
inb = lambda q: x0 <= q.x <= x1 and y0 <= q.y <= y1  # noqa: E731
gone = [t for t in b.GetTracks() if t.GetNetname() in nets and
        any(inb(q) for q in ([t.GetPosition()] if t.GetClass() == "PCB_VIA" else [t.GetStart(), t.GetEnd()]))]
for t in gone:
    b.Remove(t)
b.Save(sys.argv[1])
print("removed", len(gone))
