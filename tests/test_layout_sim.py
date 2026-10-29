"""Layout-simulation leg acceptance tests: check_irdrop + check_pdn_z.

check_irdrop (2.5D resistor-grid FDM, DC IR-drop + sheet current density):
  - analytic validation, hermetic: rectangle strip R = rho/t * L/W (2%),
    Trefethen L-corner adds 0.5587 +/- 0.02 squares, grid-doubling
    determinism, via-coupled two-layer sheets halve resistance;
  - corpus: blinky2 golden clean/advisory; the undersized-power-trace mutant
    shows the current-density maximum AT the 0.16 mm neck (inside the
    manifest region [117,105]-[120,112]) markedly above the golden's;
  - pd-trigger VBUS/GND 5 A maps < 30 s each (smoke: wall-clock sensitive).

check_pdn_z (plane-pair cavity impedance):
  - hermetic: C00 exact at low f, delta_mod low-frequency clip, modal-weight
    spot check vs a directly-coded small term sum, single-decap antiresonance
    between decap SRF and the plane response, double-M convergence fact;
  - corpus: usbbuck4 advisory (2 plane pairs), blinky2 (no pairs) clean.

All tests are pure-venv (committed boards + synthetic boards in tmp); only
the pd-trigger timing test carries the `smoke` marker.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden"
PYTHON = sys.executable
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_irdrop  # noqa: E402
from _perf import assert_under  # noqa: E402
import check_pdn_z  # noqa: E402
import geom  # noqa: E402

# undersized-power-trace manifest region (tests/golden/manifest.yaml: the
# mutated 0.16 mm segment runs [118.5, 106.95] -> [118.5, 110.5])
NECK_BOX = (117.0, 105.0, 120.0, 112.0)


# ---- synthetic boards ----------------------------------------------------

def _fp(ref: str, x: float, y: float, pads) -> str:
    body = "".join(
        f'    (pad "{n}" smd rect (at {px} {py}) (size {w} {h}) '
        f'(layers "{layer}") (net "{net}"))\n'
        for n, px, py, w, h, layer, net in pads)
    return (f'  (footprint "T:{ref}" (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 0) '
            f'(layer "F.SilkS"))\n{body}  )\n')


def _zone(net: str, layer: str, pts) -> str:
    s = " ".join(f"(xy {x} {y})" for x, y in pts)
    return (f'  (zone (net "{net}") (layer "{layer}")\n'
            f'    (polygon (pts {s}))\n'
            f'    (filled_polygon (layer "{layer}") (pts {s})))\n')


def _via(x, y, net, size=0.9, drill=0.6) -> str:
    return (f'  (via (at {x} {y}) (size {size}) (drill {drill}) '
            f'(layers "F.Cu" "B.Cu") (net "{net}"))\n')


_LAYERS2 = '(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))'
_LAYERS4 = ('(layers (0 "F.Cu" signal) (4 "In1.Cu" signal) '
            '(6 "In2.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))')


def _write_board(dirpath: Path, name: str, body: str, *, four=False,
                 w=25.0, h=8.0) -> Path:
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  {_LAYERS4 if four else _LAYERS2}
  (setup)
  (gr_rect (start -1 -1) (end {w} {h}) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = dirpath / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def _board(tmp_path_factory, name: str, body: str, **kw) -> geom.BoardGeom:
    return geom.load_board(
        _write_board(tmp_path_factory.mktemp(name), name, body, **kw))


# strip: 20 x 2 mm F.Cu sheet, thin full-width end pads (source J1, sink J2)
STRIP_RECT = [(0, 0), (20, 0), (20, 2), (0, 2)]
STRIP_BODY = (
    _zone("P", "F.Cu", STRIP_RECT)
    + _fp("J1", 0.6, 1.0, [("1", 0, 0, 0.2, 2.0, "F.Cu", "P")])
    + _fp("J2", 19.4, 1.0, [("1", 0, 0, 0.2, 2.0, "F.Cu", "P")]))
STRIP_ENTRY = {"net": "P", "current_a": 1.0, "source_ref": "J1",
               "sinks": [{"ref": "J2", "current_a": 1.0}], "cell_mm": 0.05}
RS_1OZ = check_irdrop.sheet_res_ohm_sq(0.035)


# ============================================================ pure: irdrop

def test_sheet_resistance_in_contract_band():
    # 1 oz (0.035 mm) copper: 0.48-0.51 mOhm/sq (rho_cu = 1.72e-8 Ohm-m)
    assert 0.48e-3 <= RS_1OZ <= 0.51e-3
    # via barrel: 0.3 mm drill / 1.53 mm span / 25 um wall ~ 1 mOhm
    assert check_irdrop.via_res_ohm(0.3, 1.53) == pytest.approx(1.03e-3,
                                                                rel=0.1)


def test_strip_resistance_matches_analytic(tmp_path_factory):
    bg = _board(tmp_path_factory, "strip", STRIP_BODY)
    vs, facts = check_irdrop.check_net(bg, dict(STRIP_ENTRY))
    assert vs == []
    # 18.6 mm between the facing pad edges, 2 mm wide -> 9.3 squares
    analytic_mohm = RS_1OZ * (18.6 / 2.0) * 1e3
    assert facts["resistance_mohm"] == pytest.approx(analytic_mohm, rel=0.02)
    # uniform sheet current: 1 A / 2 mm = 0.5 A/mm
    assert facts["jmax"]["a_per_mm"] == pytest.approx(0.5, rel=0.05)
    # Richardson pair present and converged
    assert facts["grid"]["refine"] == "doubled"
    assert len(facts["resistance_pair_mohm"]) == 2
    assert facts["richardson_delta"] < 0.02


def test_l_corner_trefethen(tmp_path_factory):
    # L: 5-square arm + corner square + 5-square arm, unit width;
    # straight control: 11 squares. Identical end pads cancel end effects:
    # corner_squares = R_L/Rs - R_S/Rs + 1 = 1 - 2*ln2/pi = 0.5587288.
    l_body = (
        _zone("P", "F.Cu", [(0, 0), (6, 0), (6, 6), (5, 6), (5, 1), (0, 1)])
        + _fp("J1", 0.3, 0.5, [("1", 0, 0, 0.2, 1.0, "F.Cu", "P")])
        + _fp("J2", 5.5, 5.7, [("1", 0, 0, 1.0, 0.2, "F.Cu", "P")]))
    s_body = (
        _zone("P", "F.Cu", [(0, 0), (11, 0), (11, 1), (0, 1)])
        + _fp("J1", 0.3, 0.5, [("1", 0, 0, 0.2, 1.0, "F.Cu", "P")])
        + _fp("J2", 10.7, 0.5, [("1", 0, 0, 0.2, 1.0, "F.Cu", "P")]))
    entry = dict(STRIP_ENTRY)
    _, f_l = check_irdrop.check_net(
        _board(tmp_path_factory, "lcorner", l_body, w=8, h=8), dict(entry))
    _, f_s = check_irdrop.check_net(
        _board(tmp_path_factory, "straight", s_body, w=13, h=3), dict(entry))
    r_l_sq = f_l["resistance_mohm"] / 1e3 / RS_1OZ
    r_s_sq = f_s["resistance_mohm"] / 1e3 / RS_1OZ
    corner = r_l_sq - r_s_sq + 1.0
    assert corner == pytest.approx(0.5587288, abs=0.02)


def test_grid_doubling_determinism(tmp_path_factory):
    d = tmp_path_factory.mktemp("determ")
    pcb = _write_board(d, "determ", STRIP_BODY)
    cons = d / "constraints.json"
    cons.write_text(json.dumps({"power": [STRIP_ENTRY]}), encoding="utf-8")
    argv = ["--pcb", str(pcb), "--constraints", str(cons)]
    p1, _ = check_irdrop.run(list(argv))
    p2, _ = check_irdrop.run(list(argv))
    # checklib stamps every payload with wall-clock generated_at; two runs
    # straddling a second boundary are still deterministic (U7 full-suite
    # flake, LEARNINGS 2026-08-14) - compare everything BUT the stamp.
    for p in (p1, p2):
        p.pop("generated_at", None)
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


def test_two_layer_via_coupled_parallel_sheets(tmp_path_factory):
    # identical F+B sheets tied by 3 fat vias per end: R halves (via barrels
    # + discretization keep it within a few % of exactly half)
    vias = "".join(_via(x, y, "P") for x in (0.5, 19.5)
                   for y in (0.25, 0.5, 0.75))
    two = (_zone("P", "F.Cu", [(0, 0), (20, 0), (20, 1), (0, 1)])
           + _zone("P", "B.Cu", [(0, 0), (20, 0), (20, 1), (0, 1)])
           + vias
           + _fp("J1", 0.3, 0.5, [("1", 0, 0, 0.2, 1.0, "F.Cu", "P")])
           + _fp("J2", 19.7, 0.5, [("1", 0, 0, 0.2, 1.0, "F.Cu", "P")]))
    one = (_zone("P", "F.Cu", [(0, 0), (20, 0), (20, 1), (0, 1)])
           + _fp("J1", 0.3, 0.5, [("1", 0, 0, 0.2, 1.0, "F.Cu", "P")])
           + _fp("J2", 19.7, 0.5, [("1", 0, 0, 0.2, 1.0, "F.Cu", "P")]))
    entry = dict(STRIP_ENTRY)
    _, f1 = check_irdrop.check_net(
        _board(tmp_path_factory, "one_sheet", one, w=22, h=3), dict(entry))
    _, f2 = check_irdrop.check_net(
        _board(tmp_path_factory, "two_sheet", two, w=22, h=3), dict(entry))
    assert f2["vias"]["used"] == 6
    ratio = f2["resistance_mohm"] / f1["resistance_mohm"]
    assert ratio == pytest.approx(0.5, abs=0.06)


def test_limits_violations_and_cli_exit_codes(tmp_path):
    pcb = _write_board(tmp_path, "limits", STRIP_BODY)
    entry = dict(STRIP_ENTRY)
    entry["irdrop_mv_max"] = 0.5      # measured drop ~4.6 mV -> violation
    entry["jmax_a_per_mm"] = 0.1      # measured ~0.5 A/mm -> violation
    cons = tmp_path / "constraints.json"
    cons.write_text(json.dumps({"power": [entry]}), encoding="utf-8")
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [PYTHON, str(SCRIPTS / "check_irdrop.py"), "--pcb", str(pcb),
         "--constraints", str(cons), "--out", str(out)],
        capture_output=True, text=True)
    assert proc.returncode == 1, proc.stderr
    rep = json.loads(out.read_text(encoding="utf-8"))
    kinds = {v["kind"] for v in rep["violations"]}
    assert {"irdrop_excess", "current_density_excess"} <= kinds
    assert all(v["severity"] == "error" for v in rep["violations"]
               if v["kind"] != "grid_unconverged")
    # advisory (no limits) -> exit 0
    cons.write_text(json.dumps({"power": [STRIP_ENTRY]}), encoding="utf-8")
    proc = subprocess.run(
        [PYTHON, str(SCRIPTS / "check_irdrop.py"), "--pcb", str(pcb),
         "--constraints", str(cons)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "pass"


# ============================================================ corpus: irdrop

@pytest.fixture(scope="module")
def golden_irdrop():
    payload, _ = check_irdrop.run(
        ["--pcb", str(GOLDEN / "blinky2" / "blinky2.kicad_pcb"),
         "--constraints", str(GOLDEN / "blinky2" / "constraints.json")])
    return payload


@pytest.fixture(scope="module")
def mutant_irdrop():
    payload, _ = check_irdrop.run(
        ["--pcb", str(GOLDEN / "mutants" / "undersized-power-trace"
                      / "blinky2.kicad_pcb"),
         "--constraints", str(GOLDEN / "blinky2" / "constraints.json")])
    return payload


def _net_facts(payload, net):
    return next(c for c in payload["checked"] if c.get("net") == net)


def test_blinky2_golden_clean_advisory(golden_irdrop):
    assert golden_irdrop["status"] == "pass"
    assert golden_irdrop["violations"] == []
    f = _net_facts(golden_irdrop, "+3V3")
    assert f["source_mode"] == "largest_pad_area"
    assert f["source_ref"] == "U2"          # the LDO owns the largest pads
    assert 5.0 < f["resistance_mohm"] < 100.0
    assert f["richardson_delta"] <= 0.10
    assert f["jmax"]["a_per_mm"] > 0
    assert f["grid"]["cells_across_min_feature"] >= 8.0


def test_mutant_current_density_max_at_neck(golden_irdrop, mutant_irdrop):
    """The second catcher for the undersized-power-trace defect class:
    the J maximum must sit ON the 0.16 mm neck and be markedly above the
    golden board's maximum."""
    fm = _net_facts(mutant_irdrop, "+3V3")
    fg = _net_facts(golden_irdrop, "+3V3")
    x, y = fm["jmax"]["pos"]
    x0, y0, x1, y1 = NECK_BOX
    assert x0 <= x <= x1 and y0 <= y <= y1, fm["jmax"]
    assert fm["jmax"]["layer"] == "F.Cu"
    # 0.4 A / 0.16 mm ~ 2.5 A/mm nominal; golden trunk peaks ~1.3 A/mm
    assert fm["jmax"]["a_per_mm"] > 1.5 * fg["jmax"]["a_per_mm"]
    assert fm["jmax"]["a_per_mm"] > 2.0
    # neck list agrees (a reported neck lies in the region too)
    assert any(x0 <= n["pos"][0] <= x1 and y0 <= n["pos"][1] <= y1
               for n in fm["necks"])


@pytest.mark.smoke
def test_pd_trigger_5a_maps_under_30s():
    # the routed pd-trigger board, frozen in the stage fixtures
    pcb = (REPO / "tests" / "fixtures" / "stages" / "pd_trigger" / "route"
           / "pd-trigger.kicad_pcb")
    bg = geom.load_board(pcb)
    elapsed = {}
    for entry in ({"net": "VBUS", "current_a": 5.0},
                  {"net": "GND", "current_a": 5.0}):
        t0 = time.time()
        _, facts = check_irdrop.check_net(bg, entry)
        elapsed[entry["net"]] = time.time() - t0
        assert facts["resistance_mohm"] > 0
        assert facts["worst_drop_mv"] > 0
        assert facts["jmax"]["a_per_mm"] > 0
    # timing last: a busy-box skip drops only the time check, not the maps
    net, worst = max(elapsed.items(), key=lambda kv: kv[1])
    assert_under(worst, 30.0, f"{net} map")


# ============================================================ pure: cavity

def test_c00_exact_at_low_frequency():
    a = b = 0.1
    d = 1e-3
    eps = 4.4
    f = 1e4
    z = check_pdn_z.cavity_z([f], a, b, d, eps, [(0.05, 0.05, 0.0, 0.0)],
                             30, 30)
    c00 = check_pdn_z.c00_farads(a, b, d, eps)
    assert c00 == pytest.approx(8.8541878128e-12 * eps * a * b / d,
                                rel=1e-12)
    assert abs(z[0, 0, 0]) == pytest.approx(1.0 / (2 * math.pi * f * c00),
                                            rel=0.01)


def test_delta_mod_low_frequency_clip():
    t = 35e-6
    w_lo = 2 * math.pi * 1e4          # skin depth ~0.66 mm >> t -> clip to t
    w_hi = 2 * math.pi * 1e9          # skin depth ~2.1 um << t -> delta_s
    assert check_pdn_z.delta_mod_m(w_lo, t) == pytest.approx(t, rel=0.06)
    assert check_pdn_z.delta_mod_m(w_hi, t) == pytest.approx(
        check_pdn_z.skin_depth_m(w_hi), rel=0.06)
    # clipped is always the harmonic combination, below both
    assert check_pdn_z.delta_mod_m(w_lo, t) < t
    assert check_pdn_z.delta_mod_m(w_lo, t) < check_pdn_z.skin_depth_m(w_lo)


def test_modal_weights_vs_direct_term_sum():
    """cavity_z vs an independently-coded 3x3 (m,n in 0..2) sum with the
    literal 1/2/4 weights and cos*sinc port factors."""
    a, b, d, eps = 0.15, 0.10, 5e-4, 4.4
    t_cu = 35e-6
    ports = [(0.03, 0.02, 0.004, 0.002), (0.11, 0.07, 0.003, 0.003)]
    f = 5e7
    w = 2 * math.pi * f
    mu0 = 4e-7 * math.pi
    k = check_pdn_z.lossy_k(w, eps, d, t_cu)

    def sinc(x):
        return 1.0 if x == 0 else math.sin(x) / x

    def f_port(m, n, p):
        kx = m * math.pi / a
        ky = n * math.pi / b
        return (math.cos(kx * p[0]) * math.cos(ky * p[1])
                * sinc(kx * p[2] / 2) * sinc(ky * p[3] / 2))

    expect = np.zeros((2, 2), dtype=complex)
    for m in range(3):
        cm2 = 1.0 if m == 0 else 2.0
        for n in range(3):
            cn2 = 1.0 if n == 0 else 2.0
            kx = m * math.pi / a
            ky = n * math.pi / b
            den = kx * kx + ky * ky - k * k
            for i in range(2):
                for j in range(2):
                    expect[i, j] += (cm2 * cn2 * f_port(m, n, ports[i])
                                     * f_port(m, n, ports[j]) / den)
    expect *= 1j * w * mu0 * d / (a * b)
    got = check_pdn_z.cavity_z([f], a, b, d, eps, ports, 2, 2,
                               t_cu_m=t_cu)[0]
    assert np.allclose(got, expect, rtol=1e-10)


def test_single_decap_antiresonance():
    """A lone 100 nF / 1 nH decap on a 200x200x0.5 mm pair: the parallel
    (anti)resonance sits between the decap SRF and the first cavity mode and
    tops both asymptotes (bare plane and decap branch)."""
    a = b = 0.2
    d = 5e-4
    eps = 4.4
    c, esl, esr = 100e-9, 1e-9, 0.01
    freqs = np.logspace(4, math.log10(2e8), 301)
    ports = [(0.1, 0.1, 0.002, 0.001), (0.05, 0.05, 0.002, 0.001)]
    z = check_pdn_z.cavity_z(freqs, a, b, d, eps, ports, 40, 40)
    zb = check_pdn_z.branch_z(freqs, c, esl, esr)
    y = np.zeros((len(freqs), 2), dtype=complex)
    y[:, 1] = 1.0 / zb
    zin = np.abs(check_pdn_z.load_decaps(z, y)[:, 0, 0])
    zbare = np.abs(z[:, 0, 0])
    f_srf = 1.0 / (2 * math.pi * math.sqrt(esl * c))
    f_mode = 299792458.0 / (2 * a * math.sqrt(eps))   # first cavity mode
    peaks = [i for i in check_pdn_z.local_maxima(zin)
             if f_srf < freqs[i] < f_mode]
    assert peaks, "no antiresonance between decap SRF and plane response"
    i = max(peaks, key=lambda i: zin[i])
    assert zin[i] > zbare[i]
    assert zin[i] > abs(zb[i])
    # loaded curve also dips below the bare plane around the decap SRF
    i_srf = int(np.argmin(np.abs(freqs - f_srf)))
    assert zin[i_srf] < zbare[i_srf]


# ============================================================ cavity: boards

def test_pair_detection_convergence_and_curve(tmp_path):
    """Synthetic 4-layer PWR/GND plane pair through the full run(): pair
    found with the bounding-rect flag, C00 matches the parallel-plate
    formula, the double-M convergence fact is present, curve sidecar written."""
    plane = [(0, 0), (40, 0), (40, 30), (0, 30)]
    body = (_zone("GND", "In1.Cu", plane)
            + _zone("PWR", "In2.Cu", plane)
            + _fp("U1", 10, 10, [("1", 0, 0, 0.5, 0.5, "F.Cu", "PWR"),
                                 ("2", 1.0, 0, 0.5, 0.5, "F.Cu", "GND")])
            + _fp("C1", 20, 15, [("1", 0, 0, 0.5, 0.5, "F.Cu", "PWR"),
                                 ("2", 1.0, 0, 0.5, 0.5, "F.Cu", "GND")])
            + _via(20.4, 15.6, "PWR") + _via(21.9, 15.5, "GND"))
    pcb = _write_board(tmp_path, "cavity", body, four=True, w=41, h=31)
    meta = tmp_path / "decoupling.json"
    meta.write_text(json.dumps({"associations": [
        {"cap": "C1", "ic": "U1", "pin": "1", "rail": "PWR",
         "value": "100nF"}]}), encoding="utf-8")
    curve_out = tmp_path / "curve.json"
    payload, _ = check_pdn_z.run(
        ["--pcb", str(pcb), "--metadata", str(meta),
         "--curve-out", str(curve_out)])
    assert payload["status"] == "pass"
    assert len(payload["pairs"]) == 1
    p = payload["pairs"][0]
    assert p["rail"] == "PWR" and p["reference"] == "GND"
    assert p["geometry"] == "bounding_rect_assumed"
    assert p["a_mm"] == pytest.approx(40.0, abs=0.01)
    assert p["b_mm"] == pytest.approx(30.0, abs=0.01)
    c00_expect = (8.8541878128e-12 * p["epsilon_r"]
                  * (p["a_mm"] * 1e-3) * (p["b_mm"] * 1e-3)
                  / (p["d_mm"] * 1e-3))
    assert p["c00_nf"] == pytest.approx(c00_expect * 1e9, rel=1e-3)
    # double-M convergence fact
    assert p["modes"]["m_check"] == 2 * p["modes"]["m"]
    assert p["modes"]["m"] >= 30
    assert "first_min_shift" in p["modes"]
    assert [d["cap"] for d in p["decaps"]] == ["C1"]
    assert p["decaps"][0]["esl_nh"] > 0     # via legs found -> mounting L
    # sidecar: full curve, never inline
    curve = json.loads(curve_out.read_text(encoding="utf-8"))
    assert len(curve["pairs"]) == 1
    assert len(curve["pairs"][0]["freq_hz"]) == check_pdn_z.N_FREQ
    assert len(curve["pairs"][0]["z_mohm"]) == check_pdn_z.N_FREQ
    assert "freq_hz" not in p


def test_usbbuck4_advisory_pairs():
    payload, _ = check_pdn_z.run(
        ["--pcb", str(GOLDEN / "usbbuck4" / "usbbuck4.kicad_pcb"),
         "--metadata", str(GOLDEN / "usbbuck4" / "decoupling.json")])
    assert payload["status"] == "pass"
    assert payload["violations"] == []
    assert len(payload["pairs"]) == 2       # In1/In2 and In2/B.Cu, +3V3 vs GND
    for p in payload["pairs"]:
        assert p["rail"] == "+3V3" and p["reference"] == "GND"
        assert p["obs"]["ic"] == "U1"
        assert len(p["decaps"]) == 6
        assert p["peaks"], "expected at least one antiresonance peak"


def test_blinky2_no_plane_pairs():
    payload, _ = check_pdn_z.run(
        ["--pcb", str(GOLDEN / "blinky2" / "blinky2.kicad_pcb"),
         "--metadata", str(GOLDEN / "blinky2" / "decoupling.json")])
    assert payload["status"] == "pass"
    assert payload["pairs"] == []


# -------------------------------------------------- adversarial regressions


def test_pdn_target_gates_on_peaks_not_band_edges(tmp_path):
    """B-1: pdn_target_mohm must flag modeled antiresonance PEAKS, never the
    band-edge maxima (model validity limits: no VRM/package model)."""
    pcb = GOLDEN / "usbbuck4" / "usbbuck4.kicad_pcb"
    meta = GOLDEN / "usbbuck4" / "decoupling.json"
    cons = tmp_path / "constraints.json"
    cons.write_text(json.dumps({"power": [
        {"net": "+3V3", "current_a": 0.5, "pdn_target_mohm": 50}]}),
        encoding="utf-8")
    payload, _ = check_pdn_z.run(
        ["--pcb", str(pcb), "--metadata", str(meta),
         "--constraints", str(cons), "--out", str(tmp_path / "o.json")])
    vs = [v for v in payload["violations"] if v.get("kind") == "pdn_z_excess"]
    assert vs, "a 50 mOhm target must flag the ~4 MHz antiresonances"
    for v in vs:
        assert v["f_hz"] < 5e7, f"violation at band edge? {v['f_hz']}"
    peak_fs = {p["f_hz"] for pair in payload["pairs"]
               for p in pair["peaks"]}
    assert all(v["f_hz"] in peak_fs for v in vs)

    cons.write_text(json.dumps({"power": [
        {"net": "+3V3", "current_a": 0.5, "pdn_target_mohm": 100000}]}),
        encoding="utf-8")
    payload2, _ = check_pdn_z.run(
        ["--pcb", str(pcb), "--metadata", str(meta),
         "--constraints", str(cons), "--out", str(tmp_path / "o2.json")])
    assert not [v for v in payload2["violations"]
                if v.get("kind") == "pdn_z_excess"]


def test_irdrop_zero_current_entry_skipped(tmp_path):
    """B-4: a placeholder current_a=0 entry skips that net without killing
    the whole run."""
    pcb = GOLDEN / "blinky2" / "blinky2.kicad_pcb"
    cons = tmp_path / "constraints.json"
    cons.write_text(json.dumps({"power": [
        {"net": "+3V3", "current_a": 0},
        {"net": "+3V3", "current_a": 0.4}]}), encoding="utf-8")
    payload, _ = check_irdrop.run(
        ["--pcb", str(pcb), "--constraints", str(cons),
         "--out", str(tmp_path / "o.json")])
    assert payload["status"] in ("pass", "violations")
    skipped = [c for c in payload["checked"] if "skipped" in c]
    solved = [c for c in payload["checked"] if "requested_a" in c]
    assert len(skipped) == 1 and "current_a <= 0" in skipped[0]["skipped"]
    assert len(solved) == 1
