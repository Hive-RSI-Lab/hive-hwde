"""Mutant: missing-return-via (usbbuck4).

Deletes the GND return via at (141.9, 123.4) that accompanies the MCO
clock net's F.Cu -> B.Cu layer transition at (141.0, 123.0). The next
GND via is more than 2 mm away. Must be caught by check_return_path
(layer-transition rule: same-reference-net via within radius).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    text = mutlib.remove_block(text, "(at 141.9 123.4)", "(via",
                               "missing-return-via")
    return text, {"target_net": "/MCO", "transition_at": [141.0, 123.0],
                  "removed_via": [141.9, 123.4]}


if __name__ == "__main__":
    sys.exit(mutlib.run("missing-return-via", "usbbuck4", surgery))
