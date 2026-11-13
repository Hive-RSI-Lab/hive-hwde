"""Rebuild the mazeroute golden boards (plain python, no KiCad needed).

  python3 tests/fixtures/mazeroute/make_fixture.py

Each is a two-layer board with nets GND, A, B, C and a .kicad_dru beside it.
  corridor.kicad_pcb  20 x 10: a GND via fence at x=10 with one gap
                      between the vias at y=4.8 and y=7.2; A's pads P1 (3,2) and P2 (17,2) on F.Cu
                      either side. A corridor from P1 to P2 must go through
                      the gap (#926).
  reroute.kicad_pcb   30 x 30: A1 (3,15) to A2 (27,15); B1 (15,12) to
                      B2 (15,18) joined by a 0.2 mm B track on F.Cu; GND
                      walls round A on both layers (y=11, y=19, x=1, x=29)
                      and at x=15 on B.Cu. A cannot route until B moves: reroute rips B
                      and must put it back in one piece (#927).
  group.kicad_pcb     30 x 20: pads at x=5 and x=25 for A (y=10), C (y=12)
                      and B (y=14); a 2 mm GND wall on F.Cu at x=15 with one
                      1.4 mm gap. Routed one at a time A, B, C fails at C;
                      routed as a group all three fit (#928).
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
HDR = """(kicad_pcb (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (net 0 "") (net 1 "GND") (net 2 "A") (net 3 "B") (net 4 "C")
"""
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
def pad(ref, x, y, net, size=1.0):
    return (f'  (footprint "t:{ref}" (layer "F.Cu") (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 0 0))\n'
            f'    (pad "1" smd rect (at 0 0) (size {size} {size})'
            f' (layers "F.Cu") (net "{net}")))\n')
def seg(x1, y1, x2, y2, w, layer, net, uuid):
    return (f'  (segment (start {x1} {y1}) (end {x2} {y2}) (width {w})'
            f' (layer "{layer}") (net "{net}") (uuid "{uuid}"))\n')
def via(x, y, net, uuid, size=0.6, drill=0.3):
    return (f'  (via (at {x} {y}) (size {size}) (drill {drill})'
            f' (layers "F.Cu" "B.Cu") (net "{net}") (uuid "{uuid}"))\n')
def write(path, body, w, h):
    path = Path(path)
    path.write_text(HDR + f'  (gr_rect (start 0 0) (end {w} {h})'
                    ' (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))\n'
                    + body + ")\n")
    path.with_suffix(".kicad_dru").write_text(DRU)
    return path
def corridor(path):
    # a GND via fence at x=10, y = 0.8k (0.5 mm hole to hole); k 7 and 8
    # left out: one gap between the vias at y=4.8 and y=7.2
    body = pad("P1", 3, 2, "A") + pad("P2", 17, 2, "A")
    for k in range(1, 12):
        if k in (7, 8):
            continue
        body += via(10, round(0.8 * k, 2), "GND",
                    f"00000000-0000-0000-0000-0000000001{k:02d}")
    return write(path, body, 20, 10)


def reroute(path):
    body = pad("A1", 3, 15, "A") + pad("A2", 27, 15, "A")
    body += pad("B1", 15, 12, "B") + pad("B2", 15, 18, "B")
    body += seg(15, 12, 15, 18, 0.2, "F.Cu", "B", "00000000-0000-0000-0000-0000000002b1")
    for ly in ("F.Cu", "B.Cu"):
        for y in (11, 19):
            body += seg(1, y, 29, y, 0.5, ly, "GND",
                        f"00000000-0000-0000-0000-0000000{ly[0]}{y}00")
        for x in (1, 29):    # close the box: no way round the walls' ends
            body += seg(x, 11, x, 19, 0.5, ly, "GND",
                        f"00000000-0000-0000-0000-0000000{ly[0]}{x:02d}01")
    body += seg(15, 11, 15, 19, 0.5, "B.Cu", "GND", "00000000-0000-0000-0000-0000000002c1")
    return write(path, body, 30, 30)
def group(path, gap=1.4):
    c = 10
    body = ""
    for ref, y, net in (("A", c, "A"), ("C", c + 2, "C"), ("B", c + 4, "B")):
        body += pad(ref + "1", 5, y, net) + pad(ref + "2", 25, y, net)
    lo, hi = c - gap / 2 - 1.0, c + gap / 2 + 1.0
    body += seg(15, 1.5, 15, lo, 2.0, "F.Cu", "GND", "00000000-0000-0000-0000-0000000003a1")
    body += seg(15, hi, 15, 18.5, 2.0, "F.Cu", "GND", "00000000-0000-0000-0000-0000000003a2")
    return write(path, body, 30, 20)


if __name__ == "__main__":
    for make in (corridor, reroute, group):
        print(make(HERE / f"{make.__name__}.kicad_pcb"))
