"""Remove every track/via of the given nets (rip-up for a reroute).
    KiCad's python: kicad/gen/ripnet.py PCB NET [NET ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
nets = set(sys.argv[2:])
gone = [t for t in b.GetTracks() if t.GetNetname() in nets]
for t in gone:
    b.Remove(t)
b.Save(sys.argv[1])
print("removed", len(gone))
