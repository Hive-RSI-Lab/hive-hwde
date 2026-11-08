"""Mutant: plane-fed-stub-necked (usbbuck4).

SIDECAR + copper fault (#828). +3V3 is declared plane-fed (it has an In2.Cu
pour), and U1 is declared to draw 0.3 A from it. The stub that takes U1's
feed off the plane, segment (130,118.75)->(130,125.275), is necked from
0.25 mm to 0.12 mm: below the 0.15 mm IPC-2152 width for 0.3 A at dT=10C.
A plane-fed rail's whole budget no longer lands on its leaf stubs, so the
check must charge this stub what U1 draws and still call it an error.
Must be caught by check_current (kind "undersized_track", net +3V3, ref U1).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = ("\t(segment\n"
       "\t\t(start 130 118.75)\n"
       "\t\t(end 130 125.275)\n"
       "\t\t(width 0.25)\n")
NEW = ("\t(segment\n"
       "\t\t(start 130 118.75)\n"
       "\t\t(end 130 125.275)\n"
       "\t\t(width 0.12)\n")


def surgery(text):
    text = mutlib.replace_once(text, OLD, NEW, "plane-fed-stub-necked")
    cons = mutlib.golden_json("usbbuck4", "constraints.json")
    for entry in cons["power"]:
        if entry["net"] == "+3V3":
            if entry.get("plane_fed") or entry.get("loads"):
                raise mutlib.SurgeryError("golden +3V3 already plane-fed")
            entry["plane_fed"] = True
            entry["loads"] = {"U1": 0.3}
    return text, {"net": "+3V3", "ref": "U1", "width_mm": 0.12,
                  "load_a": 0.3,
                  "sidecars": {"constraints.json": mutlib.dump_json(cons)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("plane-fed-stub-necked", "usbbuck4", surgery))
