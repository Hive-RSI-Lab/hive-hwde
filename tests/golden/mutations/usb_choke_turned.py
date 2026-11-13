"""Mutant: usb-choke-turned (usbbuck4).

NETLIST fault (issue #647): the Murata DLW21HN900SQ2L common-mode choke L9
between usbbuck4's USB connector J1 and its STM32F103 is wired turned on its
footprint: J1's D+ on pad 1, U1's D+ on pad 4, J1's D- on pad 2 and U1's D-
on pad 3. Pads 1 and 2 are one winding, so D+ and D- are shorted together at
DC and the device never enumerates. ERC sees every pin connected and the
board's copper is untouched (the check reads the netlist beside it). Must be
caught by check_usb_phy (kind "usb_choke_shorts_pair", ref L9).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
from usb_choke_pullup_weak import choke


def surgery(text):
    return text, {"ref": "L9", "wiring": "J1 D+ on 1, D- on 2 (one winding)",
                  "sidecars": {"usbbuck4.net": choke(("1", "4", "2", "3"))}}


if __name__ == "__main__":
    sys.exit(mutlib.run("usb-choke-turned", "usbbuck4", surgery))
