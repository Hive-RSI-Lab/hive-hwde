"""Widen lite-main's two VBUS stubs out of J1 (check_power_path; Lite pre-order round).
    KiCad's python: kicad/gen/vbus_neck.py kicad/lite-main.kicad_pcb

Past the pad row the stubs were 0.5 mm (left) and 0.6 mm (right): 2.17 A at dT
10 C, where a 65 W charger gives 3.25 A. From the end of the pads, the left one
goes to 0.7 mm (CC2 at x 79.615 stops it there), the right to 0.7 then 0.9 mm (J1's pads
stop it there), and the right branch's run to Q1's pour goes from 1.0 to 1.7 mm. Refill after.
"""
import sys

import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
net = None
old = []
for t in b.GetTracks():
    if t.GetNetname() == "VBUS":
        net = t.GetNet()
        if t.GetClass() != "PCB_VIA" and abs(pcbnew.ToMM(t.GetWidth()) - 1.0) < 0.001 and t.GetLayer() == pcbnew.F_Cu:
            old.append(t)  # the 1.0 mm run this replaces: left in, it'd overlap and read as parallel copper
assert len(old) == 1, old
b.Remove(old[0])
V = lambda x, y: pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
for (x0, y0), (x1, y1), w in (((78.965, 68.5), (78.965, 69.223), 0.7),
                              ((83.72, 68.52), (83.72, 68.85), 0.7),
                              ((83.72, 68.85), (83.72, 69.3), 0.9),
                              ((83.72, 69.3), (84.75, 69.55), 0.9),
                              ((84.75, 69.55), (86.865, 69.55), 1.7)):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(V(x0, y0))
    t.SetEnd(V(x1, y1))
    t.SetWidth(pcbnew.FromMM(w))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNet(net)
    b.Add(t)
b.Save(sys.argv[1])
