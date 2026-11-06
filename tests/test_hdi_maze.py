"""#477: the route_finish maze on escaped BGAs, one small board per package.

Each fixture (tests/fixtures/hdi_maze, see its README) is a BGA already through
route_escape, with a connector to reach. The answers are what the maze does on
it with the escape left in place (--no-rip-board): completion, DRC errors and
warnings, and what is still unrouted. They are measured, not goals.

Warning counts move with KiCad (conftest's RECORDED_KICAD is older than the
10.0.6 these were measured on), so they are compared only on RECORDED; the
unconnected and error counts hold on any version. holes_co_located is left out
everywhere: on the same copper KiCad counts it by the order of the vias in the
file, and route_finish does not keep that order from run to run. Shuffling one
board's vias gave 8 to 16; its unchanged order 4, another run's 6.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import env  # noqa: E402
import route_finish as rf  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "hdi_maze"
RECORDED = "10.0.6"


def _need_tools():
    if env.find_kicad_cli() is None or env.find_krt() is None:
        pytest.skip("kicad-cli or KRT not installed")


def _maze(tmp_path, case, *flags):
    for f in (FIXTURES / case).iterdir():
        shutil.copy(f, tmp_path / f.name)
    payload, _ = rf.run(["--pcb", str(tmp_path / "b.kicad_pcb"), *flags,
                         "--work-dir", str(tmp_path / "work"),
                         "--out-report", str(tmp_path / "r.json")])
    return payload


def _warnings(payload):
    found = {}
    for v in payload["violations"]:
        if v.get("severity") == "warning" and v["check"] != "holes_co_located":
            found[v["check"]] = found.get(v["check"], 0) + 1
    return found


def _answers(payload, *, before, after, warnings):
    f = payload["facts"]
    assert f["unconnected_before"] == before
    assert f["unconnected_after"] == after
    assert f["errors_after"] == {}
    ver = subprocess.run([str(env.find_kicad_cli()), "version"],
                         capture_output=True, text=True).stdout
    if re.match(r"\d+\.\d+\.\d+", ver).group(0) == RECORDED:
        assert _warnings(payload) == warnings


@pytest.mark.parametrize("case,nets", [
    ("wlcsp49_0.4", 49), ("fbga153_0.5", 153), ("tfbga54_0.8", 54)])
def test_live_fixture_starts_escaped_with_every_net_open(tmp_path, case, nets):
    _need_tools()
    payload = _maze(tmp_path, case, "--measure-only")
    assert payload["facts"]["unconnected_left"] == nets
    assert payload["counts"]["by_severity"].get("error", 0) == nets


def test_live_tfbga54_maze_finishes(tmp_path):
    _need_tools()
    payload = _maze(tmp_path, "tfbga54_0.8", "--no-rip-board")
    assert [p["by_status"] for p in payload["facts"]["passes"]] == [{"routed": 54}]
    _answers(payload, before=54, after=0, warnings={
        "track_dangling": 31, "lib_footprint_issues": 2, "silk_over_copper": 2})


@pytest.mark.smoke
def test_live_wlcsp49_maze_stops_at_7_of_49(tmp_path):
    """Smoke, not test-slow: 9 min on the loaded tower, one run. 85.7 % routed; pass
    3 gains nothing, so the maze stops there. The 7 left are real work for the
    escape or the router, not a goal."""
    _need_tools()
    payload = _maze(tmp_path, "wlcsp49_0.4", "--no-rip-board")
    assert [p["by_status"] for p in payload["facts"]["passes"]] == [
        {"routed": 41, "failed": 8},
        {"routed": 1, "failed": 7},
        {"failed": 7}]
    _answers(payload, before=49, after=7, warnings={
        "track_dangling": 21, "silk_over_copper": 15, "lib_footprint_issues": 2})


@pytest.mark.smoke
def test_live_fbga153_maze_stops_at_62_of_153(tmp_path):
    """Smoke, not test-slow: 55 min on the loaded tower, one run. 59.5 % routed;
    pass 3 gains nothing, so the maze stops there. The 62 left are real work for
    the escape or the router, not a goal."""
    _need_tools()
    payload = _maze(tmp_path, "fbga153_0.5", "--no-rip-board")
    assert [p["by_status"] for p in payload["facts"]["passes"]] == [
        {"routed": 85, "failed": 68},
        {"failed": 62, "routed": 6},
        {"failed": 62}]
    _answers(payload, before=153, after=62, warnings={
        "track_dangling": 122, "lib_footprint_issues": 2, "silk_over_copper": 2})


def _stub_board(tracks):
    """A stand-in for BoardGeom with just what stub_exits reads."""
    from shapely.geometry import LineString, box
    NS = SimpleNamespace
    return NS(
        tracks_of=lambda: [NS(net=net, layer=layer, shape=LineString(pts))
                           for net, layer, pts in tracks],
        vias_of=lambda: [NS(net="/V", at=(5.0, 1.0))],
        pads_of=lambda: [NS(net="/A", poly=box(-.1, -.1, .1, .1)),
                         NS(net="/B", poly=box(.4, -.1, .6, .1)),
                         NS(net="/V", poly=box(4.9, -.1, 5.1, .1))])


def test_stub_exits_finds_each_open_end_and_its_heading():
    """#784: stubs ending at one x,y on different layers each get their exit."""
    bg = _stub_board([("/A", "F.Cu", [(0, 0), (0, 1)]),
                      ("/B", "In1.Cu", [(.5, 0), (.5, 1)]),
                      ("/B", "In1.Cu", [(.5, 1), (1.5, 1)]),
                      ("/V", "F.Cu", [(5, 0), (5, 1)])])
    assert rf.stub_exits(bg) == {
        "/A": [("F.Cu", (0, 1), (0, 1))],
        "/B": [("In1.Cu", (1.5, 1), (1, 0))]}


def test_exit_keepouts_hold_only_other_nets_open_ends():
    """#784: a corridor past each open tip, on the tip's own layer, that a net's
    own route ignores because it is that net's copper."""
    exits = {"/A": [("F.Cu", (0, 1), (0, 1))], "/B": [("In1.Cu", (2, 1), (1, 0))]}
    items = rf.exit_keepouts(exits, open_nets=["/A", "/B"], routing="/A",
                             length=1.0, half=lambda net: 0.05)
    assert [(i.net, set(i.layers), list(i.shape.coords), i.half) for i in items] == [
        ("/B", {"In1.Cu"}, [(2, 1), (3, 1)], 0.05)]
    assert rf.exit_keepouts(exits, open_nets=[], routing="/A",
                            length=1.0, half=lambda net: 0.05) == []


def _small_grid(walls, **kw):
    """Two layers, 5 x 3 cells. Side A is the left column of layer 0, side B
    the right column of layer 1. walls: per layer, the columns blocked."""
    nx, ny = 5, 3

    def cells(pick):
        return bytes(int(pick(c % nx)) for c in range(nx * ny))

    empty = bytes(nx * ny)
    return rf.Grid(0.0, 0.0, nx, ny, 0.1, ["F.Cu", "B.Cu"],
                   [cells(lambda i: i in w) for w in walls],
                   kw.get("vblk", empty), (0.6, 0.3),
                   [[1.0] * (nx * ny)] * 2,
                   [cells(lambda i: i == 0), empty],
                   [empty, cells(lambda i: i == nx - 1)],
                   empty, empty, kw.get("span_moves", []))


@pytest.mark.parametrize("walls,kw,refuted", [
    (({2}, set()), {}, False),                         # a via past the wall
    (({2}, set()), {"vblk": bytes([1]) * 15}, True),   # no via fits anywhere
    (({2}, {2}), {}, True),                            # both layers walled
    (({0}, set()), {}, True),                          # side A is covered
])
def test_no_path_refutes_only_what_astar_cannot_route(walls, kw, refuted):
    """#784: the check before the search. It must agree with the search, or the
    maze would give up on a route it could have found."""
    gr = _small_grid(walls, **kw)
    assert rf.no_path(gr) is refuted
    assert (rf.astar(gr, (4, 1), via_cost=2.0) is None) is refuted


def test_no_path_follows_an_hdi_span():
    """#784: a span is a way between layers where a through via has no room."""
    span = SimpleNamespace(kind="micro")
    gr = _small_grid(({2}, set()), vblk=bytes([1]) * 15, span_moves=[
        [(1, span, bytes(15), [0, 1])], [(0, span, bytes(15), [0, 1])]])
    assert rf.no_path(gr) is False
    assert rf.astar(gr, (4, 1), via_cost=2.0)


def test_too_close_via_names_a_route_crowding_its_own_vias():
    """#784: TFBGA-54's /S6_7 took a microvia and a buried via 0.14 mm apart, and
    DRC rolled the whole route back on every pass."""
    span = SimpleNamespace(kind="micro", drill=0.1)
    # no span back to layer 0 at (1, 0), so merge_close_vias can't move the run
    no_move = bytes(1 if c == 1 else 0 for c in range(15))
    gr = _small_grid((set(), set()), span_moves=[
        [(1, span, bytes(15), [0, 1])], [(0, span, no_move, [0, 1])]])
    rules = SimpleNamespace(spans=[span], span_hole_to_hole=0.24, margin=0.02,
                            via_floor=(0.45, 0.2))
    near = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (2, 1, 1), (2, 1, 0), (4, 1, 0)]
    assert rf.too_close_via(gr, near, rules) == (1, 7)
    far = [(0, 0, 0), (0, 0, 1), (4, 1, 1), (4, 1, 0)]
    assert rf.too_close_via(gr, far, rules) is None
    stacked = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 0, 0), (4, 1, 0)]
    assert rf.too_close_via(gr, stacked, rules) is None


def _ring_job(net, dist):
    return {"net": net, "dist": dist}


def test_outer_first_takes_a_chips_outer_ring_before_its_inner_one():
    """#823: shortest first left the far rows' stub tips walled in. Corners and
    edges of a ball field go first, then inwards, shortest first on a tie."""
    exits = {f"/{n}": [("F.Cu", at, (0.0, 1.0))] for n, at in {
        "corner": (0.0, 0.0), "edge": (1.0, 0.0), "far": (2.0, 0.0),
        "left": (0.0, 1.0), "mid": (1.0, 1.0), "right": (2.0, 1.0),
        "b0": (0.0, 2.0), "b1": (1.0, 2.0), "b2": (2.0, 2.0)}.items()}
    jobs = [_ring_job("/mid", 1.0), _ring_job("/edge", 2.0),
            _ring_job("/corner", 9.0), _ring_job("/far", 8.0),
            _ring_job("/b0", 7.0), _ring_job("/b2", 6.0),
            _ring_job("/left", 5.0), _ring_job("/right", 4.0),
            _ring_job("/b1", 3.0)]
    assert [j["net"] for j in rf.outer_first(jobs, exits)] == [
        "/b2", "/b0", "/far", "/corner",
        "/edge", "/b1", "/right", "/left", "/mid"]


def test_outer_first_ranks_each_chip_from_its_own_centre():
    """A second chip's tips don't pull the first one's centre away."""
    exits = {"/a_in": [("F.Cu", (0.0, 0.0), (0.0, 1.0))],
             "/a_out": [("F.Cu", (1.0, 0.0), (0.0, 1.0))],
             "/a_out2": [("F.Cu", (-1.0, 0.0), (0.0, 1.0))],
             "/b_in": [("F.Cu", (50.0, 0.0), (0.0, 1.0))],
             "/b_out": [("F.Cu", (51.0, 0.0), (0.0, 1.0))],
             "/b_out2": [("F.Cu", (49.0, 0.0), (0.0, 1.0))]}
    order = [j["net"] for j in rf.outer_first(
        [_ring_job(n, 1.0) for n in exits], exits)]
    assert order.index("/a_out") < order.index("/a_in")
    assert order.index("/b_out") < order.index("/b_in")
    assert order.index("/b_out2") < order.index("/b_in")


def test_outer_first_leaves_nets_with_no_open_tip_after_in_shortest_order():
    exits = {"/t": [("F.Cu", (0.0, 0.0), (0.0, 1.0))]}
    jobs = [_ring_job("/long", 9.0), _ring_job("/t", 5.0),
            _ring_job("/short", 1.0)]
    assert [j["net"] for j in rf.outer_first(jobs, exits)] == [
        "/t", "/short", "/long"]
