"""Mutant: swd-ddr-setup (blinky2).

SIDECAR fault (copper is the golden's, copied unchanged): constraints.json
treats /SWDIO as a DDR data bit strobed by /SWCLK at DDR3L-1600 (Micron
MT41K256M16, AC135) on a controller with 100 ps output skew and no write
calibration. The golden routes /SWCLK 14.0 mm and /SWDIO 25.4 mm, so SWDIO
arrives ~70 ps after its "strobe": setup = UI/2 312.5 - tDS(VREF) 160 - 100
- ~70 < 0. Must be caught by check_ddr_timing (kind "ddr_setup_violation",
net /SWDIO). The length group's tol_mm is wide so check_length_groups stays
quiet: the fault is timing, not the HDG window.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["length_groups"] = [{"name": "SWD", "nets": ["/SWDIO"],
                              "ref_nets": ["/SWCLK"], "tol_mm": 20.0}]
    cons["ddr"] = {"dram_part": "MT41K256M16TW-107",
                   "controller_part": "GENERIC", "rate_mts": 1600,
                   "input_levels": {"data": "AC135", "addr": "AC135"},
                   "controller_terms": {"out_skew_ps": 100,
                                        "write_calibration": False,
                                        "write_leveling": False},
                   "groups": [{"name": "SWD", "kind": "dq"}]}
    return text, {"sidecars": {"constraints.json": mutlib.dump_json(cons)},
                  "group": "SWD"}


if __name__ == "__main__":
    sys.exit(mutlib.run("swd-ddr-setup", "blinky2", surgery))
