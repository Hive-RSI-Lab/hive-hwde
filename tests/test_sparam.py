"""lib/sparam.py and sparam.py (#483): S-parameter quality and vector fit.

Known answers (hermetic): an RLC ladder's exact S (ABCD cascade) scores
100 % on P370's passivity and reciprocity, is causal everywhere, and is
clean; the same S with Im(S21) flipped, or |S11| pushed past 1, at a few
points fails at exactly those points (a magnitude bump turns
counter-clockwise at the points beside it). The P370 port is checked
against the standard's formula on a hand case. Touchstone 1.x read in all
three formats and a 2-port noise block.

Live (the IIC-OSIC-TOOLS tree): scikit-rf's own passivity test agrees,
and fit -> SPICE subckt -> ngspice AC -> S lands within the fit's stated
error (test-slow).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sparam as cli  # noqa: E402
from lib import solvers  # noqa: E402
from lib import sparam as sp  # noqa: E402

HAVE_TREE = solvers.toolchain_root() is not None
live = pytest.mark.skipif(not HAVE_TREE, reason="no IIC-OSIC-TOOLS tree")


def ladder(freqs, n=5, L=2e-9, C=0.8e-12, R=2.0, z0=50.0):
    """n sections of series R+L, shunt C (fc ~ 8 GHz, Z ~ 50 ohm)."""
    out = []
    for f in freqs:
        w = 2j * math.pi * f
        m = np.eye(2, dtype=complex)
        for _ in range(n):
            m = m @ np.array([[1, R + w * L], [0, 1]]) @ np.array([[1, 0], [w * C, 1]])
        a, b, c, d = m[0, 0], m[0, 1], m[1, 0], m[1, 1]
        den = a + b / z0 + c * z0 + d
        out.append([[(a + b / z0 - c * z0 - d) / den, 2 * (a * d - b * c) / den],
                    [2 / den, (-a + b / z0 - c * z0 + d) / den]])
    return np.array(out)


F = np.linspace(0, 6e9, 401)


def test_exact_ladder_is_clean():
    r = sp.check(F, ladder(F))
    assert r["p370"]["passivity"] == 100 and r["p370"]["reciprocity"] == 100
    assert r["p370"]["causality"] > 99
    assert r["levels"] == {"passivity": "good", "reciprocity": "good", "causality": "good"}
    assert r["errors"] == [] and r["warnings"] == [] and r["ok"]


def test_im_s21_sign_flip_fails_at_exactly_those_points():
    s = ladder(F)
    planted = [100, 200, 300]
    for k in planted:
        s[k, 1, 0] = np.conj(s[k, 1, 0])
    r = sp.check(F, s)
    assert not r["ok"]
    by = {e["kind"]: e for e in r["errors"]}
    want = [float(F[k]) for k in planted]
    assert by["nonreciprocal"]["freqs"] == want
    # a lossy S can stay passive with S21 conjugated; where it doesn't, it is a planted point
    assert set(by["passive_violation"]["freqs"]) <= set(want)
    (term,) = by["noncausal_turn"]["terms"]
    assert term["term"] == "S21" and term["freqs"] == want


def test_s11_above_one_fails_at_those_points():
    s = ladder(F)
    planted = [50, 250]
    for k in planted:
        s[k, 0, 0] *= 1.05 / abs(s[k, 0, 0])
    r = sp.check(F, s)
    by = {e["kind"]: e for e in r["errors"]}
    assert by["passive_violation"]["freqs"] == [float(F[k]) for k in planted]
    assert "nonreciprocal" not in by
    # a radial bump: the points beside it turn the wrong way
    (term,) = by["noncausal_turn"]["terms"]
    assert term["term"] == "S11"
    step = F[1] - F[0]
    for f in term["freqs"]:
        assert min(abs(f - F[k]) for k in planted) <= step * 1.001
    for k in planted:
        assert any(abs(f - F[k]) <= step * 1.001 for f in term["freqs"])


def test_p370_formula_on_a_hand_case():
    # one point with sigma 1.10001 out of 4: PW = 1, PQM = 75 %; S21-S12 = 0.2 -> RM 0.2
    s = np.zeros((4, 2, 2), dtype=complex)
    s[:, 1, 0] = s[:, 0, 1] = 0.5
    s[2, 0, 0] = 1.10001
    s[2, 1, 0] = s[2, 0, 1] = 0.0
    s[3, 0, 1] = 0.3
    m = sp.p370_metrics(s)
    assert m["passivity"] == pytest.approx(75.0, abs=1e-6)
    assert m["reciprocity"] == pytest.approx((4 - (0.2 - 1e-6) / 0.1) / 4 * 100)
    assert m["levels"]["passivity"] == "poor"


def test_partial_columns_judge_only_what_ran():
    s = ladder(F)
    s[:, :, 1] = 0          # port 2 never excited (sim_solve planar --excite 0)
    r = sp.check(F, s, terms=[(0, 0), (1, 0)])
    assert r["ok"], r["errors"]
    assert r["terms"] == ["S11", "S21"]


def test_band_sanity_warnings():
    f = np.linspace(0.5e9, 6e9, 101)       # starts 9 steps above DC
    r = sp.check(f, ladder(f))
    kinds = {w["kind"] for w in r["warnings"]}
    assert {"no_dc_point", "band_start_far"} <= kinds
    assert r["ok"]                           # warnings only
    long = ladder(F, n=40, R=0.2)                   # 40 sections: delay ~ 0.5 ns, 15 MHz steps fine
    assert "step_too_coarse" not in {w["kind"] for w in sp.check(F, long)["warnings"]}
    coarse = np.linspace(0, 6e9, 21)
    assert "step_too_coarse" in {w["kind"] for w in sp.check(coarse, ladder(coarse, n=40, R=0.2))["warnings"]}


@pytest.mark.parametrize("fmt", ["ri", "ma", "db"])
def test_touchstone_v1_formats_round_trip(tmp_path, fmt):
    s = ladder(F[:20])
    lines = [f"# MHz S {fmt.upper()} R 50"]
    for k, f in enumerate(F[:20]):
        row = [f"{f / 1e6:.9f}"]
        for i, j in ((0, 0), (1, 0), (0, 1), (1, 1)):
            v = s[k, i, j]
            if fmt == "ri":
                row += [repr(float(v.real)), repr(float(v.imag))]
            else:
                mag = abs(v) if fmt == "ma" else 20 * math.log10(abs(v))
                row += [repr(float(mag)), repr(float(math.degrees(np.angle(v))))]
        lines.append(" ".join(row))
    lines += ["! noise block", "0.5 1.2 0.1 30 0.2"]   # 2-port noise data: frequency drops
    p = tmp_path / "x.s2p"
    p.write_text("\n".join(lines) + "\n")
    d = sp.read_touchstone(p)
    assert d["nports"] == 2 and len(d["freqs"]) == 20
    assert np.allclose(d["freqs"], F[:20], rtol=1e-12)
    assert np.allclose(d["s"], s, atol=1e-9)


def test_touchstone_writer_reads_back_4port(tmp_path):
    s = np.random.default_rng(1).normal(size=(5, 4, 4)) * 0.1 + 0j
    p = sp.write_touchstone(tmp_path / "a.s4p", np.arange(5) * 1e9, s)
    d = sp.read_touchstone(p)
    assert np.allclose(d["s"], s)


def test_cli_check_exits_1_on_failure(tmp_path, capsys):
    s = ladder(F)
    good = sp.write_touchstone(tmp_path / "good.s2p", F, s)
    assert cli.main(["check", str(good)]) == 0
    s[123, 1, 0] = np.conj(s[123, 1, 0])
    bad = sp.write_touchstone(tmp_path / "bad.s2p", F, s)
    assert cli.main(["check", str(bad), "--out", str(tmp_path / "r.json")]) == 1
    out = capsys.readouterr().out
    assert "FAIL nonreciprocal: 1 point(s) at 1.845e+09 Hz" in out
    assert (tmp_path / "r.json").exists()


def test_planar_quality_uses_only_excited_columns():
    import sim_solve
    freqs = list(F[1:50])
    s = ladder(F[1:50])
    cols = {0: [[s[m, 0, 0], s[m, 1, 0]] for m in range(len(freqs))]}
    q = sim_solve._quality(freqs, cols, 2)
    assert q["ok"] and q["terms"] == ["S11", "S21"]
    cols[0][10][0] = 1.2           # |S11| > 1 at one point
    q = sim_solve._quality(freqs, cols, 2)
    assert not q["ok"] and q["errors"][0]["freqs"] == [freqs[10]]


# ------------------------------------------------------------------ live

@live
def test_live_scikit_rf_agrees_on_passivity():
    rf = sp.skrf()
    s = ladder(F)
    for k in (100, 300):
        s[k, 0, 0] *= 1.05 / abs(s[k, 0, 0])
    nw = rf.Network(frequency=rf.Frequency.from_f(F, unit="hz"), s=s, z0=50)
    # scikit-rf's Network.passivity is an element-wise sqrt of S^H S: only its
    # diagonal (sqrt of each column's power sum) is a passivity figure
    sk = np.array([np.abs(np.diag(m)).max() > 1 + 1e-9 for m in nw.passivity])
    ours = sp.p370_metrics(s)["pm"] > 1 + 1e-9
    assert (sk == ours).all() and sk.sum() == 2


@live
def test_live_fit_spice_round_trip(tmp_path):
    ts = sp.write_touchstone(tmp_path / "ladder.s2p", F, ladder(F))
    res = sp.fit(ts, tmp_path / "ladder.cir")
    assert res["ok"] and res["target_met"] and res["passive_after"]
    assert res["rms_error"] < 1e-3
    txt = (tmp_path / "ladder.cir").read_text()
    assert ".SUBCKT ladder p1 p2" in txt
    rt = sp.roundtrip(ts, tmp_path / "ladder.cir", res["subckt"])
    # ngspice reproduces the model, so its error against the file is the fit's
    assert rt["rms_error"] == pytest.approx(res["rms_error"], rel=0.05, abs=1e-6)
    assert rt["max_error"] <= res["max_error"] * 1.05 + 1e-6


@live
def test_live_far_poles_are_folded_not_written():
    rf = sp.skrf()
    from skrf.vectorFitting import VectorFitting
    f = F[1:]
    vf = VectorFitting(rf.Network(frequency=rf.Frequency.from_f(f, unit="hz"), s=ladder(f), z0=50))
    vf.vector_fit(n_poles_real=2, n_poles_cmplx=5)
    before = sp.model_s(vf, f)
    sp.prune_far_poles(vf, f[-1])
    assert np.all(np.abs(vf.poles) <= 2 * math.pi * f[-1] * 100)
    assert np.max(np.abs(sp.model_s(vf, f) - before)) < 1e-6
