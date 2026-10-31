"""Mutant: reset-gate-floating (usbbuck4).

NETLIST fault (FW-08: a GPIO's default state drives a FET at reset;
docs/ingest/test-fw-mech.md check_reset_drive's board case). usbbuck4's
own netlist gains an ESP32-S3-WROOM-1, U9, on +3V3 and GND, whose GPIO5
drives a low-side 2N7002 heater switch, Q99, through a 100 R gate
resistor R99 with no pull-down. GPIO5 is an input from reset until
firmware drives it, so the gate floats through the ROM boot and the
bootloader. ERC sees a resistor between two pins. The board's copper is
untouched (the check reads the netlist beside it). Must be caught by
check_reset_drive (kind "gate_floating", ref Q99).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

NETLIST = mutlib.GOLDEN / "netlist" / "usbbuck4.net"

COMPS = (
    '\t\t(comp (ref "U9") (value "ESP32-S3-WROOM-1-N8"))\n'
    '\t\t(comp (ref "R99") (value "100R"))\n'
    '\t\t(comp (ref "Q99") (value "2N7002"))\n'
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
    text = _onto(text, "+3V3", [_node("U9", "2", "3V3_2")])
    text = _onto(text, "GND", [_node("U9", "1", "GND_1"),
                               _node("Q99", "2", "S")])
    text = mutlib.replace_once(
        text, "\t(nets\n",
        '\t(nets\n\t\t(net (code "900") (name "/HEATER_IO") '
        + _node("U9", "5", "IO5_5") + " " + _node("R99", "1", "") + ")\n"
        '\t\t(net (code "901") (name "/HEATER_GATE") '
        + _node("R99", "2", "") + " " + _node("Q99", "1", "G") + ")\n"
        '\t\t(net (code "902") (name "/HEATER_LS") '
        + _node("Q99", "3", "D") + ")\n",
        "nets")
    return text


def surgery(text):
    return text, {"ref": "Q99", "gate": "ESP32-S3 GPIO5 via 100R, no pull-down",
                  "sidecars": {"usbbuck4.net": netlist()}}


if __name__ == "__main__":
    sys.exit(mutlib.run("reset-gate-floating", "usbbuck4", surgery))
