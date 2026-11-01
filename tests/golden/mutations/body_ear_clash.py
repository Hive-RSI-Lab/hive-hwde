"""Mutant: body-ear-clash (blinky2).

LIBRARY fault (the hit: hifi core's RCA jack, whose side ear stood 4 mm past a
hand-drawn courtyard and 2 mm into the speaker post beside it). J1's footprint
gains a second body, past its courtyard, that runs into U2. The copper and the
courtyards don't change, so DRC and the courtyard check still pass. Must be
caught by check_body_clash (kind "body_clash", refs J1 and U2).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

MODEL = '(model "${KICAD10_3DMODEL_DIR}/Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x02_P2.54mm_Vertical.step"\n\t\t\t(offset\n\t\t\t\t(xyz 0 0 0)\n\t\t\t)\n\t\t\t(scale\n\t\t\t\t(xyz 1 1 1)\n\t\t\t)\n\t\t\t(rotate\n\t\t\t\t(xyz 0 0 0)\n\t\t\t)\n\t\t)'
EAR = MODEL.replace("(xyz 0 0 0)", "(xyz 0 -4 0)", 1)


def surgery(text):
    text = mutlib.edit_footprint(text, "J1", MODEL, EAR + "\n\t\t" + MODEL, "J1 ear")
    return text, {"refs": ["J1", "U2"]}


if __name__ == "__main__":
    sys.exit(mutlib.run("body-ear-clash", "blinky2", surgery))
