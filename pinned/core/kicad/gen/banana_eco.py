"""core: board-mounted banana sockets (issues #117, #124), stage 1 of the ECO (KiCad python).

The four 5 mm screw terminals J6/J7/J10/J11 become eight Amass 24.243
sockets J20-J27 in one row along the rear edge (banana_placements.json).
A socket reaches 21.4 mm into the board, so the board grows:

- the rear edge moves 13 mm back (y 120.6975 -> 107.6975);
- the board widens 34.4125 mm on the right (x 154.5875 -> 189.0) for y < 150,
  a wing that carries the sub-out and line-in RCAs (J4, J5) and the TOSLINK
  (U19) beside the sockets. Below y 150 the right edge stays where it was,
  so the ESP32's antenna still sits at the board edge.

This stage, on the board before board_update adds the sockets:
1. rips the speaker tracks that ran to the old terminals and the GND stitch
   vias under the socket row, and cuts the speaker fan-in pours (and their
   keepouts) back to y 130 (banana_route.py lays the new copper);
2. moves J4, J5, U19 with D7, D8, FB3, C126 and the copper between them by
   (+DX, -DY) as one block, and rips the rest of the five signal lines that
   ran to them (SUB1/SUB2_OUT, LIN_L/LIN_R, OPT_SPDIF; miniroute.py relays them);
3. moves the mounting holes H1/H2 to the new rear corners;
4. redraws Edge.Cuts and grows the GND planes and In2's +3V3 region to the
   new outline.

    python3.11 kicad/gen/banana_eco.py kicad/core.kicad_pcb
"""
import math
import sys

import pcbnew

MM = 1e6
DX, DY = 34.4125, 13.0
L, T0, R0, BOT = 34.5875, 120.6975, 154.5875, 220.6975
T, R, YN = T0 - DY, R0 + DX, 150.0          # new rear edge, wing edge, wing bottom
RC = 2.0                                       # corner radius
SPK = {"/A_SPK_L+", "/amp_a/A_SPK_L-", "/A_SPK_R+", "/amp_a/A_SPK_R-",
       "/amp_b/B_SPK_L+", "/amp_b/B_SPK_L-", "/amp_b/B_SPK_R+", "/amp_b/B_SPK_R-"}
GROUP = ["J4", "J5", "U19", "D7", "D8", "FB3", "C126"]
SIG = {"/dac/SUB1_OUT", "/dac/SUB2_OUT", "/linein/LIN_L", "/linein/LIN_R", "/OPT_SPDIF"}
CUT = 130.0          # speaker pours and keepouts end here; new copper above it


def V(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


def mm(v):
    return v / MM


def bbox(item):
    r = item.GetBoundingBox()
    return mm(r.GetX()), mm(r.GetY()), mm(r.GetRight()), mm(r.GetBottom())


def main(path):
    b = pcbnew.LoadBoard(path)
    fps = {}
    for fp in b.GetFootprints():
        fps[fp.GetReference()] = fp
    tracks = list(b.GetTracks())
    gone, moved = [], []

    # 1. the old terminal copper and the stitch vias under the socket row
    for t in tracks:
        net = t.GetNetname()
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition()
            if 42 < mm(p.x) < 134 and mm(p.y) < CUT + 0.5 and net == "GND":
                gone.append(t)
        elif net in SPK and min(mm(t.GetStart().y), mm(t.GetEnd().y)) < CUT - 0.4:
            gone.append(t)
    # 2. the rear connector block
    pads = [p for r in GROUP for p in fps[r].Pads()]

    def near_group(x, y, d=2.6):
        return any(math.hypot(x - mm(p.GetPosition().x), y - mm(p.GetPosition().y)) < d
                   for p in pads)
    for t in tracks:
        if t in gone:
            continue
        net = t.GetNetname()
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition()
            inside = near_group(mm(p.x), mm(p.y)) and mm(p.y) < 141
        else:
            a, e = t.GetStart(), t.GetEnd()
            inside = (near_group(mm(a.x), mm(a.y), 4.0) and near_group(mm(e.x), mm(e.y), 4.0)
                      and max(mm(a.y), mm(e.y)) < 139)
        if inside and net in SIG | {"GND", "+3V3", "/linein/OPT_VCC"}:
            moved.append(t)
        elif net in SIG:
            # the run from the block to the DAC/ADC side: keep the IC-end
            # stubs (below y 141.5 and left of the block)
            ys = [mm(t.GetPosition().y)] if t.GetClass() == "PCB_VIA" else \
                [mm(t.GetStart().y), mm(t.GetEnd().y)]
            if min(ys) < 141.5:
                gone.append(t)
    for t in moved:
        t.Move(V(DX, -DY))
    for r in GROUP:
        fps[r].Move(V(DX, -DY))
    for t in gone:
        b.Remove(t)

    # 3. mounting holes at the new rear corners
    fps["H1"].Move(V(0, -DY))
    fps["H2"].Move(V(DX, -DY))


    # the speaker pours, after the copper moves (removing zones first upsets the footprint proxies)
    drop = []
    for z in list(b.Zones()):
        x0, y0, x1, y1 = bbox(z)
        spk = z.GetNetname() in SPK or (z.GetIsRuleArea() and z.GetZoneName() == "" and x1 < 100)
        if not spk or y0 >= CUT:
            continue
        if y1 <= CUT + 0.6:
            drop.append(z)
            continue
        o = z.Outline()       # the fan-in pours are rectangles: lift the top to CUT
        for i in range(o.TotalVertices()):
            v = o.CVertex(i)
            if mm(v.y) < CUT:
                o.SetVertex(i, V(mm(v.x), CUT))

    # 4. planes: GND on F/B/In1 follow the outline (0.5 mm in); In2 +3V3 takes the wing
    m = 0.5
    gnd = [(L + m, T + m), (R - m, T + m), (R - m, YN - m), (R0 - m, YN - m),
           (R0 - m, BOT - m), (L + m, BOT - m)]
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        x0, y0, x1, y1 = bbox(z)
        if z.GetNetname() == "GND" and x1 - x0 > 110:
            pts = gnd
        elif z.GetNetname() == "+3V3" and abs(x0 - 98.087) < 0.05 and abs(y0 - T0) < 0.05:
            pts = [(x0, T), (R, T), (R, YN), (R0, YN), (R0, y1), (x0, y1)]
        else:
            continue
        o = z.Outline()
        o.RemoveAllContours()
        o.NewOutline()
        for x, y in pts:
            o.Append(V(x, y))
    # 5. outline (KiCad 10.0.6's python goes astray once shapes are added: do it last, keep refs): rounded rectangle with the wing, a concave fillet at its foot
    for d in list(b.GetDrawings()):
        if d.GetLayerName() == "Edge.Cuts":
            b.Remove(d)
    lay = b.GetLayerID("Edge.Cuts")
    keep = []

    def line(x0, y0, x1, y1):
        s = pcbnew.PCB_SHAPE(b, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x0, y0))
        s.SetEnd(V(x1, y1))
        s.SetLayer(lay)
        s.SetWidth(int(0.1 * MM))
        b.Add(s)
        keep.append(s)

    def arc(cx, cy, a0):
        """quarter arc of radius RC round (cx, cy), from angle a0 (deg) +90"""
        s = pcbnew.PCB_SHAPE(b, pcbnew.SHAPE_T_ARC)
        pt = lambda a: V(cx + RC * math.cos(math.radians(a)), cy + RC * math.sin(math.radians(a)))  # noqa: E731
        s.SetArcGeometry(pt(a0), pt(a0 + 45), pt(a0 + 90))
        s.SetLayer(lay)
        s.SetWidth(int(0.1 * MM))
        b.Add(s)
        keep.append(s)
    line(L + RC, T, R - RC, T)
    arc(R - RC, T + RC, 270)
    line(R, T + RC, R, YN - RC)
    arc(R - RC, YN - RC, 0)
    line(R - RC, YN, R0 + RC, YN)
    arc(R0 + RC, YN + RC, 180)          # concave: the centre is outside the board
    line(R0, YN + RC, R0, BOT - RC)
    arc(R0 - RC, BOT - RC, 0)
    line(R0 - RC, BOT, L + RC, BOT)
    arc(L + RC, BOT - RC, 90)
    line(L, BOT - RC, L, T + RC)
    arc(L + RC, T + RC, 180)

    for z in drop:          # last: after a zone Remove the board's proxies go bare
        b.Remove(z)
    b.Save(path)
    print(f"ripped {len(gone)}, moved {len(moved)} tracks/vias with {GROUP}")


if __name__ == "__main__":
    main(sys.argv[1])
