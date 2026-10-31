"""analogcalc.py - analog and audio hand calculations, each one checked.

The formulas a reviewer of an op-amp stage, a class-D output, a converter
front end or a line/speaker interface writes by hand. Every function here
is reproduced in tests/test_analog_calc.py against ngspice (KiCad's bundled
engine) or an independent numeric check, and reference/ingest/analog-audio.md
says which rule of thumb each one replaces and whether that rule held up
(verified, disputed or folklore).

Units are SI throughout (ohms, farads, henries, hertz, volts, seconds);
noise densities come back in V/rtHz and nV/rtHz.
"""
from __future__ import annotations

import math

K_BOLTZMANN = 1.380649e-23
ZERO_C_K = 273.15
DBU_REF_V = math.sqrt(0.6)          # 0 dBu: 1 mW into 600 ohm = 0.7746 Vrms


# ------------------------------------------------------------------ noise

def johnson_v(r: float, t_c: float = 25.0) -> float:
    """A resistor's thermal noise density sqrt(4kTR), V/rtHz."""
    return math.sqrt(4 * K_BOLTZMANN * (t_c + ZERO_C_K) * r)


def amp_noise(en: float, in_: float, rs: float, rf: float = 0.0,
              rg: float = math.inf, bw: float = 20e3, t_c: float = 25.0,
              fc_1f: float = 0.0, f_lo: float = 20.0) -> dict:
    """Input-referred noise of a non-inverting stage: source Rs on the +
    input, Rf from output to -, Rg from - to ground (rg inf: a follower).

    The terms are uncorrelated, so they add as powers: the op-amp's en, its
    in through Rs and through Rf||Rg, and the thermal noise of Rs and of
    Rf||Rg. en and in are the datasheet's white densities (V/rtHz, A/rtHz);
    fc_1f, when given, is en's 1/f corner, integrated from f_lo to bw."""
    rpar = 0.0 if rf == 0 else (rf if math.isinf(rg) else rf * rg / (rf + rg))
    terms = {
        "en": en,
        "in_rs": in_ * rs,
        "in_rfg": in_ * rpar,
        "rs_thermal": johnson_v(rs, t_c) if rs else 0.0,
        "rfg_thermal": johnson_v(rpar, t_c) if rpar else 0.0,
    }
    dens = math.sqrt(sum(v * v for v in terms.values()))
    # en's 1/f part adds en^2 * fc * ln(bw/f_lo) to the integral
    flicker = en * en * fc_1f * math.log(bw / f_lo) if fc_1f else 0.0
    vrms = math.sqrt(dens * dens * (bw - f_lo) + flicker)
    gain = 1.0 if math.isinf(rg) else 1.0 + rf / rg
    dominant = max(terms, key=terms.get)
    return {"density_nv": dens * 1e9,
            "terms_nv": {k: v * 1e9 for k, v in terms.items()},
            "dominant": dominant, "input_uvrms": vrms * 1e6,
            "noise_gain": gain, "output_uvrms": vrms * gain * 1e6,
            "rs_only_nv": johnson_v(rs, t_c) * 1e9 if rs else 0.0,
            "noise_figure_db": (20 * math.log10(dens / johnson_v(rs, t_c))
                                if rs else None)}


def snr_db(signal_vrms: float, noise_vrms: float) -> float:
    return 20 * math.log10(signal_vrms / noise_vrms)


# ------------------------------------------------------------------ levels

def dbu_to_vrms(dbu: float) -> float:
    return DBU_REF_V * 10 ** (dbu / 20)


def dbv_to_vrms(dbv: float) -> float:
    return 10 ** (dbv / 20)


def vrms_to_dbu(v: float) -> float:
    return 20 * math.log10(v / DBU_REF_V)


def bridging_loss_db(z_out: float, z_in: float) -> float:
    """Level lost where a source of z_out drives an input of z_in: the
    voltage divider, in dB (negative)."""
    return 20 * math.log10(z_in / (z_in + z_out))


def damping_deviation_db(z_out: float, z_min: float, z_max: float) -> float:
    """How far a source of z_out tilts the response of a load whose
    impedance swings z_min..z_max over frequency (headphones, a speaker):
    the difference of the divider loss at the two ends, dB. This is what the
    '1/8 rule' (z_out <= z_load/8) stands in for."""
    return bridging_loss_db(z_out, z_max) - bridging_loss_db(z_out, z_min)


# ------------------------------------------------------------------ coupling

def hpf_corner(c: float, r_load: float, r_src: float = 0.0) -> float:
    """A coupling capacitor's -3 dB corner: it sees the source and the load
    in series."""
    return 1.0 / (2 * math.pi * (r_src + r_load) * c)


def hpf_c_for(fc: float, r_load: float, r_src: float = 0.0) -> float:
    return 1.0 / (2 * math.pi * (r_src + r_load) * fc)


def hpf_loss_db(f: float, fc: float, order: int = 1) -> float:
    """Loss of `order` cascaded first-order high-pass corners at fc, at f
    (negative dB). Two coupling caps in a chain each at 20 Hz lose 6 dB at
    20 Hz, not 3."""
    x = f / fc
    return order * 20 * math.log10(x / math.sqrt(1 + x * x))


def bias_drift_v_per_s(i_bias: float, c: float) -> float:
    """How fast an op-amp's bias current walks an input that has no DC
    path: it charges whatever capacitance the node has, dV/dt = Ib/C."""
    return i_bias / c


# ------------------------------------------------------------------ slew

def full_power_bw(slew_v_per_s: float, vpk: float) -> float:
    """Highest frequency a sine of vpk can reach before the slew rate
    limits it: max dV/dt of vpk*sin(wt) is vpk*w."""
    return slew_v_per_s / (2 * math.pi * vpk)


def slew_needed(f: float, vpk: float) -> float:
    return 2 * math.pi * f * vpk


# ------------------------------------------------------------------ stability

def _second_order_overshoot(pm_deg: float) -> float:
    """Step overshoot (fraction) of a two-pole loop with phase margin pm.
    zeta from pm for the standard second-order loop:
    pm = atan(2 zeta / sqrt(sqrt(1 + 4 zeta^4) - 2 zeta^2))."""
    lo, hi = 1e-4, 2.0
    for _ in range(80):
        z = (lo + hi) / 2
        pm = math.degrees(math.atan(2 * z / math.sqrt(
            math.sqrt(1 + 4 * z ** 4) - 2 * z * z)))
        lo, hi = (z, hi) if pm < pm_deg else (lo, z)
    z = (lo + hi) / 2
    return math.exp(-math.pi * z / math.sqrt(1 - z * z)) if z < 1 else 0.0


def cap_load_pm(gbw: float, ro: float, cl: float, noise_gain: float = 1.0,
                riso: float = 0.0, f_p2: float = math.inf) -> dict:
    """Phase margin of a voltage-feedback op-amp driving cl through its
    open-loop output resistance ro and an optional series riso, feedback
    taken at the op-amp's own output (before riso).

    The model: a dominant-pole amplifier (gain-bandwidth gbw) with an
    optional second internal pole f_p2, then ro + riso into cl. The load adds
    a pole at 1/(2 pi (ro+riso) cl) and riso a zero at 1/(2 pi riso cl). The
    crossover is solved, not assumed at gbw/noise_gain, so a big load pole
    moving it is counted. Real parts have more poles and ro is often
    frequency dependent (datasheet Zo plot), so treat > 45 deg as a margin
    estimate and the datasheet's own cap-load curve as the last word."""
    def loop(f):
        s = 1j * 2 * math.pi * f
        a = (2 * math.pi * gbw) / s
        if not math.isinf(f_p2):
            a /= (1 + s / (2 * math.pi * f_p2))
        z_c = 1 / (s * cl)
        h = (riso + z_c) / (ro + riso + z_c)
        return a * h / noise_gain

    lo, hi = 1.0, gbw * 10
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if abs(loop(mid)) > 1 else (lo, mid)
    fc = math.sqrt(lo * hi)
    g = loop(fc)
    pm = 180.0 + math.degrees(math.atan2(g.imag, g.real))
    pm = pm - 360 if pm > 180 else pm
    fp = 1 / (2 * math.pi * (ro + riso) * cl)
    return {"crossover_hz": fc, "phase_margin_deg": pm,
            "load_pole_hz": fp,
            "riso_zero_hz": 1 / (2 * math.pi * riso * cl) if riso else None,
            "overshoot_est": _second_order_overshoot(max(pm, 0.1)),
            "stable_45": pm >= 45.0}


def riso_for_pm(gbw: float, ro: float, cl: float, noise_gain: float = 1.0,
                pm_target: float = 60.0, f_p2: float = math.inf) -> float | None:
    """The smallest riso giving pm_target, or None if no riso up to 10 kohm
    gets there."""
    if cap_load_pm(gbw, ro, cl, noise_gain, 0.0, f_p2)[
            "phase_margin_deg"] >= pm_target:
        return 0.0
    lo, hi = 0.0, 1e4
    if cap_load_pm(gbw, ro, cl, noise_gain, hi, f_p2)[
            "phase_margin_deg"] < pm_target:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        ok = cap_load_pm(gbw, ro, cl, noise_gain, mid, f_p2)[
            "phase_margin_deg"] >= pm_target
        lo, hi = (lo, mid) if ok else (mid, hi)
    return hi


# ------------------------------------------------------------------ class D

def classd_lc(l: float, c: float, r_load: float, btl: bool = True,
              c_diff: float = 0.0, f_sw: float | None = None,
              f_audio: float = 20e3, r_dcr: float = 0.0) -> dict:
    """A class-D output LC filter: L and C per leg (C from each output to
    ground), c_diff across the load. For a BTL output the two legs in
    series are one differential filter of 2L and C/2 + c_diff into r_load,
    so f0 = 1/(2 pi sqrt(2L (C/2 + Cd))) and Q = R sqrt(Ceff/Leff).
    Single-ended: L, C + c_diff, r_load directly.

    Q is set by the load, so it is also given with the speaker gone
    (open: only the inductors' DCR damps it) and at twice the nominal load
    (a speaker's impedance rises well above nominal near f0, from its voice
    coil inductance)."""
    leff = 2 * l if btl else l
    ceff = (c / 2 if btl else c) + c_diff
    rdcr = (2 * r_dcr) if btl else r_dcr
    f0 = 1 / (2 * math.pi * math.sqrt(leff * ceff))

    def resp(f, r):
        s = 1j * 2 * math.pi * f
        zl = s * leff + rdcr
        zc = 1 / (s * ceff)
        zp = zc if r is None else (zc * r) / (zc + r)
        return abs(zp / (zl + zp))

    def peak_db(r):
        best = 0.0
        f = f0 / 4
        while f < f0 * 4:
            best = max(best, resp(f, r))
            f *= 1.002
        return 20 * math.log10(best)

    q = r_load * math.sqrt(ceff / leff)
    out = {"f0_hz": f0, "q": q, "leff_h": leff, "ceff_f": ceff,
           "peak_db": peak_db(r_load),
           "at_f_audio_db": 20 * math.log10(resp(f_audio, r_load)),
           "peak_db_2x_load": peak_db(2 * r_load),
           "peak_db_open": peak_db(None),
           "q_open": (math.sqrt(leff / ceff) / rdcr) if rdcr else math.inf}
    if f_sw:
        out["at_fsw_db"] = 20 * math.log10(resp(f_sw, r_load))
    return out


def classd_lc_for(r_load: float, f0: float, q: float = 0.707,
                  btl: bool = True) -> dict:
    """L and C (per leg, C to ground) for a target f0 and Q into r_load."""
    # differential: leff = r/(2 pi f0 q), ceff = q/(2 pi f0 r)
    leff = r_load / (2 * math.pi * f0 * q)
    ceff = q / (2 * math.pi * f0 * r_load)
    return ({"l_h": leff / 2, "c_f": ceff * 2} if btl
            else {"l_h": leff, "c_f": ceff})


def classd_power(pvdd: float, r_load: float, rds_on: float = 0.0,
                 r_dcr: float = 0.0, btl: bool = True,
                 thd10_ratio: float = 1.22) -> dict:
    """Output power at the onset of clipping (about 1 % THD) and the
    datasheet's usual 10 % THD figure. BTL swings the full PVDD across the
    load each way; single-ended half of it. The FETs (high and low side, one
    of each in the path per leg) and the filter's DCR divide it down."""
    legs = 2 if btl else 1
    r_path = legs * (rds_on + r_dcr)
    vpk = (pvdd if btl else pvdd / 2) * r_load / (r_load + r_path)
    p1 = vpk * vpk / (2 * r_load)
    return {"vpk": vpk, "p_1pct_w": p1, "p_10pct_w": p1 * thd10_ratio,
            "i_pk_a": vpk / r_load}


# ------------------------------------------------------------------ THD

def thd_from_samples(samples, fs: float, f0: float, n_harm: int = 9) -> dict:
    """THD of a waveform sampled at fs whose record holds a whole number of
    f0 cycles (coherent: then each harmonic falls in one DFT bin and no
    window is needed). Harmonics above fs/2 are dropped."""
    n = len(samples)
    cycles = f0 * n / fs
    if abs(cycles - round(cycles)) > 1e-6:
        raise ValueError(f"record holds {cycles:.4f} cycles of f0: not "
                         f"coherent, use an integer number")
    k0 = int(round(cycles))

    def amp(k):
        re = im = 0.0
        w = 2 * math.pi * k / n
        for i, x in enumerate(samples):
            re += x * math.cos(w * i)
            im -= x * math.sin(w * i)
        return 2 * math.hypot(re, im) / n

    fund = amp(k0)
    harms = [amp(k0 * h) for h in range(2, n_harm + 1) if k0 * h < n / 2]
    thd = math.sqrt(sum(a * a for a in harms)) / fund
    return {"fundamental": fund, "harmonics": harms, "thd": thd,
            "thd_db": 20 * math.log10(thd) if thd > 0 else -math.inf}


def clipped_sine_thd(overdrive: float, n: int = 4096) -> float:
    """THD of a sine of amplitude `overdrive` hard-clipped at +-1."""
    xs = [max(-1.0, min(1.0, overdrive * math.sin(2 * math.pi * i / n)))
          for i in range(n)]
    return thd_from_samples(xs, n, 1.0, n_harm=31)["thd"]


# ------------------------------------------------------------------ SAR ADC

def sar_rc(r: float, c_filt: float, c_samp: float, n_bits: int,
           t_acq: float, vfs: float = 1.0) -> dict:
    """The RC between a driver and a SAR ADC's switched sampling capacitor.

    At the start of acquisition the sample cap (holding the last sample,
    worst case the opposite end of the range) is switched onto c_filt and
    the charge shares: the input steps by vfs * Cs / (Cs + Cf), the
    kickback. The driver then pulls it back through r with tau =
    r (Cf + Cs). It must be inside half an LSB by the end of t_acq.
    The '20x' rule (Cf >= 20 Cs) only bounds the kickback to 1/21 of full
    scale (4.8 %); the settling still has to be checked."""
    kick = vfs * c_samp / (c_samp + c_filt)
    tau = r * (c_filt + c_samp)
    resid = kick * math.exp(-t_acq / tau)
    half_lsb = vfs / 2 ** (n_bits + 1)
    t_need = tau * math.log(kick / half_lsb) if kick > half_lsb else 0.0
    return {"kickback_v": kick, "kickback_lsb": kick / (vfs / 2 ** n_bits),
            "tau_s": tau, "residual_v": resid,
            "residual_lsb": resid / (vfs / 2 ** n_bits),
            "settled": resid <= half_lsb, "t_settle_s": t_need,
            "n_tau": t_need / tau if tau else 0.0,
            "filter_fc_hz": 1 / (2 * math.pi * r * c_filt),
            "c_ratio": c_filt / c_samp}


def settle_taus(n_bits: int, step_fraction: float = 1.0) -> float:
    """Time constants a first-order step of step_fraction of full scale
    takes to land inside half an LSB: ln(step * 2^(N+1))."""
    return math.log(step_fraction * 2 ** (n_bits + 1))
