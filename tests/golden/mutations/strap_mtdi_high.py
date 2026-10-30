"""Mutant: strap-mtdi-high (usbbuck4).

NETLIST fault (the hit: hifi-receiver-2's core, review-2 DIGITAL-1/2, where
an ESP32 strap read a level nothing on the board meant it to). usbbuck4's
own netlist gains an ESP32-WROVER-E, U9, on +3V3 and GND, and a 10 k R99
from its MTDI/IO12 to +3V3. MTDI high at power-on selects a 1.8 V flash
supply, which the WROVER's 3.3 V flash won't run from. ERC and DRC see a
resistor to a rail. The board's copper is untouched (the check reads the
netlist beside it). Must be caught by check_straps (kind "strap_level",
ref U9).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

NETLIST = mutlib.GOLDEN / "netlist" / "usbbuck4.net"

COMPS = (
    '\t\t(comp (ref "U9") (value "ESP32-WROVER-E-N8R8"))\n'
    '\t\t(comp (ref "R99") (value "10k"))\n'
)


def _node(ref, pin, fn):
    return (f'(node (ref "{ref}") (pin "{pin}") (pinfunction "{fn}") '
            '(pintype "passive"))')


def _onto(text, net, nodes):
    """nodes added to net `net` (the (nets ...) section only: a component's
    fields carry names too)."""
    comps, sep, nets = text.partition("\t(nets\n")
    head = f'(name "{net}")'
    if not sep or nets.count(head) != 1:
        raise mutlib.SurgeryError(f"net {net} not found once in {NETLIST.name}")
    return comps + sep + nets.replace(head, head + " " + " ".join(nodes), 1)


def netlist():
    text = NETLIST.read_text(encoding="utf-8")
    text = mutlib.replace_once(text, "\t(components\n",
                               "\t(components\n" + COMPS, "components")
    text = _onto(text, "+3V3", [_node("U9", "2", "3V3_2"), _node("R99", "2", "")])
    text = _onto(text, "GND", [_node("U9", "1", "GND_1")])
    text = mutlib.replace_once(
        text, "\t(nets\n",
        '\t(nets\n\t\t(net (code "900") (name "/ESP_MTDI") '
        + _node("U9", "14", "IO12_14") + " " + _node("R99", "1", "") + ")\n",
        "nets")
    return text


def surgery(text):
    return text, {"ref": "U9", "strap": "MTDI/IO12 10k to +3V3",
                  "sidecars": {"usbbuck4.net": netlist()}}


if __name__ == "__main__":
    sys.exit(mutlib.run("strap-mtdi-high", "usbbuck4", surgery))
