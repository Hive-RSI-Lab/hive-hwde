"""Mutant: silk-over-pad (blinky2).

Adds a silkscreen text directly over D1's cathode pad aperture. Must be
caught by check_silk (S5) / dfm_check (S12).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

UUID = "aaaa0006-dead-beef-0006-000000000001"


def surgery(text):
    text = mutlib.append_items(
        text, mutlib.silk_text_sexpr("TP1", (132.44, 129.5), UUID),
        "silk over pad")
    return text, {"ref": "D1", "pad": "1", "at": [132.44, 129.5]}


if __name__ == "__main__":
    sys.exit(mutlib.run("silk-over-pad", "blinky2", surgery))
