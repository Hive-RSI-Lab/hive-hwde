"""soa.py - a MOSFET's junction temperature through a power pulse.

A load switch that charges bulk capacitance runs in its linear region for
the whole ramp: it takes about the energy it puts into the capacitor
(1/2 C V^2 from a stiff source) in a few milliseconds, which a datasheet's
single-pulse SOA curve allows only up to a point. The curve and the
transient thermal impedance say the same thing: Zth(t) = (Tj_max - 25 C) /
P_SOA(t). With Zth(t) the junction rise for any power waveform follows by
superposition of step responses,

    dTj(t) = sum_k (P_k - P_k-1) * Zth(t - t_k)

so a simulated or calculated p(t) gives a peak Tj, not only a peak power or
an energy to hold against a curve by eye. Zth is interpolated log-log
between the points in reference/fet_power.yaml; below its first point it
falls as sqrt(t) (one-dimensional heat flow into the die), past its last it
stays at the last value (steady state).

Linear-mode SOA (rows with soa_vds): past a few volts the SOA lines of
many trench parts (OptiMOS, most modern low-RDS(on) FETs) fall as 1/VDS^2
- thermal instability, hot spots - not as the 1/VDS that Zth alone gives,
so the power a part takes for t depends on VDS. A row whose zth was read
off the SOA at soa_vds carries soa_vds, soa_vds_exp (1 for 1/VDS^2 current
lines: allowed power ~ 1/VDS) and soa_vds_knee (the VDS below which the
lines stop following it); junction() given vds(t) then superposes
p(t) x (max(|vds|, knee) / soa_vds)^exp - an equivalent pulse on the
curve's own basis. The Tj it reports is that equivalent, not a die
temperature; it is the SOA verdict.

Verdicts (judge()): tj_peak over tj_max is an error; within DERATE_C of it a
warning. Data from a package's generic row (no part row) never makes an
error: it is a warning naming the part to look up.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import yaml

REF_YAML = Path(__file__).resolve().parents[2] / "reference" / "fet_power.yaml"
DERATE_C = 25.0
SAMPLES = 600


def load(path: Path = REF_YAML) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {"fets": data.get("fets") or [], "generic": data.get("generic")
            or []}


def find(mpn: str = "", value: str = "", footprint: str = "",
         table: dict | None = None) -> dict | None:
    """The part's row, else a generic row for its package (marked
    generic=True), else None."""
    table = table or load()
    for row in table["fets"]:
        for text in (mpn, value):
            if text and re.search(row["match"], text, re.I):
                return dict(row, generic=False)
    for row in table["generic"]:
        if footprint and re.search(row["package"], footprint, re.I):
            return dict(row, generic=True, match=row["package"],
                        source=f"generic {row['package']} package row")
    return None


def zth(row: dict, t: float) -> float:
    """Single-pulse Zth (C/W) at t seconds."""
    pts = sorted((float(a), float(b)) for a, b in row["zth"])
    if t <= 0:
        return 0.0
    if t <= pts[0][0]:
        return pts[0][1] * math.sqrt(t / pts[0][0])
    for (t0, z0), (t1, z1) in zip(pts, pts[1:]):
        if t <= t1:
            f = math.log(t / t0) / math.log(t1 / t0)
            return math.exp(math.log(z0) + f * (math.log(z1) - math.log(z0)))
    return pts[-1][1]


def soa_power(row: dict, t: float, ta: float = 25.0) -> float:
    """Single-pulse power the part takes for t seconds at ambient ta."""
    z = zth(row, t)
    return (row["tj_max"] - ta) / z if z > 0 else math.inf


def resample(ts: list[float], ps: list[float], n: int = SAMPLES
             ) -> tuple[list[float], list[float]]:
    """The waveform on n uniform steps across the span where p is above
    0.5 % of its peak (plus one step either side), held piecewise-constant
    at each step's mean - enough for the millisecond events a switch sees
    and cheap for the O(n^2) superposition."""
    if not ts:
        return [], []
    pk = max(ps) if ps else 0.0
    if pk <= 0:
        return [ts[0]], [0.0]
    on = [i for i, p in enumerate(ps) if p > 0.005 * pk]
    t0, t1 = ts[max(on[0] - 1, 0)], ts[min(on[-1] + 1, len(ts) - 1)]
    if t1 <= t0:
        return [t0], [pk]
    dt = (t1 - t0) / n
    out_t, out_p = [], []
    j = 0
    for k in range(n):
        a, b = t0 + k * dt, t0 + (k + 1) * dt
        # trapezoid mean of p over [a, b]
        acc, last_t, last_p = 0.0, a, _interp(ts, ps, a)
        while j < len(ts) and ts[j] <= a:
            j += 1
        jj = j
        while jj < len(ts) and ts[jj] < b:
            acc += 0.5 * (last_p + ps[jj]) * (ts[jj] - last_t)
            last_t, last_p = ts[jj], ps[jj]
            jj += 1
        pb = _interp(ts, ps, b)
        acc += 0.5 * (last_p + pb) * (b - last_t)
        out_t.append(a)
        out_p.append(acc / dt)
    return out_t, out_p


def _interp(ts, ps, t):
    if t <= ts[0]:
        return ps[0]
    if t >= ts[-1]:
        return ps[-1]
    lo, hi = 0, len(ts) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ts[mid] <= t:
            lo = mid
        else:
            hi = mid
    f = (t - ts[lo]) / (ts[hi] - ts[lo]) if ts[hi] > ts[lo] else 0.0
    return ps[lo] + f * (ps[hi] - ps[lo])


def vds_scale(row: dict, vds: float) -> float:
    """The factor on p for a pulse at vds against a zth read off the SOA
    at the row's soa_vds (1 for a row without one)."""
    v0 = row.get("soa_vds")
    if not v0:
        return 1.0
    v = max(abs(vds), float(row.get("soa_vds_knee") or 0.0))
    return (v / float(v0)) ** float(row.get("soa_vds_exp", 1.0))


def junction(row: dict, ts: list[float], ps: list[float],
             ta: float = 25.0, vds: list[float] | None = None) -> dict:
    """Peak Tj for power waveform p(t) (W) at ambient ta, plus the pulse's
    energy, peak power, its width and the time of the Tj peak. With vds(t)
    and a row carrying soa_vds, p is scaled to the SOA's VDS first."""
    energy = sum(0.5 * (ps[i] + ps[i + 1]) * (ts[i + 1] - ts[i])
                 for i in range(len(ts) - 1))
    pk = max(ps) if ps else 0.0
    scaled = bool(vds is not None and row.get("soa_vds"))
    if scaled:
        ps = [p * vds_scale(row, v) for p, v in zip(ps, vds)]
    rt, rp = resample(ts, ps)
    if len(rt) < 2:
        return {"tj_peak_c": ta, "energy_j": energy, "p_peak_w": pk,
                "width_s": 0.0, "t_peak_s": rt[0] if rt else 0.0,
                "vds_scaled": scaled}
    dt = rt[1] - rt[0]
    steps = [rp[0]] + [rp[k] - rp[k - 1] for k in range(1, len(rp))]
    # evaluate at each step end and one pulse-length after (cooling)
    best, t_best = ta, rt[0]
    zcache = [zth(row, (m + 1) * dt) for m in range(len(rp) + 1)]
    for n in range(len(rp)):
        rise = sum(steps[k] * zcache[n - k] for k in range(n + 1))
        if ta + rise > best:
            best, t_best = ta + rise, rt[n] + dt
    width = energy / pk if pk > 0 else 0.0
    return {"tj_peak_c": best, "energy_j": energy, "p_peak_w": pk,
            "width_s": width, "t_peak_s": t_best,
            "zth_at_width": zth(row, width) if width > 0 else None,
            "vds_scaled": scaled}


def hotswap_profile(c_f: float, v: float, *, t_ramp: float | None = None,
                    i_a: float | None = None, i_load: float = 0.0,
                    n: int = 200, with_vds: bool = False) -> tuple:
    """p(t) of a switch ramping a capacitor c_f from 0 to v at a constant
    charging current (i_a, or c_f * v / t_ramp), with a resistive-free
    load current i_load beside it: p = (i_c + i_load) * (v - vout(t)).
    with_vds: also the switch's vds(t) = v - vout(t)."""
    if i_a is None:
        if not t_ramp:
            raise ValueError("hotswap needs t_ramp or i_a")
        i_a = c_f * v / t_ramp
    t_end = c_f * v / i_a
    ts = [t_end * k / n for k in range(n + 1)]
    vds = [v - i_a * t / c_f for t in ts]
    ps = [(i_a + i_load) * x for x in vds]
    return (ts, ps, vds) if with_vds else (ts, ps)


def judge(row: dict, res: dict) -> tuple[str | None, str]:
    """(severity or None, reason) for a junction() result."""
    tj, tmax = res["tj_peak_c"], row["tj_max"]
    if tj > tmax:
        sev = "warning" if row.get("generic") else "error"
        return sev, f"Tj peaks at {tj:.0f} C, over its {tmax:g} C maximum"
    if tj > tmax - DERATE_C:
        return "warning", (f"Tj peaks at {tj:.0f} C, within {DERATE_C:g} C "
                           f"of its {tmax:g} C maximum")
    return None, f"Tj peaks at {tj:.0f} C (max {tmax:g} C)"
