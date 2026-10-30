"""route_finish: the maze finisher's pure parts (no KiCad): the rules it
reads from a board, its islands, and the routes it plans around copper."""
from __future__ import annotations

import math
import sys
from pathlib import Path

from shapely.geometry import LineString, Point

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import geom  # noqa: E402
import route_auto  # noqa: E402
import route_edit  # noqa: E402
import route_finish as rf  # noqa: E402

HDR = """(kicad_pcb (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (net 0 "") (net 1 "GND") (net 2 "SIG") (net 3 "+3V3")
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
(rule "aiee_pwr_width_SIG"
	(constraint track_width (min 0.3000mm))
	(condition "A.NetName == 'SIG' && A.Type == 'track'")
)
"""


def _pad(ref, x, y, net, size=1.0):
    return (f'  (footprint "t:{ref}" (layer "F.Cu") (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 0 0))\n'
            f'    (pad "1" smd rect (at 0 0) (size {size} {size})'
            f' (layers "F.Cu") (net "{net}")))\n')


def _seg(x1, y1, x2, y2, w, layer, net, uuid=None):
    u = f' (uuid "{uuid}")' if uuid else ""
    return (f'  (segment (start {x1} {y1}) (end {x2} {y2}) (width {w})'
            f' (layer "{layer}") (net "{net}"){u})\n')


def _zone(net_id, net, layer, x1, y1, x2, y2):
    pts = f"(xy {x1} {y1}) (xy {x2} {y1}) (xy {x2} {y2}) (xy {x1} {y2})"
    return (f'  (zone (net {net_id}) (net_name "{net}") (layer "{layer}")\n'
            f'    (connect_pads (clearance 0.3)) (min_thickness 0.25)\n'
            f'    (polygon (pts {pts}))\n'
            f'    (filled_polygon (layer "{layer}") (pts {pts})))\n')


def _board(tmp_path, body, size=30.0, dru=DRU):
    p = tmp_path / "t.kicad_pcb"
    p.write_text(HDR + f'  (gr_rect (start 0 0) (end {size} {size})'
                 ' (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))\n'
                 + body + ")\n", encoding="utf-8")
    if dru:
        p.with_suffix(".kicad_dru").write_text(dru, encoding="utf-8")
    return p


def _pair(net, a, b, amsg="Pad 1 [SIG] of U1 on F.Cu",
          bmsg="Pad 1 [SIG] of U2 on F.Cu"):
    return {"net": net, "a": {"msg": amsg, "pos": list(a)},
            "b": {"msg": bmsg, "pos": list(b)}}


def _plan(pcb, pairs, **kw):
    bg = geom.BoardGeom.from_file(pcb)
    rules = rf.board_rules(pcb, bg)
    return rules, rf.plan_routes(bg, rules, pairs, bg.copper_layers, **kw)


def _tracks(job, layer=None):
    return [op for op in job["ops"] if op["op"] == "add_track"
            and (layer is None or op["layer"] == layer)]


# ------------------------------------------------------------------ rules

def test_rules_come_from_the_board(tmp_path):
    body = _pad("U1", 5, 15, "SIG")
    body += ('  (via (at 3 3) (size 0.7) (drill 0.35) (layers "F.Cu" "B.Cu")'
             ' (net "GND"))\n') * 1
    pcb = _board(tmp_path, body)
    rules = rf.board_rules(pcb, geom.BoardGeom.from_file(pcb))
    assert rules.clearance == 0.15            # the .kicad_dru baseline
    assert rules.hole_to_hole == 0.5
    assert rules.edge_clearance == 0.3
    assert (rules.via_size, rules.via_drill) == (0.7, 0.35)  # the board's via
    assert rules.width("SIG") == 0.301        # its rules_gen width, rounded up
    assert rules.width("OTHER") == 0.2
    assert rules.clr("SIG", "GND") == 0.15 + rules.margin


# ----------------------------------------------------------------- routes

def test_routes_around_a_wall_on_one_layer(tmp_path):
    body = _pad("U1", 5, 15, "SIG") + _pad("U2", 25, 15, "SIG")
    body += _seg(15, 0.8, 15, 20, 0.25, "F.Cu", "+3V3")
    pcb = _board(tmp_path, body)
    # vias priced high: the way round the wall's end is the cheaper one
    rules, jobs = _plan(pcb, [_pair("SIG", (5, 15), (25, 15))],
                        via_cost_mm=30, margin=8)
    job = jobs[0]
    assert job["status"] == "routed" and job["vias"] == 0
    wall = LineString([(15, 0.8), (15, 20)])
    for op in _tracks(job):
        assert op["width"] == 0.301
        seg = LineString([op["start"], op["end"]])
        assert seg.distance(wall) >= 0.125 + op["width"] / 2 + 0.15 - 1e-6
        dx = op["end"][0] - op["start"][0]
        dy = op["end"][1] - op["start"][1]
        ang = math.degrees(math.atan2(dy, dx)) % 45
        assert min(ang, 45 - ang) < 1e-6            # 0/45/90 only
    route_edit.validate_ops({"version": 1, "ops": job["ops"]})


def test_vias_under_a_full_wall(tmp_path):
    body = _pad("U1", 5, 15, "SIG") + _pad("U2", 25, 15, "SIG")
    body += _seg(15, 0.1, 15, 29.9, 0.25, "F.Cu", "+3V3")
    pcb = _board(tmp_path, body)
    _, jobs = _plan(pcb, [_pair("SIG", (5, 15), (25, 15))])
    job = jobs[0]
    assert job["status"] == "routed" and job["vias"] == 2
    assert _tracks(job, "B.Cu")
    for op in job["ops"]:
        if op["op"] == "add_via":
            assert abs(op["at"][0] - 15) >= 0.3 + 0.125 + 0.15 - 1e-6


def test_no_path_is_reported_not_forced(tmp_path):
    body = _pad("U1", 5, 15, "SIG") + _pad("U2", 25, 15, "SIG")
    body += _seg(15, 0.1, 15, 29.9, 0.25, "F.Cu", "+3V3")
    body += _seg(15, 0.1, 15, 29.9, 0.25, "B.Cu", "+3V3")
    pcb = _board(tmp_path, body)
    _, jobs = _plan(pcb, [_pair("SIG", (5, 15), (25, 15))])
    assert jobs[0]["status"] == "failed"
    assert "ops" not in jobs[0]


def test_later_routes_keep_clear_of_earlier_ones(tmp_path):
    body = _pad("U1", 5, 15, "SIG") + _pad("U2", 25, 15, "SIG")
    body += _pad("U3", 15, 5, "GND") + _pad("U4", 15, 25, "GND")
    pcb = _board(tmp_path, body)
    pairs = [_pair("SIG", (5, 15), (25, 15)),
             _pair("GND", (15, 5), (15, 25), "Pad 1 [GND] of U3 on F.Cu",
                   "Pad 1 [GND] of U4 on F.Cu")]
    _, jobs = _plan(pcb, pairs)
    assert all(j["status"] == "routed" for j in jobs)
    sig = [LineString([o["start"], o["end"]]) for o in _tracks(jobs[0], "F.Cu")]
    for o in _tracks(jobs[1], "F.Cu"):
        g = LineString([o["start"], o["end"]])
        assert min(g.distance(s) for s in sig) >= 0.301 / 2 + 0.1 + 0.15 - 1e-6


def test_a_pour_piece_is_joined_by_a_via(tmp_path):
    # KiCad names a zone by its outline corner, not by the piece cut off;
    # the piece is found as an island and joined to the largest one
    body = _zone(1, "GND", "B.Cu", 1, 1, 29, 29)
    body += _zone(1, "GND", "F.Cu", 10, 10, 13, 13)
    pcb = _board(tmp_path, body)
    zmsg = "Zone [GND] on F.Cu, priority 0"
    _, jobs = _plan(pcb, [_pair("GND", (1, 1), (1, 1), zmsg,
                                "Zone [GND] on B.Cu, priority 0")])
    job = jobs[0]
    assert job["status"] == "routed"
    vias = [op for op in job["ops"] if op["op"] == "add_via"]
    assert len(vias) == 1
    assert 10 < vias[0]["at"][0] < 13 and 10 < vias[0]["at"][1] < 13


def test_islands_join_through_vias(tmp_path):
    body = _pad("U1", 5, 15, "SIG") + _pad("U2", 25, 15, "SIG")
    body += _seg(5, 15, 10, 15, 0.3, "F.Cu", "SIG")
    body += _seg(10, 15, 20, 15, 0.3, "B.Cu", "SIG")
    body += _seg(20, 15, 25, 15, 0.3, "F.Cu", "SIG")
    body += ('  (via (at 20 15) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
             ' (net "SIG"))\n')
    pcb = _board(tmp_path, body)
    bg = geom.BoardGeom.from_file(pcb)
    assert len(rf.net_islands(bg, "SIG")) == 2      # no via at x=10 yet
    body += ('  (via (at 10 15) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
             ' (net "SIG"))\n')
    pcb = _board(tmp_path, body)
    bg = geom.BoardGeom.from_file(pcb)
    isl = rf.net_islands(bg, "SIG")
    assert len(isl) == 1
    _, jobs = _plan(pcb, [_pair("SIG", (5, 15), (25, 15))])
    assert jobs[0]["status"] == "skipped"


def test_pick_island_follows_the_layer_named():
    from shapely.geometry import box
    isl = [{"F.Cu": [box(0, 0, 1, 1)]}, {"B.Cu": [box(0, 0, 1, 1)]}]
    assert rf.pick_island(isl, (0.5, 0.5), "Track [X] on B.Cu") == 1
    assert rf.pick_island(isl, (0.5, 0.5), "Track [X] on F.Cu") == 0


def test_path_to_ops_vias_at_layer_changes():
    gr = rf.Grid(x0=0, y0=0, nx=10, ny=10, g=0.1, layers=["F.Cu", "B.Cu"],
                 blk=[], vblk=b"", via=(0.6, 0.3), cost=[], in_a=[], in_b=[],
                 a_plane=b"", b_plane=b"")
    rules = rf.Rules(clearance=0.1, hole_clearance=0.25, hole_to_hole=0.5,
                     edge_clearance=0.3, min_width=0.2, via_size=0.6,
                     via_drill=0.3)
    path = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 0, 1), (2, 1, 1), (3, 2, 1),
            (3, 2, -1)]
    ops = rf.path_to_ops(gr, path, "SIG", 0.2, rules)
    tracks = [o for o in ops if o["op"] == "add_track"]
    vias = [o["at"] for o in ops if o["op"] == "add_via"]
    assert [(t["layer"], t["start"], t["end"]) for t in tracks] == [
        ("F.Cu", [0.0, 0.0], [0.2, 0.0]),
        ("B.Cu", [0.2, 0.0], [0.2, 0.1]),
        ("B.Cu", [0.2, 0.1], [0.3, 0.2])]
    assert vias == [[0.2, 0.0], [0.3, 0.2]]


# ------------------------------------------------------- route_auto hooks

def test_krt_narrow_tracks_on_power_nets_are_found(tmp_path):
    body = _seg(5, 5, 10, 5, 0.2, "F.Cu", "SIG", "u-narrow")  # KRT: under 0.3
    body += _seg(5, 8, 10, 8, 0.35, "F.Cu", "SIG", "u-wide")
    body += _seg(5, 11, 10, 11, 0.2, "F.Cu", "GND", "u-gnd")  # no width rule
    pcb = _board(tmp_path, body)
    bg = geom.BoardGeom.from_file(pcb)
    narrow = route_auto._narrow_new_tracks(pcb, {"SIG": 0.3}, set())
    assert narrow == ["u-narrow"]
    assert route_auto._narrow_new_tracks(
        pcb, {"SIG": 0.3}, {t.uuid for t in bg.tracks_of()}) == []


def test_a_walled_in_pad_rips_the_signal_track_in_the_way(tmp_path):
    body = _pad("U1", 5, 15, "SIG") + _pad("U2", 25, 15, "SIG")
    body += _seg(15, 0.1, 15, 29.9, 0.25, "F.Cu", "+3V3", "u-wf")
    body += _seg(15, 0.1, 15, 29.9, 0.25, "B.Cu", "+3V3", "u-wb")
    pcb = _board(tmp_path, body)
    pairs = [_pair("SIG", (5, 15), (25, 15))]
    _, jobs = _plan(pcb, pairs)
    assert jobs[0]["status"] == "failed"            # only with rip_board
    rip: list = []
    _, jobs = _plan(pcb, pairs, rip_board=True, ripped_out=rip)
    job = jobs[0]
    assert job["status"] == "routed"
    assert job["ripped_nets"] == ["+3V3"]
    assert rip and set(rip) <= {"u-wf", "u-wb"} and len(rip) == 1
    assert job["vias"] == 0 or len(rip) == 1        # one layer crossed


def test_power_and_pour_copper_is_never_ripped(tmp_path):
    body = _pad("U1", 5, 15, "+3V3") + _pad("U2", 25, 15, "+3V3")
    # SIG carries a .kicad_dru width rule: sized by rule, never ripped
    body += _seg(15, 0.1, 15, 29.9, 0.35, "F.Cu", "SIG", "u-wf")
    body += _seg(15, 0.1, 15, 29.9, 0.35, "B.Cu", "SIG", "u-wb")
    pcb = _board(tmp_path, body)
    rip: list = []
    _, jobs = _plan(pcb, [_pair("+3V3", (5, 15), (25, 15),
                                "Pad 1 [+3V3] of U1 on F.Cu",
                                "Pad 1 [+3V3] of U2 on F.Cu")],
                    rip_board=True, ripped_out=rip)
    assert jobs[0]["status"] == "failed"         # SIG stays a wall
    assert "ripped_nets" not in jobs[0]
    assert rip == []
