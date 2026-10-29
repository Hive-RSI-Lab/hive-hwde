"""Mutant: ldo-thermal-starved (blinky2).

SIDECAR fault (copper is the golden's, copied unchanged): this mutant's
constraints.json adds a `thermal` entry for U2, the AMS1117-3.3 (5 V -> 3.3 V
at the 0.4 A budget = 0.68 W) heatsunk on its +3V3 tab, allowed 40 C rise. The
golden gives the tab only ~27 mm2 of +3V3 copper near U2 (~112 C rise) and no
thermal vias. Must be caught by check_thermal (kind "thermal_area").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["thermal"] = [{"ref": "U2", "power_w": 0.68, "net": "+3V3",
                        "dt_c": 40}]
    return text, {"sidecars": {"constraints.json": mutlib.dump_json(cons)},
                  "ref": "U2", "power_w": 0.68}


if __name__ == "__main__":
    sys.exit(mutlib.run("ldo-thermal-starved", "blinky2", surgery))
