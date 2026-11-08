# stitch_necks.py BASE_PCB CONSTRAINTS HWDE_SCRIPTS NEW_VIAS OUT: for stitch vias planned in the 5 A In2.Cu pours (VBUS, VBUS_IN, PVDD), list the ones to drop: a via stays only if the pour still carries its required width (docs/boards/core-stitch.md)
import json,sys,math
from pathlib import Path
sys.path[:0]=[sys.argv[3]+'/lib',sys.argv[3]]
import geom,check_current as cc
from shapely.geometry import Point
bg=geom.load_board(Path(sys.argv[1])); c=json.load(open(sys.argv[2])); new=json.load(open(sys.argv[4]))
R=0.5; L='In2.Cu'; cu=bg.stackup.copper_thickness; drop=[]; res={}
for e in c['power']:
    net=e['net']
    if net not in ('VBUS','VBUS_IN','PVDD'): continue
    req=cc.required_width_mm(5.0,10.0,cu[L])
    others={l:(bg.zone_fill(net,l),cc.required_width_mm(5.0,10.0,cu[l])) for l in bg.layers_with_zone(net) if l!=L}
    fill=bg.zone_fill(net,L); print(net,'base',cc.pour_neck(bg,net,L,fill,req,others),flush=True)
    area=fill.buffer(0.35); cand=sorted((v for v in new if area.contains(Point(v[:2]))),key=lambda v:(v[1],v[0]))
    kept=[]
    for v in cand:
        trial=fill.difference(Point(v[:2]).buffer(R))
        if cc.pour_neck(bg,net,L,trial,req,others) is None: fill=trial; kept.append(v)
        else: drop.append(v)
    res[net]=(len(cand),len(kept)); print(net,res[net],flush=True)
print(res,'drop',len(drop)); json.dump(drop,open(sys.argv[5],'w'))
