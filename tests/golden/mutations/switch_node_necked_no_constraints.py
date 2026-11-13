"""Mutant: switch-node-necked-no-constraints (usbbuck4).

Copper fault on a board with no constraints.json (#876, the digits v1 miss:
an LM2596 switch node routed 0.25 mm on a board whose author never wrote a
power list). U2 (AP63203WU, 2 A) has its switch pin named SW, as a board
from a schematic carries it, and the /SW segment (112.6,104.1)->(117.9,104.1)
is necked from 0.8 mm to 0.2 mm, below the width 2 A needs. The manifest
row drops the constraints sidecar, so check_current must run without one and
still judge the switch node from the part table.
Must be caught by check_current (kind "undersized_track", net /SW, ref U2).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = ("\t(segment\n"
       "\t\t(start 112.6 104.1)\n"
       "\t\t(end 117.9 104.1)\n"
       "\t\t(width 0.8)\n")
NEW = OLD.replace("(width 0.8)", "(width 0.2)")
PAD_OLD = ("\t\t\t(net \"/SW\")\n"
           "\t\t\t(uuid \"6e61df7f-7472-4fc2-9957-36466ab3c505\")\n")
PAD_NEW = ("\t\t\t(net \"/SW\")\n"
           "\t\t\t(pinfunction \"SW\")\n"
           "\t\t\t(uuid \"6e61df7f-7472-4fc2-9957-36466ab3c505\")\n")


def surgery(text):
    text = mutlib.replace_once(text, OLD, NEW, "switch-node-necked")
    text = mutlib.replace_once(text, PAD_OLD, PAD_NEW, "U2 pad 5 pinfunction")
    return text, {"net": "/SW", "ref": "U2", "width_mm": 0.2}


if __name__ == "__main__":
    sys.exit(mutlib.run("switch-node-necked-no-constraints", "usbbuck4",
                        surgery))
