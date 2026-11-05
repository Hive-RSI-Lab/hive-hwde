"""#743: a castellated hole exempts the copper at the hole, not the whole piece.

check_copper_to_edge skipped every piece touching a castellated hole, so a pour
tied to a castellated pad could run past the edge anywhere with no finding. Only
the copper within the hole's radius plus CAST_EDGE_MARGIN_MM is exempt now.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import dfm_check  # noqa: E402

RULES = {"min_copper_to_edge_mm": 0.3}
BAND = {"min_copper_to_edge_mm": 0.3, "abs_min_copper_to_edge_mm": 0.2}
OUTLINE = box(0, 0, 100, 50)
BAR = box(1, 10, 99, 20)
HOLE = (21, 50, 1.0)           # on the top edge, over the tab at x = 20..22
MARGIN = dfm_check.CAST_EDGE_MARGIN_MM


def _edge(*polys, rules=RULES, cast=()):
    layer = SimpleNamespace(components=lambda: list(polys))
    fab = SimpleNamespace(copper_layer_names=lambda: ["F.Cu"],
                          copper=lambda name: layer, outline=OUTLINE)
    vios: list = []
    dfm_check.check_copper_to_edge(fab, rules, vios, cast)
    return vios


def _tabs(xs, top=49.9, width=2.0):
    """The bar with tabs up to `top` (the top edge is at y = 50)."""
    return unary_union([BAR] + [box(x, 20, x + width, top) for x in xs])


def test_the_repro_reports_the_far_tab():
    """The repro in #743: tabs at x = 20 and 60, the hole at the first."""
    vios = _edge(_tabs([20, 60]), cast=[HOLE])
    assert [v["kind"] for v in vios] == ["dfm_copper_to_edge"]
    assert vios[0]["distance_mm"] == pytest.approx(0.1)
    assert 60 <= vios[0]["pos"][0] <= 62


def test_the_far_tab_reads_as_it_does_with_no_hole():
    plain = _edge(_tabs([20, 60]))
    assert len(plain) == 2
    far = [v for v in plain if v["pos"][0] > 50]
    got = _edge(_tabs([20, 60]), cast=[HOLE])
    assert [(v["pos"], v["ends"], v["distance_mm"]) for v in got] == \
           [(v["pos"], v["ends"], v["distance_mm"]) for v in far]


def test_a_piece_close_only_at_the_hole_stays_clean():
    assert _edge(_tabs([20]), cast=[HOLE]) == []


def test_copper_all_inside_the_area_gives_nothing():
    assert _edge(box(20.8, 49.8, 21.2, 50.2), cast=[HOLE]) == []


@pytest.mark.parametrize("drill, ring", [(1.0, 0.25), (0.6, 0.25), (0.6, 0.18),
                                         (0.85, 0.2), (1.0, 0.5)])
def test_a_buildable_pad_straddling_the_edge_stays_clean(drill, ring):
    """castellated_fp's pad: drill + 2 ring wide, ring past the hole outward,
    the extension inward, with a track leaving it. The pad's sides cross the
    edge, so the area has to reach past them."""
    half = drill / 2 + ring
    pad = box(50 - half, 50 - drill / 2 - 0.8, 50 + half, 50 + half)
    piece = unary_union([pad, box(49.9, 20, 50.1, 49), BAR])
    assert _edge(piece, cast=[(50, 50, drill)]) == []
    # the same pad with no castellated hole is an ordinary edge error
    assert _edge(piece) != []


def test_the_margin_both_ways():
    """A tab's corner just inside the area is exempt and just outside is not."""
    r = HOLE[2] / 2
    # the tab's near side at x, its top 0.1 mm under the edge; the area ends
    # r + margin from the hole, and the tab is close as far down as 0.3 mm
    reach = ((r + MARGIN) ** 2 - 0.3 ** 2) ** 0.5
    inside = box(21 + reach - 0.6, 20, 21 + reach - 0.05, 49.9)
    outside = box(21 + r + MARGIN + 0.05, 20, 21 + r + MARGIN + 0.6, 49.9)
    feed = box(20.8, 20, 21.2, 49.6)     # touches the hole, 0.4 mm off the edge
    assert _edge(unary_union([BAR, feed, inside]), cast=[HOLE]) == []
    assert len(_edge(unary_union([BAR, feed, outside]), cast=[HOLE])) == 1


def test_a_run_along_the_edge_is_reported_either_side_of_the_hole():
    """Copper 0.1 mm off the edge passing through the hole: what is left past
    the area on each side is still too close."""
    run = unary_union([box(10, 49.0, 32, 49.9), box(20.8, 49.9, 21.2, 50)])
    vios = _edge(run, cast=[HOLE])
    assert len(vios) == 2
    xs = sorted(v["pos"][0] for v in vios)
    assert xs[0] < 21 - 0.5 and xs[1] > 21 + 0.5


def test_each_hole_on_a_piece_exempts_its_own_copper():
    cast = [HOLE, (61, 50, 1.0)]
    assert _edge(_tabs([20, 60]), cast=cast) == []
    vios = _edge(_tabs([20, 60, 80]), cast=cast)
    assert len(vios) == 1 and 80 <= vios[0]["pos"][0] <= 82


def test_a_hole_exempts_only_the_piece_it_touches():
    """A separate piece inside the area never touched the hole, so nothing
    exempts it, as before."""
    apart = box(21.6, 40, 22, 49.9)
    assert len(_edge(_tabs([20]), apart, cast=[HOLE])) == 1


def test_the_hole_of_another_piece_leaves_a_piece_whole():
    """A piece no hole touches is measured as it always was."""
    other = _tabs([20, 60])
    far_hole = [(5, 0, 1.0)]
    assert [v["pos"] for v in _edge(other, cast=far_hole)] == \
           [v["pos"] for v in _edge(other)]


def test_the_exempt_copper_does_not_use_up_the_cap():
    xs = [5 + 8 * i for i in range(7)]         # 7 tabs, the hole at the first
    vios = _edge(_tabs(xs), cast=[(6, 50, 1.0)])
    assert len(vios) == dfm_check.PINCH_CAP_PER_PAIR
    assert vios[0]["pinches_not_shown"] == 1
    assert all(v["pos"][0] > 7 for v in vios)


def test_the_band_warning_leaves_out_the_hole_too():
    piece = _tabs([20, 60], top=49.75)         # 0.25: in the 0.2-0.3 band
    plain = _edge(piece, rules=BAND)
    assert plain[0]["count"] == 2
    vios = _edge(piece, rules=BAND, cast=[HOLE])
    assert [v["kind"] for v in vios] == ["dfm_copper_to_edge_preferred"]
    assert vios[0]["count"] == 1
    assert 60 <= vios[0]["items"][0]["pos"][0] <= 62
    assert _edge(_tabs([20], top=49.75), rules=BAND, cast=[HOLE]) == []
