"""#700: OSH Park's 4 and 6 layer services hold a plated hole 10 mil (0.254 mm)
from inner-layer copper it isn't joined to, over their 5 mil copper clearance.
The floor is in the vendor row (min_hole_to_inner_copper_mm); rules_gen writes
it as a (layer inner) hole_clearance rule that check_dru holds, dfm_check
measures it on the gerbers, and order_quote no longer quotes OSH Park a
green HASL board they don't make.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import Point, box

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "bin"), str(REPO / "scripts"), str(REPO / "scripts" / "lib")]

import dfm_check  # noqa: E402
import fabfloors  # noqa: E402
import order_quote  # noqa: E402
import rules_gen  # noqa: E402
import strength  # noqa: E402
import vendors  # noqa: E402

KEY = fabfloors.INNER_HOLE_KEY
RULES = {KEY: 0.254}
HOLE = SimpleNamespace(x=10.0, y=10.0, diameter=0.3, plated=True)


def _osh(row: str) -> dict:
    return vendors.load("oshpark")["design_rules"][row]


def _plane(antipad: float | None):
    """A plane around HOLE with a round antipad `antipad` from its wall
    (None: the plane is tied to the hole, no antipad)."""
    p = box(0, 0, 20, 20)
    if antipad is not None:
        p = p.difference(Point(HOLE.x, HOLE.y).buffer(HOLE.diameter / 2 + antipad, 64))
    return p


def _fab(comps, layers=("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"), holes=(HOLE,)):
    def copper(name):
        if name.startswith("In"):
            return SimpleNamespace(components=lambda: list(comps))
        return SimpleNamespace(components=lambda: [])
    return SimpleNamespace(copper_layer_names=lambda: list(layers),
                           copper=copper, holes=list(holes))


def _found(fab, rules=RULES):
    vios: list = []
    dfm_check.check_hole_to_inner(fab, rules, vios)
    return vios


def test_the_floor_is_on_oshparks_multilayer_rows_only():
    assert _osh("4layer_1oz")[KEY] == pytest.approx(0.254)
    assert _osh("6layer_1oz")[KEY] == pytest.approx(0.254)
    assert KEY not in _osh("2layer_1oz")       # no inner copper
    # over the 5 mil copper clearance, which is why the plain rule misses it
    assert _osh("4layer_1oz")[KEY] > _osh("4layer_1oz")["min_clearance_mm"]


def test_an_antipad_under_the_floor_is_caught_on_each_inner_layer():
    vios = _found(_fab([_plane(0.15)]))
    assert [v["layer"] for v in vios] == ["In1.Cu", "In2.Cu"]
    v = vios[0]
    assert (v["kind"], v["severity"]) == ("dfm_hole_to_inner_copper", "error")
    assert v["distance_mm"] == pytest.approx(0.15, abs=1e-3)
    assert v["min_mm"] == 0.254 and v["pos"] == [10.0, 10.0]
    assert "dfm_hole_to_inner_copper" in dfm_check.DFM_FAMILIES["drill"]


@pytest.mark.parametrize("comps, holes, rules", [
    ([_plane(0.30)], (HOLE,), RULES),                   # antipad wide enough
    ([_plane(None)], (HOLE,), RULES),                   # the hole's own plane
    ([_plane(0.15)], (SimpleNamespace(x=10.0, y=10.0, diameter=0.3,
                                      plated=False),), RULES),  # NPTH
    ([_plane(0.15)], (HOLE,), {}),                      # no published floor
])
def test_what_is_not_a_finding(comps, holes, rules):
    assert _found(_fab(comps, holes=holes), rules) == []


def test_outer_copper_is_not_held_to_it():
    assert _found(_fab([_plane(0.15)], layers=("F.Cu", "B.Cu"))) == []


def test_the_plane_beside_its_own_pad_is_measured_from_the_hole():
    """An unused inner pad joins the hole; the plane past it is the gap."""
    pad = Point(HOLE.x, HOLE.y).buffer(0.3, 64)
    assert _found(_fab([pad, _plane(0.30)])) == []      # ring 0.15, gap 0.15
    near = _found(_fab([pad, _plane(0.20)]))
    assert near and near[0]["distance_mm"] == pytest.approx(0.20, abs=1e-3)


def test_rules_gen_writes_it_and_check_dru_holds_it():
    _, cap = fabfloors.vendor_profile("oshpark", 4, 1.0)   # what board_init uses
    rule = {r.name: r for r in rules_gen.baseline_rules(cap)}[fabfloors.INNER_HOLE_RULE]
    assert (rule.constraint, rule.minv, rule.layer) == ("hole_clearance", 0.254, "inner")
    dru = "(version 1)\n" + "\n".join(r.render() for r in rules_gen.baseline_rules(cap))
    assert fabfloors.check_dru(dru, cap) == []
    for bad in (dru.replace(f'"{fabfloors.INNER_HOLE_RULE}"', '"renamed"'),
                dru.replace("(constraint hole_clearance (min 0.2540mm))",
                            "(constraint hole_clearance (min 0.1270mm))"),
                dru.replace("\t(layer inner)\n", "")):
        assert any(fabfloors.INNER_HOLE_RULE in m for m in fabfloors.check_dru(bad, cap))
    jlc = fabfloors.load_capabilities()["4layer_1oz"]
    assert fabfloors.INNER_HOLE_RULE not in {r.name for r in rules_gen.baseline_rules(jlc)}


def test_a_lowered_vendor_floor_lets_the_planted_fault_through(tmp_path, monkeypatch):
    src = REPO / "tests" / "fixtures" / "stages" / "pd_trigger" / "fab" / "gerbers"
    assert all(ok for *_, ok in strength.inner_faults(src, tmp_path / "a"))
    real = vendors.load

    def lowered(v, *a, **k):
        prof = real(v, *a, **k)
        for row in prof["design_rules"].values():
            if KEY in row:
                row[KEY] = 0.1
        return prof
    monkeypatch.setattr(vendors, "load", lowered)
    assert not any(ok for *_, ok in strength.inner_faults(src, tmp_path / "b"))


def test_oshpark_is_quoted_purple_enig():
    led = REPO / "boards" / "hwde-led-test" / "kicad" / "hwde-led-test.kicad_pcb"
    rep = order_quote.run(led, [3], ["HASL", "ENIG"], ["green"], vendor="oshpark")
    assert {(r["surface_finish"], r["solder_mask_color"]) for r in rep["matrix"]} \
        == {("ENIG", "purple")}
    assert order_quote._offered(["enig", "HASL"], ["ENIG"]) == ["enig"]
    assert order_quote._offered(["HASL"], None) == ["HASL"]
