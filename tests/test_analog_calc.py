"""lib/analogcalc.py, scripts/analog_calc.py and scripts/analog_verify.py.

The numbers here are the published or derived values each formula must
give (reference/ingest/analog-audio.md); the ngspice test re-runs every
case of analog_verify where KiCad's ngspice is installed."""
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import analogcalc as ac  # noqa: E402
import env  # noqa: E402


def test_johnson_noise_of_1k_is_4_07_nv():
    # 4.07 nV/rtHz at 25 C (ADI MT-049, TI SBOA066); 12.9 at 10k
    assert ac.johnson_v(1e3) * 1e9 == pytest.approx(4.058, abs=0.005)
    assert ac.johnson_v(10e3) * 1e9 == pytest.approx(12.83, abs=0.02)


def test_amp_noise_terms_add_as_powers_and_name_the_dominant():
    r = ac.amp_noise(en=1e-9, in_=0.0, rs=10e3)
    assert r["dominant"] == "rs_thermal"
    assert r["density_nv"] == pytest.approx(math.hypot(1.0, 12.83), abs=0.02)
    # a low-noise op-amp behind a 10k source is wasted: NF ~0 dB
    assert r["noise_figure_db"] < 0.1
    # 20 kHz of 10 nV/rtHz white is 1.41 uV
    w = ac.amp_noise(en=10e-9, in_=0.0, rs=0.0, bw=20020, f_lo=20)
    assert w["input_uvrms"] == pytest.approx(1.4142, rel=1e-3)
    # gain 11 stage: noise gain 1 + Rf/Rg
    g = ac.amp_noise(en=4.5e-9, in_=0.4e-12, rs=0, rf=10e3, rg=1e3)
    assert g["noise_gain"] == 11.0


def test_line_levels():
    assert ac.dbu_to_vrms(4) == pytest.approx(1.228, abs=1e-3)
    assert ac.dbv_to_vrms(-10) == pytest.approx(0.3162, abs=1e-4)
    # +4 dBu and -10 dBV are 11.8 dB apart, not 14
    assert 20 * math.log10(ac.dbu_to_vrms(4) / ac.dbv_to_vrms(-10)) == \
        pytest.approx(11.79, abs=0.01)
    # a 600 ohm output into a 10k bridging input loses 0.5 dB
    assert ac.bridging_loss_db(600, 10e3) == pytest.approx(-0.506, abs=1e-3)


def test_the_one_eighth_rule_is_a_1db_rule():
    # Zout = Zmin/8 into a load that rises to many times Zmin: 20log(9/8)
    assert ac.damping_deviation_db(4, 32, 1e9) == pytest.approx(1.02,
                                                               abs=0.01)
    # it is the swing, not the nominal: a flat 32 ohm load tilts nothing
    assert ac.damping_deviation_db(30, 32, 32) == 0


def test_coupling_corner_counts_the_source_and_stacks():
    assert ac.hpf_corner(10e-6, 10e3, 600) == pytest.approx(1.5015, abs=1e-3)
    fc = 20.0
    assert ac.hpf_loss_db(20, fc, 1) == pytest.approx(-3.01, abs=0.01)
    assert ac.hpf_loss_db(20, fc, 2) == pytest.approx(-6.02, abs=0.01)
    assert ac.hpf_c_for(fc, 10e3) == pytest.approx(0.796e-6, rel=1e-3)
    assert ac.bias_drift_v_per_s(10e-9, 1e-6) == pytest.approx(0.01)


def test_full_power_bandwidth():
    # NE5532: 9 V/us, 10 Vpk -> 143 kHz
    assert ac.full_power_bw(9e6, 10) == pytest.approx(143.2e3, rel=1e-3)
    assert ac.slew_needed(20e3, 10) == pytest.approx(1.2566e6, rel=1e-4)


def test_cap_load_phase_margin_limits():
    # no load: a single-pole follower has 90 degrees
    assert ac.cap_load_pm(10e6, 50, 1e-15)["phase_margin_deg"] == \
        pytest.approx(90, abs=0.5)
    bad = ac.cap_load_pm(10e6, 50, 1e-9)
    assert not bad["stable_45"] and bad["overshoot_est"] > 0.35
    r = ac.riso_for_pm(10e6, 50, 1e-9, pm_target=60)
    assert 20 < r < 30
    assert ac.cap_load_pm(10e6, 50, 1e-9, riso=r)["phase_margin_deg"] == \
        pytest.approx(60, abs=0.1)
    # more noise gain buys margin with no riso (the noise-gain method)
    assert ac.cap_load_pm(10e6, 50, 1e-9, 4)["phase_margin_deg"] > 55
    # PM 45 -> ~23 % overshoot, PM 60 -> ~9 % (second-order loop)
    assert ac._second_order_overshoot(45) == pytest.approx(0.23, abs=0.01)
    assert ac._second_order_overshoot(60) == pytest.approx(0.088, abs=0.01)


def test_ti_sloa119b_8_ohm_filter_is_butterworth_at_28k():
    # SLOA119B table 1, 8 ohm BTL AD: 33 uH per leg, 0.47 uF across the
    # load, 0.1 uF from each output to ground
    r = ac.classd_lc(33e-6, 0.1e-6, 8, btl=True, c_diff=0.47e-6)
    assert r["f0_hz"] == pytest.approx(27.2e3, rel=0.01)
    assert r["q"] == pytest.approx(0.71, abs=0.01)
    # ... and it is 1.1 dB down at 20 kHz into a resistive 8 ohm
    assert r["at_f_audio_db"] == pytest.approx(-1.09, abs=0.02)
    # the design inverse gives the same L and C back
    d = ac.classd_lc_for(8, r["f0_hz"], r["q"])
    assert d["l_h"] == pytest.approx(33e-6, rel=1e-6)
    assert d["c_f"] / 2 == pytest.approx(0.05e-6 + 0.47e-6, rel=1e-6)


def test_lc_peaks_without_its_load():
    r = ac.classd_lc(10e-6, 0.68e-6, 8, f_sw=400e3, r_dcr=0.05)
    assert r["peak_db"] == pytest.approx(1.5, abs=0.05)
    assert r["peak_db_2x_load"] > r["peak_db"] + 3
    assert r["peak_db_open"] > 30 and r["q_open"] > 50


def test_classd_power_and_the_10pct_ratio():
    p = ac.classd_power(24, 8)
    assert p["p_1pct_w"] == pytest.approx(36.0)
    assert ac.classd_power(24, 8, btl=False)["p_1pct_w"] == pytest.approx(9.0)
    # datasheets' 10 % THD power over the 1 % one, for a hard clip: 1.22
    # (the folk "25 % more" is close; a soft clip differs)

    def od_for(t):
        lo, hi = 1.0, 3.0
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if ac.clipped_sine_thd(mid, 2048) < t \
                else (lo, mid)
        return lo

    def fund(od, n=2048):
        xs = [max(-1, min(1, od * math.sin(2 * math.pi * i / n)))
              for i in range(n)]
        return ac.thd_from_samples(xs, n, 1, 3)["fundamental"] ** 2

    ratio = fund(od_for(0.10)) / fund(od_for(0.01))
    assert ratio == pytest.approx(1.22, abs=0.01)


def test_thd_from_samples():
    n = 256
    xs = [math.sin(2 * math.pi * 3 * i / n) for i in range(n)]
    r = ac.thd_from_samples([x + 0.01 * x ** 3 for x in xs], n, 3)
    assert r["thd"] == pytest.approx(0.0025 / 1.0075, rel=1e-9)
    with pytest.raises(ValueError, match="coherent"):
        ac.thd_from_samples(xs, n, 3.5)


def test_twenty_x_bucket_rule_does_not_settle_by_itself():
    # Cf = 20 Cs bounds the kickback to 1/21 of full scale ...
    r = ac.sar_rc(100, 800e-12, 40e-12, 12, 300e-9)
    assert r["c_ratio"] == 20 and r["kickback_v"] == pytest.approx(1 / 21)
    # ... but with 100 ohm and 300 ns a 12-bit SAR is still 5 LSB off
    assert not r["settled"] and r["residual_lsb"] == pytest.approx(5.48,
                                                                   abs=0.05)
    # and a far bigger Cf is worse: tau grows faster than the kick shrinks
    big = ac.sar_rc(50, 10e-9, 25e-12, 18, 1e-6)
    assert not big["settled"]
    # 16 bits from a full-scale step need ln(2^17) = 11.8 tau
    assert ac.settle_taus(16) == pytest.approx(11.78, abs=0.01)


def run_cli(*args):
    cp = subprocess.run([sys.executable, str(SCRIPTS / "analog_calc.py"),
                         *args], capture_output=True, text=True, timeout=60)
    return cp.returncode, json.loads(cp.stdout)


def test_cli():
    rc, out = run_cli("lc", "--l", "33u", "--c", "100n", "--cdiff", "470n",
                      "--rload", "8", "--fsw", "400k")
    assert rc == 0 and out["result"]["q"] == pytest.approx(0.71, abs=0.01)
    assert out["result"]["q_open"] is None   # no DCR given: undamped
    rc, out = run_cli("level", "--dbv", "-10")
    assert out["result"]["vrms"] == pytest.approx(0.3162, abs=1e-4)
    rc, out = run_cli("fpbw", "--slew", "9V/us", "--vpk", "10")
    assert out["result"]["fpbw_hz"] == pytest.approx(143.2e3, rel=1e-3)
    rc, out = run_cli("riso", "--gbw", "10M", "--ro", "50", "--cl", "1n")
    assert 20 < out["result"]["riso_for_pm"] < 30
    rc, out = run_cli("noise", "--en", "4.5nV", "--rs", "10k")
    assert out["result"]["dominant"] == "rs_thermal"
    rc, out = run_cli("hpf", "--rload", "10k")
    assert rc == 2 and out["status"] == "error"


def test_analog_verify_against_ngspice(tmp_path):
    if env.find_ngspice_dll() is None:
        pytest.skip("no ngspice shared library (KiCad bundle or "
                    "HWDE_NGSPICE_DLL)")
    out = tmp_path / "av.json"
    cp = subprocess.run([sys.executable, str(SCRIPTS / "analog_verify.py"),
                         "--out", str(out)], capture_output=True, text=True,
                        timeout=300)
    rep = json.loads(out.read_text())
    assert cp.returncode == 0 and rep["status"] == "pass", rep["failed"]
    assert set(rep["cases"]) >= {"johnson", "classd_lc", "cap_load_pm",
                                 "sar_rc", "hpf", "thd", "engine_facts"}
