"""Mutant: usb-dp-pullup-weak (usbbuck4).

NETLIST fault (the Blue Pill clone's defect, reference/interfaces/usb-fs.json
and AN4879 Rev 5 Table 4): usbbuck4's STM32F103 has no D+ pull-up of its own,
and R3, its external 1.5 k from /USB_DP to +3V3, becomes 10 k. The host may
never see the device. ERC and DRC see a resistor to a rail. The board's copper
is untouched (the check reads the netlist beside it). Must be caught by
check_usb_phy (kind "usb_dp_pullup_value", ref R3).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

NETLIST = mutlib.GOLDEN / "netlist" / "usbbuck4.net"


def surgery(text):
    net = mutlib.replace_once(
        NETLIST.read_text(encoding="utf-8"),
        '(ref "R3")\n\t\t\t(value "1k5")', '(ref "R3")\n\t\t\t(value "10k")',
        "R3 value")
    return text, {"ref": "R3", "pullup": "R3 1k5 -> 10k",
                  "sidecars": {"usbbuck4.net": net}}


if __name__ == "__main__":
    sys.exit(mutlib.run("usb-dp-pullup-weak", "usbbuck4", surgery))
