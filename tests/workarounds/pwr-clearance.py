"""pwr-clearance: does rules_gen give a low-voltage power class more
clearance than the fab floor?

lite-main (hifi-receiver-2) is a 2-layer board whose CC1/CC2/CFG1 pins sit
between power pins closer than 0.2 mm. rules_gen puts every power class at
max(floor, 0.2 mm) whatever its voltage, so Freerouting couldn't enter those
pins and left them unrouted; the board's kicad/gen/pro_clearance.py sets the
Pwr_* classes back to 0.15 mm after rules_gen. Fixed when a 5 V, 3 A rail's
class gets the board's floor (higher clearance only for a reason, like HV).
"""
import _probe

pro, _ = _probe.rules_gen([{"net": "+5V", "current_a": 3.0, "dt_c": 10}])
floor = pro["board"]["design_settings"]["rules"]["min_clearance"]
wide = [(c["name"], c["clearance"]) for c in pro["net_settings"]["classes"]
        if c["name"] != "Default" and c["clearance"] > floor + 1e-9]
if wide:
    _probe.stands(", ".join(f"{n} at {c} mm" for n, c in wide)
                  + f" for a 5 V rail (floor {floor} mm)")
_probe.fixed(f"power classes take the {floor} mm floor")
