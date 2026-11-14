"""Mutant: tvs-clamped-net-over-rating (blinky2).

SIDECAR + board edits (hive-hwde #867). Like clamped-net-over-rating, but D1
is a TVS (K moved GND -> +5V, A on /LED_A) whose extraction carries only the
free-text rows a datasheet prints for one - VRWM, a "VBR min to max" range and
VC - and no recommended K-vs-A row. Read as a clamp at the top of its VBR
range, it bounds /LED_A to about 1..5.7 V. C7, moved onto /LED_A and
extracted as a 1 V input, is over its rating at that bound.
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
            f'\t\t\t(uuid "aaaa0010-dead-beef-0867-00000000000{n}")\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
            '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')


TVS = {
    "mpn": "SMF3.3A", "lcsc": "C990866",
    "pinout": [{"pin": "1", "name": "K", "type": "passive"},
               {"pin": "2", "name": "A", "type": "passive"}],
    "abs_max": [
        {"param": "VRWM reverse standoff voltage", "value": 3.3, "unit": "V"},
        {"param": "VBR breakdown voltage at IT = 1 mA, min to max",
         "value": "3.6 to 4.0", "unit": "V"},
        {"param": "VC maximum clamping voltage at IPP", "value": 7.3,
         "unit": "V"}]}
SINK = {
    "mpn": "SEEDED-1V", "lcsc": "C990865",
    "pinout": [{"pin": "1", "name": "VIN", "type": "input"},
               {"pin": "2", "name": "GND", "type": "ground"}],
    "abs_max": [{"param": "Input voltage VIN", "value": 1, "unit": "V"}]}


def surgery(text):
    what = "tvs-clamped-net-over-rating"
    text = mutlib.edit_footprint(text, "D1", '(property "Reference" "D1"',
                                 prop("C990866", 1) + '(property "Reference" "D1"', what)
    text = mutlib.edit_footprint(text, "D1", '(net "GND")', '(net "+5V")', what)
    text = mutlib.edit_footprint(text, "C7", '(property "Reference" "C7"',
                                 prop("C990865", 2) + '(property "Reference" "C7"', what)
    text = mutlib.edit_footprint(text, "C7", '(net "/OSC_IN")', '(net "/LED_A")', what)
    return text, {"sidecars": {"parts/C990866.json": mutlib.dump_json(TVS),
                               "parts/C990865.json": mutlib.dump_json(SINK)},
                  "ref": "C7", "net": "/LED_A"}


if __name__ == "__main__":
    sys.exit(mutlib.run("tvs-clamped-net-over-rating", "blinky2", surgery))
