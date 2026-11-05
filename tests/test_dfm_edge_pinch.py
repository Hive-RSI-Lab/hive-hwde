"""#737: dfm_copper_to_edge reports each place one piece of copper nears the edge.

check_copper_to_edge took one closest point per piece, so a second tab on the
same piece stayed hidden until the first was fixed, one fab round at a time.
#732's rules apply: minima under PINCH_RADIUS_MM apart are one place, a piece
stops at the cap and says how many it left, and the old finding stays first. The
0.2-0.3 band warning had the same limit and lists every place the same way.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))

import dfm_check  # noqa: E402
import gate  # noqa: E402
import geom  # noqa: E402
import test_dfm_gap_nets as nets  # noqa: E402

RULES = {"min_copper_to_edge_mm": 0.3}
BAND = {"min_copper_to_edge_mm": 0.3, "abs_min_copper_to_edge_mm": 0.2}
OUTLINE = box(0, 0, 100, 50)
CAP = dfm_check.PINCH_CAP_PER_PAIR


def _fab(*polys, outline=OUTLINE):
    layer = SimpleNamespace(components=lambda: list(polys))
    return SimpleNamespace(copper_layer_names=lambda: ["F.Cu"],
                           copper=lambda name: layer, outline=outline)


def _edge(*polys, rules=None, outline=OUTLINE, cast=()):
    vios: list = []
    dfm_check.check_copper_to_edge(_fab(*polys, outline=outline),
                                   RULES if rules is None else rules, vios, cast)
    return vios


def _tabs(xs, gap_of=lambda x: 0.1):
    """A bar at y 10..20 with 2 mm wide tabs up to gap_of(x) below the top edge."""
    return unary_union([box(1, 10, 99, 20)]
                       + [box(x, 20, x + 2, 50 - gap_of(x)) for x in xs])


def test_two_tabs_give_two_findings():
    """The repro in #737: tabs at x = 20 and 60, each 0.1 mm off the top edge."""
    comp = unary_union([box(10, 10, 90, 20), box(20, 20, 22, 49.9),
                        box(60, 20, 62, 49.9)])
    vios = _edge(comp)
    assert [v["kind"] for v in vios] == ["dfm_copper_to_edge"] * 2
    assert all(v["severity"] == "error" for v in vios)
    assert all(v["distance_mm"] == pytest.approx(0.1, abs=1e-3) for v in vios)
    xs = sorted(v["pos"][0] for v in vios)
    assert 20 <= xs[0] <= 22 and 60 <= xs[1] <= 62


def test_each_finding_has_its_own_ends_copper_first():
    for v in _edge(_tabs([20, 60])):
        copper, edge = v["ends"]
        assert copper[1] == pytest.approx(49.9, abs=1e-3)
        assert edge[1] == pytest.approx(50.0, abs=1e-3)
        assert math.dist(copper, edge) == pytest.approx(v["distance_mm"], abs=1e-3)
        assert v["items"][0]["pos"] == v["pos"]


def test_the_first_finding_of_a_piece_is_the_old_one():
    """Existing positions must not move, so a pos-pinned waiver still matches."""
    comp = _tabs([20, 60], lambda x: 0.05 if x == 60 else 0.1)
    vios = _edge(comp)
    pos, ends = dfm_check._gap(comp, OUTLINE.boundary)
    assert vios[0]["pos"] == [dfm_check.checklib.rnd(pos[0]),
                              dfm_check.checklib.rnd(pos[1])]
    assert vios[0]["ends"] == ends
    assert vios[0]["distance_mm"] == pytest.approx(0.05, abs=1e-3)
    assert vios[1]["distance_mm"] == pytest.approx(0.1, abs=1e-3)


def test_copper_running_along_an_edge_is_one_finding():
    (v,) = _edge(box(1, 49.9, 99, 49.95))
    assert v["distance_mm"] == pytest.approx(0.05, abs=1e-3)
    assert "pinches_not_shown" not in v


def test_a_pour_tight_all_round_the_board_stays_short():
    """A square outline's corners open the gap to 0.14 mm. The sides beside the
    first are within the radius of it, and only the far side is a place of its
    own: not one per side, and the ring's seam adds none."""
    vios = _edge(box(0.1, 0.1, 99.9, 49.9))
    assert len(vios) == 2
    assert abs(vios[0]["pos"][0] - vios[1]["pos"][0]) > 50


def test_a_run_around_a_board_corner_is_one_finding():
    corner = unary_union([box(1, 49.9, 99.9, 49.95), box(99.85, 1, 99.9, 49.95)])
    assert len(_edge(corner)) == 1


def test_copper_past_the_edge_in_two_places_is_two_findings():
    vios = _edge(_tabs([20, 60], lambda x: -1.0))
    assert [v["distance_mm"] for v in vios] == [0, 0]
    assert sorted(round(v["pos"][1]) for v in vios) == [50, 50]
    assert vios[0]["pos"][0] < 30 < vios[1]["pos"][0]


def test_tabs_within_the_radius_are_one_finding():
    near = dfm_check.PINCH_RADIUS_MM / 2.0
    assert len(_edge(_tabs([20, 22 + near]))) == 1


def test_tabs_just_past_the_radius_are_two():
    far = dfm_check.PINCH_RADIUS_MM + 0.5
    assert len(_edge(_tabs([20, 22 + far]))) == 2


def test_a_piece_stops_at_the_cap_and_says_how_many_it_left_out():
    xs = [5 + 4 * i for i in range(20)]
    # The tightest tabs are the last ones, so a cap by order would miss them.
    vios = _edge(_tabs(xs, lambda x: 0.03 + (81 - x) / 1000))
    assert len(vios) == CAP
    gaps = [v["distance_mm"] for v in vios]
    assert gaps == sorted(gaps)
    assert gaps[0] == pytest.approx(0.03, abs=1e-3)
    assert vios[0]["pinches_not_shown"] == 20 - CAP
    assert f"{20 - CAP} more" in vios[0]["msg"]
    assert vios[0]["items"][0]["msg"] == vios[0]["msg"]
    assert all("pinches_not_shown" not in v for v in vios[1:])


def test_the_cap_is_per_piece():
    xs = [5 + 4 * i for i in range(10)]
    low = unary_union([box(1, 30, 99, 40)]
                      + [box(x, 0.1, x + 2, 30) for x in xs])
    assert len(_edge(_tabs(xs), low)) == 2 * CAP


def test_a_castellated_hole_exempts_only_its_own_place():
    """#743; tests/test_dfm_castellated_edge.py has the rest."""
    vios = _edge(_tabs([20, 60]), cast=[(21, 50, 1.0)])
    assert [round(v["distance_mm"], 3) for v in vios] == [0.1]
    assert vios[0]["pos"][0] > 50


def test_clear_copper_gives_nothing():
    assert _edge(_tabs([20, 60], lambda x: 0.3)) == []


def test_a_routed_slot_counts_as_edge_too():
    slot = box(40, 22, 60, 24)
    outline = OUTLINE.difference(slot)
    comp = unary_union([box(1, 10, 39, 20), box(39, 10, 61, 21.9),
                        box(61, 10, 99, 20), box(20, 20, 22, 49.9)])
    vios = _edge(comp, outline=outline)
    assert sorted(round(v["distance_mm"], 3) for v in vios) == [0.1, 0.1]


# ------------------------------------------------------- the 0.2-0.3 band

def test_band_warning_lists_each_place_on_a_piece():
    """Same limit, same fix: still one warning, one item per place."""
    vios = _edge(_tabs([20, 60], lambda x: 0.22 if x == 60 else 0.25), rules=BAND)
    (v,) = vios
    assert v["kind"] == "dfm_copper_to_edge_preferred"
    assert v["severity"] == "warning"
    assert v["count"] == 2 and len(v["items"]) == 2
    assert v["distance_mm"] == pytest.approx(0.22, abs=1e-3)
    assert 60 <= v["items"][0]["pos"][0] <= 62
    assert 20 <= v["items"][1]["pos"][0] <= 22
    assert "pinches_not_shown" not in v


def test_band_warning_stays_at_the_old_worst_place():
    comp = _tabs([20, 60], lambda x: 0.22)
    (v,) = _edge(comp, rules=BAND)
    pos, _ = dfm_check._gap(comp, OUTLINE.boundary)
    assert v["pos"] == [dfm_check.checklib.rnd(pos[0]), dfm_check.checklib.rnd(pos[1])]


def test_a_band_place_is_listed_beside_an_error_on_the_same_piece():
    """The piece used to give the error and hide the band place."""
    vios = _edge(_tabs([20, 60], lambda x: 0.1 if x == 20 else 0.25), rules=BAND)
    assert sorted(v["kind"] for v in vios) == ["dfm_copper_to_edge",
                                               "dfm_copper_to_edge_preferred"]
    err, warn = sorted(vios, key=lambda v: v["kind"])
    assert 20 <= err["pos"][0] <= 22 and "pinches_not_shown" not in err
    assert 60 <= warn["pos"][0] <= 62 and warn["count"] == 1


def test_band_places_stop_at_the_cap_per_piece_and_say_so():
    xs = [5 + 4 * i for i in range(10)]
    (v,) = _edge(_tabs(xs, lambda x: 0.25), rules=BAND)
    assert v["count"] == CAP and len(v["items"]) == CAP
    assert v["pinches_not_shown"] == 10 - CAP
    assert f"{10 - CAP} more" in v["msg"]


def test_errors_and_band_places_are_capped_apart():
    """A band place past the cap must not read as an error left out, nor crowd
    out one."""
    xs = [5 + 4 * i for i in range(10)]
    vios = _edge(_tabs(xs, lambda x: 0.1 if x < 25 else 0.25), rules=BAND)
    errs = [v for v in vios if v["kind"] == "dfm_copper_to_edge"]
    (warn,) = [v for v in vios if v["kind"].endswith("_preferred")]
    assert len(errs) == 5 and "pinches_not_shown" not in errs[0]
    assert warn["count"] == 5 and "pinches_not_shown" not in warn


# ------------------------------------------------------- names and waivers

def test_each_finding_is_named_for_itself(tmp_path):
    """#722's names, per finding: each place gets the part beside it."""
    comp = _tabs([20, 60])
    zone = nets._zone("/A", (1, 10, 99, 20), (20, 20, 22, 49.9), (60, 20, 62, 49.9))
    pcb = nets._board(tmp_path, zone,
                      nets._fp("U1", nets._pad(1, 21, 49, "/A", size=1.6)),
                      nets._fp("U2", nets._pad(1, 61, 49, "/A", size=1.6)))
    vios = _edge(comp)
    dfm_check.name_gaps(geom.BoardGeom.from_file(pcb), vios)
    vios.sort(key=lambda v: v["pos"][0])
    assert [v["refs"] for v in vios] == [["U1"], ["U2"]]
    assert [v["net"] for v in vios] == ["/A", "/A"]
    assert all(len(v["on"]) == 1 for v in vios)
    assert "U1" in vios[0]["msg"] and "U2" in vios[1]["msg"]


def test_a_pos_waiver_of_the_first_finding_leaves_the_second_standing():
    vios = _edge(_tabs([20, 60], lambda x: 0.05 if x == 20 else 0.1))
    waiver = {"check": "dfm", "kind": "dfm_copper_to_edge", "pos": vios[0]["pos"],
              "reason": "r", "approved": "test 2026-11-05"}
    assert gate.waiver_matches(waiver, vios[0])
    assert not gate.waiver_matches(waiver, vios[1])
