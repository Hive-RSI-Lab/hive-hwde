"""#722: dfm_clearance and dfm_copper_to_edge name whose copper it is.

The gerbers carry no nets, so a clearance finding said nothing about the
copper on either side: a sliver between two islands of GND read the same as a
pinch between two signals, and a waiver could only be scoped by pos. With the
board at hand each of the finding's `ends` is looked up in it. The known
answers follow precision-daq's three findings: slivers of GND between U201's
pads, and one with no pad anywhere near.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon, box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import dfm_check  # noqa: E402
import gate  # noqa: E402
import geom  # noqa: E402

RULES = {"min_clearance_mm": 0.2, "min_copper_to_edge_mm": 0.3}
HEAD = """(kicad_pcb (version 20240108) (generator "pcbnew") (generator_version "8.0")
  (general (thickness 1.6) (legacy_teardrops no))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0))
"""
NETS = ["", "GND", "/A", "/B", "/C"]
NUM = {name: i for i, name in enumerate(NETS)}


def _net(name):
    return f'(net {NUM[name]} "{name}")'


def _pad(num, x, y, net, size=0.5):
    return (f'(pad "{num}" smd rect (at {x} {y}) (size {size} {size}) '
            f'(layers "F.Cu") {_net(net)})')


def _fp(ref, *pads):
    return (f'(footprint "t:t" (layer "F.Cu") (at 0 0) '
            f'(property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS") '
            f'(effects (font (size 1 1)))) ' + " ".join(pads) + ")")


def _track(x0, y0, x1, y1, net, width=0.2):
    return (f'(segment (start {x0} {y0}) (end {x1} {y1}) (width {width}) '
            f'(layer "F.Cu") {_net(net)})')


def _pts(rect):
    x0, y0, x1, y1 = rect
    return (f"(pts (xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1}) (xy {x0} {y1}))")


def _zone(net, *rects):
    fills = " ".join(f'(filled_polygon (layer "F.Cu") {_pts(r)})' for r in rects)
    return (f'(zone {_net(net)} (net_name "{net}") (layer "F.Cu") '
            f'(hatch edge 0.5) (connect_pads (clearance 0.1)) (min_thickness 0.1) '
            f'(fill yes) (polygon {_pts((0, 0, 100, 100))}) {fills})')


def _board(tmp_path, *items) -> Path:
    outline = "".join(
        f'(gr_line (start {a} {b}) (end {c} {d}) (layer "Edge.Cuts") (width 0.05))\n'
        for a, b, c, d in ((0, 0, 100, 0), (100, 0, 100, 100),
                           (100, 100, 0, 100), (0, 100, 0, 0)))
    text = (HEAD + "".join(f"(net {i} \"{n}\")\n" for i, n in enumerate(NETS))
            + "\n".join(items) + "\n" + outline + ")\n")
    pcb = tmp_path / "t.kicad_pcb"
    pcb.write_text(text)
    return pcb


def _fab(*polys, outline=box(0, 0, 100, 100)):
    layer = SimpleNamespace(components=lambda: list(polys))
    return SimpleNamespace(copper_layer_names=lambda: ["F.Cu"],
                           copper=lambda name: layer, outline=outline)


def _clearance(tmp_path, polys, *items) -> dict:
    bg = geom.BoardGeom.from_file(_board(tmp_path, *items))
    vios: list = []
    dfm_check.check_clearance(_fab(*polys), RULES, vios)
    dfm_check.name_gaps(bg, vios)
    (v,) = vios
    return v


# Two islands of one GND zone, 0.05 mm apart at x = 50, in the corner between
# U1's two signal pads (precision-daq's F.Cu findings at U201).
LEFT = (40, 40, 50, 60)
RIGHT = (50.05, 40, 60, 60)


def test_sliver_between_islands_of_one_net_names_it_and_its_pads(tmp_path):
    v = _clearance(
        tmp_path, [box(*LEFT), box(*RIGHT)], _zone("GND", LEFT, RIGHT),
        _fp("U1", _pad(1, 50.025, 39.7, "/A"), _pad(2, 50.025, 60.3, "/B")))
    assert v["net"] == "GND"
    assert v["same_net"] is True
    assert v["refs"] == ["U1"]
    assert [e["item"] for e in v["on"]] == ["zone", "zone"]
    assert v["on"][0]["near"] and v["on"][0]["near"][0].startswith("U1.")
    assert "GND" in v["msg"] and "same net" in v["msg"] and "U1" in v["msg"]
    assert v["items"][0]["msg"] == v["msg"]


def test_sliver_with_no_pad_near_has_no_refs(tmp_path):
    """precision-daq's B.Cu finding: nothing within 18 mm. Net, no refs."""
    v = _clearance(tmp_path, [box(*LEFT), box(*RIGHT)], _zone("GND", LEFT, RIGHT),
                   _fp("U1", _pad(1, 10, 10, "/A")))
    assert (v["net"], v["refs"], v["same_net"]) == ("GND", [], True)


def test_pad_to_track_pinch_names_both_nets_and_the_pad(tmp_path):
    pad = box(10, 10, 11, 11)
    trace = box(11.05, 5, 11.25, 15)
    v = _clearance(tmp_path, [pad, trace],
                   _fp("R1", _pad(1, 10.5, 10.5, "/A", size=1)),
                   _track(11.15, 5, 11.15, 15, "/B"))
    assert v["net"] == "/A | /B"
    assert v["same_net"] is False
    assert v["refs"] == ["R1"]
    got = sorted(v["on"], key=lambda e: e["net"])
    assert got[0] == {"net": "/A", "item": "pad", "ref": "R1", "pad": "1"}
    assert got[1] == {"net": "/B", "item": "track"}
    assert "R1.1 (/A)" in v["msg"] and "/B track" in v["msg"]


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_two_net_form_does_not_depend_on_which_end_comes_first(tmp_path, order):
    polys = [box(10, 5, 10.2, 15), box(10.25, 5, 10.45, 15)]
    v = _clearance(tmp_path, [polys[i] for i in order],
                   _track(10.1, 5, 10.1, 15, "/B"),
                   _track(10.35, 5, 10.35, 15, "/A"))
    assert v["net"] == "/A | /B"
    assert v["refs"] == []


def test_a_via_end_is_named_a_via(tmp_path):
    via = ('(via (at 10 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
           + _net("/A") + ")")
    v = _clearance(tmp_path, [Polygon([(10, 9.7), (10.3, 10), (10, 10.3), (9.7, 10)]),
                              box(10.35, 5, 10.55, 15)],
                   via, _track(10.45, 5, 10.45, 15, "/B"))
    assert sorted(e["item"] for e in v["on"]) == ["track", "via"]
    assert v["net"] == "/A | /B"


def test_a_pad_wins_over_the_pour_it_sits_in(tmp_path):
    """A pad flooded into its own net's pour: the end lies on both, and the
    pad is the one with a name."""
    pour = Polygon([(10, 10), (19.5, 10), (20, 15), (19.5, 20), (10, 20)])
    v = _clearance(tmp_path, [pour, box(20.05, 10, 30, 20)],
                   _zone("GND", (10, 10, 20, 20)),
                   _fp("U1", _pad(1, 19.5, 15, "GND", size=1)),
                   _track(25, 10, 25, 20, "/A", width=9.9))
    assert v["net"] == "/A | GND"
    assert v["refs"] == ["U1"]
    assert {"net": "GND", "item": "pad", "ref": "U1", "pad": "1"} in v["on"]


def test_an_end_the_board_does_not_have_leaves_the_net_unknown(tmp_path):
    """Gerbers and board disagree (a stale fill). Saying "GND" from one end
    would claim a same-net sliver nobody measured."""
    v = _clearance(tmp_path, [box(10, 10, 11, 11), box(11.05, 10, 12, 11)],
                   _fp("R1", _pad(1, 10.5, 10.5, "/A", size=1)))
    assert v["net"] is None
    assert v["refs"] == ["R1"]
    assert "same_net" not in v
    assert {"net": None, "item": None} in v["on"]
    assert "unknown copper" in v["msg"]


def test_no_copper_found_at_either_end_keeps_the_message(tmp_path):
    v = _clearance(tmp_path, [box(10, 10, 11, 11), box(11.05, 10, 12, 11)],
                   _fp("R1", _pad(1, 80, 80, "/A")))
    assert (v["net"], v["refs"]) == (None, [])
    assert v["msg"] == v["items"][0]["msg"]
    assert v["msg"].endswith("on F.Cu")


def test_footprint_copper_names_its_part(tmp_path):
    poly = ('(fp_poly (pts (xy 10 10) (xy 11 10) (xy 11 11) (xy 10 11)) '
            '(stroke (width 0) (type solid)) (fill solid) (layer "F.Cu"))')
    v = _clearance(tmp_path, [box(10, 10, 11, 11), box(11.05, 5, 11.25, 15)],
                   _fp("ANT1", poly, _pad(1, 8, 8, "/A")),
                   _track(11.15, 5, 11.15, 15, "/B"))
    assert v["refs"] == ["ANT1"]
    assert v["net"] == "/A | /B"  # its one pad says whose copper it is
    assert any(e["item"] == "footprint" for e in v["on"])
    assert "ANT1 copper" in v["msg"]


def test_a_pad_with_no_net_gives_a_ref_but_no_net(tmp_path):
    no_net = ('(pad "1" smd rect (at 10.5 10.5) (size 1 1) (layers "F.Cu"))')
    v = _clearance(tmp_path, [box(10, 10, 11, 11), box(11.05, 5, 11.25, 15)],
                   _fp("MH1", no_net), _track(11.15, 5, 11.15, 15, "/B"))
    assert v["net"] is None
    assert v["refs"] == ["MH1"]
    assert {"net": None, "item": "pad", "ref": "MH1", "pad": "1"} in v["on"]


def test_edge_finding_names_the_copper_not_the_edge(tmp_path):
    bg = geom.BoardGeom.from_file(_board(
        tmp_path, _fp("J1", _pad(1, 0.6, 50, "/A", size=1))))
    vios: list = []
    dfm_check.check_copper_to_edge(_fab(box(0.1, 49.5, 1.1, 50.5)), RULES, vios)
    dfm_check.name_gaps(bg, vios)
    (v,) = vios
    assert v["kind"] == "dfm_copper_to_edge"
    assert (v["net"], v["refs"]) == ("/A", ["J1"])
    assert v["on"] == [{"net": "/A", "item": "pad", "ref": "J1", "pad": "1"}]
    assert "same_net" not in v
    assert "J1.1 (/A)" in v["msg"]


def test_edge_band_warning_stays_one_anonymous_finding(tmp_path):
    """It rolls every pinch in the band into one, so it has no one net."""
    bg = geom.BoardGeom.from_file(_board(tmp_path, _track(0.35, 40, 0.35, 60, "/A")))
    rules = {"min_copper_to_edge_mm": 0.3, "abs_min_copper_to_edge_mm": 0.2}
    vios: list = []
    dfm_check.check_copper_to_edge(_fab(box(0.25, 40, 0.45, 60)), rules, vios)
    dfm_check.name_gaps(bg, vios)
    (v,) = vios
    assert v["kind"] == "dfm_copper_to_edge_preferred"
    assert (v["net"], v["refs"]) == (None, [])


def test_other_findings_are_left_alone(tmp_path):
    bg = geom.BoardGeom.from_file(_board(tmp_path, _track(10, 5, 10, 15, "/A")))
    other = {"kind": "dfm_trace_width", "layer": "F.Cu", "net": None, "refs": [],
             "msg": "m", "ends": [[10, 10], [10, 10]],
             "items": [{"msg": "m", "pos": [10, 10]}]}
    before = {**other}
    dfm_check.name_gaps(bg, [other])
    assert other == before


def test_without_a_board_the_finding_is_what_it_was(tmp_path):
    vios: list = []
    dfm_check.check_clearance(_fab(box(*LEFT), box(*RIGHT)), RULES, vios)
    (v,) = vios
    assert (v["net"], v["refs"]) == (None, [])
    assert "on" not in v and "same_net" not in v
    assert v["msg"].endswith("on F.Cu")


SIGNED = {"check": "dfm", "kind": "dfm_clearance", "reason": "r",
          "approved": "test 2026-11-05"}


class TestWaivers:
    """`net` is matched exactly, so naming the net is a change for waivers."""

    def _finding(self, tmp_path):
        return _clearance(
            tmp_path, [box(*LEFT), box(*RIGHT)], _zone("GND", LEFT, RIGHT),
            _fp("U1", _pad(1, 50.025, 39.7, "/A"), _pad(2, 50.025, 60.3, "/B")))

    def test_a_waiver_with_no_net_stops_matching(self, tmp_path):
        v = self._finding(tmp_path)
        # it matched while the finding had no net
        assert gate.waiver_matches({**SIGNED, "pos": v["pos"]},
                                   {**v, "net": None})
        assert not gate.waiver_matches({**SIGNED, "pos": v["pos"]}, v)
        assert not gate.waiver_matches(SIGNED, v)

    def test_a_pos_pinned_waiver_matches_once_it_names_the_net(self, tmp_path):
        v = self._finding(tmp_path)
        assert gate.waiver_matches({**SIGNED, "net": "GND", "pos": v["pos"]},
                                   v)

    def test_a_waiver_can_scope_by_net_and_ref_without_pos(self, tmp_path):
        v = self._finding(tmp_path)
        scoped = {**SIGNED, "net": "GND", "refs": ["U1"]}
        assert gate.waiver_matches(scoped, v)
        assert not gate.waiver_matches({**scoped, "net": "+3V3"}, v)
        assert not gate.waiver_matches({**scoped, "refs": ["U2"]}, v)


def test_run_names_the_copper_at_a_finding(monkeypatch):
    """Through run, on real gerbers: the post-pass is wired to the board."""
    d = REPO / "tests" / "fixtures" / "stages" / "mutant_paste"
    pcb = d / "blinky2.kicad_pcb"
    pad = next(p for p in geom.BoardGeom.from_file(pcb).pads_of(layer="F.Cu") if p.net)
    xy = [pad.center[0], pad.center[1]]

    def finding(fab, rules, vios):
        vios.append(dfm_check.checklib.violation(
            "dfm", "error", xy, "F.Cu", None, [], "copper clearance 0.05 mm",
            dfm_check.SOURCE, kind="dfm_clearance", clearance_mm=0.05,
            min_mm=0.1, ends=[xy, xy]))

    monkeypatch.setattr(dfm_check, "check_clearance", finding)
    rep = dfm_check.run(pcb, fab_dir=d / "gerbers", polarity=False,
                        skip=("placement", "netlist", "teardrop", "drill",
                              "zone_fill"))
    (v,) = [x for x in rep["violations"] if x.get("kind") == "dfm_clearance"]
    assert (v["net"], v["refs"], v["same_net"]) == (pad.net, [pad.ref], True)
