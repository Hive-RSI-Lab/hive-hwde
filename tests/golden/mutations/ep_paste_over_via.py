"""Mutant: ep-paste-over-via (blinky2).

The hit (hifi-receiver-2 first-spin review, DFM-9): Lite's U8 had its
exposed pad's paste printed whole over its thermal vias, so solder ran down
the barrels and the ground joint voided. Here U1 gets a 5 x 5 mm GND
exposed pad, pasted whole, with an open (unfilled, uncapped) GND via at its
centre. Must be caught by check_ep_paste (kind "paste_on_via", at the via).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

EP = ('\t\t(pad "EP" smd rect\n'
      '\t\t\t(at 0 0)\n'
      '\t\t\t(size 5 5)\n'
      '\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
      '\t\t\t(net "GND")\n'
      '\t\t\t(uuid "9b1e0c7a-5d2f-4c61-9a0e-e00000000001")\n'
      '\t\t)\n')


def surgery(text):
    s, e = mutlib.footprint_block(text, "U1")
    block = text[s:e]
    i = block.index("\t\t(pad ")
    text = text[:s] + block[:i] + EP + block[i:] + text[e:]
    text = mutlib.append_items(
        text, mutlib.via_sexpr((128, 118), "GND",
                               "9b1e0c7a-5d2f-4c61-9a0e-e00000000002"),
        "EP via")
    return text, {"ref": "U1", "via": [128, 118]}


if __name__ == "__main__":
    sys.exit(mutlib.run("ep-paste-over-via", "blinky2", surgery))
