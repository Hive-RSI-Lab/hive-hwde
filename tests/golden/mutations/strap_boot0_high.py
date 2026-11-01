"""Mutant: strap-boot0-high (usbbuck4).

NETLIST fault (#602). usbbuck4's STM32F103 has BOOT0 on 10 k to ground; a
1 k R99 from /BOOT0 to +3V3 beats it, so the chip boots its system-memory
bootloader instead of the flash on every reset. ERC and DRC see a resistor
to a rail. The board's copper is untouched (the check reads the netlist
beside it). Must be caught by check_straps (kind "strap_level", ref U1).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

NETLIST = mutlib.GOLDEN / "netlist" / "usbbuck4.net"


def _node(ref, pin):
    return (f'(node (ref "{ref}") (pin "{pin}") (pinfunction "") '
            '(pintype "passive"))')


def _onto(text, net, node):
    comps, sep, nets = text.partition("\t(nets\n")
    head = f'(name "{net}")'
    if not sep or nets.count(head) != 1:
        raise mutlib.SurgeryError(f"net {net} not found once in {NETLIST.name}")
    return comps + sep + nets.replace(head, head + " " + node, 1)


def netlist():
    text = NETLIST.read_text(encoding="utf-8")
    text = mutlib.replace_once(
        text, "\t(components\n",
        '\t(components\n\t\t(comp (ref "R99") (value "1k"))\n', "components")
    text = _onto(text, "/BOOT0", _node("R99", "1"))
    return _onto(text, "+3V3", _node("R99", "2"))


def surgery(text):
    return text, {"ref": "U1", "strap": "BOOT0 1 k to +3V3",
                  "sidecars": {"usbbuck4.net": netlist()}}


if __name__ == "__main__":
    sys.exit(mutlib.run("strap-boot0-high", "usbbuck4", surgery))
