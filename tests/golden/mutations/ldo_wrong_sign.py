"""Mutant: ldo-wrong-sign (blinky2).

SIDECAR + one board property. U2 (the LDO) gets an `LCSC` property on the board,
and this mutant's parts/ dir says that part is an ADP7182-3.3, a negative regulator
(#744). A netlist beside the board names U2's pads, so its OUT pad is known to be
on +3V3: the magnitude matches and only the sign is wrong.
Must be caught by check_ratings (kind "rating_regulator_output").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C6190"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0744-000000000002")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "U", "mpn": "ADP7182-3.3", "lcsc": LCSC,
    "value": "ADP7182-3.3", "package": "SOT-223",
    "attributes": [{"name": "Voltage - Supply", "value": "15V"},
                   {"name": "Output Voltage", "value": "3.3V"}],
    "role": "U2 LDO regulator"}]}
NETLIST = """(export (version "E")
  (nets
    (net (code "1") (name "GND") (node (ref "U2") (pin "1") (pinfunction "GND_1")))
    (net (code "2") (name "+3V3") (node (ref "U2") (pin "2") (pinfunction "OUT_2")))
    (net (code "3") (name "+5V") (node (ref "U2") (pin "3") (pinfunction "IN_3")))))
"""


def surgery(text):
    text = mutlib.edit_footprint(
        text, "U2", '(property "Reference" "U2"',
        PROP + '(property "Reference" "U2"', "ldo-wrong-sign")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS),
                               "blinky2.net": NETLIST},
                  "ref": "U2", "net": "+3V3"}


if __name__ == "__main__":
    sys.exit(mutlib.run("ldo-wrong-sign", "blinky2", surgery))
