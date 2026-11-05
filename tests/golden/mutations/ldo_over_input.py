"""Mutant: ldo-over-input (blinky2).

SIDECAR + one board property. U2 (the LDO) gets an `LCSC` property on the board
(goldens carry none; check_ratings finds parts through it) and this mutant's
parts/ dir lists that LCSC part with a filled row rating its input at 4 V, no
extraction: the row is all that can judge it, and U2 sits on +5V.
Must be caught by check_ratings (kind "rating_over_voltage").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C6186"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0708-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "U", "mpn": "AMS1117-3.3", "lcsc": LCSC,
    "value": "AMS1117-3.3", "package": "SOT-223",
    "attributes": [{"name": "Input Voltage(Max)", "value": "4V"}],
    "role": "U2 LDO regulator"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "U2", '(property "Reference" "U2"',
        PROP + '(property "Reference" "U2"', "ldo-over-input")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS)},
                  "ref": "U2", "net": "+5V"}


if __name__ == "__main__":
    sys.exit(mutlib.run("ldo-over-input", "blinky2", surgery))
