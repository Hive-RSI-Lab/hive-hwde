"""Mutant: bare-power-ic-no-attributes (blinky2).

SIDECAR + one board property. U2 gets an `LCSC` property on the board, and this
mutant's parts/ dir lists that LCSC part (C11338) as a TPS60403 charge pump with
empty `attributes` and no extraction. It is no regulator and no word in the row says
it is rated: only its family does (#797, electrometer U103).
Must be caught by check_ratings (kind "rating_no_attributes").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C11338"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0797-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "U", "mpn": "TPS60403DBVR", "lcsc": LCSC,
    "value": "TPS60403DBVR", "package": "SOT-23-5", "attributes": [],
    "role": "U2"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "U2", '(property "Reference" "U2"',
        PROP + '(property "Reference" "U2"', "bare-power-ic-no-attributes")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS)},
                  "ref": "U2", "net": "+3V3"}


if __name__ == "__main__":
    sys.exit(mutlib.run("bare-power-ic-no-attributes", "blinky2", surgery))
