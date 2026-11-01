"""Mutant: surge-swd-unclamped (blinky2).

SIDECAR fault (copper is the golden's, copied unchanged): constraints.json
declares J2 (the SWD header) a signal port on an outdoor cable. The board has
no clamp, so the assumed 1 kV line-ground surge through 42 ohm meets /SWDIO
and /SWCLK unprotected. Must be caught by check_surge (kind "surge_unclamped",
net /SWDIO).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["surge"] = {"ports": {"J2": {"kind": "signal", "cable_m": 40,
                                      "outdoor": True}}}
    return text, {"sidecars": {"constraints.json": mutlib.dump_json(cons)},
                  "group": "J2"}


if __name__ == "__main__":
    sys.exit(mutlib.run("surge-swd-unclamped", "blinky2", surgery))
