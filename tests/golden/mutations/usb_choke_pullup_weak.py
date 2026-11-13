"""Mutant: usb-choke-pullup-weak (usbbuck4).

NETLIST fault (issue #647): a Murata DLW21HN900SQ2L common-mode choke L9 is
added between usbbuck4's USB connector J1 and its STM32F103 U1, wired as
hw-lab emc-lab's L501 (D+ through 1-2, D- through 4-3), and the external
D+ pull-up R3 on the MCU side becomes 10 k, not 1.5 k. Before #647 the check
took the choke for the PHY and only warned it did not know it, so the weak
pull-up behind a choke went unjudged. ERC sees every pin connected and the
board's copper is untouched (the check reads the netlist beside it). Must be
caught by check_usb_phy (kind "usb_dp_pullup_value", ref R3).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

NETLIST = mutlib.GOLDEN / "netlist" / "usbbuck4.net"


def _node(ref, pin, fn=""):
    f = f'\t\t\t\t(pinfunction "{fn}")\n' if fn else ""
    return (f'\t\t\t(node\n\t\t\t\t(ref "{ref}")\n\t\t\t\t(pin "{pin}")\n{f}'
            '\t\t\t\t(pintype "passive")\n\t\t\t)\n')


def _j1(pin, fn):
    return (f'\t\t\t(node\n\t\t\t\t(ref "J1")\n\t\t\t\t(pin "{pin}")\n'
            f'\t\t\t\t(pinfunction "{fn}")\n'
            '\t\t\t\t(pintype "bidirectional")\n\t\t\t)\n')


def choke(pins, r3="1k5"):
    """usbbuck4's netlist with L9 between J1 and U1. pins: L9's pad on
    {J1 D+, U1 D+, J1 D-, U1 D-}. /USB_DP and /USB_DM keep the MCU side
    (U1, R3); J1's pins move to new nets /USB_DP_J and /USB_DM_J."""
    a, b, c, d = pins
    net = NETLIST.read_text(encoding="utf-8")
    net = mutlib.replace_once(
        net, "\t(components\n", '\t(components\n\t\t(comp\n\t\t\t(ref "L9")\n'
        '\t\t\t(value "DLW21HN900SQ2L")\n\t\t)\n', "components")
    net = mutlib.replace_once(net, _j1("3", "D+_3"), _node("L9", b), "J1.3")
    net = mutlib.replace_once(net, _j1("2", "D-_2"), _node("L9", d), "J1.2")
    new = "".join(
        f'\t\t(net\n\t\t\t(code "{code}")\n\t\t\t(name "{name}")\n'
        f'\t\t\t(class "Default")\n{_j1(*j)}{_node("L9", p)}\t\t)\n'
        for code, name, j, p in (("911", "/USB_DP_J", ("3", "D+_3"), a),
                                 ("912", "/USB_DM_J", ("2", "D-_2"), c)))
    net = mutlib.replace_once(net, "\t(nets\n", "\t(nets\n" + new, "nets")
    if r3 != "1k5":
        net = mutlib.replace_once(
            net, '(ref "R3")\n\t\t\t(value "1k5")',
            f'(ref "R3")\n\t\t\t(value "{r3}")', "R3 value")
    return net


def surgery(text):
    return text, {"ref": "R3", "pullup": "R3 1k5 -> 10k behind choke L9",
                  "sidecars": {"usbbuck4.net": choke(("1", "2", "4", "3"),
                                                     r3="10k")}}


if __name__ == "__main__":
    sys.exit(mutlib.run("usb-choke-pullup-weak", "usbbuck4", surgery))
