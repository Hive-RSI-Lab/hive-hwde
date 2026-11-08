"""Silk finishing ops for lite-main, after hwde's silk_place (place_edit list).

- J1/J2/J3: their EasyEDA silk draws the plug-side body past the board edge.
- C58/C59: the electrolytic outlines sit on their own pads (fpfix couldn't
  narrow them clear); L1-L4 and J1's pin-1 circle collide with neighbours.
  U9's lens arc reaches the front edge. Their footprint silk goes; C58/C59 get a "+" at the + pad instead.
- U2, R4, C38, R15: no legal spot for the refdes in the dense power/codec areas.
- The sub-out mute (review-3 R3-AUDIO-2): Q11-Q15 sit in two rows of touching
  courtyards, so their body outlines overlap each other; their outlines go, and
  the refdes of the parts silk_place can't place there (Q13, Q15, R82, R39, C9,
  C33, and R38/C32 it then crowds) stay on the fab layer only.

    python3 kicad/gen/silk.py > kicad/silk-fix-ops.json
"""
import json
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
X0, Y0 = json.loads((WS / "reports" / "board-init.json").read_text())["outline_origin"]
ops = [{"op": "silk_clear", "ref": r, "only_offboard": True} for r in ("J1", "J2", "J3")]
ops += [{"op": "silk_clear", "ref": r, "layer": "F.SilkS"} for r in ("C58", "C59", "L1", "L2", "L3", "L4", "J1", "U9",
                                                                 "J4", "J5", "J6", "J7")]
ops += [{"op": "set_text", "ref": r, "field": "reference", "hide": True}
        for r in ("U2", "R4", "C38", "R15", "R5", "J4", "J5", "J6", "J7")]
ops += [{"op": "silk_clear", "ref": r, "layer": "F.SilkS"} for r in ("Q11", "Q12", "Q13", "Q14", "Q15")]
ops += [{"op": "set_text", "ref": r, "field": "reference", "hide": True}
        for r in ("Q13", "Q15", "R82", "R39", "C9", "C33", "R38", "C32")]
for u, v in ((61.0, 43.8), (97.6, 43.8)):      # C58, C59 "+" (pad 1 up, rot 270)
    ops.append({"op": "add_text", "text": "+", "x": round(X0 + u, 3), "y": round(Y0 + v, 3),
                "layer": "F.SilkS", "size": 1.2, "thickness": 0.2})
# J4-J7, the banana sockets (their outlines touch, so theirs go): ref and pole on each body
for t, u in (("J4 L+", 77.4), ("J5 L-", 88.4), ("J6 R+", 99.4), ("J7 R-", 110.4)):
    ops.append({"op": "add_text", "text": t, "x": round(X0 + u, 3), "y": round(Y0 + 10.0, 3),
                "layer": "F.SilkS", "size": 1.2, "thickness": 0.2})
print(json.dumps({"version": 1, "ops": ops}, indent=1))
