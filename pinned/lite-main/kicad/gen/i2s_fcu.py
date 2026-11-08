"""Pull lite-main's I2S DOUT and LRCK apart on F.Cu next to the ESP32 (check_si
crosstalk; antenna and crosstalk round). They ran 0.6 mm centre to centre for
4.3 mm at y 133.4 and 134.0, with LRCK 0.57 mm from I2C_SCL.
DOUT now drops to B.Cu at (47.3, 135.38) and runs under BCLK's via to its old
vertical at x 51.17, so its via at (51.17, 134.01) goes. LRCK stays at y 134.01
to x 51.21. BCLK's via moves 0.2 mm up its own diagonal to make room for DOUT,
keeping clear of the GND via at (48.72, 135.88). Refill after.
    KiCad's python: kicad/gen/i2s_fcu.py kicad/lite-main.kicad_pcb
"""
import sys

import pcbnew

F, B = pcbnew.F_Cu, pcbnew.B_Cu
MM = pcbnew.FromMM
V = lambda p: pcbnew.VECTOR2I(MM(p[0]), MM(p[1]))
TOL = MM(0.03)
b = pcbnew.LoadBoard(sys.argv[1])


def near(p, q):
    return abs(p.x - MM(q[0])) < TOL and abs(p.y - MM(q[1])) < TOL


def seg(net, layer, a, c):
    """The one track of `net` on `layer` joining a and c."""
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA" or t.GetNetname() != net or t.GetLayer() != layer:
            continue
        s, e = t.GetStart(), t.GetEnd()
        if (near(s, a) and near(e, c)) or (near(s, c) and near(e, a)):
            return t
    raise SystemExit(f"no {net} track {a} {c}")


def via(net, p):
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA" and t.GetNetname() == net and near(t.GetPosition(), p):
            return t
    raise SystemExit(f"no {net} via {p}")


def add(like, layer, pts):
    for a, c in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(a))
        t.SetEnd(V(c))
        t.SetWidth(like.GetWidth())
        t.SetLayer(layer)
        t.SetNet(like.GetNet())
        b.Add(t)


def move_end(t, old, new):
    (t.SetStart if near(t.GetStart(), old) else t.SetEnd)(V(new))


# BCLK: via 0.2 mm up the diagonal, on both layers
VB0, VB1 = (48.95, 134.85), (49.152, 134.646)
via("/I2S_BCLK", VB0).SetPosition(V(VB1))
move_end(seg("/I2S_BCLK", F, (47.7, 136.09), VB0), VB0, VB1)
move_end(seg("/I2S_BCLK", B, (50.6, 133.2), VB0), VB0, VB1)

# DOUT: F.Cu ends at a new via, B.Cu runs from there to the old vertical
f1 = seg("/I2S_DOUT", F, (45.51, 135.38), (46.88, 134.01))
f2 = seg("/I2S_DOUT", F, (46.88, 134.01), (51.17, 134.01))
old = via("/I2S_DOUT", (51.17, 134.01))
end = old.GetPosition()
vert = seg("/I2S_DOUT", B, (51.17, 132.85), (51.17, 134.01))
add(f1, F, [(45.51, 135.38), (47.3, 135.38)])
nv = pcbnew.PCB_VIA(b)
nv.SetPosition(V((47.3, 135.38)))
nv.SetWidth(old.GetWidth(F))
nv.SetDrill(old.GetDrill())
nv.SetNet(old.GetNet())
nv.SetViaType(old.GetViaType())
b.Add(nv)
ex = pcbnew.ToMM(end.x)
add(vert, B, [(47.3, 135.38), (47.43, 135.25), (ex - 0.77, 135.25), (ex, 134.48), (ex, pcbnew.ToMM(end.y))])
b.Remove(f1)
b.Remove(f2)
b.Remove(old)

# LRCK: stay at y 134.01, then up to its via
f1 = seg("/I2S_LRCK", F, (46.25, 134.01), (46.85, 133.41))
f2 = seg("/I2S_LRCK", F, (46.85, 133.41), (51.81, 133.41))
start = [q for q in (f1.GetStart(), f1.GetEnd()) if near(q, (46.25, 134.01))][0]
vl = via("/I2S_LRCK", (51.81, 133.41)).GetPosition()
sx, sy = pcbnew.ToMM(start.x), pcbnew.ToMM(start.y)
vx, vy = pcbnew.ToMM(vl.x), pcbnew.ToMM(vl.y)
add(f1, F, [(sx, sy), (vx - (sy - vy), sy), (vx, vy)])
b.Remove(f1)
b.Remove(f2)
b.Save(sys.argv[1])
print("i2s_fcu done")
