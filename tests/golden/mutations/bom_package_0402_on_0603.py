"""Mutant: bom-package-0402-on-0603 (usbbuck4).

Erratum class #879 (oasis v0.2 Rfbb1: the BOM part is an 0402, C844504, on
an 0603 footprint; JLC's BOM review caught it, hwde did not). R3 (1k5,
R_0603_1608Metric) gets an LCSC field naming C25867, JLC's 0402 1.5 kOhm
part, where its 0603 part is C22843. The copper and the schematic are
untouched, so ERC, DRC and parity stay clean, and the golden carries no
parts.json: only the pinned package table (reference/lcsc_packages.json)
says what C25867 is. Must be caught by check_bom_sync as
bom_package_mismatch at R3, offline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = ('\t\t(property "LCSC" "C25867"\n'
        '\t\t\t(at 0 0 90)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "8790b1e2-5c3a-4f0e-9d61-0402060300a1")\n'
        '\t\t\t(effects\n'
        '\t\t\t\t(font\n'
        '\t\t\t\t\t(size 1.27 1.27)\n'
        '\t\t\t\t)\n'
        '\t\t\t)\n'
        '\t\t)\n')


def surgery(text):
    text = mutlib.edit_footprint(
        text, "R3", '\t\t(property "Value" "1k5"',
        LCSC + '\t\t(property "Value" "1k5"', "R3 LCSC field")
    return text, {"ref": "R3"}


if __name__ == "__main__":
    sys.exit(mutlib.run("bom-package-0402-on-0603", "usbbuck4", surgery))
