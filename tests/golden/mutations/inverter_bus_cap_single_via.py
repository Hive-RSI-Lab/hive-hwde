"""Mutant: inverter-bus-cap-single-via (usbbuck4).

SIDECAR fault (#816). An inverter is declared on VBUS with a design bus ripple
of 1.2 A RMS. C13's even share, 0.6 A across two bus caps, is over its one via
at (104.0, 124.4), good for 0.5 A.
The buck U2 alone gives C13 0.19 A (0.4 A at D = 0.66), which that via carries,
so only the inverter's term can catch it: a check that drops the term passes.
Must be caught by check_current (kind "insufficient_transition_vias", net VBUS,
ref C13).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("usbbuck4", "constraints.json")
    if "voltages" in cons or "blocks" in cons:
        raise mutlib.SurgeryError("golden constraints already declare these")
    cons["voltages"] = [{"net": "VBUS", "voltage": 5},
                        {"net": "+3V3", "voltage": 3.3}]
    cons["blocks"] = [{"topology": "half-bridge-inverter", "block": "bridge",
                       "operating_point": {
                           "vin_v": 5, "iout_a": 2.0, "bus_net": "VBUS",
                           "bus_irms_a": 1.2,
                           "bus_irms_source": "planted by this mutant"}}]
    return text, {"net": "VBUS", "cap": "C13", "bus_irms_a": 1.2,
                  "sidecars": {"constraints.json": mutlib.dump_json(cons)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("inverter-bus-cap-single-via", "usbbuck4", surgery))
