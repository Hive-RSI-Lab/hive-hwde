"""Mutant: pulled-output-over-rating (blinky2).

SIDECAR + board edits (hive-hwde #1006). U1's extraction types pin 15 a
push-pull output onto /LED (no stated voltage) on its +3V3/GND rail. D1, its
first pad moved to +5V, is extracted as a part that drives /LED_A from +5V,
and R2 joins /LED_A to /LED: a pull-up past U1's rail. A push-pull pin would
hold /LED at 3.3 V, but the pull-up says it is open drain in fact, so /LED
lies in 0..5 V. C7, moved onto /LED and extracted as a 3.6 V input, is over
its rating at that bound.
Must be caught by check_ratings (kind "rating_over_voltage", on C7).
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
            f'\t\t\t(uuid "aaaa0010-dead-beef-1006-00000000001{n}")\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
            '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')


MCU = {
    "mpn": "SEEDED-MCU-PP", "lcsc": "C991008",
    "pinout": [*({"pin": p, "name": "VDD", "type": "power_in"}
                 for p in ("1", "9", "24", "36", "48")),
               *({"pin": p, "name": "VSS", "type": "ground"}
                 for p in ("8", "23", "35", "47")),
               {"pin": "15", "name": "PA5", "type": "output"}]}
DRV5 = {
    "mpn": "SEEDED-DRV5V", "lcsc": "C991009",
    "pinout": [{"pin": "1", "name": "VDD", "type": "power_in"},
               {"pin": "2", "name": "OUT", "type": "output"}]}
SINK = {
    "mpn": "SEEDED-3V6", "lcsc": "C991010",
    "pinout": [{"pin": "1", "name": "VIN", "type": "input"},
               {"pin": "2", "name": "GND", "type": "ground"}],
    "abs_max": [{"param": "Input voltage VIN", "value": 3.6, "unit": "V"}]}


def surgery(text):
    what = "pulled-output-over-rating"
    text = mutlib.edit_footprint(text, "U1", '(property "Reference" "U1"',
                                 prop("C991008", 1) + '(property "Reference" "U1"', what)
    text = mutlib.edit_footprint(text, "D1", '(property "Reference" "D1"',
                                 prop("C991009", 2) + '(property "Reference" "D1"', what)
    text = mutlib.edit_footprint(text, "D1", '(net "GND")', '(net "+5V")', what)
    text = mutlib.edit_footprint(text, "C7", '(property "Reference" "C7"',
                                 prop("C991010", 3) + '(property "Reference" "C7"', what)
    text = mutlib.edit_footprint(text, "C7", '(net "/OSC_IN")', '(net "/LED")', what)
    return text, {"sidecars": {"parts/C991008.json": mutlib.dump_json(MCU),
                               "parts/C991009.json": mutlib.dump_json(DRV5),
                               "parts/C991010.json": mutlib.dump_json(SINK)},
                  "ref": "C7", "net": "/LED"}


if __name__ == "__main__":
    sys.exit(mutlib.run("pulled-output-over-rating", "blinky2", surgery))
