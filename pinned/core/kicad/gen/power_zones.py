"""core: heavy-copper fan-in pours for the amp output stage (both zones).

After routing, each TPA3221 output and speaker net gets solid-connected
pours over its routed track, sized for the continuous-sine case (3.7 A rms):
- OUTx on F.Cu, from the pins to the inductor pad;
- the top-row inductor (L1/L4) to its terminal on F.Cu;
- the bottom-row inductor (L2/L3) through a 6-via array to B.Cu and on to
  the through-hole terminal.

Writes kicad/power_zones.constraints.json, a planes-only sidecar for
planes_gen, and anneal/power_vias.ops.json, add_via ops for route_edit.
All coordinates are board-local mm (u from the left edge, v from the rear),
the same as anchors.py.
"""
import json
from pathlib import Path

B = Path(__file__).resolve().parents[2]
X0, Y0 = json.loads((B / "reports" / "board_init.json").read_text())["outline_origin"]

ZONE = {  # zone A geometry; zone B is the same shifted by DU
    "OUT1P": ("F.Cu", [(3.6, 31.0, 7.45, 36.0), (7.1, 35.6, 8.3, 38.0)]),
    "OUT1M": ("F.Cu", [(9.9, 20.6, 11.5, 35.4), (3.6, 20.6, 9.9, 24.0),
                       (10.2, 35.4, 10.9, 38.0)]),
    "OUT2P": ("F.Cu", [(11.75, 20.6, 12.75, 35.4), (12.75, 20.6, 19.5, 24.0),
                       (12.2, 35.4, 12.7, 38.0)]),
    "OUT2M": ("F.Cu", [(15.7, 31.5, 19.5, 36.0), (14.75, 35.6, 15.9, 38.0)]),
    "SPK_L-": ("F.Cu", [(3.6, 13.4, 13.3, 14.6), (10.7, 4.2, 13.3, 13.4)]),
    "SPK_R+": ("F.Cu", [(15.7, 13.4, 30.8, 14.6), (28.2, 4.2, 31.3, 13.4)]),
    "SPK_L+": ("F.Cu", [(7.2, 24.6, 9.6, 27.9)]),
    "SPK_L+/B": ("B.Cu", [(3.6, 24.4, 9.6, 28.0), (8.0, 7.0, 9.6, 28.0),
                          (8.0, 7.0, 18.2, 8.6), (15.8, 4.2, 18.2, 8.6)]),
    "SPK_R-": ("F.Cu", [(19.3, 24.6, 21.7, 27.9)]),
    "SPK_R-/B": ("B.Cu", [(19.9, 7.2, 21.6, 28.0), (19.9, 7.2, 25.7, 8.8),
                          (23.3, 4.2, 25.7, 8.8)]),
}
# PVDD fingers between the output pins, tied down to the In2 PVDD plane by
# five vias each: a finger feeds one bridge, up to 2.5 A (constraints.json)
ZONE_PVDD = [(8.0, 30.2, 9.75, 36.2), (8.42, 36.2, 10.08, 38.0),
             (13.0, 30.2, 14.6, 36.2), (12.9, 36.2, 14.55, 38.0)]
VIAS_PVDD = [(u, v) for u in (8.85, 13.8) for v in (30.9, 31.7, 32.5, 33.7, 34.8)]
# GND pins 33/34 sit between the OUT1M and OUT2P channels: stub down to a
# via under the package body
VIAS_GND = [(11.5, 39.0)]
VIAS = {"SPK_L+": [(8.1, v) for v in (25.2, 26.3, 27.4)] + [(9.1, v) for v in (25.2, 26.3, 27.4)],
        "SPK_R-": [(20.0, v) for v in (25.2, 26.3, 27.4)] + [(21.0, v) for v in (25.2, 26.3, 27.4)]}
DU = 32.9


def main():
    planes, ops = [], []
    for z, du in (("A", 0.0), ("B", DU)):
        sheet = f"/amp_{z.lower()}/{z}_"
        for key, (layer, rects) in ZONE.items():
            net = sheet + key.split("/")[0]
            for (u1, v1, u2, v2) in rects:
                planes.append({"layer": layer, "net": net, "connect": "solid",
                               "priority": 5,
                               "region": [round(X0 + u1 + du, 3), round(Y0 + v1, 3),
                                          round(X0 + u2 + du, 3), round(Y0 + v2, 3)]})
        for (u1, v1, u2, v2) in ZONE_PVDD:
            planes.append({"layer": "F.Cu", "net": "PVDD", "connect": "solid", "priority": 5,
                           "region": [round(X0 + u1 + du, 3), round(Y0 + v1, 3),
                                      round(X0 + u2 + du, 3), round(Y0 + v2, 3)]})
        for (u, v) in VIAS_PVDD:
            ops.append({"op": "add_via", "at": [round(X0 + u + du, 3), round(Y0 + v, 3)],
                        "net": "PVDD", "size": 0.6, "drill": 0.3})
        for (u, v) in VIAS_GND:
            ops.append({"op": "add_via", "at": [round(X0 + u + du, 3), round(Y0 + v, 3)],
                        "net": "GND", "size": 0.6, "drill": 0.3})
        for key, pts in VIAS.items():
            for (u, v) in pts:
                ops.append({"op": "add_via", "at": [round(X0 + u + du, 3), round(Y0 + v, 3)],
                            "net": sheet + key, "size": 0.6, "drill": 0.3})
    # 5 V buck switch node: U4 pin 6 straight across to L9, and L9 down to
    # the bootstrap cap C12
    for (u1, v1, u2, v2) in ((32.6, 49.95, 35.8, 50.7), (35.5, 51.6, 38.6, 53.6)):
        planes.append({"layer": "F.Cu", "net": "/rails/BUCK5_LX", "connect": "solid",
                       "priority": 5, "region": [round(X0 + u1, 3), round(Y0 + v1, 3),
                                                 round(X0 + u2, 3), round(Y0 + v2, 3)]})
    (B / "kicad" / "power_zones.constraints.json").write_text(
        json.dumps({"planes": planes}, indent=1) + "\n")
    (B / "anneal" / "power_vias.ops.json").write_text(
        json.dumps({"version": 1, "ops": ops}, indent=1) + "\n")
    print(len(planes), "zones,", len(ops), "vias")


if __name__ == "__main__":
    main()
