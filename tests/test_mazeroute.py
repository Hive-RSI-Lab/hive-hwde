"""mazeroute: route_edit's route / route_group / reroute ops and zone_patch's
corridor mode (#926 #927 #928), on three golden boards that each tool must
route (tests/fixtures/mazeroute, rebuilt by make_fixture.py):

  corridor  a via fence with one gap: the corridor must go through it
  reroute   A is walled off by B's track: reroute rips B, routes A, puts B
            back in one piece
  group     three nets through one gap in a wall: greedy order fails at the
            third, PathFinder routes all three

Pure tests (venv only) drive the router. The live tests apply the ops with
KiCad and want no DRC finding on the routed nets (the boards' GND copper is
bare fence and walls: its own findings are not counted).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import env  # noqa: E402
import geom  # noqa: E402
import mazeroute  # noqa: E402
import route_edit  # noqa: E402
import zone_patch  # noqa: E402
from checklib import CheckError  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "mazeroute"
B_TRACK = "00000000-0000-0000-0000-0000000002b1"
ABC = [{"net": n, "from": f"{n}1.1", "to": f"{n}2.1"} for n in "ABC"]


def _board(tmp_path, name):
    for src in FIX.glob(f"{name}.*"):
        shutil.copy2(src, tmp_path / src.name)
    return tmp_path / f"{name}.kicad_pcb"


def _bg(name):
    return geom.BoardGeom.from_file(FIX / f"{name}.kicad_pcb")


def _tracks(ops, net=None):
    return [o for o in ops if o["op"] == "add_track"
            and (net is None or o["net"] == net)]


def _gap(ops, rules):
    """The least edge-to-edge distance minus clearance between the added
    tracks of different nets (>= 0 when every pair keeps its clearance)."""
    shapes = [(o["net"], o["layer"], LineString([o["start"], o["end"]])
               .buffer(o["width"] / 2)) for o in _tracks(ops)]
    worst = float("inf")
    for i, (na, la, a) in enumerate(shapes):
        for nb, lb, b in shapes[i + 1:]:
            if na != nb and la == lb:
                worst = min(worst, a.distance(b) - rules.clr(na, nb))
    return worst


# ------------------------------------------------------------ ends

def test_end_specs():
    r = mazeroute.Router(_bg("group"))
    assert set(r.end("A1.1", "A")) == {"F.Cu"}
    assert set(r.end("A1", "A")) == {"F.Cu"}
    assert set(r.end("3,4@B.Cu", "A")) == {"B.Cu"}
    assert set(r.end("3,4", "A")) == set(r.layers)
    assert len(r.end("A1.1|A2.1", "A")["F.Cu"]) == 2


@pytest.mark.parametrize("spec, msg", [
    ("Z9.1", "no such pad"),
    ("B1.1", "is on net 'B'"),
    ("B1", "no pad of B1 on 'A'"),
    ("3,4@In1.Cu", "not a routable layer"),
    ("3,x", "want x,y"),
    ("", "names nothing"),
])
def test_end_refuses(spec, msg):
    with pytest.raises(CheckError, match=msg):
        mazeroute.Router(_bg("group")).end(spec, "A")


# ------------------------------------------------------------ corridor (#926)

def test_corridor_goes_through_the_gap():
    bg = _bg("corridor")
    polys, ops = mazeroute.Router(bg, layers=["F.Cu"]).corridor(
        "A", "P1.1", "P2.1", 0.5)
    poly = polys["F.Cu"]
    assert poly.geom_type == "Polygon"
    for pad in bg.pads_of(net="A"):             # it lands on both pads
        assert poly.intersection(pad.poly).area > 0.1, pad.ref
    # it crosses the fence only in the gap between the vias at y=4.8, 7.2
    cut = poly.intersection(LineString([(10, 0), (10, 10)]))
    assert 5.1 < cut.bounds[1] and cut.bounds[3] < 6.9
    for v in bg.vias_of(net="GND"):
        assert poly.distance(v.poly) > 0.1, v.at
    assert all(o["op"] == "add_track" for o in ops)


def test_zone_patch_corridor_dry_run(tmp_path):
    pcb = _board(tmp_path, "corridor")
    raw = pcb.read_bytes()
    p, _ = zone_patch.run(["--pcb", str(pcb), "--net", "A", "--from", "P1.1",
                           "--to", "P2.1", "--half-width", "0.5",
                           "--dry-run"])
    assert p["status"] == "pass" and p["corridor"] == ["P1.1", "P2.1"]
    xs = [x for x, _ in p["outline"]]
    assert min(xs) < 3 and max(xs) > 17
    assert pcb.read_bytes() == raw


@pytest.mark.parametrize("args, msg", [
    (["--from", "P1.1"], "both --from and --to"),
    (["--from", "P1.1", "--to", "P2.1", "--poly", "0,0 1,0 1,1"],
     "no --poly"),
    (["--from", "P1.1", "--to", "P2.1", "--half-width", "0"], "> 0"),
])
def test_zone_patch_corridor_refuses(tmp_path, args, msg):
    pcb = _board(tmp_path, "corridor")
    with pytest.raises(CheckError, match=msg):
        zone_patch.run(["--pcb", str(pcb), "--net", "A", *args])


# ------------------------------------------------------------ reroute (#927)

def test_plain_route_is_walled_off():
    with pytest.raises(CheckError):
        mazeroute.Router(_bg("reroute")).route("A", "A1.1", "A2.1")


def test_reroute_rips_b_and_puts_it_back_whole():
    r = mazeroute.Router(_bg("reroute"))
    assert len(r.islands("B")) == 1
    removed, ops, _ = r.reroute("A", [14, 14, 16, 16], "A1.1", "A2.1")
    assert removed == [B_TRACK]                # never the GND walls
    assert _tracks(ops, "A") and _tracks(ops, "B")
    assert len(r.islands("A")) == 1 and len(r.islands("B")) == 1
    assert _gap(ops, r.rules) >= -1e-6


def test_reroute_keep_and_one_layer_refuse():
    with pytest.raises(CheckError):
        mazeroute.Router(_bg("reroute")).reroute(
            "A", [14, 14, 16, 16], "A1.1", "A2.1", keep={"B"})
    with pytest.raises(CheckError):
        mazeroute.Router(_bg("reroute"), layers=["F.Cu"]).reroute(
            "A", [14, 14, 16, 16], "A1.1", "A2.1")


def test_reroute_that_fails_leaves_the_board_byte_identical(tmp_path):
    """The router fails in resolve_ops, before KiCad is started."""
    pcb = _board(tmp_path, "reroute")
    raw = pcb.read_bytes()
    with pytest.raises(CheckError):
        route_edit.apply_ops(pcb, route_edit.validate_ops({"version": 1, "ops": [
            {"op": "reroute", "net": "A", "region": [14, 14, 16, 16],
             "from": "A1.1", "to": "A2.1", "layers": ["F.Cu"]}]}))
    assert pcb.read_bytes() == raw


def test_resolve_reroute_writes_guards():
    guards: dict = {}
    flat, spans = route_edit.resolve_ops(_bg("reroute"), [
        {"op": "reroute", "net": "A", "region": [14, 14, 16, 16]}], guards)
    assert guards == {"A": "lt", "B": "le"}
    assert flat[0] == {"op": "remove", "uuid": B_TRACK}
    assert spans == [list(range(len(flat)))]


def test_check_guards():
    bg = _bg("reroute")
    assert route_edit.check_guards(bg, bg, {"B": "le"}) == []
    assert route_edit.check_guards(bg, bg, {"A": "lt"}) == ["A: 2 -> 2 islands"]


# ------------------------------------------------------------ group (#928)

def test_greedy_fails_where_the_group_routes():
    bg = _bg("group")
    r = mazeroute.Router(bg, layers=["F.Cu"])
    with pytest.raises(CheckError):
        for jb in ABC:
            r.route(jb["net"], jb["from"], jb["to"])
    r = mazeroute.Router(bg, layers=["F.Cu"])
    ops = r.group(ABC)
    assert {o["net"] for o in _tracks(ops)} == {"A", "B", "C"}
    assert _gap(ops, r.rules) >= -1e-6
    for n in "ABC":
        assert len(r.islands(n)) == 1, n


def test_ops_in_one_list_route_around_each_other():
    """A route op after a route_group sees the group's tracks."""
    flat, spans = route_edit.resolve_ops(_bg("group"), [
        {"op": "route_group", "jobs": ABC[:2], "layers": ["F.Cu"]},
        {"op": "route", "net": "C", "from": "C1.1", "to": "C2.1",
         "layers": ["F.Cu"]}])
    rules = mazeroute.Router(_bg("group")).rules
    assert _gap(flat, rules) >= -1e-6
    assert {flat[j]["net"] for j in spans[1]} == {"C"}


# ------------------------------------------------------------ validation

@pytest.mark.parametrize("op, msg", [
    ({"op": "route", "net": "A", "from": "A1.1"}, "missing"),
    ({"op": "route", "net": "A", "from": "A1.1", "to": "A2.1", "grid": 2},
     "grid must be"),
    ({"op": "route", "net": "A", "from": "A1.1", "to": "A2.1", "layers": 3},
     "layers must be"),
    ({"op": "route_group", "jobs": []}, "non-empty list"),
    ({"op": "route_group", "jobs": [{"net": "A", "from": "A1.1"}]},
     "needs net, from, to"),
    ({"op": "route_group", "jobs": [dict(ABC[0], via=1)]}, "unknown keys"),
    ({"op": "route_group", "jobs": [dict(ABC[0], width=0)]}, "width must"),
    ({"op": "route_group", "jobs": ABC, "iterations": 0}, "iterations"),
    ({"op": "reroute", "net": "A", "region": [5, 5, 1, 1]}, "region must"),
    ({"op": "reroute", "net": "A", "region": [0, 0, 1, 1], "from": "A1.1"},
     "from and to together"),
    ({"op": "reroute", "net": "A", "region": [0, 0, 1, 1], "keep": [1]},
     "keep must"),
])
def test_validate_refuses(op, msg):
    with pytest.raises(CheckError, match=msg):
        route_edit.validate_ops({"version": 1, "ops": [op]})


def test_apply_refuses_unknown_net_and_layer(tmp_path):
    pcb = _board(tmp_path, "group")
    with pytest.raises(CheckError, match="net 'Z' not on board"):
        route_edit.apply_ops(pcb, [{"op": "route_group", "jobs": [
            {"net": "Z", "from": "A1.1", "to": "A2.1"}]}])
    with pytest.raises(CheckError, match="In1.Cu"):
        route_edit.apply_ops(pcb, [dict(ABC[0], op="route",
                                        layers=["In1.Cu"])])


# ------------------------------------------------------------ live (KiCad)

@pytest.fixture
def kicad():
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("KiCad bundled python not installed")
    return cli


def _signal_drc(cli, pcb) -> list:
    """DRC findings on the signal nets (GND is bare fence and walls) and
    none of the fixture's stand-in footprint library warnings."""
    import kc
    return [v for v in kc.run_drc(cli, pcb)["violations"]
            if v.get("net") != "GND" and v["check"] != "lib_footprint_issues"]


def test_live_route_group_drc_clean(kicad, tmp_path):
    pcb = _board(tmp_path, "group")
    assert len(_signal_drc(kicad, pcb)) == 3       # A, B, C unconnected
    payload, _ = route_edit.run([
        "--pcb", str(pcb), "--op",
        json.dumps({"op": "route_group", "jobs": ABC, "layers": ["F.Cu"]})])
    assert payload["results"][0]["status"] == "added"
    assert _signal_drc(kicad, pcb) == []


def test_live_reroute_drc_clean(kicad, tmp_path):
    pcb = _board(tmp_path, "reroute")
    payload, _ = route_edit.run([
        "--pcb", str(pcb), "--op",
        json.dumps({"op": "reroute", "net": "A", "region": [14, 14, 16, 16],
                    "from": "A1.1", "to": "A2.1"})])
    assert payload["results"][0]["status"] == "rerouted"
    assert B_TRACK not in pcb.read_text(encoding="utf-8")
    assert _signal_drc(kicad, pcb) == []


def test_live_route_and_corridor_drc_clean(kicad, tmp_path):
    pcb = _board(tmp_path, "corridor")
    route_edit.run(["--pcb", str(pcb), "--op", json.dumps(
        {"op": "route", "net": "A", "from": "P1.1", "to": "P2.1"})])
    assert _signal_drc(kicad, pcb) == []

    (tmp_path / "z").mkdir()
    pcb = _board(tmp_path / "z", "corridor")
    p, _ = zone_patch.run(["--pcb", str(pcb), "--net", "A", "--from", "P1.1",
                           "--to", "P2.1", "--half-width", "0.5"])
    assert p["status"] == "pass" and p["fill_mm2"] > 10
    assert _signal_drc(kicad, pcb) == []
