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
            (capacitances here and i2c --cb: a bare number is pF)
  ldo       --vin 5 --vout 3.3 --iout 0.3 --theta 180 [--ta 40] [--iq 50u]
  holdup    --c 470u --vstart 20 --vmin 12 --iload 0.5
  rpower    --r 100 --v 3.3 [--footprint R_0603_1608Metric]
  cascade   --stage LNA:15:1.2[:oip3] --stage mixer:-7:7:15 ...
            [--bw 10M --snr 10]                      Friis NF/gain/IIP3
            (a passive loss of L dB is --stage name:-L:L)
  rxsens    --nf 4 --bw 1M [--snr 10] [--t 290]      kTB + NF floor
  detector  --bw 1M (--responsivity 5000 --rv 5k | --is 3u [--n 1.05]
            [--ib 0]) [--en 4n] [--rl 100k] [--t 300] square-law NEP/TSS
  pll       --icp 5m --kvco 100M --n 2400 (--fc 100k --pm 50 |
            --r 1k --cser 10n --cshunt 1n) [--fref 10M]
                                                     loop filter, fc, PM
  lmatch    --z 200-j100 --z0 100 --f 500M           L-sections (Pozar
            5.1); pi/T, real parts, MC: hwde rf_synth match
  line      --f 24G (--eeff 3.3 | --er 4.4 [--w 0.3 --h 0.2 [--t 0.035]
            [--bare]]) [--length 12.5]               wavelength, phase

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


def pf(text: str) -> float:
    """A small capacitance in farads: '18p', '18pF' or a bare '18' (pF, as
    load and bus capacitances are written; 18 F is never meant). A bare
    number under 1e-6 is already farads ('18e-12')."""
    s = str(text).strip()
    try:
        v = float(s)
        return v if abs(v) < 1e-6 else v * 1e-12
    except ValueError:
        pass
    v = engcalc.parse_value(s)
    if v is None:
        raise argparse.ArgumentTypeError(f"not a capacitance: {text!r}")
    return v


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
    if c == "cascade":
        res = engcalc.friis_cascade([stage(t) for t in a.stage])
        if a.bw is not None:
            res["rx"] = engcalc.rx_sensitivity(res["nf_db"], a.bw, a.snr)
        return res
    if c == "rxsens":
        return engcalc.rx_sensitivity(a.nf, a.bw, a.snr, a.t)
    if c == "detector":
        return engcalc.diode_detector(a.bw, a.responsivity, a.rv, a.i_s, a.n,
                                      a.ib, a.en, a.rl, a.t)
    if c == "pll":
        parts = (a.r, a.cser, a.cshunt)
        if all(v is not None for v in parts):
            return engcalc.pll_loop_analyse(a.icp, a.kvco, a.n, *parts,
                                            f_ref=a.fref)
        if a.fc is None or a.pm is None:
            raise ValueError("give --fc and --pm, or --r --cser --cshunt")
        d = engcalc.pll_loop_design(a.icp, a.kvco, a.n, a.fc, a.pm)
        d["check"] = engcalc.pll_loop_analyse(
            a.icp, a.kvco, a.n, d["r_ohm"], d["c_series_f"], d["c_shunt_f"],
            f_ref=a.fref)
        return d
    if c == "line":
        return line(a)
    if c == "lmatch":
        return lmatch(a)
    raise ValueError(c)


def lmatch(a) -> dict:
    """Every L-section from --z to a real --z0 at --f (Pozar sec. 5.1,
    lib/rfmatch.py); the full design (pi/T, real parts, Monte Carlo) is
    `hwde rf_synth match`."""
    import rfmatch
    z, fz = rfmatch.parse_z(a.z)
    f = a.f or fz
    if not f:
        raise ValueError("no frequency: --f or --z R+jX@f")
    unit = {"L": "H", "C": "F"}
    sols = []
    for n in rfmatch.l_sections(z, a.z0, f):
        sols.append({"form": n["form"], "elements": [
            {"where": e["where"], "kind": e["kind"], "value": e["value"],
             "text": rfmatch.fmt_si(e["value"], unit[e["kind"]]),
             **({"x_ohm": e["x"]} if "x" in e else {"b_s": e["b"]})}
            for e in n["elements"]]})
    return {"z_load": [z.real, z.imag], "z0": a.z0, "f_hz": f,
            "solutions": sols}


def stage(text: str) -> dict:
    """'LNA:15:1.2' or 'mixer:-7:7:15' -> a friis_cascade stage."""
    f = text.split(":")
    if len(f) not in (3, 4):
        raise ValueError(f"stage {text!r}: want name:gain_db:nf_db[:oip3_dbm]")
    st = {"name": f[0], "gain_db": float(f[1]), "nf_db": float(f[2])}
    if len(f) == 4:
        st["oip3_dbm"] = float(f[3])
    return st


def line(a) -> dict:
    """eps_eff given, a stripline/coax er, or a microstrip from w/h/er (the
    field solver's quasi-static eps_eff, JLC mask unless --bare, then
    Getsinger dispersion at f)."""
    extra = {}
    if a.eeff is not None:
        eeff, how = a.eeff, "given"
    elif a.er is None:
        raise ValueError("give --eeff, or --er (stripline) with --w --h "
                         "for microstrip")
    elif a.w is None and a.h is None:
        eeff, how = a.er, "stripline (eps_eff = er)"
    elif a.w is None or a.h is None:
        raise ValueError("microstrip needs both --w and --h (mm)")
    else:
        import impedance
        if impedance.SOLVER_OK:
            ms = impedance.field_microstrip(
                a.w, a.h, a.t, a.er, None,
                None if a.bare else impedance.JLC_MASK)
            how = "microstrip, field solver + Getsinger dispersion"
        else:
            ms = {"eps_eff": engcalc.microstrip_eeff_static(a.w, a.h, a.er),
                  "z0": impedance.microstrip_z0_closed_form(a.w, a.h, a.t,
                                                            a.er)}
            how = "microstrip, Hammerstad (bare, no solver) + Getsinger"
        eeff = engcalc.microstrip_dispersion(a.er, ms["eps_eff"], ms["z0"],
                                             a.h, a.f)
        extra = {"z0": ms["z0"], "eps_eff_static": ms["eps_eff"],
                 "masked": not a.bare and impedance.SOLVER_OK}
    res = engcalc.line_wavelength(a.f, eeff, a.length)
    res["eps_eff_from"] = how
    res.update(extra)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, *specs):
        p = sub.add_parser(name)
        for flag, kw in specs:
            if kw.get("action") != "store_true":
                kw.setdefault("type", num)
            p.add_argument(flag, **kw)
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
    cmd("i2c", ("--vdd", dict(req)), ("--rp", dict(req)), ("--cb", dict(req, type=pf)),
        ("--khz", {"type": int, "default": 400}))
    cmd("crystal", ("--c1", dict(req, type=pf)), ("--c2", dict(req, type=pf)),
        ("--stray", {"type": pf,
                     "default": engcalc.CRYSTAL_STRAY_PF * 1e-12}))
    cmd("ldo", ("--vin", dict(req)), ("--vout", dict(req)),
        ("--iout", dict(req)), ("--theta", dict(req)),
        ("--ta", {"default": 25.0}), ("--iq", {"default": 0.0}))
    cmd("holdup", ("--c", dict(req)), ("--vstart", dict(req)),
        ("--vmin", dict(req)), ("--iload", dict(req)))
    cmd("rpower", ("--r", dict(req)), ("--v", dict(req)),
        ("--footprint", {"type": str, "default": None}))
    opt = {"default": None}
    cmd("cascade", ("--stage", {"type": str, "action": "append",
                                "required": True}),
        ("--bw", dict(opt)), ("--snr", {"type": float, "default": 0.0}))
    cmd("rxsens", ("--nf", {"type": float, "required": True}),
        ("--bw", dict(req)), ("--snr", {"type": float, "default": 0.0}),
        ("--t", {"default": engcalc.T0_K}))
    cmd("detector", ("--bw", dict(req)), ("--responsivity", dict(opt)),
        ("--rv", dict(opt)), ("--is", {"dest": "i_s", "default": None}),
        ("--n", {"default": 1.05}), ("--ib", {"default": 0.0}),
        ("--en", {"default": 0.0}), ("--rl", dict(opt)),
        ("--t", {"default": 300.0}))
    cmd("pll", ("--icp", dict(req)), ("--kvco", dict(req)), ("--n", dict(req)),
        ("--fc", dict(opt)), ("--pm", dict(opt)), ("--r", dict(opt)),
        ("--cser", dict(opt)), ("--cshunt", dict(opt)), ("--fref", dict(opt)))
    cmd("line", ("--f", dict(req)), ("--eeff", dict(opt)), ("--er", dict(opt)),
        ("--w", dict(opt)), ("--h", dict(opt)), ("--t", {"default": 0.035}),
        ("--bare", {"action": "store_true"}),
        ("--length", dict(opt)))
    cmd("lmatch", ("--z", dict(req, type=str)), ("--z0", {"default": 50.0}),
        ("--f", dict(opt)))
    try:
        a = ap.parse_args(argv)
        res = calc(a)
    except (ValueError, ZeroDivisionError, RuntimeError) as exc:
        print(json.dumps({"script": "eng_calc", "status": "error",
                          "error": str(exc)}))
        return 2
    print(json.dumps({"script": "eng_calc", "status": "pass", "calc": a.cmd,
                      "result": res}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
