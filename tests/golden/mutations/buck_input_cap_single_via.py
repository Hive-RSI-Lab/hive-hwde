"""Mutant: buck-input-cap-single-via (usbbuck4).

SIDECAR fault (#798). U2's input cap C13 hangs off VBUS through the one via
at (104.0, 124.4). The golden's U2 feeds +3V3 at 0.4 A, where C13 ripples
0.19 A and a via carries it. Here the same rail is declared a 2 A output, with
VBUS at 5 V and +3V3 at 3.3 V: D = 0.66, so C13 carries
2 * sqrt(0.66 * 0.34) = 0.95 A RMS, and one via at 0.5 A falls short. VBUS's own
0.5 A budget says nothing about that, so only the ripple figure can catch it.
Must be caught by check_current (kind "insufficient_transition_vias", net VBUS,
ref C13).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("usbbuck4", "constraints.json")
    if "voltages" in cons:
        raise mutlib.SurgeryError("golden constraints already declare voltages")
    for entry in cons["power"]:
        if entry["net"] == "+3V3":
            entry["current_a"] = 2.0
    cons["voltages"] = [{"net": "VBUS", "voltage": 5},
                        {"net": "+3V3", "voltage": 3.3}]
    return text, {"net": "VBUS", "cap": "C13", "iout_a": 2.0,
                  "sidecars": {"constraints.json": mutlib.dump_json(cons)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("buck-input-cap-single-via", "usbbuck4", surgery))
