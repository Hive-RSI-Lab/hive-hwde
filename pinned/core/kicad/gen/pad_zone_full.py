"""core: given pads join their zones solidly (no thermal spokes), for the
USB-C's power pins carrying 5 A into the In2 plane.
  KiCad's python: kicad/gen/pad_zone_full.py PCB REF PAD [PAD ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
fp = b.FindFootprintByReference(sys.argv[2])
for p in fp.Pads():
    if p.GetNumber() in sys.argv[3:]:
        p.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
print(sys.argv[2], "solid:", " ".join(sys.argv[3:]))
