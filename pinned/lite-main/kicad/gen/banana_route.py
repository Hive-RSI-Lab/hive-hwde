"""Speaker-output copper for the four banana sockets J4-J7 (task 261030-140102):
L pad 2 -> output-filter cap -> socket, R66/R68 taps, the L pad 1 -> U8 links
after the L row moved 2.2 mm down, and PVDD/GND vias round them, as one
route_edit op list in board coordinates (u right, v down from the rear edge).

    python3 kicad/gen/banana_route.py > route/banana-route.json
"""
import json
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
X0, Y0 = json.loads((WS / "reports" / "board-init.json").read_text())["outline_origin"]
SPK, OUT, SIG = 1.25, 0.8, 0.25
ops = []


def k(p):
    return [round(X0 + p[0], 3), round(Y0 + p[1], 3)]


def path(net, pts, w, layer="F.Cu"):
    ops.append({"op": "add_path", "points": [k(p) for p in pts], "width": w, "layer": layer, "net": net})


def via(net, p, size=0.8, drill=0.4):
    ops.append({"op": "add_via", "at": k(p), "size": size, "drill": drill, "net": net})


# what the moves left behind: the old speaker/OUT copper and the PVDD vias the
# L row's pads now sit on or crowd
ops.append({"op": "rip", "net": ["/SPK_L_P", "/SPK_L_N", "/SPK_R_P", "/SPK_R_N"]})
for p in ((70.5, 34.0), (87.5, 34.0), (89.0, 34.0), (69.49, 18.81)):
    ops.append({"op": "rip", "at": k(p), "radius": 0.05, "kind": "via"})
ops.append({"op": "rip", "net": "/OUT_R_N", "at": k((86.8, 36.2)), "radius": 0.05, "kind": "track"})
ops.append({"op": "rip", "at": k((68.79, 18.81)), "radius": 0.05, "kind": "track"})   # old C68 GND stub

# socket pad 1 (back, v 17.85) to pad 2 (nose, v 2.15): one contact, both pins carry
J = {"J4": 77.4, "J5": 88.4, "J6": 99.4, "J7": 110.4}
path("/SPK_L_P", [(J["J4"], 17.85), (J["J4"], 2.15)], SPK)
path("/SPK_L_N", [(J["J5"], 17.85), (J["J5"], 2.15)], SPK, "B.Cu")
path("/SPK_R_P", [(J["J6"], 17.85), (J["J6"], 2.15)], SPK)
path("/SPK_R_N", [(J["J7"], 17.85), (J["J7"], 2.15)], SPK)

# L+ : L1 -> C68 -> J4 on F.Cu, R66 (TAP_L) off C68
path("/SPK_L_P", [(67.0, 25.3), (65.525, 23.825), (65.525, 22.55)], SPK)
# (over the PVDD strip's F.Cu, which B.Cu carries on, as before; clear of its 1.5 mm stub at u 71.15)
path("/SPK_L_P", [(65.525, 22.55), (65.525, 20.5), (69.125, 16.9), (76.45, 16.9), (J["J4"], 17.85)], SPK)
path("/SPK_L_P", [(61.81, 22.8), (65.525, 22.8)], SPK)
# L- : L2 -> C69, seven vias (3.5 A, one per 0.5 A), B.Cu under J4 to J5
VL = [round(73.0 + 1.3 * i, 3) for i in range(7)]
path("/SPK_L_N", [(75.5, 25.3), (74.025, 23.825), (74.025, 22.55), (74.025, 20.6)], SPK)
path("/SPK_L_N", [(u, 20.6) for u in VL], SPK)      # a vertex at every via: each one carries
for u in VL:
    via("/SPK_L_N", (u, 20.6))
path("/SPK_L_N", [(u, 20.6) for u in VL] + [(85.65, 20.6), (J["J5"], 17.85)], SPK, "B.Cu")
# R+ : L3 -> C70 -> up between J4 and J5, over J5 to J6; R68 (TAP_R) off C70
path("/SPK_R_P", [(84.0, 25.3), (82.525, 23.825), (82.525, 22.55)], SPK)
path("/SPK_R_P", [(82.525, 22.55), (82.525, 15.85), (84.525, 13.85), (95.4, 13.85), (J["J6"], 17.85)], SPK)
path("/SPK_R_P", [(80.525, 22.55), (82.525, 22.55)], SPK)
# R- : L4 -> C71 (turned, its + pad right), seven vias, B.Cu under J6 to J7
VR = [round(93.975 + 1.3 * i, 3) for i in range(7)]
path("/SPK_R_N", [(92.5, 25.3), (93.975, 23.825), (93.975, 22.55), (93.975, 20.6)], SPK)
path("/SPK_R_N", [(u, 20.6) for u in VR], SPK)
for u in VR:
    via("/SPK_R_N", (u, 20.6))
path("/SPK_R_N", [(u, 20.6) for u in VR] + [(107.65, 20.6), (J["J7"], 17.85)], SPK, "B.Cu")

# TAP_R: R68 pad 2 down between L2 and L3, under the L1/L2 bodies, over L5 to its old via
path("/TAP_R", [(78.975, 22.55), (79.6, 23.175), (79.6, 29.2), (63.4, 29.2), (63.4, 25.1),
                (56.3, 25.1)], SIG)
via("/TAP_R", (56.3, 25.1), 0.6, 0.3)
path("/TAP_R", [(56.3, 25.1), (53.6, 25.1)], 0.2, "B.Cu")

# L pad 1 (OUT) to the bootstrap caps' OUT pads, where the old tracks to U8 carry on
path("/OUT_L_P", [(67.0, 33.1), (69.6, 33.1), (72.7, 36.2), (73.0, 36.2)], OUT)
path("/OUT_L_N", [(75.5, 33.1), (77.6, 35.2), (77.6, 36.2)], OUT)
path("/OUT_R_P", [(84.0, 33.1), (80.9, 36.2), (80.4, 36.2)], OUT)
path("/OUT_R_N", [(92.5, 33.1), (89.9, 33.1), (86.8, 36.2), (85.0, 36.2)], OUT)

# PVDD F.Cu pours (u 58.5-72 and 86-99.5, v 33-38) to the B.Cu PVDD pour, clear of the L pads
# (inside both: the B.Cu pour ends at v 35)
for p in ((71.9, 33.4), (97.5, 34.0), (98.6, 34.0)):
    via("PVDD", p)
# GND: a via in each F.Cu pocket the speaker copper closes off under the sockets
for p in ((75.0, 10.0), (80.0, 10.0), (92.0, 8.0), (91.5, 16.5), (105.0, 9.0), (114.0, 10.0)):
    via("GND", p, 0.6, 0.3)

print(json.dumps({"version": 1, "ops": ops}, indent=1))
