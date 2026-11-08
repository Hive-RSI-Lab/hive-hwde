"""Mutant: adj-buck-fb-on-rail (blinky2).

SIDECAR + one board property. U2 gets an `LCSC` property on the board, and this
mutant's parts/ dir holds a datasheet extraction of the adjustable AP63200,
whose FB pin is on +3V3 with no divider: the footprint of the fixed AP63203
with the adjustable part fitted. FB tied straight to the rail regulates it to
Vref, 0.8 V, not 3.3 V. The part has no output pin and no fixed output, so only
its FB divider and Vref say what it makes (hive-hwde #832). Its input is in
rating: only the output is wrong.
Must be caught by check_ratings (kind "rating_regulator_output").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C2071058"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0832-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
EXTRACTION = {
    "mpn": "AP63200WU-7",
    "description": "2 A synchronous buck, adjustable output",
    "pinout": [{"pin": "1", "name": "GND", "type": "ground"},
               {"pin": "2", "name": "FB", "type": "input"},
               {"pin": "3", "name": "VIN", "type": "power_in"}],
    "abs_max": [{"param": "Input voltage VIN", "value": 32, "unit": "V"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "U2", '(property "Reference" "U2"',
        PROP + '(property "Reference" "U2"', "adj-buck-fb-on-rail")
    return text, {"sidecars": {f"parts/{LCSC}.json": mutlib.dump_json(EXTRACTION)},
                  "ref": "U2", "net": "+3V3"}


if __name__ == "__main__":
    sys.exit(mutlib.run("adj-buck-fb-on-rail", "blinky2", surgery))
