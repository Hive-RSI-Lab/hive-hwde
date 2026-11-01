"""Mutant: iso-swd-uncrossable (blinky2).

SIDECAR fault (copper is the golden's, copied unchanged): constraints.json puts
the SWD lines behind a reinforced barrier from the MCU's rails. Nothing on the
board isolates them, so U1 and J2 have pins in both domains with no rating, and
the copper sits far inside the requirement. Must be caught by check_isolation
(kind "iso_part_unrated", net +3V3).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["isolation"] = {
        "domains": {"SWD": {"nets": ["/SWDIO", "/SWCLK"]},
                    "MCU": {"nets": ["+3V3", "GND"]}},
        "barriers": [{"name": "swd", "between": ["SWD", "MCU"],
                      "standard": "iec60664-1", "grade": "reinforced",
                      "working_v_rms": 250, "impulse_v": 2500,
                      "pollution_degree": 2, "material_group": "IIIa",
                      "altitude_m": 2000}]}
    return text, {"sidecars": {"constraints.json": mutlib.dump_json(cons)},
                  "group": "U1"}


if __name__ == "__main__":
    sys.exit(mutlib.run("iso-swd-uncrossable", "blinky2", surgery))
