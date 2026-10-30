"""Lower the clearance of rules_gen's Pwr_* net classes from 0.2 to 0.15 mm.

rules_gen gives every power class 0.2 mm, but Freerouting then can't enter a
pin whose neighbours are power pins closer than that (it left CC1/CC2/CFG1
unrouted beside the CH224's VBUS pin). Every net here is under 30 V and JLC's
floor is 0.127 mm. Snag logged for hive-hwde; run after rules_gen.
(Raising Default to 0.2 to keep Freerouting's vias 0.5 mm apart hole to hole
was tried and made the ES8388's 0.45 mm-pitch QFN unroutable.)
    python3 kicad/gen/pro_clearance.py kicad/lite-main.kicad_pro
"""
import json
import sys

p = sys.argv[1]
d = json.load(open(p))
for c in d["net_settings"]["classes"]:
    if c["name"].startswith("Pwr_") and c.get("clearance", 0) > 0.15:
        c["clearance"] = 0.15
json.dump(d, open(p, "w"), indent=2)
print({"classes": [(c["name"], c["clearance"]) for c in d["net_settings"]["classes"]]})
