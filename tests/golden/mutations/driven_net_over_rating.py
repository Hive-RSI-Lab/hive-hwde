"""Mutant: driven-net-over-rating (blinky2).

SIDECAR + board edits (hive-hwde #869). The board states no voltage for /LED,
which U1 drives from pin 15. U1 gets an extraction naming its supply pins
(VDD on +3V3, VSS on GND) and pin 15 a push-pull output, so /LED lies on
U1's 0..3.3 V rail. C7, moved onto /LED and extracted as a 1 V input, is over
its rating at that bound, though no net voltage is stated.
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
            f'\t\t\t(uuid "aaaa0010-dead-beef-0869-00000000000{n}")\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
            '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')


MCU = {
    "mpn": "SEEDED-MCU", "lcsc": "C990869",
    "pinout": [*({"pin": p, "name": "VDD", "type": "power_in"}
                 for p in ("1", "9", "24", "36", "48")),
               *({"pin": p, "name": "VSS", "type": "ground"}
                 for p in ("8", "23", "35", "47")),
               {"pin": "15", "name": "PA5", "type": "output"}]}
SINK = {
    "mpn": "SEEDED-1V", "lcsc": "C990865",
    "pinout": [{"pin": "1", "name": "VIN", "type": "input"},
               {"pin": "2", "name": "GND", "type": "ground"}],
    "abs_max": [{"param": "Input voltage VIN", "value": 1, "unit": "V"}]}


def surgery(text):
    what = "driven-net-over-rating"
    text = mutlib.edit_footprint(text, "U1", '(property "Reference" "U1"',
                                 prop("C990869", 1) + '(property "Reference" "U1"', what)
    text = mutlib.edit_footprint(text, "C7", '(property "Reference" "C7"',
                                 prop("C990865", 2) + '(property "Reference" "C7"', what)
    text = mutlib.edit_footprint(text, "C7", '(net "/OSC_IN")', '(net "/LED")', what)
    return text, {"sidecars": {"parts/C990869.json": mutlib.dump_json(MCU),
                               "parts/C990865.json": mutlib.dump_json(SINK)},
                  "ref": "C7", "net": "/LED"}


if __name__ == "__main__":
    sys.exit(mutlib.run("driven-net-over-rating", "blinky2", surgery))
