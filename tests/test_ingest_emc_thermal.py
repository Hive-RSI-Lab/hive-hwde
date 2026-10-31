"""reference/ingest/emc-thermal/verify.py against the numbers its sources
print: a rule the ingest note marks verified stays reproduced, and a rule it
marks disputed stays shown to disagree (reference/ingest/emc-thermal.md)."""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VERIFY = REPO / "reference" / "ingest" / "emc-thermal" / "verify.py"
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

_spec = importlib.util.spec_from_file_location("emc_verify", VERIFY)
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)


# ------------------------------------------------------------------ ESD

def test_esd1_iec_slope_and_ti_ten_volts():
    # IEC 61000-4-2 Table 3: 30 A in 0.8 ns at 8 kV; TI SLVA680A rounds it
    # to 4e10 A/s and says 0.25 nH adds ~10 V
    assert v.esd_di_dt(8) == pytest.approx(3.75e10)
    assert v.esd_l_volts(0.25e-9) == pytest.approx(9.4, abs=0.1)
    assert 0.25e-9 * 4e10 == pytest.approx(10.0)


def test_esd2_one_nh_per_mm_is_partial_not_loop():
    """~1 nH/mm holds for a thin trace's partial self-inductance; over a
    solid plane the loop is ~0.3 nH/mm, so check_esd's estimate errs high."""
    per_mm = v.trace_partial_l(10e-3, 0.2e-3) * 1e9 / 10
    assert 0.8 < per_mm < 1.2
    assert v.microstrip_loop_l_per_m(50, 3.3) * 1e6 == pytest.approx(0.30, abs=0.02)
    import check_esd
    assert check_esd.NH_PER_MM == pytest.approx(per_mm, rel=0.2)


def test_esd3_via_inductance():
    l = v.via_l(1.6e-3, 0.3e-3) * 1e9
    assert 1.0 < l < 1.5
    import check_esd
    assert check_esd.NH_PER_VIA == pytest.approx(l, rel=0.15)


def test_esd6_ti_corner_radius_is_a_breakdown_gap():
    """TI's 2.6 mm for 7 kV is the air gap that holds 7 kV at ~3 kV/mm
    (2.3 mm here), a spacing figure, not a property of a trace corner."""
    assert v.air_gap_for_kv(7) == pytest.approx(2.33, abs=0.01)


def test_esd4_ngspice_clamp_overshoot_is_l_di_dt():
    out = subprocess.run([sys.executable, "-I", str(VERIFY), "--spice",
                          "--json"], capture_output=True, text=True,
                         timeout=300)
    res = json.loads(out.stdout)["esd_spice"]
    if "error" in res:
        pytest.skip(f"ngspice not reachable: {res['error']}")
    slope = res["slope_a_per_s"]
    for l, extra in res["extra_v"].items():
        assert extra == pytest.approx(float(l) * slope, rel=0.05), (l, extra)


# ------------------------------------------------------------------ EMC

def test_emc1_ott_loop_constant_from_first_principles():
    assert v.dm_loop_e(1, 1, 1, 1) == pytest.approx(131.6e-16, rel=0.002)
    assert v.dm_loop_e(1, 1, 1, 1, True) == pytest.approx(263e-16, rel=0.002)


def test_emc2_five_microamps_two_ways():
    """Ott / Bogatin short wire and Hubing's resonant dipole both land on
    a few uA of common-mode current for FCC class B at 3 m."""
    assert v.cm_wire_e(1, 1, 1, 1) == pytest.approx(1.257e-6, rel=0.002)
    bogatin = v.cm_current_at_limit(100e-6, 100e6, 1, 3, False)
    hubing = v.resonant_dipole_current_for(100e-6, 3)
    assert bogatin == pytest.approx(4.8e-6, rel=0.02)
    assert hubing == pytest.approx(5.0e-6, rel=0.05)
    # the 150 uV/m band above 88 MHz with the ground reflection: 3.6 uA
    assert v.cm_current_at_limit(150e-6, 100e6, 1, 3) == pytest.approx(
        3.6e-6, rel=0.02)


def test_emc2_fcc_and_cispr_class_b_agree_within_a_db():
    # CISPR 32 class B is at 10 m; 20 log(10/3) = 10.5 dB to 3 m
    cispr_3m = 30.0 + 20 * math.log10(10 / 3)
    assert abs(cispr_3m - v.dbuv(v.limit_fcc_b_3m(50e6))) < 1.0


def test_emc3_trapezoid_spectrum_stays_under_pauls_envelope():
    f0, d, tr = 1e6, 0.5, 10e-9
    harm = v.trapezoid_harmonics(f0, d, tr, 400)
    for f, c in harm:
        assert c <= v.trapezoid_envelope(f, f0, d, tr) * 1.02 + 1e-9, f
    # past 1/(pi tr) = 31.8 MHz the odd harmonics fall 40 dB/decade
    c3 = dict(harm)
    hi = 20 * math.log10(c3[351 * f0])
    lo = 20 * math.log10(c3[35 * f0])
    assert -45 < hi - lo < -35


def test_emc4_cavity_resonance_matches_kicad_happy():
    assert v.cavity_resonance(0.1, 0.08, 4.4) / 1e6 == pytest.approx(715, abs=2)


def test_emc5_lambda_over_20_is_twenty_db_by_otts_slot_formula():
    lam = v.C0 / 1e9
    assert v.slot_se_db(1e9, lam / 20) == pytest.approx(20.0)
    assert v.slot_se_db(1e9, lam / 10) == pytest.approx(14.0, abs=0.1)
    assert v.slot_se_db(1e9, lam / 2) == 0.0


def test_emc7_bead_cap_peaking():
    """ADI AN-1368: a bead into a low-ESR cap peaks; ~1 uH into 10 uF with
    16 mohm of loss resonates near 50 kHz with ~26 dB of gain."""
    f, gain = v.lc_filter_peak_db(1e-6, 10e-6, 0.016)
    assert f == pytest.approx(50.3e3, rel=0.01)
    assert gain > 20


# -------------------------------------------------------------- thermal

def test_th3_via_theta_matches_ti_and_wurth():
    assert v.via_theta(0.3048e-3, 17.5e-6, 1.65e-3) == pytest.approx(261, rel=0.06)
    assert v.via_theta(0.67e-3, 35e-6, 1.6e-3) == pytest.approx(64, rel=0.1)
    assert v.via_theta(0.6e-3, 0, 1.6e-3, filled=True) == pytest.approx(14, rel=0.1)


def test_th3_check_thermal_via_model_agrees_with_snva419():
    import check_thermal
    r = check_thermal.via_r_cw(0.3048, 1.65)
    assert r == pytest.approx(261, rel=0.15)
    # the "5-10 K/W per unfilled via" figure some blogs print is folklore
    assert r > 100


def test_th5_one_square_inch_does_not_saturate_a_fin():
    """Disputed: the fin model (still air, both faces) still gains past
    1 in2; check_thermal's curve floors there, which is conservative."""
    at_645 = v.fin_theta(2e-3, math.sqrt(645e-6 / math.pi))
    inf = v.fin_theta(2e-3, 2.0)
    assert at_645 > 2 * inf
    assert v.fin_saturation_area(2e-3) > 3 * 645


def test_th2_psi_jt():
    assert v.tj_from_top(80.0, 1.5, 2.0) == pytest.approx(83.0)
