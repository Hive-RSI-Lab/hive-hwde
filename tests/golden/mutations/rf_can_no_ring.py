"""Mutant: rf-can-no-ring (rf4).

SIDECAR fault: constraints.json declares a shield can over the pi match
(L2, C14, C15), but the layout never got the can's frame: no exposed GND
land runs round the outline to solder it to, so the can sits on solder mask
and shields nothing. Nothing in DRC knows a can is meant to be there. Must
be caught by check_rf (kind "shield_ring_gap").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
import rf4_rf

SHIELD = {"name": "match", "rect": [122.4, 117.2, 129.4, 126.4],
          "refs": ["L2", "C14", "C15"], "f_ghz": 0.915,
          "reason": "can over the pi match"}


def surgery(text):
    return text, {"sidecars": rf4_rf.sidecar(shields=[SHIELD])}


if __name__ == "__main__":
    sys.exit(mutlib.run("rf-can-no-ring", "rf4", surgery))
