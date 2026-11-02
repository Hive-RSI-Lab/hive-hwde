"""Mutant: kelvin-tap-past-neck (blinky2).

MEASUREMENT fault (the hit: hc-power's cold review, U16 reading R104 through a tap
past a 0.2 mm neck, 46 % high, every gate green). A 10 mOhm shunt R901, its inductor
L901 and amplifier U901 are added, with R902's tap taken at the inductor's pad, 6 mm of
0.5 mm track from the shunt. All nets connect and all widths are legal. Must be caught
by check_kelvin (kind "kelvin_error", refs R901 and U901).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def _u(n, k):
    return f"79000000-0000-4000-8000-{n:06d}{k:06d}"


def _fp(ref, value, x, y, n, pads):
    rows = "".join(
        f'\t\t(pad "{num}" smd rect\n\t\t\t(at {dx} 0)\n\t\t\t(size {s} {s})\n'
        f'\t\t\t(layers "F.Cu")\n\t\t\t(net "{net}")\n'
        f'\t\t\t(uuid "{_u(n, 100 + int(num))}")\n\t\t)\n'
        for num, dx, s, net in pads)
    return (f'\t(footprint "t:KELVIN"\n\t\t(layer "F.Cu")\n'
            f'\t\t(uuid "{_u(n, 1)}")\n\t\t(at {x} {y})\n'
            f'\t\t(property "Reference" "{ref}"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")\n'
            f'\t\t\t(uuid "{_u(n, 2)}")\n\t\t)\n'
            f'\t\t(property "Value" "{value}"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")\n'
            f'\t\t\t(uuid "{_u(n, 3)}")\n\t\t)\n'
            + mutlib.footprint_fields(_u(n, 9)[:35])
            + f'{rows}\t)\n')


def _seg(net, x0, y0, x1, y1, w, n):
    return (f'\t(segment\n\t\t(start {x0} {y0})\n\t\t(end {x1} {y1})\n\t\t(width {w})\n'
            f'\t\t(layer "F.Cu")\n\t\t(net "{net}")\n'
            f'\t\t(uuid "{_u(9, n)}")\n\t)\n')


ITEMS = (
    _fp("R901", "10m", 120, 125, 1, [("1", 2, 2, "KA"), ("2", -2, 2, "KB")])
    + _fp("L901", "4.7uH", 132, 125, 2, [("1", -2, 2, "KA"), ("2", 2, 2, "KSW")])
    + _fp("J901", "CONN", 106, 125, 3, [("1", 0, 2, "KB")])
    + _fp("R902", "0R", 124, 130, 4, [("1", 0, 1, "KA"), ("2", 2, 1, "KK")])
    + _fp("U901", "INA240", 112, 130, 5, [("1", 14, 1, "KK"), ("2", 0, 1, "KB")])
    + _seg("KA", 122, 125, 130, 125, 0.5, 1)
    + _seg("KB", 106, 125, 118, 125, 3, 2)
    + _seg("KA", 124, 130, 130, 130, 0.2, 3)
    + _seg("KA", 130, 130, 130, 125, 0.2, 4)
    + _seg("KB", 112, 130, 118, 130, 0.2, 5)
    + _seg("KB", 118, 130, 118, 125, 0.2, 6)
)


def surgery(text):
    at = text.index("\t(segment\n")
    return text[:at] + ITEMS + text[at:], {"refs": ["R901", "U901"]}


if __name__ == "__main__":
    sys.exit(mutlib.run("kelvin-tap-past-neck", "blinky2", surgery))
