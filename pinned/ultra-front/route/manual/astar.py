"""Tiny 2-layer grid A* for one net; prints route_edit ops JSON."""
import json, sys, heapq, math
import numpy as np
from shapely.geometry import Polygon, Point, LineString, box
from shapely.ops import unary_union
from shapely import prepared
geo=json.load(open(sys.argv[1])); net=sys.argv[2]; region=[float(v) for v in sys.argv[3].split(',')]
VC=float(__import__("os").environ.get("VC","25")); W=0.15; CL=0.14; VIA=0.6; G=0.1; EDGE=0.35
def shapes(L, infl):
    s=[]
    for it in geo[L]:
        if it["net"]==net: continue
        if "poly" in it:
            for p in it["poly"]:
                if len(p)>=3: s.append(Polygon(p).buffer(infl))
        elif "seg" in it:
            x1,y1,x2,y2,r=it["seg"]; s.append(LineString([(x1,y1),(x2,y2)]).buffer(r+infl) if (x1,y1)!=(x2,y2) else Point(x1,y1).buffer(r+infl))
        else:
            x,y,r=it["circle"]; s.append(Point(x,y).buffer(r+infl))
    return prepared.prep(unary_union(s))
x0,y0,x1,y1=region
_t=geo["target"]; _s=(_t[0]["x"],_t[0]["y"])
x0=_s[0]-round((_s[0]-x0)/G)*G; y0=_s[1]-round((_s[1]-y0)/G)*G
bx=geo["bbox"]; inner=prepared.prep(box(bx[0]+EDGE+W/2,bx[1]+EDGE+W/2,bx[2]-EDGE-W/2,bx[3]-EDGE-W/2))
innerv=prepared.prep(box(bx[0]+EDGE+VIA/2,bx[1]+EDGE+VIA/2,bx[2]-EDGE-VIA/2,bx[3]-EDGE-VIA/2))
nx=int((x1-x0)/G)+1; ny=int((y1-y0)/G)+1
tr={L:shapes(L,CL+W/2) for L in "FB"}; vi={L:shapes(L,CL+VIA/2) for L in "FB"}
crt=prepared.prep(unary_union([Polygon(p).buffer(-0.3) for p in geo["crt"] if len(p)>=3]))
free=np.zeros((2,nx,ny),bool); vfree=np.zeros((nx,ny),bool)
for i in range(nx):
    for j in range(ny):
        p=Point(x0+i*G,y0+j*G)
        for k,L in enumerate("FB"): free[k,i,j]=inner.contains(p) and not tr[L].contains(p)
        vfree[i,j]=innerv.contains(p) and not vi["F"].contains(p) and not vi["B"].contains(p) and not crt.contains(p)
t=geo["target"]; s=(t[0]["x"],t[0]["y"]); e=(t[1]["x"],t[1]["y"])
si=(0,round((s[0]-x0)/G),round((s[1]-y0)/G)); ei=(0,round((e[0]-x0)/G),round((e[1]-y0)/G))
for (k,i,j) in (si,ei):  # pads are SMD top; open a tiny start area
    for di in range(-2,3):
        for dj in range(-2,3): free[0,i+di,j+dj]=True
D=[(1,0,1),(-1,0,1),(0,1,1),(0,-1,1),(1,1,1.414),(1,-1,1.414),(-1,1,1.414),(-1,-1,1.414)]
goal=set()
if len(sys.argv)>4:
    for k,L in enumerate("FB"):
        segs=[LineString([(it["seg"][0],it["seg"][1]),(it["seg"][2],it["seg"][3])]).buffer(it["seg"][4]*0.5) for it in geo[L] if it["net"]==net and "seg" in it]
        if not segs: continue
        u=prepared.prep(unary_union(segs))
        for i in range(nx):
            for j in range(ny):
                if u.contains(Point(x0+i*G,y0+j*G)): goal.add((k,i,j)); free[k,i,j]=True
goal.add(ei)
h=lambda n: 0 if goal!={ei} else math.hypot(n[1]-ei[1],n[2]-ei[2])
dist={si:0}; prev={}; pq=[(h(si),0,si,None)]
while pq:
    f,g,n,d=heapq.heappop(pq)
    if n in goal: ei=n; break
    if g>dist.get(n,1e18): continue
    k,i,j=n
    nbrs=[]
    for di,dj,c in D:
        m=(k,i+di,j+dj)
        if 0<=m[1]<nx and 0<=m[2]<ny and free[m]: nbrs.append((m,c+(0.3 if d and d!=(di,dj) else 0),(di,dj)))
    if vfree[i,j]: nbrs.append(((1-k,i,j),VC,None))
    for m,c,dd in nbrs:
        ng=g+c
        if ng<dist.get(m,1e18): dist[m]=ng; prev[m]=n; heapq.heappush(pq,(ng+h(m),ng,m,dd))
else:
    print(json.dumps({"status":"fail","visited":len(dist),"layers":[sum(1 for n in dist if n[0]==0),sum(1 for n in dist if n[0]==1)],"vias_free":int(vfree.sum()),"free":[int(free[0].sum()),int(free[1].sum())]})); sys.exit(1)
path=[ei]
while path[-1]!=si: path.append(prev[path[-1]])
path=path[::-1]
ops=[]; cur=path[0]; seg_start=path[0]; lastd=None
pt=lambda n:[round(x0+n[1]*G,4),round(y0+n[2]*G,4)]
for a,b in zip(path,path[1:]):
    if a[0]!=b[0]:
        if seg_start!=a: ops.append({"op":"add_track","start":pt(seg_start),"end":pt(a),"width":W,"layer":"F.Cu" if a[0]==0 else "B.Cu","net":net})
        ops.append({"op":"add_via","at":pt(a),"size":VIA,"drill":0.3,"net":net}); seg_start=b; lastd=None; continue
    d=(b[1]-a[1],b[2]-a[2])
    if lastd is not None and d!=lastd:
        ops.append({"op":"add_track","start":pt(seg_start),"end":pt(a),"width":W,"layer":"F.Cu" if a[0]==0 else "B.Cu","net":net}); seg_start=a
    lastd=d
ops.append({"op":"add_track","start":pt(seg_start),"end":pt(path[-1]),"width":W,"layer":"F.Cu" if path[-1][0]==0 else "B.Cu","net":net})
# snap first/last endpoints exactly onto pad centres
ops[0]["start"]=[s[0],s[1]]
if len(sys.argv)<=4: ops[-1]["end"]=[e[0],e[1]]
ops=[o for o in ops if o["op"]!="add_track" or o["start"]!=o["end"]]
print(json.dumps({"version":1,"ops":ops},indent=0))
