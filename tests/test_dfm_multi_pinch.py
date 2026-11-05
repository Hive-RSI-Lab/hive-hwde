"""#732: dfm_clearance reports each pinch between two pieces of copper.

check_clearance used to compare each pair of copper pieces once and report its
closest point, so a second pinch between the same two stayed hidden until the
first was fixed. A pour can run past another piece of copper in many places.
The known answers are a comb and a bar: each tine that comes close is its own
defect. A long parallel run is one defect however long it is, and a pair with
very many pinches stops at the cap and says how many it left out.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))

import dfm_check  # noqa: E402
import gate  # noqa: E402
import geom  # noqa: E402
import test_dfm_gap_nets as nets  # noqa: E402

RULES = {"min_clearance_mm": 0.2, "min_copper_to_edge_mm": 0.3}


def _fab(*polys):
    layer = SimpleNamespace(components=lambda: list(polys))
    return SimpleNamespace(copper_layer_names=lambda: ["F.Cu"],
                           copper=lambda name: layer,
                           outline=box(0, -20, 300, 40))


def _clearance(*polys):
    vios: list = []
    dfm_check.check_clearance(_fab(*polys), RULES, vios)
    return vios


def _comb(tines, gap_of=lambda x: 0.05):
    """A spine at y 5..8 with 1 mm wide tines hanging down to the bar's top
    edge at y = 3, each stopping gap_of(x) above it."""
    return unary_union([box(0, 5, 100 + tines[-1], 8)]
                       + [box(x, 3 + gap_of(x), x + 1, 5.5) for x in tines])


BAR = box(0, 0, 100, 3)


def test_comb_and_bar_gives_a_finding_per_pinch():
    """The repro in #732: tines at 5 and 45, 40 mm apart, 0.05 mm off the bar."""
    vios = _clearance(_comb([5, 45]), BAR)
    assert [v["kind"] for v in vios] == ["dfm_clearance"] * 2
    assert all(v["clearance_mm"] == pytest.approx(0.05, abs=1e-3) for v in vios)
    xs = sorted(v["pos"][0] for v in vios)
    assert 5 <= xs[0] <= 6 and 45 <= xs[1] <= 46


def test_each_pinch_has_its_own_ends_on_the_right_sides():
    for v in _clearance(_comb([5, 45]), BAR):
        comb_end, bar_end = v["ends"]
        assert comb_end[1] == pytest.approx(3.05, abs=1e-3)
        assert bar_end[1] == pytest.approx(3.0, abs=1e-3)
        assert math.dist(comb_end, bar_end) == pytest.approx(
            v["clearance_mm"], abs=1e-3)
        assert v["items"][0]["pos"] == v["pos"]


def test_the_first_finding_of_a_pair_is_the_old_one():
    """Existing positions must not move, so a pos-pinned waiver still matches."""
    comb = _comb([5, 45], lambda x: 0.05 if x == 45 else 0.1)
    vios = _clearance(comb, BAR)
    pos, ends = dfm_check._gap(comb, BAR)
    assert vios[0]["pos"] == [dfm_check.checklib.rnd(pos[0]),
                              dfm_check.checklib.rnd(pos[1])]
    assert vios[0]["ends"] == ends
    assert vios[0]["clearance_mm"] == pytest.approx(0.05, abs=1e-3)
    assert vios[1]["clearance_mm"] == pytest.approx(0.1, abs=1e-3)


def test_a_long_parallel_run_is_one_finding():
    vios = _clearance(box(0, 3.05, 100, 8), BAR)
    assert len(vios) == 1
    assert vios[0]["clearance_mm"] == pytest.approx(0.05, abs=1e-3)
    assert "pinches_not_shown" not in vios[0]


def test_two_minima_in_one_stretch_are_two_pinches():
    """The gap never reaches the limit between them, yet they are two places:
    0.05 at x = 10 and x = 50, opening to 0.15 between."""
    upper = Polygon([(0, 8), (0, 3.15), (10, 3.05), (30, 3.15), (50, 3.05),
                     (60, 3.15), (60, 8)])
    vios = _clearance(upper, box(0, 0, 60, 3))
    assert sorted(round(v["pos"][0]) for v in vios) == [10, 50]


def test_a_shallow_ripple_on_a_parallel_run_is_one_finding():
    """A faceted edge wanders by microns: that is no second pinch."""
    points = [(0, 8), (0, 3.05)]
    points += [(x, 3.05 + (0.002 if x % 2 else 0.0)) for x in range(1, 100)]
    points += [(100, 3.05), (100, 8)]
    assert len(_clearance(Polygon(points), BAR)) == 1


def test_pinches_within_the_radius_are_one_finding():
    """One fix covers both, so they are one defect."""
    near = dfm_check.PINCH_RADIUS_MM / 2.0
    assert len(_clearance(_comb([5, 5 + 1 + near]), BAR)) == 1


def test_pinches_just_past_the_radius_are_two():
    far = dfm_check.PINCH_RADIUS_MM + 0.5
    assert len(_clearance(_comb([5, 5 + 1 + far]), BAR)) == 2


def test_a_pair_stops_at_the_cap_and_says_how_many_it_left_out():
    """The cap must not hide the count: that was the defect in the first place."""
    tines = [5 + 4 * i for i in range(20)]
    # The tightest tines are the last ones, so a cap by order would miss them.
    vios = _clearance(_comb(tines, lambda x: 0.03 + (81 - x) / 1000), BAR)
    cap = dfm_check.PINCH_CAP_PER_PAIR
    assert len(vios) == cap
    gaps = [v["clearance_mm"] for v in vios]
    assert gaps == sorted(gaps)
    assert gaps[0] == pytest.approx(0.03, abs=1e-3)
    assert vios[0]["pinches_not_shown"] == 20 - cap
    assert f"{20 - cap} more" in vios[0]["msg"]
    assert vios[0]["items"][0]["msg"] == vios[0]["msg"]
    assert all("pinches_not_shown" not in v for v in vios[1:])


def test_the_cap_is_per_pair_not_per_layer():
    tines = [5 + 4 * i for i in range(20)]
    other = [t + 150 for t in tines]
    bar2 = box(150, 0, 250, 3)
    vios = _clearance(unary_union([box(0, 5, 100, 8)] + [
        box(x, 3.05, x + 1, 5.5) for x in tines]),
        BAR,
        unary_union([box(150, 5, 250, 8)] + [
            box(x, 3.05, x + 1, 5.5) for x in other]),
        bar2)
    assert len(vios) == 2 * dfm_check.PINCH_CAP_PER_PAIR


def test_a_stretch_at_the_limit_is_no_finding():
    """Past the limit, within GEOM_TOL_MM: the same grace a whole pair gets."""
    comb = _comb([5, 45], lambda x: 0.05 if x == 5 else
                 0.2 - dfm_check.GEOM_TOL_MM / 2.0)
    assert len(_clearance(comb, BAR)) == 1


def test_touching_and_clear_pairs_still_give_nothing():
    assert _clearance(box(0, 3, 100, 8), BAR) == []
    assert _clearance(box(0, 3.3, 100, 8), BAR) == []


def test_an_island_in_a_cutout_tight_all_round_is_one_finding():
    pour = box(0, 0, 100, 40).difference(box(40, 10, 60, 30))
    island = box(40.05, 10.05, 59.95, 29.95)
    assert len(_clearance(pour, island)) == 1


def test_each_pinch_is_named_for_itself(tmp_path):
    """#722's names, per finding: each pinch gets the part beside it, which one
    finding per pair could not say."""
    comb = _comb([5, 45])
    spine_and_tines = ((0, 5, 100, 8), (5, 3.05, 6, 5.5), (45, 3.05, 46, 5.5))
    pcb = nets._board(
        tmp_path, nets._zone("/A", *spine_and_tines),
        nets._zone("GND", (0, 0, 100, 3)),
        nets._fp("U1", nets._pad(1, 5.5, 2.2, "GND", size=1.0)),
        nets._fp("U2", nets._pad(1, 45.5, 2.2, "GND", size=1.0)))
    vios = _clearance(comb, BAR)
    dfm_check.name_gaps(geom.BoardGeom.from_file(pcb), vios)
    vios.sort(key=lambda v: v["pos"][0])
    assert [v["refs"] for v in vios] == [["U1"], ["U2"]]
    assert [v["net"] for v in vios] == ["/A | GND", "/A | GND"]
    assert [v["same_net"] for v in vios] == [False, False]
    assert all(len(v["on"]) == 2 for v in vios)
    assert "U1" in vios[0]["msg"] and "U2" in vios[1]["msg"]


def test_a_pos_waiver_of_the_first_finding_leaves_the_second_standing():
    vios = _clearance(_comb([5, 45], lambda x: 0.05 if x == 5 else 0.1), BAR)
    waiver = {"check": "dfm", "kind": "dfm_clearance", "pos": vios[0]["pos"]}
    assert gate.waiver_matches(waiver, vios[0])
    assert not gate.waiver_matches(waiver, vios[1])
