"""tolerance.py - one tolerance source and one analysis engine for hwde.

Where a part's tolerance comes from (`part_tolerance`, `parse_spec`):
  1. an explicit spec: a number (a fraction, 0.01), a string out of a BOM
     value field ("10k 1%", "100n X7R 16V", "+80/-20%", "±0.5%") or a dict
     {tol | lo + hi, dist, vdc, footprint, value};
  2. else the dielectric the value names (X7R/X5R/X6S/X7S ±10%, C0G/NP0
     ±5%, Y5V/Z5U +80/-20%), mlcc.classify's class for a cap with none;
  3. else the kind default (`DEFAULTS`, marked assumed).
A class-II cap given `vdc` is centred on mlcc.c_eff at that bias and its low
edge reaches c_eff_low (the generic curve's low case) times (1 - tol).

The analyses (WCCA practice: EVA, RSS, Monte Carlo; see the module test for
the known answers) take `params` {name: Param} and a model
f({name: value}) -> {measure: value}, run it as often as they need, and
never care whether f is a closed form or a SPICE deck:
  sensitivities  central difference at nominal: dM/dp and the normalised
                 (dM/M)/(dp/p); a measure's top-3 contributors by
                 |dM/dp| * half-width
  eva            extreme value analysis: each measure at the vertex where
                 every parameter sits at the edge that pushes it out (two
                 runs per measure; exact for a monotonic measure)
  rss            sigma_M = sqrt(sum (dM/dp * sigma_p)^2), the band nom +-3 sigma
  corners        every 2^k vertex (k <= MAX_CORNER_PARAMS), else EVA's
  monte_carlo    n draws (numpy default_rng(seed)): min/max/mean/sigma and
                 the yield against bounds
`evaluate_batch` lets a SPICE caller run all the points of one analysis in
one engine batch: every analysis asks for its points first and evaluates
them in one call.
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field

import numpy as np

# Kind defaults when nothing names a tolerance (assumed): thick-film chip R
# as JLC stocks them (1%), inductors and ferrites 20%, an unread cap 20%.
DEFAULTS = {"R": 0.01, "C": 0.20, "L": 0.20, "V": 0.0, "I": 0.0}
# Cap class -> tolerance when only the class is known (common stock codes:
# K for class II, J for C0G, M for electrolytics).
CAP_CLASS_TOL = {"class2": 0.10, "class1": 0.05, "electrolytic": 0.20,
                 "tantalum": 0.10, "film": 0.05, "unknown": 0.20}
DIELECTRIC_TOL = {"X5R": (0.10, 0.10), "X6S": (0.10, 0.10),
                  "X7R": (0.10, 0.10), "X7S": (0.10, 0.10),
                  "X8R": (0.10, 0.10), "C0G": (0.05, 0.05),
                  "NP0": (0.05, 0.05), "Y5V": (0.20, 0.80),
                  "Z5U": (0.20, 0.80)}
DISTS = ("uniform", "gauss")
ANALYSES = ("mc", "corners", "wca")
MAX_CORNER_PARAMS = 10
SENS_STEP = 1e-3  # relative central-difference step

_PCT_ASYM = re.compile(r"\+\s*(\d+(?:\.\d+)?)\s*%?\s*/\s*-\s*(\d+(?:\.\d+)?)"
                       r"\s*%")
_PCT = re.compile(r"(?:±|\+/?-|\+-)?\s*(\d+(?:\.\d+)?)\s*%")
_DIEL = re.compile(r"\b(X5R|X6S|X7R|X7S|X8R|C0G|COG|NP0|NPO|Y5V|Z5U)\b",
                   re.I)


@dataclass
class Param:
    """One varied quantity: nominal, absolute edges lo <= nom <= hi, and a
    distribution over [lo, hi] (uniform, or gauss with 3 sigma at the edges,
    clipped to them)."""
    name: str
    nom: float
    lo: float
    hi: float
    dist: str = "uniform"
    source: str = ""
    assumed: bool = False
    notes: list = field(default_factory=list)

    @property
    def half(self) -> float:
        return (self.hi - self.lo) / 2

    @property
    def mid(self) -> float:
        return (self.hi + self.lo) / 2

    @property
    def sigma(self) -> float:
        if self.dist == "gauss":
            return self.half / 3
        return self.half / math.sqrt(3)

    def as_dict(self) -> dict:
        out = {"nom": self.nom, "lo": self.lo, "hi": self.hi,
               "dist": self.dist, "source": self.source}
        if self.assumed:
            out["assumed"] = True
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def make_param(name: str, nom: float, tol_lo: float, tol_hi: float | None =
               None, dist: str = "uniform", source: str = "",
               assumed: bool = False) -> Param:
    tol_hi = tol_lo if tol_hi is None else tol_hi
    if dist not in DISTS:
        raise ValueError(f"{name}: dist must be one of {DISTS}")
    a, b = nom * (1 - tol_lo), nom * (1 + tol_hi)
    return Param(name, nom, min(a, b), max(a, b), dist, source, assumed)


def parse_spec(spec, bare: bool = True) -> tuple[float, float, str] | None:
    """A BOM-ish tolerance -> (lo_frac, hi_frac, how) or None.
    '1%' / '±1%' / '+-1%' / '10k 1% 0603' -> 0.01 both ways; '+80/-20%'
    -> (0.20, 0.80); a dielectric code -> its class tolerance; a bare number
    is a fraction (0.01) unless bare=False (a BOM value field, where '100'
    is the part's value)."""
    if spec is None or isinstance(spec, bool):
        return None
    if isinstance(spec, (int, float)):
        if not math.isfinite(spec) or spec < 0:
            raise ValueError(f"tolerance {spec!r} must be a finite fraction "
                             ">= 0")
        return float(spec), float(spec), "fraction"
    s = str(spec)
    m = _PCT_ASYM.search(s)
    if m:
        return float(m.group(2)) / 100, float(m.group(1)) / 100, "percent"
    m = _PCT.search(s)
    if m:
        t = float(m.group(1)) / 100
        return t, t, "percent"
    m = _DIEL.search(s)
    if m:
        code = m.group(1).upper().replace("COG", "C0G").replace("NPO", "NP0")
        lo, hi = DIELECTRIC_TOL[code]
        return lo, hi, f"dielectric {code}"
    if not bare:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return parse_spec(v)


def part_tolerance(kind: str, value_text: str = "", tol_text=None, *,
                   footprint: str = "", nom: float | None = None,
                   vdc: float | None = None, dist: str = "uniform",
                   name: str = "") -> Param | None:
    """The tolerance band of one part, from its BOM fields. kind R/C/L/V/I
    (FB counts as L); nom defaults to the value read from value_text.
    Returns None when no nominal can be read."""
    import engcalc
    kind = {"FB": "L"}.get(kind.upper(), kind.upper())
    if nom is None:
        nom = engcalc.parse_value(value_text)
    if nom is None:
        return None
    got = parse_spec(tol_text) if tol_text is not None else None
    source = f"tolerance field {tol_text!r}" if got else ""
    if got is None:
        got = parse_spec(value_text, bare=False) if value_text else None
        source = f"value field {value_text!r}" if got else ""
    assumed = False
    notes: list[str] = []
    if got is None and kind == "C":
        import mlcc
        cls = mlcc.classify(value_text, footprint)
        t = CAP_CLASS_TOL[cls["kind"]]
        got = (*DIELECTRIC_TOL.get(cls["dielectric"] or "", (t, t)), "")
        source = (f"{cls['kind']} {cls['dielectric'] or ''}".strip()
                  + (" (assumed)" if cls["assumed"] else ""))
        assumed = cls["assumed"]
    if got is None:
        t = DEFAULTS.get(kind, 0.0)
        got = (t, t, "")
        source = f"{kind} default {t:g} (assumed)"
        assumed = True
    lo_f, hi_f, _ = got
    p = make_param(name, nom, lo_f, hi_f, dist, source, assumed)
    if kind == "C" and vdc:
        import mlcc
        ce = mlcc.c_eff(nom, vdc, footprint=footprint, value=value_text)
        if ce["c_eff"] < nom:
            p.nom = ce["c_eff"]
            p.lo = ce["c_eff_low"] * (1 - lo_f)
            p.hi = ce["c_eff"] * (1 + hi_f)
            p.notes.append(f"DC bias {vdc:g} V: c_eff {ce['c_eff']:.4g} "
                           f"(low {ce['c_eff_low']:.4g}, {ce['curve']})")
    return p


def spec_param(name: str, kind: str, nom: float, spec, *,
               value_text: str = "", dist: str = "uniform") -> Param:
    """A Param from a sidecar/deck spec: number, string or dict
    {tol | lo + hi, dist, vdc, footprint, value}."""
    if isinstance(spec, dict):
        d = spec.get("dist", dist)
        if "lo" in spec or "hi" in spec:
            lo = float(spec.get("lo", spec.get("hi", 0)))
            hi = float(spec.get("hi", spec.get("lo", 0)))
            p = make_param(name, nom, lo, hi, d, "spec lo/hi")
        else:
            p = part_tolerance(kind, spec.get("value", value_text),
                               spec.get("tol"),
                               footprint=spec.get("footprint", ""), nom=nom,
                               vdc=spec.get("vdc"), dist=d, name=name)
        return p
    got = parse_spec(spec)
    if got is None:
        raise ValueError(f"{name}: tolerance {spec!r} does not read")
    return make_param(name, nom, got[0], got[1], dist, f"spec {spec!r}")


# ---------------------------------------------------------------- engine

def _points_eval(f, points, batch):
    if batch is not None:
        return batch(points)
    return [f(p) for p in points]


def _nominal(params):
    return {k: p.nom for k, p in params.items()}


def sensitivity_points(params: dict[str, Param], step: float = SENS_STEP):
    pts = [_nominal(params)]
    for k, p in params.items():
        h = (abs(p.nom) or 1.0) * step
        for s in (+1, -1):
            q = _nominal(params)
            q[k] = p.nom + s * h
            pts.append(q)
    return pts


def sensitivities_from(params, results, step: float = SENS_STEP) -> dict:
    """{measure: {param: {d, norm, contrib}}} from sensitivity_points'
    results (nominal first, then +h/-h per parameter)."""
    nom = results[0]
    out: dict[str, dict] = {}
    for i, (k, p) in enumerate(params.items()):
        h = (abs(p.nom) or 1.0) * step
        up, dn = results[1 + 2 * i], results[2 + 2 * i]
        for m, m0 in nom.items():
            if m not in up or m not in dn:
                continue
            d = (up[m] - dn[m]) / (2 * h)
            norm = d * p.nom / m0 if m0 else None
            out.setdefault(m, {})[k] = {"d": d, "norm": norm,
                                        "contrib": abs(d) * p.half}
    return out


def top(sens_m: dict, n: int = 3) -> list[dict]:
    rows = sorted(sens_m.items(), key=lambda kv: -kv[1]["contrib"])
    return [{"param": k, **v} for k, v in rows[:n] if v["contrib"] > 0]


def eva_points(params, sens: dict) -> dict[str, tuple[dict, dict]]:
    """{measure: (min vertex, max vertex)} from the sensitivity signs."""
    out = {}
    for m, sm in sens.items():
        lo, hi = _nominal(params), _nominal(params)
        for k, p in params.items():
            d = sm.get(k, {"d": 0.0})["d"]
            if d > 0:
                lo[k], hi[k] = p.lo, p.hi
            elif d < 0:
                lo[k], hi[k] = p.hi, p.lo
        out[m] = (lo, hi)
    return out


def rss(params, sens: dict, nominal: dict) -> dict:
    out = {}
    for m, sm in sens.items():
        s = math.sqrt(sum((v["d"] * params[k].sigma) ** 2
                          for k, v in sm.items()))
        out[m] = {"sigma": s, "min": nominal[m] - 3 * s,
                  "max": nominal[m] + 3 * s}
    return out


def corner_points(params) -> list[dict]:
    keys = list(params)
    pts = []
    for edges in itertools.product((0, 1), repeat=len(keys)):
        pts.append({k: (params[k].lo, params[k].hi)[e]
                    for k, e in zip(keys, edges)})
    return pts


def mc_points(params, n: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    cols = {}
    for k, p in params.items():
        if p.dist == "gauss":
            x = rng.normal(p.mid, p.sigma, n)
            cols[k] = np.clip(x, p.lo, p.hi)
        else:
            cols[k] = rng.uniform(p.lo, p.hi, n)
    return [{k: float(cols[k][i]) for k in params} for i in range(n)]


def stats(values: list[float]) -> dict:
    a = np.asarray([v for v in values if v is not None and math.isfinite(v)])
    if not len(a):
        return {"n": 0}
    return {"n": int(len(a)), "min": float(a.min()), "max": float(a.max()),
            "mean": float(a.mean()),
            "sigma": float(a.std(ddof=1)) if len(a) > 1 else 0.0}


def _within(b: dict, v) -> bool:
    if v is None or not math.isfinite(v):
        return False
    return not ((b.get("min") is not None and v < b["min"])
                or (b.get("max") is not None and v > b["max"]))


def analyse(params: dict[str, Param], f=None, *, analysis: str = "wca",
            n: int = 500, seed: int = 1, bounds: list[dict] | None = None,
            batch=None, step: float = SENS_STEP) -> dict:
    """Run one tolerance analysis. f(point) -> {measure: value}; or give
    batch(points) -> [results] to evaluate a list at once (SPICE). Returns
    {analysis, params, nominal, sensitivity, top3, measures: {m: {min, max,
    ...}}, yield?, runs}. Every analysis reports nominal + sensitivities;
    wca adds EVA and RSS, corners the full factorial (EVA's vertices when
    there are more than MAX_CORNER_PARAMS), mc the n-draw statistics."""
    if analysis not in ANALYSES:
        raise ValueError(f"analysis must be one of {ANALYSES}")
    params = {k: p for k, p in params.items() if p.hi > p.lo}
    spts = sensitivity_points(params, step)
    sres = _points_eval(f, spts, batch)
    runs = len(spts)
    nominal = sres[0]
    sens = sensitivities_from(params, sres, step)
    out: dict = {"analysis": analysis,
                 "params": {k: p.as_dict() for k, p in params.items()},
                 "nominal": nominal,
                 "sensitivity": {m: {k: {"d": v["d"], "norm": v["norm"]}
                                     for k, v in sm.items()}
                                 for m, sm in sens.items()},
                 "top3": {m: top(sm) for m, sm in sens.items()},
                 "measures": {}}
    if analysis == "wca":
        ev = eva_points(params, sens)
        order = [(m, i) for m in ev for i in (0, 1)]
        res = _points_eval(f, [ev[m][i] for m, i in order], batch)
        runs += len(order)
        r = rss(params, sens, nominal)
        for (m, i), rv in zip(order, res):
            row = out["measures"].setdefault(m, {"nom": nominal[m]})
            row["eva_" + ("min" if i == 0 else "max")] = rv.get(m)
        for m, rr in r.items():
            row = out["measures"].setdefault(m, {"nom": nominal[m]})
            row.update(rss_sigma=rr["sigma"], rss_min=rr["min"],
                       rss_max=rr["max"])
            row["min"], row["max"] = row.get("eva_min"), row.get("eva_max")
            if row["min"] is not None and row["max"] is not None \
                    and row["min"] > row["max"]:
                row["min"], row["max"] = row["max"], row["min"]
    elif analysis == "corners":
        if len(params) <= MAX_CORNER_PARAMS:
            pts = corner_points(params)
            out["corners"] = "full"
        else:
            ev = eva_points(params, sens)
            pts = [v for pair in ev.values() for v in pair]
            out["corners"] = "eva"
        res = _points_eval(f, pts, batch)
        runs += len(pts)
        for m in nominal:
            vals = [r.get(m) for r in res]
            good = [v for v in vals if v is not None and math.isfinite(v)]
            row = {"nom": nominal[m], "n": len(good)}
            if good:
                row["min"], row["max"] = min(good), max(good)
                row["min_at"] = pts[vals.index(row["min"])]
                row["max_at"] = pts[vals.index(row["max"])]
            out["measures"][m] = row
        out["samples"] = res
    else:  # mc
        pts = mc_points(params, n, seed)
        res = _points_eval(f, pts, batch)
        runs += len(pts)
        out.update(n=n, seed=seed)
        for m in nominal:
            out["measures"][m] = {"nom": nominal[m],
                                  **stats([r.get(m) for r in res])}
        out["samples"] = res
    if bounds and analysis in ("mc", "corners"):
        res = out["samples"]
        per = {}
        ok_all = [True] * len(res)
        for b in bounds:
            m = b["measure"].lower()
            hits = [_within(b, r.get(m)) for r in res]
            per[b["measure"]] = sum(hits) / len(hits) if hits else 0.0
            ok_all = [a and h for a, h in zip(ok_all, hits)]
        out["yield"] = {"all": sum(ok_all) / len(ok_all) if ok_all else 0.0,
                        "per_measure": per}
    out["runs"] = runs
    return out


def band_violations(bounds: list[dict], result: dict) -> list[tuple]:
    """(bound, edge, value) for every bound the analysed band crosses:
    the band's min below a bound's min or its max above a bound's max."""
    bad = []
    for b in bounds:
        row = result["measures"].get(b["measure"].lower())
        if not row or row.get("min") is None:
            continue
        if b.get("min") is not None and row["min"] < b["min"]:
            bad.append((b, "min", row["min"]))
        if b.get("max") is not None and row["max"] > b["max"]:
            bad.append((b, "max", row["max"]))
    return bad
