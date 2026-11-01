"""Mutant: tp-tented (blinky2).

TEST fault: a test point added on /NRST (the reset line bring-up probes)
from a library footprint whose pad has no F.Mask opening: the pad is
covered by solder mask, so neither a flying probe nor a fixture pin can
touch it. KiCad's DRC passes it (a pad without a mask layer is legal).
Must be caught by check_testpoints (kind "tp_unusable" at the pad).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

TP = ('\t(footprint "TestPoint:TestPoint_Pad_D1.0mm"\n'
      '\t\t(layer "F.Cu")\n'
      '\t\t(uuid "5c0de470-0000-4000-8000-000000000001")\n'
      '\t\t(at 140 124)\n'
      '\t\t(property "Reference" "TP1"\n'
      '\t\t\t(at 0 -1.5 0)\n'
      '\t\t\t(layer "F.SilkS")\n'
      '\t\t\t(uuid "5c0de470-0000-4000-8000-000000000002")\n'
      '\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n'
      '\t\t)\n'
      '\t\t(property "Value" "TestPoint"\n'
      '\t\t\t(at 0 1.5 0)\n'
      '\t\t\t(layer "F.Fab")\n'
      '\t\t\t(uuid "5c0de470-0000-4000-8000-000000000003")\n'
      '\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n'
      '\t\t)\n'
      + mutlib.footprint_fields("5c0de470-0000-4000-8000-00000000001") +
      '\t\t(attr exclude_from_pos_files exclude_from_bom)\n'
      '\t\t(pad "1" smd circle\n'
      '\t\t\t(at 0 0)\n'
      '\t\t\t(size 1 1)\n'
      '\t\t\t(layers "F.Cu")\n'
      '\t\t\t(net "/NRST")\n'
      '\t\t\t(uuid "5c0de470-0000-4000-8000-000000000004")\n'
      '\t\t)\n'
      '\t)\n')


def surgery(text):
    text = mutlib.append_items(text, TP, "tented test point")
    return text, {"ref": "TP1", "pad": [140, 124]}


if __name__ == "__main__":
    sys.exit(mutlib.run("tp-tented", "blinky2", surgery))
