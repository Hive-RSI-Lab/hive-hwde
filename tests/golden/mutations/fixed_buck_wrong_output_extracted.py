"""Mutant: fixed-buck-wrong-output-extracted (blinky2).

SIDECAR + one board property. U2 gets an `LCSC` property on the board, and this
mutant's parts/ dir holds a datasheet extraction of the fixed 5 V AP63205, whose
FB pin is on +3V3. The part has no output pin: FB senses the rail directly, so
no power_out pin says where its 5 V goes (hive-hwde #774). Its input is in
rating: only the output is wrong.
Must be caught by check_ratings (kind "rating_regulator_output").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C2071056"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0774-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
EXTRACTION = {
    "mpn": "AP63205WU-7",
    "description": "2 A synchronous buck, fixed 5.0V output",
    "pinout": [{"pin": "1", "name": "GND", "type": "ground"},
               {"pin": "2", "name": "FB", "type": "input"},
               {"pin": "3", "name": "VIN", "type": "power_in"}],
    "abs_max": [{"param": "Input voltage VIN", "value": 32, "unit": "V"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "U2", '(property "Reference" "U2"',
        PROP + '(property "Reference" "U2"', "fixed-buck-wrong-output-extracted")
    return text, {"sidecars": {f"parts/{LCSC}.json": mutlib.dump_json(EXTRACTION)},
                  "ref": "U2", "net": "+3V3"}


if __name__ == "__main__":
    sys.exit(mutlib.run("fixed-buck-wrong-output-extracted", "blinky2", surgery))
