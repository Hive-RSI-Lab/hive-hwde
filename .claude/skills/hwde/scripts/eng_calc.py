#!/usr/bin/env python
"""eng_calc.py - the reviewer's hand calculations, deterministic and logged.

Each subcommand is one formula from lib/engcalc.py (the same code
sim_board.py uses on a whole board), so a written margin in a review or
design doc cites a command anyone can re-run. Values take schematic
notation (4k7, 2.2u, 100n, 1M). Output is one JSON object.

  divider   --vin 21 --rtop 100k --rbot 10k
  fb        --vref 0.6 --rtop 150k --rbot 33k [--rtol 1%] [--vref-tol 2%]
  rc        --r 10k --c 1u [--fraction 0.7]          corner, tau, t to fraction
  led       --vsupply 3.3 --r 1k [--vf 2.0 | --color red]
  i2c       --vdd 3.3 --rp 2.2k --cb 50p [--khz 400]
  crystal   --c1 18p --c2 18p [--stray 4p]           load capacitance
  ldo       --vin 5 --vout 3.3 --iout 0.3 --theta 180 [--ta 40] [--iq 50u]
  holdup    --c 470u --vstart 20 --vmin 12 --iload 0.5
  rpower    --r 100 --v 3.3 [--footprint R_0603_1608Metric]

Exit 0 with the result; 2 with {"status": "error"} on bad input.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import engcalc  # noqa: E402


def num(text: str) -> float:
    """'4k7', '2.2u', '18p', '1%', '20V', '0.5A', '3.3' -> a float."""
    s = str(text).strip()
    if s.endswith("%"):
        return float(s[:-1]) / 100.0
    for cand in (s, s[:-1] if s[-1:] in "VAWs" else None):
        v = engcalc.parse_value(cand) if cand else None
        if v is not None:
            return v
    raise argparse.ArgumentTypeError(f"not a value: {text!r}")


def calc(a) -> dict:
    c = a.cmd
    if c == "divider":
        return {"vout": engcalc.divider_out(a.vin, a.rtop, a.rbot),
                "i_a": a.vin / (a.rtop + a.rbot)}
    if c == "fb":
        return engcalc.fb_vout(a.vref, a.rtop, a.rbot, a.rtol, a.vref_tol)
    if c == "rc":
        tau = a.r * a.c
        return {"tau_s": tau, "fc_hz": engcalc.rc_corner_hz(a.r, a.c),
                "fraction": a.fraction,
                "t_to_fraction_s": engcalc.rc_cross_s(tau, a.fraction)}
    if c == "led":
        vf = a.vf if a.vf is not None else engcalc.led_vf(a.color)
        i = max(0.0, (a.vsupply - vf) / a.r)
        return {"vf": vf, "vf_assumed": a.vf is None, "i_a": i,
                "p_r_w": i * i * a.r}
    if c == "i2c":
        return engcalc.i2c_pullup(a.vdd, a.rp, a.cb * 1e12, a.khz)
    if c == "crystal":
        return {"cl_pf": engcalc.crystal_load_pf(a.c1 * 1e12, a.c2 * 1e12,
                                                 a.stray * 1e12)}
    if c == "ldo":
        return engcalc.ldo_tj(a.vin, a.vout, a.iout, a.theta, a.ta, a.iq)
    if c == "holdup":
        return {"t_s": engcalc.holdup_s(a.c, a.vstart, a.vmin, a.iload)}
    if c == "rpower":
        p = a.v * a.v / a.r
        rating = engcalc.resistor_rating_w(a.footprint) if a.footprint \
            else None
        return {"p_w": p, "rating_w": rating,
                "load": p / rating if rating else None}
    raise ValueError(c)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, *specs):
        p = sub.add_parser(name)
        for flag, kw in specs:
            p.add_argument(flag, type=kw.pop("type", num), **kw)
        return p

    req = {"required": True}
    cmd("divider", ("--vin", dict(req)), ("--rtop", dict(req)),
        ("--rbot", dict(req)))
    cmd("fb", ("--vref", dict(req)), ("--rtop", dict(req)),
        ("--rbot", dict(req)), ("--rtol", {"default": 0.01}),
        ("--vref-tol", {"default": 0.02}))
    cmd("rc", ("--r", dict(req)), ("--c", dict(req)),
        ("--fraction", {"default": 1 - math.exp(-1)}))
    cmd("led", ("--vsupply", dict(req)), ("--r", dict(req)),
        ("--vf", {"default": None}), ("--color", {"type": str,
                                                  "default": None}))
    cmd("i2c", ("--vdd", dict(req)), ("--rp", dict(req)), ("--cb", dict(req)),
        ("--khz", {"type": int, "default": 400}))
    cmd("crystal", ("--c1", dict(req)), ("--c2", dict(req)),
        ("--stray", {"default": engcalc.CRYSTAL_STRAY_PF * 1e-12}))
    cmd("ldo", ("--vin", dict(req)), ("--vout", dict(req)),
        ("--iout", dict(req)), ("--theta", dict(req)),
        ("--ta", {"default": 25.0}), ("--iq", {"default": 0.0}))
    cmd("holdup", ("--c", dict(req)), ("--vstart", dict(req)),
        ("--vmin", dict(req)), ("--iload", dict(req)))
    cmd("rpower", ("--r", dict(req)), ("--v", dict(req)),
        ("--footprint", {"type": str, "default": None}))
    try:
        a = ap.parse_args(argv)
        res = calc(a)
    except (ValueError, ZeroDivisionError) as exc:
        print(json.dumps({"script": "eng_calc", "status": "error",
                          "error": str(exc)}))
        return 2
    print(json.dumps({"script": "eng_calc", "status": "pass", "calc": a.cmd,
                      "result": res}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
