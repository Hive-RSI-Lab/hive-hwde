"""core: rename nets on the board (pads, tracks, vias, zones), e.g. when a
local net becomes global because another sheet now uses it.
  KiCad's python: kicad/gen/rename_net.py PCB OLD=NEW [OLD=NEW ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
for pair in sys.argv[2:]:
    old, new = pair.split("=", 1)
    no = b.FindNet(old)
    if no is None:
        print("no net", old)
        continue
    nn = b.FindNet(new)
    if nn is None:
        nn = pcbnew.NETINFO_ITEM(b, new)
        b.Add(nn)
    n = 0
    items = [p for fp in b.GetFootprints() for p in fp.Pads()] + list(b.GetTracks()) + list(b.Zones())
    for it in items:
        if it.GetNetname() == old:
            it.SetNet(nn)
            n += 1
    print(old, "->", new, n, "items")
b.Save(sys.argv[1])
