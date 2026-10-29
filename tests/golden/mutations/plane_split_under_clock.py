"""Mutant: plane-split-under-clock (rf4).

Adds a copper-pour keepout slot on In1.Cu crossing under the RF_FEED
trace, so the feed's return current has no continuous reference plane.
Must be caught by check_return_path (corridor intersects plane void).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

RECT = (133.0, 116.5, 134.4, 127.5)  # slot straddling the feed at y=122
UUID = "aaaa0001-dead-beef-0001-000000000001"


def surgery(text):
    text = mutlib.append_items(
        text,
        mutlib.keepout_zone_sexpr("In1.Cu", RECT, "mutant-plane-split", UUID),
        "plane-split keepout")
    return text, {"target_net": "RF_FEED", "layer": "In1.Cu", "slot": RECT}


if __name__ == "__main__":
    sys.exit(mutlib.run("plane-split-under-clock", "rf4", surgery))
