"""Mutant: listing-disagrees-with-datasheet (blinky2).

SIDECAR + one board property. D1 gets an `LCSC` property on the board (goldens
carry none; check_ratings finds parts through it) and this mutant's parts/ dir
holds a listing of 50 V reverse beside an extraction reading 25 V for that
code. Both sit well above D1's rail, so nothing else on the board fires.
Must be caught by check_ratings (kind "rating_listing_disagrees").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C2286"
PROP = (f'(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 0)\n'
        '\t\t\t(layer "F.Fab")\n'
        '\t\t\t(hide yes)\n'
        '\t\t\t(uuid "aaaa0010-dead-beef-0799-000000000001")\n'
        '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n'
        '\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t\t')
PARTS = {"parts": [{
    "ref_prefix_hint": "D", "mpn": "D-TEST", "lcsc": LCSC,
    "value": "red", "package": "0603",
    "attributes": [{"name": "Voltage - DC Reverse(Vr)", "value": "50V"}],
    "role": "D1 status LED"}]}
EXTRACTION = {"mpn": "D-TEST", "abs_max": [
    {"param": "VR reverse voltage", "value": 25, "unit": "V"}]}


def surgery(text):
    text = mutlib.edit_footprint(
        text, "D1", '(property "Reference" "D1"',
        PROP + '(property "Reference" "D1"',
        "listing-disagrees-with-datasheet")
    return text, {"sidecars": {"parts/parts.json": mutlib.dump_json(PARTS),
                               f"parts/{LCSC}.json": mutlib.dump_json(EXTRACTION)},
                  "ref": "D1", "net": "/LED_A"}


if __name__ == "__main__":
    sys.exit(mutlib.run("listing-disagrees-with-datasheet", "blinky2", surgery))
