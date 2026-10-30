"""Remove stitch vias that land under the 0.5 mm hole-to-hole floor or on
another net's track (stitch_vias checks neither: it dropped GND vias onto
the pre-routed B.Cu VBUS track).

hwde's stitch_vias keeps copper clearance but not JLC's hole-to-hole minimum,
so some of its GND vias sit 0.23-0.48 mm from a mounting-hole via ring, the
TPA3118's thermal vias or another via (snag logged for hive-hwde). For every
drc_routed hole_to_hole finding, drop the via that stitch_vias placed (its
position is in route/stitch_vias.json). Prints a route_edit op list.

    python3 kicad/gen/fix_holes.py > route/fix-holes.json
"""
import json
from pathlib import Path

ws = Path(__file__).resolve().parents[2]
gate = json.loads((ws / "reports" / "drc_routed-gate.json").read_text())
stitched = [tuple(o["at"]) for o in json.loads((ws / "route" / "stitch_vias.json").read_text())["ops"] if "at" in o]
ops, seen = [], set()
for v in gate["failing"]:
    if v.get("check") not in ("hole_to_hole", "shorting_items", "clearance", "hole_clearance"):
        continue
    for it in v["items"]:
        if not it["msg"].startswith("Via"):
            continue
        p = it["pos"]
        if any(abs(p[0] - s[0]) < 0.01 and abs(p[1] - s[1]) < 0.01 for s in stitched) and it["uuid"] not in seen:
            ops.append({"op": "remove", "uuid": it["uuid"]})
            seen.add(it["uuid"])
            break
print(json.dumps({"version": 1, "ops": ops}, indent=1))
