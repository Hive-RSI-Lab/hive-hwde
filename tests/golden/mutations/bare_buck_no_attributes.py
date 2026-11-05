"""Mutant: bare-buck-no-attributes (blinky2).

SIDECAR + one board property. U2 gets an `LCSC` property on the board, and this
mutant's parts/ dir lists that LCSC part (C1850354) as a TPS54560B buck with empty
`attributes` and no extraction. No word in the row says regulator and its MPN holds
no output code: only its family does (#763, rf-plasma U201).
Must be caught by check_ratings (kind "rating_no_attributes").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C1850354"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0763-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "U", "mpn": "TPS54560BDDAR", "lcsc": LCSC,
    "value": "TPS54560BDDAR", "package": "HSOP-8", "attributes": [],
    "role": "U2"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "U2", '(property "Reference" "U2"',
        PROP + '(property "Reference" "U2"', "bare-buck-no-attributes")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS)},
                  "ref": "U2", "net": "+3V3"}


if __name__ == "__main__":
    sys.exit(mutlib.run("bare-buck-no-attributes", "blinky2", surgery))
