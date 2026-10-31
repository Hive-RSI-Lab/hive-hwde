"""Research claims, checked by hwde itself (reference/research/papers.yaml).

Each test is one claim's evidence: it names the claim id, computes what
hwde's own model says, and asserts the verdict the ledger records
(reproduced, partial or refuted). A test that starts failing means the
verdict no longer holds: hwde's model moved or the claim was misread, and
the ledger entry has to be looked at again. The paper's numbers are quoted
here as the paper gives them, with where they were read.
"""
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import check_current as cc  # noqa: E402
import check_pdn_z as pz  # noqa: E402
import mlcc  # noqa: E402

# ------------------------------------------------- PCB trace fuses (2026)
FUSE = "design-thermal-characterization-printed-circuit-2026"
# Methods: "IPC-2152 external-conductor model for 10 C temperature rise ...
# 35 um copper ... 0.30 A (0.10 mm), 0.50 A (0.15 mm), 0.72 A (0.20 mm),
# and 1.02 A (0.25 mm)".
FUSE_RATED = {0.10: 0.30, 0.15: 0.50, 0.20: 0.72, 0.25: 1.02}
# Table 1: measured blow time (s), mean of n = 4, at 1.5/2/2.5/3/4 x rated.
FUSE_K = (1.5, 2.0, 2.5, 3.0, 4.0)
FUSE_T = {0.10: (8.4, 3.2, 1.7, 0.9, 0.3), 0.15: (14.7, 5.8, 2.9, 1.6, 0.5),
          0.20: (23.1, 9.4, 4.6, 2.4, 0.8), 0.25: (38.6, 15.2, 7.3, 3.8, 1.2)}


def _hwde_rating_1oz_10c(width_mm: float) -> float:
    """The current hwde's IPC-2152 table allows a 1 oz outer track at
    dT = 10 C: required_width_mm inverted by bisection."""
    lo, hi = 0.0, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if cc.required_width_mm(mid, 10.0, cc.OZ1_MM) > width_mm:
            hi = mid
        else:
            lo = mid
    return lo


def test_trace_fuse_ratings_disagree_with_ipc2152():
    """design-thermal-characterization-printed-circuit-2026/1: refuted.
    The paper's "IPC-2152" ratings are 1.5 to 2x what hwde's IPC-2152 table
    allows, and they grow 3.4x for a 2.5x wider track, while every IPC
    model has rating growing slower than the cross-section (IPC-2221's
    I ~ A^0.725 gives 1.94x)."""
    for w, paper in FUSE_RATED.items():
        hwde = _hwde_rating_1oz_10c(w)
        assert paper / hwde > 1.4, (w, paper, hwde)
    growth = FUSE_RATED[0.25] / FUSE_RATED[0.10]
    assert growth > 3.0
    assert (0.25 / 0.10) ** 0.725 < 2.0  # IPC-2221's exponent on area
    hwde_growth = _hwde_rating_1oz_10c(0.25) / _hwde_rating_1oz_10c(0.10)
    assert hwde_growth < 2.6
    assert growth > 1.3 * hwde_growth


def _exponent(t0, t1, k0, k1):
    return math.log(t0 / t1) / math.log(k1 / k0)


def test_trace_fuse_blow_time_exponent_contradicts_its_table():
    """design-thermal-characterization-printed-circuit-2026/2: refuted by
    the paper's own Table 1. The text says blow time falls as
    (I/I_rated)^-1.7 at 1.5-2x and ^-2.3 at 3-4x; Table 1 gives exponents
    of 3.1 to 4.1 at both ends, on every width."""
    for w, t in FUSE_T.items():
        low = _exponent(t[0], t[1], FUSE_K[0], FUSE_K[1])
        high = _exponent(t[3], t[4], FUSE_K[3], FUSE_K[4])
        assert 3.0 < low < 3.5, (w, low)
        assert 3.7 < high < 4.1, (w, high)
        assert abs(low - 1.7) > 1.0 and abs(high - 2.3) > 1.0


# -------------------------------------- PDN impedance and anti-resonance
PDN = "impedance-analysis-optimization-research-power-2025"
F = np.logspace(3, 9, 6001)


def _par(*zs):
    return 1.0 / sum(1.0 / z for z in zs)


def test_pdn_single_capacitor_series_resonance():
    """impedance-analysis-optimization-research-power-2025/1: reproduced.
    Section 2.2: Rs = 0.1 mOhm, Ls = 0.2 nH, C = 47 uF resonate at 1.64 MHz,
    where |Z| is its minimum, the ESR."""
    z = np.abs(pz.branch_z(F, 47e-6, 0.2e-9, 0.1e-3))
    i = int(np.argmin(z))
    assert abs(F[i] / 1.64e6 - 1) < 0.01
    assert abs(z[i] / 0.1e-3 - 1) < 0.01


# A bulk capacitor in parallel with a smaller MLCC: the bulk's ESL against
# the MLCC's C makes the anti-resonance between their series resonances.
CB, LB, RB = 100e-6, 1e-9, 5e-3
CP, LP, RP = 1e-6, 0.5e-9, 3e-3


def _peak(rb=RB, rp=RP, lp=LP):
    z = np.abs(_par(pz.branch_z(F, CB, LB, rb), pz.branch_z(F, CP, lp, rp)))
    srf_b = 1 / (2 * math.pi * math.sqrt(LB * CB))
    srf_p = 1 / (2 * math.pi * math.sqrt(lp * CP))
    band = (F > srf_b) & (F < srf_p)
    i = int(np.argmax(np.where(band, z, 0)))
    return F[i], z[i]


def test_pdn_antiresonance_frequency_holds_magnitude_does_not():
    """impedance-analysis-optimization-research-power-2025/2: partial.
    Section 2.4 puts the anti-resonance at f1 = 1/(2 pi sqrt(Lb Cp)) with
    |Z| = sqrt(Lb/Cp) there. The frequency holds only while the small
    capacitor's own ESL is small next to the bulk's: the peak sits at
    1/(2 pi sqrt((Lb+Lp) Cp)), 19 % below f1 with Lp = Lb/2. The magnitude
    doesn't hold: the peak is about X^2/(Rb+Rp), X the bulk branch's
    reactance there, 2.7x sqrt(Lb/Cp) with these mOhm ESRs."""
    f1 = 1 / (2 * math.pi * math.sqrt(LB * CP))
    f, zpk = _peak()
    assert abs(f * 2 * math.pi * math.sqrt((LB + LP) * CP) - 1) < 0.02
    assert f / f1 < 0.85
    f_small, _ = _peak(lp=LB / 20)
    assert abs(f_small / f1 - 1) < 0.05
    z0 = math.sqrt(LB / CP)
    x = 2 * math.pi * f * LB - 1 / (2 * math.pi * f * CB)
    assert zpk > 2.5 * z0
    assert abs(zpk / (x * x / (RB + RP)) - 1) < 0.1


def test_pdn_lower_q_lowers_antiresonance():
    """impedance-analysis-optimization-research-power-2025/3: partial.
    Section 2.4 (3): a higher-ESR (lower-Q) capacitor lowers the
    anti-resonance spike. It does, until the two branches' ESR adds up to
    about sqrt((Lb+Lp)/Cp); past that the spike is gone and more ESR only
    raises |Z| across the band (the worst |Z| between the two series
    resonances is least near that ESR)."""
    ks = np.geomspace(0.25, 30, 60)
    worst = [_peak(rb=k * RB, rp=k * RP)[1] for k in ks]
    best = int(np.argmin(worst))
    z0 = math.sqrt((LB + LP) / CP)
    assert 0.6 < ks[best] * (RB + RP) / z0 < 1.6
    assert all(b < a for a, b in zip(worst[:best], worst[1:best + 1]))
    assert worst[-1] > 2 * worst[best]


def test_pdn_n_identical_caps_divide_impedance():
    """impedance-analysis-optimization-research-power-2025/4: reproduced.
    Section 2.4 (1): n identical capacitors in parallel keep the resonance
    frequency and divide |Z| by n."""
    one = np.abs(pz.branch_z(F, CP, LP, RP))
    for n in (2, 4, 8):
        many = np.abs(_par(*[pz.branch_z(F, CP, LP, RP)] * n))
        assert F[int(np.argmin(many))] == F[int(np.argmin(one))]
        assert np.allclose(many * n, one, rtol=1e-9)


# ------------------------------------------- MLCC DC bias (review, 2020)
REVIEW = "practical-considerations-designing-reliable-dc-2020"


def test_mlcc_80pct_bias_loss_holds_for_dense_parts_only():
    """practical-considerations-designing-reliable-dc-2020/1: partial.
    Section 2.2 (repeating its ref [52]): X7R MLCCs lose 80 % of their
    capacitance at 80 % of rated voltage. hwde's generic bias curve
    (lib/mlcc.py) agrees for a dense part and not for a sparse one: the loss
    follows capacitance per volume, not a fixed share of the rating."""
    dense = mlcc.bias_fraction(10e-6, "0603", 8.0, "X7R", rated_v=10.0)
    sparse = mlcc.bias_fraction(100e-9, "0603", 40.0, "X7R", rated_v=50.0)
    assert 1 - dense > 0.7
    assert 1 - sparse < 0.4
