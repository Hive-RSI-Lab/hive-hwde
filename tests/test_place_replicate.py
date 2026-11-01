"""place_replicate / replicatelib (#482): stamp a repeated channel's
placement and routing into its other instances.

Fixtures (tests/fixtures/replicate, built by build.sh from gen_fixture.py):
ok/ = one sheet file instanced three times (ch1..ch3: R1 C1 R2 C2) + J1,
channel 1 placed and routed (incl. a B.Cu GND hop through two vias),
channels 2 and 3 where board_init packed them. mutant/ = channel 3's sheet
has an extra C3. Known answers are computed here from the source channel
and the declared transform, never from replicatelib.
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import constraints_lint  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import placelib  # noqa: E402
import replicatelib as rl  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "replicate"
SRC = ["C101", "C102", "R101", "R102"]
TOL = 1e-3  # 1 um


def _cons(**ch) -> dict:
    base = {"name": "rc", "source": "ch1", "targets": ["ch2", "ch3"],
            "transform": "grid", "pitch": [0, 10]}
    base.update(ch)
    return {"channels": [base]}


def _xf(p, dx, dy, rot=0.0, origin=(0.0, 0.0)):
    """Independent of replicatelib: KiCad's y-down frame, a part's angle
    adds rot, so a point turns by R(-rot) about origin."""
    ox, oy = origin
    a = math.radians(-rot)
    x, y = p[0] - ox, p[1] - oy
    return (ox + x * math.cos(a) - y * math.sin(a) + dx,
            oy + x * math.sin(a) + y * math.cos(a) + dy)


def _src_geometry(pcb: Path):
    m = placelib.PlaceModel(pcb)
    bg = geom.BoardGeom.from_file(pcb)
    return m, bg


def _ref_k(ref: str, k: int) -> str:
    return f"{ref[0]}{k}{ref[2:]}"


def _net_k(net: str, k: int) -> str:
    return {"/ch1/MID": f"/ch{k}/MID", "/OUT1": f"/OUT{k}",
            "/IN1": f"/IN{k}"}.get(net, net)


# ------------------------------------------------------------- identity

def test_idents_from_the_netlist_pair_by_symbol_uuid():
    ids = rl.idents_from_netlist(FIX / "ok" / "rep.net")
    assert ids["R201"].sheet == "/ch2/"
    assert ids["R101"].sym == ids["R201"].sym == ids["R301"].sym
    assert ids["R101"].sym != ids["R102"].sym
    assert rl._sheet_refs(ids, "ch3") == ["C301", "C302", "R301", "R302"]
    assert rl._sheet_refs(ids, "/ch3/") == rl._sheet_refs(ids, "ch3")
    pairs, unm, miss, how = rl.pair_refs(SRC, rl._sheet_refs(ids, "ch2"),
                                         ids, False)
    assert how == "symbol_uuid" and not unm and not miss
    assert pairs == {r: _ref_k(r, 2) for r in SRC}


def test_sheets_with_no_common_uuid_pair_by_ref_suffix():
    ids = {r: rl.Ident(r, s, f"{r}-uuid") for r, s in
           (("R101", "/a/"), ("C101", "/a/"), ("R201", "/b/"),
            ("C201", "/b/"), ("C202", "/b/"))}
    pairs, unm, miss, how = rl.pair_refs(["C101", "R101"],
                                         ["C201", "C202", "R201"], ids, False)
    assert how == "ref_suffix"
    assert pairs == {"C101": "C201", "R101": "R201"}
    assert unm == ["C202"] and miss == []


def test_board_footprint_paths_win_over_the_netlist(tmp_path):
    pcb = tmp_path / "rep.kicad_pcb"
    text = (FIX / "ok" / "rep.kicad_pcb").read_text()
    ids = rl.idents_from_netlist(FIX / "ok" / "rep.net")
    # give R201 a KiCad-style path + sheetname, the way "update PCB from
    # schematic" writes them
    i = text.index('(property "Reference" "R201"')
    text = (text[:i] + f'(path "/root/sheet2/{ids["R201"].sym}")\n'
            '(sheetname "chX")\n' + text[i:])
    pcb.write_text(text)
    got, src = rl.load_idents(pcb, None)
    assert src == "board"
    assert got["R201"].sheet == "/chX/" and got["R201"].sym == ids["R201"].sym


# ------------------------------------------------------------- the plan

def test_grid_plan_is_the_source_under_the_pitch_within_1um():
    pcb = FIX / "ok" / "rep.kicad_pcb"
    m, bg = _src_geometry(pcb)
    p = rl.plan(pcb, _cons())
    assert p["violations"] == []
    place = {op["ref"]: op for op in p["place_ops"]}
    assert sorted(place) == sorted(_ref_k(r, k) for r in SRC for k in (2, 3))
    for k, dy in ((2, 10.0), (3, 20.0)):
        for r in SRC:
            f = m.footprints[r]
            op = place[_ref_k(r, k)]
            assert abs(op["x"] - f.pos[0]) <= TOL
            assert abs(op["y"] - (f.pos[1] + dy)) <= TOL
            assert op["deg"] == f.angle % 360 and op["side"] == f.side
    # copper: every source segment / via, moved by the pitch, on the mapped
    # net - 8 segments and 2 vias per target, no remove (nothing to rip)
    adds = [op for op in p["route_ops"] if op["op"] != "remove"]
    assert len(adds) == 2 * (8 + 2)
    want = set()
    for k, dy in ((2, 10.0), (3, 20.0)):
        for t in bg.tracks_of():
            a, b = t.shape.coords[0], t.shape.coords[-1]
            want.add(("t", t.layer, _net_k(t.net, k), round(a[0], 3),
                      round(a[1] + dy, 3), round(b[0], 3), round(b[1] + dy, 3)))
        for v in bg.vias_of():
            want.add(("v", _net_k(v.net, k), round(v.at[0], 3),
                      round(v.at[1] + dy, 3)))
    got = set()
    for op in adds:
        if op["op"] == "add_track":
            got.add(("t", op["layer"], op["net"], round(op["start"][0], 3),
                     round(op["start"][1], 3), round(op["end"][0], 3),
                     round(op["end"][1], 3)))
        else:
            got.add(("v", op["net"], round(op["at"][0], 3),
                     round(op["at"][1], 3)))
    assert got == want


def test_explicit_rot180_plan_turns_the_channel_about_its_centre():
    pcb = FIX / "ok" / "rep.kicad_pcb"
    m, bg = _src_geometry(pcb)
    cons = _cons(transform="explicit",
                 offsets=[{"dx": 0, "dy": 10}, {"dx": 0, "dy": 20, "rot": 180}])
    p = rl.plan(pcb, cons)
    xs = [m.footprints[r].pos[0] for r in SRC]
    ys = [m.footprints[r].pos[1] for r in SRC]
    o = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    place = {op["ref"]: op for op in p["place_ops"]}
    for r in SRC:
        f = m.footprints[r]
        x, y = _xf(f.pos, 0, 20, 180, o)
        op = place[_ref_k(r, 3)]
        assert abs(op["x"] - x) <= TOL and abs(op["y"] - y) <= TOL
        assert op["deg"] == (f.angle + 180) % 360
    ends = {(round(op["start"][0], 3), round(op["start"][1], 3),
             round(op["end"][0], 3), round(op["end"][1], 3))
            for op in p["route_ops"] if op["op"] == "add_track"
            and op["net"] == "/ch3/MID"}
    want = set()
    for t in bg.tracks_of(net="/ch1/MID"):
        a = _xf(t.shape.coords[0], 0, 20, 180, o)
        b = _xf(t.shape.coords[-1], 0, 20, 180, o)
        want.add((round(a[0], 3), round(a[1], 3), round(b[0], 3),
                  round(b[1], 3)))
    assert ends == want and len(want) == 3


def test_mutant_extra_part_is_reported_and_only_it_falls_back():
    pcb = FIX / "mutant" / "rep.kicad_pcb"
    m = placelib.PlaceModel(pcb)
    p = rl.plan(pcb, _cons())
    kinds = [(v["kind"], v["refs"]) for v in p["violations"]]
    assert kinds == [("replicate_unmatched", ["C303"])]
    place = [op for op in p["place_ops"] if op["op"] == "place"]
    assert sorted(op["ref"] for op in place) == sorted(
        _ref_k(r, k) for r in SRC for k in (2, 3))
    moves = [op for op in p["place_ops"] if op["op"] == "move"]
    assert [op["ref"] for op in moves] == ["C303"]
    assert p["report"]["fallback_refs"] == ["C303"]
    # the fallback spot is clear of every stamped courtyard and in the board
    c = m.footprints["C303"]
    spot = placelib.Footprint(**{**c.__dict__})
    spot.pos = (moves[0]["x"], moves[0]["y"])
    shape = spot.extents_abs()
    assert m.outline.contains(shape)
    for op in place:
        f = placelib.Footprint(**{**m.footprints[op["ref"]].__dict__})
        f.pos, f.angle = (op["x"], op["y"]), op["deg"]
        assert not f.extents_abs().intersects(shape)


def test_a_copy_onto_another_nets_copper_is_dropped(tmp_path):
    """A /IN2 track already crossing where ch2's MID run lands: the copied
    MID segments it touches are dropped and listed, the rest copied."""
    d = tmp_path / "b"
    shutil.copytree(FIX / "ok", d)
    pcb = d / "rep.kicad_pcb"
    t = pcb.read_text()
    seg = ('\t(segment (start 118 114) (end 118 118) (width 0.25) '
           '(layer "F.Cu") (net "/IN2") '
           '(uuid "00000000-0000-0000-0000-000000000482"))\n')
    i = t.rindex(")")
    pcb.write_text(t[:i] + seg + t[i:])
    p = rl.plan(pcb, _cons())
    dropped = [v for v in p["violations"]
               if v["kind"] == "replicate_track_dropped"]
    assert len(dropped) == 1 and dropped[0]["net"] == "/ch2/MID"
    assert "/IN2" in dropped[0]["msg"]
    mid2 = [op for op in p["route_ops"] if op.get("net") == "/ch2/MID"]
    assert len(mid2) == 2          # 3 source MID segments, 1 dropped
    assert all(not (min(op["start"][0], op["end"][0]) < 118 <
                    max(op["start"][0], op["end"][0])) for op in mid2)


def test_ref_list_source_pairs_by_position():
    pcb = FIX / "ok" / "rep.kicad_pcb"
    cons = {"channels": [{"source": ["R101", "C101"],
                          "targets": [["R201", "C201"]],
                          "transform": "grid", "pitch": [0, 10],
                          "route": False}]}
    p = rl.plan(pcb, cons)
    assert sorted(op["ref"] for op in p["place_ops"]) == ["C201", "R201"]
    assert p["route_ops"] == []


def test_bad_transforms_are_refused():
    pcb = FIX / "ok" / "rep.kicad_pcb"
    with pytest.raises(rl.ReplicateError, match="pitch"):
        rl.plan(pcb, _cons(pitch=None))
    with pytest.raises(rl.ReplicateError, match="offsets"):
        rl.plan(pcb, _cons(transform="explicit", offsets=[{"dx": 1}]))
    with pytest.raises(rl.ReplicateError, match="0/90/180/270"):
        rl.plan(pcb, _cons(transform="explicit",
                           offsets=[{"dx": 1}, {"dx": 2, "rot": 45}]))
    with pytest.raises(rl.ReplicateError, match="no parts"):
        rl.plan(pcb, _cons(source="nosuchsheet"))


def test_symmetry_reads_zero_before_the_stamp():
    s = rl.symmetry(FIX / "ok" / "rep.kicad_pcb", _cons())
    assert s["score"] == 0.0
    rows = s["channels"][0]["targets"]
    assert [r["placement"] for r in rows] == [0.0, 0.0]
    assert [r["routing"] for r in rows] == [0.0, 0.0]
    assert rl.symmetry(FIX / "ok" / "rep.kicad_pcb", {}) is None


def test_place_metrics_and_route_quality_report_channel_symmetry():
    import place_metrics
    import route_quality
    pcb = FIX / "ok" / "rep.kicad_pcb"
    _, metrics = place_metrics.collect(pcb, FIX / "ok" / "constraints.json",
                                       None, 2.0)
    assert metrics["channel_symmetry"]["score"] == 0.0
    assert "routing" not in metrics["channel_symmetry"]["channels"][0][
        "targets"][0]
    payload, _ = route_quality.run(["--pcb", str(pcb)])
    assert payload["channel_symmetry"]["score"] == 0.0


def test_constraints_lint_knows_channels(tmp_path):
    good = tmp_path / "c.json"
    good.write_text(json.dumps(_cons(transform="explicit", offsets=[
        {"dx": 0, "dy": 10}, {"dx": 0, "dy": 20, "rot": 180}])))
    payload, _ = constraints_lint.run(["--file", str(good)])
    assert payload["violations"] == []
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"channels": [{"source": "ch1",
                                             "targets": "ch2"}]}))
    payload, _ = constraints_lint.run(["--file", str(bad)])
    assert any("targets" in v["msg"] for v in payload["violations"])


# ------------------------------------------------------------- live

def _live():
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("no kicad-cli / KiCad python")
    return cli


def _stamp(tmp_path, which: str, cons: dict | None = None):
    import place_replicate
    d = tmp_path / which
    shutil.copytree(FIX / which, d)
    if cons is not None:
        (d / "constraints.json").write_text(json.dumps(cons))
    pcb = d / "rep.kicad_pcb"
    payload, _ = place_replicate.run(["--pcb", str(pcb), "--apply"])
    return pcb, payload


def _drc(cli, pcb):
    import kc
    r = kc.run_drc(cli, pcb, parity=True)
    vs = r["violations"]
    return ([v for v in vs if v["source"] == "drc"],
            [v for v in vs if v["source"] == "parity"],
            {v.get("net") for v in vs if v["source"] == "unconnected"})


def _check_stamped(src_pcb, pcb, k, dx, dy, rot=0.0, origin=None):
    """Parse the SAVED board: target k's parts and copper are the source's
    under the transform within 1 um."""
    ms, bs = _src_geometry(src_pcb)
    mt, bt = _src_geometry(pcb)
    if origin is None:
        xs = [ms.footprints[r].pos[0] for r in SRC]
        ys = [ms.footprints[r].pos[1] for r in SRC]
        origin = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    for r in SRC:
        f, g = ms.footprints[r], mt.footprints[_ref_k(r, k)]
        x, y = _xf(f.pos, dx, dy, rot, origin)
        assert abs(g.pos[0] - x) <= TOL and abs(g.pos[1] - y) <= TOL, r
        assert abs((g.angle - f.angle - rot + 180) % 360 - 180) < 0.01
        assert g.locked
    have = [(t.layer, t.net, t.shape.coords[0], t.shape.coords[-1])
            for t in bt.tracks_of()]
    for t in bs.tracks_of():
        if t.net not in ("/ch1/MID", "/OUT1", "/GND"):
            continue
        a = _xf(t.shape.coords[0], dx, dy, rot, origin)
        b = _xf(t.shape.coords[-1], dx, dy, rot, origin)
        assert any(ly == t.layer and n == _net_k(t.net, k) and (
            (math.dist(p, a) <= TOL and math.dist(q, b) <= TOL)
            or (math.dist(p, b) <= TOL and math.dist(q, a) <= TOL))
            for ly, n, p, q in have), (t.net, a, b)
    vias = [(v.net, v.at) for v in bt.vias_of()]
    for v in bs.vias_of():
        at = _xf(v.at, dx, dy, rot, origin)
        assert any(n == _net_k(v.net, k) and math.dist(p, at) <= TOL
                   for n, p in vias)


def test_live_stamp_known_answer_drc_and_parity_clean(tmp_path):
    cli = _live()
    pcb, payload = _stamp(tmp_path, "ok")
    assert payload["status"] == "pass", payload.get("violations")
    assert payload["channel_symmetry"]["score"] == 1.0
    src = FIX / "ok" / "rep.kicad_pcb"
    _check_stamped(src, pcb, 2, 0, 10)
    _check_stamped(src, pcb, 3, 0, 20)
    drc, parity, unconnected = _drc(cli, pcb)
    assert drc == [] and parity == []
    # what is left is route_auto's: the nets between the channels and J1,
    # never a channel's own net
    assert unconnected <= {"/GND", "/IN1", "/IN2", "/IN3", "/OUT1",
                           "/OUT2", "/OUT3"}
    # a second run changes nothing
    before = pcb.read_text().count("(segment") + pcb.read_text().count("(via")
    import place_replicate
    again, _ = place_replicate.run(["--pcb", str(pcb), "--apply"])
    assert again["status"] == "pass"
    t = pcb.read_text()
    assert t.count("(segment") + t.count("(via") == before


def test_live_stamp_rot180_drc_clean(tmp_path):
    cli = _live()
    cons = _cons(transform="explicit",
                 offsets=[{"dx": 0, "dy": 10}, {"dx": 0, "dy": 20, "rot": 180}])
    pcb, payload = _stamp(tmp_path, "ok", cons)
    assert payload["channel_symmetry"]["score"] == 1.0
    _check_stamped(FIX / "ok" / "rep.kicad_pcb", pcb, 3, 0, 20, 180)
    drc, parity, _ = _drc(cli, pcb)
    assert drc == [] and parity == []


def test_live_mutant_extra_part_falls_back_alone(tmp_path):
    cli = _live()
    pcb, payload = _stamp(tmp_path, "mutant")
    assert [v["refs"] for v in payload["violations"]] == [["C303"]]
    assert payload["channel_symmetry"]["score"] == 1.0
    _check_stamped(FIX / "mutant" / "rep.kicad_pcb", pcb, 3, 0, 20)
    m = placelib.PlaceModel(pcb)
    assert not m.footprints["C303"].locked
    drc, parity, unconnected = _drc(cli, pcb)
    assert drc == [] and parity == []
    assert "/ch3/MID" in unconnected      # C303's pad: route_auto's
