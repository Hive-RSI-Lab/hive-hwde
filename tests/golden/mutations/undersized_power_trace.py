"""Mutant: undersized-power-trace (blinky2).

Necks the 0.8 mm +3V3 feed segment (118.5,106.95)->(118.5,110.5) down
to 0.16 mm. Still above the 0.127 mm DRC minimum (DRC stays quiet) but
far below IPC-2152 for the rail's 0.4 A budget at dT=10C. Must be
caught by check_current.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = ("\t(segment\n"
       "\t\t(start 118.5 106.95)\n"
       "\t\t(end 118.5 110.5)\n"
       "\t\t(width 0.8)\n")
NEW = ("\t(segment\n"
       "\t\t(start 118.5 106.95)\n"
       "\t\t(end 118.5 110.5)\n"
       "\t\t(width 0.16)\n")


def surgery(text):
    text = mutlib.replace_once(text, OLD, NEW, "undersized-power-trace")
    return text, {"target_net": "+3V3", "segment": [[118.5, 106.95],
                  [118.5, 110.5]], "width_mm": 0.16, "budget_a": 0.4}


if __name__ == "__main__":
    sys.exit(mutlib.run("undersized-power-trace", "blinky2", surgery))
