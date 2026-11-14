"""Mutant: solder-wire-hole-loose (blinky2).

The hit (oasis v0.2 errata, issue #881): the power input J1 was a
Connector_Wire:SolderWire-2.5sqmm footprint, 3.0 mm holes, fed through a
pour's four 0.5 mm thermal spokes, so the wire that copper carries left
the joint loose. Here J1's 1x02 header becomes a SolderWire footprint with
2.0 mm holes, and its GND pad joins the pours through 0.2 mm thermal
spokes instead of solid, so the copper feeding it sizes a 0.75 mm^2 wire
(1.25 mm conductor) that leaves 0.75 mm in the holes. Pad positions and
nets are untouched. Must be caught by check_connector_fit (kind "tht_hole_loose",
ref J1).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    s, e = mutlib.footprint_block(text, "J1")
    block = mutlib.replace_once(
        text[s:e],
        '(footprint "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"',
        '(footprint "Connector_Wire:SolderWire-1.5sqmm_1x02_P2.54mm_D2mm"',
        "J1 footprint")
    old = "(size 1.7 1.7)\n\t\t\t(drill 1)"
    if block.count(old) != 2:
        raise ValueError(f"J1 pad drill: expected 2 pads, found {block.count(old)}")
    block = block.replace(old, "(size 2.4 2.4)\n\t\t\t(drill 2)")
    # GND pad 2 joins the pours through thin thermal spokes, as oasis's J1
    # did, instead of solid (a pad tied solid has an unbounded feed)
    block = mutlib.replace_once(
        block, '(net "GND")',
        '(net "GND")\n\t\t\t(zone_connect 1)\n\t\t\t(thermal_bridge_width 0.2)',
        "J1 pad 2 thermal")
    return text[:s] + block + text[e:], {"ref": "J1"}


if __name__ == "__main__":
    sys.exit(mutlib.run("solder-wire-hole-loose", "blinky2", surgery))
