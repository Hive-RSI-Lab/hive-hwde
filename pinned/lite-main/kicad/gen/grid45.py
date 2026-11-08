"""Split the wick_fix stubs that run off the 45-degree grid (check_route_style).
    KiCad's python: kicad/gen/grid45.py kicad/lite-main.kicad_pcb
Each becomes a 45-degree leg from its first end plus a straight leg to the other."""
import sys

import pcbnew

STUBS = [((62.8, 91.15), (63.225, 90.4), "/MUTE_S"), ((62.94, 89.02), (62.765, 89.745), "/SUB_L"),
         ((84.5, 94.0), (84.3, 93.2), "+5V"), ((86.3, 100.85), (86.6, 100.3), "+5V"),
         ((85.9, 98.95), (86.3, 100.85), "+5V"), ((50.2, 93.4), (50.4, 93.973), "/TAP_L_C"),
         ((50.475, 92.8), (50.2, 93.4), "/TAP_L_C"), ((105.446, 109.085), (105.196, 107.785), "GND"),
         ((83.72, 69.3), (84.75, 69.55), "VBUS")]
MM = pcbnew.ToMM
b = pcbnew.LoadBoard(sys.argv[1])
V = lambda p: pcbnew.VECTOR2I(pcbnew.FromMM(p[0]), pcbnew.FromMM(p[1]))
found = []
for t in b.GetTracks():
    if t.GetClass() == "PCB_VIA":
        continue
    s = (round(MM(t.GetStart().x), 3), round(MM(t.GetStart().y), 3))
    e = (round(MM(t.GetEnd().x), 3), round(MM(t.GetEnd().y), 3))
    for a, c, net in STUBS:
        if t.GetNetname() == net and {s, e} == {a, c}:
            found.append((t, a, c, t.GetWidth(), t.GetLayer(), t.GetNet()))
assert len(found) == 11, len(found)  # /MUTE_S and /SUB_L have a stub on each layer
for f in found:
    b.Remove(f[0])
for _, a, c, w, layer, net in found:
    dx, dy = c[0] - a[0], c[1] - a[1]
    d = min(abs(dx), abs(dy))
    mid = (a[0] + (d if dx > 0 else -d), a[1] + (d if dy > 0 else -d))
    for p, q in ((a, mid), (mid, c)):
        if p == q:
            continue
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(p))
        t.SetEnd(V(q))
        t.SetWidth(w)
        t.SetLayer(layer)
        t.SetNet(net)
        b.Add(t)
b.Save(sys.argv[1])
print("split", len(found))
