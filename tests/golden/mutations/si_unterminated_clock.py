"""Mutant: si-unterminated-clock (usbbuck4).

Replaces MCO's straight 15.6 mm B.Cu run (140.4, 123) -> (140.4, 138.6)
with a detour out to x = 157, so the MCU clock-out runs ~63 mm - past the
~55 mm critical length of a 2 ns edge on this stackup - with no series
resistor. Must be caught by check_si (si_unterminated_clock on /MCO).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

U = [f"aaaa0031-dead-beef-0031-00000000000{i}" for i in range(1, 4)]


def surgery(text):
    text = mutlib.remove_block(
        text, "(start 140.4 123)\n\t\t(end 140.4 138.6)", "(segment",
        "drop MCO B.Cu run")
    pts = [(140.4, 123.0), (157.0, 123.0), (157.0, 138.6), (140.4, 138.6)]
    items = "".join(
        mutlib.segment_sexpr(a, b, 0.25, "B.Cu", "/MCO", U[i])
        for i, (a, b) in enumerate(zip(pts, pts[1:])))
    text = mutlib.append_items(text, items, "MCO detour")
    return text, {"target_net": "/MCO", "added_mm": 33.2}


if __name__ == "__main__":
    sys.exit(mutlib.run("si-unterminated-clock", "usbbuck4", surgery))
