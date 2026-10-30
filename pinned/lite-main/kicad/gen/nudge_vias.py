"""Nudge apart vias that Freerouting left under JLC's 0.5 mm hole-to-hole floor.

For each drc_routed hole_to_hole finding between two vias, move one via
straight away from the other by the shortfall + 0.03 mm and bridge its old
spot to the new one with a stub on both layers (so the tracks that ended at
the old spot stay connected). argv[1] picks which via of each pair moves
(0 or 1), so a pair that clashes one way can be tried the other way.
Prints a route_edit op list. (Freerouting knows nothing of the floor: snag
#17 in HWDE-SNAGS.md.)
    python3 kicad/gen/nudge_vias.py 0 > route/nudge.json
"""
import json
import re
import sys
from pathlib import Path

ws = Path(__file__).resolve().parents[2]
which = int(sys.argv[1]) if len(sys.argv) > 1 else 0
gate = json.loads((ws / "reports" / "drc_routed-gate.json").read_text())
ops, moved = [], set()
for v in gate["failing"]:
    if v.get("check") != "hole_to_hole":
        continue
    its = [i for i in v["items"] if i["msg"].startswith("Via")]
    if len(its) != 2:
        continue
    a, b = its[which], its[1 - which]
    if a["uuid"] in moved:
        continue
    got = float(re.search(r"actual ([\d.]+) mm", v["msg"]).group(1))
    net = re.search(r"Via \[([^\]]+)\]", a["msg"]).group(1)
    (ax, ay), (bx, by) = a["pos"], b["pos"]
    dx, dy = ax - bx, ay - by
    n = (dx * dx + dy * dy) ** 0.5
    d = 0.5 - got + 0.03
    new = [round(ax + dx / n * d, 4), round(ay + dy / n * d, 4)]
    ops += [{"op": "remove", "uuid": a["uuid"]},
            {"op": "add_via", "at": new, "size": 0.6, "drill": 0.3, "net": net}]
    for layer in ("F.Cu", "B.Cu"):
        ops.append({"op": "add_track", "start": [ax, ay], "end": new, "width": 0.2, "layer": layer, "net": net})
    moved.add(a["uuid"])
print(json.dumps({"version": 1, "ops": ops}, indent=1))
