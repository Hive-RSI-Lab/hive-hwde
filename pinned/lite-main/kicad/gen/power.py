"""lite-main power copper: the pours (constraints.json "planes") and the
pre-routed VBUS/PVDD vias and tracks (kicad/power-vias.json for route_edit),
in board coordinates (u right, v down from the outline's top-left corner).

board_init re-centres the outline on every run, so the absolute coordinates
are regenerated from reports/board-init.json each time (build.sh board).

Why pours: VBUS and PVDD carry up to 5 A, and a 2.4 mm track can't reach the
USB-C, ESSOP or HTSSOP pads; a pour necks around foreign pads where a track
can't (hwde remediations/track_width.md, ladder step 4).

    python3 kicad/gen/power.py
"""
import json
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
X0, Y0 = json.loads((WS / "reports" / "board-init.json").read_text())["outline_origin"]


def A(u, v):
    return [round(X0 + u, 3), round(Y0 + v, 3)]


def R(u1, v1, u2, v2):
    return A(u1, v1) + A(u2, v2)


planes = [
    {"net": "GND", "layer": "B.Cu", "priority": 0},
    # Q1's source; J1's right VBUS pad (A4+B9) reaches it by a 1 mm track
    # (a pour around the receptacle's pads broke into islands)
    {"net": "VBUS", "layer": "F.Cu", "region": R(63.6, 2.5, 66.9, 13.4), "priority": 1, "connect": "solid"},  # + D1 cathode
    # Q1's drain pads, and vias straight down to a B.Cu PVDD column that runs
    # under the filter caps to the B.Cu pour (on F.Cu the speaker lines from
    # J4 to L1/L2 have to cross that path, and cut an F.Cu strip in two)
    {"net": "PVDD", "layer": "F.Cu", "region": R(69.4, 2.5, 72.4, 9.6), "priority": 1, "connect": "solid"},
    {"net": "PVDD", "layer": "B.Cu", "region": R(69.4, 3.0, 72.9, 19.8), "priority": 1, "connect": "solid"},
    # B.Cu PVDD under the inductors only: it stops 5 mm short of the TPA3118 so
    # the B.Cu ground under the chip spreads its heat in every direction
    {"net": "PVDD", "layer": "B.Cu", "region": R(63.5, 19.8, 98.7, 35.0), "priority": 1, "connect": "solid"},
    # the TPA3118's two PVCC pin groups, their decoupling and the bulk caps,
    # each reaching up to the B.Cu pour's edge (v 33-35) for its vias
    {"net": "PVDD", "layer": "F.Cu", "region": R(58.5, 33.0, 72.0, 38.0), "priority": 1, "connect": "solid"},
    {"net": "PVDD", "layer": "F.Cu", "region": R(58.5, 38.0, 74.6, 43.5), "priority": 1, "connect": "solid"},
    {"net": "PVDD", "layer": "F.Cu", "region": R(86.0, 33.0, 99.5, 38.0), "priority": 1, "connect": "solid"},
    {"net": "PVDD", "layer": "F.Cu", "region": R(82.4, 38.0, 99.5, 43.5), "priority": 1, "connect": "solid"},
]
ops = []


def via(u, v, net, size=0.8, drill=0.4):
    ops.append({"op": "add_via", "at": A(u, v), "size": size, "drill": drill, "net": net})


def trk(a, b, w, layer, net):
    ops.append({"op": "add_track", "start": A(*a), "end": A(*b), "width": w, "layer": layer, "net": net})


# J1's left VBUS pad (B4+A9): F.Cu stub to a via, then B.Cu under U1 and the
# PD parts to the F.Cu VBUS island at Q1's source; U1's VBUS pin 8 ties to it
trk((56.1, 7.5), (56.1, 8.9), 0.5, "F.Cu", "VBUS")
via(56.1, 8.9, "VBUS", 0.6, 0.3)
trk((55.75, 9.85), (56.1, 9.2), 0.3, "F.Cu", "VBUS")
# (straight across under J1's pad row: further down it would run over the
# CH224A's exposed-pad GND vias, which planes_gen adds)
trk((56.1, 8.9), (64.2, 8.9), 1.2, "B.Cu", "VBUS")
trk((64.2, 8.9), (64.2, 9.4), 1.2, "B.Cu", "VBUS")
via(64.2, 9.4, "VBUS")
# J1's SMD ground pads tie sideways to the shell's through-hole pads
trk((55.3, 7.5), (54.4, 7.5), 0.4, "F.Cu", "GND")
trk((61.7, 7.5), (62.6, 7.5), 0.4, "F.Cu", "GND")
# J1's right VBUS pad (A4+B9) to the VBUS pour at Q1's source, under the GND
# pad and the shell pin
trk((60.9, 7.5), (60.9, 8.9), 0.6, "F.Cu", "VBUS")
trk((60.9, 8.9), (64.0, 8.9), 1.0, "F.Cu", "VBUS")
# same-net pours that only abut are joined by a track across the seams
# (overlapping them makes planes_gen raise one's priority, and KiCad then
# keeps the two fills apart)
trk((62.0, 35.5), (62.0, 43.0), 1.0, "F.Cu", "PVDD")
trk((98.4, 35.5), (98.4, 43.0), 1.0, "F.Cu", "PVDD")
trk((71.15, 18.8), (71.15, 21.0), 1.5, "B.Cu", "PVDD")    # column -> pour seam
# C2 (Miller cap) PVDD pad to a via into the column
trk((68.9, 11.7), (70.6, 11.7), 0.6, "F.Cu", "PVDD")
via(70.6, 11.7, "PVDD", 0.6, 0.3)
# PVDD layer changes: Q1's drain -> B.Cu column, B.Cu pour -> the PVCC groups
for v in (3.6, 4.85, 6.1, 7.35, 8.6):
    via(71.75, v, "PVDD")
for u in (64.0, 65.3, 66.6, 67.9, 69.2):
    via(u, 34.0, "PVDD")
for u in (88.0, 90.0, 92.0, 94.0, 96.0):
    via(u, 34.0, "PVDD")

for name in ("kicad/constraints.json",):
    c = json.loads((WS / name).read_text())
    c["planes"] = planes
    (WS / name).write_text(json.dumps(c, indent=1) + "\n")
# after routing: top-side ground in every free area (return paths, shielding,
# heat spreading for the TPA3118). Poured after Freerouting, which would
# otherwise treat it as a plane to route around.
post = json.loads((WS / "kicad" / "constraints.json").read_text())
post["planes"] = [{"net": "GND", "layer": "F.Cu", "priority": 0, "connect": "solid"}]
(WS / "route" / "post-route-planes.json").write_text(json.dumps(post, indent=1) + "\n")
(WS / "kicad" / "power-vias.json").write_text(json.dumps({"version": 1, "ops": ops}, indent=1) + "\n")
print(json.dumps({"planes": len(planes), "ops": len(ops), "origin": [X0, Y0]}))
