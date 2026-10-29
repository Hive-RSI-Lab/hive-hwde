"""Mutant: diffpair-skew (usbbuck4).

Inserts a 6.2 mm meander into USB_DM's bottom corridor run while
USB_DP is untouched, adding ~6.2 mm of intra-pair skew (~40 ps on FR4).
Must be caught by check_diffpair.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

U = [f"aaaa0005-dead-beef-0005-00000000000{i}" for i in range(1, 6)]


def surgery(text):
    text = mutlib.remove_block(
        text, "(start 106.85 134.5)\n\t\t(end 142.75 134.5)", "(segment",
        "drop DM corridor run")
    # meander goes NORTH: USB_DP runs 0.65 mm south of DM (y=135.15)
    pts = [(106.85, 134.5), (128.0, 134.5), (128.0, 131.4),
           (136.0, 131.4), (136.0, 134.5), (142.75, 134.5)]
    items = "".join(
        mutlib.segment_sexpr(a, b, 0.25, "F.Cu", "/USB_DM", U[i])
        for i, (a, b) in enumerate(zip(pts, pts[1:])))
    text = mutlib.append_items(text, items, "DM meander")
    return text, {"pair": ["/USB_DP", "/USB_DM"], "added_skew_mm": 6.2}


if __name__ == "__main__":
    sys.exit(mutlib.run("diffpair-skew", "usbbuck4", surgery))
