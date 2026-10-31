import sys, json, math, numpy as np, skrf
from skrf.media import MLine
fr = skrf.Frequency(1, 1, 1, unit="MHz")
for line in sys.stdin:
    w, h, t, er = map(float, line.split())
    m = MLine(frequency=fr, w=w*1e-3, h=h*1e-3, t=t*1e-3, ep_r=er, model="hammerstadjensen", disp="none")
    print(json.dumps({"w": w, "h": h, "t": t, "er": er, "hj": round(float(np.real(m.Z0[0])), 3)}))
