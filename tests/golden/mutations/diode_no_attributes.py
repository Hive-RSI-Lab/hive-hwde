"""Mutant: diode-no-attributes (blinky2).

SIDECAR + one board property. D1 (the LED) gets an `LCSC` property on the board
(goldens carry none; check_ratings finds parts through it) and this mutant's
parts/ dir lists that LCSC part (C12624, KT-0603G) with empty `attributes`,
no extraction, and no entry in reference/extractions: a rated part nothing
rates.
Must be caught by check_ratings (kind "rating_no_attributes").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C12624"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0675-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "D", "mpn": "KT-0603G", "lcsc": LCSC,
    "value": "green", "package": "0603", "attributes": [],
    "role": "D1 status LED"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "D1", '(property "Reference" "D1"',
        PROP + '(property "Reference" "D1"', "diode-no-attributes")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS)},
                  "ref": "D1", "net": "/LED_A"}


if __name__ == "__main__":
    sys.exit(mutlib.run("diode-no-attributes", "blinky2", surgery))
