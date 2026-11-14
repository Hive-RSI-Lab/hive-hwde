"""Mutant: cathode-clamp-over-power (blinky2).

SIDECAR fault (hive-hwde #868). D1 stays where it is (pad 1 on GND, pad 2 on
/LED_A) but is extracted as a 3.3 V zener whose pin 1 is the anode and pin 2
the cathode: the clamp's K is on /LED_A, a net the board states no voltage
for, and its A on GND, so it bounds /LED_A to 0..3.3 V. parts.json gives it a
10 mW Pd. Its 470 R feed from /LED (anywhere on the board's 0..5 V rails) can
force about 10.6 mA through it, 35 mW at its 3.3 V: over its power rating.
Must be caught by check_ratings (kind "rating_over_current", on D1).
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
            f'\t\t\t(uuid "aaaa0010-dead-beef-0868-00000000000{n}")\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
            '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')


ZENER = {
    "mpn": "ZEN3V3-AK", "lcsc": "C990867",
    "pinout": [{"pin": "1", "name": "A", "type": "passive"},
               {"pin": "2", "name": "K", "type": "passive"}],
    "pin_ratings": [{"pins": ["K"], "kind": "voltage", "level": "recommended",
                     "max": 3.3, "ref": "A", "source": "seeded: zener range top"}]}
BOM = {"parts": [{"lcsc": "C990867", "mpn": "ZEN3V3-AK", "attributes": [
    {"name": "Pd - Power Dissipation", "value": "10mW"}]}]}


def surgery(text):
    what = "cathode-clamp-over-power"
    text = mutlib.edit_footprint(text, "D1", '(property "Reference" "D1"',
                                 prop("C990867", 1) + '(property "Reference" "D1"', what)
    return text, {"sidecars": {"parts/C990867.json": mutlib.dump_json(ZENER),
                               "parts/parts.json": mutlib.dump_json(BOM)},
                  "ref": "D1", "net": "/LED_A"}


if __name__ == "__main__":
    sys.exit(mutlib.run("cathode-clamp-over-power", "blinky2", surgery))
