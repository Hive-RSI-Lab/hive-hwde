#!/usr/bin/env python
"""analog_calc.py - analog and audio hand calculations, checked against ngspice.

Each subcommand is one function of lib/analogcalc.py; `hwde analog_verify`
re-proves them against KiCad's ngspice, and reference/ingest/analog-audio.md
says which rule of thumb each one replaces and whether that rule held up.
Values take schematic notation (4k7, 2.2u, 100n, 1M, 10nV, 1%).

  noise     --en 4.5n --in 0.4p --rs 10k [--rf 10k --rg 1k] [--bw 20k]
            [--fc-1f 10] [--t 25]          input-referred noise, which term rules
  level     --dbu 4 | --dbv -10 | --vrms 1.228   line level conversions
  bridge    --zout 100 --zin 10k               level lost into a load
  damping   --zout 10 --zmin 32 --zmax 100     response tilt from source Z (the "1/8 rule")
  hpf       --c 10u --rload 10k [--rsrc 600] [--f 20] [--stages 2]
            | --fc 2 --rload 10k               coupling-cap corner, or the C for one
  bias      --ib 10n --c 1u                    drift of an input with no DC path
  fpbw      --slew 9V/us --vpk 10 | --f 20k --vpk 10   full-power bandwidth / slew needed
  riso      --gbw 10M --ro 50 --cl 1n [--ng 1] [--riso 20] [--fp2 30M] [--pm 60]
            op-amp phase margin into a cap load, and the Riso that gives --pm
  lc        --l 10u --c 680n --rload 8 [--cdiff 0] [--se] [--fsw 400k] [--dcr 0]
            | --rload 8 --f0 40k [--q 0.707]  class-D output filter, or L and C for one
  classd    --pvdd 24 --rload 8 [--rds 0.12] [--dcr 0.05] [--se]   output power
  sar       --r 20 --cfilt 1n --csamp 40p --bits 16 --tacq 500n [--vfs 4.096]
            charge-bucket RC: kickback, settling to 1/2 LSB in the acquisition time

Exit 0 with the result; 2 with {"status": "error"} on bad input.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import analogcalc as ac  # noqa: E402
from eng_calc import num as _num  # noqa: E402


def num(text: str) -> float:
    """eng_calc's values, plus a per-microsecond slew ('9V/us')."""
    s = str(text).strip()
    if s.startswith("-"):
        return -num(s[1:])
    for unit, mult in (("V/us", 1e6), ("V/µs", 1e6), ("V/ns", 1e9),
                       ("V/s", 1.0)):
        if s.endswith(unit):
            return float(_num(s[:-len(unit)])) * mult
    for unit in ("nV", "pA", "Hz", "dB"):
        if s.endswith(unit) and len(s) > len(unit):
            s = s[:-len(unit)] + {"nV": "n", "pA": "p", "Hz": "",
                                  "dB": ""}[unit]
    return _num(s)


def calc(a) -> dict:
    c = a.cmd
    if c == "noise":
        return ac.amp_noise(a.en, getattr(a, "in"), a.rs, a.rf, a.rg, a.bw,
                            a.t, a.fc_1f)
    if c == "level":
        given = [x for x in (a.dbu, a.dbv, a.vrms) if x is not None]
        if len(given) != 1:
            raise ValueError("give one of --dbu, --dbv, --vrms")
        v = (ac.dbu_to_vrms(a.dbu) if a.dbu is not None else
             ac.dbv_to_vrms(a.dbv) if a.dbv is not None else a.vrms)
        return {"vrms": v, "vpk": v * math.sqrt(2),
                "vpp": 2 * v * math.sqrt(2), "dbu": ac.vrms_to_dbu(v),
                "dbv": 20 * math.log10(v)}
    if c == "bridge":
        return {"loss_db": ac.bridging_loss_db(a.zout, a.zin),
                "ratio": a.zin / a.zout}
    if c == "damping":
        dev = ac.damping_deviation_db(a.zout, a.zmin, a.zmax)
        return {"deviation_db": dev, "zout_over_zmin": a.zout / a.zmin,
                "within_1db": abs(dev) <= 1.0,
                "zout_max_for_1db": _zout_for(1.0, a.zmin, a.zmax)}
    if c == "hpf":
        if a.fc is not None:
            return {"c_f": ac.hpf_c_for(a.fc, a.rload, a.rsrc)}
        if a.c is None:
            raise ValueError("give --c (corner from C) or --fc (C for it)")
        fc = ac.hpf_corner(a.c, a.rload, a.rsrc)
        return {"fc_hz": fc, "at_f_db": ac.hpf_loss_db(a.f, fc, a.stages),
                "f_hz": a.f, "stages": a.stages}
    if c == "bias":
        r = ac.bias_drift_v_per_s(a.ib, a.c)
        return {"v_per_s": r, "s_to_1v": 1.0 / r}
    if c == "fpbw":
        if a.slew is not None:
            return {"fpbw_hz": ac.full_power_bw(a.slew, a.vpk)}
        return {"slew_v_per_us": ac.slew_needed(a.f, a.vpk) / 1e6}
    if c == "riso":
        fp2 = a.fp2 if a.fp2 is not None else math.inf
        res = ac.cap_load_pm(a.gbw, a.ro, a.cl, a.ng, a.riso, fp2)
        res["riso_for_pm"] = ac.riso_for_pm(a.gbw, a.ro, a.cl, a.ng, a.pm,
                                            fp2)
        res["pm_target_deg"] = a.pm
        return res
    if c == "lc":
        if a.f0 is not None:
            return ac.classd_lc_for(a.rload, a.f0, a.q, not a.se)
        if a.l is None or a.c is None:
            raise ValueError("give --l and --c, or --f0 for a design")
        return ac.classd_lc(a.l, a.c, a.rload, not a.se, a.cdiff, a.fsw,
                            r_dcr=a.dcr)
    if c == "classd":
        return ac.classd_power(a.pvdd, a.rload, a.rds, a.dcr, not a.se)
    if c == "sar":
        return ac.sar_rc(a.r, a.cfilt, a.csamp, int(a.bits), a.tacq, a.vfs)
    raise ValueError(c)


def _finite(x):
    """JSON has no infinity: an infinite value (a Q with no damping) is
    written as null."""
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_finite(v) for v in x]
    if isinstance(x, float) and math.isinf(x):
        return None
    return x


def _zout_for(db: float, zmin: float, zmax: float) -> float:
    lo, hi = 0.0, zmax * 10
    for _ in range(80):
        mid = (lo + hi) / 2
        ok = abs(ac.damping_deviation_db(mid, zmin, zmax)) <= db
        lo, hi = (mid, hi) if ok else (lo, mid)
    return lo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, *specs):
        p = sub.add_parser(name)
        for flag, kw in specs:
            if kw.get("action"):
                p.add_argument(flag, **kw)
            else:
                p.add_argument(flag, type=kw.pop("type", num), **kw)
        return p

    req = {"required": True}

    def opt(d=None):
        return {"default": d}

    cmd("noise", ("--en", dict(req)), ("--in", opt(0.0)),
        ("--rs", opt(0.0)), ("--rf", opt(0.0)), ("--rg", opt(math.inf)),
        ("--bw", opt(20e3)), ("--fc-1f", opt(0.0)), ("--t", opt(25.0)))
    cmd("level", ("--dbu", opt()), ("--dbv", opt()), ("--vrms", opt()))
    cmd("bridge", ("--zout", dict(req)), ("--zin", dict(req)))
    cmd("damping", ("--zout", dict(req)), ("--zmin", dict(req)),
        ("--zmax", dict(req)))
    cmd("hpf", ("--c", opt()), ("--fc", opt()), ("--rload", dict(req)),
        ("--rsrc", opt(0.0)), ("--f", opt(20.0)),
        ("--stages", {"type": int, "default": 1}))
    cmd("bias", ("--ib", dict(req)), ("--c", dict(req)))
    cmd("fpbw", ("--slew", opt()), ("--f", opt(20e3)), ("--vpk", dict(req)))
    cmd("riso", ("--gbw", dict(req)), ("--ro", dict(req)),
        ("--cl", dict(req)), ("--ng", opt(1.0)), ("--riso", opt(0.0)),
        ("--fp2", opt()), ("--pm", opt(60.0)))
    cmd("lc", ("--l", opt()), ("--c", opt()), ("--rload", dict(req)),
        ("--cdiff", opt(0.0)), ("--se", {"action": "store_true"}),
        ("--fsw", opt()), ("--dcr", opt(0.0)), ("--f0", opt()),
        ("--q", opt(1 / math.sqrt(2))))
    cmd("classd", ("--pvdd", dict(req)), ("--rload", dict(req)),
        ("--rds", opt(0.0)), ("--dcr", opt(0.0)),
        ("--se", {"action": "store_true"}))
    cmd("sar", ("--r", dict(req)), ("--cfilt", dict(req)),
        ("--csamp", dict(req)), ("--bits", dict(req)), ("--tacq", dict(req)),
        ("--vfs", opt(1.0)))
    try:
        a = ap.parse_args(argv)
        res = calc(a)
    except (ValueError, ZeroDivisionError, argparse.ArgumentTypeError) as exc:
        print(json.dumps({"script": "analog_calc", "status": "error",
                          "error": str(exc)}))
        return 2
    print(json.dumps({"script": "analog_calc", "status": "pass",
                      "calc": a.cmd, "result": _finite(res)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
