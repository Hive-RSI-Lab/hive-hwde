"""Mutant: stackup-copper-core-thick (blinky2).

Stackup fault: a stackup block whose B.Cu carries the core's 1.524 mm, as
rf-kit-ro4350b's generator wrote it (#728). The golden has no block, so the
mutation adds one. KiCad's DRC doesn't read the stack. Must be caught by
check_stackup (kind "copper_thickness_range").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

BLOCK = '''\t(setup
\t\t(stackup
\t\t\t(layer "F.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "dielectric 1"
\t\t\t\t(type "core")
\t\t\t\t(thickness 1.524)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "B.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 1.524)
\t\t\t)
\t\t\t(copper_finish "HASL")
\t\t\t(dielectric_constraints no)
\t\t)
'''


def surgery(text):
    if "(stackup" in text:
        raise ValueError("golden already has a stackup block")
    return mutlib.replace_once(text, "\t(setup\n", BLOCK, "setup"), \
        {"b_cu_mm": 1.524}


if __name__ == "__main__":
    sys.exit(mutlib.run("stackup-copper-core-thick", "blinky2", surgery))
