"""Mutant: opamp-cable-no-buildout (usbbuck4).

NETLIST fault (reference/ingest/analog-audio.md gap 2, rules A2/A6): a
line-out jack driven straight from an NE5532. usbbuck4's own netlist gains
U98, an NE5532 wired as a follower (OUT A on its - input), its output through
a 10 uF coupling cap C98 to J98's tip with no build-out resistor. 10 m of
cable on the jack is ~1 nF, a pole near the NE5532's crossover: about 31 deg
of phase margin, a ringing or oscillating line driver. ERC and DRC see a
cap and a connector. The board's copper is untouched (the check reads the
netlist beside it). Must be caught by check_cap_load (kind "cap_load_pm",
ref U98).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

NETLIST = mutlib.GOLDEN / "netlist" / "usbbuck4.net"

COMPS = (
    '\t\t(comp (ref "U98") (value "NE5532"))\n'
    '\t\t(comp (ref "C98") (value "10uF"))\n'
    '\t\t(comp (ref "J98") (value "AudioJack2"))\n'
)


def _node(ref, pin, fn):
    return (f'(node (ref "{ref}") (pin "{pin}") (pinfunction "{fn}") '
            '(pintype "passive"))')


def _onto(text, net, nodes):
    """nodes added to net `net` (the (nets ...) section only)."""
    comps, sep, nets = text.partition("\t(nets\n")
    head = f'(name "{net}")'
    if not sep or nets.count(head) != 1:
        raise mutlib.SurgeryError(f"net {net} not found once in {NETLIST.name}")
    return comps + sep + nets.replace(head, head + " " + " ".join(nodes), 1)


def _net(code, name, nodes):
    return (f'\t\t(net (code "{code}") (name "{name}") '
            + " ".join(nodes) + ")\n")


def netlist():
    text = NETLIST.read_text(encoding="utf-8")
    text = mutlib.replace_once(text, "\t(components\n",
                               "\t(components\n" + COMPS, "components")
    text = _onto(text, "+3V3", [_node("U98", "8", "V+")])
    text = _onto(text, "GND", [_node("U98", "4", "V-"),
                               _node("J98", "2", "S")])
    nets = (_net(910, "/LINE_A", [_node("U98", "1", "~"),
                                  _node("U98", "2", "-"),
                                  _node("C98", "1", "")])
            + _net(911, "/LINE_IN", [_node("U98", "3", "+")])
            + _net(912, "/LINE_JACK", [_node("C98", "2", ""),
                                       _node("J98", "1", "T")]))
    return mutlib.replace_once(text, "\t(nets\n", "\t(nets\n" + nets, "nets")


def surgery(text):
    return text, {"ref": "U98", "load": "J98 cable via C98, no build-out",
                  "sidecars": {"usbbuck4.net": netlist()}}


if __name__ == "__main__":
    sys.exit(mutlib.run("opamp-cable-no-buildout", "usbbuck4", surgery))
