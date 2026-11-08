"""ESD clamp stubs on lite-main (issue #133): run once after
`hwde board_update --placements kicad/esd-placements.json`.
    KiCad's python: kicad/gen/esd_eco.py kicad/lite-main.kicad_pcb

All six clamps (D3-D8, PESDNC2FD5VB, DFN1006-2L) sit on F.Cu; Lite stays
top-side only. Each line pad gets a stub from its connector-side track, each
ground pad a short track to its own ground via (SLVA680 2.4). USB_DM leaves
J1 on B.Cu, so D8 reaches it through one via. docs/boards/lite-esd.md.
"""
import sys

import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
MM = pcbnew.FromMM

TRACKS = [  # net, width, layer, points
    ("/LINEIN_L", 0.25, "F.Cu", [(46.465, 77.073), (46.465, 80.71)]),
    ("GND", 0.4, "F.Cu", [(47.305, 80.71), (48.2, 80.71)]),
    ("/LINEIN_R", 0.25, "F.Cu", [(38.465, 77.748), (38.465, 80.71)]),
    ("GND", 0.4, "F.Cu", [(39.305, 80.71), (40.2, 80.71)]),
    ("/SUB_L", 0.25, "F.Cu", [(68.665, 77.648), (68.665, 80.71)]),
    ("GND", 0.4, "F.Cu", [(67.825, 80.71), (66.9, 80.71)]),
    ("/SUB_R", 0.25, "F.Cu", [(60.665, 79.6), (60.665, 80.71)]),
    ("GND", 0.4, "F.Cu", [(61.505, 80.71), (62.4, 80.71)]),
    ("/USB_DP", 0.2, "F.Cu", [(75.534, 64.6), (75.5343, 66.3271)]),
    ("GND", 0.4, "F.Cu", [(74.694, 64.6), (73.85, 64.6)]),
    ("/USB_DM", 0.2, "B.Cu", [(74.8288, 67.8), (74.18, 67.8)]),
    ("/USB_DM", 0.2, "F.Cu", [(74.18, 67.8), (74.18, 68.45)]),
    ("GND", 0.4, "F.Cu", [(75.02, 68.45), (75.02, 69.9)]),
]
VIAS = [("GND", 48.2, 80.71), ("GND", 40.2, 80.71), ("GND", 66.9, 80.71),
        ("GND", 62.4, 80.71), ("GND", 73.85, 64.6), ("/USB_DM", 74.18, 67.8)]

# USB_DM's B.Cu run down x 74.83 is split where D8's via stub meets it, so
# the stub joins at segment ends (check_esd follows ends, not T's).
SPLIT = ("/USB_DM", (74.8288, 67.4057), (74.8288, 76.3111), (74.8288, 67.8))
near = lambda p, x, y: abs(p.x - MM(x)) < 2000 and abs(p.y - MM(y)) < 2000
for t in list(b.GetTracks()):
    if t.GetClass() == "PCB_TRACK" and t.GetNetname() == SPLIT[0] \
            and near(t.GetStart(), *SPLIT[1]) and near(t.GetEnd(), *SPLIT[2]):
        n = pcbnew.PCB_TRACK(b)
        n.SetStart(pcbnew.VECTOR2I(MM(SPLIT[3][0]), MM(SPLIT[3][1])))
        n.SetEnd(t.GetEnd())
        n.SetWidth(t.GetWidth())
        n.SetLayer(t.GetLayer())
        n.SetNet(t.GetNet())
        t.SetEnd(pcbnew.VECTOR2I(MM(SPLIT[3][0]), MM(SPLIT[3][1])))
        b.Add(n)
        break
else:
    sys.exit("USB_DM B.Cu segment to split not found")

for net, w, layer, pts in TRACKS:
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(MM(x1), MM(y1)))
        t.SetEnd(pcbnew.VECTOR2I(MM(x2), MM(y2)))
        t.SetWidth(MM(w))
        t.SetLayer(b.GetLayerID(layer))
        t.SetNet(b.FindNet(net))
        b.Add(t)
for net, x, y in VIAS:
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
    v.SetWidth(MM(0.6))
    v.SetDrill(MM(0.3))
    v.SetNet(b.FindNet(net))
    b.Add(v)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
print(len(TRACKS), "tracks,", len(VIAS), "vias")
