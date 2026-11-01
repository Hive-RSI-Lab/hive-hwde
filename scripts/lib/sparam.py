"""sparam.py - S-parameter files: read, judge, fit (#483).

An S-parameter set from an EM solve or a VNA is only fit to drive a
time-domain sim when it is passive, reciprocal (for a passive structure
with no ferrites) and causal. IEEE P370-2020 scores each one 0-100 %;
`p370_metrics` is a line-by-line port of the standard's own open code
(opensource.ieee.org/elec-char/ieee-370, TG3/qualityCheckFrequencyDomain.m,
BSD-3-Clause), so the numbers are the standard's:

  passivity    PM(f) = largest singular value of S(f); PW = (PM - 1.00001)/0.1
               where PM > 1.00001; PQM = max(N - sum PW, 0)/N * 100
  reciprocity  RM(f) = sum |S_ij - S_ji| / (P(P-1)); RW = (RM - 1e-6)/0.1
               where RM > 1e-6; RQM likewise
  causality    for each S_ij, R_k = Re(V_k+1) Im(V_k) - Im(V_k+1) Re(V_k)
               with V_k = S(f_k+1) - S(f_k): a causal response turns
               clockwise in the complex plane (R > 0). CQM = min over ij
               of sum(R > 0) / sum |R| * 100

P370's quality levels (passivity/reciprocity: good > 99.9, acceptable
> 99, inconclusive > 80, else poor; causality: good > 80, acceptable > 50,
inconclusive > 20) are reported beside each metric.

A metric is an average: two bad points among 400 still score "good". So
`check` also lists the frequency points that fail, and the verdict fails
on any of them:

  passive_violation   PM(f) > 1 + passivity_tol (default 1e-3)
  nonreciprocal       max |S_ij - S_ji| > reciprocity_tol (default 1e-2;
                      the standard's 1e-6 is below any solver's or VNA's
                      noise)
  noncausal_turn      an S_ij turns counter-clockwise (R < 0) by more than
                      causality_tol (default 30) times the median |R| of the
                      21 turns around it; the point reported is the middle
                      one of the three that turn. A glitch stands out of
                      its neighbours; noise near a measurement's floor
                      turns both ways at the same scale and is left to CQM.

The defaults pass P370's own sample measurement (TG3 briefcase
S5_M1_plugandplay.s2p: sigma_max 1.0004 at 6 MHz, |S21 - S12| up to 2.8e-3,
no isolated turn above 10x its neighbours) and fail its de-embedded 4-port
(Test8_afterAFR_42p5.s4p, sigma_max 1.14 above 45 GHz).

plus warnings on what a transient sim needs from the band: a DC point or
a band start within 1.5 steps of DC (`no_dc_point`, `band_start_far`), a
uniform grid (`nonuniform_grid`), a real-valued DC point (`dc_not_real`),
a step fine enough for the longest delay (`step_too_coarse`: phase
moving more than 90 degrees per step on a transmission term) and a
transmission phase that extrapolates to 0 at DC (`dc_phase_offset`).

`fit` vector-fits (Gustavsen & Semlyen 1999) with scikit-rf's
VectorFitting from the IIC-OSIC-TOOLS tree's dist-packages (not in hwde's
lock), enforces passivity, and writes a SPICE subcircuit (pins p1..pN,
reference ground 0). Nothing here needs the tree but `fit`.
"""
from __future__ import annotations

import glob
import math
import re
import sys
from pathlib import Path

import numpy as np

# P370 TG3 qualityCheckFrequencyDomain.m constants
P370_A = 1.00001
P370_B = 0.1
P370_C = 1e-6

LEVELS_PR = ((99.9, "good"), (99.0, "acceptable"), (80.0, "inconclusive"))
LEVELS_C = ((80.0, "good"), (50.0, "acceptable"), (20.0, "inconclusive"))

DEFAULT_TOLS = {"passivity_tol": 1e-3, "reciprocity_tol": 1e-2, "causality_tol": 30.0}
CAUSALITY_WINDOW = 21

UNITS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


class SparamError(ValueError):
    pass


# ------------------------------------------------------------- Touchstone

def _nports_from_name(path: Path) -> int | None:
    m = re.search(r"\.s(\d+)p$", path.name, re.I)
    return int(m.group(1)) if m else None


def read_touchstone(path, nports: int | None = None) -> dict:
    """{freqs [Hz] (N,), s (N, P, P) complex, z0 (float), nports, comments}.
    Touchstone 1.x (and 2.0 files with S data in the v1 layout): # line with
    Hz/kHz/MHz/GHz, S only, MA/DB/RI, one reference R. A 2-port's noise
    block (frequency drops) ends the data."""
    path = Path(path)
    text = path.read_text(errors="replace")
    p = nports or _nports_from_name(path)
    unit, fmt, z0, par = 1e9, "ma", 50.0, "s"
    order21 = None
    nums: list[float] = []
    comments: list[str] = []
    seen_opt = False
    for raw in text.splitlines():
        line, _, com = raw.partition("!")
        if com.strip():
            comments.append(com.strip())
        line = line.strip()
        if not line:
            continue
        if line.startswith("["):
            key, _, val = line[1:].partition("]")
            key = key.strip().lower()
            val = val.strip()
            if key == "number of ports":
                p = int(val)
            elif key == "two-port data order":
                order21 = val.replace(" ", "").lower() == "21_12"
            elif key == "noise data" or key == "end":
                break
            elif key == "reference" and val:
                z0 = float(val.split()[0])
            continue
        if line.startswith("#"):
            if seen_opt:
                continue
            seen_opt = True
            toks = line[1:].lower().split()
            i = 0
            while i < len(toks):
                t = toks[i]
                if t in UNITS:
                    unit = UNITS[t]
                elif t in ("ma", "db", "ri"):
                    fmt = t
                elif t in ("s", "y", "z", "h", "g"):
                    par = t
                elif t == "r" and i + 1 < len(toks):
                    z0 = float(toks[i + 1])
                    i += 1
                i += 1
            continue
        nums += [float(v) for v in line.split()]
    if par != "s":
        raise SparamError(f"{path.name}: only S-parameter files are read (this is {par.upper()})")
    if not p:
        raise SparamError(f"{path.name}: port count unknown (name it .sNp or pass nports)")
    per = 1 + 2 * p * p
    rows = len(nums) // per
    if rows == 0:
        raise SparamError(f"{path.name}: no data")
    arr = np.array(nums[:rows * per]).reshape(rows, per)
    f = arr[:, 0]
    # a 2-port noise block (v1) starts where the frequency stops rising
    drop = np.nonzero(np.diff(f) <= 0)[0]
    if len(drop):
        arr = arr[:drop[0] + 1]
        f = arr[:, 0]
    a, b = arr[:, 1::2], arr[:, 2::2]
    if fmt == "ri":
        v = a + 1j * b
    elif fmt == "ma":
        v = a * np.exp(1j * np.deg2rad(b))
    else:
        v = 10 ** (a / 20) * np.exp(1j * np.deg2rad(b))
    s = v.reshape(len(f), p, p)
    if p == 2 and order21 is not False:  # v1 and [Two-Port Data Order] 21_12: S11 S21 S12 S22
        s = s.transpose(0, 2, 1)
    return {"freqs": f * unit, "s": s, "z0": z0, "nports": p, "comments": comments,
            "path": str(path)}


def write_touchstone(path, freqs, s, z0: float = 50.0, comment: str = "hwde") -> Path:
    """A Touchstone v1 file (Hz, S, RI, R z0)."""
    s = np.asarray(s)
    n = s.shape[1]
    lines = [f"! {comment}", f"# Hz S RI R {z0:g}"]
    order = ([(0, 0), (1, 0), (0, 1), (1, 1)] if n == 2
             else [(i, j) for i in range(n) for j in range(n)])
    for m, f in enumerate(freqs):
        row = [f"{f:.9e}"]
        for i, j in order:
            v = s[m, i, j]
            row += [f"{v.real:.12e}", f"{v.imag:.12e}"]
        lines.append(" ".join(row))
    path = Path(path)
    path.write_text("\n".join(lines) + "\n")
    return path


# ----------------------------------------------------------------- P370

def level(metric: float, table) -> str:
    for floor, name in table:
        if metric > floor:
            return name
    return "poor"


def p370_metrics(s) -> dict:
    """P370 frequency-domain quality metrics (TG3 qualityCheckFrequencyDomain.m),
    with the per-point arrays they are built from."""
    s = np.asarray(s, dtype=complex)
    nf, p, _ = s.shape
    pm = np.linalg.norm(s, ord=2, axis=(1, 2))
    pw = np.where(pm > P370_A, (pm - P370_A) / P370_B, 0.0)
    if p > 1:
        rm = np.abs(s - s.transpose(0, 2, 1)).sum(axis=(1, 2)) / (p * (p - 1))
    else:
        rm = np.zeros(nf)
    rw = np.where(rm > P370_C, (rm - P370_C) / P370_B, 0.0)
    pqm = max(nf - pw.sum(), 0.0) / nf * 100
    rqm = max(nf - rw.sum(), 0.0) / nf * 100
    cqm = np.full((p, p), 100.0)
    turns = np.zeros((p, p, max(nf - 2, 0)))
    for i in range(p):
        for j in range(p):
            x = s[:, i, j]
            if len(np.unique(x)) == 1 or nf < 3:
                continue
            v = np.diff(x)
            r = v[1:].real * v[:-1].imag - v[1:].imag * v[:-1].real
            turns[i, j] = r
            tot = np.abs(r).sum()
            cqm[i, j] = max(r[r > 0].sum() / tot, 0.0) * 100 if tot > 0 else 100.0
    c = float(cqm.min())
    return {"passivity": pqm, "reciprocity": rqm if p > 1 else 100.0, "causality": c,
            "levels": {"passivity": level(pqm, LEVELS_PR),
                       "reciprocity": level(rqm, LEVELS_PR) if p > 1 else "good",
                       "causality": level(c, LEVELS_C)},
            "causality_ij": cqm, "pm": pm, "rm": rm, "turns": turns}


# ---------------------------------------------------------------- check

def _runs(idx) -> list[list[int]]:
    out: list[list[int]] = []
    for k in idx:
        if out and k == out[-1][-1] + 1:
            out[-1].append(int(k))
        else:
            out.append([int(k)])
    return out


def _sanity(freqs, s) -> list[dict]:
    w: list[dict] = []
    nf, p, _ = s.shape
    if nf < 3:
        return [{"kind": "too_few_points", "points": nf}]
    df = np.diff(freqs)
    step = float(np.median(df))
    dev = float(np.max(np.abs(df - step)) / step)
    if dev > 1e-3:
        w.append({"kind": "nonuniform_grid", "max_step_dev": round(dev, 4),
                  "detail": "an IFFT to the time domain wants a uniform grid; resample first"})
    f0 = float(freqs[0])
    if f0 == 0.0:
        im = float(np.max(np.abs(s[0].imag)))
        if im > 1e-6:
            w.append({"kind": "dc_not_real", "max_imag": im,
                      "detail": "S at DC of a real network is real"})
    else:
        w.append({"kind": "no_dc_point", "f_start": f0,
                  "detail": "a transient sim extrapolates to DC; measure or simulate down to it"})
        if f0 > 1.5 * step:
            w.append({"kind": "band_start_far", "f_start": f0, "step": step,
                      "detail": "band starts more than 1.5 steps above DC: the DC extrapolation is a guess"})
    for i in range(p):
        for j in range(p):
            if i == j:
                continue
            x = s[:, i, j]
            strong = np.abs(x) > 0.5
            if strong[:3].all():
                ph = np.unwrap(np.angle(x))
                dph = np.abs(np.diff(ph))
                if float(np.max(dph)) > math.pi / 2:
                    k = int(np.argmax(dph))
                    w.append({"kind": "step_too_coarse", "term": f"S{i + 1}{j + 1}",
                              "f": float(freqs[k]), "deg_per_step": round(math.degrees(dph[k]), 1),
                              "detail": "phase moves more than 90 degrees per step: the delay aliases"})
                    continue
                n = min(5, nf)
                a, b = np.polyfit(freqs[:n], ph[:n], 1)
                off = float(b - 2 * math.pi * round(b / (2 * math.pi)))
                if abs(off) > 0.2:
                    w.append({"kind": "dc_phase_offset", "term": f"S{i + 1}{j + 1}",
                              "rad": round(off, 3),
                              "detail": "the transmission phase does not extrapolate to 0 at DC"})
    return w


def check(freqs, s, tols: dict | None = None, terms=None) -> dict:
    """P370 metrics + every failing point + band sanity. terms: the (i, j)
    entries that hold data (a solve that excited only some ports); with a
    partial set, passivity is judged column by column on the full columns
    and reciprocity only on pairs that both hold data."""
    t = {**DEFAULT_TOLS, **(tols or {})}
    freqs = np.asarray(freqs, dtype=float)
    s = np.asarray(s, dtype=complex)
    nf, p, _ = s.shape
    full = terms is None or len(set(terms)) == p * p
    have = {(i, j) for i in range(p) for j in range(p)} if full else set(terms)
    m = p370_metrics(s)
    errors: list[dict] = []

    def pts(idx):
        return [float(freqs[k]) for k in idx]

    # passivity
    if full:
        pm = m["pm"]
    else:
        cols = [j for j in range(p) if all((i, j) in have for i in range(p))]
        pm = (np.max(np.linalg.norm(s[:, :, cols], axis=1), axis=1) if cols
              else np.zeros(nf))
    bad = np.nonzero(pm > 1 + t["passivity_tol"])[0]
    if len(bad):
        k = int(bad[np.argmax(pm[bad])])
        errors.append({"kind": "passive_violation", "points": len(bad), "freqs": pts(bad),
                       "worst": {"f": float(freqs[k]), "sigma_max": float(pm[k])}})
    # reciprocity
    pairs = [(i, j) for i in range(p) for j in range(i + 1, p) if (i, j) in have and (j, i) in have]
    if pairs:
        rm = np.max([np.abs(s[:, i, j] - s[:, j, i]) for i, j in pairs], axis=0)
        bad = np.nonzero(rm > t["reciprocity_tol"])[0]
        if len(bad):
            k = int(bad[np.argmax(rm[bad])])
            errors.append({"kind": "nonreciprocal", "points": len(bad), "freqs": pts(bad),
                           "worst": {"f": float(freqs[k]), "abs_sij_minus_sji": float(rm[k])}})
    # causality: counter-clockwise turns far above the turns around them
    from scipy.ndimage import median_filter
    ncs = []
    for i, j in sorted(have):
        r = m["turns"][i, j]
        if not r.size or not np.any(r):
            continue
        local = median_filter(np.abs(r), size=CAUSALITY_WINDOW, mode="nearest")
        idx = np.nonzero((r < 0) & (-r > t["causality_tol"] * local))[0] + 1  # middle point
        if len(idx):
            ncs.append({"term": f"S{i + 1}{j + 1}", "points": len(idx), "freqs": pts(idx),
                        "cqm": round(float(m["causality_ij"][i, j]), 3)})
    if ncs:
        errors.append({"kind": "noncausal_turn", "points": sum(c["points"] for c in ncs),
                       "terms": ncs})
    # the averages, with P370's own floors
    names = ["passivity", "causality"] + (["reciprocity"] if full and p > 1 else [])
    for name in names:
        if m["levels"][name] in ("inconclusive", "poor"):
            errors.append({"kind": f"p370_{name}_{m['levels'][name]}", "metric": round(m[name], 3)})
    return {"points": nf, "nports": p, "f_min": float(freqs[0]), "f_max": float(freqs[-1]),
            "terms": "all" if full else sorted(f"S{i + 1}{j + 1}" for i, j in have),
            "p370": {k: round(m[k], 4) for k in ("passivity", "reciprocity", "causality")},
            "levels": m["levels"],
            "sigma_max": float(np.max(pm)) if nf else None,
            "tolerances": t, "errors": errors, "warnings": _sanity(freqs, s),
            "ok": not errors}


def check_file(path, tols: dict | None = None, terms=None) -> dict:
    d = read_touchstone(path)
    res = check(d["freqs"], d["s"], tols, terms)
    res["file"] = str(path)
    res["z0"] = d["z0"]
    return res


def summary(res: dict) -> str:
    p = res["p370"]
    lv = res["levels"]
    head = (f"{Path(res.get('file', '')).name or 'S'}: {res['nports']}-port, {res['points']} points "
            f"{res['f_min']:.4g}-{res['f_max']:.4g} Hz; P370 passivity {p['passivity']:.2f}% "
            f"({lv['passivity']}), reciprocity {p['reciprocity']:.2f}% ({lv['reciprocity']}), "
            f"causality {p['causality']:.2f}% ({lv['causality']})")
    out = [head]
    for e in res["errors"]:
        fs = e.get("freqs") or [f for c in e.get("terms", []) for f in c["freqs"]]
        if "metric" in e:
            out.append(f"  FAIL {e['kind']}: {e['metric']:.2f}%")
            continue
        shown = ", ".join(f"{f:.6g}" for f in fs[:8]) + (" ..." if len(fs) > 8 else "")
        out.append(f"  FAIL {e['kind']}: {e['points']} point(s) at {shown} Hz")
    for w in res["warnings"]:
        out.append(f"  warn {w['kind']}: {w.get('detail', '')}")
    out.append("ok" if res["ok"] else "FAILED")
    return "\n".join(out)


# ------------------------------------------------------------------ fit

def skrf():
    """scikit-rf from the IIC-OSIC-TOOLS tree (pure Python; numpy/scipy come
    from hwde's venv, the tree's path is appended after it)."""
    try:
        import skrf as rf  # noqa: F401
        return rf
    except ImportError:
        pass
    from lib import solvers
    root = solvers.toolchain_root()
    if root is None:
        raise SparamError("sparam fit needs scikit-rf: no IIC-OSIC-TOOLS tree (set HWDE_EDA_TOOLCHAIN)")
    for d in sorted(glob.glob(str(root / "usr/local/lib/python3*/dist-packages"))):
        if Path(d, "skrf").is_dir() and d not in sys.path:
            sys.path.append(d)
    try:
        import skrf as rf
    except ImportError as e:
        raise SparamError(f"scikit-rf not importable from {root}: {e}") from e
    return rf


def _network(rf, freqs, s, z0):
    return rf.Network(frequency=rf.Frequency.from_f(np.asarray(freqs), unit="hz"),
                      s=np.asarray(s), z0=z0)


def prune_far_poles(vf, f_max: float, factor: float = 100.0) -> int:
    """Fold real poles far above the band (|p| > factor * 2 pi f_max) into
    the constant term: r/(s - p) = -r/p to within |s/p|. Vector fitting
    leaves such poles (|Re| 1e14..1e30 rad/s on a 6 GHz fit) where the
    data wants a constant; written as SPICE they become sub-pOhm
    resistors that ngspice clamps, and the model breaks. Returns how many
    were folded."""
    w = 2 * math.pi * f_max * factor
    far = (vf.poles.imag == 0) & (np.abs(vf.poles) > w)
    if not far.any():
        return 0
    for k in np.nonzero(far)[0]:
        vf.constant_coeff = vf.constant_coeff - (vf.residues[:, k] / vf.poles[k]).real
    vf.poles = vf.poles[~far]
    vf.residues = vf.residues[:, ~far]
    return int(far.sum())


PASSIVE_MODEL_TOL = 1e-6


def model_sigma_max(vf, f_max: float, points: int = 4000) -> float:
    """Largest singular value of the fitted S over 0..10 f_max and at
    infinity (the constant term). scikit-rf's is_passive() has no
    tolerance: a lowpass ladder's S(inf) = diag(1, -1) sits exactly on the
    unit circle, so a 1e-13 fit error reads as a violation and its
    enforcement then wrecks a 1e-15 fit."""
    fs = np.linspace(0, 10 * f_max, points)
    s = model_s(vf, fs)
    p = vf.network.nports
    d = np.asarray(vf.constant_coeff, dtype=float).reshape(p, p)
    return float(max(np.linalg.norm(s, ord=2, axis=(1, 2)).max(), np.linalg.norm(d, ord=2)))


SEARCH_POINTS = 400
SEARCH_ORDERS = [(nr, nc) for nc in (1, 2, 3, 4, 5, 6, 8) for nr in (0, 1, 2)] + \
    [(2, nc) for nc in (10, 12, 16, 20, 24, 32, 40, 48, 64)]


def _fit_order(VectorFitting, nw, f_max, target_error, n_real, n_cmplx,
               budget_s: float = 300.0) -> dict:
    """The lowest order in SEARCH_ORDERS whose fit, far poles folded, has
    rms |dS| under target_error, or the orders given. The search fits a
    copy decimated to SEARCH_POINTS points (one fit of 10k points x 2
    ports at 10 poles takes ~100 s here); the chosen order is then fitted
    on every point. It stops after budget_s with the best order so far."""
    import time
    tried = []
    t0 = time.time()

    def one(net, nr, nc):
        vf = VectorFitting(net)
        vf.vector_fit(n_poles_real=nr, n_poles_cmplx=nc)
        folded = prune_far_poles(vf, f_max)
        e = np.abs(model_s(vf, net.f) - net.s)
        return vf, folded, float(np.sqrt(np.mean(e ** 2)))

    if n_real is not None or n_cmplx is not None:
        vf, folded, rms = one(nw, n_real or 0, n_cmplx or 0)
        tried.append({"real": n_real or 0, "cmplx": n_cmplx or 0, "rms": rms})
        return {"vf": vf, "folded": folded, "tried": tried, "met": rms <= target_error}
    n = len(nw.f)
    small = nw if n <= SEARCH_POINTS else nw[np.unique(np.linspace(0, n - 1, SEARCH_POINTS).round().astype(int))]
    best, met = None, False
    for nr, nc in SEARCH_ORDERS:
        if 2 * nc + nr >= len(small.f):
            break
        _, _, rms = one(small, nr, nc)
        tried.append({"real": nr, "cmplx": nc, "rms": rms})
        if best is None or rms < best[2]:
            best = (nr, nc, rms)
        if rms <= target_error:
            met = True
            break
        if time.time() - t0 > budget_s:
            break
    nr, nc, _ = best
    if small is nw:
        vf, folded, _ = one(nw, nr, nc)
    else:
        vf, folded, rms = one(nw, nr, nc)
        tried.append({"real": nr, "cmplx": nc, "rms": rms, "points": n})
    return {"vf": vf, "folded": folded, "tried": tried, "met": met,
            "searched_s": round(time.time() - t0, 1)}


def fit(path, out, n_poles_real: int | None = None, n_poles_cmplx: int | None = None,
        enforce: bool = True, name: str | None = None, target_error: float = 1e-3) -> dict:
    """Vector-fit a Touchstone file, enforce passivity, write a subckt.

    Poles: n_poles_real/n_poles_cmplx when given, else the lowest order
    whose rms error is under target_error (_fit_order; scikit-rf's
    auto_fit is not used: a 1e-13 change in the data moved it from 6 to 8
    poles on the same ladder). Passivity is judged by model_sigma_max with
    a 1e-6 tolerance and enforced (scikit-rf passivity_enforce) past it.
    `ok` = passive and rms within 10x target_error. A DC point is
    dropped before fitting (scikit-rf's fit with one is 1e-2 worse on a
    ladder it fits to 1e-15 without) but kept in the error. Returns
    {rms_error, max_error (|S_fit - S| over every entry and point), poles,
    passive_before, passivity_enforced, passive_after, subckt, pins, out}."""
    rf = skrf()
    d = read_touchstone(path)
    f, s = d["freqs"], d["s"]
    keep = f > 0
    if keep.sum() < 3:
        raise SparamError(f"{path}: fewer than 3 points above DC")
    nw = _network(rf, f[keep], s[keep], d["z0"])
    from skrf.vectorFitting import VectorFitting
    import logging
    import warnings
    logging.getLogger("skrf").setLevel(logging.ERROR)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _fit(VectorFitting, nw, path, out, f, s, d, n_poles_real, n_poles_cmplx,
                    enforce, name, target_error)


def _fit(VectorFitting, nw, path, out, f, s, d, n_poles_real, n_poles_cmplx,
         enforce, name, target_error) -> dict:
    order = _fit_order(VectorFitting, nw, float(f[-1]), target_error,
                       n_poles_real, n_poles_cmplx)
    vf, folded = order["vf"], order["folded"]
    sig_before = model_sigma_max(vf, float(f[-1]))
    passive_before = sig_before <= 1 + PASSIVE_MODEL_TOL
    enforced = False
    if enforce and not passive_before:
        vf.passivity_enforce(f_max=float(f[-1]) * 1.2)
        enforced = True
    sig_after = model_sigma_max(vf, float(f[-1]))
    passive_after = sig_after <= 1 + PASSIVE_MODEL_TOL
    err = np.abs(model_s(vf, f) - s)
    rms = float(np.sqrt(np.mean(err ** 2)))
    name = name or re.sub(r"\W", "_", Path(path).stem) or "sparam"
    if name[0].isdigit():
        name = "s_" + name
    out = Path(out)
    vf.write_spice_subcircuit_s(str(out), fitted_model_name=name)
    txt = out.read_text()
    out.write_text(f"* hwde sparam fit {Path(path).name}: {len(vf.poles)} poles, "
                   f"rms |dS| {rms:.3e}, max {float(np.max(err)):.3e}, passive {passive_after}\n"
                   f"* pins p1..p{d['nports']} (reference: ground 0), z0 {d['z0']:g} ohm\n" + txt)
    return {"file": str(path), "out": str(out), "subckt": name, "nports": d["nports"],
            "z0": d["z0"], "poles": int(len(vf.poles)), "far_poles_folded": folded,
            "rms_error": rms, "max_error": float(np.max(err)),
            "passive_before": bool(passive_before), "passivity_enforced": enforced,
            "passive_after": bool(passive_after), "sigma_max_before": sig_before,
            "sigma_max_after": sig_after, "tried": order["tried"],
            "target_met": order["met"], "target_error": target_error,
            "ok": bool(passive_after and rms <= max(target_error, 0) * 10),
            "pins": [f"p{k + 1}" for k in range(d["nports"])]}


def model_s(vf, freqs) -> np.ndarray:
    """The fitted model's S on freqs, (N, P, P)."""
    p = vf.network.nports
    out = np.zeros((len(freqs), p, p), dtype=complex)
    for i in range(p):
        for j in range(p):
            out[:, i, j] = vf.get_model_response(i, j, np.asarray(freqs))
    return out


# --------------------------------------------------------- SPICE round trip

def ac_deck(subckt_file, subckt: str, nports: int, drive: int, freqs, z0: float = 50.0,
            out: str = "ac.txt") -> str:
    """An ngspice AC deck that measures column `drive` of S of a subckt:
    port `drive` behind a 2 V source and z0 (so a = 1), the others in z0;
    S_i,drive = V_i (i != drive), S_drive,drive = V_drive - 1."""
    f = np.asarray(freqs)
    nodes = " ".join(f"n{k + 1}" for k in range(nports))
    lines = [f"* hwde sparam AC column {drive + 1}", f".include {subckt_file}",
             f"X1 {nodes} {subckt}"]
    for k in range(nports):
        if k == drive:
            lines += [f"Vs src 0 dc 0 ac 2", f"Rs src n{k + 1} {z0}"]
        else:
            lines.append(f"Rt{k + 1} n{k + 1} 0 {z0}")
    step = np.diff(f)
    if len(f) > 2 and np.max(np.abs(step - step[0])) < 1e-6 * step[0]:
        ana = f"ac lin {len(f)} {f[0]:.12g} {f[-1]:.12g}"
    else:
        raise SparamError("ac_deck wants a uniform grid")
    vecs = " ".join(f"v(n{k + 1})" for k in range(nports))
    lines += [".control", "set wr_singlescale", "set wr_vecnames", "option numdgt=12",
              ana,
              f"wrdata {out} {vecs}", ".endc", ".end"]
    return "\n".join(lines) + "\n"


def read_wrdata(path, nports: int) -> tuple[np.ndarray, np.ndarray]:
    """ngspice `wrdata` of complex vectors with wr_singlescale: freq then
    re im per vector."""
    rows = []
    for line in Path(path).read_text().splitlines():
        toks = line.split()
        if not toks:
            continue
        try:
            rows.append([float(x) for x in toks])
        except ValueError:
            continue  # the vecnames header
    a = np.array(rows)
    f = a[:, 0]
    v = a[:, 1::2][:, :nports] + 1j * a[:, 2::2][:, :nports]
    return f, v


def spice_s(cir, subckt: str, nports: int, freqs, z0: float = 50.0,
            timeout: float = 600) -> np.ndarray:
    """S of a SPICE subckt measured by ngspice AC (the tree's ngspice), one
    run per driven port, on a uniform grid freqs (no DC point)."""
    import tempfile
    from lib import solvers
    cir = Path(cir).resolve()
    out = np.zeros((len(freqs), nports, nports), dtype=complex)
    with tempfile.TemporaryDirectory(prefix="hwde-sparam-") as td:
        for k in range(nports):
            deck = Path(td) / f"ac{k}.cir"
            deck.write_text(ac_deck(str(cir), subckt, nports, k, freqs, z0, out=f"ac{k}.txt"))
            r = solvers.run("ngspice", ["-b", deck.name], cwd=td, timeout=timeout)
            res = Path(td) / f"ac{k}.txt"
            if not res.exists():
                raise SparamError(f"ngspice wrote no AC data: {(r.stdout + r.stderr)[-400:]}")
            _, v = read_wrdata(res, nports)
            out[:, :, k] = v
            out[:, k, k] -= 1
    return out


def roundtrip(touchstone, cir, subckt: str) -> dict:
    """The fitted subckt's S by ngspice against the file's: {rms, max}
    over every entry and point above DC."""
    d = read_touchstone(touchstone)
    keep = d["freqs"] > 0
    f = d["freqs"][keep]
    got = spice_s(cir, subckt, d["nports"], f, d["z0"])
    e = np.abs(got - d["s"][keep])
    return {"rms_error": float(np.sqrt(np.mean(e ** 2))), "max_error": float(np.max(e)),
            "points": int(len(f))}
