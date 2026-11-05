"""Mutant: stackup-thickness-general (blinky2).

Stackup fault: sane copper but a 4.5 mm core, so the layers sum to 4.57 mm
against the board's general thickness (#745; rf-kit-ro4350b sums 4.617
against 1.6). The golden has no block, so the mutation adds one. KiCad's DRC
doesn't read the stack. Must be caught by check_stackup (kind
"thickness_stack_vs_general").
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
\t\t\t\t(thickness 4.5)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "B.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(copper_finish "HASL")
\t\t\t(dielectric_constraints no)
\t\t)
'''


def surgery(text):
    if "(stackup" in text:
        raise ValueError("golden already has a stackup block")
    return mutlib.replace_once(text, "\t(setup\n", BLOCK, "setup"), \
        {"core_mm": 4.5}


if __name__ == "__main__":
    sys.exit(mutlib.run("stackup-thickness-general", "blinky2", surgery))
