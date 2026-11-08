"""Respace lite-main's I2S run on B.Cu (check_si crosstalk; Lite pre-order round).
    KiCad's python: kicad/gen/i2s_respace.py kicad/lite-main.kicad_pcb

BCLK and DOUT ran 0.58 mm centre to centre for 30 mm, then 0.33 mm in the
diagonal at the ESP32 end (0.13 mm gap). BCLK moves to x 50.6, DOUT to x 52.1 and
LRCK to x 53.6, which puts 1.5 mm and a strip of GND pour between the three,
and 0.9 mm between LRCK and I2C_SCL (0.5 mm before).
The vias at both ends stay where they are, so F.Cu is untouched. Refill after.
"""
import sys

import pcbnew

ROUTES = {
    "/I2S_BCLK": [(52.622, 100.628), (50.6, 102.65), (50.6, 133.2), (48.952, 134.846)],
    "/I2S_DOUT": [(52.755, 99.365), (53.205, 99.814), (53.205, 101.2), (52.1, 102.305),
                  (52.1, 131.928), (51.173, 132.855), (51.173, 134.014)],
    "/I2S_LRCK": [(53.995, 100.092), (53.995, 101.0), (53.6, 101.395), (53.6, 131.624), (51.813, 133.411)],
}
b = pcbnew.LoadBoard(sys.argv[1])
old = [t for t in b.GetTracks() if t.GetClass() != "PCB_VIA" and t.GetLayer() == pcbnew.B_Cu
       and t.GetNetname() in ROUTES]
nets = {t.GetNetname(): t.GetNet() for t in old}
widths = {t.GetNetname(): t.GetWidth() for t in old}
for t in old:
    b.Remove(t)
V = lambda p: pcbnew.VECTOR2I(pcbnew.FromMM(p[0]), pcbnew.FromMM(p[1]))
for net, pts in ROUTES.items():
    for a, c in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(a))
        t.SetEnd(V(c))
        t.SetWidth(widths[net])
        t.SetLayer(pcbnew.B_Cu)
        t.SetNet(nets[net])
        b.Add(t)
b.Save(sys.argv[1])
print("respaced", list(ROUTES), "removed", len(old))
