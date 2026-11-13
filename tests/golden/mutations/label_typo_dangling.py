"""Mutant: label-typo-dangling (usbbuck4).

Erratum class #877 (digits v1: J7's BELL_1/BELL_2 labels reach no other
part). One of the three OSC_IN labels, the one at C7's top pin, is typed
OSC_IM: C7 drops off the crystal net onto a net of its own. ERC, DRC and
parity never see it: the board is exported from this schematic. Must be
caught by netlist_audit as dangling_net /OSC_IM at C7.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    sch = mutlib.rename_label(mutlib.load_golden_sch("usbbuck4"), "OSC_IN",
                              (281.94, 146.05), "OSC_IM", "OSC_IN label typo")
    return text, {"net": "/OSC_IM", "ref": "C7",
                  "sidecars": {"usbbuck4.kicad_sch": sch}}


if __name__ == "__main__":
    sys.exit(mutlib.run("label-typo-dangling", "usbbuck4", surgery))
