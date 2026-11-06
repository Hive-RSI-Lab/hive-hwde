"""Mutant: supervisor-over-vdd (blinky2).

SIDECAR + two board edits. U2 gets an `LCSC` property on the board (goldens carry
none; check_ratings finds parts through it), and its +5V pad is renumbered 5, so
it sits on the pad the table calls a TPS3823-33's VDD. This mutant's parts/ dir
lists U2 as that part with a filled row topping out at 4 V, no extraction, and
the row names no regulator: only the family table can say pad 5 is an input (#815).
Must be caught by check_ratings (kind "rating_over_voltage").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C7719"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0815-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "U", "mpn": "TPS3823-33DBVR", "lcsc": LCSC,
    "value": "TPS3823-33DBVR", "package": "SOT-23-5",
    "attributes": [{"name": "Voltage - Supply", "value": "1.1V~4V"}],
    "role": "U2 watchdog supervisor"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "U2", '(property "Reference" "U2"',
        PROP + '(property "Reference" "U2"', "supervisor-over-vdd")
    text = mutlib.edit_footprint(
        text, "U2", '(pad "3" ', '(pad "5" ', "supervisor-over-vdd")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS)},
                  "ref": "U2", "net": "+5V"}


if __name__ == "__main__":
    sys.exit(mutlib.run("supervisor-over-vdd", "blinky2", surgery))
