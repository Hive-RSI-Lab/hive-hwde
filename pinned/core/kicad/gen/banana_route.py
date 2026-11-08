"""core: speaker copper from the fan-in pours to the banana sockets J20-J27, stage 2 of the ECO (KiCad python).

banana_eco.py cut the eight speaker pours back to y 130. Each net now
climbs a column from its pour, runs along its own band between the sockets'
two pad rows (pin 1 at y 125.5, pin 2 at y 109.8), and drops onto its
socket's pin-2 spine. The pours keep their layers: L-/R+ on F.Cu, L+/R- on
B.Cu, four nets per layer. In pour order the nets reach their sockets in
the same left-to-right order on each layer, so bands stack (the further
right a net goes, the lower its band) and nothing crosses: no vias.
A spine runs on down to pin 1 where no later band on its layer crosses it.

Zones are left unfilled (a full-board fill in python takes >20 min here;
the DRC gate refills). Every piece is a solid-connected zone (priority 5, like power_zones.py's
pours), 2.0 mm or wider: 3.7 A rms continuous (constraints.json
power_notes) on 1 oz outer copper.

    python3.11 kicad/gen/banana_route.py kicad/core.kicad_pcb
"""
import sys

import pcbnew

MM = 1e6
P1, P2 = 125.4975, 109.7975          # socket pin rows
COL0 = 131.0                         # columns overlap the cut pours (y 130) by 1 mm: priority 6 over their 5,
                                     # so same-net zones of one priority never overlap (zones_intersect)
W = 1.0                              # half band/spine width
# net, layer, column (x0, x1), band centre y, socket x, spine to pin 1
NETS = [
    ("/amp_a/A_SPK_L-", "F.Cu", (45.3, 47.9), 112.8, 61.0, True),
    ("/A_SPK_R+", "F.Cu", (63.3, 65.9), 115.6, 72.0, True),
    ("/amp_b/B_SPK_L-", "F.Cu", (78.2, 80.8), 118.4, 105.0, False),
    ("/amp_b/B_SPK_R+", "F.Cu", (96.2, 98.8), 121.2, 116.0, True),
    ("/A_SPK_L+", "B.Cu", (42.6, 44.6), 112.8, 50.0, True),
    ("/amp_a/A_SPK_R-", "B.Cu", (54.3, 56.5), 115.6, 83.0, False),
    ("/amp_b/B_SPK_L+", "B.Cu", (75.3, 77.5), 118.4, 94.0, False),
    ("/amp_b/B_SPK_R-", "B.Cu", (87.2, 89.4), 121.2, 127.0, True),
]


def V(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


def outline(col, band, s, full):
    """the staircase as one polygon (same-net zones may not overlap): column up
    from the pour, band across, spine up to pin 2 (and on down to pin 1)"""
    c0, c1 = col
    pts = [(c0, band - W), (s - W, band - W), (s - W, P2), (s + W, P2)]
    if full:
        pts += [(s + W, P1), (s - W, P1), (s - W, band + W)]
    else:
        pts += [(s + W, band + W)]
    return pts + [(c1, band + W), (c1, COL0), (c0, COL0)]


# pin 1 of a socket whose spine stops at its band: a later band on the same
# layer crosses below it, and on the other layer too. A 1 mm In2 link joins
# its pins; the In2 plane fills round it. The current goes in at pin 2.
IN2_LINKS = [(s, net) for net, _, _, _, s, full in NETS if not full]


def main(path):
    b = pcbnew.LoadBoard(path)
    proto = {}
    for z in b.Zones():
        if not z.GetIsRuleArea() and "SPK_" in z.GetNetname():
            proto.setdefault(b.GetLayerName(z.GetFirstLayer()), z)
    nets = b.GetNetsByName()
    keep = []
    for net, layer, col, band, s, full in NETS:
        z = pcbnew.Cast_to_BOARD_ITEM(proto[layer].Clone()).Cast()   # Clone() comes back as EDA_ITEM
        z.SetNet(nets[net])
        o = z.Outline()
        o.RemoveAllContours()
        o.NewOutline()
        for x, y in outline(col, band, s, full):
            o.Append(V(x, y))
        z.SetZoneName("")
        z.ResetUuid()      # a Clone keeps the UUID; duplicates deadlock the zone filler
        z.SetAssignedPriority(6)
        b.Add(z)
        keep.append(z)
    in2 = b.GetLayerID("In2.Cu")
    for s, net in IN2_LINKS:
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(s, P2))
        t.SetEnd(V(s, P1))
        t.SetWidth(int(1.0 * MM))
        t.SetLayer(in2)
        t.SetNet(nets[net])
        b.Add(t)
        keep.append(t)
    b.Save(path)
    print(len(keep), "zones and In2 links")


if __name__ == "__main__":
    main(sys.argv[1])
