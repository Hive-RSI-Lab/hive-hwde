"""Mutant: power-ic-over-input (blinky2).

SIDECAR + one board property. U2 gets an `LCSC` property on the board (goldens
carry none; check_ratings finds parts through it) and this mutant's parts/ dir
lists it as an LM66200 with a filled row rating its input at 4 V, no extraction.
The table's VIN1 is pad 3, on +5V here, and the row names no regulator: only the
family table can say that pad is an input (#805).
Must be caught by check_ratings (kind "rating_over_voltage").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C3235556"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0805-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "U", "mpn": "LM66200DRLR", "lcsc": LCSC,
    "value": "LM66200DRLR", "package": "SOT-5X3-8",
    "attributes": [{"name": "Input Voltage (Vin)", "value": "1.6V~4V"}],
    "role": "U2 dual ideal-diode OR"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "U2", '(property "Reference" "U2"',
        PROP + '(property "Reference" "U2"', "power-ic-over-input")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS)},
                  "ref": "U2", "net": "+5V"}


if __name__ == "__main__":
    sys.exit(mutlib.run("power-ic-over-input", "blinky2", surgery))
