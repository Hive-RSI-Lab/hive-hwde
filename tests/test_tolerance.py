"""Tolerance analysis (#495): lib/tolerance.py engine, lib/simtol.py deck
handling, sim_run's tolerance sidecar and sim_board's FB/RC reuse.

Known answers (closed forms, no engine): divider Vout EVA band exact; RSS
sigma within 5% of Monte Carlo sigma at n=2000 (seed 1); RC low-pass fc
corners exact; divider sensitivities equal the analytic partials. Live:
the same divider and RC through ngspice (needs the KiCad libngspice), and
an Xyce `.sampling` run of the divider statistically against the Python
Monte Carlo (skips without the IIC-OSIC-TOOLS tree).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import engcalc  # noqa: E402
import sim_board  # noqa: E402
import sim_run  # noqa: E402
import simlib  # noqa: E402
import simtol  # noqa: E402
import tolerance as tl  # noqa: E402
from lib import env  # noqa: E402

VIN, R1, R2 = 10.0, 10e3, 4.7e3


def divider(p):
    return {"vout": VIN * p["r2"] / (p["r1"] + p["r2"])}


def div_params(dist="uniform", t1=0.01, t2=0.05):
    return {"r1": tl.make_param("r1", R1, t1, dist=dist),
            "r2": tl.make_param("r2", R2, t2, dist=dist)}


# ------------------------------------------------------- known answers

def test_divider_eva_band_is_the_closed_form():
    res = tl.analyse(div_params(), divider, analysis="wca")
    m = res["measures"]["vout"]
    lo = VIN * R2 * 0.95 / (R1 * 1.01 + R2 * 0.95)
    hi = VIN * R2 * 1.05 / (R1 * 0.99 + R2 * 1.05)
    assert m["eva_min"] == pytest.approx(lo, rel=1e-12)
    assert m["eva_max"] == pytest.approx(hi, rel=1e-12)
    assert (m["min"], m["max"]) == (m["eva_min"], m["eva_max"])
    assert m["nom"] == pytest.approx(VIN * R2 / (R1 + R2), rel=1e-12)


@pytest.mark.parametrize("dist", ["uniform", "gauss"])
def test_rss_sigma_matches_monte_carlo_sigma(dist):
    params = div_params(dist)
    wca = tl.analyse(params, divider, analysis="wca")
    mc = tl.analyse(params, divider, analysis="mc", n=2000, seed=1)
    s_rss = wca["measures"]["vout"]["rss_sigma"]
    s_mc = mc["measures"]["vout"]["sigma"]
    assert s_mc == pytest.approx(s_rss, rel=0.05)
    assert mc["measures"]["vout"]["n"] == 2000
    # the same seed draws the same samples
    again = tl.analyse(params, divider, analysis="mc", n=2000, seed=1)
    assert again["measures"] == mc["measures"]


def test_rc_lowpass_fc_corners_exact():
    r, c = 10e3, 10e-9
    params = {"r": tl.make_param("r", r, 0.01),
              "c": tl.make_param("c", c, 0.10)}
    res = tl.analyse(params, lambda p: {"fc": engcalc.rc_corner_hz(
        p["r"], p["c"])}, analysis="corners")
    m = res["measures"]["fc"]
    assert res["corners"] == "full" and m["n"] == 4
    assert m["min"] == pytest.approx(1 / (2 * math.pi * r * 1.01 * c * 1.1),
                                     rel=1e-12)
    assert m["max"] == pytest.approx(1 / (2 * math.pi * r * 0.99 * c * 0.9),
                                     rel=1e-12)
    assert m["min_at"] == {"r": r * 1.01, "c": c * 1.1}


def test_divider_sensitivity_is_the_analytic_partial():
    res = tl.analyse(div_params(), divider, analysis="wca")
    s = res["sensitivity"]["vout"]
    assert s["r1"]["d"] == pytest.approx(-VIN * R2 / (R1 + R2) ** 2,
                                         rel=1e-6)
    assert s["r2"]["d"] == pytest.approx(VIN * R1 / (R1 + R2) ** 2,
                                         rel=1e-6)
    # normalised: dlnV/dlnR2 = R1/(R1+R2)
    assert s["r2"]["norm"] == pytest.approx(R1 / (R1 + R2), rel=1e-6)
    # R2 is 5%, R1 1%: R2 contributes most
    assert [t["param"] for t in res["top3"]["vout"]] == ["r2", "r1"]


def test_mc_yield_against_bounds():
    nom = VIN * R2 / (R1 + R2)
    bounds = [{"measure": "vout", "min": nom * 0.99, "max": nom * 1.01}]
    res = tl.analyse(div_params(), divider, analysis="mc", n=500, seed=3,
                     bounds=bounds)
    y = res["yield"]["all"]
    assert 0.2 < y < 0.9
    wide = [{"measure": "vout", "min": 0, "max": VIN}]
    assert tl.analyse(div_params(), divider, analysis="mc", n=50,
                      bounds=wide)["yield"]["all"] == 1.0


def test_corners_fall_back_to_eva_vertices_past_the_limit():
    params = {f"r{i}": tl.make_param(f"r{i}", 1e3, 0.01)
              for i in range(tl.MAX_CORNER_PARAMS + 1)}
    res = tl.analyse(params, lambda p: {"s": sum(p.values())},
                     analysis="corners")
    assert res["corners"] == "eva"
    n = len(params)
    assert res["measures"]["s"]["max"] == pytest.approx(n * 1010)


# ------------------------------------------------------- tolerance source

@pytest.mark.parametrize("spec,want", [
    ("1%", (0.01, 0.01)), ("±0.5%", (0.005, 0.005)),
    ("+-5%", (0.05, 0.05)), ("10k 1% 0603", (0.01, 0.01)),
    ("+80/-20%", (0.20, 0.80)), ("100n X7R 16V", (0.10, 0.10)),
    ("10p NP0", (0.05, 0.05)), ("Y5V", (0.20, 0.80)),
    (0.02, (0.02, 0.02)), ("0.02", (0.02, 0.02))])
def test_parse_spec(spec, want):
    lo, hi, _ = tl.parse_spec(spec)
    assert (lo, hi) == pytest.approx(want)


def test_parse_spec_value_field_numbers_are_not_tolerances():
    assert tl.parse_spec("100", bare=False) is None
    assert tl.parse_spec("10k") is None
    with pytest.raises(ValueError):
        tl.parse_spec(-0.1)


def test_part_tolerance_sources():
    p = tl.part_tolerance("R", "10k", "±0.1%")
    assert (p.lo, p.hi) == pytest.approx((9990, 10010))
    assert "tolerance field" in p.source and not p.assumed
    p = tl.part_tolerance("R", "4.7k")
    assert p.assumed and p.hi == pytest.approx(4747)
    p = tl.part_tolerance("C", "100n", footprint="C_0603_1608Metric")
    assert p.hi == pytest.approx(110e-9)  # X7R assumed between 1n and 1u
    p = tl.part_tolerance("C", "22p C0G")
    assert p.hi == pytest.approx(22e-12 * 1.05) and not p.assumed
    assert tl.part_tolerance("R", "DNP") is None


def test_part_tolerance_mlcc_dc_bias_widens_the_low_edge():
    p = tl.part_tolerance("C", "10u X5R", footprint="C_0603_1608Metric",
                          vdc=5.0)
    assert p.nom < 10e-6 and p.lo < p.nom * 0.9 and p.hi < 11e-6
    assert p.notes and "DC bias" in p.notes[0]


# ------------------------------------------------------- deck handling

DECK = """* divider
.param vref=1.2 tol_vref=0.02 tol_C=0.1
V1 in 0 DC {vref}
R1 in out 10k ; 1% 0603
R2 out 0 4.7k
RX out 0 1meg $ X7R
C1 out 0 100n
.dc V1 1 1 1
.end
"""


def test_spice_number():
    assert simtol.spice_number("4.7k") == pytest.approx(4700)
    assert simtol.spice_number("1meg") == pytest.approx(1e6)
    assert simtol.spice_number("1m") == pytest.approx(1e-3)
    assert simtol.spice_number("100nF") == pytest.approx(1e-7)
    assert simtol.spice_number("{x}") is None


def test_build_params_sources_and_render():
    params, info, assumed = simtol.build_params(
        DECK, {"tolerances": {"R2": "5%"}})
    assert params["R1"].hi == pytest.approx(10100)
    assert "deck comment" in params["R1"].source
    assert params["R2"].hi == pytest.approx(4700 * 1.05)
    assert params["RX"].hi == pytest.approx(1.1e6)  # comment names X7R
    assert params["C1"].hi == pytest.approx(110e-9)  # tol_C kind default
    assert params[".vref"].hi == pytest.approx(1.224)
    assert assumed == []
    out = simtol.render(DECK, info, {"R1": 10100.0, ".vref": 1.176})
    assert "R1 in out 10100" in out
    assert ".param vref=1.176 tol_vref=0.02" in out
    assert "R2 out 0 4.7k" in out


def test_build_params_vary_and_errors():
    params, _, _ = simtol.build_params(DECK, {"vary": ["r1", "vref"]})
    assert set(params) == {"R1", ".vref"}
    with pytest.raises(ValueError):
        simtol.build_params(DECK, {"vary": ["R9"]})
    _, _, assumed = simtol.build_params("* t\nR5 a 0 1k\n.end\n", {})
    assert assumed == ["R5"]


def test_sidecar_object_form(tmp_path):
    bp = tmp_path / "b.bounds.json"
    bp.write_text(json.dumps({"bounds": [{"measure": "v", "min": 1}],
                              "tolerance": {"analysis": "mc", "n": 50}}))
    bounds, cfg = simlib.load_sidecar(bp)
    assert bounds[0]["measure"] == "v"
    assert cfg["analysis"] == "mc" and cfg["seed"] == 1 and cfg["n"] == 50
    assert simlib.load_bounds(bp) == bounds
    for bad in ({"analysis": "x"}, {"n": 0}, {"min_yield": 2},
                {"dist": "cauchy"}, []):
        bp.write_text(json.dumps({"bounds": [{"measure": "v", "min": 1}],
                                  "tolerance": bad}))
        with pytest.raises(checklib.CheckError):
            simlib.load_sidecar(bp)
    bp.write_text(json.dumps([{"measure": "v", "min": 1}]))
    assert simlib.load_sidecar(bp)[1] is None


def _fake_batch(circuits, dll, timeout):
    """Evaluate rendered divider decks in closed form (no engine)."""
    runs = []
    for c in circuits:
        e = simtol.scan(c)["elements"]
        r1, r2 = e["R1"]["nom"], e["R2"]["nom"]
        runs.append({"measures": {"vout": VIN * r2 / (r1 + r2)}})
    return {"runs": runs}


def _divider_bench(tmp_path, tol, lo, hi):
    cir = tmp_path / "div.cir"
    cir.write_text("* div\nV1 in 0 DC 10\nR1 in out 10k ; 1%\n"
                   "R2 out 0 4.7k ; 5%\n.dc V1 9 11 1\n"
                   ".measure dc vout FIND v(out) AT=10\n.end\n")
    (tmp_path / "div.bounds.json").write_text(json.dumps(
        {"bounds": [{"measure": "vout", "min": lo, "max": hi}],
         "tolerance": tol}))
    return cir


def test_run_tolerance_wca_band_fails_a_tight_window(tmp_path, monkeypatch):
    monkeypatch.setattr(sim_run, "_run_batch", _fake_batch)
    cir = _divider_bench(tmp_path, {"analysis": "wca"}, 3.15, 3.25)
    bounds, cfg = simlib.load_sidecar(tmp_path / "div.bounds.json")
    entry = {}
    vios = sim_run.run_tolerance(cir, bounds, cfg, Path("x"), 60, 0.1, entry)
    kinds = {v["kind"] for v in vios}
    assert kinds == {"sim_tolerance_fail"}
    assert {v["edge"] for v in vios} == {"min", "max"}
    t = entry["tolerance"]
    assert t["measures"]["vout"]["eva_min"] == pytest.approx(
        VIN * R2 * 0.95 / (R1 * 1.01 + R2 * 0.95))
    assert t["top3"]["vout"][0]["param"] == "R2"
    assert "samples" not in t


def test_run_tolerance_mc_yield(tmp_path, monkeypatch):
    monkeypatch.setattr(sim_run, "_run_batch", _fake_batch)
    cir = _divider_bench(tmp_path, {"analysis": "mc", "n": 300,
                                    "min_yield": 0.99}, 3.17, 3.23)
    bounds, cfg = simlib.load_sidecar(tmp_path / "div.bounds.json")
    entry = {}
    vios = sim_run.run_tolerance(cir, bounds, cfg, Path("x"), 60, 0.1, entry)
    assert [v["kind"] for v in vios] == ["sim_yield_low"]
    assert vios[0]["severity"] == "error"
    assert entry["tolerance"]["yield"]["all"] < 0.99
    vios = sim_run.run_tolerance(cir, [{"measure": "vout", "min": 3,
                                        "max": 3.5}], cfg, Path("x"), 60,
                                 0.1, {})
    assert vios == []


# ------------------------------------------------------- sim_board reuse

def test_fb_wca_eva_equals_fb_vout_and_rss_inside():
    vref, r_top, r_bot = 0.8, 100e3, 22e3
    fb = engcalc.fb_vout(vref, r_top, r_bot, 0.01, 0.02)
    wc = sim_board.fb_wca(vref, 0.02, r_top, r_bot, 0.01)
    params = {"vref": tl.make_param("vref", vref, 0.02),
              "r_top": tl.make_param("r_top", r_top, 0.01),
              "r_bot": tl.make_param("r_bot", r_bot, 0.01)}
    res = tl.analyse(params, lambda p: {"v": p["vref"] * (
        1 + p["r_top"] / p["r_bot"])}, analysis="wca")
    assert res["measures"]["v"]["eva_min"] == pytest.approx(fb["min"],
                                                           rel=1e-12)
    assert res["measures"]["v"]["eva_max"] == pytest.approx(fb["max"],
                                                           rel=1e-12)
    assert fb["min"] < wc["rss"]["min"] < wc["rss"]["max"] < fb["max"]
    assert wc["top3"][0] == "vref"


def test_r_tolerance_reads_bom_fields():
    board = {"comps": {"R1": {"value": "10k 0.1%"}, "R2": {"value": "10k"},
                       "R3": {"value": "10k"}}}
    parts = {"R3": {"attrs": {"Tolerance": "±5%"}}}
    assert sim_board.r_tolerance(board, parts, "R1") == pytest.approx(0.001)
    assert sim_board.r_tolerance(board, parts, "R2") == pytest.approx(0.01)
    assert sim_board.r_tolerance(board, parts, "R3") == pytest.approx(0.05)


def test_rc_network_corners_in_passive_facts():
    board = {"comps": {"R1": {"value": "10k", "kind": "R"},
                       "C1": {"value": "100n X7R", "kind": "C"}},
             "nets": []}
    model = {"elems": [
        {"ref": "R1", "kind": "R", "nets": ["IN", "F"], "value": 10e3,
         "footprint": "R_0603"},
        {"ref": "C1", "kind": "C", "nets": ["F", "GND"], "value": 100e-9,
         "footprint": "C_0603"}], "grounds": {"GND"}}
    facts = {}
    sim_board.passive_facts(board, {}, model, facts, {})
    row = facts["rc_networks"][0]
    lo, hi = row["fc_corners_hz"]
    assert lo == pytest.approx(1 / (2 * math.pi * 10100 * 110e-9), rel=1e-5)
    assert hi == pytest.approx(1 / (2 * math.pi * 9900 * 90e-9), rel=1e-5)
    assert lo < row["fc_hz"] < hi


# ------------------------------------------------------- live engines

@pytest.fixture(scope="module")
def ngspice_dll():
    dll = env.find_ngspice_dll()
    if dll is None:
        pytest.skip("no ngspice shared library (KiCad bundle or "
                    "HWDE_NGSPICE_DLL)")
    return dll


def test_live_ngspice_divider_wca_and_mc(ngspice_dll, tmp_path):
    cir = _divider_bench(tmp_path, {"analysis": "wca"}, 3.0, 3.4)
    rep = sim_run.run(cir)
    assert rep["status"] == "pass", rep["violations"]
    t = rep["testbenches"][0]["tolerance"]
    m = t["measures"]["vout"]
    # rshunt=1e9 on every node moves the answer by ~1e-5 relative
    assert m["eva_min"] == pytest.approx(
        VIN * R2 * 0.95 / (R1 * 1.01 + R2 * 0.95), rel=1e-4)
    assert m["eva_max"] == pytest.approx(
        VIN * R2 * 1.05 / (R1 * 0.99 + R2 * 1.05), rel=1e-4)
    d = t["sensitivity"]["vout"]["R2"]["d"]
    assert d == pytest.approx(VIN * R1 / (R1 + R2) ** 2, rel=1e-3)
    _divider_bench(tmp_path, {"analysis": "mc", "n": 40, "seed": 2},
                   3.15, 3.25)
    rep = sim_run.run(cir)
    assert [v["kind"] for v in rep["violations"]] == ["sim_yield_low"]
    assert rep["testbenches"][0]["tolerance"]["runs"] == 45


def test_live_ngspice_rc_fc_corners(ngspice_dll, tmp_path):
    cir = tmp_path / "rc.cir"
    cir.write_text("* rc\nV1 in 0 DC 0 AC 1\nR1 in out 10k ; 1%\n"
                   "C1 out 0 10n ; 10%\n.ac dec 2000 100 100k\n"
                   ".measure ac fc WHEN vdb(out)=-3.0103 FALL=1\n.end\n")
    (tmp_path / "rc.bounds.json").write_text(json.dumps(
        {"bounds": [{"measure": "fc", "min": 1000, "max": 2000}],
         "tolerance": {"analysis": "corners"}}))
    rep = sim_run.run(cir)
    assert rep["status"] == "pass", rep["violations"]
    m = rep["testbenches"][0]["tolerance"]["measures"]["fc"]
    assert m["min"] == pytest.approx(1 / (2 * math.pi * 10.1e3 * 11e-9),
                                     rel=1e-3)
    assert m["max"] == pytest.approx(1 / (2 * math.pi * 9.9e3 * 9e-9),
                                     rel=1e-3)


def test_live_xyce_sampling_agrees_with_python_mc(tmp_path):
    import numpy as np
    import solvers
    if solvers.tool_path("xyce") is None:
        pytest.skip("no Xyce in the IIC-OSIC-TOOLS tree")
    n = 1000
    params = div_params("uniform")
    deck = tmp_path / "s.cir"
    deck.write_text(
        "* sampling divider\n.param r1v=10k r2v=4.7k\nV1 in 0 DC 10\n"
        "R1 in out {r1v}\nR2 out 0 {r2v}\n.dc V1 10 10 1\n.print dc v(out)\n"
        f".sampling param=r1v,r2v type=uniform,uniform "
        f"lower_bounds={params['r1'].lo},{params['r2'].lo} "
        f"upper_bounds={params['r1'].hi},{params['r2'].hi}\n"
        f".options samples numsamples={n} sample_type=mc seed=1 "
        "outputs={v(out)}\n.end\n")
    r = solvers.run("xyce", [deck.name], cwd=tmp_path, timeout=300)
    assert r.ok, r.stdout[-800:]
    rows = (tmp_path / "s.cir.prn").read_text().splitlines()[1:]
    x = np.array([float(ln.split()[1]) for ln in rows
                  if len(ln.split()) == 2])
    assert len(x) == n
    py = tl.analyse(params, divider, analysis="mc", n=n, seed=1)
    m = py["measures"]["vout"]
    se = m["sigma"] / math.sqrt(n)
    assert abs(x.mean() - m["mean"]) < 4 * math.sqrt(2) * se
    assert x.std(ddof=1) == pytest.approx(m["sigma"], rel=0.10)
    band = tl.analyse(params, divider, analysis="wca")["measures"]["vout"]
    assert band["eva_min"] - 1e-6 <= x.min() <= x.max() <= band["eva_max"] + 1e-6
