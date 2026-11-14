"""Mutant: pin-labels-missing (blinky2).

J2 (1x04 header) is turned into a motor-phase output: pads 1-3 now carry
/PH_A, /PH_B and /PH_C, and no silkscreen text near J2 says which pin is
which phase, so a wrong-order hookup reverses the motor. Must be caught by
check_silk pin_labels_missing (minuet v3's J8, errata issue #886).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    for old, new in (("+3V3", "/PH_A"), ("/SWDIO", "/PH_B"),
                     ("/SWCLK", "/PH_C")):
        text = mutlib.edit_footprint(
            text, "J2", f'(net "{old}")', f'(net "{new}")',
            f"J2 pad net {old}")
    return text, {"ref": "J2", "nets": ["/PH_A", "/PH_B", "/PH_C"]}


if __name__ == "__main__":
    sys.exit(mutlib.run("pin-labels-missing", "blinky2", surgery))
