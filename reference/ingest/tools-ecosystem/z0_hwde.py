import sys, json, math
sys.path[:0] = [sys.argv[1] + "/scripts", sys.argv[1] + "/scripts/lib"]
import impedance
for line in sys.stdin:
    w, h, t, er = map(float, line.split())
    z = impedance.field_microstrip(w, h, t, er, None, None)["z0"]
    zm = impedance.field_microstrip(w, h, t, er, None, impedance.JLC_MASK)["z0"]
    ipc = 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))
    print(json.dumps({"w": w, "h": h, "t": t, "er": er, "hwde": round(z, 3), "hwde_masked": round(zm, 3), "ipc2141": round(ipc, 3)}), flush=True)
