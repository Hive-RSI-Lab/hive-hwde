"""Mutant: swd-length-unmatched (blinky2).

SIDECAR fault (copper is the golden's, copied unchanged): this mutant's
constraints.json declares SWCLK and SWDIO a length group matched within
1 mm, as a DDR byte lane is. The golden routes /SWCLK 14.0 mm (J2.3 to U1.37)
and /SWDIO 25.4 mm (J2.2 to U1.34), so SWCLK is 11.4 mm short of the
group's longest member. Must be caught by check_length_groups (kind
"length_group_mismatch", net /SWCLK).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["length_groups"] = [{"name": "SWD", "nets": ["/SWCLK", "/SWDIO"],
                              "tol_mm": 1.0}]
    return text, {"sidecars": {"constraints.json": mutlib.dump_json(cons)},
                  "group": "SWD"}


if __name__ == "__main__":
    sys.exit(mutlib.run("swd-length-unmatched", "blinky2", surgery))
