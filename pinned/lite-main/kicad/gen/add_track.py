"""Add F.Cu (or given layer) track polylines of a net.
    KiCad's python: kicad/gen/add_track.py PCB NET WIDTH LAYER x1,y1 x2,y2 [...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
net, w, layer = sys.argv[2], pcbnew.FromMM(float(sys.argv[3])), b.GetLayerID(sys.argv[4])
pts = [tuple(map(float, s.split(","))) for s in sys.argv[5:]]
ni = b.FindNet(net)
for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
    t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x2), pcbnew.FromMM(y2)))
    t.SetWidth(w)
    t.SetLayer(layer)
    t.SetNet(ni)
    b.Add(t)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[1])
print(net, len(pts) - 1, "segments")
