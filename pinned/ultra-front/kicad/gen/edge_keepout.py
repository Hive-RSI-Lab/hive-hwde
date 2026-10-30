import pcbnew, sys
b=pcbnew.LoadBoard(sys.argv[1]); w=float(sys.argv[2])
bb=b.GetBoardEdgesBoundingBox(); mm=pcbnew.ToMM; F=pcbnew.FromMM
x0,y0,x1,y1=mm(bb.GetX())+0.05,mm(bb.GetY())+0.05,mm(bb.GetRight())-0.05,mm(bb.GetBottom())-0.05
rects=[(x0,y0,x1,y0+w),(x0,y1-w,x1,y1),(x0,y0,x0+w,y1),(x1-w,y0,x1,y1)]
ls=pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); ls.AddLayer(pcbnew.B_Cu)
for (a,c,d,e) in rects:
    z=pcbnew.ZONE(b); z.SetIsRuleArea(True); z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True)
    z.SetDoNotAllowZoneFills(False); z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False)
    z.SetLayerSet(ls); z.SetZoneName("edge-keepout")
    o=z.Outline(); o.NewOutline()
    for (px,py) in ((a,c),(d,c),(d,e),(a,e)): o.Append(F(px),F(py))
    b.Add(z)
b.Save(sys.argv[1]); print("added",len(rects))
