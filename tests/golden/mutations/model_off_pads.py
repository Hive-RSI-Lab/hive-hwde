"""Mutant: model-off-pads (blinky2).

LIBRARY fault (the hit: EasyEDA's HRO TYPE-C-31-M-12 USB-C, lite-main J1,
whose model lib_pull fetches 2.2 mm off its footprint). J2's 3D model is
offset half a pitch, 1.27 mm, along the header, so every pin of the model
stands between two holes: the render, the STEP export and an enclosure
fitted to that STEP show the header where it isn't. Copper is untouched.
Must be caught by check_model_fit (kind "model_offset", ref J2, at J2's
origin).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

MODEL = ('(model "${KICAD10_3DMODEL_DIR}/Connector_PinHeader_2.54mm.3dshapes/'
         'PinHeader_1x04_P2.54mm_Vertical.step"\n\t\t\t(offset\n'
         '\t\t\t\t(xyz 0 0 0)')
OLD, NEW = MODEL, MODEL.replace("(xyz 0 0 0)", "(xyz 0 1.27 0)")


def surgery(text):
    text = mutlib.edit_footprint(text, "J2", OLD, NEW, "J2 model offset")
    return text, {"ref": "J2", "model_offset": [0, 1.27, 0]}


if __name__ == "__main__":
    sys.exit(mutlib.run("model-off-pads", "blinky2", surgery))
