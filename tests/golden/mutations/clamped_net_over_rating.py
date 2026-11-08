"""Mutant: clamped-net-over-rating (blinky2).

SIDECAR + board edits (hive-hwde #864). The board states no voltage for
/LED_A. D1 is made a 3.3 V zener clamp (K moved GND -> +5V, A on /LED_A, a
datasheet extraction in the parts dir), so /LED_A can only lie between
+5V - 3.3 V and +5V + a diode drop (and the 470 R feed from /LED). C7 is moved
onto /LED_A and extracted as a 1 V input: a part that sees the derived bound
reaching 5.7 V, over its rating, though no net voltage is stated.
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
            f'\t\t\t(uuid "aaaa0010-dead-beef-0864-00000000000{n}")\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
            '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')


ZENER = {
    "mpn": "ZEN3V3", "lcsc": "C990864",
    "pinout": [{"pin": "1", "name": "K", "type": "passive"},
               {"pin": "2", "name": "A", "type": "passive"}],
    "pin_ratings": [{"pins": ["K"], "kind": "voltage", "level": "recommended",
                     "max": 3.3, "ref": "A", "source": "seeded: zener range top"}]}
SINK = {
    "mpn": "SEEDED-1V", "lcsc": "C990865",
    "pinout": [{"pin": "1", "name": "VIN", "type": "input"},
               {"pin": "2", "name": "GND", "type": "ground"}],
    "abs_max": [{"param": "Input voltage VIN", "value": 1, "unit": "V"}]}


def surgery(text):
    what = "clamped-net-over-rating"
    text = mutlib.edit_footprint(text, "D1", '(property "Reference" "D1"',
                                 prop("C990864", 1) + '(property "Reference" "D1"', what)
    text = mutlib.edit_footprint(text, "D1", '(net "GND")', '(net "+5V")', what)
    text = mutlib.edit_footprint(text, "C7", '(property "Reference" "C7"',
                                 prop("C990865", 2) + '(property "Reference" "C7"', what)
    text = mutlib.edit_footprint(text, "C7", '(net "/OSC_IN")', '(net "/LED_A")', what)
    return text, {"sidecars": {"parts/C990864.json": mutlib.dump_json(ZENER),
                               "parts/C990865.json": mutlib.dump_json(SINK)},
                  "ref": "C7", "net": "/LED_A"}


if __name__ == "__main__":
    sys.exit(mutlib.run("clamped-net-over-rating", "blinky2", surgery))
