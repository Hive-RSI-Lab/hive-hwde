"""Mutant: courtyard-inside-pads (blinky2).

LIBRARY fault (the hit: EasyEDA's 0603s on hwde-led-test, three parts). R1's
courtyard is drawn as the part's body outline, +-0.80 x +-0.40 mm, inside
pads that reach +-1.2 mm, so KiCad's DRC, which tests courtyards only
against each other, lets a neighbour sit on its pads. Copper is untouched.
Must be caught by check_courtyard (kind "courtyard_pads", ref R1, at R1's
origin).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = "\t\t\t(start -1.48 -0.73)\n\t\t\t(end 1.48 0.73)"
NEW = "\t\t\t(start -0.8 -0.4)\n\t\t\t(end 0.8 0.4)"


def surgery(text):
    text = mutlib.edit_footprint(text, "R1", OLD, NEW, "R1 courtyard")
    return text, {"ref": "R1", "courtyard": "+-0.8 x +-0.4 mm"}


if __name__ == "__main__":
    sys.exit(mutlib.run("courtyard-inside-pads", "blinky2", surgery))
