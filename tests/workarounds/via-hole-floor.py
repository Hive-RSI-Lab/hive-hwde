"""via-hole-floor: can Freerouting, held only to rules_gen's net classes,
put two vias closer than JLC's hole-to-hole floor?

Freerouting spaces vias by copper clearance only: centre to centre at via
diameter + clearance, so drill edges at diameter + clearance - drill. With
rules_gen's classes that is under min_hole_to_hole (0.5 mm), and the hi-fi
boards' routes broke it. core (hifi-receiver-2) sets every class to a
0.7/0.3 mm via after rules_gen (kicad/gen/pro_vias.py); lite-main re-ran the
route for the cleanest result and nudged the last vias with route_edit by
hand. Fixed when every class rules_gen writes holds the floor. (If the fix
lands in route_auto's DSN instead, this probe moves there.)
"""
import _probe

pro, _ = _probe.rules_gen([{"net": "PVDD", "current_a": 5.0, "dt_c": 20}])
floor = pro["board"]["design_settings"]["rules"]["min_hole_to_hole"]
short = []
for c in pro["net_settings"]["classes"]:
    gap = c["via_diameter"] + c["clearance"] - c["via_drill"]
    if gap < floor - 1e-9:
        short.append(f"{c['name']} {c['via_diameter']}/{c['via_drill']} mm at "
                     f"{c['clearance']} mm: {gap:.3f} mm")
if short:
    _probe.stands(f"vias packed by clearance alone break the {floor} mm "
                  f"hole-to-hole floor: {'; '.join(short)}")
_probe.fixed(f"every class's vias hold the {floor} mm hole-to-hole floor")
