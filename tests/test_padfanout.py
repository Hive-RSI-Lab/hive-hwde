"""padfanout / route_escape --pads (#929) on the golden QFN board
(tests/fixtures/padfanout, rebuilt by make_fixture.py).

The board crowds U1.2's (VIN) outward side with a C and a B via, so the
straight spot is taken and the fanout must find one off to the side. U1.11
(GND) already has a via 0.8 mm away. Pure tests check the chosen vias and
stubs with geometry written here, not the module's own checks; the live test
applies the ops with KiCad and wants no new DRC finding.
"""
from __future__ import annotations

import json
import math
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
import padfanout  # noqa: E402
import route_escape  # noqa: E402
import route_finish as rf  # noqa: E402
from checklib import CheckError  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "padfanout"
PCB = FIX / "qfn.kicad_pcb"
PADS = ["U1.2", "U1.1", "U1.3", "U1.11", "U1.5"]


def _bg():
    return geom.BoardGeom.from_file(PCB)


def _board(tmp_path):
    for src in FIX.glob("qfn.*"):
        shutil.copy2(src, tmp_path / src.name)
    return tmp_path / "qfn.kicad_pcb"


@pytest.fixture(scope="module")
def fanned():
    bg = _bg()
    fo = padfanout.Fanout(bg)
    ops, rows = fo.fan(PADS)
    return bg, fo.rules, ops, {r["pad"]: r for r in rows}


def _pad(bg, spec):
    ref, num = spec.split(".")
    return next(p for p in bg.pads_of(ref=ref) if p.number == num)


def test_report_rows(fanned):
    _, _, ops, rows = fanned
    assert [rows[p]["result"] for p in PADS] == \
        ["via", "via", "via", "has via", "via"]
    assert len(ops) == 2 * 4                      # a stub and a via per pad


def test_crowded_pad_goes_to_the_side(fanned):
    """U1.2's straight-out spot (y = 10) is taken by C's via: the via sits
    off the pad's axis, outward of the pad (x < pad edge), never under U1."""
    bg, _, _, rows = fanned
    pad = _pad(bg, "U1.2")
    x, y = rows["U1.2"]["at"]
    assert abs(y - pad.center[1]) > 0.1
    assert x < pad.poly.bounds[0]


def test_never_inward():
    """Each pad's search directions exclude the one toward U1's centre."""
    bg = _bg()
    centre = bg.footprints["U1"]["at"]
    for p in bg.pads_of(ref="U1"):
        dirs = padfanout.Fanout.directions(p, centre)
        inward = (centre[0] - p.center[0], centre[1] - p.center[1])
        # the pad's long axis points at the centre: that direction is gone
        ax = (0 if abs(inward[0]) < 1e-6 else math.copysign(1, inward[0]),
              0 if abs(inward[1]) < 1e-6 else math.copysign(1, inward[1]))
        main = (int(ax[0]), 0) if abs(inward[0]) > abs(inward[1]) \
            else (0, int(ax[1]))
        assert main not in dirs and (-main[0], -main[1]) == dirs[0]


def test_vias_clear_every_other_net(fanned):
    """Independent check: each new via against every pad, via, track and
    other new via at the board's clearance, hole to hole and hole
    clearance."""
    bg, rules, ops, _ = fanned
    copper = [(p.net, p.poly, None) for p in bg.pads_of()]
    copper += [(v.net, v.poly, (v.at, v.drill)) for v in bg.vias_of()]
    copper += [(t.net, t.poly, None) for t in bg.tracks_of()]
    new = [o for o in ops if o["op"] == "add_via"]
    for o in ops:
        if o["op"] == "add_track":
            copper.append((o["net"], LineString([o["start"], o["end"]])
                           .buffer(o["width"] / 2), None))
        else:
            copper.append((o["net"], Point(o["at"]).buffer(o["size"] / 2),
                           (tuple(o["at"]), o["drill"])))
    for v in new:
        at = Point(v["at"])
        ring = at.buffer(v["size"] / 2)
        hole = at.buffer(v["drill"] / 2)
        for net, poly, drilled in copper:
            if drilled and tuple(drilled[0]) == tuple(v["at"]):
                continue                           # itself
            if drilled:
                gap = at.distance(Point(drilled[0])) \
                    - v["drill"] / 2 - drilled[1] / 2
                assert gap >= rules.hole_to_hole - 1e-6, (v, net)
            if net == v["net"]:
                continue
            assert ring.distance(poly) >= rules.clearance - 1e-6, (v, net)
            assert hole.distance(poly) >= rules.hole_clearance - 1e-6, (v, net)


def test_stubs_on_pad_layer_and_clear(fanned):
    bg, rules, ops, _ = fanned
    others = [(p.net, p.poly) for p in bg.pads_of() if "F.Cu" in p.layers]
    others += [(v.net, v.poly) for v in bg.vias_of()]
    others += [(o["net"], Point(o["at"]).buffer(o["size"] / 2))
               for o in ops if o["op"] == "add_via"]
    for o in ops:
        if o["op"] != "add_track":
            continue
        assert o["layer"] == "F.Cu"
        stub = LineString([o["start"], o["end"]]).buffer(o["width"] / 2)
        for net, poly in others:
            if net != o["net"]:
                assert stub.distance(poly) >= rules.clearance - 1e-6, (o, net)


def test_own_net_smd_keep(fanned):
    """No via drill within route_finish's via-to-own-SMD-pad keep."""
    bg, _, ops, _ = fanned
    for v in (o for o in ops if o["op"] == "add_via"):
        for p in bg.pads_of():
            if p.net == v["net"] and p.drill is None:
                assert p.poly.distance(Point(v["at"])) - v["drill"] / 2 \
                    >= rf.VIA_PAD_KEEP_MM - 1e-6


def test_bad_specs():
    fo = padfanout.Fanout(_bg())
    with pytest.raises(CheckError, match="want REF.PAD"):
        fo.fan(["U1"])
    with pytest.raises(CheckError, match="no such pad"):
        fo.fan(["U1.99"])
    with pytest.raises(CheckError, match="must be under"):
        padfanout.Fanout(_bg(), via=(0.3, 0.3))


def test_fan_via_parse_error(tmp_path):
    pcb = _board(tmp_path)
    with pytest.raises(CheckError, match="SIZE/DRILL"):
        route_escape.run(["--pcb", str(pcb), "--pads", "U1.2",
                          "--fan-via", "0.45"])


def test_plan_leaves_board(tmp_path):
    pcb = _board(tmp_path)
    before = pcb.read_bytes()
    out = tmp_path / "ops.json"
    payload, _ = route_escape.run(["--pcb", str(pcb), "--pads", "U1.2,U1.1",
                                   "--plan", "--out-ops", str(out)])
    assert pcb.read_bytes() == before
    assert payload["facts"]["board_updated"] is False
    assert len(json.loads(out.read_text())["ops"]) == 4


def test_no_spot_is_a_warning(tmp_path, monkeypatch):
    pcb = _board(tmp_path)
    monkeypatch.setattr(padfanout.Fanout, "spot", lambda *a: None)
    monkeypatch.setattr(padfanout.Fanout, "join", lambda *a: None)
    payload, _ = route_escape.run(["--pcb", str(pcb), "--pads", "U1.2",
                                   "--plan"])
    kinds = [v.get("kind") for v in payload["violations"]]
    assert "pad_fanout_incomplete" in kinds


@pytest.fixture(scope="module")
def kicad():
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("KiCad bundled python not installed")
    return cli


def _drc(cli, pcb) -> list:
    """DRC findings other than unconnected and dangling items (the fixture's
    vias and stubs lead nowhere) and its stand-in footprint library
    warnings."""
    import kc
    return [v for v in kc.run_drc(cli, pcb)["violations"]
            if v["check"] not in ("lib_footprint_issues", "unconnected_items",
                                  "via_dangling", "track_dangling")]


def test_live_fanout_drc_clean(kicad, tmp_path):
    pcb = _board(tmp_path)
    assert _drc(kicad, pcb) == []
    payload, _ = route_escape.run(["--pcb", str(pcb), "--pads",
                                   ",".join(PADS)])
    assert payload["facts"]["board_updated"] is True
    assert pcb.read_text(encoding="utf-8").count("(via") == 3 + 4
    assert _drc(kicad, pcb) == []
