"""plane_repair tests (S11, SPEC P7.4).

Pure tests (no toolchain): component/anchor analysis, electrical grouping,
grid pathfinding + simplification on synthetic scenes, detection + repair
PLANNING on synthetic .kicad_pcb files (geom parses them without KiCad),
and the rf4 plane-split mutant detection truth:
  - the mutant's In1.Cu GND fill is ONE component (the keepout slot does NOT
    fully bisect the plane) -> healthy, slot visible as reduced fill area;
  - GND on F.Cu is 2 components on BOTH golden and mutant rf4, stitched
    through the intact inner planes -> healthy fact, never a violation.

Smoke tests (live KiCad 10.0.3 via env.py; marked `smoke`): a reproducible
split fixture = golden blinky2 + a full-width foreign +3V3 track across the
B.Cu GND pour at y=112.4 + refill (splits the pour into two via-anchored,
electrically separate halves; kicad-cli DRC agrees with 1 unconnected GND
item), then flag-only (exit 1, no writes) and --repair (exit 0, groups
merge, DRC unconnected -> 0, no new DRC errors, idempotent re-run).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Point, box
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden"
MUTANT = GOLDEN / "mutants" / "plane-split-under-clock"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import geom  # noqa: E402
import plane_repair as pr  # noqa: E402

PYTHON = str(REPO / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))
if not Path(PYTHON).exists():   # a checkout with no .venv (the landing gate): the venv running pytest
    PYTHON = sys.executable
SCRIPT = str(SCRIPTS / "plane_repair.py")


def run_cli(*args):
    cp = subprocess.run([PYTHON, SCRIPT, *args], capture_output=True,
                        text=True, encoding="utf-8", errors="replace",
                        timeout=600)
    return cp


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ============================================================ pure: analysis

def test_fill_components_sorted_and_merged():
    small = box(0, 0, 1, 1)
    big = box(5, 0, 9, 4)
    touch_a = box(20, 0, 21, 1)
    touch_b = box(20.5, 0, 22, 1)   # overlaps touch_a -> one component
    comps = pr.fill_components(MultiPolygon([small, big, touch_a, touch_b]))
    assert len(comps) == 3
    assert comps[0].area == pytest.approx(16.0)       # largest first
    assert comps[1].area == pytest.approx(2.0)        # merged pair
    assert comps[2].area == pytest.approx(1.0)
    # single polygon input
    assert len(pr.fill_components(big)) == 1


def test_connectivity_groups_via_stitching():
    # two B.Cu islands + one F.Cu plane; a via under each island joins both
    # to the F.Cu plane -> ONE electrical group (the rf4-F.Cu situation).
    layer_comps = {"B.Cu": [box(0, 0, 4, 4), box(10, 0, 14, 4)],
                   "F.Cu": [box(0, 0, 14, 4)]}
    vias = [((2, 2), ("F.Cu", "B.Cu")), ((12, 2), ("F.Cu", "B.Cu"))]
    groups = pr.connectivity_groups(layer_comps, vias)
    assert groups[("B.Cu", 0)] == groups[("B.Cu", 1)] == groups[("F.Cu", 0)]
    # without the second via the islands are separate groups
    groups2 = pr.connectivity_groups(layer_comps, vias[:1])
    assert groups2[("B.Cu", 0)] != groups2[("B.Cu", 1)]


def test_erode_terminal_adaptive():
    fat = box(0, 0, 10, 10)
    e = pr.erode_terminal(fat)
    assert e.bounds == pytest.approx((0.3, 0.3, 9.7, 9.7))
    thin = box(0, 0, 10, 0.2)      # track-like: 0.3 erosion would vanish
    e2 = pr.erode_terminal(thin)
    assert not e2.is_empty
    assert thin.covers(e2)


# ============================================================ pure: pathfinder

def _scene(foreign=None, keepouts=None, own=None, **kw):
    return pr.PathScene(box(0, 0, 20, 10), foreign, keepouts=keepouts,
                        own=own, **kw)


def _assert_path_legal(scene, pts):
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        import math
        step_len = math.hypot(b[0] - a[0], b[1] - a[1])
        ok = scene.corridor_clear(a, b)
        # single grid steps are margin-guaranteed and may skip the exact check
        assert ok or step_len <= scene.step * 1.5, (a, b)


def test_scene_straight_route_simplifies():
    scene = _scene()
    pts = scene.route_between(Point(2, 5).buffer(0.3), Point(18, 5).buffer(0.3))
    assert pts is not None
    assert len(pts) == 2                       # collinear -> one segment
    assert scene.corridor_clear(pts[0], pts[-1])


def test_scene_routes_around_obstacle():
    # vertical foreign bar with a gap at the top
    foreign = box(9.8, 0, 10.2, 7)
    scene = _scene(foreign=foreign)
    pts = scene.route_between(Point(2, 5).buffer(0.3), Point(18, 5).buffer(0.3))
    assert pts is not None
    assert max(y for _, y in pts) > 7          # detours above the bar
    _assert_path_legal(scene, pts)


def test_scene_blocked_returns_none():
    foreign = box(9.8, -1, 10.2, 11)           # full wall
    scene = _scene(foreign=foreign)
    assert scene.route_between(Point(2, 5).buffer(0.3),
                               Point(18, 5).buffer(0.3)) is None


def test_scene_avoids_keepout_and_full_keepout_blocks():
    ko = box(9.5, 0, 10.5, 7)
    scene = _scene(keepouts=ko)
    pts = scene.route_between(Point(2, 5).buffer(0.3), Point(18, 5).buffer(0.3))
    assert pts is not None
    _assert_path_legal(scene, pts)
    for i in range(len(pts) - 1):
        from shapely.geometry import LineString
        seg = LineString([pts[i], pts[i + 1]]).buffer(scene.width / 2.0)
        assert not seg.intersects(ko)
    full = box(9.5, -1, 10.5, 11)
    assert _scene(keepouts=full).route_between(
        Point(2, 5).buffer(0.3), Point(18, 5).buffer(0.3)) is None


def test_scene_own_copper_exempts_outline_not_foreign():
    # own fill hugging the outline: cells on it are free (outline inset
    # exempt); but own copper near foreign stays blocked (clearance wins).
    own = box(0.1, 0.1, 20, 1.0)   # entirely outside the 0.55 outline inset
    scene = _scene(own=own)
    assert scene.cells_on(own)     # still free cells there
    foreign = box(0, 2, 20, 2.2)
    scene2 = _scene(own=box(0.1, 0.1, 20, 2.0), foreign=foreign)
    # own cell within clearance of foreign must NOT be free
    on_edge = [c for c in scene2.cells_on(box(0.1, 1.6, 20, 2.0))]
    assert not on_edge


def test_scene_deterministic():
    foreign = unary_union([box(9.8, 0, 10.2, 7), box(4, 3, 5, 4)])
    a = _scene(foreign=foreign).route_between(Point(2, 5).buffer(0.3),
                                              Point(18, 5).buffer(0.3))
    b = _scene(foreign=foreign).route_between(Point(2, 5).buffer(0.3),
                                              Point(18, 5).buffer(0.3))
    assert a == b


def test_track_ops_shape():
    ops = pr.track_ops([(1, 1), (5, 1), (5, 5)], "GND", "B.Cu", 0.5)
    assert [o["op"] for o in ops] == ["add_track", "add_track"]
    assert ops[0]["start"] == [1, 1] and ops[0]["end"] == [5, 1]
    assert ops[1]["layer"] == "B.Cu" and ops[1]["net"] == "GND"
    assert all(o["width"] == 0.5 for o in ops)


def test_iter_via_spots_deterministic_and_legal():
    comp = box(0, 0, 5, 5)
    blocked = box(0, 0, 5, 2.4)      # lower part illegal
    spots = pr.iter_via_spots(comp, blocked, box(-1, -1, 21, 11),
                              toward=(10, 0))
    assert spots
    for s in spots:
        assert comp.buffer(-pr.VIA_SIZE / 2.0).covers(Point(s))
        assert not blocked.covers(Point(s))
    assert spots == pr.iter_via_spots(comp, blocked, box(-1, -1, 21, 11),
                                      toward=(10, 0))
    # nearest-to-toward first
    assert spots[0][0] >= spots[-1][0]


# ============================================================ pure: synthetic boards

def _synth_pcb(tmp_path, name, body, w=20.0, h=10.0):
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end {w} {h}) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def _fill_zone(net, layer, x1, y1, x2, y2):
    pts = f"(xy {x1} {y1}) (xy {x2} {y1}) (xy {x2} {y2}) (xy {x1} {y2})"
    return (f'  (zone (net "{net}") (layer "{layer}")\n'
            f'    (polygon (pts {pts}))\n'
            f'    (filled_polygon (layer "{layer}") (pts {pts})))\n')


def _via(net, x, y):
    return (f'  (via (at {x} {y}) (size 0.6) (drill 0.3)'
            f' (layers "F.Cu" "B.Cu") (net "{net}"))\n')


def _segment(net, layer, x1, y1, x2, y2, w=0.2):
    return (f'  (segment (start {x1} {y1}) (end {x2} {y2}) (width {w})'
            f' (layer "{layer}") (net "{net}"))\n')


def _bpad(ref, net, x, y):
    """Footprint with one B.Cu-only pad (anchor without F.Cu copper)."""
    return (f'  (footprint "t:{ref}" (layer "B.Cu")\n'
            f'    (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 0 0))\n'
            f'    (pad "1" smd rect (at 0 0) (size 0.8 0.8)'
            f' (layers "B.Cu") (net "{net}")))\n')


def _keepout(layer, x1, y1, x2, y2, name="ko"):
    pts = f"(xy {x1} {y1}) (xy {x2} {y1}) (xy {x2} {y2}) (xy {x1} {y2})"
    return (f'  (zone (net "") (layer "{layer}") (name "{name}")\n'
            f'    (keepout (tracks not_allowed) (copperpour not_allowed))\n'
            f'    (polygon (pts {pts})))\n')


# two B.Cu fill islands, one GND via in each; +3V3 wall between them
def _split_board(tmp_path, wall_body, extra=""):
    body = (_fill_zone("GND", "B.Cu", 1, 1, 8, 9)
            + _fill_zone("GND", "B.Cu", 12, 1, 19, 9)
            + _via("GND", 4, 5) + _via("GND", 16, 5)
            + wall_body + extra)
    return _synth_pcb(tmp_path, "synth", body)


def test_detect_split_on_synthetic(tmp_path):
    # partial wall -> still TWO electrically separate anchored components
    pcb = _split_board(tmp_path, _segment("+3V3", "B.Cu", 10, 0, 10, 7))
    bg = geom.BoardGeom.from_file(pcb)
    planes = pr.analyze_board(bg)
    assert len(planes) == 1
    st = planes[0]
    assert (st["net"], st["layer"]) == ("GND", "B.Cu")
    assert st["components"] == 2 and st["anchored_components"] == 2
    assert st["groups"] == 2 and st["split"] is True
    assert st["component_facts"][0]["area_mm2"] >= \
        st["component_facts"][1]["area_mm2"]


def test_detect_stitched_elsewhere_not_split(tmp_path):
    # same two islands, but a GND F.Cu track joins the two via positions:
    # 2 components, 1 electrical group -> healthy (the rf4-F.Cu situation)
    pcb = _split_board(tmp_path, _segment("+3V3", "B.Cu", 10, 0, 10, 7),
                       extra=_segment("GND", "F.Cu", 4, 5, 16, 5))
    bg = geom.BoardGeom.from_file(pcb)
    st = pr.analyze_board(bg)[0]
    assert st["components"] == 2 and st["groups"] == 1
    assert st["split"] is False and st["stitched_elsewhere"] is True


def test_detect_dead_island_is_fact_not_violation(tmp_path):
    # second island has NO anchor -> dead copper, healthy report
    body = (_fill_zone("GND", "B.Cu", 1, 1, 8, 9)
            + _fill_zone("GND", "B.Cu", 12, 1, 19, 9)
            + _via("GND", 4, 5))
    pcb = _synth_pcb(tmp_path, "dead", body)
    st = pr.analyze_board(geom.BoardGeom.from_file(pcb))[0]
    assert st["split"] is False
    assert st["anchored_components"] == 1
    assert len(st["dead_islands"]) == 1
    assert st["dead_islands"][0]["area_mm2"] == pytest.approx(56.0, abs=0.1)


def test_flag_only_cli_on_synthetic(tmp_path):
    pcb = _split_board(tmp_path, _segment("+3V3", "B.Cu", 10, 0, 10, 7))
    rep = tmp_path / "r.json"
    before = sha(pcb)
    cp = run_cli("--pcb", str(pcb), "--flag-only", "--out-report", str(rep))
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert sha(pcb) == before                     # never writes
    r = json.loads(rep.read_text(encoding="utf-8"))
    assert r["status"] == "violations"
    v = r["violations"][0]
    assert v["check"] == "plane_repair" and v["kind"] == "plane_split"
    assert v["source"] == "check.plane_repair"
    assert v["severity"] == "error"
    assert v["net"] == "GND" and v["layer"] == "B.Cu"
    assert 8 <= v["pos"][0] <= 12                 # in the gap
    assert set(v) >= {"check", "severity", "pos", "layer", "net", "refs",
                      "msg", "source", "items"}


def test_cli_errors_exit_2(tmp_path):
    cp = run_cli("--pcb", str(tmp_path / "nope.kicad_pcb"), "--flag-only")
    assert cp.returncode == 2
    assert json.loads(cp.stdout)["status"] == "error"
    pcb = _split_board(tmp_path, "")
    cp2 = run_cli("--pcb", str(pcb), "--net", "NOSUCH", "--flag-only")
    assert cp2.returncode == 2


def test_plan_same_layer_track_bridge(tmp_path):
    # partial wall leaves a corridor at the top -> same-layer track plan
    pcb = _split_board(tmp_path, _segment("+3V3", "B.Cu", 10, 0, 10, 7))
    bg = geom.BoardGeom.from_file(pcb)
    st = pr.analyze_board(bg)[0]
    plan = pr.plan_bridge(bg, st)
    assert plan is not None and plan["method"] == "track"
    assert plan["layer"] == "B.Cu"
    assert all(o["op"] == "add_track" for o in plan["ops"])
    # path stays clear of the wall track (with clearance)
    wall = geom.BoardGeom.from_file(pcb).tracks_of(net="+3V3")[0].poly
    from shapely.geometry import LineString
    for o in plan["ops"]:
        seg = LineString([o["start"], o["end"]]).buffer(o["width"] / 2 + 0.19)
        assert not seg.intersects(wall)
    # deterministic
    assert pr.plan_bridge(bg, st) == plan


def test_plan_via_jumper_when_wall_spans_board(tmp_path):
    # full wall, anchors are B.Cu-only pads (no F.Cu group copper) ->
    # same-layer AND other-layer track bridges impossible -> via jumper
    body = (_fill_zone("GND", "B.Cu", 1, 1, 8, 9)
            + _fill_zone("GND", "B.Cu", 12, 1, 19, 9)
            + _bpad("P1", "GND", 4, 5) + _bpad("P2", "GND", 16, 5)
            + _segment("+3V3", "B.Cu", 10, -1, 10, 11))
    pcb = _synth_pcb(tmp_path, "jump", body)
    bg = geom.BoardGeom.from_file(pcb)
    st = pr.analyze_board(bg)[0]
    assert st["split"] is True
    plan = pr.plan_bridge(bg, st)
    assert plan is not None and plan["method"] == "via_jumper"
    assert plan["via_layer"] == "F.Cu"
    vias = [o for o in plan["ops"] if o["op"] == "add_via"]
    assert len(vias) == 2
    comps = pr.fill_components(bg.zone_fill("GND", "B.Cu"))
    hulls = unary_union(comps)
    for v in vias:
        assert hulls.covers(Point(v["at"]))       # one via inside each fill
        assert v["size"] == pr.VIA_SIZE and v["drill"] == pr.VIA_DRILL
    tracks = [o for o in plan["ops"] if o["op"] == "add_track"]
    assert tracks and all(o["layer"] == "F.Cu" for o in tracks)


def test_plan_routes_around_partial_keepout(tmp_path):
    # keepout slot blocks the middle; same-layer path must go around it
    body = (_fill_zone("GND", "B.Cu", 1, 1, 8, 9)
            + _fill_zone("GND", "B.Cu", 12, 1, 19, 9)
            + _via("GND", 4, 5) + _via("GND", 16, 5)
            + _keepout("B.Cu", 9.5, 0, 10.5, 7))
    pcb = _synth_pcb(tmp_path, "koPart", body)
    bg = geom.BoardGeom.from_file(pcb)
    st = pr.analyze_board(bg)[0]
    plan = pr.plan_bridge(bg, st)
    assert plan is not None and plan["method"] == "track"
    ko = box(9.5, 0, 10.5, 7)
    from shapely.geometry import LineString
    for o in plan["ops"]:
        seg = LineString([o["start"], o["end"]]).buffer(o["width"] / 2)
        assert not seg.intersects(ko)


def test_plan_jumper_when_keepout_spans_plane(tmp_path):
    # keepout slot across the WHOLE plane (task: rf4-slot scenario) and
    # B.Cu-only anchors -> two-via jumper on the other layer is the repair
    body = (_fill_zone("GND", "B.Cu", 1, 1, 8, 9)
            + _fill_zone("GND", "B.Cu", 12, 1, 19, 9)
            + _bpad("P1", "GND", 4, 5) + _bpad("P2", "GND", 16, 5)
            + _keepout("B.Cu", 9.5, -1, 10.5, 11))
    pcb = _synth_pcb(tmp_path, "koFull", body)
    bg = geom.BoardGeom.from_file(pcb)
    st = pr.analyze_board(bg)[0]
    assert st["split"] is True
    plan = pr.plan_bridge(bg, st)
    assert plan is not None and plan["method"] == "via_jumper"
    # via spots respect the keepout (barrel spans all layers)
    for o in plan["ops"]:
        if o["op"] == "add_via":
            assert not box(9.5, -1, 10.5, 11).buffer(pr.VIA_SIZE / 2).covers(
                Point(o["at"]))


# ============================================================ rf4 mutant truth

def test_rf4_mutant_detection_truth():
    """The plane-split mutant's In1.Cu fill is NOT split (the keepout slot
    does not fully bisect it) - detection must truthfully report healthy,
    with the slot visible as reduced fill area vs golden. F.Cu's two GND
    pours are stitched through the inner planes - a fact, not a violation."""
    mut = geom.BoardGeom.from_file(MUTANT / "rf4.kicad_pcb")
    gold = geom.BoardGeom.from_file(GOLDEN / "rf4" / "rf4.kicad_pcb")

    # the mutant carries the slot keepout on In1.Cu
    names = [(ra["name"], ra["layers"]) for ra in mut.rule_areas]
    assert ("mutant-plane-split", ("In1.Cu",)) in names

    planes = pr.analyze_board(mut)
    by = {(p["net"], p["layer"]): p for p in planes}
    in1 = by[("GND", "In1.Cu")]
    assert in1["components"] == 1 and in1["split"] is False
    fcu = by[("GND", "F.Cu")]
    assert fcu["components"] == 2 and fcu["groups"] == 1
    assert fcu["split"] is False and fcu["stitched_elsewhere"] is True
    assert not any(p["split"] for p in planes)

    # slot visible: mutant In1.Cu fill lost ~the keepout area vs golden
    a_mut = mut.zone_fill("GND", "In1.Cu").area
    a_gold = gold.zone_fill("GND", "In1.Cu").area
    assert a_mut < a_gold - 10.0

    # clean golden must also be split-free (no false positives)
    assert not any(p["split"] for p in pr.analyze_board(gold))


# ============================================================ smoke: live fixture

@pytest.fixture(scope="module")
def bisected(tmp_path_factory):
    """Golden blinky2 + full-width +3V3 B.Cu track at y=112.4 + refill:
    a reproducible electrical GND split (verified: kicad-cli DRC reports
    exactly one unconnected GND item on this fixture)."""
    import env
    import kc
    import route_edit
    cli = env.find_kicad_cli()
    if cli is None:
        pytest.skip("kicad-cli not available")
    d = tmp_path_factory.mktemp("bisect")
    for ext in (".kicad_pcb", ".kicad_pro"):
        shutil.copy2(GOLDEN / "blinky2" / f"blinky2{ext}", d / f"blinky2{ext}")
    pcb = d / "blinky2.kicad_pcb"
    route_edit.apply_ops(pcb, [{
        "op": "add_track", "start": [100.0, 112.4], "end": [150.0, 112.4],
        "width": 0.3, "layer": "B.Cu", "net": "+3V3"}])
    kc.run_drc(cli, pcb, refill=True, save_board=True)
    drc = kc.run_drc(cli, pcb, parity=False, all_track_errors=True)
    return {"dir": d, "pcb": pcb, "cli": cli, "drc_before": drc}


def _copy_fixture(src_dir: Path, dst_dir: Path) -> Path:
    for ext in (".kicad_pcb", ".kicad_pro"):
        shutil.copy2(src_dir / f"blinky2{ext}", dst_dir / f"blinky2{ext}")
    return dst_dir / "blinky2.kicad_pcb"


@pytest.mark.smoke
def test_smoke_fixture_is_truly_split(bisected):
    unconnected = [v for v in bisected["drc_before"]["violations"]
                   if v["source"] == "unconnected"]
    assert len(unconnected) == 1
    assert unconnected[0]["net"] == "GND"
    st = [p for p in pr.analyze_board(
        geom.load_board(bisected["pcb"], refresh=True))
        if (p["net"], p["layer"]) == ("GND", "B.Cu")][0]
    assert st["components"] == 2 and st["anchored_components"] == 2
    assert st["groups"] == 2 and st["split"] is True


@pytest.mark.smoke
def test_smoke_flag_only_reports_and_never_writes(bisected, tmp_path):
    pcb = _copy_fixture(bisected["dir"], tmp_path)
    before = sha(pcb)
    rep = tmp_path / "r.json"
    cp = run_cli("--pcb", str(pcb), "--flag-only", "--out-report", str(rep))
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert sha(pcb) == before
    r = json.loads(rep.read_text(encoding="utf-8"))
    v = r["violations"][0]
    assert v["kind"] == "plane_split" and v["net"] == "GND"
    assert v["layer"] == "B.Cu"
    assert abs(v["pos"][1] - 112.4) < 2.0        # gap sits on the cut line


@pytest.mark.smoke
def test_smoke_repair_merges_and_drc_reconnects(bisected, tmp_path):
    import kc
    pcb = _copy_fixture(bisected["dir"], tmp_path)
    rep = tmp_path / "r.json"
    cp = run_cli("--pcb", str(pcb), "--repair", "--out-report", str(rep))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    r = json.loads(rep.read_text(encoding="utf-8"))
    assert r["status"] == "pass"
    assert r["splits_found"] == 1 and r["splits_repaired"] == 1
    assert r["bridges"] and all(b["merged"] for b in r["bridges"])
    for b in r["bridges"]:
        assert b["ops"] and b["length_mm"] > 0
    plane = [p for p in r["planes"]
             if (p["net"], p["layer"]) == ("GND", "B.Cu")][0]
    assert plane["repaired"] is True and plane["groups_after"] == 1

    # board truth: groups merged, DRC unconnected -> 0, no NEW errors
    st = [p for p in pr.analyze_board(geom.load_board(pcb, refresh=True))
          if (p["net"], p["layer"]) == ("GND", "B.Cu")][0]
    assert st["groups"] == 1 and st["split"] is False
    drc = kc.run_drc(bisected["cli"], pcb, parity=False,
                     all_track_errors=True)
    unconnected = [v for v in drc["violations"] if v["source"] == "unconnected"]
    assert len(unconnected) == 0
    before_other = [v for v in bisected["drc_before"]["violations"]
                    if v["source"] != "unconnected"]
    after_other = [v for v in drc["violations"] if v["source"] != "unconnected"]
    assert len(after_other) <= len(before_other)

    # idempotent: a second --repair run finds nothing and changes nothing
    before2 = sha(pcb)
    cp2 = run_cli("--pcb", str(pcb), "--repair")
    assert cp2.returncode == 0
    r2 = json.loads(cp2.stdout)
    assert r2["splits_found"] == 0 and r2["bridges"] == []
    assert sha(pcb) == before2
