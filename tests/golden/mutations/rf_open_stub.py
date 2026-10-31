"""Mutant: rf-open-stub (rf4).

LAYOUT fault: a 14 mm F.Cu branch left on /RF_FEED (a rip-up's leftover, or
a tuning stub never cut back) runs up from the feed between the fence vias
and ends on no pad: at 915 MHz it is ~25 deg, past lambda/20, a reactive
stub that detunes the match. DRC passes a dangling track of the net's own
copper. Must be caught by check_rf (kind "rf_stub", net /RF_FEED).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
import rf4_rf

START, END = (134.2, 122.0), (134.2, 108.0)


def surgery(text):
    seg = mutlib.segment_sexpr(START, END, 0.35, "F.Cu", "/RF_FEED",
                               "a9e7a000-0000-4000-8000-000000000a01")
    text = mutlib.append_items(text, seg, "stub segment")
    return text, {"sidecars": rf4_rf.sidecar(),
                  "stub": [list(START), list(END)]}


if __name__ == "__main__":
    sys.exit(mutlib.run("rf-open-stub", "rf4", surgery))
