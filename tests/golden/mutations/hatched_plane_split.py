"""Mutant: hatched-plane-split (rf4).

Turns rf4's In1 GND plane into a hatched pour (0.25 mm bars, 0.55 mm
cells: a flex tail's usual GND) and cuts the plane-split-under-clock slot
under RF_FEED. Before #870 check_return_path read every hatch cell as a
void; it now reads the hatch as solid, and this pins that a real cut-out
in a hatched plane is still found (corridor_void on /RF_FEED at the slot).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
import plane_split_under_clock as split

PLANE_UUID = "bf1fb3fe-3fb9-4e5f-b7ee-7756ae19f96a"
SOLID = ("\t\t(fill yes\n\t\t\t(thermal_gap 0.5)\n"
         "\t\t\t(thermal_bridge_width 0.5)\n\t\t\t(island_removal_mode 0)\n"
         "\t\t)\n")
HATCH = ("\t\t(fill yes\n\t\t\t(mode hatch)\n\t\t\t(thermal_gap 0.5)\n"
         "\t\t\t(thermal_bridge_width 0.5)\n\t\t\t(island_removal_mode 0)\n"
         "\t\t\t(hatch_thickness 0.25)\n\t\t\t(hatch_gap 0.55)\n"
         "\t\t\t(hatch_orientation 45)\n"
         "\t\t\t(hatch_border_algorithm hatch_thickness)\n"
         "\t\t\t(hatch_min_hole_area 0.15)\n\t\t)\n")
UUID = "aaaa0870-dead-beef-0870-000000000001"


def surgery(text):
    head = f'(uuid "{PLANE_UUID}")'
    i = text.index(head)
    j = text.index(SOLID, i)
    if j - i > 400:
        raise mutlib.SurgeryError("In1 GND plane fill block not found")
    text = text[:j] + HATCH + text[j + len(SOLID):]
    text = mutlib.append_items(
        text,
        mutlib.keepout_zone_sexpr("In1.Cu", split.RECT, "mutant-hatch-split",
                                  UUID),
        "hatched-plane slot keepout")
    return text, {"target_net": "/RF_FEED", "layer": "In1.Cu",
                  "slot": split.RECT, "hatched": True}


if __name__ == "__main__":
    sys.exit(mutlib.run("hatched-plane-split", "rf4", surgery))
