"""core: the banana ECO's route ends back on the 45-degree grid (hwde check_route_style) (KiCad python).

miniroute's and the hand stubs' last segments into a pad centre came out at
odd angles. Each one's pad-side end slides along its pad until the segment
is straight or 45 degrees; it still ends inside the pad. OPT_SPDIF's two
pieces into U19 pin 3 become one straight drop.

    python3.11 kicad/gen/banana_45.py kicad/core.kicad_pcb
"""
import sys

import pcbnew

# net, layer, start, old end -> new end (None: delete the segment)
EDITS = [
    ("/dac/SUB2_OUT", "F.Cu", (104.703, 141.551), (104.666, 142.472), (104.703, 142.472)),
    ("/dac/SUB2_OUT", "B.Cu", (141.003, 124.851), (141.0625, 124.85), (141.0625, 124.851)),
    ("/OPT_SPDIF", "F.Cu", (171.8, 120.86), (171.86, 120.9), (171.8, 119.507)),
    ("/OPT_SPDIF", "F.Cu", (171.86, 120.9), (171.859, 119.507), None),
    ("/OPT_SPDIF", "F.Cu", (117.437, 150.152), (118.087, 150.197), (118.087, 150.152)),
    ("/linein/LIN_L", "F.Cu", (115.8625, 156.55), (117.287, 156.539), (117.287, 156.55)),
    ("/linein/LIN_L", "B.Cu", (163.8625, 125.25), (164.0, 125.3), (164.0, 125.25)),
    ("/linein/LIN_R", "B.Cu", (157.71, 121.75), (158.0625, 122.35), (158.06, 122.1)),
]


def near(p, q):
    return abs(pcbnew.ToMM(p.x) - q[0]) < 0.002 and abs(pcbnew.ToMM(p.y) - q[1]) < 0.002


b = pcbnew.LoadBoard(sys.argv[1])
done, gone = 0, []
tracks = [t for t in b.GetTracks() if t.GetClass() == "PCB_TRACK"]
for net, layer, s, e, new in EDITS:
    for t in tracks:
        if t.GetNetname() != net or t.GetLayerName() != layer:
            continue
        if near(t.GetStart(), s) and near(t.GetEnd(), e):
            if new is None:
                gone.append(t)
            else:
                t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(new[0]), pcbnew.FromMM(new[1])))
            done += 1
for t in gone:
    b.Remove(t)
b.Save(sys.argv[1])
print(done, "of", len(EDITS), "segments")
