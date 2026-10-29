"""Mutant: hv-rail-spacing (blinky2).

SIDECAR fault (copper is the golden's, copied unchanged): the design's +5V
input is declared a 100 V net in this mutant's constraints.json `voltages`
(a rail that turned out to be a 100 V bus). The golden's 0.50 mm +5V-to-GND
pour spacing at J1 pad 1 (B.Cu) then falls under IPC-2221 B2/A6 0.60 mm for
100 V. Must be caught by check_creepage (kind "creepage").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["voltages"] = [{"net": "+5V", "voltage": 100}]
    return text, {"sidecars": {"constraints.json": mutlib.dump_json(cons)},
                  "net": "+5V", "voltage": 100}


if __name__ == "__main__":
    sys.exit(mutlib.run("hv-rail-spacing", "blinky2", surgery))
