"""Mutant: sense-tap-teed-off-rail (blinky2).

MEASUREMENT fault (the hit: the voice board's feedback divider tops R113/R201 sat in
their rails' pours, reading 6-16 mm of copper away from the pad their sense line was
meant to tap). A rail KP runs from L911 to its output capacitor C911 on a 1 mm track;
R911's sense line, declared in constraints.json power[].sense_pads to read C911.1,
tees into that track 7 mm short of the pad instead. All nets connect and all widths
are legal. Must be caught by check_kelvin (kind "sense_tap_off_pad", refs C911 and
R911).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def _u(n, k):
    return f"79100000-0000-4000-8000-{n:06d}{k:06d}"


def _fp(ref, value, x, y, n, pads):
    rows = "".join(
        f'\t\t(pad "{num}" smd rect\n\t\t\t(at {dx} 0)\n\t\t\t(size {s} {s})\n'
        f'\t\t\t(layers "F.Cu")\n\t\t\t(net "{net}")\n'
        f'\t\t\t(uuid "{_u(n, 100 + int(num))}")\n\t\t)\n'
        for num, dx, s, net in pads)
    return (f'\t(footprint "t:SENSE"\n\t\t(layer "F.Cu")\n'
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
    _fp("L911", "4.7uH", 120, 125, 1, [("1", 0, 2, "KP")])
    + _fp("C911", "22u", 132, 125, 2, [("1", 0, 2, "KP")])
    + _fp("R911", "100k", 132, 131, 3, [("1", 0, 1, "KP"), ("2", 2, 1, "KFB")])
    + _seg("KP", 120, 125, 132, 125, 1, 1)
    + _seg("KP", 132, 131, 124, 131, 0.2, 2)
    + _seg("KP", 124, 131, 124, 125, 0.2, 3)
)


def surgery(text):
    at = text.index("\t(segment\n")
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["power"].append({"net": "KP", "current_a": 0.1, "pdn": False,
                          "sense_pads": {"R911.1": "C911.1"}})
    return text[:at] + ITEMS + text[at:], {
        "refs": ["C911", "R911"],
        "sidecars": {"constraints.json": mutlib.dump_json(cons)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("sense-tap-teed-off-rail", "blinky2", surgery))
