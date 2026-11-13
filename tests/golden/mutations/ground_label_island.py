"""Mutant: ground-label-island (usbbuck4).

Erratum class #877 (digits v1: MIC_GND is a local label that never reaches
GND). The crystal's load caps C7 and C8 return to a local OSC_GND label
where the GND label belonged, so their ground floats. Must be caught by
netlist_audit as ground_island /OSC_GND at C7 and C8.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    sch = mutlib.load_golden_sch("usbbuck4")
    for ref, at in (("C7", (281.94, 158.75)), ("C8", (297.18, 158.75))):
        sch = mutlib.rename_label(sch, "GND", at, "OSC_GND",
                                  f"{ref} ground label")
    return text, {"net": "/OSC_GND", "refs": ["C7", "C8"],
                  "sidecars": {"usbbuck4.kicad_sch": sch}}


if __name__ == "__main__":
    sys.exit(mutlib.run("ground-label-island", "usbbuck4", surgery))
