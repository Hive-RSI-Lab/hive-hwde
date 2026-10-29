"""Mutant: cpl-rotation (blinky2).

Rotates polarized D1 (LED) 180 degrees in place and swaps its pad net
assignments so the copper stays connected: the physical board now has
the LED mounted backwards (anode net on the cathode pad). DRC stays
quiet; schematic-parity and polarity-aware CPL checks disagree with it.
Must be caught by dfm_check / bom_cpl rotation validation (S12).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    # footprint angle 180 -> 0 (pads swap sides)
    text = mutlib.edit_footprint(text, "D1", "(at 131.5 129.5 180)",
                                 "(at 131.5 129.5)", "rotate D1")
    # keep copper consistent: swap the two pad nets inside D1's block
    text = mutlib.edit_footprint(text, "D1", '(net "GND")',
                                 '(net "__SWAP__")', "D1 pad1 net")
    text = mutlib.edit_footprint(text, "D1", '(net "/LED_A")',
                                 '(net "GND")', "D1 pad2 net")
    text = mutlib.edit_footprint(text, "D1", '(net "__SWAP__")',
                                 '(net "/LED_A")', "D1 pad1 net swap")
    return text, {"ref": "D1", "rotation_delta_deg": 180,
                  "note": "polarized part mounted backwards"}


if __name__ == "__main__":
    sys.exit(mutlib.run("cpl-rotation", "blinky2", surgery))
