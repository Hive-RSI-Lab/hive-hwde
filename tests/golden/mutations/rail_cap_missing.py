"""Mutant: rail-cap-missing (blinky2).

GEOMETRY + SIDECAR. C6, the only capacitor on the +5V input rail, is deleted
from the board, and dropped from this mutant's decoupling.json (the metadata
mirrors the board). +5V is left with no decoupling at all. Must be caught by
check_pdn (kind "pdn_undecoupled").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    s, e = mutlib.footprint_block(text, "C6")
    text = text[:s] + text[e:]
    dec = mutlib.golden_json("blinky2", "decoupling.json")
    dec["associations"] = [a for a in dec["associations"] if a["cap"] != "C6"]
    return text, {"sidecars": {"decoupling.json": mutlib.dump_json(dec)},
                  "rail": "+5V", "removed": "C6"}


if __name__ == "__main__":
    sys.exit(mutlib.run("rail-cap-missing", "blinky2", surgery))
