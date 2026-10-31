"""Mutant: attr-tht-on-smd (blinky2).

LIBRARY fault (the hit: EasyEDA's XR0850 power inductor, lite-main L1-L4).
U2's SOT-223 footprint says `(attr through_hole)` on its four SMD pads, so
bom_cpl's old reading and KiCad's position export sent a reflowed regulator
to the THT step. Copper is untouched. Must be caught by check_fp_attr (kind
"fp_attr_mismatch", ref U2, at U2's origin).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    s, e = mutlib.footprint_block(text, "U2")
    block = mutlib.replace_once(text[s:e], "(attr smd)",
                                "(attr through_hole)", "U2 attr")
    return text[:s] + block + text[e:], {"ref": "U2", "attr": "through_hole"}


if __name__ == "__main__":
    sys.exit(mutlib.run("attr-tht-on-smd", "blinky2", surgery))
