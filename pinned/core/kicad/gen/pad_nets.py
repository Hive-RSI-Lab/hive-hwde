"""Set the nets of unrouted pads from the netlist (parity after a part swap
renames its no-connect pins; board_update skips unconnected-* nets).
    KiCad's python: kicad/gen/pad_nets.py kicad/lite-main.kicad_pcb kicad/lite-main.net REF"""
import re
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
net = open(sys.argv[2]).read()
ref = sys.argv[3]
fp = b.FindFootprintByReference(ref)
for m in re.finditer(r'\(net\s+\(code "?\d+"?\)\s+\(name "([^"]+)"\)(.*?)(?=\n\t\t\(net\s|\Z)', net, re.S):
    name = m.group(1)
    for nm in re.finditer(r'\(ref "%s"\)\s+\(pin "([^"]+)"\)' % re.escape(ref), m.group(2)):
        p = fp.FindPadByNumber(nm.group(1))
        if p and p.GetNetname() != name:
            ni = b.FindNet(name)
            if ni is None:
                ni = pcbnew.NETINFO_ITEM(b, name)
                b.Add(ni)
            print(ref, nm.group(1), p.GetNetname(), "->", name)
            p.SetNet(ni)
b.Save(sys.argv[1])
