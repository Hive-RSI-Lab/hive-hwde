"""Mutant: post-holes-missing (blinky2).

The hit (hifi-receiver-2 first-spin review, DFM-1): core's RCA jacks sat on
a footprint with no holes for their two locating posts, so the posts stood
on the board top and the leads never reached the solder side. Here J2's
drawing (its extraction, parts/C9900001.json, `mechanical.posts`) has two
1.0 mm posts and the 1x04 header footprint has no hole for them. Copper is
untouched; J2 gains its LCSC property. Must be caught by check_connector_fit
(kind "post_holes_missing", ref J2, at J2's origin).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C9900001"
EXTRACTION = {
    "mpn": "HDR-1x04-POSTS", "lcsc": LCSC,
    "pinout": [{"pin": str(n), "name": f"P{n}", "type": "passive"}
               for n in range(1, 5)],
    "mechanical": {"posts": [{"count": 2, "dia_mm": 1.0}],
                   "source": "planted fault"},
}


def surgery(text):
    s, e = mutlib.footprint_block(text, "J2")
    block = mutlib.replace_once(
        text[s:e], "(at 146.5 110.5 -90)",
        f'(at 146.5 110.5 -90)\n\t\t(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 -90)\n\t\t\t(layer "F.Fab")\n\t\t\t(hide yes)\n'
        '\t\t\t(uuid "9b1e0c7a-5d2f-4c61-9a0e-f00000000001")\n\t\t)',
        "J2 LCSC")
    return text[:s] + block + text[e:], {
        "ref": "J2", "lcsc": LCSC,
        "sidecars": {f"parts/{LCSC}.json": mutlib.dump_json(EXTRACTION)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("post-holes-missing", "blinky2", surgery))
