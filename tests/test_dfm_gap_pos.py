"""#672: dfm_clearance and dfm_copper_to_edge report where the gap is.

A clearance finding used to give the point on one component nearest the
other's centroid, and an edge finding the component's centroid. On a pour
those are many mm from the pinch (precision-daq: nofill rectangles placed
around the reported spots did nothing). The known answers here are shapes
whose only pinch is at one corner, far from both centroids.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon, box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import dfm_check  # noqa: E402

RULES = {"min_clearance_mm": 0.2, "min_copper_to_edge_mm": 0.3}
TOL = 0.05


def _fab(*polys, outline=box(0, 0, 200, 200)):
    layer = SimpleNamespace(components=lambda: list(polys))
    return SimpleNamespace(copper_layer_names=lambda: ["F.Cu"],
                           copper=lambda name: layer, outline=outline)


def _off(pos, x, y):
    return math.hypot(pos[0] - x, pos[1] - y)


# A pour's corner at (60, 10), 0.1 mm from a second pour's flat side at
# x = 60.1. The pour's centroid is far away, and so is the point on its
# outline nearest the other's centroid.
POUR = Polygon([(50, 0), (60, 10), (50, 60), (45, 30)])
FLAT = box(60.1, 0, 90, 100)


def test_clearance_pos_is_the_pinch_between_two_pours():
    vios: list = []
    dfm_check.check_clearance(_fab(POUR, FLAT), RULES, vios)
    (v,) = vios
    assert v["kind"] == "dfm_clearance"
    assert v["clearance_mm"] == pytest.approx(0.1, abs=1e-3)
    assert _off(v["pos"], 60.05, 10) <= TOL
    assert v["items"][0]["pos"] == v["pos"]


def test_clearance_gives_both_ends_of_the_gap():
    vios: list = []
    dfm_check.check_clearance(_fab(POUR, FLAT), RULES, vios)
    (a, b) = sorted(vios[0]["ends"])
    assert a == pytest.approx([60, 10], abs=1e-3)
    assert b == pytest.approx([60.1, 10], abs=1e-3)
    assert math.dist(a, b) == pytest.approx(vios[0]["clearance_mm"], abs=1e-3)


def test_clearance_pos_inside_a_pour_cutout():
    """An island in a pour's hole: the gap is to the hole's edge, which is
    the pour's interior ring, not its exterior."""
    pour = box(0, 0, 100, 100).difference(box(40, 40, 60, 60))
    island = Polygon([(40.1, 50), (50, 45), (59, 50), (50, 55)])
    vios: list = []
    dfm_check.check_clearance(_fab(pour, island), RULES, vios)
    (v,) = vios
    assert _off(v["pos"], 40.05, 50) <= TOL


def test_edge_pos_is_the_pinch():
    tip = Polygon([(0.1, 100), (40, 20), (40, 180)])
    vios: list = []
    dfm_check.check_copper_to_edge(_fab(tip), RULES, vios)
    (v,) = vios
    assert v["kind"] == "dfm_copper_to_edge"
    assert v["distance_mm"] == pytest.approx(0.1, abs=1e-3)
    assert _off(v["pos"], 0.05, 100) <= TOL
    assert sorted(v["ends"]) == [pytest.approx([0, 100], abs=1e-3),
                                 pytest.approx([0.1, 100], abs=1e-3)]


def test_edge_pos_at_a_routed_slot():
    """A slot is an edge too (the outline's interior ring)."""
    outline = box(0, 0, 200, 200).difference(box(90, 90, 110, 110))
    tip = Polygon([(40, 60), (40, 140), (89.9, 100)])
    vios: list = []
    dfm_check.check_copper_to_edge(_fab(tip, outline=outline), RULES, vios)
    (v,) = vios
    assert _off(v["pos"], 89.95, 100) <= TOL


def test_edge_copper_past_the_outline_reports_a_point_on_the_edge():
    over = box(-1, 50, 40, 52)
    vios: list = []
    dfm_check.check_copper_to_edge(_fab(over), RULES, vios)
    (v,) = vios
    assert v["distance_mm"] == 0
    assert v["pos"][0] == pytest.approx(0, abs=1e-3)
    assert 50 <= v["pos"][1] <= 52


def test_edge_band_warning_points_at_each_pinch():
    """The 0.2-0.3 band warning shares the position: worst first, one item
    per pinch."""
    near = Polygon([(0.25, 100), (40, 20), (40, 180)])
    nearer = Polygon([(199.78, 100), (160, 20), (160, 180)])
    rules = {"min_copper_to_edge_mm": 0.3, "abs_min_copper_to_edge_mm": 0.2}
    vios: list = []
    dfm_check.check_copper_to_edge(_fab(near, nearer), rules, vios)
    (v,) = vios
    assert v["kind"] == "dfm_copper_to_edge_preferred"
    assert _off(v["pos"], 199.89, 100) <= TOL
    assert _off(v["items"][0]["pos"], 199.89, 100) <= TOL
    assert _off(v["items"][1]["pos"], 0.125, 100) <= TOL
