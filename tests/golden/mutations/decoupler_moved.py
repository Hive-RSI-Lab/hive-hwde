"""Mutant: decoupler-moved-15mm (blinky2).

Moves C1 (the VDD_3/pin-48 100 nF decoupler) from (124.5,110.5) to
(140.0,106.8) - 15.7 mm pad-to-pin - and rewires it legally (feeds from
the 3V3 top corridor, ground via of its own), so DRC stays clean. Must
be caught by check_decoupling (Manhattan distance / loop inductance).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

U1 = "aaaa0004-dead-beef-0004-000000000001"
U2 = "aaaa0004-dead-beef-0004-000000000002"
U3 = "aaaa0004-dead-beef-0004-000000000003"


def surgery(text):
    # relocate the footprint
    text = mutlib.edit_footprint(text, "C1", "(at 124.5 110.5)",
                                 "(at 140 106.8)", "move C1")
    # old C1.2 ground stub + its via are now orphaned
    text = mutlib.remove_block(
        text, "(start 125.275 110.5)", "(segment", "drop C1.2 stub")
    text = mutlib.remove_block(
        text, "(at 125.275 109.2)", "(via", "drop C1.2 via")
    # shorten the west 3V3 bus: it used to end on C1.1
    text = mutlib.remove_block(
        text, "(start 122.5 110.5)\n\t\t(end 123.725 110.5)", "(segment",
        "trim 3V3 bus tail")
    # rewire relocated C1: 3V3 from the top corridor, own ground via
    items = (
        mutlib.segment_sexpr((139.225, 106.8), (139.225, 104.2), 0.25,
                             "F.Cu", "+3V3", U1)
        + mutlib.segment_sexpr((140.775, 106.8), (141.6, 106.8), 0.25,
                               "F.Cu", "GND", U2)
        + mutlib.via_sexpr((141.6, 106.8), "GND", U3)
    )
    text = mutlib.append_items(text, items, "rewire C1")
    return text, {"ref": "C1", "target_pin": "U1.48",
                  "new_at": [140.0, 106.8], "pad_to_pin_mm": 15.7}


if __name__ == "__main__":
    sys.exit(mutlib.run("decoupler-moved", "blinky2", surgery))
