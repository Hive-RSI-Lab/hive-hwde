"""Mutant: mech-05-pi-header-mirrored (blinky2).

PLACEMENT fault (MECH-05, a stacking header mirrored against its mate, as on
digits v1 J1 in the real-errata corpus). A Raspberry Pi 40-pin GPIO header
J9 is added on B.Cu along the bottom edge so blinky2 sits on a Pi below it:
pin 1 +3V3, pin 2 +5V, pin 6 GND, the Pi's own pinout. It is the stock
PinHeader_2x20 footprint, numbered for F.Cu, flipped to the back (KiCad
mirrors its pads), so seen from above its pattern is the mirror of the Pi's
header and the odd and even rows swap: J9 pin 2 (+5V) lands on the Pi's
pin 1 (3V3). No constraints.json names the Pi; the standard header is
known by its supply pins. Must be caught by check_mate_pins (kind
"mate_mirrored_pinout", ref J9, at J9 pin 1).

`header(mirrored=False)` builds the same header with the socket's back-side
numbering (the right pattern); the unit test uses it as the clean case.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

AT = (100.95, 136.2, -90)
NETS = {"1": "+3V3", "2": "+5V", "6": "GND"}
FPID = "Connector_PinHeader_2.54mm:PinHeader_2x20_P2.54mm_Vertical"


def header(mirrored: bool = True, ref: str = "J9", at=AT,
           nets=NETS) -> str:
    """The 2x20 on B.Cu as KiCad saves it. mirrored: pin 2 at +x, rows
    along -y (a F.Cu PinHeader flipped); else rows along +y (the pattern a
    back-side socket needs to meet a Pi face up below)."""
    sy = -1 if mirrored else 1
    pads = []
    for n in range(1, 41):
        x, y = 2.54 * ((n - 1) % 2), sy * 2.54 * ((n - 1) // 2)
        net = f' (net "{nets[str(n)]}")' if str(n) in nets else ""
        pads.append(
            f'\t\t(pad "{n}" thru_hole {"rect" if n == 1 else "oval"}'
            f' (at {x:.2f} {y:.2f} {at[2]}) (size 1.7 1.7) (drill 1)'
            f' (layers "*.Cu" "*.Mask"){net}'
            f' (uuid "00000000-0000-4000-8000-0000000507{n:02d}"))\n')
    y0, y1 = sorted((-1.33 * sy, sy * 49.59))
    return (f'\n\t(footprint "{FPID}"\n\t\t(layer "B.Cu")\n'
            f'\t\t(uuid "00000000-0000-4000-8000-000000050700")\n'
            f'\t\t(at {at[0]} {at[1]} {at[2]})\n'
            f'\t\t(property "Reference" "{ref}" (at 1.27 -3 {at[2]})'
            f' (layer "B.SilkS")'
            f' (uuid "00000000-0000-4000-8000-000000050741")\n'
            f'\t\t\t(effects (font (size 1 1) (thickness 0.15))'
            f' (justify mirror)))\n'
            f'\t\t(property "Value" "Conn_02x20_Odd_Even" (at 1.27 3 {at[2]})'
            f' (layer "B.Fab")'
            f' (uuid "00000000-0000-4000-8000-000000050742")\n'
            f'\t\t\t(effects (font (size 1 1) (thickness 0.15))'
            f' (justify mirror)))\n'
            f'\t\t(attr through_hole)\n'
            f'\t\t(fp_rect (start -1.8 {y0}) (end 4.35 {y1})'
            f' (stroke (width 0.05) (type solid)) (fill no)'
            f' (layer "B.CrtYd")'
            f' (uuid "00000000-0000-4000-8000-000000050743"))\n'
            + "".join(pads) + "\t)")


def surgery(text):
    # after the last footprint, so every other item keeps its place
    i = text.rfind("\n\t(footprint")
    last = re.search(r'\(property "Reference" "([^"]+)"', text[i:]).group(1)
    e_last = mutlib.footprint_block(text, last)[1]
    text = text[:e_last] + header() + text[e_last:]
    return text, {"ref": "J9", "at": list(AT), "layer": "B.Cu",
                  "pad_nets": NETS, "mirrored": True}


if __name__ == "__main__":
    sys.exit(mutlib.run("mech-05-pi-header-mirrored", "blinky2", surgery))
