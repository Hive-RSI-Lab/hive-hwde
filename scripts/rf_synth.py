#!/usr/bin/env python
"""rf_synth.py - RF network synthesis (hive-hwde #488).

  match  (--z R+jX@f | --s1p F) [--f0 F] [--bw B] [--z0 50]
         [--topology all|L|pi|T] [--q Q] [--series E24]
         [--model generic0402|ideal|parts.json] [--points 21] [--rl 10]
         [--mc 200] [--seed 1] [--tol-l 5%] [--tol-c 0.1p]
         [--engine ngspice|python] [--load-model lumped|const]

Every valid L-section (Pozar sec. 5.1) and, for a loaded Q (--q, else
max(2, 1.5 x the least Q the load allows)), the pi and T networks
(Bowick ch. 4), each as its ideal values, snapped to the E-series with
the best worst-case return loss across the band, and the RL across the
band: ideal; ideal values with the parts' parasitics; snapped values with
parasitics. Then a tolerance Monte Carlo of each snapped network (the
shared lib/tolerance.py engine, the circuits run in ngspice: one AC deck
per band frequency holding every draw) with the yield against --rl, and
the solutions ranked by it.

Parasitics: the generic 0402 model (assumed typical numbers, lib/rfmatch.py:
inductor Q 20 and SRF ~ 11 GHz / sqrt(L/nH), cap ESL 0.4 nH ESR 0.2 ohm; no
datasheet behind them), or a JSON part
model {"L": {"q":..}, "C": {"esl_h":.., "esr_ohm":..},
"parts": [{"kind": "L", "value": "3.9n", "srf_hz": 5.8e9, "q": 28}]}.
Tolerances: C under 10 pF +-0.1 pF, else +-5 %; L +-5 % (--tol-l/--tol-c
take '2%' or an absolute '0.3n').

Output: one JSON object. Exit 0 ok, 1 when no snapped network meets --rl
across the band (nominal), 2 on bad input.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))

import rfmatch  # noqa: E402
import tolerance  # noqa: E402

UNIT = {"L": "H", "C": "F"}
WORKER_TIMEOUT_S = 600


# ----------------------------------------------------------------- engine

def exec_decks(path: Path, dll: Path) -> dict:
    """Worker side: every deck through one in-process ngspice; return the
    port voltages v(p<k>) of each as [re, im]."""
    import re

    import numpy as np
    import simlib
    decks = json.loads(path.read_text(encoding="utf-8"))
    simlib.setup_engine_env(dll)
    try:
        ng = simlib._new_engine()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"engine load failed: {exc}"}
    out = []
    port = re.compile(r"p(\d+)")
    for i, d in enumerate(decks):
        try:
            ng.load_circuit(simlib.prepare_circuit(d))
            ng.run()
            vecs = ng.plot(None, ng.last_plot)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"deck {i}: {exc}: "
                    f"{(ng.stderr or ng.stdout or '')[-400:]}"}
        row = {}
        for k, v in vecs.items():
            m = port.fullmatch(k.lower())
            if m:
                c = complex(np.asarray(v.to_waveform())[0])
                row[int(m.group(1))] = [c.real, c.imag]
        out.append([row[k] for k in sorted(row)])
        try:
            ng.destroy()
            ng.remove_circuit()
        except Exception:  # noqa: BLE001
            pass
    return {"status": "ok", "ngspice_version": ng.ngspice_version,
            "ports": out}


def run_decks(decks: list[str], dll: Path) -> list[list[complex]]:
    """Every deck in one killable worker -> per deck, v(p<k>) per copy."""
    with tempfile.TemporaryDirectory(prefix="rf_synth-") as td:
        f = Path(td) / "decks.json"
        f.write_text(json.dumps(decks), encoding="utf-8")
        cp = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                             "--exec-decks", str(f), "--dll", str(dll)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=WORKER_TIMEOUT_S)
    try:
        res = json.loads(cp.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"ngspice worker died (exit {cp.returncode}): "
                           f"{(cp.stderr or cp.stdout)[-400:]}")
    if res.get("status") != "ok":
        raise RuntimeError(f"ngspice: {res.get('error')}")
    return [[complex(*p) for p in d] for d in res["ports"]]


def spice_rl(variants: list[list[dict]], load: rfmatch.Load, freqs, z0,
             model, dll) -> list[list[float]]:
    """RL (dB) of each variant at each frequency, from ngspice AC."""
    decks = [rfmatch.deck(variants, load.z(f), f, z0, model) for f in freqs]
    ports = run_decks(decks, dll)
    return [[rfmatch.rl_db(2 * ports[i][k] - 1) for i in range(len(freqs))]
            for k in range(len(variants))]


def find_dll():
    try:
        import env
        return env.find_ngspice_dll()
    except Exception:  # noqa: BLE001 - no KiCad: python engine
        return None


# ----------------------------------------------------------------- model

def part_model(spec: str) -> rfmatch.PartModel:
    if spec in ("generic0402", "generic", None):
        return rfmatch.PartModel()
    if spec == "ideal":
        return rfmatch.PartModel(kind="ideal")
    doc = json.loads(Path(spec).read_text(encoding="utf-8"))
    pm = rfmatch.PartModel(kind=doc.get("kind", "custom"))
    lk, ck = doc.get("L", {}), doc.get("C", {})
    pm.l_q = float(lk.get("q", pm.l_q))
    pm.l_srf_1nh_hz = float(lk.get("srf_1nh_hz", pm.l_srf_1nh_hz))
    pm.c_esl_h = float(ck.get("esl_h", pm.c_esl_h))
    pm.c_esr_ohm = float(ck.get("esr_ohm", pm.c_esr_ohm))
    for p in doc.get("parts", []):
        v = rfmatch.parse_si(p["value"], UNIT[p["kind"]])
        pm.per_value[v] = {k: p[k] for k in ("kind", "srf_hz", "q",
                                             "esr_ohm", "esl_h") if k in p}
    return pm


def fmt_elements(els) -> list[dict]:
    return [{"where": e["where"], "kind": e["kind"],
             "value": float(f"{e['value']:.6g}"),
             "text": rfmatch.fmt_si(e["value"], UNIT[e["kind"]])}
            for e in els]


def _band_row(r: dict) -> dict:
    return {"min_db": r["min_db"], "worst_hz": round(r["worst_hz"]),
            "mean_db": r["mean_db"]}


# ----------------------------------------------------------------- match

def monte_carlo(snapped, load, freqs, z0, model, a, dll) -> dict:
    params = {}
    for i, e in enumerate(snapped):
        lo, hi = rfmatch.default_tol(e["kind"], e["value"], a.tol_l,
                                     a.tol_c)
        params[f"e{i}"] = tolerance.Param(
            f"e{i}", e["value"], lo, hi, a.dist,
            assumed=(a.tol_l if e["kind"] == "L" else a.tol_c) is None,
            source=f"{e['where']} {e['kind']} "
                   f"{rfmatch.fmt_si(e['value'], UNIT[e['kind']])}")

    def f_py(pt):
        els = rfmatch.elements_with(snapped, pt)
        return {"rl_min_db": rfmatch.rl_band(els, load, freqs, z0,
                                             model)["min_db"]}

    def batch(points):
        variants = [rfmatch.elements_with(snapped, p) for p in points]
        rls = spice_rl(variants, load, freqs, z0, model, dll)
        return [{"rl_min_db": min(r)} for r in rls]

    res = tolerance.analyse(params, f_py, analysis="mc", n=a.mc,
                            seed=a.seed,
                            bounds=[{"measure": "rl_min_db", "min": a.rl}],
                            batch=batch if dll else None)
    m = res["measures"]["rl_min_db"]
    return {"engine": "ngspice" if dll else "python", "n": a.mc,
            "seed": a.seed, "dist": a.dist,
            "tolerances": {k: {"part": p.source, "lo": p.lo, "hi": p.hi,
                               "assumed": p.assumed}
                           for k, p in params.items()},
            "yield": round(res["yield"]["all"], 4),
            "rl_min_db": {k: round(m[k], 3) for k in ("min", "max", "mean")
                          if m.get(k) is not None},
            "top3": res.get("top3", {}).get("rl_min_db", [])}


def match(a) -> tuple[dict, int]:
    if bool(a.z) == bool(a.s1p):
        raise ValueError("give --z R+jX[@f] or --s1p FILE")
    f0 = rfmatch.parse_si(a.f0, "Hz") if a.f0 else None
    if a.z:
        z, fz = rfmatch.parse_z(a.z)
        f0 = f0 or fz
        if not f0:
            raise ValueError("no frequency: --f0 or --z R+jX@f")
        load = rfmatch.Load(z0=z, f0=fz or f0, model=a.load_model,
                            source=a.z)
    else:
        load = rfmatch.read_s1p(a.s1p)
        if not f0:
            raise ValueError("--s1p needs --f0")
    bw = rfmatch.parse_si(a.bw, "Hz") if a.bw else 0.0
    freqs = rfmatch.band(f0, bw, a.points)
    z0 = float(a.z0)
    zl = load.z(f0)
    model = part_model(a.model)
    ideal = rfmatch.PartModel(kind="ideal")
    dll = find_dll() if a.engine == "ngspice" and a.mc else None
    notes = []
    if a.engine == "ngspice" and a.mc and dll is None:
        notes.append("no ngspice library (KiCad's libngspice): the Monte "
                     "Carlo ran on the closed form")

    nets = []
    topo = a.topology.lower()
    if topo in ("all", "l"):
        nets += rfmatch.l_sections(zl, z0, f0)
    q = a.q
    qmin = max(rfmatch.q_min(zl, z0, "pi"), rfmatch.q_min(zl, z0, "T"))
    if q is None:
        q = max(2.0, 1.5 * qmin)
    for kind, fn in (("pi", rfmatch.pi_networks), ("t", rfmatch.t_networks)):
        if topo in ("all", kind):
            try:
                nets += fn(zl, z0, f0, q)
            except ValueError as exc:
                if topo == kind:
                    raise
                notes.append(str(exc))

    sols = []
    for n in nets:
        if not n["elements"]:
            sols.append({"topology": n["topology"], "form": "none",
                         "elements": [], "note": "already matched"})
            continue
        snapped = rfmatch.snap(n["elements"], load, freqs, z0, a.series,
                               model)
        row = {"topology": n["topology"], "form": n["form"],
               "elements": fmt_elements(n["elements"]),
               "snapped": fmt_elements(snapped),
               "rl": {"ideal": _band_row(rfmatch.rl_band(
                          n["elements"], load, freqs, z0, ideal)),
                      "ideal_parasitic": _band_row(rfmatch.rl_band(
                          n["elements"], load, freqs, z0, model)),
                      "snapped": _band_row(rfmatch.rl_band(
                          snapped, load, freqs, z0, model))},
               "srf_hz": [model.params(e["kind"], e["value"]).get("srf_hz")
                          for e in snapped]}
        if "q" in n:
            row["q"] = n["q"]
            row["r_virtual"] = round(n["r_virtual"], 4)
        low_srf = [i for i, s in enumerate(row["srf_hz"])
                   if s and s < 2 * freqs[-1]]
        if low_srf:
            row["warnings"] = [
                f"{row['snapped'][i]['text']} self-resonates at "
                f"{row['srf_hz'][i] / 1e9:.3g} GHz, under 2 x the band top"
                for i in low_srf]
        if a.mc:
            row["mc"] = monte_carlo(snapped, load, freqs, z0, model, a, dll)
        sols.append(row)

    def rank(s):
        if not s.get("snapped"):
            return (0, 0)
        return (-(s.get("mc", {}).get("yield", 0.0)),
                -s["rl"]["snapped"]["min_db"])
    sols.sort(key=rank)
    ok = any(s.get("snapped") is not None and
             s["rl"]["snapped"]["min_db"] >= a.rl for s in sols) or \
        any(s.get("form") == "none" for s in sols)
    unmatched = rfmatch.rl_band([], load, freqs, z0, ideal)
    out = {"status": "ok" if ok else "no_match",
           "load": {"z_f0": [round(zl.real, 4), round(zl.imag, 4)],
                    "source": load.source, "model": "s1p" if load.freqs
                    else a.load_model},
           "f0_hz": f0, "bw_hz": bw, "points": len(freqs), "z0": z0,
           "rl_target_db": a.rl, "series": a.series,
           "part_model": model.kind, "q": q, "q_min": round(qmin, 4),
           "rl_unmatched": _band_row(unmatched),
           "solutions": sols}
    if notes:
        out["notes"] = notes
    return out, 0 if ok else 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--exec-decks"]:
        ap = argparse.ArgumentParser()
        ap.add_argument("--exec-decks", type=Path)
        ap.add_argument("--dll", type=Path)
        w = ap.parse_args(argv)
        print(json.dumps(exec_decks(w.exec_decks, w.dll)))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match", help="L / pi / T matching networks")
    m.add_argument("--z", help="load impedance R+jX[@f], e.g. 35-j12@2.45G")
    m.add_argument("--s1p", help="load as a Touchstone one-port")
    m.add_argument("--f0", help="design frequency (2.45G, 868M)")
    m.add_argument("--bw", help="band width around f0 (default: f0 only)")
    m.add_argument("--z0", default="50", help="source resistance (50)")
    m.add_argument("--topology", default="all",
                   choices=["all", "L", "pi", "T", "l", "t"])
    m.add_argument("--q", type=float, help="loaded Q for pi / T")
    m.add_argument("--series", default="E24")
    m.add_argument("--model", default="generic0402",
                   help="generic0402 | ideal | part-model JSON")
    m.add_argument("--points", type=int, default=21)
    m.add_argument("--rl", type=float, default=10.0,
                   help="return-loss target across the band, dB (10)")
    m.add_argument("--mc", type=int, default=200,
                   help="Monte Carlo draws per network (0: off)")
    m.add_argument("--seed", type=int, default=1)
    m.add_argument("--dist", default="uniform", choices=tolerance.DISTS)
    m.add_argument("--tol-l")
    m.add_argument("--tol-c")
    m.add_argument("--engine", default="ngspice",
                   choices=["ngspice", "python"])
    m.add_argument("--load-model", default="lumped",
                   choices=["lumped", "const"])
    m.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        res, code = match(a)
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    text = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
