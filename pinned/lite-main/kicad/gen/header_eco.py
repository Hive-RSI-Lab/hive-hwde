"""Hand-laid copper for the spare-use header J8 (docs/boards/lite-header.md).

The maze router connects these but leaves staircases that check_route_style
rejects, so the geometry is written out on 45-degree runs. Run once on a board
with J8 placed and unrouted: it adds tracks and vias, refills and saves.
"""
from pathlib import Path

import pcbnew

PCB = str(Path(__file__).resolve().parents[2] / "kicad" / "lite-main.kicad_pcb")
F, B = pcbnew.F_Cu, pcbnew.B_Cu
TRACKS = [
    ("/U0TXD", 0.2, F, [(34.74, 119.32), (34.74, 113.888), (33.675, 112.823)]),
    ("/U0RXD", 0.2, F, [(36.02, 119.32), (36.02, 118.72), (36.72, 118.02),
                        (36.72, 114.858), (38.755, 112.823)]),
    ("/GPIO5", 0.2, F, [(42.37, 119.32), (42.37, 118.72), (43.57, 117.52), (44.37, 117.52),
                        (44.67, 117.22), (44.67, 114.52), (43.835, 113.685), (43.835, 112.823)]),
    ("/GPIO15", 0.2, F, [(49.98, 119.32), (49.98, 118.08), (48.16, 118.08), (47.98, 117.9)]),
    ("/GPIO15", 0.2, B, [(47.98, 117.9), (47.98, 115.17), (48.6, 114.55)]),
    ("/GPIO15", 0.2, F, [(48.6, 114.55), (48.915, 114.235), (48.915, 112.823)]),
    ("/I2C_SCL", 0.2, F, [(51.455, 109.223), (51.455, 109.52), (52.45, 110.515),
                          (52.45, 112.604), (54.4, 114.554)]),
    ("/I2C_SCL", 0.2, B, [(54.4, 114.554), (55.3, 115.454)]),
    ("/I2C_SDA", 0.2, F, [(46.919, 106.371), (46.919, 107.6), (46.375, 108.144), (46.375, 109.223)]),
    ("+3V3", 0.3, F, [(41.295, 109.223), (46.2, 104.318), (46.3, 104.318)]),
    ("+3V3", 0.3, B, [(46.3, 104.318), (47.3, 103.318), (48.3, 103.318)]),
    ("+3V3", 0.3, F, [(48.3, 103.318), (50.038, 103.318)]),
    ("GND", 0.3, F, [(36.215, 109.223), (34.7, 109.223)]),
]
VIAS = [
    ("/GPIO15", 47.98, 117.9), ("/GPIO15", 48.6, 114.55), ("/I2C_SCL", 54.4, 114.554),
    ("+3V3", 46.3, 104.318), ("+3V3", 48.3, 103.318), ("GND", 34.7, 109.223),
]
# Pin names sit outside the rows: the header's plastic body covers the gap between them.
SILK = [
    ("GND", 36.215, 107.4), ("3V3", 41.295, 107.4), ("SDA", 46.375, 107.4), ("SCL", 51.455, 107.4),
    ("TX", 33.675, 114.6), ("RX", 38.755, 114.6), ("IO5", 41.6, 114.6), ("IO15", 50.4, 114.6),
    ("IO5/IO15: HIGH AT RESET", 36.6, 105.9),
]
# These refdes land on J8's pads or labels; F.Fab keeps them, as esd_silk.py does for R1.
FAB = {"R8", "R21", "J8"}


def pt(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


b = pcbnew.LoadBoard(PCB)
nets = b.GetNetsByName()
for net, w, layer, pts in TRACKS:
    for s, e in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pt(*s)); t.SetEnd(pt(*e)); t.SetWidth(pcbnew.FromMM(w))
        t.SetLayer(layer); t.SetNet(nets[net]); b.Add(t)
for net, x, y in VIAS:
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pt(x, y)); v.SetWidth(pcbnew.FromMM(0.6)); v.SetDrill(pcbnew.FromMM(0.3))
    v.SetLayerPair(F, B); v.SetNet(nets[net]); b.Add(v)
for text, x, y in SILK:
    t = pcbnew.PCB_TEXT(b)
    t.SetText(text); t.SetPosition(pt(x, y)); t.SetLayer(pcbnew.F_SilkS)
    t.SetTextSize(pt(0.8, 0.8)); t.SetTextThickness(pcbnew.FromMM(0.15)); b.Add(t)
for fp in b.GetFootprints():
    if fp.GetReference() in FAB:
        fp.Reference().SetLayer(pcbnew.F_Fab)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard(PCB, b)
print("added", sum(len(p) - 1 for *_, p in TRACKS), "tracks", len(VIAS), "vias")
