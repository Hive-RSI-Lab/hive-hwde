"""Mutant: source-stub-necked (usbbuck4).

SIDECAR + copper fault (#845). +3V3 is declared plane-fed with L1, the
inductor behind the 3V3 regulator, named as its source at 0.4 A. The track
that takes L1's output toward the rail, segment (118.5,106.9)->(121.3,106.9),
is necked from 0.8 mm to 0.1 mm: below the 0.15 mm IPC-2152 width for 0.4 A at
dT=10C. A stub that holds the named source carries its output, so this is an
error, not the full-budget warning a source stub used to get.
Must be caught by check_current (kind "undersized_track", net +3V3, ref L1).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = ("\t(segment\n"
       "\t\t(start 118.5 106.9)\n"
       "\t\t(end 121.3 106.9)\n"
       "\t\t(width 0.8)\n")
NEW = OLD.replace("(width 0.8)", "(width 0.1)")


def surgery(text):
    text = mutlib.replace_once(text, OLD, NEW, "source-stub-necked")
    cons = mutlib.golden_json("usbbuck4", "constraints.json")
    for entry in cons["power"]:
        if entry["net"] == "+3V3":
            if entry.get("plane_fed") or entry.get("source"):
                raise mutlib.SurgeryError("golden +3V3 already plane-fed")
            entry["plane_fed"] = True
            entry["source"] = "L1"
    return text, {"net": "+3V3", "ref": "L1", "width_mm": 0.1,
                  "output_a": 0.4,
                  "sidecars": {"constraints.json": mutlib.dump_json(cons)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("source-stub-necked", "usbbuck4", surgery))
