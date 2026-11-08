"""Mutant: tvs-forward-on-rail (blinky2).

SIDECAR + board surgery. D1 gets an `LCSC` property (goldens carry none;
check_ratings finds parts through it) and this mutant's parts/ dir lists that
code as a unidirectional TVS (SMBJ5.0A, Type TVS). D1's pad 2 (the anode by
KiCad's pad-1-is-cathode convention) moves from /LED_A onto +3V3 while pad 1
stays on GND: a clamp fitted backwards, forward biased across the 3.3 V rail
(#645). Every rating holds (the reverse bias is negative), so only a forward
bias test sees it.
Must be caught by check_ratings (kind "rating_diode_forward").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C113956"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0645-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "D", "mpn": "SMBJ5.0A", "lcsc": LCSC,
    "value": "SMBJ5.0A", "package": "SMB",
    "attributes": [
        {"name": "Type", "value": "TVS"},
        {"name": "Polarity", "value": "Unidirectional"},
        {"name": "Reverse Stand-Off Voltage (Vrwm)", "value": "5V"},
        {"name": "Clamping Voltage", "value": "9.2V"}],
    "role": "D1 3V3 rail clamp"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "D1", '(property "Reference" "D1"',
        PROP + '(property "Reference" "D1"', "tvs-forward-on-rail")
    text = mutlib.edit_footprint(text, "D1", '(net "/LED_A")', '(net "+3V3")',
                                 "tvs-forward-on-rail")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS)},
                  "ref": "D1", "net": "+3V3"}


if __name__ == "__main__":
    sys.exit(mutlib.run("tvs-forward-on-rail", "blinky2", surgery))
