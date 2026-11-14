"""Mutant: groundless-driver-below-rating (blinky2).

SIDECAR + board edits (hive-hwde #1006). J1 carries -5V on its second pin, so
the board has a negative rail. U1's extraction names its VDD pins on +3V3 and
pin 15 a push-pull output onto /LED (no stated voltage), but no ground pin, so
what U1 swings down to is not known: /LED lies in -5..3.3 V, not 0..3.3 V.
C7, moved onto /LED and extracted as an input rated -0.3 V min against GND,
is below its rating at that bound.
Must be caught by check_ratings (kind "rating_reverse_polarity", on C7).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def prop(lcsc, n):
    return (f'(property "LCSC" "{lcsc}"\n'
            '\t\t\t(at 0 0 0)\n'
            '\t\t\t(layer "F.Fab")\n'
            '\t\t\t(hide yes)\n'
            f'\t\t\t(uuid "aaaa0010-dead-beef-1006-00000000000{n}")\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
            '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')


MCU = {
    "mpn": "SEEDED-MCU-NOGND", "lcsc": "C991006",
    "pinout": [*({"pin": p, "name": "VDD", "type": "power_in"}
                 for p in ("1", "9", "24", "36", "48")),
               {"pin": "15", "name": "PA5", "type": "output"}]}
SINK = {
    "mpn": "SEEDED-NEG03", "lcsc": "C991007",
    "pinout": [{"pin": "1", "name": "VIN", "type": "input"},
               {"pin": "2", "name": "GND", "type": "ground"}],
    "pin_ratings": [{"pins": ["VIN"], "kind": "voltage", "level": "abs_max",
                     "min": -0.3, "max": 6, "ref": "GND",
                     "source": "seeded p.1"}]}


def surgery(text):
    what = "groundless-driver-below-rating"
    text = mutlib.edit_footprint(text, "J1", '(net "GND")', '(net "-5V")', what)
    text = mutlib.edit_footprint(text, "U1", '(property "Reference" "U1"',
                                 prop("C991006", 1) + '(property "Reference" "U1"', what)
    text = mutlib.edit_footprint(text, "C7", '(property "Reference" "C7"',
                                 prop("C991007", 2) + '(property "Reference" "C7"', what)
    text = mutlib.edit_footprint(text, "C7", '(net "/OSC_IN")', '(net "/LED")', what)
    return text, {"sidecars": {"parts/C991006.json": mutlib.dump_json(MCU),
                               "parts/C991007.json": mutlib.dump_json(SINK)},
                  "ref": "C7", "net": "/LED"}


if __name__ == "__main__":
    sys.exit(mutlib.run("groundless-driver-below-rating", "blinky2", surgery))
