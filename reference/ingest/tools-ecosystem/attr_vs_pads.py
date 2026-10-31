"""Footprints whose (attr) disagrees with their pads: an `attr through_hole`
footprint with no thru_hole pad, or an `attr smd` one with thru_hole pads.
KLC F6.1/F7.1 in spirit, run on a placed board. Usage: attr_vs_pads.py X.kicad_pcb"""
import re, sys
s = open(sys.argv[1], encoding="utf-8").read()
starts = [m.start() for m in re.finditer(r'\(footprint "', s)] + [len(s)]
for a, b in zip(starts[:-1], starts[1:]):
    blk = s[a:b]
    name = re.match(r'\(footprint "([^"]+)"', blk).group(1)
    ref = re.search(r'\(property "Reference" "([^"]+)"', blk)
    attr = re.search(r'\(attr ([^)]*)\)', blk)
    attrs = attr.group(1).split() if attr else []
    kinds = set(re.findall(r'\(pad "[^"]*" (\w+)', blk))
    if "through_hole" in attrs and "thru_hole" not in kinds:
        print(f"{ref.group(1) if ref else '?'}\t{name}\tattr through_hole, pads {sorted(kinds)}")
    elif "smd" in attrs and "thru_hole" in kinds:
        print(f"{ref.group(1) if ref else '?'}\t{name}\tattr smd, has thru_hole pads")
