"""Zone copper held to the trace floor (#890).

A board that draws its controlled-impedance copper as zones (hw-lab zcoupon)
has no trace in its gerbers: a pair zone shaved to 0.1043 mm against a
0.127 mm floor passed. check_pour_width measures the pours themselves.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from shapely.geometry import LineString, box
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import dfm_check  # noqa: E402

RULES = {"min_trace_width_mm": 0.127, "min_clearance_mm": 0.127}


def _fab(pours, traces=()):
    polys = list(pours) + list(traces)
    layer = SimpleNamespace(
        pours=list(pours), traces=list(traces), trace_lines=[],
        components=lambda: list(getattr(unary_union(polys), "geoms",
                                         [unary_union(polys)])))
    return SimpleNamespace(copper_layer_names=lambda: ["F.Cu"],
                           copper=lambda name: layer)


def _run(fab):
    vios = []
    dfm_check.check_pour_width(fab, RULES, vios)
    return vios


def _strip(y, w, x0=0.0, x1=20.0):
    return box(x0, y - w / 2, x1, y + w / 2)


def test_a_pair_zone_shaved_under_the_floor_is_found():
    """The planted fault: zcoupon's pair at 0.1043 mm, a gap of 0.2."""
    (a, b) = _run(_fab([_strip(0, 0.1043), _strip(0.3043, 0.1043)]))
    assert a["kind"] == b["kind"] == "dfm_pour_width"
    assert abs(a["width_mm"] - 0.1043) < 2e-3 and a["min_mm"] == 0.127
    assert {round(v["pos"][1], 1) for v in (a, b)} \
        == {0.0, 0.3}


def test_pours_at_the_floor_and_their_corners_pass():
    # at the floor, a wide pour with square and acute-ish corners, a pad
    # pour with a sub-floor corner chamfer
    poly = box(0, 0, 10, 10).difference(box(4, 4, 6, 6))
    assert _run(_fab([_strip(0, 0.127), poly,
                      box(20, 0, 20.5, 0.5)])) == []


def test_one_neck_in_a_wide_pour_is_found():
    """A plane necked to 0.1 mm for 1 mm between two wide parts."""
    poly = unary_union([box(0, 0, 5, 5), box(5, 2.45, 6, 2.55),
                        box(6, 0, 11, 5)])
    (v,) = _run(_fab([poly]))
    assert abs(v["width_mm"] - 0.1) < 2e-3
    assert 5 <= v["pos"][0] <= 6


def test_a_thin_trace_is_not_a_pour_finding():
    """check_trace_width owns tracks: a pour-free layer is never read."""
    t = LineString([(0, 0), (10, 0)]).buffer(0.05)
    assert _run(_fab([], traces=[t])) == []
    # a teardrop-like pour on a floor-width track keeps it whole
    tr = LineString([(0, 0), (10, 0)]).buffer(0.0635)
    tear = box(9, -0.3, 10, 0.3)
    assert _run(_fab([tear], traces=[tr])) == []


def test_no_floor_no_check():
    vios = []
    dfm_check.check_pour_width(_fab([_strip(0, 0.05)]), {}, vios)
    assert vios == []


def test_run_finds_and_names_a_thin_pour(monkeypatch):
    """Through run, on real gerbers: the check is wired in and name_gaps
    names the zone's net at the neck."""
    import geom
    d = REPO / "tests" / "fixtures" / "stages" / "mutant_paste"
    pcb = d / "blinky2.kicad_pcb"
    z = next(z for z in geom.BoardGeom.from_file(pcb).zones_of()
             if z.net and z.fills)
    layer, polys = next(iter(z.fills.items()))
    at = polys[0].representative_point()
    seen = []

    def thin(lg, lo):
        seen.append(lg.name)
        if lg.name != layer:
            return []
        return [(at.buffer(0.05), 0.1, (at.x, at.y))]

    monkeypatch.setattr(dfm_check, "_thin_pours", thin)
    rep = dfm_check.run(pcb, fab_dir=d / "gerbers", polarity=False,
                        skip=("placement", "netlist", "teardrop", "drill",
                              "zone_fill"))
    (v,) = [x for x in rep["violations"] if x.get("kind") == "dfm_pour_width"]
    assert layer in seen and v["layer"] == layer
    assert v["net"] == z.net and v["on"][0]["item"] == "zone"


def test_clearance_between_pours_is_measured():
    """The other half of #890's first ask: check_clearance reads the
    layer's copper islands, pours included, so two pair zones 0.1 mm apart
    are a clearance finding and 0.2 mm apart are not."""
    def clear(gap):
        vios = []
        dfm_check.check_clearance(
            _fab([_strip(0, 0.3), _strip(0.3 + gap, 0.3)]), RULES, vios)
        return [v for v in vios if v["kind"] == "dfm_clearance"]
    (v,) = clear(0.1)
    assert abs(v["clearance_mm"] - 0.1) < 1e-3
    assert clear(0.2) == []
