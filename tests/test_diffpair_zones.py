"""A diff pair drawn as two zones is judged, not skipped (#890).

hw-lab zcoupon draws its controlled-impedance pairs as zone strips with no
tracks; check_diffpair reported "pair not fully routed; coupling not
evaluated". Now each layer both nets fill is sampled along the coupled run:
width per side, edge gap, Zdiff as drawn, and a side narrowed against its
partner. Planted faults: the same pair drawn as tracks gives the same Zdiff;
one side narrowed over a stretch is found.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import check_diffpair  # noqa: E402
from test_t2_diffpair import _board, _straight_pair  # noqa: E402

SPEC = {"p": "/D_P", "n": "/D_N", "gap_mm": 0.3}


def _zone(net, pts, layer="F.Cu"):
    xy = " ".join(f"(xy {x} {y})" for x, y in pts)
    return (f'  (zone (net "{net}") (layer "{layer}")\n'
            f'    (polygon (pts {xy}))\n'
            f'    (filled_polygon (layer "{layer}") (pts {xy})))\n')


def _strip(net, y0, w, notch=None):
    """A 2-18 mm strip of width w from y0; notch=(x0, x1, w2) narrows it
    from its far side to w2 there."""
    if notch is None:
        return _zone(net, [(2, y0), (18, y0), (18, y0 + w), (2, y0 + w)])
    x0, x1, w2 = notch
    sgn = 1 if net.endswith("P") else -1     # P sits below N
    far = y0 if sgn > 0 else y0 + w
    cut = far + sgn * (w - w2)
    near = y0 + w if sgn > 0 else y0
    return _zone(net, [(2, near), (18, near), (18, far), (x1, far), (x1, cut),
                       (x0, cut), (x0, far), (2, far)])


def _zone_pair(tmp_path_factory, name, notch=None):
    # P 4.85-5.15, N 5.45-5.75: _straight_pair's 0.3 mm tracks on 5.0 and
    # 5.6 centres (edge gap 0.3) drawn as zones instead
    body = (_strip("/D_P", 4.85, 0.3)
            + _strip("/D_N", 5.45, 0.3, notch))
    return _board(tmp_path_factory, name, body)


def test_a_zone_pair_is_judged_like_its_tracks(tmp_path_factory):
    bg = _zone_pair(tmp_path_factory, "zp")
    vs, facts = check_diffpair.check_pair(bg, SPEC)
    assert facts.get("routed") is not False and facts["drawn_as"] == "zones"
    row = facts["layers"]["F.Cu"]
    assert row["width_p_mm"] == pytest.approx(0.3, abs=1e-3)
    assert row["width_n_mm"] == pytest.approx(0.3, abs=1e-3)
    assert row["gap_mm"] == pytest.approx(0.3, abs=1e-3)
    assert row["coupled_mm"] > 14
    # the same pair drawn as tracks solves to the same Zdiff
    _, tf = check_diffpair.check_pair(
        _straight_pair(tmp_path_factory, "zt"), SPEC)
    assert row["zdiff_ohm"] == pytest.approx(tf["impedance"]["zdiff_ohm"],
                                             rel=0.01)
    assert vs == []
    # a target it misses is flagged, as for tracks
    vs, _ = check_diffpair.check_pair(bg, {**SPEC, "impedance_ohm": 90})
    assert [v["kind"] for v in vs] == ["diffpair_impedance"]
    assert vs[0]["drawn_as"] == "zones" and vs[0]["layer"] == "F.Cu"


def test_one_side_narrowed_is_found(tmp_path_factory):
    bg = _zone_pair(tmp_path_factory, "zn", notch=(6, 12, 0.2))
    vs, facts = check_diffpair.check_pair(bg, SPEC)
    (v,) = [v for v in vs if v["kind"] == "diffpair_width_imbalance"]
    assert v["net"] == "/D_N" and v["width_n_mm"] == pytest.approx(0.2, abs=1e-3)
    assert v["stretch_mm"] == pytest.approx(6, abs=0.3)
    assert facts["layers"]["F.Cu"]["min_width_n_mm"] == pytest.approx(0.2,
                                                                       abs=1e-3)
    # a notch shorter than the stretch limit is the fab floor's, not this
    vs, _ = check_diffpair.check_pair(
        _zone_pair(tmp_path_factory, "zs", notch=(6, 6.5, 0.2)), SPEC)
    assert "diffpair_width_imbalance" not in {v["kind"] for v in vs}
    # inside the limit is balanced
    vs, _ = check_diffpair.check_pair(
        _zone_pair(tmp_path_factory, "zk", notch=(6, 12, 0.28)), SPEC)
    assert vs == []


def test_a_sense_zone_pair_is_held_to_no_impedance(tmp_path_factory):
    bg = _zone_pair(tmp_path_factory, "zsn")
    vs, facts = check_diffpair.check_pair(
        bg, {**SPEC, "impedance_ohm": 50, "kind": "sense"})
    assert vs == [] and facts["kind"] == "sense"


def test_run_counts_a_zone_pair_as_judged(tmp_path_factory):
    bg = _zone_pair(tmp_path_factory, "zr")
    payload, _ = check_diffpair.run(["--pcb", str(bg.path)])
    assert not any("not fully routed" in s for s in payload["not_judged"])
    assert payload["checked"][0]["drawn_as"] == "zones"
