#!/usr/bin/env python
"""analog_verify.py - re-prove lib/analogcalc.py against ngspice.

Each case builds a small circuit, runs it in KiCad's bundled ngspice and
compares what the engine measures with what the hand calculation says. It
also re-checks two facts about the engine itself that a sim writer trips on
(reference/ingest/analog-audio.md, "Engine facts"):

  pow_sign       B-source x^3 and pow(x,3) of a negative x come back
                 positive (|x|^3); x*x*x and pwr(x,3) keep the sign.
  thd_step_floor a linear circuit shows a false THD when the transient has
                 no max timestep (0.002 % here at reltol 1e-6: as much as a
                 good audio stage's real THD); a step of period/1000 is clean.

  analog_verify.py [--out report.json] [--case NAME ...]

Exit 0 when every case agrees, 1 when one doesn't, 2 when ngspice can't be
loaded. Runs the engine in-process: call it as its own process.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))

import analogcalc as ac  # noqa: E402

_NG = None


def _engine():
    global _NG
    if _NG is None:
        import env  # noqa: PLC0415
        import simlib  # noqa: PLC0415
        dll = env.find_ngspice_dll()
        if dll is None:
            raise RuntimeError("no ngspice shared library")
        simlib.setup_engine_env(dll)
        from InSpice.Spice.NgSpice.Shared import NgSpiceShared  # noqa
        NgSpiceShared.find_library = classmethod(lambda cls, n: str(dll))
        _NG = NgSpiceShared.new_instance()
    return _NG


def sim(circuit: str) -> dict:
    """Run a circuit; every vector of its last plot as a numpy array."""
    import numpy as np  # noqa: PLC0415
    import simlib  # noqa: PLC0415
    ng = _engine()
    ng.load_circuit(simlib.prepare_circuit(circuit))
    try:
        ng.run()
    except Exception:  # a failed .measure; the plot is still there
        pass
    pl = ng.plot(None, ng.last_plot)
    return {k.lower(): np.array(getattr(v, "_data", v)) for k, v in
            pl.items()}


def fourier_thd(node: str, f0: float) -> float:
    """ngspice's own `fourier` THD (%) of the last transient."""
    out = _engine().exec_command(f"fourier {f0:g} v({node})")
    for line in out.splitlines():
        if "THD:" in line:
            return float(line.split("THD:")[1].split("%")[0])
    raise RuntimeError(f"no THD in fourier output: {out[-300:]}")


def _close(a, b, rel=0.0, abs_=0.0):
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


# ------------------------------------------------------------------ cases

def case_johnson():
    import numpy as np  # noqa: PLC0415
    ng = _engine()
    import simlib  # noqa: PLC0415
    ng.load_circuit(simlib.prepare_circuit("""johnson
V1 a 0 dc 0 ac 1
R1 a b 1k
.options temp=25 tnom=25
.noise v(b) V1 dec 1 1k 10k
"""))
    ng.run()
    name = next(n for n in ng.plot_names if n.startswith("noise1"))
    got = float(np.array(ng.plot(None, name)["onoise_spectrum"]._data)[0])
    want = ac.johnson_v(1e3, 25.0)
    return [{"what": "1k at 25 C, V/rtHz", "sim": got, "calc": want,
             "ok": _close(got, want, rel=0.002)}]


def case_classd_lc():
    import numpy as np  # noqa: PLC0415
    rows = []
    for l, c, r, cd, dcr in ((10e-6, 0.68e-6, 8, 0, 0),
                             (22e-6, 0.68e-6, 8, 0, 0),
                             (10e-6, 1e-6, 4, 0.22e-6, 0),
                             (10e-6, 0.68e-6, None, 0, 0.05)):
        load = f"RL op on {r}" if r else "RL op on 1e9"
        p = sim(f"""btl
Va pa 0 dc 0 ac 0.5
Vb pb 0 dc 0 ac -0.5
L1 pa x1 {l}
R1 x1 op {max(dcr, 1e-6)}
L2 pb x2 {l}
R2 x2 on {max(dcr, 1e-6)}
C1 op 0 {c}
C2 on 0 {c}
Cd op on {cd or 1e-18}
{load}
.ac dec 400 1k 1meg
""")
        f = p["frequency"].real
        h = abs(p["op"] - p["on"])
        calc = ac.classd_lc(l, c, r or 8, True, cd, f_sw=400e3, r_dcr=dcr)
        what = f"L {l:g} C {c:g} Cd {cd:g} R {r or 'open'}"
        if r is None:
            got = 20 * np.log10(h.max())
            rows.append({"what": what + " peak dB (open)", "sim": got,
                         "calc": calc["peak_db_open"],
                         "ok": _close(got, calc["peak_db_open"], abs_=0.5)})
            continue
        i20 = int(np.argmin(abs(f - 20e3)))
        isw = int(np.argmin(abs(f - 400e3)))
        for key, got, tol in (
                ("peak_db", max(0.0, 20 * np.log10(h.max())), 0.05),
                ("at_f_audio_db", 20 * np.log10(h[i20]), 0.05),
                ("at_fsw_db", 20 * np.log10(h[isw]), 0.2)):
            want = max(0.0, calc[key]) if key == "peak_db" else calc[key]
            rows.append({"what": f"{what} {key}", "sim": float(got),
                         "calc": want, "ok": _close(got, want, abs_=tol)})
    return rows


def _loop(gbw, ro, cl, noise_gain, riso, fp2=None):
    """The open loop broken at the op-amp's input: dominant pole (gm 1
    into 1/(2 pi gbw) F), an optional second pole, ro, riso, cl, feedback
    1/noise_gain taken at the op-amp's output."""
    import numpy as np  # noqa: PLC0415
    pole2, src = "", "x"
    if fp2:
        pole2 = (f"R2 y 0 1\nC2 y 0 {1 / (2 * math.pi * fp2)}\n"
                 f"G2 0 y x 0 1\n")
        src = "y"
    p = sim(f"""loop
Vt in 0 dc 0 ac 1
G1 0 x in 0 1
C1 x 0 {1 / (2 * math.pi * gbw)}
R1 x 0 1e12
{pole2}E1 a 0 {src} 0 1
Ro a o {ro}
Ri o out {max(riso, 1e-6)}
CL out 0 {cl}
Eb fb 0 o 0 {1 / noise_gain}
.ac dec 400 1 1g
""")
    f, t = p["frequency"].real, p["fb"]
    i = int(np.where(abs(t) < 1)[0][0])
    return float(f[i]), float(180 + np.degrees(np.angle(t[i])))


def case_cap_load_pm():
    rows = []
    for gbw, ro, cl, ng_, ri, fp2 in ((10e6, 50, 1e-9, 1, 0, None),
                                      (10e6, 50, 1e-9, 1, 20, None),
                                      (10e6, 50, 1e-9, 4, 0, None),
                                      (10e6, 50, 1e-9, 1, 10, 30e6),
                                      (1e6, 100, 10e-9, 1, 0, None)):
        fc, pm = _loop(gbw, ro, cl, ng_, ri, fp2)
        c = ac.cap_load_pm(gbw, ro, cl, ng_, ri, fp2 or math.inf)
        rows.append({"what": f"GBW {gbw:g} Ro {ro} CL {cl:g} NG {ng_} "
                             f"Riso {ri} fp2 {fp2} PM deg", "sim": pm,
                     "calc": c["phase_margin_deg"],
                     "ok": _close(pm, c["phase_margin_deg"], abs_=1.0)})
    # the second-order PM -> overshoot estimate, against a step
    for ri in (0.0, 20.0):
        p = sim(f"""step
Vs s 0 pulse(0 0.01 1n 1p 1p 1 2)
G1 0 x s o 1
C1 x 0 {1 / (2 * math.pi * 10e6)}
R1 x 0 1e12
E1 a 0 x 0 1
Ro a o 50
Ri o out {max(ri, 1e-6)}
CL out 0 1n
.tran 1n 5u 0 1n
""")
        got = float(p["o"].real.max() / 0.01 - 1)
        est = ac.cap_load_pm(10e6, 50, 1e-9, 1, ri)["overshoot_est"]
        # without riso the loop is second order and the estimate exact;
        # riso's zero adds overshoot, so the estimate is a floor there
        ok = _close(got, est, abs_=0.01) if not ri else got >= est - 0.01
        rows.append({"what": f"step overshoot, Riso {ri:g}", "sim": got,
                     "calc": est, "ok": ok})
    return rows


def case_sar_rc():
    rows = []
    for r, cf, cs, tacq, n in ((20, 1e-9, 40e-12, 500e-9, 16),
                               (100, 800e-12, 40e-12, 300e-9, 12),
                               (50, 10e-9, 25e-12, 1e-6, 18)):
        p = sim(f"""sar
Vd d 0 dc 0
R1 d n {r}
Cf n 0 {cf} ic=0
Rsw n s 0.01
Cs s 0 {cs} ic=1
.tran {tacq / 4000} {tacq} 0 {tacq / 4000} uic
""")
        v = p["n"].real
        c = ac.sar_rc(r, cf, cs, n, tacq)
        what = f"R {r} Cf {cf:g} Cs {cs:g} N {n}"
        rows.append({"what": what + " kickback V", "sim": float(v.max()),
                     "calc": c["kickback_v"],
                     "ok": _close(v.max(), c["kickback_v"], rel=0.02)})
        rows.append({"what": what + " residual V", "sim": float(v[-1]),
                     "calc": c["residual_v"],
                     "ok": _close(v[-1], c["residual_v"], rel=0.05,
                                  abs_=1e-9)})
    return rows


def case_hpf():
    import numpy as np  # noqa: PLC0415
    p = sim("""hpf
V1 a 0 dc 0 ac 1
Rs a b 600
C1 b c 10u
Rl c 0 10k
.ac dec 400 1 1k
""")
    f, h = p["frequency"].real, abs(p["c"])
    flat = 10e3 / 10.6e3
    i = int(np.argmin(abs(h - flat / math.sqrt(2))))
    want = ac.hpf_corner(10e-6, 10e3, 600)
    return [{"what": "600R source, 10u, 10k load: corner Hz", "sim":
             float(f[i]), "calc": want, "ok": _close(f[i], want, rel=0.01)}]


def case_thd():
    rows = []
    k = 0.01
    want = (k / 4) / (1 + 3 * k / 4)          # analytic x + k x^3
    n = 256
    xs = [math.sin(2 * math.pi * i / n) for i in range(n)]
    ours = ac.thd_from_samples([x + k * x ** 3 for x in xs], n, 1)["thd"]
    rows.append({"what": "thd_from_samples, x+0.01x^3", "sim": ours,
                 "calc": want, "ok": _close(ours, want, rel=1e-6)})
    sim(f"""thd
V1 a 0 sin(0 1 1k)
B1 b 0 v=v(a)+{k}*v(a)*v(a)*v(a)
R1 b 0 1k
.tran 1u 10m 0 1u
""")
    got = fourier_thd("b", 1e3) / 100
    rows.append({"what": "ngspice fourier, x+0.01x^3, step 1u", "sim": got,
                 "calc": want, "ok": _close(got, want, rel=0.002)})
    return rows


def case_engine_facts():
    rows = []
    for expr, sign in (("v(a)^3", 1.0), ("pow(v(a),3)", 1.0),
                       ("v(a)*v(a)*v(a)", -1.0), ("pwr(v(a),3)", -1.0)):
        p = sim(f"""pow
V1 a 0 dc -1
B1 b 0 v={expr}
R1 b 0 1k
.op
""")
        got = float(p["b"].real[0])
        rows.append({"what": f"pow_sign: {expr} at x=-1", "sim": got,
                     "calc": sign, "ok": _close(got, sign, abs_=1e-9)})
    lin = """lin
V1 a 0 sin(0 1 1k)
R1 a b 1k
C1 b 0 100n
L1 b c 1m
R2 c 0 100
{opt}
.tran {step}
"""
    sim(lin.format(opt=".options reltol=1e-6", step="10u 10m"))
    coarse = fourier_thd("c", 1e3)
    sim(lin.format(opt=".options reltol=1e-6", step="1u 10m 0 1u"))
    fine = fourier_thd("c", 1e3)
    rows.append({"what": "thd_step_floor: linear RLC, no max step, THD %",
                 "sim": coarse, "calc": 1e-3, "ok": coarse > 1e-4})
    rows.append({"what": "thd_step_floor: max step period/1000, THD %",
                 "sim": fine, "calc": 1e-6, "ok": fine < 1e-6})
    return rows


CASES = {"johnson": case_johnson, "classd_lc": case_classd_lc,
         "cap_load_pm": case_cap_load_pm, "sar_rc": case_sar_rc,
         "hpf": case_hpf, "thd": case_thd, "engine_facts": case_engine_facts}


def run(names=None) -> dict:
    try:
        _engine()
    except Exception as exc:
        return {"script": "analog_verify", "status": "error",
                "error": f"{type(exc).__name__}: {exc}"}
    out = {}
    for name in names or CASES:
        out[name] = CASES[name]()
    bad = [f"{k}: {r['what']}" for k, rows in out.items() for r in rows
           if not r["ok"]]
    return {"script": "analog_verify",
            "status": "fail" if bad else "pass", "failed": bad,
            "cases": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    ap.add_argument("--case", action="append", choices=sorted(CASES))
    a = ap.parse_args(argv)
    rep = run(a.case)
    text = json.dumps(rep, indent=1, default=float)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return {"pass": 0, "fail": 1}.get(rep["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
