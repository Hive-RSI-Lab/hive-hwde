"""lib/impedance.py's 2D field solver against exact results and JLC's own
calculator (V12, V18).

The JLC figures are raw request/response pairs from the public calculator
behind https://jlcpcb.com/pcb-impedance-calculator (POST
/api/jlcTools/impedance/calc, Polar SI9000 models; the result arrives over the
page's websocket), fetched 2026-09-27 for JLC's own templates JLC04161H-1080B
and JLC04162H-7628A. All their inputs are mil.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "hwde" / "scripts" / "lib"))
import impedance as imp  # noqa: E402

JLC = ROOT / "tests" / "fixtures" / "jlc_impedance"
MIL = 0.0254
JLC_TOL_PCT = 1.5      # measured +0.59..+0.91% on every case below


def _jlc_cases():
    for p in sorted(JLC.glob("*_L*.json")):
        d = json.loads(p.read_text("utf-8"))
        yield pytest.param(d, id=p.stem)


def _solve(d: dict) -> float:
    a = d["request"]["impedance_calc_arg"]
    mark = d["request"]["impedance_calc_mark"]
    mm = {k: float(v) * MIL for k, v in a.items()
          if k in ("H1", "H2", "W1", "S1", "T1", "C1", "C2")}
    if mark.startswith("OffsetStripline"):
        # SI9000 offset stripline: the trace sits on H1 and is embedded in H2
        return imp.field_stripline(mm["W1"], mm["H1"], mm["H2"], mm["T1"],
                                   float(a["Er1"]), float(a["Er2"]))["z0"]
    mask = {"c1": mm["C1"], "c2": mm["C2"], "er": float(a["CEr"])}
    r = imp.field_microstrip(mm["W1"], mm["H1"], mm["T1"], float(a["Er1"]),
                             mm.get("S1"), mask)
    return r["zdiff"] if "S1" in mm else r["z0"]


@pytest.mark.parametrize("d", list(_jlc_cases()))
def test_field_solver_matches_jlc_calculator(d):
    jlc = float(d["response"]["impedance_calc_result"]["dImpedance"])
    got = _solve(d)
    assert abs(got - jlc) / jlc * 100 < JLC_TOL_PCT, (got, jlc)


def test_jlc_fixtures_present():
    """Nine outer-layer cases (SE + diff, both stackups) and one stripline."""
    assert len(list(_jlc_cases())) == 10


def test_field_beats_closed_form_on_jlc():
    """The reason the solver exists: the IPC-2141A fallback misses JLC by
    more than 3% on at least one case; the solver misses none by 1.5%."""
    worst_cf = 0.0
    for p in _jlc_cases():
        d = p.values[0]
        a = {k: float(v) * MIL for k, v in d["request"]["impedance_calc_arg"].items()
             if k in ("H1", "W1", "S1", "T1")}
        if d["request"]["impedance_calc_mark"].startswith("OffsetStripline"):
            continue
        er = float(d["request"]["impedance_calc_arg"]["Er1"])
        jlc = float(d["response"]["impedance_calc_result"]["dImpedance"])
        cf = (imp._zdiff(a["W1"], a["S1"], a["H1"], a["T1"], er) if "S1" in a
              else imp.microstrip_z0_closed_form(a["W1"], a["H1"], a["T1"], er))
        worst_cf = max(worst_cf, abs(cf - jlc) / jlc * 100)
    assert worst_cf > 3.0


def test_stripline_matches_cohn_exact():
    st = imp.solver_status()
    assert st["ok"] and st["err_pct"] < 1.0


def test_microstrip_matches_hammerstad_jensen():
    """Bare thin microstrip vs Hammerstad-Jensen (itself good to ~0.2%)."""
    h, er = 0.2444, 4.05
    for w in (0.1, 0.43, 1.0):
        u = w / h
        a = 1 + math.log((u**4 + (u / 52)**2) / (u**4 + 0.432)) / 49 \
            + math.log(1 + (u / 18.1)**3) / 18.7
        b = 0.564 * ((er - 0.9) / (er + 3))**0.053
        ee = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u)**(-a * b)
        f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u)**0.7528)
        hj = 60 / math.sqrt(ee) * math.log(f / u + math.sqrt(1 + 4 / u**2))
        got = imp.field_microstrip(w, h, 0.002, er, mask=None)["z0"]
        assert abs(got - hj) / hj < 0.01, (w, got, hj)


def test_mask_lowers_impedance():
    bare = imp.field_microstrip(0.4, 0.2444, 0.035, 3.91, mask=None)["z0"]
    coated = imp.field_microstrip(0.4, 0.2444, 0.035, 3.91)["z0"]
    assert 0.5 < bare - coated < 5.0


def test_diff_modes_consistent():
    r = imp.field_microstrip(0.3, 0.2444, 0.035, 3.91, 0.2444, even=True)
    assert r["zdiff"] == pytest.approx(2 * r["zodd"])
    assert r["zeven"] > r["zodd"]          # coupling splits the modes
    assert r["zcommon"] == pytest.approx(r["zeven"] / 2)


def test_solve_width_roundtrip_field_and_closed_form():
    for method in ("field", "closed_form"):
        w = imp.solve_width(50, 0.2444, 0.035, 3.91, method=method)
        assert abs(imp.microstrip_z0(w, 0.2444, 0.035, 3.91, method) - 50) < 0.05
        w, s = imp.diff_pair(90, 0.2444, 0.035, 3.91, method=method)
        assert abs(imp.zdiff(w, s, 0.2444, 0.035, 3.91, method) - 90) < 0.1
        w, s = imp.diff_pair(100, 0.2444, 0.035, 3.91, width=0.3, method=method)
        assert w == 0.3 and abs(imp.zdiff(w, s, 0.2444, 0.035, 3.91, method) - 100) < 0.2


def test_default_method_is_field():
    assert imp.SOLVER_OK and imp.default_method() == "field"
