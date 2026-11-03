"""Known answers for the segmented plane cavity (lib/planecav.py, #498).

Closed forms: rectangle modes, a rectangle cut into pieces against the same
rectangle whole, and the Z(f) matrix against check_pdn_z's cavity sum. Published:
the L-shaped cavity against Dauge's benchmark. Exact: the right triangle's
Neumann modes for what a staircase does to a slanted edge. Live: Palace.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import check_pdn_z  # noqa: E402
import planecav as pc  # noqa: E402
import sim_solve  # noqa: E402
import solvers  # noqa: E402
from test_layout_sim import _fp, _via, _write_board, _zone  # noqa: E402

FR4 = 4.4
D = 1e-3


def rect_modes(a_m, b_m, eps_r, n):
    fs = sorted(math.hypot(i / a_m, j / b_m) for i in range(12) for j in range(12))
    return [f * pc.C0 / (2 * math.sqrt(eps_r)) for f in fs[1:n + 1]]


def errs(got, ref):
    return [abs(g / r - 1) for g, r in zip(got, ref)]


L_POLY = Polygon([(0, 0), (100, 0), (100, 50), (50, 50), (50, 100), (0, 100)])
L_RECTS = [(0, 0, 50, 100), (50, 0, 50, 50)]


# ---------------------------------------------------------------- modes

def test_one_rectangle_modes_are_the_closed_form():
    cav = pc.build([(0, 0, 100, 40)], D, FR4, 2e9)
    assert errs(cav.mode_freqs(8), rect_modes(0.1, 0.04, FR4, 8)) == pytest.approx(
        [0.0] * 8, abs=1e-5)


def test_degenerate_modes_are_both_counted():
    """A square has (1,0) and (0,1) at one frequency. Peak picking finds one."""
    f = pc.build([(0, 0, 50, 50)], D, 1.0, 5e9).mode_freqs(3)
    ref = pc.C0 / (2 * 0.05)
    assert f[0] == pytest.approx(ref, rel=1e-5)
    assert f[1] == pytest.approx(ref, rel=1e-5)
    assert f[2] == pytest.approx(ref * math.sqrt(2), rel=1e-5)


def test_two_equal_rectangles_are_the_union():
    """The issue's test: modes within 0.5 %. Modes with a node on the join are
    exact, and the even ones approach from above."""
    cav = pc.build([(0, 0, 50, 40), (50, 0, 50, 40)], D, FR4, 2e9, strip_mm=2.0)
    assert max(errs(cav.mode_freqs(6), rect_modes(0.1, 0.04, FR4, 6))) < 0.005


def test_off_centre_cut_into_four_is_the_union():
    """Corners that do not line up, so a strip meets two rectangles."""
    rects = [(0, 0, 30, 40), (30, 0, 30, 15), (30, 15, 30, 25), (60, 0, 40, 40)]
    cav = pc.build(rects, D, FR4, 2e9, strip_mm=1.0)
    assert max(errs(cav.mode_freqs(6), rect_modes(0.1, 0.04, FR4, 6))) < 0.005


def dauge_hz(eps_r=1.0):
    return [g * 1e9 for g in sim_solve.dauge_l_ghz(50.0, eps_r)]


def test_l_shape_against_the_published_benchmark():
    cav = pc.build(L_RECTS, D, 1.0, 5e9, strip_mm=2.5)
    assert max(errs(cav.mode_freqs(4), dauge_hz())) < 0.005


def test_l_shape_converges_to_the_benchmark_as_strips_shrink():
    worst = [errs(pc.build(L_RECTS, D, 1.0, 5e9, strip_mm=w).mode_freqs(4),
                  dauge_hz())[0] for w in (12.5, 6.25, 3.125, 1.5625)]
    assert worst == sorted(worst, reverse=True)
    assert worst[-1] < 0.002


def test_l_shape_scales_with_dielectric():
    cav = pc.build(L_RECTS, D, FR4, 5e9, strip_mm=2.5)
    assert max(errs(cav.mode_freqs(4), dauge_hz(FR4))) < 0.005


def test_l_shape_from_its_polygon_is_the_two_rectangles():
    cav, facts = pc.from_polygon(L_POLY, D, 1.0, 5e9)
    assert facts["area_model_mm2"] == pytest.approx(L_POLY.area)
    assert len(cav.rects) == 2
    assert max(errs(cav.mode_freqs(4), dauge_hz())) < 0.005


def test_slanted_edge_staircase_is_within_two_percent_at_the_default_grid():
    """Right isosceles triangle, exact f ~ sqrt(m^2 + n^2) for m > n >= 0. The
    vertex is nudged so no diagonal cell is exactly half full (a tie would
    round them all the same way). The error is what a board's long diagonal
    costs here, and why Palace on the true polygon is the check for one."""
    a = 100.0
    tri = Polygon([(0, 0), (a, 0.003), (a, a)])
    fs = sorted(math.hypot(m, n) for m in range(8) for n in range(m + 1))[1:5]
    ref = [f / (a * 1e-3) * pc.C0 / 2 for f in fs]
    cav, _ = pc.from_polygon(tri, D, 1.0, 3e9, cells=32)
    assert max(errs(cav.mode_freqs(4), ref)) < 0.02


# ---------------------------------------------------------------- Z(f)

PORTS = [(0.012, 0.017, 1e-3, 1e-3), (0.083, 0.031, 1e-3, 1e-3)]


def lossy(w):
    return check_pdn_z.lossy_k(w, FR4, D, 35e-6)


def test_z_of_one_rectangle_is_the_cavity_sum():
    fr = np.logspace(4, math.log10(2e9), 25)
    k_cut = 60 * math.pi / 0.1
    cav = pc.Cavity([pc.Rect(0, 0, 0.1, 0.04)], D, FR4, 1.0, k_cut)
    nn = math.ceil(0.04 * k_cut / math.pi)
    want = check_pdn_z.cavity_z(fr, 0.1, 0.04, D, FR4, PORTS, 60, nn)
    assert np.max(np.abs(cav.z(fr, PORTS, lossy_k=lossy) / want - 1)) < 1e-3


def test_z_between_ports_across_a_join_is_the_cavity_sum():
    fr = np.logspace(4, math.log10(2e8), 31)
    want = check_pdn_z.cavity_z(fr, 0.1, 0.04, D, FR4, PORTS, 60, 24)
    cav = pc.build([(0, 0, 50, 40), (50, 0, 50, 40)], D, FR4, 2e9, strip_mm=3.0,
                   k_cut=60 * math.pi / 0.1)
    got = cav.z(fr, PORTS, lossy_k=lossy)
    assert np.max(np.abs(got[:, 0, 1] / want[:, 0, 1] - 1)) < 0.01
    assert np.max(np.abs(got[:, 0, 0] / want[:, 0, 0] - 1)) < 0.02


def test_z_through_the_first_modes_converges_with_strips():
    """Up to 2 GHz, past three modes, the transfer impedance error falls with
    the strip width, which is the join's own convergence."""
    fr = np.linspace(2e8, 2e9, 61)
    want = check_pdn_z.cavity_z(fr, 0.1, 0.04, D, FR4, PORTS, 60, 24)
    e = []
    for w in (6.0, 3.0, 1.5):
        cav = pc.build([(0, 0, 50, 40), (50, 0, 50, 40)], D, FR4, 2e9, strip_mm=w,
                       k_cut=60 * math.pi / 0.1)
        z = cav.z(fr, PORTS, lossy_k=lossy)
        e.append(float(np.max(np.abs(z[:, 0, 1] / want[:, 0, 1] - 1))))
    assert e == sorted(e, reverse=True)
    assert e[-1] < 0.25


def test_sweep_interpolation_matches_the_solves_on_the_checks_grid():
    fr = np.logspace(4, math.log10(2e8), check_pdn_z.N_FREQ)
    cav = pc.build(L_RECTS, D, FR4, 2e8)
    ports = [(0.02, 0.09, 1e-3, 1e-3), (0.09, 0.02, 1e-3, 1e-3)]
    got = cav.z_sweep(fr, ports, lossy_k=lossy)
    want = cav.z(fr, ports, lossy_k=lossy)
    assert np.max(np.abs(got / want - 1)) < 1e-3


def test_low_frequency_is_the_plane_capacitance_of_the_real_area():
    cav = pc.build(L_RECTS, D, FR4, 2e9)
    f = 1e4
    z = cav.z([f], [(0.02, 0.02, 1e-3, 1e-3)], lossy_k=None)[0, 0, 0]
    c = pc.EPS0 * FR4 * 0.0075 / D
    assert abs(z) == pytest.approx(1 / (2 * math.pi * f * c), rel=1e-4)


def test_z_is_reciprocal():
    cav = pc.build(L_RECTS, D, FR4, 2e9)
    ports = [(0.02, 0.09, 1e-3, 1e-3), (0.09, 0.02, 1e-3, 1e-3)]
    z = cav.z([3e8, 9e8], ports, lossy_k=lossy)
    assert np.max(np.abs(z[:, 0, 1] / z[:, 1, 0] - 1)) < 1e-9


# ---------------------------------------------------------------- antinodes

def test_mode_report_finds_the_antinode_and_the_node():
    """TM10 of a rectangle: V ~ cos(pi x / a), antinodes on the short edges and
    a line of zero down the middle."""
    cav = pc.build([(0, 0, 100, 40)], D, FR4, 2e9)
    md = cav.mode_report(1, [(0.05, 0.02), (0.0, 0.02)])[0]
    assert md["antinode"][0] in (0.0, 0.1)
    assert md["v_marks"][0] < 0.02
    assert md["v_marks"][1] > 0.9


def test_mode_report_separates_a_degenerate_pair():
    cav = pc.build(L_RECTS, D, 1.0, 5e9, strip_mm=2.5)
    out = cav.mode_report(4)
    assert len(out) == 4
    assert out[2]["f_hz"] == pytest.approx(out[3]["f_hz"], rel=1e-3)


# ---------------------------------------------------------------- shapes

def test_decompose_is_exact_on_a_rectilinear_plane_at_any_grid():
    for h in (40.0, 7.3, 3.0):
        facts = pc.decompose(L_POLY, h)
        assert facts["area_model_mm2"] == pytest.approx(7500.0)
        assert len(facts["rects"]) == 2


def test_decompose_keeps_a_thin_slot():
    """A 0.5 mm slot across most of a 100 mm plane moves its modes a lot, and a
    grid of 3 mm cells would fill it in."""
    outer = [(0, 0), (100, 0), (100, 40), (0, 40)]
    slot = [(50, 0.0), (50.5, 0.0), (50.5, 30), (50, 30)]
    poly = Polygon(outer).difference(Polygon(slot))
    facts = pc.decompose(poly, 3.125)
    assert facts["area_model_mm2"] == pytest.approx(poly.area, rel=1e-3)
    cav, _ = pc.from_polygon(poly, D, FR4, 2e9)
    assert cav.mode_freqs(1)[0] < 0.75 * rect_modes(0.1, 0.04, FR4, 1)[0]


def test_decompose_ignores_antipads():
    plate = Polygon([(0, 0), (100, 0), (100, 60), (0, 60)])
    for x in range(5, 100, 10):
        plate = plate.difference(Polygon([(x, 30), (x + 0.6, 30), (x + 0.6, 30.6),
                                          (x, 30.6)]))
    facts = pc.decompose(plate, 100 / 32)
    assert len(facts["rects"]) == 1
    assert facts["fill"] == pytest.approx(1.0)


def test_decompose_keeps_a_cut_out():
    plate = Polygon([(0, 0), (100, 0), (100, 60), (0, 60)],
                    [[(40, 20), (60, 20), (60, 40), (40, 40)]])
    facts = pc.decompose(plate, 100 / 32)
    assert facts["area_model_mm2"] == pytest.approx(plate.area)
    assert len(facts["rects"]) >= 3


def test_components_are_largest_first_and_skip_lines():
    big = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    small = Polygon([(20, 0), (21, 0), (21, 1), (20, 1)])
    assert pc.components(big.union(small))[0].equals(big)
    assert pc.components(None) == []


# ---------------------------------------------------------------- check_pdn_z

def _meta(tmp_path, caps):
    meta = tmp_path / "decoupling.json"
    meta.write_text(json.dumps({"associations": [
        {"cap": c, "ic": "U1", "pin": "1", "rail": "PWR", "value": v}
        for c, v in caps]}), encoding="utf-8")
    return meta


def _parts(cap_at):
    body = _fp("U1", 10, 10, [("1", 0, 0, 0.5, 0.5, "F.Cu", "PWR"),
                              ("2", 1.0, 0, 0.5, 0.5, "F.Cu", "GND")])
    for name, x, y, *_ in cap_at:
        body += (_fp(name, x, y, [("1", 0, 0, 0.5, 0.5, "F.Cu", "PWR"),
                                  ("2", 1.0, 0, 0.5, 0.5, "F.Cu", "GND")])
                 + _via(x + 0.4, y + 0.6, "PWR") + _via(x + 1.9, y + 0.5, "GND"))
    return body


L_ZONE = [(0, 0), (60, 0), (60, 25), (25, 25), (25, 50), (0, 50)]
FULL = [(0, 0), (60, 0), (60, 50), (0, 50)]


def _run(tmp_path, ref_zone, rail_zone, caps=(("C1", 15, 30),), cons=None):
    body = (_zone("GND", "In1.Cu", ref_zone) + _zone("PWR", "In2.Cu", rail_zone)
            + _parts(caps))
    pcb = _write_board(tmp_path, "b", body, four=True, w=61, h=51)
    argv = ["--pcb", str(pcb), "--metadata",
            str(_meta(tmp_path, [(c[0], c[3] if len(c) > 3 else "100nF")
                                 for c in caps]))]
    if cons:
        path = tmp_path / "constraints.json"
        path.write_text(json.dumps({"power": [cons]}), encoding="utf-8")
        argv += ["--constraints", str(path)]
    return check_pdn_z.run(argv)[0]


def test_full_rectangle_keeps_the_closed_form_and_its_numbers(tmp_path):
    p = _run(tmp_path, FULL, FULL)
    pair = p["pairs"][0]
    assert pair["geometry"] == "bounding_rect_assumed"
    assert "bounding_rect" not in pair
    assert pair["c00_nf"] == pytest.approx(
        pc.EPS0 * pair["epsilon_r"] * 0.06 * 0.05 / (pair["d_mm"] * 1e-3) * 1e9,
        rel=1e-3)
    assert pair["cavity_modes"][0]["f_hz"] == pytest.approx(
        rect_modes(0.06, 0.05, pair["epsilon_r"], 1)[0], rel=1e-3)


def test_rectangle_curve_is_unchanged_by_the_shape_code(tmp_path, monkeypatch):
    """Exact, not close: the fast path never touches the segmented sums."""
    a = _run(tmp_path, FULL, FULL)["pairs"][0]
    def boom(*_a, **_k):
        raise AssertionError("segmentation ran on a rectangle")
    monkeypatch.setattr(pc, "from_polygon", boom)
    assert _run(tmp_path, FULL, FULL)["pairs"][0]["peaks"] == a["peaks"]


def test_l_shaped_plane_is_segmented_and_reports_both(tmp_path):
    p = _run(tmp_path, L_ZONE, L_ZONE)
    pair = p["pairs"][0]
    assert pair["geometry"] == "segmented"
    area = Polygon(L_ZONE).area
    assert pair["shape"]["area_model_mm2"] == pytest.approx(area)
    assert pair["shape"]["n_rect"] == 2
    assert pair["c00_nf"] == pytest.approx(
        pc.EPS0 * pair["epsilon_r"] * area * 1e-6 / (pair["d_mm"] * 1e-3) * 1e9,
        rel=1e-3)
    old = pair["bounding_rect"]
    assert old["c00_nf"] == pytest.approx(pair["c00_nf"] * 3000 / area, rel=1e-3)
    assert pair["shape"]["convergence"]["f1_shift"] < 0.02
    modes = pair["cavity_modes"]
    assert len(modes) == check_pdn_z.N_MODES
    assert modes[0]["nearest_decap"]["cap"] == "C1"
    assert 0.0 <= modes[0]["v_obs"] <= 1.0
    assert modes[0]["f_hz"] < rect_modes(0.06, 0.05, pair["epsilon_r"], 1)[0]


def test_split_plane_uses_the_island_under_the_ic(tmp_path):
    left = [(0, 0), (28, 0), (28, 50), (0, 50)]
    right = [(32, 0), (60, 0), (60, 50), (32, 50)]
    body = (_zone("GND", "In1.Cu", FULL) + _zone("PWR", "In2.Cu", left)
            + _zone("PWR", "In2.Cu", right)
            + _parts([("C1", 15, 30), ("C2", 45, 30)]))
    pcb = _write_board(tmp_path, "b", body, four=True, w=61, h=51)
    pair = check_pdn_z.run(["--pcb", str(pcb), "--metadata", str(_meta(
        tmp_path, [("C1", "100nF"), ("C2", "100nF")]))])[0]["pairs"][0]
    assert pair["geometry"] == "segmented"
    assert pair["shape"]["area_model_mm2"] == pytest.approx(28 * 50)
    assert pair["shape"]["other_islands_mm2"] == [pytest.approx(28 * 50)]
    near, far = pair["decaps"]
    assert "off_plane_mm" not in near
    assert far["off_plane_mm"] == pytest.approx(17.0, abs=0.1)
    assert far["l_path_nh"] == pytest.approx(
        17.0 * check_pdn_z.pdnlib.SPREAD_NH_PER_MM, rel=0.01)


def test_moved_verdict_warns(tmp_path):
    """A target between the two models' answers: the old model and the real
    shape disagree, and that must say so."""
    caps = (("C1", 15, 30, "1uF"), ("C2", 45, 10, "1nF"))
    cons = {"net": "PWR", "pdn_target_mohm": 1e5}
    base = _run(tmp_path, L_ZONE, L_ZONE, caps, cons)["pairs"][0]
    old = max(p["z_mohm"] for p in base["bounding_rect"]["peaks"])
    new = max(p["z_mohm"] for p in base["peaks"])
    assert old != new
    cons = {"net": "PWR", "pdn_target_mohm": 0.5 * (old + new)}
    p = _run(tmp_path, L_ZONE, L_ZONE, caps, cons)
    moved = [v for v in p["violations"] if v.get("kind") == "pdn_verdict_moved"]
    assert len(moved) == 1
    assert moved[0]["was_over"] != moved[0]["now_over"]


def test_no_warning_when_the_verdict_holds(tmp_path):
    cons = {"net": "PWR", "pdn_target_mohm": 1e7}
    p = _run(tmp_path, L_ZONE, L_ZONE, cons=cons)
    assert not [v for v in p["violations"] if v.get("kind") == "pdn_verdict_moved"]


# ---------------------------------------------------------------- Palace (live)

HAVE_TREE = solvers.toolchain_root() is not None
live = pytest.mark.skipif(not HAVE_TREE, reason="no IIC-OSIC-TOOLS tree")


@live
def test_live_palace_l_shape_is_the_benchmark():
    c = sim_solve.validate({"palace_lshape"})["cases"][0]
    assert c["status"] == "pass", c


@live
def test_live_palace_agrees_with_segmentation_on_an_l(tmp_path):
    cav, _ = pc.from_polygon(L_POLY, D, 1.0, 5e9)
    seg = [f * 1e-9 for f in cav.mode_freqs(4)]
    got = sim_solve.palace_plane_modes(tmp_path, L_POLY, 1.0, 4, seg[0], 6.0)
    assert max(errs(seg, got["f_ghz"])) < 0.02


# ---------------------------------------------------------------- --palace-check

def _palace_run(tmp_path, monkeypatch, result):
    def fake(_work, _poly, _er, _n, _f1, _size, **_k):
        if isinstance(result, Exception):
            raise result
        return {"f_ghz": result(_f1, _n), "seconds": 1.0, "size_mm": _size,
                "order": 2}
    monkeypatch.setattr(sim_solve, "palace_plane_modes", fake)
    body = (_zone("GND", "In1.Cu", L_ZONE) + _zone("PWR", "In2.Cu", L_ZONE)
            + _parts((("C1", 15, 30),)))
    pcb = _write_board(tmp_path, "b", body, four=True, w=61, h=51)
    return check_pdn_z.run(["--pcb", str(pcb), "--palace-check", "--metadata",
                            str(_meta(tmp_path, [("C1", "100nF")]))])[0]


def _kinds(p):
    return [v.get("kind") for v in p["violations"]]


def _modes_of(p):
    return [m["f_hz"] * 1e-9 for m in p["pairs"][0]["cavity_modes"]]


def test_palace_check_that_agrees_is_quiet(tmp_path, monkeypatch):
    seen = {}
    def same(_f1, n):
        return seen["modes"][:n]
    body = (_zone("GND", "In1.Cu", L_ZONE) + _zone("PWR", "In2.Cu", L_ZONE)
            + _parts((("C1", 15, 30),)))
    pcb = _write_board(tmp_path, "a", body, four=True, w=61, h=51)
    seen["modes"] = _modes_of(check_pdn_z.run(
        ["--pcb", str(pcb), "--metadata",
         str(_meta(tmp_path, [("C1", "100nF")]))])[0])
    p = _palace_run(tmp_path, monkeypatch, same)
    assert p["pairs"][0]["palace"]["status"] == "run"
    assert "palace_disagrees" not in _kinds(p)


def test_palace_check_that_disagrees_warns(tmp_path, monkeypatch):
    p = _palace_run(tmp_path, monkeypatch, lambda f1, n: [f1 * 1.2] * n)
    assert "palace_disagrees" in _kinds(p)


def test_palace_check_short_of_modes_warns(tmp_path, monkeypatch):
    p = _palace_run(tmp_path, monkeypatch, lambda f1, n: [f1])
    assert "palace_disagrees" in _kinds(p)


def test_palace_that_cannot_run_says_so(tmp_path, monkeypatch):
    p = _palace_run(tmp_path, monkeypatch, sim_solve.solvers.SolverError("no tree"))
    assert p["pairs"][0]["palace"]["status"] == "not_run"
    assert "palace_not_run" in _kinds(p)
