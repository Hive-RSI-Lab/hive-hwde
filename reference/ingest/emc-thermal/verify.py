"""verify.py - reproduce the EMC / ESD / thermal rules of thumb that
reference/ingest/emc-thermal.md marks verified or disputed, from first
principles or a simulation, so a rule hwde turns into a check rests on
arithmetic anyone can rerun, not on a blog.

    bin/hwde python reference/ingest/emc-thermal/verify.py          # table
    bin/hwde python reference/ingest/emc-thermal/verify.py --spice  # + ngspice
(or the venv's python directly). tests/test_ingest_emc_thermal.py runs every
function here against the numbers the sources print.

Each function names the rule id it reproduces (ESD-n, EMC-n, TH-n).
SI units throughout unless a name says otherwise.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

C0 = 299_792_458.0          # m/s
MU0 = 4e-7 * math.pi        # H/m
ETA0 = MU0 * C0             # ~376.7 ohm
K_CU = 385.0                # W/m-K, annealed copper (vendors use 380-401)
OZ_M = 34.8e-6              # 1 oz/ft2 copper, m

# ------------------------------------------------------------------ ESD

# IEC 61000-4-2:2008 Table 3, contact discharge, per kV of test level
IEC_AMPS_PER_KV = 3.75      # first peak, +/-15 %
IEC_RISE_S = 0.8e-9         # 10-90 %, +/-25 %
IEC_I30_PER_KV = 2.0        # A at 30 ns
IEC_I60_PER_KV = 1.0        # A at 60 ns


def esd_di_dt(level_kv: float) -> float:
    """ESD-1: the steepest slope of the first peak, peak / rise (A/s).
    TI SLVA680A rounds the 8 kV figure to 4e10."""
    return IEC_AMPS_PER_KV * level_kv / IEC_RISE_S


def esd_l_volts(l_h: float, level_kv: float = 8.0) -> float:
    """ESD-4: the L dI/dt overshoot one inductance adds (V)."""
    return l_h * esd_di_dt(level_kv)


def trace_partial_l(length_m: float, width_m: float,
                    thick_m: float = OZ_M) -> float:
    """ESD-2: partial self-inductance of a straight flat bar (Grover;
    Ruehli's closed form for l >> w+t), H. A loop's inductance is the sum of
    its partials less the mutuals, so over a solid plane the real loop
    inductance is less than this."""
    l, wt = length_m, width_m + thick_m
    return 2e-7 * l * (math.log(2 * l / wt) + 0.5 + wt / (3 * l))


def microstrip_loop_l_per_m(z0: float, eps_eff: float) -> float:
    """ESD-2: loop inductance per length of a line over its plane,
    L = Z0 sqrt(eps_eff) / c (H/m)."""
    return z0 * math.sqrt(eps_eff) / C0


def via_l(height_m: float, drill_m: float) -> float:
    """ESD-3: a through via's partial inductance (Johnson & Graham,
    High-Speed Digital Design 1993: L = h/5 (1 + ln(4h/d)) nH, h, d in mm
    as the metric form of 5.08 h(in) ...), H."""
    h, d = height_m * 1e3, drill_m * 1e3
    return h / 5 * (1 + math.log(4 * h / d)) * 1e-9


def air_gap_for_kv(kv: float, field_kv_per_mm: float = 3.0) -> float:
    """ESD-6: the uniform-field air gap that holds `kv` (mm) at ~3 kV/mm,
    the textbook dry-air breakdown for mm gaps (Paschen gives more for
    smaller gaps). TI's '2.6 mm' corner radius for 7 kV is this gap, not a
    corner property."""
    return kv / field_kv_per_mm


def esd_spice(l_gnd_h: list[float], level_kv: float = 8.0,
              bv: float = 6.0, rdyn: float = 0.3) -> dict:
    """ESD-4 by simulation: an IEC-shaped current into a zener clamp whose
    ground runs through L; the peak at the clamp's protected node minus the
    L=0 peak is the inductive overshoot. Needs KiCad's ngspice (hwde's
    simlib); returns {"error": ...} when it can't run."""
    here = Path(__file__).resolve()
    scripts = here.parents[3] / "scripts"
    sys.path[:0] = [str(scripts), str(scripts / "lib")]
    try:
        import env  # noqa: PLC0415
        import simlib  # noqa: PLC0415
        dll = env.find_ngspice_dll()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"no engine: {exc}"}
    if dll is None:
        return {"error": "no ngspice library"}
    simlib.setup_engine_env(dll)
    ip = IEC_AMPS_PER_KV * level_kv
    # 10-90 % in 0.8 ns: a linear 0 -> peak ramp of 1.0 ns, then the IEC
    # shoulder (I30, I60) and a tail
    pwl = (f"0 0 1n {ip} 3.5n {0.55 * ip} 30n {IEC_I30_PER_KV * level_kv} "
           f"60n {IEC_I60_PER_KV * level_kv} 200n 0")
    out = {}
    for l in [0.0] + list(l_gnd_h):
        lval = max(l, 1e-15)
        cir = f"""* ESD clamp with ground inductance
I1 0 n1 PWL({pwl})
D1 g n1 DZ
L3 g 0 {lval:g}
R1 n1 0 1e6
.model DZ D(BV={bv} IBV=1m RS={rdyn} N=1 CJO=1p)
.tran 5p 20n
.measure tran vpk MAX v(n1)
.end
"""
        res = simlib.run_circuit(simlib.prepare_circuit(cir))
        if res.get("status") != "ok" or "vpk" not in res["measures"]:
            return {"error": res.get("error") or res.get("failed_measures")}
        out[l] = res["measures"]["vpk"]
    base = out[0.0]
    return {"base_v": base, "peaks": {f"{l:g}": v for l, v in out.items()},
            "extra_v": {f"{l:g}": out[l] - base for l in l_gnd_h},
            "slope_a_per_s": ip / 1e-9}


# ------------------------------------------------------------------ EMC

def dm_loop_e(f_hz: float, area_m2: float, i_a: float, r_m: float,
              ground_reflection: bool = False) -> float:
    """EMC-1: far field of a small current loop, E = eta beta^2 A I /
    (4 pi r) (V/m); Ott's 131.6e-16 f^2 A I / r, doubled to 263e-16 over a
    reflecting ground plane (an OATS)."""
    beta = 2 * math.pi * f_hz / C0
    e = ETA0 * beta ** 2 * area_m2 * i_a / (4 * math.pi * r_m)
    return 2 * e if ground_reflection else e


def cm_wire_e(f_hz: float, length_m: float, i_a: float, r_m: float,
              ground_reflection: bool = True) -> float:
    """EMC-2: far field of a short wire carrying uniform current, E = eta
    beta I L / (4 pi r) broadside (V/m); Ott's 1.257e-6 f L I / r is this
    doubled for the ground reflection."""
    beta = 2 * math.pi * f_hz / C0
    e = ETA0 * beta * i_a * length_m / (4 * math.pi * r_m)
    return 2 * e if ground_reflection else e


def cm_current_at_limit(e_limit: float, f_hz: float, length_m: float,
                        r_m: float, ground_reflection: bool = True) -> float:
    """EMC-2: the common-mode current that reaches a field limit (A)."""
    return e_limit / cm_wire_e(f_hz, length_m, 1.0, r_m, ground_reflection)


def resonant_dipole_current_for(e_limit: float, r_m: float,
                                r_rad: float = 73.0,
                                gain: float = 1.64) -> float:
    """EMC-2 by Hubing's power argument: the rms current in a resonant
    half-wave dipole that radiates the field `e_limit` (V/m) at r in its best
    direction. P = E^2 4 pi r^2 / (eta G), I = sqrt(P / R_rad)."""
    p = e_limit ** 2 * 4 * math.pi * r_m ** 2 / (ETA0 * gain)
    return math.sqrt(p / r_rad)


# FCC 47 CFR 15.109(a), class B, 3 m, quasi-peak (uV/m)
FCC_B_3M = [(30e6, 88e6, 100.0), (88e6, 216e6, 150.0),
            (216e6, 960e6, 200.0), (960e6, 40e9, 500.0)]
# CISPR 32 / EN 55032 class B, 10 m, quasi-peak (dBuV/m)
CISPR32_B_10M = [(30e6, 230e6, 30.0), (230e6, 1e9, 37.0)]


def dbuv(v_per_m: float) -> float:
    return 20 * math.log10(v_per_m / 1e-6)


def limit_fcc_b_3m(f_hz: float) -> float:
    for lo, hi, uv in FCC_B_3M:
        if lo <= f_hz < hi:
            return uv * 1e-6
    raise ValueError(f"{f_hz} Hz outside FCC 15.109")


def trapezoid_harmonics(f0: float, duty: float, tr: float, n_max: int,
                        amp: float = 1.0, samples: int = 1 << 16):
    """EMC-3: one-sided harmonic amplitudes of a trapezoid (equal rise and
    fall) by FFT: list of (f, |c_n|)."""
    t = np.arange(samples) / samples / f0
    T = 1 / f0
    tau = duty * T          # 50 % width, as Paul defines it
    x = np.zeros_like(t)
    t1 = tau - tr           # flat top length
    rise = (t < tr)
    top = (t >= tr) & (t < tr + t1)
    fall = (t >= tr + t1) & (t < 2 * tr + t1)
    x[rise] = t[rise] / tr
    x[top] = 1.0
    x[fall] = 1 - (t[fall] - tr - t1) / tr
    spec = np.fft.rfft(x * amp) / samples
    return [(n * f0, 2 * abs(spec[n])) for n in range(1, n_max + 1)]


def trapezoid_envelope(f: float, f0: float, duty: float, tr: float,
                       amp: float = 1.0) -> float:
    """EMC-3: Paul's bound 2 A d, -20 dB/dec past 1/(pi tau), -40 dB/dec
    past 1/(pi tr)."""
    tau = duty / f0
    f1, f2 = 1 / (math.pi * tau), 1 / (math.pi * tr)
    e = 2 * amp * duty
    if f > f1:
        e *= f1 / f
    if f > f2:
        e *= f2 / f
    return e


def cavity_resonance(a_m: float, b_m: float, eps_r: float, m: int = 1,
                     n: int = 0) -> float:
    """EMC-4: TM_mn of a plane pair with open edges (Hz)."""
    return C0 / (2 * math.sqrt(eps_r)) * math.hypot(m / a_m, n / b_m)


def slot_se_db(f_hz: float, slot_m: float) -> float:
    """EMC-5: Ott's single-aperture shielding estimate, SE = 20 log
    (lambda / 2L), floored at 0 (a slot of lambda/2 is a resonant antenna)."""
    lam = C0 / f_hz
    return max(0.0, 20 * math.log10(lam / (2 * slot_m)))


def stitch_pitch(f_hz: float, eps_r: float = 4.4, frac: float = 20.0) -> float:
    """EMC-5: lambda/frac in the dielectric (m)."""
    return C0 / (f_hz * math.sqrt(eps_r)) / frac


def lc_filter_peak_db(l_h: float, c_f: float, r_damp: float) -> tuple:
    """EMC-7: a bead (as L) into a cap: resonance and the gain at it, with
    r_damp the total series loss (bead DCR + cap ESR)."""
    f = 1 / (2 * math.pi * math.sqrt(l_h * c_f))
    z0 = math.sqrt(l_h / c_f)
    q = z0 / r_damp
    return f, 20 * math.log10(q)


# -------------------------------------------------------------- thermal

def via_theta(drill_m: float, plating_m: float, length_m: float,
              k: float = K_CU, filled: bool = False) -> float:
    """TH-3: one via's conduction along its barrel, L / (k A) (K/W); the
    annulus from the drill wall inward (plating inside the drilled hole),
    or the whole drill when copper-filled."""
    r = drill_m / 2
    if filled:
        a = math.pi * r ** 2
    else:
        a = math.pi * (r ** 2 - (r - plating_m) ** 2)
    return length_m / (k * a)


def fin_theta(r0_m: float, r_out_m: float, cu_m: float = OZ_M,
              h_w_m2k: float = 10.0, sides: int = 2,
              k: float = K_CU) -> float:
    """TH-5: an annular copper fin heated at radius r0, insulated at r_out,
    cooled by h on `sides` faces: the exact Bessel-function solution (K/W),
    plus the heated disc's own surface under the part."""
    from scipy.special import i0, i1, k0, k1  # noqa: PLC0415
    m = math.sqrt(sides * h_w_m2k / (k * cu_m))
    a, b = m * r0_m, m * r_out_m
    num = k1(a) * i1(b) - i1(a) * k1(b)
    den = k0(a) * i1(b) + i0(a) * k1(b)
    g_fin = 2 * math.pi * k * cu_m * r0_m * m * num / den
    g_disc = sides * h_w_m2k * math.pi * r0_m ** 2
    return 1 / (g_fin + g_disc)


def fin_saturation_area(r0_m: float, within: float = 0.10, **kw) -> float:
    """TH-5: the copper area (mm^2) at which the fin's theta comes within
    `within` of an infinite sheet's: more copper past it buys < 10 %."""
    th_inf = fin_theta(r0_m, 2.0, **kw)
    lo, hi = r0_m, 2.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if fin_theta(r0_m, mid, **kw) > th_inf * (1 + within):
            lo = mid
        else:
            hi = mid
    return math.pi * hi ** 2 * 1e6


def tj_from_top(t_top_c: float, psi_jt: float, p_w: float) -> float:
    """TH-2: TI SPRA953 eq 8."""
    return t_top_c + psi_jt * p_w


# ----------------------------------------------------------------- table

def table() -> list[tuple]:
    rows = []
    rows.append(("ESD-1", "8 kV contact dI/dt (A/s)", esd_di_dt(8)))
    rows.append(("ESD-4", "0.25 nH at 8 kV (V), TI: ~10", esd_l_volts(0.25e-9)))
    rows.append(("ESD-2", "10 mm x 0.2 mm 1 oz trace partial L (nH)",
                 trace_partial_l(10e-3, 0.2e-3) * 1e9))
    rows.append(("ESD-2", "50 ohm microstrip loop L (nH/mm), eps_eff 3.3",
                 microstrip_loop_l_per_m(50, 3.3) * 1e6))
    rows.append(("ESD-3", "1.6 mm via, 0.3 mm drill (nH)",
                 via_l(1.6e-3, 0.3e-3) * 1e9))
    rows.append(("ESD-6", "air gap holding 7 kV (mm), TI: 2.6",
                 air_gap_for_kv(7)))
    rows.append(("EMC-1", "Ott constant, free space (x1e-16)",
                 dm_loop_e(1, 1, 1, 1) * 1e16))
    rows.append(("EMC-2", "Ott CM constant w/ reflection (x1e-6)",
                 cm_wire_e(1, 1, 1, 1) * 1e6))
    rows.append(("EMC-2", "CM uA on 1 m at 100 MHz, 3 m, 150 uV/m",
                 cm_current_at_limit(150e-6, 100e6, 1, 3) * 1e6))
    rows.append(("EMC-2", "CM uA, same, no reflection, 100 uV/m (Bogatin)",
                 cm_current_at_limit(100e-6, 100e6, 1, 3, False) * 1e6))
    rows.append(("EMC-2", "resonant dipole uA at 100 uV/m, 3 m (Hubing ~5)",
                 resonant_dipole_current_for(100e-6, 3) * 1e6))
    rows.append(("EMC-4", "100 x 80 mm FR4 TM10 (MHz)",
                 cavity_resonance(0.1, 0.08, 4.4) / 1e6))
    rows.append(("EMC-5", "SE of a lambda/20 slot (dB)",
                 slot_se_db(1e9, C0 / 1e9 / 20)))
    rows.append(("EMC-5", "lambda/20 in FR4 at 1 GHz (mm)",
                 stitch_pitch(1e9) * 1e3))
    rows.append(("TH-3", "SNVA419C 12 mil via, 0.5 oz, 1.65 mm (K/W): 261",
                 via_theta(0.3048e-3, 17.5e-6, 1.65e-3)))
    rows.append(("TH-3", "Wurth 0.6 mm ID, 35 um, 1.6 mm (K/W): 64",
                 via_theta(0.67e-3, 35e-6, 1.6e-3)))
    rows.append(("TH-3", "Wurth copper-filled 0.6 mm (K/W): 14",
                 via_theta(0.6e-3, 0, 1.6e-3, filled=True)))
    rows.append(("TH-5", "1 oz fin, r0 2 mm, h 10: theta within 10 % (mm2)",
                 fin_saturation_area(2e-3)))
    rows.append(("TH-5", "2 oz fin, same (mm2)",
                 fin_saturation_area(2e-3, cu_m=2 * OZ_M)))
    rows.append(("TH-5", "1 oz fin, r0 2 mm, infinite sheet (K/W)",
                 fin_theta(2e-3, 2.0)))
    rows.append(("TH-5", "1 oz fin, r0 2 mm, 645 mm2 (K/W)",
                 fin_theta(2e-3, math.sqrt(645e-6 / math.pi))))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spice", action="store_true",
                    help="also run the ESD-4 clamp simulation")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = table()
    out = {"rows": [{"rule": r, "what": w, "value": v} for r, w, v in rows]}
    if args.spice:
        out["esd_spice"] = esd_spice([0.25e-9, 1e-9, 3e-9])
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        for r, w, v in rows:
            print(f"{r:6} {w:55} {v:12.4g}")
        if args.spice:
            print(json.dumps(out["esd_spice"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
