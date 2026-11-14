"""Rebuild the pad fanout golden board (plain python, no KiCad needed).

  python3 tests/fixtures/padfanout/make_fixture.py

qfn.kicad_pcb: 20 x 20, two layers, nets GND VIN A B C D E. U1 at (10,10)
is a 12-pin QFN-like part (3 pads a side, 0.5 mm pitch, 0.7 x 0.25 mm pads,
long axis outward), no exposed pad:
  left   1 A (8.5,9.5)   2 VIN (8.5,10)   3 B (8.5,10.5)
  bottom 4 C  5 D  6 E   right 7 C 8 D 9 E   top 10 C 11 GND 12 E
Other nets' vias crowd pin 2's outward side: C at (7.3,10.55) and B at
(6.6,11.2). The straight spot is taken, so the fanout must find one off to
the side that clears both, and pins 1 and 3 (#929). GND has a via at
(10,7.7), 0.8 mm from pin 11: that pin already fans out ("has via").

vip.kicad_pcb (#940): 20 x 20, four layers, the same nets. U2 at (10,10)
has pad 1 VIN, 1.2 x 1.2 mm at (8,10), and pad 2 GND at (12,10). A
0.2 mm net-A track walls pad 1 in on F.Cu on its outward (x = 7.1) and
both sides (y = 9.1 and 10.9, out to x = 8.9), 0.2 mm off its copper:
every via spot outside needs a stub across A, and there is no VIN copper
to join. Only a via in the pad itself fans it out.

bga.kicad_pcb (#956): 20 x 20, four layers, nets P GND S1..S6. U3 at (10,10)
is a 4 x 4 ball grid at 0.5 mm pitch, 0.2 mm round balls, rows A-D down,
columns 1-4 right (A1 at (9.25,9.25)):
  A  P  S1 P  GND
  B  S2 P  S3 P
  C  P  S4 GND S5
  D  GND P S6 P
Plane nets P and GND. The DRU's via floor is 0.254/0.127 and the board's
common via is GND's 0.6/0.3 at (3,3) and (17,17), far off: a ball takes the
floor via (the .kicad_pro's 0.254 mm and 0.127 mm drill at its 0.0635 mm
annular ring). A3 already holds a 0.254/0.127 P via; B4 is 0.71 mm from it and
still needs its own. A 0.1 mm S4 track on F.Cu runs between rows B and C
under column 2, 0.1 mm off both balls' copper: a via in B2 would sit
0.073 mm from it, so B2 has no room.
A decoupler C1 on B.Cu has a 0.3 mm GND pad right under ball D1: a
plain via there would drain its paste and is kept off, but D1's via is
filled, so it still goes in.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
NETS = ["GND", "VIN", "A", "B", "C", "D", "E"]
HDR = ('(kicad_pcb (version 20260206) (generator "test")\n'
       '  (general (thickness 1.6))\n'
       '  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))\n'
       '  (setup)\n  (net 0 "")'
       + "".join(f' (net {i + 1} "{n}")' for i, n in enumerate(NETS)) + "\n")
DRU = """(version 1)
(rule "aiee_clearance_floor"
	(constraint clearance (min 0.1500mm))
)
(rule "aiee_hole_to_hole_floor"
	(constraint hole_to_hole (min 0.5000mm))
)
(rule "aiee_edge_clearance_floor"
	(constraint edge_clearance (min 0.3000mm))
)
"""
PINS = {1: "A", 2: "VIN", 3: "B", 4: "C", 5: "D", 6: "E", 7: "C", 8: "D",
        9: "E", 10: "C", 11: "GND", 12: "E"}


def pads():
    out = []
    for i, y in enumerate((-0.5, 0, 0.5)):       # left, top to bottom
        out.append((1 + i, -1.5, y, 0.7, 0.25))
    for i, x in enumerate((-0.5, 0, 0.5)):       # bottom, left to right
        out.append((4 + i, x, 1.5, 0.25, 0.7))
    for i, y in enumerate((0.5, 0, -0.5)):       # right, bottom to top
        out.append((7 + i, 1.5, y, 0.7, 0.25))
    for i, x in enumerate((0.5, 0, -0.5)):       # top, right to left
        out.append((10 + i, x, -1.5, 0.25, 0.7))
    return out


def via(x, y, net, n, size=0.6, drill=0.3):
    return (f'  (via (at {x} {y}) (size {size}) (drill {drill})'
            f' (layers "F.Cu" "B.Cu") (net "{net}")'
            f' (uuid "00000000-0000-0000-0000-0000000004{n:02d}"))\n')


def qfn(path):
    body = '  (footprint "t:QFN12" (layer "F.Cu") (at 10 10)\n'
    body += '    (property "Reference" "U1" (at 0 0 0))\n'
    for num, x, y, w, h in pads():
        body += (f'    (pad "{num}" smd rect (at {x} {y}) (size {w} {h})'
                 f' (layers "F.Cu") (net "{PINS[num]}"))\n')
    body += "  )\n"
    body += via(7.3, 10.55, "C", 1) + via(6.6, 11.2, "B", 3)
    body += via(10, 7.7, "GND", 4)
    path = Path(path)
    path.write_text(HDR + '  (gr_rect (start 0 0) (end 20 20)'
                    ' (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))\n'
                    + body + ")\n")
    path.with_suffix(".kicad_dru").write_text(DRU)
    return path


HDR4 = HDR.replace('(2 "B.Cu" signal)',
                   '(4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal)')


def track(a, b, net, n, w=0.2, layer="F.Cu"):
    return (f'  (segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]})'
            f' (width {w}) (layer "{layer}") (net "{net}")'
            f' (uuid "00000000-0000-0000-0000-0000000005{n:02d}"))\n')


def vip(path):
    body = '  (footprint "t:PWR2" (layer "F.Cu") (at 10 10)\n'
    body += '    (property "Reference" "U2" (at 0 0 0))\n'
    body += ('    (pad "1" smd rect (at -2 0) (size 1.2 1.2)'
             ' (layers "F.Cu") (net "VIN"))\n')
    body += ('    (pad "2" smd rect (at 2 0) (size 1.2 1.2)'
             ' (layers "F.Cu") (net "GND"))\n')
    body += "  )\n"
    wall = [(8.9, 9.1), (7.1, 9.1), (7.1, 10.9), (8.9, 10.9)]
    body += "".join(track(a, b, "A", i + 1)
                    for i, (a, b) in enumerate(zip(wall, wall[1:])))
    path = Path(path)
    path.write_text(HDR4 + '  (gr_rect (start 0 0) (end 20 20)'
                    ' (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))\n'
                    + body + ")\n")
    path.with_suffix(".kicad_dru").write_text(DRU)
    return path


BGA_NETS = ["P", "GND"] + [f"S{i}" for i in range(1, 7)]
BGA = ["P S1 P GND", "S2 P S3 P", "P S4 GND S5", "GND P S6 P"]
BGA_DRU = """(version 1)
(rule "aiee_clearance_floor"
	(constraint clearance (min 0.1000mm))
)
(rule "aiee_hole_to_hole_floor"
	(constraint hole_to_hole (min 0.2500mm))
)
(rule "aiee_edge_clearance_floor"
	(constraint edge_clearance (min 0.3000mm))
)
(rule "aiee_via_drill_floor"
	(constraint hole_size (min 0.1270mm))
	(condition "A.Type == 'via'")
)
(rule "aiee_via_diameter_floor"
	(constraint via_diameter (min 0.2540mm))
	(condition "A.Type == 'via'")
)
(rule "aiee_annular_floor"
	(constraint annular_width (min 0.0635mm))
	(condition "A.Type == 'via'")
)
"""


BGA_PRO_RULES = {"min_via_diameter": 0.254, "min_through_hole_diameter": 0.127,
                 "min_via_annular_width": 0.0635, "min_hole_to_hole": 0.25,
                 "min_clearance": 0.1, "min_track_width": 0.1}


def ball_at(name):
    r, c = "ABCD".index(name[0]), int(name[1:]) - 1
    return round(9.25 + c * 0.5, 4), round(9.25 + r * 0.5, 4)


def bga(path):
    hdr = HDR4
    for i, n in enumerate(NETS):
        hdr = hdr.replace(f' (net {i + 1} "{n}")', "")
    hdr = hdr.replace('(net 0 "")', '(net 0 "")' + "".join(
        f' (net {i + 1} "{n}")' for i, n in enumerate(BGA_NETS)))
    body = '  (footprint "t:BGA16" (layer "F.Cu") (at 10 10)\n'
    body += '    (property "Reference" "U3" (at 0 0 0))\n'
    for r, row in zip("ABCD", BGA):
        for c, net in enumerate(row.split(), 1):
            x, y = ball_at(f"{r}{c}")
            body += (f'    (pad "{r}{c}" smd circle (at {x - 10:g} {y - 10:g})'
                     f' (size 0.2 0.2) (layers "F.Cu") (net "{net}"))\n')
    body += "  )\n"
    body += ('  (footprint "t:C0201" (layer "B.Cu") (at 9.25 10.75)\n'
             '    (property "Reference" "C1" (at 0 0 0))\n'
             '    (pad "1" smd rect (at 0 0) (size 0.3 0.3)'
             ' (layers "B.Cu" "B.Paste" "B.Mask") (net "GND"))\n  )\n')
    body += via(*ball_at("A3"), "P", 1, size=0.254, drill=0.127)
    body += via(3, 3, "GND", 2) + via(17, 17, "GND", 3)
    y = 9.25 + 1.5 * 0.5                            # between rows B and C
    body += track((9.55, y), (9.95, y), "S4", 1, w=0.1)
    path = Path(path)
    path.write_text(hdr + '  (gr_rect (start 0 0) (end 20 20)'
                    ' (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))\n'
                    + body + ")\n")
    path.with_suffix(".kicad_dru").write_text(BGA_DRU)
    path.with_suffix(".kicad_pro").write_text(json.dumps(
        {"board": {"design_settings": {"rules": BGA_PRO_RULES}}},
        indent=2) + "\n")
    return path


if __name__ == "__main__":
    print(qfn(HERE / "qfn.kicad_pcb"))
    print(vip(HERE / "vip.kicad_pcb"))
    print(bga(HERE / "bga.kicad_pcb"))
