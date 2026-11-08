"""Fixes for hive-hwde 9ad982e's stricter checks on lite-main (review round 4):
    KiCad's python: kicad/gen/ep_paste.py kicad/lite-main.kicad_pcb

- check_ep_paste: U1 (CH224A, pad 11) and U7 (pad 29) have GND vias in their
  exposed pads, under the paste. The EP pad loses its own paste, and
  window-pane apertures go between the vias (the way U8's footprint already
  does it). U1: 4 x 1.45 x 0.55 mm, two rows clear of the via row (46 %).
  U7: 4 x 0.7 x 1.0 mm, either side of the via column (49 %).
- dfm (JLC annular ring >= 0.18 mm, hwde #458-460): U8's 16 thermal-via pads
  go from 0.61 to 0.70 mm on their 0.30 mm drill (ring 0.20 mm).
The same edits are in lib/aiee.pretty, so a rebuild keeps them.
"""
import sys

import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
MM = pcbnew.FromMM
WINDOWS = {  # ref: (EP pad number, [(x, y, w, h) relative to the footprint])
    "U1": ("11", [(-0.825, -0.675, 1.45, 0.55), (0.825, -0.675, 1.45, 0.55),
                  (-0.825, 0.675, 1.45, 0.55), (0.825, 0.675, 1.45, 0.55)]),
    "U7": ("29", [(-0.75, -0.6, 0.7, 1.0), (0.75, -0.6, 0.7, 1.0),
                  (-0.75, 0.6, 0.7, 1.0), (0.75, 0.6, 0.7, 1.0)]),
}
done = []
for f in b.GetFootprints():
    ref = f.GetReference()
    if ref in WINDOWS:
        num, wins = WINDOWS[ref]
        assert f.GetOrientationDegrees() == 0 and not f.IsFlipped(), ref
        ep = [p for p in f.Pads() if p.GetNumber() == num][0]
        ls = ep.GetLayerSet()
        ls.RemoveLayer(pcbnew.F_Paste)
        ep.SetLayerSet(ls)
        pos = f.GetPosition()
        for x, y, w, h in wins:
            p = pcbnew.PAD(f)
            p.SetNumber("")
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetSize(pcbnew.VECTOR2I(MM(w), MM(h)))
            ps = pcbnew.LSET()
            ps.AddLayer(pcbnew.F_Paste)
            p.SetLayerSet(ps)
            p.SetPosition(pcbnew.VECTOR2I(pos.x + MM(x), pos.y + MM(y)))
            f.Add(p)
        done.append(ref)
    if ref == "U8":
        n = 0
        for p in f.Pads():
            if p.HasHole() and p.GetNumber() == "33":
                p.SetSize(pcbnew.VECTOR2I(MM(0.70), MM(0.70)))
                n += 1
        assert n == 16, n
        done.append("U8")
assert sorted(done) == ["U1", "U7", "U8"], done
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
print("fixed", done)
