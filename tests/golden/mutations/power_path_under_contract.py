"""Mutant: power-path-under-contract (usbbuck4).

SIDECAR fault (hifi-receiver-2 review-2 POWER-1: a 5 A PD input routed in
0.25 mm tracks, passed because its budget said 0.5 A). usbbuck4's micro-USB
input is declared in constraints.json as a 1.8 A charger path, J1 -> U2's
VIN (power_paths), while its copper is the golden's, sized for the 0.5 A
budget: 0.5 mm F.Cu tracks that carry about 1 A at dT 10 C. Every segment
still meets the 0.5 A budget, so check_current stays quiet. Must be caught
by check_power_path (kind "path_capacity", net VBUS, ref J1, at the neck
track by J1).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("usbbuck4", "constraints.json")
    if "power_paths" in cons:
        raise mutlib.SurgeryError("golden constraints already declare paths")
    cons["power_paths"] = [{"net": "VBUS", "from": "J1", "to": "U2",
                            "current_a": 1.8}]
    return text, {"net": "VBUS", "path": "J1 -> U2", "current_a": 1.8,
                  "sidecars": {"constraints.json": mutlib.dump_json(cons)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("power-path-under-contract", "usbbuck4", surgery))
