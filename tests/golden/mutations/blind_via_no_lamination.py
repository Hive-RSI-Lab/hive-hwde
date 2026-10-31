"""Mutant: blind-via-no-lamination (rf4).

STACKUP fault: one of rf4's GND stitching vias (143.6, 110.4) is redrawn as
a blind via F.Cu-In1.Cu. KiCad's DRC passes it (the copper is legal), but
rf4 is a standard 4-layer lamination with no HDI build named, so no fab
press order makes the hole. Must be caught by check_vias (kind
"via_no_lamination" at the via).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = ('\t(via\n\t\t(at 143.6 110.4)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
       '\t\t(layers "F.Cu" "B.Cu")')
NEW = ('\t(via blind\n\t\t(at 143.6 110.4)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
       '\t\t(layers "F.Cu" "In1.Cu")')


def surgery(text):
    text = mutlib.replace_once(text, OLD, NEW, "GND via -> blind F.Cu-In1.Cu")
    return text, {"via": [143.6, 110.4], "span": "F.Cu-In1.Cu"}


if __name__ == "__main__":
    sys.exit(mutlib.run("blind-via-no-lamination", "rf4", surgery))
