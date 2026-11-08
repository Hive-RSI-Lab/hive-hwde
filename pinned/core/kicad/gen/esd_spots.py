"""core: find B.Cu spots for the ESD clamps D6-D9 next to their connectors (#133).

find_spots.py lets a part sit on copper of any of its own nets, so a clamp's
GND pad could land on a jack pin of its line net. This search is per pad:
every clamp pad must clear other-net copper on B.Cu (pads, tracks, vias) by
CLR and every hole by HOLE, the courtyard must not touch another B.Cu
courtyard or any through-hole pad, and a pad may overlap only copper of its
own net. Among the clean spots it keeps the one with the shortest straight
line from each line pad to its connector pin. Writes placements JSON for
board_update. KiCad's python:
  python3.11 kicad/gen/esd_spots.py PCB OUT.json
"""
import json
import math
import sys

import pcbnew

MM = 1e6
CLR, HOLE, CRT = 0.2, 0.3, 0.2
# clamp: footprint, (pad -> net), connector, (pad -> connector pin), search centre, radius
CLAMPS = {
    # J1's pin rows box CC1 (A5) in from the left and CC2 (B5) from the
    # right: D6 taps the CC1 run on B.Cu past the pin field, D9 takes B5
    "D6": ("SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BL-2",
           {"1": "/power_in/CC1", "2": "", "3": "GND"},
           "J1", {"1": (43.9, 184.0)}, (44.0, 182.5), 5.0),
    "D9": ("SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BL-2",
           {"1": "", "2": "/power_in/CC2", "3": "GND"},
           "J1", {"2": "B5"}, (38.0, 179.0), 5.0),
    "D7": ("SOT-23_L2.9-W1.3-P1.90-LS2.4-BR",
           {"1": "/dac/SUB1_OUT", "2": "/dac/SUB2_OUT", "3": "GND"},
           "J4", {"1": "1", "2": "3"}, (107.6, 138.5), 5.0),
    "D8": ("SOT-23_L2.9-W1.3-P1.90-LS2.4-BR",
           {"1": "/linein/LIN_L", "2": "/linein/LIN_R", "3": "GND"},
           "J5", {"1": "1", "2": "3"}, (125.6, 138.5), 5.0),
}


def V(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


pcb, out_path = sys.argv[1:3]
b = pcbnew.LoadBoard(pcb)
lib = str(__import__("pathlib").Path(__file__).resolve().parents[2] / "lib" / "aiee.pretty")
mine = set(CLAMPS)
others = [f for f in b.GetFootprints() if f.GetReference() not in mine]
opads = [p for f in others for p in f.Pads()]
tracks = [t for t in b.GetTracks()]
crts = []
for f in others:
    if f.IsFlipped():
        bb = f.GetCourtyard(pcbnew.B_CrtYd).BBox()
        if bb.GetWidth() > 0:
            crts.append(bb)
placed = {}
for ref, (fpname, nets, conn, pins, (cx, cy), rad) in CLAMPS.items():
    fp = pcbnew.FootprintLoad(lib, fpname)
    b.Add(fp)
    fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    cfp = b.FindFootprintByReference(conn)
    pin_xy = {n: q.GetPosition() for q in cfp.Pads() for n in [q.GetNumber()]}
    near = lambda it: math.hypot(it.GetBoundingBox().Centre().x / MM - cx,
                                 it.GetBoundingBox().Centre().y / MM - cy) < rad + 6
    lpads = [q for q in opads if near(q)]
    ltracks = [t for t in tracks if near(t) or t.GetLength() / MM > 4]
    lcrts = [c for c in crts if math.hypot(c.Centre().x / MM - cx, c.Centre().y / MM - cy) < rad + 6]
    best = None
    steps = int(rad / 0.2)
    for j in range(-steps, steps + 1):
        for i in range(-steps, steps + 1):
            x, y = cx + i * 0.2, cy + j * 0.2
            if math.hypot(x - cx, y - cy) > rad:
                continue
            for deg in (0, 90, 180, 270):
                fp.SetOrientationDegrees(deg)
                fp.SetPosition(V(x, y))
                cy_bb = fp.GetCourtyard(pcbnew.B_CrtYd).BBox()
                cy_bb.Inflate(int(CRT * MM))
                if any(cy_bb.Intersects(c) for c in lcrts):
                    continue
                if any(p.HasHole() and cy_bb.Intersects(p.GetBoundingBox()) for p in lpads):
                    continue
                ok = True
                for p in fp.Pads():
                    net = nets[p.GetNumber()]
                    bb = p.GetBoundingBox()
                    pc = p.GetPosition()
                    r = max(bb.GetWidth(), bb.GetHeight()) / 2
                    for q in lpads:
                        if not q.IsOnLayer(pcbnew.B_Cu) and not q.HasHole():
                            continue
                        qb = q.GetBoundingBox()
                        if abs(qb.Centre().x - pc.x) > r + qb.GetWidth() + MM or \
                           abs(qb.Centre().y - pc.y) > r + qb.GetHeight() + MM:
                            continue
                        big = pcbnew.BOX2I(bb.GetOrigin(), bb.GetSize())
                        big.Inflate(int((HOLE if q.HasHole() else CLR) * MM))
                        if big.Intersects(qb) and (q.HasHole() or q.GetNetname() != net):
                            ok = False
                            break
                    if not ok:
                        break
                    for t in ltracks:
                        is_via = t.GetClass() == "PCB_VIA"
                        if not is_via and t.GetLayer() != pcbnew.B_Cu:
                            continue
                        if t.GetNetname() == net and not is_via:
                            continue
                        c = HOLE if is_via else CLR
                        big = pcbnew.BOX2I(bb.GetOrigin(), bb.GetSize())
                        big.Inflate(int(c * MM))
                        if not big.Intersects(t.GetBoundingBox()):
                            continue
                        # pad rectangle vs the track's own shape
                        if t.HitTest(big, False):
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    continue
                d = 0.0
                for pn, cn in pins.items():
                    pp = [p for p in fp.Pads() if p.GetNumber() == pn][0].GetPosition()
                    tx, ty = cn if isinstance(cn, tuple) else (pin_xy[cn].x / MM, pin_xy[cn].y / MM)
                    d += math.hypot(pp.x / MM - tx, pp.y / MM - ty)
                if best is None or d < best[0]:
                    best = (d, round(x, 2), round(y, 2), deg)
    b.Remove(fp)
    print(ref, best)
    if best:
        placed[ref] = {"x": best[1], "y": best[2], "deg": best[3], "side": "back"}
        crts.append(pcbnew.BOX2I(V(best[1] - 1.8, best[2] - 1.6), V(3.6, 3.2)))
json.dump(placed, open(out_path, "w"), indent=1)
