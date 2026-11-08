"""core: the RCA jack's side ear clashed with its neighbours (person's render, af884 110112318f32).

The ear reaches local x +12.3 at 10.8-16.2 mm over the board, local y up to 9.05;
the old courtyard stopped at +8. New rear row: J4 x 145 (ear clears J27), J5 x 166.3
(21.3 pitch: ear courtyard clears J4's flange), U19 x 180.5 (0.4 mm off J5's), FB3/C126 +5.75, H2 south
to (185.45, 127.4) on the east edge so U19 stays on the rear wall.

Moves the parts, carries their local copper, rips LIN_L, LIN_R and OPT_SPDIF's runs
and re-routes them with miniroute's maze (I2S tracks inflated by GAP on their layer,
as preorder_eco.py). SUB1/SUB2 stretch along their horizontal run at x 131.

    MR_NOFILL=1 MR_LAYERS=F.Cu,B.Cu MR_VIA_D=0.45 MR_VIA_H=0.2 python3.11 kicad/gen/rca_ear_eco.py
Then the courtyards (rca_ear_court.py J4, then J5) and a refill.
"""
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("MR_LAYERS", "F.Cu,B.Cu")
sys.path.insert(0, str(Path(__file__).parent))
import miniroute as mr  # noqa: E402
import pcbnew  # noqa: E402

MM = 1e6
GAP = float(os.environ.get("LIN_GAP", 0.6))
DJ4, DJ5, DU19, DFB = 3.0, 6.3, 6.1, 5.75
MOVES = {"J4": (DJ4, 0), "D7": (DJ4, 0), "J5": (DJ5, 0), "D8": (DJ5, 0), "U19": (DU19, 0), "FB3": (DFB, 0),
         "C126": (DFB, 0), "H2": (0, 127.4 - 111.25)}
LIN_L, LIN_R, SPDIF = "/linein/LIN_L", "/linein/LIN_R", "/OPT_SPDIF"
SUB = ("/dac/SUB1_OUT", "/dac/SUB2_OUT")


def xy(p):
    return p.x / MM, p.y / MM


def shift(t, f):
    """Move a track's ends, or a via, by f(x, y) -> (dx, dy)."""
    if t.GetClass() == "PCB_VIA":
        x, y = xy(t.GetPosition())
        d = f(x, y)
        t.SetPosition(mr.V(x + d[0], y + d[1]))
        return
    for get, put in ((t.GetStart, t.SetStart), (t.GetEnd, t.SetEnd)):
        x, y = xy(get())
        d = f(x, y)
        put(mr.V(x + d[0], y + d[1]))


class Avoid(mr.Router):
    avoid = ("/I2S_",)
    gap = GAP

    def grids(self, net, x0, y0, nx, ny, width):
        blk, vblk = super().grids(net, x0, y0, nx, ny, width)
        # miniroute takes an oval NPTH hole's clearance from its long axis only and DRC holds 0.3 mm to it
        for p in self.pads:
            if p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                continue
            px, py = xy(p.GetPosition())
            r = max(p.GetSize().x, p.GetSize().y) / 2 / MM + 0.35 + width / 2
            for j in range(max(0, int((py - r - y0) / mr.G)), min(ny, int((py + r - y0) / mr.G) + 2)):
                for i in range(max(0, int((px - r - x0) / mr.G)), min(nx, int((px + r - x0) / mr.G) + 2)):
                    if math.hypot(x0 + i * mr.G - px, y0 + j * mr.G - py) < r:
                        for arr in blk:
                            arr[j * nx + i] = 1
        for t in self.tracks:
            if t.GetClass() == "PCB_VIA" or not t.GetNetname().startswith(self.avoid):
                continue
            r = self.gap + width / 2 + t.GetWidth() / 2 / MM
            bb = t.GetBoundingBox()
            k = self.layers.index(t.GetLayer())
            for j in range(max(0, int((bb.GetTop() / MM - r - y0) / mr.G)), min(ny, int((bb.GetBottom() / MM + r - y0) / mr.G) + 2)):
                for i in range(max(0, int((bb.GetLeft() / MM - r - x0) / mr.G)), min(nx, int((bb.GetRight() / MM + r - x0) / mr.G) + 2)):
                    if t.HitTest(mr.V(x0 + i * mr.G, y0 + j * mr.G), int(r * MM)):
                        blk[k][j * nx + i] = 1
        return blk, vblk


def free_end(tracks, xmin, xmax, layer):
    """The one dangling end of a net's kept copper in an x window."""
    cnt = {}
    for t in tracks:
        for p in (t.GetStart(), t.GetEnd()):
            k = (round(p.x / MM, 2), round(p.y / MM, 2))
            cnt[k] = cnt.get(k, 0) + 1
    ends = [k for k, n in cnt.items() if n == 1 and xmin <= k[0] <= xmax]
    if len(ends) != 1:
        sys.exit(f"ends in {xmin}-{xmax} on {layer}: {ends}")
    return ends[0]


def main():
    b = pcbnew.LoadBoard(mr.PCB)
    fps = {f.GetReference(): f for f in b.GetFootprints()}
    # GND taps of D7/D8 go with them: pick before anything moves
    tracks = list(b.GetTracks())
    seg = [t for t in tracks if t.GetClass() != "PCB_VIA"]

    def both(t, x0, x1=999):
        return all(x0 <= p.x / MM <= x1 for p in (t.GetStart(), t.GetEnd()))

    rip, rigid = [], []
    for t in tracks:
        n = t.GetNetname()
        via = t.GetClass() == "PCB_VIA"
        if n == "GND":
            if via and min(math.dist(xy(t.GetPosition()), q) for q in ((141.61, 122.55), (159.96, 123.3))) < 0.02:
                rigid.append((t, DJ4 if t.GetPosition().x / MM < 150 else DJ5))
            elif not via and t.GetStart().y / MM < 124 and t.GetStart().x / MM > 133 and t.GetLength() / MM < 0.6:
                rigid.append((t, DJ4 if t.GetStart().x / MM < 150 else DJ5))
        elif n == "/linein/OPT_VCC":
            rigid.append((t, "vcc"))
        elif n == "+3V3" and t.GetPosition().x / MM > 178 and t.GetPosition().y / MM < 124:
            rigid.append((t, DFB))
        elif n in SUB:
            rigid.append((t, None))
        elif via:
            if n == LIN_R and t.GetPosition().x / MM > 150:
                rigid.append((t, DJ5))
        elif n == LIN_L and t.IsOnLayer(pcbnew.B_Cu):
            if both(t, 158.0):
                rigid.append((t, DJ5))
            elif both(t, 133.5, 158.1):
                rip.append(t)
        elif n == LIN_R:
            if both(t, 155.4):
                rigid.append((t, DJ5))
            elif t.IsOnLayer(pcbnew.F_Cu) and both(t, 134.5, 155.5):
                rip.append(t)
        elif n == SPDIF:
            if both(t, 171.7):
                rigid.append((t, DU19))
            elif both(t, 150.9, 171.9):
                rip.append(t)
    print("rip", len(rip), "rigid", len(rigid))
    for t, d in rigid:
        if d is None:
            shift(t, lambda x, y: (DJ4, 0) if x > 131 else (0, 0))
        elif d == "vcc":  # U19's pin end goes with U19, the rest with FB3/C126; the run at y 119.93 shortens
            shift(t, lambda x, y: (DU19, 0) if x < 178 else (DFB, 0))
        else:
            shift(t, lambda x, y: (d, 0))
    for ref, (dx, dy) in MOVES.items():
        p = fps[ref].GetPosition()
        fps[ref].SetPosition(mr.V(p.x / MM + dx, p.y / MM + dy))
    # stitching vias the moved parts now sit on
    drop = []
    keep_via = {id(t) for t, _ in rigid}
    pads = [p for r in MOVES for p in fps[r].Pads()]
    for t in tracks:
        if t.GetClass() != "PCB_VIA" or t.GetNetname() != "GND" or id(t) in keep_via:
            continue
        x, y = xy(t.GetPosition())
        for p in pads:
            px, py = xy(p.GetPosition())
            s = max(p.GetSize().x, p.GetSize().y) / 2 / MM
            if math.hypot(x - px, y - py) < s + 0.75:
                drop.append(t)
                break
    print("GND vias dropped", [xy(t.GetPosition()) for t in drop])
    # SUB1's stretched run at y 125.95 now crosses this one
    drop += [t for t in tracks if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"
             and math.dist(xy(t.GetPosition()), (139.5, 126.2)) < 0.05]
    # silk: C126's reference off the edge, H2's off FB3
    fps["C126"].Reference().SetTextAngleDegrees(90)
    fps["C126"].Reference().SetPosition(mr.V(187.9, 117.0))
    fps["H2"].Reference().SetPosition(mr.V(185.45, 131.7))
    r = Avoid(b)
    a = (133.56, 126.85)
    d8_1 = xy(fps["D8"].Pads()[0].GetPosition())
    for p in fps["D8"].Pads():
        if p.GetNumber() == "1":
            d8_1 = xy(p.GetPosition())
    print("LIN_L", a, d8_1, r.route(LIN_L, a, d8_1, la=(1,), lb=(1,), margin=8.0, width=0.205))
    # LIN_R's kept F.Cu stair off the via at (133.71, 133.5): the rip took what lies wholly past x 134.5
    near = [t for t in seg if t.GetNetname() == LIN_R and t.IsOnLayer(pcbnew.F_Cu) and both(t, 133, 135.5)
            and min(t.GetStart().x, t.GetEnd().x) / MM < 134.5]
    ar = free_end(near, 134.4, 135.5, "F")
    print("LIN_R", ar, r.route(LIN_R, ar, (155.41 + DJ5, 122.2), la=(0,), lb=(0,), margin=8.0, width=0.205))
    Avoid.avoid = ("/I2S_", LIN_L, LIN_R)
    Avoid.gap = float(os.environ.get("SPDIF_GAP", 0.6))
    u = (171.8 + DU19, 120.86)
    print("SPDIF", (150.9, 131.46), r.route(SPDIF, (150.9, 131.46), u, la=(0,), lb=(0,), margin=8.0, width=0.2))
    # GND stitch where H2 was
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(mr.V(185.45, 111.25))
    v.SetWidth(int(0.6 * MM))
    v.SetDrill(int(0.3 * MM))
    v.SetNet(b.GetNetsByName()["GND"])
    b.Add(v)
    for t in rip + drop:
        b.Remove(t)
    b.Save(mr.PCB)
    print("saved")


main()
