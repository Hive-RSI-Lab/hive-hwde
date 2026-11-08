"""Mutant: xfmr-forward (usbbuck4).

NETLIST fault (hw-lab mains-psu's defect, cold review 1 finding 1, issue
#644): a Wurth 750314727 flyback transformer T9 is added to usbbuck4 wired
the way that board had it: the input (VBUS) on dotted primary pin 3 and the
switch Q9's drain on pin 1, with the rectifier D9's anode on dotted
secondary pin 8 (10 and 11 bridged, 13 to GND). That is a forward
converter: D9 conducts in the on-time. ERC sees every pin connected and the
board's copper is untouched (the check reads the netlist beside it). Must be
caught by check_transformer (kind "xfmr_phase", ref T9).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

NETLIST = mutlib.GOLDEN / "netlist" / "usbbuck4.net"


def _comp(ref, value):
    return (f'\t\t(comp\n\t\t\t(ref "{ref}")\n\t\t\t(value "{value}")\n'
            '\t\t)\n')


def _node(ref, pin, fn=""):
    f = f'\t\t\t\t(pinfunction "{fn}")\n' if fn else ""
    return (f'\t\t\t(node\n\t\t\t\t(ref "{ref}")\n\t\t\t\t(pin "{pin}")\n{f}'
            '\t\t\t\t(pintype "passive")\n\t\t\t)\n')


def _net(code, name, nodes):
    return (f'\t\t(net\n\t\t\t(code "{code}")\n\t\t\t(name "{name}")\n'
            '\t\t\t(class "Default")\n' + "".join(_node(*n) for n in nodes)
            + '\t\t)\n')


def surgery(text):
    net = NETLIST.read_text(encoding="utf-8")
    comps = (_comp("T9", "750314727") + _comp("Q9", "IPD60R1K4C6")
             + _comp("D9", "STPS5H100"))
    net = mutlib.replace_once(net, "\t(components\n",
                              "\t(components\n" + comps, "components")
    nets = (_net("901", "/XF_DRAIN", [("T9", "1"), ("Q9", "2", "D")])
            + _net("902", "/XF_S", [("T9", "8"), ("D9", "1", "A")])
            + _net("903", "/XF_BR", [("T9", "10"), ("T9", "11")])
            + _net("904", "/XF_OUT", [("D9", "2", "K")]))
    net = mutlib.replace_once(net, "\t(nets\n", "\t(nets\n" + nets, "nets")
    for name, add in (("GND", [("T9", "13"), ("Q9", "3", "S")]),
                      ("VBUS", [("T9", "3")])):
        anchor = f'\t\t\t(name "{name}")\n\t\t\t(class "Default")\n'
        net = mutlib.replace_once(net, anchor, anchor + "".join(
            _node(*n) for n in add), f"{name} net")
    return text, {"ref": "T9", "wiring": "bulk on 3, drain on 1, anode on 8",
                  "sidecars": {"usbbuck4.net": net}}


if __name__ == "__main__":
    sys.exit(mutlib.run("xfmr-forward", "usbbuck4", surgery))
