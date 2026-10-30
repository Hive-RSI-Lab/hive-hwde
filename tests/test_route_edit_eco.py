"""U-ECO tests: the copper and footprint ECOs boards used to hand-script with
pcbnew (the hi-fi boards' rip*/add_*/renet/rename_net/pad_*/set_dnp scripts)
as route_edit / place_edit ops.

Pure tests (venv only) cover validation and the selector -> uuid resolution on
the frozen routed pd-trigger fixture. Smoke tests (SWIG bundled python) apply
them end to end: what landed, the byte-identical rollback when KiCad undoes a
renet, and place_edit's refusal to re-net a pad copper touches.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import env  # noqa: E402
import geom  # noqa: E402
import place_edit  # noqa: E402
import route_edit  # noqa: E402
from checklib import CheckError  # noqa: E402

PD_DIR = REPO / "tests" / "fixtures" / "stages" / "pd_trigger" / "route"
PD_PCB = PD_DIR / "pd-trigger.kicad_pcb"


@pytest.fixture(scope="module")
def bg():
    return geom.BoardGeom.from_file(PD_PCB)


def _v(*ops):
    return route_edit.validate_ops({"version": 1, "ops": list(ops)})


# ------------------------------------------------------------ validation

@pytest.mark.parametrize("op, msg", [
    ({"op": "rip"}, "needs at least one of"),
    ({"op": "rip", "layer": "F.Cu"}, "needs at least one of"),
    ({"op": "rip", "net": []}, "net must be"),
    ({"op": "rip", "net": "A", "kind": "zone"}, "kind must be"),
    ({"op": "rip", "net": "A", "touch": "any"}, "touch goes with at"),
    ({"op": "rip", "net": "A", "radius": 1}, "radius goes with"),
    ({"op": "rip", "region": [5, 0, 1, 1]}, "region must be"),
    ({"op": "rip", "at": [1]}, "at must be"),
    ({"op": "rip", "net": "A", "bogus": 1}, "unknown keys"),
    ({"op": "renet", "net": "A"}, "missing ['to']"),
    ({"op": "add_path", "points": [[0, 0]], "width": 0.2, "layer": "F.Cu",
      "net": "A"}, "points must be"),
    ({"op": "rename_net", "from": "A", "to": "A"}, "same net"),
])
def test_validate_refuses(op, msg):
    with pytest.raises(CheckError, match=msg.replace("[", r"\[")
                       .replace("]", r"\]")):
        _v(op)


def test_validate_accepts_selectors():
    _v({"op": "rip", "net": ["A", "B"], "layer": "F.Cu"},
       {"op": "rip", "at": [1, 2], "radius": 0.1, "touch": "any"},
       {"op": "rip", "pads": ["U1", "R1.2"], "radius": 0.5},
       {"op": "renet", "uuid_prefix": "ab", "to": "GND"},
       {"op": "add_path", "points": [[0, 0], [1, 0], [1, 1]], "width": 0.2,
        "layer": "F.Cu", "net": "A"},
       {"op": "rename_net", "from": "A", "to": "B"})


def test_load_ops_inline_and_file(tmp_path):
    f = tmp_path / "ops.json"
    f.write_text(json.dumps({"version": 1, "ops": [
        {"op": "remove", "uuid": "x"}]}))
    ops = route_edit.load_ops(str(f), ['{"op": "rip", "net": "A"}',
                                       '[{"op": "rip", "net": "B"}]'])
    assert [o["op"] for o in ops] == ["remove", "rip", "rip"]
    with pytest.raises(CheckError, match="give --ops"):
        route_edit.load_ops(None, [])
    with pytest.raises(CheckError, match="not JSON"):
        route_edit.load_ops(None, ["{nope"])


def test_place_edit_validates_pad_and_dnp():
    ok = {"version": 1, "ops": [
        {"op": "set_pad", "ref": "R1", "pad": ["1", "2"], "net": ""},
        {"op": "set_pad", "ref": "R1", "pad": "1", "size": [0.7, 0.8]},
        {"op": "set_dnp", "ref": "R1", "dnp": True}]}
    assert len(place_edit.validate_ops(ok)) == 3
    for op, msg in (({"op": "set_pad", "ref": "R1", "pad": "1"},
                     "needs net and/or size"),
                    ({"op": "set_pad", "ref": "R1", "pad": 1, "net": "A"},
                     "pad must be"),
                    ({"op": "set_pad", "ref": "R1", "pad": "1", "size": 0},
                     "size must be"),
                    ({"op": "set_dnp", "ref": "R1", "dnp": "yes"},
                     "dnp must be")):
        with pytest.raises(CheckError, match=msg):
            place_edit.validate_ops({"version": 1, "ops": [op]})


# ------------------------------------------------------------ selectors

def test_select_by_net_layer_kind(bg):
    every = route_edit.select_items(bg, {"net": "GND"})
    tracks = route_edit.select_items(bg, {"net": "GND", "kind": "track"})
    vias = route_edit.select_items(bg, {"net": "GND", "kind": "via"})
    assert len(tracks) == len(bg.tracks_of(net="GND")) > 0
    assert len(vias) == len(bg.vias_of(net="GND")) > 0
    assert len(every) == len(tracks) + len(vias)
    f = route_edit.select_items(bg, {"net": "GND", "layer": "F.Cu"})
    assert f and all(isinstance(t, geom.Track) and t.layer == "F.Cu"
                     for t in f)
    two = route_edit.select_items(bg, {"net": ["GND", "/VDD"]})
    assert {t.net for t in two} == {"GND", "/VDD"}


def test_select_at_point_and_touch_any(bg):
    t = bg.tracks_of(net="/VIND")[0]
    c = list(t.shape.coords)
    hit = route_edit.select_items(bg, {"at": list(c[0])})
    assert t.uuid in {x.uuid for x in hit}
    assert all(x.net == "/VIND" for x in hit)
    mid = t.shape.interpolate(0.5, normalized=True)
    if t.length > 0.2:
        assert t.uuid not in {x.uuid for x in route_edit.select_items(
            bg, {"at": [mid.x, mid.y]})}
    assert t.uuid in {x.uuid for x in route_edit.select_items(
        bg, {"at": [mid.x, mid.y], "touch": "any"})}


def test_select_region_and_uuid_prefix(bg):
    t = bg.tracks_of(net="/CFG1")[0]
    x, y = list(t.shape.coords)[0]
    hit = route_edit.select_items(
        bg, {"region": [x - 0.01, y - 0.01, x + 0.01, y + 0.01]})
    assert t.uuid in {i.uuid for i in hit}
    assert route_edit.select_items(bg, {"region": [-9, -9, -8, -8]}) == []
    got = route_edit.select_items(bg, {"uuid_prefix": t.uuid[:12]})
    assert [i.uuid for i in got] == [t.uuid]


def test_select_pads_on_own_net_only(bg):
    hit = route_edit.select_items(bg, {"pads": ["R14.2"]})
    assert hit and all(i.net == "/VIND" for i in hit)
    assert route_edit.select_items(bg, {"pads": ["R14.2"],
                                        "net": "GND"}) == []
    whole = route_edit.select_items(bg, {"pads": ["R14"]})
    assert {i.uuid for i in hit} <= {i.uuid for i in whole}
    wide = route_edit.select_items(bg, {"pads": ["R14.2"], "radius": 5})
    assert len(wide) >= len(hit)


def test_resolve_ops_expands_and_maps_back(bg):
    ops = _v({"op": "rip", "net": "/CFG1"},
             {"op": "rip", "net": "/CFG1", "region": [-9, -9, -8, -8]},
             {"op": "add_path", "points": [[0, 0], [1, 0], [1, 0], [1, 1]],
              "width": 0.2, "layer": "F.Cu", "net": "GND"},
             {"op": "remove", "uuid": "u"})
    flat, spans = route_edit.resolve_ops(bg, ops)
    n = len(bg.tracks_of(net="/CFG1")) + len(bg.vias_of(net="/CFG1"))
    assert len(spans[0]) == n and spans[1] == []
    assert [flat[j]["op"] for j in spans[2]] == ["add_track"] * 2
    assert flat[spans[3][0]] == {"op": "remove", "uuid": "u"}


# ------------------------------------------------------------ smoke (KiCad)

@pytest.fixture
def kicad():
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("KiCad bundled python not installed")


@pytest.fixture
def pd(tmp_path):
    dst = tmp_path / "pdt"
    shutil.copytree(PD_DIR, dst)
    return dst / PD_PCB.name


def test_route_edit_ecos_apply(kicad, pd):
    before = geom.BoardGeom.from_file(pd)
    n_cfg1 = len(before.tracks_of(net="/CFG1")) \
        + len(before.vias_of(net="/CFG1"))
    payload, _ = route_edit.run([
        "--pcb", str(pd),
        "--op", '{"op": "rip", "net": "/CFG1"}',
        "--op", json.dumps({"op": "add_path", "net": "GND", "layer": "B.Cu",
                            "width": 0.3,
                            "points": [[10, 10], [12, 10], [12, 12]]}),
        "--op", '{"op": "rename_net", "from": "/HV_OK", "to": "/HV_GOOD"}'])
    rip, path, ren = payload["results"]
    assert rip["status"] == "removed" and rip["count"] == n_cfg1
    assert path == {"op": "add_path", "count": 2, "status": "added",
                    "segments": ["added", "added"]}
    assert ren["status"] == "renamed" and ren["count"] > 0
    after = geom.BoardGeom.from_file(pd)
    assert not after.tracks_of(net="/CFG1")
    assert not after.pads_of(net="/HV_OK")
    assert len(after.pads_of(net="/HV_GOOD")) \
        == len(before.pads_of(net="/HV_OK"))
    # a re-run finds nothing left to rip and the path already there
    again, _ = route_edit.run([
        "--pcb", str(pd), "--op", '{"op": "rip", "net": "/CFG1"}',
        "--op", json.dumps({"op": "add_path", "net": "GND", "layer": "B.Cu",
                            "width": 0.3, "points": [[10, 10], [12, 10]]})])
    assert again["results"][0]["status"] == "absent"
    assert again["results"][1]["segments"] == ["exists"]


def test_renet_of_pad_tied_copper_rolls_back(kicad, pd):
    """KiCad re-derives a pad-tied track's net from its pad on save; verify
    catches it and the board stays byte-identical."""
    raw = pd.read_bytes()
    with pytest.raises(CheckError, match="re-derives"):
        route_edit.apply_ops(pd, _v({"op": "renet", "pads": ["R14.2"],
                                     "to": "GND"}))
    assert pd.read_bytes() == raw


def test_place_edit_pad_and_dnp(kicad, pd):
    payload, _ = place_edit.run([
        "--pcb", str(pd),
        "--op", '{"op": "set_pad", "ref": "U1", "pad": "10", "net": ""}',
        "--op", '{"op": "set_pad", "ref": "J2", "pad": "2", "size": 2.5}',
        "--op", '{"op": "set_dnp", "ref": "D2", "dnp": true}'])
    assert payload["verified"]
    bg = geom.BoardGeom.from_file(pd)
    assert {p.net for p in bg.pads_of(ref="U1") if p.number == "10"} \
        in ({None}, {""})
    assert [p.size for p in bg.pads_of(ref="J2") if p.number == "2"] \
        == [(2.5, 2.5)]
    assert "D2" in place_edit._dnp_refs(pd)


def test_place_edit_refuses_routed_pad_renet(kicad, pd):
    raw = pd.read_bytes()
    with pytest.raises(CheckError, match="rip them first"):
        place_edit.run(["--pcb", str(pd), "--op",
                        '{"op": "set_pad", "ref": "R14", "pad": "2", '
                        '"net": "GND"}'])
    assert pd.read_bytes() == raw
