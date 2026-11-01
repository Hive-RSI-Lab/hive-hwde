#!/usr/bin/env python3
"""sparam.py - is an S-parameter file fit for a time-domain sim, and its
SPICE model (#483).

    hwde sparam check F.sNp [--json] [--out R.json] [--terms S11,S21]
                      [--passivity-tol T] [--reciprocity-tol T]
                      [--causality-tol K]
        IEEE P370 frequency-domain quality metrics (passivity, reciprocity,
        causality, 0-100 %, the standard's own formulas and levels), every
        frequency point that fails (sigma_max(S) > 1 + T, |S_ij - S_ji| > T,
        a counter-clockwise turn K x its neighbours'), and the band's
        fitness for a transient: a DC point or a start near it, a uniform
        grid, a step fine enough for the delay, a transmission phase that
        meets 0 at DC (warnings). --terms: only these entries hold data
        (a solve that excited some ports). Exit 1 on any failing point or
        a P370 level below "acceptable".

    hwde sparam fit F.sNp --out F.cir [--name SUBCKT] [--target-error E]
                    [--poles-real N] [--poles-cmplx N] [--no-enforce]
                    [--verify] [--json]
        vector fit (scikit-rf VectorFitting, from the IIC-OSIC-TOOLS tree),
        lowest order under rms |dS| E (default 1e-3), passivity enforced,
        written as a SPICE subckt `SUBCKT p1 .. pN` referenced to ground 0
        (.include it in a sim_run bench, ngspice or Xyce). --verify runs the
        subckt through ngspice AC and compares its S with the file's.
        Exit 1 when the model is not passive, misses 10 E, or --verify
        disagrees by more than 2x the fit's own max error + 1e-6.

lib/sparam.py has the formulas, sources and tolerances.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# numpy's BLAS would take every core of the shared machine for a fit
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import sparam as sp  # noqa: E402

SCRIPT = "sparam"


def _terms(arg: str | None):
    if not arg:
        return None
    out = []
    for t in arg.split(","):
        t = t.strip().upper().lstrip("S")
        if len(t) != 2 or not t.isdigit():
            raise SystemExit(f"--terms: '{t}' is not Sij (single-digit ports)")
        out.append((int(t[0]) - 1, int(t[1]) - 1))
    return out


def _jsonable(x):
    return json.loads(json.dumps(x, default=float))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hwde sparam", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("file")
    c.add_argument("--json", action="store_true")
    c.add_argument("--out")
    c.add_argument("--terms")
    c.add_argument("--passivity-tol", type=float)
    c.add_argument("--reciprocity-tol", type=float)
    c.add_argument("--causality-tol", type=float)
    f = sub.add_parser("fit")
    f.add_argument("file")
    f.add_argument("--out", required=True)
    f.add_argument("--name")
    f.add_argument("--target-error", type=float, default=1e-3)
    f.add_argument("--poles-real", type=int)
    f.add_argument("--poles-cmplx", type=int)
    f.add_argument("--no-enforce", action="store_true")
    f.add_argument("--verify", action="store_true")
    f.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "check":
            tols = {k: v for k, v in (("passivity_tol", a.passivity_tol),
                                      ("reciprocity_tol", a.reciprocity_tol),
                                      ("causality_tol", a.causality_tol)) if v is not None}
            res = sp.check_file(a.file, tols, _terms(a.terms))
            res = {"script": SCRIPT, "cmd": "check", **_jsonable(res)}
            if a.out:
                Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
            print(json.dumps(res, indent=1) if a.json else sp.summary(res))
            return 0 if res["ok"] else 1
        res = sp.fit(a.file, a.out, a.poles_real, a.poles_cmplx, not a.no_enforce,
                     a.name, a.target_error)
        if a.verify:
            rt = sp.roundtrip(a.file, a.out, res["subckt"])
            rt["ok"] = rt["max_error"] <= 2 * res["max_error"] + 1e-6
            res["spice"] = rt
            res["ok"] = res["ok"] and rt["ok"]
        res = {"script": SCRIPT, "cmd": "fit", **_jsonable(res)}
    except sp.SparamError as e:
        print(f"sparam: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"{res['out']}: .subckt {res['subckt']} {' '.join(res['pins'])}; {res['poles']} poles, "
              f"rms |dS| {res['rms_error']:.3e}, max {res['max_error']:.3e}; "
              f"passive {res['passive_after']} (sigma_max {res['sigma_max_after']:.6f}"
              f"{', enforced' if res['passivity_enforced'] else ''})")
        if "spice" in res:
            print(f"  ngspice AC: rms {res['spice']['rms_error']:.3e}, max {res['spice']['max_error']:.3e}")
        print("ok" if res["ok"] else "FAILED")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
