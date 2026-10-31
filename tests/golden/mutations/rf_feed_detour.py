"""Mutant: rf-feed-detour (rf4).

LAYOUT fault: /RF_FEED from L2 to the SMA J1 takes a 16 mm U-shaped detour
down between the fence vias, so the feed is ~59 deg at 915 MHz where the pi
match was laid for ~31 (constraints max_deg 40). Every track still meets
its pad and DRC passes. Must be caught by check_rf (kind "rf_length", net
/RF_FEED).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
import rf4_rf

OLD = ("\t\t(start 126.175 122)\n\t\t(end 141.9 122)\n\t\t(width 0.35)\n"
       "\t\t(layer \"F.Cu\")\n\t\t(net \"/RF_FEED\")\n")
PATH = [(126.175, 122.0), (130.2, 122.0), (130.2, 130.0), (132.2, 130.0),
        (132.2, 122.0), (141.9, 122.0)]


def surgery(text):
    new = ("\t\t(start 126.175 122)\n\t\t(end 130.2 122)\n\t\t(width 0.35)\n"
           "\t\t(layer \"F.Cu\")\n\t\t(net \"/RF_FEED\")\n")
    text = mutlib.replace_once(text, OLD, new, "feed segment")
    segs = "".join(
        mutlib.segment_sexpr(a, b, 0.35, "F.Cu", "/RF_FEED",
                             f"a9e7a000-0000-4000-8000-000000000b0{k}")
        for k, (a, b) in enumerate(zip(PATH[1:], PATH[2:])))
    text = mutlib.append_items(text, segs, "detour")
    return text, {"sidecars": rf4_rf.sidecar(),
                  "path": [list(p) for p in PATH]}


if __name__ == "__main__":
    sys.exit(mutlib.run("rf-feed-detour", "rf4", surgery))
