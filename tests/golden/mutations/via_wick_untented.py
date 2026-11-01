"""Mutant: via-wick-untented (blinky2).

ASSEMBLY fault: the board's via tenting is switched off on the top side
(setup `(tenting (front no) ...)`, a stock KiCad option). The GND via at
(130.5, 105.05) sits 0.2 mm from R1 pad 2 with no mask margin, so its mask
opening merges into the pad's: no dam, and the joint's solder wicks into
the via. KiCad's DRC passes it. Must be caught by check_wicking (kind
"wick_via_mask_web" at the via).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = "\t\t(tenting\n\t\t\t(front yes)\n\t\t\t(back yes)\n\t\t)"
NEW = "\t\t(tenting\n\t\t\t(front no)\n\t\t\t(back yes)\n\t\t)"


def surgery(text):
    text = mutlib.replace_once(text, OLD, NEW, "board tenting front -> no")
    return text, {"via": [130.5, 105.05], "pad": "R1.2"}


if __name__ == "__main__":
    sys.exit(mutlib.run("via-wick-untented", "blinky2", surgery))
