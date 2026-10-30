"""core: after rules_gen, give every netclass a 0.7/0.3 mm via. Freerouting
spaces vias by copper clearance only, but JLC also wants 0.5 mm
hole-to-hole, which a 0.7 mm via pad at 0.125 mm clearance guarantees."""
import json
p = "kicad/core.kicad_pro"
d = json.load(open(p))
for c in d["net_settings"]["classes"]:
    c["via_diameter"] = max(c.get("via_diameter", 0.6), 0.7)
    c["via_drill"] = 0.3
json.dump(d, open(p, "w"), indent=2)
print("vias 0.7/0.3 on", len(d["net_settings"]["classes"]), "classes")
