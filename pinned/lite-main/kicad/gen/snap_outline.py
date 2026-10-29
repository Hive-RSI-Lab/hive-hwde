"""Snap the board outline to a 1 um grid with exact 90-degree corner arcs.

board_init draws the outline at the shelf-pack origin (e.g. x = 22.864999),
and KiCad's Gerber writer then puts the corner arcs' ends up to 0.05 um off
the straight edges. hwde's dfm check reads that as an open outline and skips
the edge-distance checks (snag in HWDE-SNAGS.md). Moving each outline vertex
by <= 0.05 um closes it exactly.
    KiCad's python: kicad/gen/snap_outline.py kicad/lite-main.kicad_pcb
"""
import math
import sys
import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
nm = lambda v: int(round(v / 1000.0)) * 1000          # to 1 um, in KiCad nm
for s in b.GetDrawings():
    if s.GetLayer() != pcbnew.Edge_Cuts or not isinstance(s, pcbnew.PCB_SHAPE):
        continue
    st, en = s.GetStart(), s.GetEnd()
    a = pcbnew.VECTOR2I(nm(st.x), nm(st.y))
    e = pcbnew.VECTOR2I(nm(en.x), nm(en.y))
    if s.GetShape() == pcbnew.SHAPE_T_ARC:
        c = s.GetCenter()
        c = pcbnew.VECTOR2I(nm(c.x), nm(c.y))
        r = math.hypot(a.x - c.x, a.y - c.y)
        m0 = s.GetArcMid()
        ang = math.atan2(m0.y - c.y, m0.x - c.x)
        mid = pcbnew.VECTOR2I(int(round(c.x + r * math.cos(ang))), int(round(c.y + r * math.sin(ang))))
        s.SetArcGeometry(a, mid, e)
    else:
        s.SetStart(a)
        s.SetEnd(e)
b.Save(sys.argv[1])
