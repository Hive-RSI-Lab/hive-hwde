#!/usr/bin/env python3
"""sim_solve.py - hwde's open solvers: what is there, whether each one is
right, and the board-facing studies built on them.

    hwde sim_solve probe [--json]
        every solver lib/solvers.py drives (ngspice, Xyce, openEMS, Palace,
        gmsh, FastHenry, FasterCap) and its version, from the Hive's
        IIC-OSIC-TOOLS tree.

    hwde sim_solve validate [--only NAME,...] [--json] [--out FILE] [--strict]
        run each solver on a case with a closed-form answer and compare.
        A solver is trusted for board work only while its case passes (a
        result from a solver whose case fails, or never ran, is a guess).
        A missing solver is reported as missing, not failed, unless
        --strict. Exit 1 when a case that ran is outside its tolerance.

    hwde sim_solve line --w W --h H --er ER [--t T] [--f-max HZ]
                        [--z-target OHM] [--json]
        full-wave (openEMS) characteristic impedance and eps_eff of a
        bare microstrip (mm), beside impedance.py's 2D quasi-static field
        solve of the same cross-section. The two answer the same question
        two ways: a disagreement past 3% means one of them is wrong for
        this geometry. --z-target adds the error against the target.

The cases (docs/solvers.md has the measured errors):

    circuit_ngspice     RC low-pass |H(fc)| = 1/sqrt2 and v(tau) = 1 - 1/e
    circuit_xyce        the same deck in Xyce
    openems_microstrip  openEMS Z0 vs impedance.py's field solver (and
                        Hammerstad-Jensen), 1.5 mm on 0.8 mm of er 4
    palace_cavity       Palace eigenmodes of a 40x30x20 mm PEC box vs
                        f = c/2 sqrt((m/a)^2 + (n/b)^2 + (p/d)^2)
    fasthenry_bar       a 10 x 1 x 0.035 mm copper bar: R = l/(sigma w t),
                        L vs Ruehli's partial self-inductance
    fastercap_cube      a 1 mm cube's capacitance vs 0.66067813 * 4 pi eps0 a
    thermal_fin         thermalfv's 1D fin vs 1/cosh(mL)
    thermal_point       thermalfv's point source on a plate vs P/(2 pi kt) K0(mr)
    electrothermal      the coupled strip (copper's R rising with T under
                        held voltage) vs its closed-form quadratic

Solves go through `hive heavy` (bin/hwde lists sim_solve as heavy), so a
full-wave run never lands beside another heavy job on the machine.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import emsolve as em  # noqa: E402
from lib import solvers  # noqa: E402

SCRIPT = "sim_solve"


def _case(name, solver, quantity, measured, expected, tol_pct, reference, **extra):
    err = (measured - expected) / expected * 100 if expected else math.inf
    return {"name": name, "solver": solver, "quantity": quantity,
            "measured": measured, "expected": expected,
            "err_pct": round(err, 4), "tol_pct": tol_pct,
            "status": "pass" if abs(err) <= tol_pct else "fail",
            "reference": reference, **extra}


def _worst(name, solver, quantity, pairs, tol_pct, reference, **extra):
    """One case from several (measured, expected) pairs: the worst error rules."""
    errs = [(m - e) / e * 100 for m, e in pairs]
    k = max(range(len(errs)), key=lambda i: abs(errs[i]))
    return _case(name, solver, quantity, pairs[k][0], pairs[k][1], tol_pct,
                 reference, all=[{"measured": m, "expected": e} for m, e in pairs],
                 **extra)


# ---------------------------------------------------------------- circuit

RC_R, RC_C = 1e3, 159.1549e-9          # fc = 1/(2 pi R C) = 1.0000 kHz
RC_TAU = RC_R * RC_C


def _rc_body() -> str:
    return (f"V1 in 0 DC 0 AC 1 PULSE(0 1 0 1n 1n 1 2)\n"
            f"R1 in out {RC_R}\nC1 out 0 {RC_C}\n")


def _interp(xs, ys, x):
    for i in range(1, len(xs)):
        if xs[i] >= x:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + f * (ys[i] - ys[i - 1])
    raise ValueError(f"{x} past the data")


def _rc_expected():
    return 1 / math.sqrt(2), 1 - math.exp(-1)


def case_circuit_ngspice(work: Path) -> dict:
    deck = ("* hwde rc\n" + _rc_body() +
            ".control\ntran 1u 2m\nwrdata tran.txt v(out)\n"
            "ac lin 3 500 1500\nwrdata ac.txt vm(out)\n.endc\n.end\n")
    (work / "rc.cir").write_text(deck)
    r = solvers.run("ngspice", ["-b", "rc.cir"], cwd=work, timeout=1200)
    if not (work / "ac.txt").exists():
        raise solvers.SolverError(f"ngspice wrote nothing: {r.stderr[-300:]}")

    def cols(p):
        rows = [list(map(float, ln.split())) for ln in p.read_text().splitlines() if ln.strip()]
        return [r_[0] for r_ in rows], [r_[1] for r_ in rows]
    fa, ha = cols(work / "ac.txt")
    tt, vt = cols(work / "tran.txt")
    h_fc = _interp(fa, ha, 1000.0)
    v_tau = _interp(tt, vt, RC_TAU)
    e_h, e_v = _rc_expected()
    return _worst("circuit_ngspice", "ngspice", "|H(fc)|, v(tau)",
                  [(h_fc, e_h), (v_tau, e_v)], 0.5,
                  "RC low-pass: |H(1/(2 pi RC))| = 1/sqrt(2), step v(RC) = 1 - 1/e")


def _xyce_csv(p: Path):
    lines = p.read_text().splitlines()
    rows = [list(map(float, ln.split(","))) for ln in lines[1:] if ln.strip()]
    return [r_[0] for r_ in rows], [r_[1] for r_ in rows]


def case_circuit_xyce(work: Path) -> dict:
    (work / "tran.cir").write_text("* hwde rc tran\n" + _rc_body() +
                                   ".tran 1u 2m\n.print tran format=csv file=tran.csv v(out)\n.end\n")
    (work / "ac.cir").write_text("* hwde rc ac\n" + _rc_body() +
                                 ".ac lin 3 500 1500\n.print ac format=csv file=ac.csv vm(out)\n.end\n")
    for deck in ("tran.cir", "ac.cir"):
        r = solvers.run("xyce", [deck], cwd=work, timeout=1200)
        if not r.ok:
            raise solvers.SolverError(f"Xyce {deck}: {(r.stdout + r.stderr)[-300:]}")
    fa, ha = _xyce_csv(work / "ac.csv")
    tt, vt = _xyce_csv(work / "tran.csv")
    e_h, e_v = _rc_expected()
    return _worst("circuit_xyce", "xyce", "|H(fc)|, v(tau)",
                  [(_interp(fa, ha, 1000.0), e_h), (_interp(tt, vt, RC_TAU), e_v)], 0.5,
                  "RC low-pass: |H(1/(2 pi RC))| = 1/sqrt(2), step v(RC) = 1 - 1/e")


# ---------------------------------------------------------------- full-wave

def openems_line(work: Path, w, h, er, t=0.0, f_max=4e9, freqs=None) -> dict:
    csx, where = em.microstrip_line(w, h, er, t=t, f_max=f_max)
    (work / "line.xml").write_text(csx.xml())
    t0 = time.time()
    r = solvers.run("openems", ["line.xml"], cwd=work, timeout=3600)
    if not (work / "u2").exists():
        raise solvers.SolverError(f"openEMS wrote no probes: {(r.stdout + r.stderr)[-400:]}")
    # the band's upper half carries the pulse's energy; below f_max/4 the
    # probes' DFT is noise-limited (eps_eff wanders)
    freqs = freqs or [f_max * k / 8 for k in (2, 3, 4)]
    rows = em.microstrip_z0_from_probes(work, where, freqs)
    return {"rows": rows, "cells": where["cells"], "seconds": round(time.time() - t0, 1),
            "z0": sum(r_["z0"] for r_ in rows) / len(rows),
            "eps_eff": sum(r_["eps_eff"] for r_ in rows) / len(rows)}


def _quasi_static(w, h, er, t):
    from lib import impedance as imp
    return imp.field_microstrip(w, h, max(t, 1e-4), er, mask=None)


def _hammerstad(w, h, er):
    """Hammerstad-Jensen zero-thickness microstrip (accurate to ~0.2%)."""
    u = w / h
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 \
        + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    ee = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    z_air = 60 * math.log(f / u + math.sqrt(1 + (2 / u) ** 2))
    return z_air / math.sqrt(ee), ee


MS_W, MS_H, MS_ER = 1.5, 0.8, 4.0


def case_openems_microstrip(work: Path) -> dict:
    got = openems_line(work, MS_W, MS_H, MS_ER)
    qs = _quasi_static(MS_W, MS_H, MS_ER, 0.0)
    hj_z, hj_e = _hammerstad(MS_W, MS_H, MS_ER)
    c = _worst("openems_microstrip", "openems", "Z0 (ohm), eps_eff",
               [(got["z0"], hj_z), (got["eps_eff"], hj_e)], 2.0,
               f"Hammerstad-Jensen, {MS_W} mm on {MS_H} mm of er {MS_ER}, 1-2 GHz",
               quasi_static={"z0": qs["z0"], "eps_eff": qs["eps_eff"]},
               rows=got["rows"], cells=got["cells"], seconds=got["seconds"])
    return c


CAV = (40.0, 30.0, 20.0)


def case_palace_cavity(work: Path) -> dict:
    a, b, d = CAV
    ref = em.cavity_modes(a, b, d, 4)
    (work / "box.geo").write_text(em.box_mesh_geo(a, b, d, size=6.0))
    r = solvers.run("gmsh", ["box.geo", "-3", "-o", "box.msh"], cwd=work, timeout=1200)
    if not (work / "box.msh").exists():
        raise solvers.SolverError(f"gmsh: {(r.stdout + r.stderr)[-300:]}")
    (work / "cavity.json").write_text(json.dumps(
        em.palace_eigen_config("box.msh", len(ref), ref[0] * 0.9), indent=1))
    t0 = time.time()
    r = solvers.run("palace", ["cavity.json"], cwd=work, timeout=1800)
    if not (work / "postpro" / "eig.csv").exists():
        raise solvers.SolverError(f"Palace: {(r.stdout + r.stderr)[-400:]}")
    got = em.palace_eigs(work / "postpro")[:len(ref)]
    return _worst("palace_cavity", "palace", "eigenfrequency (GHz)",
                  list(zip(got, ref)), 0.5,
                  f"closed PEC box {a}x{b}x{d} mm: TM110, TE101, TE/TM111 ...",
                  seconds=round(time.time() - t0, 1))


# ---------------------------------------------------------------- extraction

BAR = (10.0, 1.0, 0.035)


def case_fasthenry_bar(work: Path) -> dict:
    l, w, t = BAR
    (work / "bar.inp").write_text(em.fasthenry_bar(l, w, t, f=1e3, nw=3))
    r = solvers.run("fasthenry", ["bar.inp"], cwd=work, timeout=1200)
    if not (work / "Zc.mat").exists():
        raise solvers.SolverError(f"FastHenry: {(r.stdout + r.stderr)[-300:]}")
    f, z = em.read_zc(work / "Zc.mat")
    L = z.imag / (2 * math.pi * f)
    R_dc = l * 1e-3 / (5.8e7 * w * 1e-3 * t * 1e-3)
    return _worst("fasthenry_bar", "fasthenry", "R (ohm), L (H)",
                  [(z.real, R_dc), (L, em.bar_partial_inductance(l, w, t))], 2.0,
                  f"{l}x{w}x{t} mm Cu bar at 1 kHz: R = l/(sigma w t); "
                  "L = mu0 l/2pi (ln(2l/(w+t)) + 1/2 + 0.2235 (w+t)/l)")


def case_fastercap_cube(work: Path) -> dict:
    a = 1.0
    (work / "cube.txt").write_text(em.fastercap_cube(a, 4))
    r = solvers.run("fastercap", ["-b", "cube.txt", "-a0.001"], cwd=work, timeout=1200)
    got = em.read_fastercap(r.stdout)
    ref = em.CUBE_C * 4 * math.pi * em.EPS0 * a * 1e-3
    return _case("fastercap_cube", "fastercap", "C (F)", got, ref, 1.5,
                 "isolated cube: C = 0.66067813 * 4 pi eps0 a (Hwang & Mascagni 2004)")


# ---------------------------------------------------------------- thermal

def case_thermal_fin(work: Path) -> dict:
    import numpy as np
    from lib import thermalfv as th
    dx, L, h = 0.25e-3, 50e-3, 20.0
    nx = int(round(L / dx)) + 1
    ks = th.K_CU * 35e-6 + th.K_FR4 * 1.6e-3
    fixed = np.full((1, nx), np.nan)
    fixed[0, 0] = 10.0
    T = th.solve_plane(np.full((1, nx), ks), h, np.zeros((1, nx)), dx, fixed=fixed)
    # the base cell's centre is x=0 and the last cell ends half a cell past
    # its centre, so the fin is (nx - 1/2) dx long; the tip cell sits dx/2 in
    exact = math.cosh(math.sqrt(h / ks) * dx / 2) / math.cosh(math.sqrt(h / ks) * (nx - 0.5) * dx)
    return _case("thermal_fin", "thermalfv", "theta(tip)/theta(base)",
                 float(T[0, -1] / 10.0), exact, 0.5,
                 "1D fin, adiabatic tip: theta(x)/theta0 = cosh(m(L-x))/cosh(mL)")


def case_thermal_point(work: Path) -> dict:
    import numpy as np
    from lib import thermalfv as th
    dx, n, P, h = 0.5e-3, 401, 1.0, 20.0
    ks = th.K_CU * 35e-6 + th.K_FR4 * 1.6e-3
    q = np.zeros((n, n))
    c = n // 2
    q[c, c] = P
    T = th.solve_plane(np.full((n, n), ks), h, q, dx)
    pairs = [(float(T[c, c + k]), th.point_source_theta(P, k * dx, ks, h)) for k in (6, 10, 20)]
    return _worst("thermal_point", "thermalfv", "theta at r = 3, 5, 10 mm (K)",
                  pairs, 1.0, "1 W on a 1 oz + 1.6 mm FR4 plate, h 20: P/(2 pi kt) K0(mr)")


def case_electrothermal(work: Path) -> dict:
    import numpy as np
    from lib import thermalfv as th
    dx, W, Ls, h = 0.2e-3, 2e-3, 40e-3, 15.0
    ny, nx = int(round(W / dx)), int(round(Ls / dx))
    cu = np.ones((ny, nx), bool)
    R0 = th.RHO_CU * (nx - 1) * dx / (35e-6 * W)
    V = 5.0 * R0                     # 5 A cold
    v = np.full((ny, nx), np.nan)
    v[:, 0], v[:, -1] = V, 0.0
    ks = th.K_CU * 35e-6 + th.K_FR4 * 1.6e-3
    r = th.joule(cu, 35e-6, v, np.full((ny, nx), ks), h, dx, t_amb=20.0, t0=20.0)
    rise = float(r["T"].mean() - 20.0)
    exact = th.strip_selfheat_v(V, R0, h * W * nx * dx)
    return _case("electrothermal", "thermalfv", "mean rise (K)", rise, exact, 1.0,
                 "2x40 mm 1 oz strip held at 5 A-cold volts, h 15: "
                 "hA theta = V^2/(R0(1 + alpha theta))",
                 iterations=r["iterations"], current_a=r["current"])


CASES = {
    "circuit_ngspice": ("ngspice", case_circuit_ngspice),
    "circuit_xyce": ("xyce", case_circuit_xyce),
    "openems_microstrip": ("openems", case_openems_microstrip),
    "palace_cavity": ("palace", case_palace_cavity),
    "fasthenry_bar": ("fasthenry", case_fasthenry_bar),
    "fastercap_cube": ("fastercap", case_fastercap_cube),
    "thermal_fin": (None, case_thermal_fin),
    "thermal_point": (None, case_thermal_point),
    "electrothermal": (None, case_electrothermal),
}
NEEDS = {"palace_cavity": ("gmsh",)}


def validate(only=None, strict=False) -> dict:
    root = solvers.toolchain_root()
    out = []
    for name, (tool, fn) in CASES.items():
        if only and name not in only:
            continue
        missing = [t for t in (tool, *NEEDS.get(name, ())) if t and solvers.tool_path(t, root) is None]
        if missing:
            out.append({"name": name, "solver": tool, "status": "missing",
                        "detail": f"{', '.join(missing)} not found (no IIC-OSIC-TOOLS tree, "
                                  "or set HWDE_EDA_TOOLCHAIN)"})
            continue
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix=f"hwde-{name}-") as d:
            try:
                c = fn(Path(d))
            except (solvers.SolverError, ValueError, OSError) as e:
                c = {"name": name, "solver": tool, "status": "error", "detail": str(e)}
        c.setdefault("seconds", round(time.time() - t0, 1))
        out.append(c)
    bad = [c for c in out if c["status"] in ("fail", "error") or (strict and c["status"] == "missing")]
    return {"script": SCRIPT, "toolchain": str(root) if root else None,
            "cases": out, "ok": not bad}


def _print_validate(res: dict) -> None:
    print(f"toolchain: {res['toolchain'] or '(none)'}")
    for c in res["cases"]:
        if c["status"] in ("pass", "fail"):
            print(f"  {c['status']:5} {c['name']:20} {c['quantity']}: {c['measured']:.6g} vs "
                  f"{c['expected']:.6g} ({c['err_pct']:+.3f}%, tol {c['tol_pct']}%) "
                  f"[{c['seconds']} s]")
        else:
            print(f"  {c['status']:5} {c['name']:20} {c.get('detail', '')}")
    print("ok" if res["ok"] else "FAILED")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hwde sim_solve", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--json", action="store_true")
    v = sub.add_parser("validate")
    v.add_argument("--only", default="")
    v.add_argument("--json", action="store_true")
    v.add_argument("--out")
    v.add_argument("--strict", action="store_true")
    ln = sub.add_parser("line")
    ln.add_argument("--w", type=float, required=True)
    ln.add_argument("--h", type=float, required=True)
    ln.add_argument("--er", type=float, required=True)
    ln.add_argument("--t", type=float, default=0.0)
    ln.add_argument("--f-max", type=float, default=4e9)
    ln.add_argument("--z-target", type=float)
    ln.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "probe":
        res = solvers.probe()
        if args.json:
            print(json.dumps(res, indent=1))
        else:
            print(f"toolchain: {res['toolchain'] or '(none: set HWDE_EDA_TOOLCHAIN)'}")
            for k, t in res["tools"].items():
                print(f"  {k:10} {t['version'] or 'missing':28} {t['kind']}")
        return 0 if res["toolchain"] else 1

    if args.cmd == "validate":
        only = {s.strip() for s in args.only.split(",") if s.strip()} or None
        unknown = (only or set()) - set(CASES)
        if unknown:
            ap.error(f"unknown case(s): {', '.join(sorted(unknown))}; have {', '.join(CASES)}")
        res = validate(only, args.strict)
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1) + "\n")
        if args.json:
            print(json.dumps(res, indent=1))
        else:
            _print_validate(res)
        return 0 if res["ok"] else 1

    # line
    with tempfile.TemporaryDirectory(prefix="hwde-line-") as d:
        got = openems_line(Path(d), args.w, args.h, args.er, args.t, args.f_max)
    qs = _quasi_static(args.w, args.h, args.er, args.t)
    diff = (got["z0"] - qs["z0"]) / qs["z0"] * 100
    res = {"script": SCRIPT, "geometry": {"w": args.w, "h": args.h, "er": args.er, "t": args.t},
           "full_wave": got, "quasi_static": qs, "z0_diff_pct": round(diff, 3),
           "agree": abs(diff) <= 3.0}
    if args.z_target:
        res["z0_target"] = args.z_target
        res["z0_err_pct"] = round((got["z0"] - args.z_target) / args.z_target * 100, 3)
    if args.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"openEMS: Z0 {got['z0']:.2f} ohm, eps_eff {got['eps_eff']:.3f} "
              f"({got['cells']} cells, {got['seconds']} s)")
        for r_ in got["rows"]:
            print(f"   {r_['f'] / 1e9:5.2f} GHz  Z0 {r_['z0']:.2f}  eps_eff {r_['eps_eff']:.3f}")
        print(f"quasi-static: Z0 {qs['z0']:.2f} ohm, eps_eff {qs['eps_eff']:.3f}; "
              f"full-wave is {diff:+.2f}% ({'agree' if res['agree'] else 'DISAGREE'})")
        if args.z_target:
            print(f"target {args.z_target} ohm: {res['z0_err_pct']:+.2f}%")
    return 0 if res["agree"] else 1


if __name__ == "__main__":
    sys.exit(main())
