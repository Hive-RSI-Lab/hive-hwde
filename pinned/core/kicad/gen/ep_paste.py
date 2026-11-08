"""core: exposed-pad paste off the via holes (hwde check_ep_paste paste_on_via, af884 2026-11-01) (KiCad python).

U10 (ES9018K2M, EP 3.25 mm) had a 3 x 3 grid of 0.3 mm GND vias under one
full paste aperture, U9 (LAN8720A, EP 2.55 mm) one centre via: the solder
runs down the barrels. As on the TPA3220s (U15/U16), the EP copper keeps no
paste of its own and paste-only windows sit between the holes:

- U10 keeps its four corner vias (+-1.2 mm), drops the other five, and gets
  a 1.8 mm centre window and four 1.7 x 0.5 mm edge windows (63 %);
- U9 keeps its centre via and gets four 1.0 mm windows round it (61 %);
- U13 (ES7243E, EP 1.65 mm) keeps its corner via; two windows beside it (55 %).
An EP that already has no paste of its own is left alone, so a re-run adds nothing.
Every window edge is >= 0.25 mm from a hole centre (0.15 mm hole + 0.1 mm).

    python3.11 kicad/gen/ep_paste.py kicad/core.kicad_pcb
"""
import sys

import pcbnew

MM = 1e6
# ref, EP pad number, vias to drop (offsets from the EP centre), windows (cx, cy, w, h) as offsets
EPS = {
    "U10": ("29", [(0, 0), (0, -1.2), (0, 1.2), (-1.2, 0), (1.2, 0)],
            [(0, 0, 1.8, 1.8), (0, -1.35, 1.7, 0.5), (0, 1.35, 1.7, 0.5),
             (-1.35, 0, 0.5, 1.7), (1.35, 0, 0.5, 1.7)]),
    "U9": ("25", [], [(sx * 0.7, sy * 0.7, 1.0, 1.0) for sx in (-1, 1) for sy in (-1, 1)]),
    # ES7243E, EP 1.65 mm with one via in its top-right corner (+0.56, -0.55): two windows clear of it (55 %)
    "U13": ("21", [], [(-0.4125, 0, 0.625, 1.45), (0.4125, 0.2625, 0.625, 0.925)]),
}


def V(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


def main(path):
    b = pcbnew.LoadBoard(path)
    fps = {}
    for fp in b.GetFootprints():
        fps[fp.GetReference()] = fp
    vias = [t for t in b.GetTracks() if t.GetClass() == "PCB_VIA"]
    gone, keep = [], []
    for ref, (num, drop, wins) in EPS.items():
        fp = fps[ref]
        ep = [p for p in fp.Pads() if p.GetNumber() == num][0]
        if not ep.IsOnLayer(pcbnew.F_Paste):
            continue
        c = ep.GetPosition()
        cx, cy = c.x / MM, c.y / MM
        ls = ep.GetLayerSet()
        ls.RemoveLayer(pcbnew.F_Paste)
        ep.SetLayerSet(ls)
        for dx, dy in drop:
            for v in vias:
                p = v.GetPosition()
                if abs(p.x / MM - cx - dx) < 0.05 and abs(p.y / MM - cy - dy) < 0.05:
                    gone.append(v)
        for dx, dy, w, h in wins:
            p = pcbnew.PAD(fp)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetSize(V(w, h))
            ls = pcbnew.LSET()
            ls.AddLayer(pcbnew.F_Paste)
            p.SetLayerSet(ls)
            p.SetNumber("")
            fp.Add(p)
            p.SetPosition(V(cx + dx, cy + dy))
            keep.append(p)
    for v in gone:
        b.Remove(v)
    b.Save(path)
    print(f"{len(keep)} paste windows, {len(gone)} vias dropped")


if __name__ == "__main__":
    main(sys.argv[1])
