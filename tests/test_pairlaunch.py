"""lib/pairlaunch.py: pair stubs a coupled router can launch from (#546)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))

import pairlaunch as pl  # noqa: E402

LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
SPEC = pl.Spec("In2.Cu", 0.09, 0.26, 0.125, 0.45, 0.2, 0.5)


class Board:
    """Just what pairlaunch reads from geom.BoardGeom."""

    def __init__(self, tracks=(), vias=(), pads=()):
        self.tracks, self.vias, self.pads = list(tracks), list(vias), list(pads)
        self.copper_layers = LAYERS
        self.nets = {x.net for x in [*self.tracks, *self.vias, *self.pads]}

    def tracks_of(self, net=None, layer=None):
        return [t for t in self.tracks
                if (net is None or t.net == net) and (layer is None or t.layer == layer)]

    def vias_of(self, net=None, layer=None):
        return [v for v in self.vias
                if (net is None or v.net == net) and (layer is None or layer in v.layers)]

    def pads_of(self, net=None, layer=None, ref=None):
        return [p for p in self.pads
                if (net is None or p.net == net) and (layer is None or p.on(layer))
                and (ref is None or p.ref == ref)]

    def zones_of(self, net=None, layer=None):
        return []

    def net_copper(self, net, layer):
        parts = [t.shape.buffer(t.width / 2) for t in self.tracks_of(net, layer)]
        parts += [Point(v.at).buffer(v.diameter / 2) for v in self.vias_of(net, layer)]
        parts += [Point(p.center).buffer(0.16) for p in self.pads_of(net, layer)]
        return unary_union(parts)


def track(net, layer, *pts, width=0.09):
    return NS(net=net, layer=layer, width=width, shape=LineString(pts),
              uuid=f"t-{net}-{layer}-{pts[0]}")


def via(net, at):
    return NS(net=net, at=at, diameter=0.45, drill=0.2, layers=tuple(LAYERS),
              uuid=f"v-{net}-{at}")


def pad(net, ref, number, center):
    return NS(net=net, ref=ref, number=number, center=center, layers=("F.Cu",),
              on=lambda ly, _l=("F.Cu",): ly in _l)


def bga(ref="U1", nets=None, bbox=(0.0, 0.0, 4.0, 4.0)):
    return NS(ref=ref, bbox=bbox, pitch=0.8, balls=25, nets=nets or {})


def dog_bone(net, ball_xy, via_xy, *stub, number="A1", ref="U1"):
    """ball -> F.Cu neck -> via -> In2 stub."""
    return (track(net, "F.Cu", ball_xy, via_xy),
            via(net, via_xy),
            track(net, "In2.Cu", via_xy, *stub),
            pad(net, ref, number, ball_xy))


def test_island_finds_neck_via_and_tip():
    t1, v, t2, b = dog_bone("P", (1.2, 1.2), (1.6, 1.6), (1.6, 3.0), (1.6, 4.45))
    bg = Board([t1, t2], [v], [b])
    isl = pl.island(bg, "P", b)
    assert isl.via is v and isl.neck == [t1]
    assert isl.tips == [(1.6, 4.45, "In2.Cu")]
    assert not isl.routed


def test_island_joins_a_stub_ending_on_the_via_ring():
    # KRT's neck ends 0.026 mm off the via centre (linux-computer DQS0)
    b = pad("P", "U1", "A1", (1.2, 1.2))
    t1 = track("P", "F.Cu", (1.2, 1.2), (1.6, 1.6))
    v = via("P", (1.6, 1.6))
    t2 = track("P", "In2.Cu", (1.6, 1.574), (1.6, 4.45))
    isl = pl.island(Board([t1, t2], [v], [b]), "P", b)
    assert t2 in isl.tracks and isl.tips == [(1.6, 4.45, "In2.Cu")]


def test_island_of_routed_net_says_so():
    a, b = pad("P", "U1", "A1", (0, 0)), pad("P", "U2", "A1", (5, 0))
    bg = Board([track("P", "F.Cu", (0, 0), (5, 0))], [], [a, b])
    assert pl.island(bg, "P", a).routed


def test_chirality_opposite_at_the_two_ends_of_a_straight_pair():
    # a pair running north: P east of N at both ends. South terminal looks
    # north, north terminal looks south.
    south = pl.chirality("N", (1.0, 5.0), (0.0, 5.0))
    north = pl.chirality("S", (1.0, 0.0), (0.0, 0.0))
    assert south == -north


def test_chirality_around_a_corner():
    # north out of one field, then west into the east side of another:
    # P left of N going north, P south of N arriving west (linux-computer DQS0)
    assert pl.chirality("N", (87.6, 138.4), (88.1, 138.4)) \
        == -pl.chirality("E", (88.45, 130.2), (88.45, 129.9))


def test_needs_relay_reasons():
    ball_p, ball_n = pad("P", "U1", "A", (1, 1)), pad("N", "U1", "B", (1.8, 1))
    def isl(net, ball, tip):
        return NS(net=net, ball=ball, tips=[tip])
    bbox = (0, 0, 4, 4)
    good = (isl("P", ball_p, (1.2, 4.45, "In2.Cu")), isl("N", ball_n, (1.0, 4.45, "In2.Cu")))
    assert pl.needs_relay(bbox, 0.8, *good, "In2.Cu", (1, 10), True) is None
    assert pl.needs_relay(bbox, 0.8, *good, "In2.Cu", (1, -10), True) \
        == "tips point away from the other terminal"
    mixed = (good[0], isl("N", ball_n, (1.0, 4.45, "B.Cu")))
    assert pl.needs_relay(bbox, 0.8, *mixed, "In2.Cu", (1, 10), True) \
        == "tips on different layers"
    apart = (good[0], isl("N", ball_n, (4.45, 1.0, "In2.Cu")))
    assert pl.needs_relay(bbox, 0.8, *apart, "In2.Cu", (1, 10), True) == "tips apart"
    outer = (isl("P", ball_p, (1.2, 4.45, "F.Cu")), isl("N", ball_n, (1.0, 4.45, "F.Cu")))
    assert pl.needs_relay(bbox, 0.8, *outer, "In2.Cu", (1, 10), True) \
        == "tips off the stripline layer"
    # a BGA-to-connector pair may stay on the outer layer
    assert pl.needs_relay(bbox, 0.8, *outer, "In2.Cu", (1, 10), False) is None


def _pointing_south_board():
    """Pair whose In2 stubs leave south while the partner part is north."""
    tp, vp, sp, bp = dog_bone("P", (2.0, 2.4), (2.4, 2.8), (2.4, 4.45), number="C3")
    tn, vn, sn, bn = dog_bone("N", (1.2, 2.4), (1.6, 2.8), (1.6, 4.45), number="C2")
    far_p, far_n = pad("P", "U2", "A", (2.4, -10)), pad("N", "U2", "B", (1.6, -10))
    return Board([tp, sp, tn, sn], [vp, vn], [bp, bn, far_p, far_n]), bp, bn


def test_relay_turns_a_terminal_toward_its_partner():
    bg, bp, bn = _pointing_south_board()
    b = bga(nets={"C3": "P", "C2": "N"})
    got = pl.plan_launch(bg, [b], "P", "N", SPEC)
    assert not got["failed"]
    term = got["terminals"][0]
    assert term["side"] == "N"
    assert term["via_in_pad"] == []
    tips = term["tips"]
    assert tips["P"][1] == pytest.approx(tips["N"][1])          # side by side
    assert abs(tips["P"][0] - tips["N"][0]) == pytest.approx(SPEC.pitch, abs=0.06)
    # the In2 stubs go, the F.Cu necks and the vias stay
    removed = {o["uuid"] for o in got["ops"] if o["op"] == "remove"}
    assert all("In2" in u for u in removed) and len(removed) == 2
    assert sum(o["op"] == "add_path" for o in got["ops"]) == 2


def test_relay_keeps_clear_of_foreign_copper():
    bg, _, _ = _pointing_south_board()
    wall = track("X", "In2.Cu", (-1.0, 1.5), (5.0, 1.5))
    bg.tracks.append(wall)
    bg.nets.add("X")
    b = bga(nets={"C3": "P", "C2": "N"})
    got = pl.plan_launch(bg, [b], "P", "N", SPEC)
    # north is walled off across the whole field: it must take another side
    assert got["terminals"][0]["side"] != "N"
    for o in got["ops"]:
        if o["op"] == "add_path":
            line = LineString(o["points"]).buffer(SPEC.width / 2)
            assert line.distance(wall.shape.buffer(wall.width / 2)) \
                >= SPEC.clearance - 1e-4


def test_second_terminal_takes_the_opposite_chirality():
    # two fields, P/N stubs both need re-laying. A pair straight north of the
    # other: the north field's balls are in the "wrong" order, so one net must
    # go round the other.
    tp, vp, sp, bp = dog_bone("P", (2.0, 2.4), (2.4, 2.8), (2.4, 4.45), number="C3")
    tn, vn, sn, bn = dog_bone("N", (1.2, 2.4), (1.6, 2.8), (1.6, 4.45), number="C2")
    up_p = pad("P", "U2", "C2", (1.2, -6.0))
    up_n = pad("N", "U2", "C3", (2.0, -6.0))
    bg = Board([tp, sp, tn, sn], [vp, vn], [bp, bn, up_p, up_n])
    fields = [bga("U1", {"C3": "P", "C2": "N"}),
              bga("U2", {"C2": "P", "C3": "N"}, bbox=(0.0, -8.0, 4.0, -4.0))]
    fields[1].balls = 30
    got = pl.plan_launch(bg, fields, "P", "N", SPEC)
    assert not got["failed"]
    a, b = got["terminals"]
    sa = pl.chirality(a["side"], a["tips"]["P"], a["tips"]["N"])
    sb = pl.chirality(b["side"], b["tips"]["P"], b["tips"]["N"])
    assert sa == -sb
    assert b["via_in_pad"] == ["U2.C2", "U2.C3"]


def test_already_routed_pair_is_left_alone():
    a, b = pad("P", "U1", "C3", (2.0, 2.4)), pad("P", "U2", "C3", (2.0, -6.0))
    c, d = pad("N", "U1", "C2", (1.2, 2.4)), pad("N", "U2", "C2", (1.2, -6.0))
    bg = Board([track("P", "F.Cu", (2.0, 2.4), (2.0, -6.0)),
                track("N", "F.Cu", (1.2, 2.4), (1.2, -6.0))], [], [a, b, c, d])
    fields = [bga("U1", {"C3": "P", "C2": "N"}),
              bga("U2", {"C3": "P", "C2": "N"}, bbox=(0.0, -8.0, 4.0, -4.0))]
    assert pl.plan_launch(bg, fields, "P", "N", SPEC)["ops"] == []


def test_tip_transition_when_via_in_pad_clashes():
    # F.Cu stubs to the south edge, with an In2 track under the balls so a
    # via-in-pad can't go there: the layer change goes past the tips.
    bp = pad("P", "U1", "C3", (2.0, 3.2))
    bn = pad("N", "U1", "C2", (1.2, 3.2))
    tp = track("P", "F.Cu", (2.0, 3.2), (1.71, 4.45))
    tn = track("N", "F.Cu", (1.2, 3.2), (1.49, 4.45))
    wall = track("X", "In2.Cu", (1.0, 3.2), (2.2, 3.2))
    far_p, far_n = pad("P", "U2", "A", (1.71, 12.0)), pad("N", "U2", "B", (1.49, 12.0))
    bg = Board([tp, tn, wall], [], [bp, bn, far_p, far_n])
    b = bga(nets={"C3": "P", "C2": "N"})
    p_isl, n_isl = pl.island(bg, "P", bp), pl.island(bg, "N", bn)
    assert "why" in pl.relay_terminal(bg, p_isl, n_isl, b, SPEC, (1.6, 12.0), None)
    got = pl.tip_transition(bg, p_isl, n_isl, b, SPEC, None)
    assert "why" not in got
    vias = [o["at"] for o in got["ops"] if o["op"] == "add_via"]
    assert len(vias) == 2
    assert abs(vias[0][0] - vias[1][0]) == pytest.approx(0.7)   # drill + hole-to-hole
    assert all(v[1] > 4.45 for v in vias)
    # and P keeps its side
    assert got["tips"]["P"][0] > got["tips"]["N"][0]


def test_tip_transition_refuses_the_wrong_order():
    bp, bn = pad("P", "U1", "C3", (2.0, 3.2)), pad("N", "U1", "C2", (1.2, 3.2))
    tp = track("P", "F.Cu", (2.0, 3.2), (1.71, 4.45))
    tn = track("N", "F.Cu", (1.2, 3.2), (1.49, 4.45))
    bg = Board([tp, tn], [], [bp, bn])
    b = bga(nets={"C3": "P", "C2": "N"})
    p_isl, n_isl = pl.island(bg, "P", bp), pl.island(bg, "N", bn)
    wrong = -pl.chirality("S", (1.71, 4.45), (1.49, 4.45))
    assert "wrong P/N order" in pl.tip_transition(bg, p_isl, n_isl, b, SPEC, wrong)["why"]


def test_netconn_joins_a_via_in_pad():
    # the ball has only a via on it, no F.Cu track: the pad is still connected
    import netconn
    ball = NS(net="P", ref="U1", number="A1", center=(0.0, 0.0), layers=("F.Cu",),
              poly=Point(0, 0).buffer(0.16))
    other = NS(net="P", ref="U2", number="A1", center=(5.0, 0.0), layers=("In2.Cu",),
               poly=Point(5, 0).buffer(0.16))
    v = NS(net="P", at=(0.0, 0.0), diameter=0.45, drill=0.2,
           spans=lambda ly: ly in LAYERS)
    t = track("P", "In2.Cu", (0.0, 0.0), (5.0, 0.0))
    t.length = 5.0
    bg = NS(tracks_of=lambda net: [t], vias_of=lambda net: [v],
            pads_of=lambda net: [ball, other])
    g = netconn.build(bg, "P")
    assert netconn.shortest_path_len(g, ("pad", "U1", "A1"), ("pad", "U2", "A1")) \
        == pytest.approx(5.0)
