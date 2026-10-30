import pcbnew, sys, json
b=pcbnew.LoadBoard(sys.argv[1]); net=sys.argv[2]; want=sys.argv[4].split(",") if len(sys.argv)>4 else None
mm=pcbnew.ToMM
out={"F":[], "B":[], "target":[]}
def poly(shape):
    res=[]
    for i in range(shape.OutlineCount()):
        o=shape.Outline(i); res.append([(mm(o.CPoint(j).x),mm(o.CPoint(j).y)) for j in range(o.PointCount())])
    return res
for fp in b.GetFootprints():
    for p in fp.Pads():
        for L,lid in (("F",pcbnew.F_Cu),("B",pcbnew.B_Cu)):
            if not p.IsOnLayer(lid): continue
            sh=p.GetEffectivePolygon(lid, pcbnew.ERROR_INSIDE)
            item={"net":p.GetNetname(),"poly":poly(sh),"ref":fp.GetReference(),"pad":p.GetNumber()}
            out[L].append(item)
        if p.GetNetname()==net and (want is None or f"{fp.GetReference()}.{p.GetNumber()}" in want): out["target"].append({"ref":fp.GetReference(),"pad":p.GetNumber(),"x":mm(p.GetPosition().x),"y":mm(p.GetPosition().y)})
        if p.HasHole():
            pass
for t in b.GetTracks():
    if t.GetClass()=="PCB_VIA":
        for L in ("F","B"): out[L].append({"net":t.GetNetname(),"circle":[mm(t.GetPosition().x),mm(t.GetPosition().y),mm(t.GetWidth(pcbnew.F_Cu))/2]})
    else:
        L="F" if t.GetLayer()==pcbnew.F_Cu else "B"
        out[L].append({"net":t.GetNetname(),"seg":[mm(t.GetStart().x),mm(t.GetStart().y),mm(t.GetEnd().x),mm(t.GetEnd().y),mm(t.GetWidth())/2]})
# holes (NPTH/PTH) as obstacles both layers
for fp in b.GetFootprints():
    for p in fp.Pads():
        if p.HasHole():
            r=max(mm(p.GetDrillSize().x),mm(p.GetDrillSize().y))/2
            for L in ("F","B"): out[L].append({"net":p.GetNetname(),"circle":[mm(p.GetPosition().x),mm(p.GetPosition().y),r]})
out["crt"]=[]
for fp in b.GetFootprints():
    c=fp.GetCourtyard(pcbnew.F_CrtYd)
    if c.OutlineCount(): out["crt"]+= [p for p in poly(c)]
bb=b.GetBoardEdgesBoundingBox(); out["bbox"]=[mm(bb.GetX()),mm(bb.GetY()),mm(bb.GetRight()),mm(bb.GetBottom())]
if want: out["target"].sort(key=lambda d: want.index(f"{d['ref']}.{d['pad']}"))
json.dump(out,open(sys.argv[3],'w'))
