"""Silk finishing ops for lite-main, after hwde's silk_place (place_edit list).

- J1/J2/J3: their EasyEDA silk draws the plug-side body past the board edge.
- C58/C59: the electrolytic outlines sit on their own pads (fpfix couldn't
  narrow them clear); L1-L4 and J1's pin-1 circle collide with neighbours.
  U9's lens arc reaches the front edge. Their footprint silk goes; C58/C59 get a "+" at the + pad instead.
- U2, R4, C38, R15: no legal spot for the refdes in the dense power/codec areas.

    python3 kicad/gen/silk.py > kicad/silk-fix-ops.json
"""
import json
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
X0, Y0 = json.loads((WS / "reports" / "board-init.json").read_text())["outline_origin"]
ops = [{"op": "silk_clear", "ref": r, "only_offboard": True} for r in ("J1", "J2", "J3")]
ops += [{"op": "silk_clear", "ref": r, "layer": "F.SilkS"} for r in ("C58", "C59", "L1", "L2", "L3", "L4", "J1", "U9")]
ops += [{"op": "set_text", "ref": r, "field": "reference", "hide": True} for r in ("U2", "R4", "C38", "R15")]
for u, v in ((61.0, 43.8), (97.6, 43.8)):      # C58, C59 "+" (pad 1 up, rot 270)
    ops.append({"op": "add_text", "text": "+", "x": round(X0 + u, 3), "y": round(Y0 + v, 3),
                "layer": "F.SilkS", "size": 1.2, "thickness": 0.2})
# J4: which wire goes where, for whoever wires the binding posts
for t, u in (("L+", 75.18), ("L-", 80.26), ("R+", 85.34), ("R-", 90.42)):
    ops.append({"op": "add_text", "text": t, "x": round(X0 + u, 3), "y": round(Y0 + 12.3, 3),
                "layer": "F.SilkS", "size": 1.2, "thickness": 0.2})
print(json.dumps({"version": 1, "ops": ops}, indent=1))
