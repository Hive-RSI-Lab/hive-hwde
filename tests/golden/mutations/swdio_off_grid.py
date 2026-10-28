"""Mutant: swdio-off-grid (blinky2).

GEOMETRY fault. The straight /SWDIO run (133.4,120.2)->(143.96,120.2) is
replaced by a two-segment dogleg through (138.68,118.8): both legs sit ~15
degrees off the 45-degree grid (owner style: straight and 45 only). Same net,
same endpoints, so connectivity is unchanged. Must be caught by
check_route_style (kind "route_style", an `angle` finding).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = ("\t(segment\n"
       "\t\t(start 133.4 120.2)\n"
       "\t\t(end 143.96 120.2)\n"
       "\t\t(width 0.25)\n"
       "\t\t(layer \"F.Cu\")\n"
       "\t\t(net \"/SWDIO\")\n"
       "\t\t(uuid \"c4713da8-bfdd-4d9a-9eb6-1c5b3a4d0f69\")\n"
       "\t)\n")
MID = (138.68, 118.8)


def surgery(text):
    new = (mutlib.segment_sexpr((133.4, 120.2), MID, 0.25, "F.Cu", "/SWDIO",
                                "c4713da8-bfdd-4d9a-9eb6-1c5b3a4d0f69")
           + mutlib.segment_sexpr(MID, (143.96, 120.2), 0.25, "F.Cu",
                                  "/SWDIO",
                                  "aaaa0008-dead-beef-0008-000000000001"))
    text = mutlib.replace_once(text, OLD, new, "swdio-off-grid")
    return text, {"net": "/SWDIO", "mid": list(MID)}


if __name__ == "__main__":
    sys.exit(mutlib.run("swdio-off-grid", "blinky2", surgery))
