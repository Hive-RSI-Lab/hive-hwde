"""Mutant: usb-faces-inward-unnamed (usbbuck4).

PLACEMENT fault, as usb-faces-inward (J1 turned 180 degrees so its mouth
faces into the board), on a footprint whose name says nothing: J1's
footprint is renamed CONN-SMD_5P-TEST, the way an EasyEDA import names a
connector (the laptop hub's microSD sat under SIM-SMD_8P-1040310811, #953).
Its value still says USB_micro. Must be caught by check_mating (kind
"mating_faces_inward", ref J1), which then matches the family on the
value.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
import usb_faces_inward


def surgery(text):
    text, detail = usb_faces_inward.surgery(text)
    text = mutlib.replace_once(
        text, '(footprint "Connector_USB:USB_Micro-B_Amphenol_10118194_'
        'Horizontal"', '(footprint "Connector:CONN-SMD_5P-TEST"',
        "rename J1's footprint")
    return text, {**detail, "footprint": "Connector:CONN-SMD_5P-TEST"}


if __name__ == "__main__":
    sys.exit(mutlib.run("usb-faces-inward-unnamed", "usbbuck4", surgery))
