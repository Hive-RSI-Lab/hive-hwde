"""Mutant: rf-launch-plane-solid (rf4).

SIDECAR fault: constraints.json declares a reference cutout on In1.Cu under
the SMA's signal pad (J1.1), the launch's compensation, but the GND plane
was poured solid over it (keepout_gen never ran, or a refill ignored it):
the pad's extra capacitance stays and the launch mismatches. Must be caught
by check_rf (kind "rf_cutout_copper", layer In1.Cu, ref J1).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
import rf4_rf

CUTOUT = {"ref": "J1", "pads": ["1"], "margin_mm": 0.3,
          "layers": ["In1.Cu"], "reason": "SMA launch: clear In1 under pin 1"}


def surgery(text):
    return text, {"sidecars": rf4_rf.sidecar(rf_cutouts=[CUTOUT])}


if __name__ == "__main__":
    sys.exit(mutlib.run("rf-launch-plane-solid", "rf4", surgery))
