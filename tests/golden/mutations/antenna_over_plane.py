"""Mutant: antenna-over-plane (rf4).

PLACEMENT fault (the hit: hifi-receiver-2's core, review-2 DIGITAL-19, an
ESP32 module's PCB antenna over the board's ground). rf4 gains an
ESP32-WROOM-32E, U9, mid-board with its pad-free antenna end (local -y,
toward the board's top edge) over rf4's GND planes on all four layers. DRC
sees a module with no courtyard clash it is asked about and copper where it
was poured. Must be caught by check_antenna (kind "antenna_copper", ref U9).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

AT = (122.5, 119.0)


def module() -> str:
    """An 18 x 26 mm module, pads down both long sides from local y -6 to
    +12, so its antenna end is local -y (tests/test_antenna_straps.py's
    shape). Hard-coded UUIDs keep the file deterministic."""
    pads = []
    for i, y in enumerate(range(-6, 13, 2)):
        for n, x in ((i + 1, -8.5), (i + 21, 8.5)):
            pads.append(
                f'\t\t(pad "{n}" smd rect (at {x} {y}) (size 1 0.9) '
                f'(layers "F.Cu" "F.Mask" "F.Paste") (net "GND") '
                f'(uuid "a9e7a000-0000-4000-8000-{n:012d}"))')
    return (
        '\t(footprint "RF_Module:ESP32-WROOM-32E"\n'
        '\t\t(layer "F.Cu")\n'
        '\t\t(uuid "a9e7a000-0000-4000-8000-000000000900")\n'
        f'\t\t(at {AT[0]} {AT[1]})\n'
        '\t\t(property "Reference" "U9" (at 0 -14 0) (layer "F.SilkS") '
        '(uuid "a9e7a000-0000-4000-8000-000000000901") '
        '(effects (font (size 1 1) (thickness 0.15))))\n'
        '\t\t(property "Value" "ESP32-WROOM-32E" (at 0 14 0) (layer "F.Fab") '
        '(uuid "a9e7a000-0000-4000-8000-000000000902") '
        '(effects (font (size 1 1) (thickness 0.15))))\n'
        + mutlib.footprint_fields("a9e7a000-0000-4000-8000-00000000091") +
        '\t\t(fp_rect (start -9 -13) (end 9 13) (stroke (width 0.05) '
        '(type solid)) (fill no) (layer "F.CrtYd") '
        '(uuid "a9e7a000-0000-4000-8000-000000000903"))\n'
        + "\n".join(pads) + "\n\t)\n")


def surgery(text):
    text = mutlib.append_items(text, module(), "U9 module")
    return text, {"ref": "U9", "at": list(AT)}


if __name__ == "__main__":
    sys.exit(mutlib.run("antenna-over-plane", "rf4", surgery))
