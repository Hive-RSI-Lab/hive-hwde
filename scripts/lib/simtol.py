"""simtol.py - tolerance analysis of a sim_run testbench deck.

A bench's bounds sidecar may be an object instead of a bare list:
  {"bounds": [...the usual list...],
   "tolerance": {"analysis": "mc"|"corners"|"wca", "n": 500, "seed": 1,
                 "dist": "uniform"|"gauss", "min_yield": 1.0,
                 "vary": ["R1", "C1", "vref"],          (default: every R/C/L)
                 "tolerances": {"R1": "1%", "C1": {"tol": "X7R", "vdc": 3.3},
                                "vref": 0.02}}}
A list sidecar (or one without "tolerance") runs at nominal only, as before.

What varies and by how much, per element/parameter name (case-insensitive):
  1. sidecar "tolerances";
  2. deck `.param tol_<name>=<fraction>` (tol_R / tol_C / tol_L set a kind
     default for the deck);
  3. the element line's trailing comment as a BOM value field
     (`R1 a b 10k ; 1% 0603`, `C3 x 0 100n $ X7R 16V`);
  4. tolerance.DEFAULTS by kind (assumed, listed in the report).
A `.param` is varied only when a tolerance names it (1, 2). Values the
deck writes as {expressions} or with model parameters are left alone.
"""
from __future__ import annotations

import math
import re

import tolerance

SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "mil": 25.4e-6,
          "m": 1e-3, "u": 1e-6, "µ": 1e-6, "n": 1e-9, "p": 1e-12,
          "f": 1e-15, "a": 1e-18}
_NUM = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)"
                  r"(meg|mil|[tgkmuµnpfa])?[a-zA-Z]*$", re.I)
_PARAM = re.compile(r"^\s*\.param\s+(.*)$", re.I)
_ASSIGN = re.compile(r"([A-Za-z_]\w*)\s*=\s*([^\s=]+)")


def spice_number(tok: str) -> float | None:
    """SPICE number with scale suffix ('4.7k', '100n', '2meg', '1m' is
    milli) -> float, or None."""
    m = _NUM.match(tok.strip())
    if not m:
        return None
    v = float(m.group(1))
    s = (m.group(2) or "").lower()
    return v * SUFFIX.get(s, 1.0)


def _split_comment(line: str) -> tuple[str, str]:
    for mark in (";", "$ "):
        i = line.find(mark)
        if i > 0:
            return line[:i], line[i + len(mark.strip()):]
    return line, ""


def scan(text: str) -> dict:
    """The deck's tunable values: {"elements": {NAME: {line, tok, nom,
    kind, comment}}, "params": {name: {line, nom}}, "tol_params": {...}}."""
    elements, params, tolp = {}, {}, {}
    for i, raw in enumerate(text.splitlines()):
        s = raw.strip()
        if not s or s.startswith("*"):
            continue
        m = _PARAM.match(s)
        if m:
            body, _ = _split_comment(m.group(1))
            for name, val in _ASSIGN.findall(body):
                v = spice_number(val)
                if v is None:
                    continue
                if name.lower().startswith("tol_"):
                    tolp[name[4:].lower()] = v
                else:
                    params[name.lower()] = {"line": i, "nom": v,
                                            "name": name}
            continue
        kind = s[0].upper()
        if kind not in "RCL":
            continue
        body, comment = _split_comment(s)
        toks = body.split()
        if len(toks) < 4:
            continue
        v = spice_number(toks[3])
        if v is None:
            continue
        elements[toks[0].upper()] = {"line": i, "tok": 3, "nom": v,
                                     "kind": kind, "comment": comment.strip()}
    return {"elements": elements, "params": params, "tol_params": tolp}


def build_params(text: str, cfg: dict) -> tuple[dict, dict, list[str]]:
    """-> (params {name: tolerance.Param}, scan info, assumed names)."""
    info = scan(text)
    dist = cfg.get("dist", "uniform")
    specs = {k.lower(): v for k, v in (cfg.get("tolerances") or {}).items()}
    vary = cfg.get("vary")
    vary = {v.lower() for v in vary} if vary else None
    params: dict = {}
    assumed: list[str] = []
    for name, e in info["elements"].items():
        key = name.lower()
        if vary is not None and key not in vary:
            continue
        if key in specs:
            p = tolerance.spec_param(name, e["kind"], e["nom"], specs[key],
                                     value_text=e["comment"], dist=dist)
        elif key in info["tol_params"]:
            t = info["tol_params"][key]
            p = tolerance.make_param(name, e["nom"], t, t, dist,
                                     f".param tol_{name}")
        elif e["comment"] and tolerance.parse_spec(e["comment"], bare=False):
            p = tolerance.part_tolerance(e["kind"], e["comment"], None,
                                         nom=e["nom"], dist=dist, name=name)
            p.source = f"deck comment {e['comment']!r}"
        elif e["kind"].lower() in info["tol_params"]:
            t = info["tol_params"][e["kind"].lower()]
            p = tolerance.make_param(name, e["nom"], t, t, dist,
                                     f".param tol_{e['kind']}")
        else:
            p = tolerance.part_tolerance(e["kind"], e["comment"], None,
                                         nom=e["nom"], dist=dist, name=name)
            if p.assumed:
                assumed.append(name)
        params[name] = p
    for key, pr in info["params"].items():
        if vary is not None and key not in vary:
            continue
        if key in specs:
            p = tolerance.spec_param(pr["name"], "V", pr["nom"], specs[key],
                                     dist=dist)
        elif key in info["tol_params"]:
            t = info["tol_params"][key]
            p = tolerance.make_param(pr["name"], pr["nom"], t, t, dist,
                                     f".param tol_{pr['name']}")
        else:
            continue
        params["." + key] = p
    missing = sorted((vary or set()) - {k.lower().lstrip(".")
                                        for k in params})
    if missing:
        raise ValueError(f"vary names nothing in the deck: {missing}")
    return params, info, assumed


def render(text: str, info: dict, point: dict) -> str:
    """The deck with every varied value replaced by point's."""
    lines = text.splitlines()
    for name, v in point.items():
        if name.startswith("."):
            key = name[1:]
            i = info["params"][key]["line"]
            pname = info["params"][key]["name"]
            lines[i] = re.sub(rf"(\b{re.escape(pname)}\s*=\s*)[^\s=]+",
                              lambda m: m.group(1) + f"{v:.10g}", lines[i],
                              count=1)
            continue
        e = info["elements"][name]
        body, comment = _split_comment(lines[e["line"]].strip())
        toks = body.split()
        toks[e["tok"]] = f"{v:.10g}"
        lines[e["line"]] = " ".join(toks)
    return "\n".join(lines) + "\n"


def check_cfg(cfg, where: str) -> dict:
    """Validate a sidecar "tolerance" block -> cfg with defaults."""
    import checklib
    if not isinstance(cfg, dict):
        raise checklib.CheckError(f"{where}: 'tolerance' must be an object")
    out = {"analysis": "wca", "n": 200, "seed": 1, "dist": "uniform",
           "min_yield": 1.0, **cfg}
    if out["analysis"] not in tolerance.ANALYSES:
        raise checklib.CheckError(
            f"{where}: analysis must be one of {tolerance.ANALYSES}")
    if out["dist"] not in tolerance.DISTS:
        raise checklib.CheckError(
            f"{where}: dist must be one of {tolerance.DISTS}")
    for k in ("n", "seed"):
        if isinstance(out[k], bool) or not isinstance(out[k], int) \
                or out[k] < (1 if k == "n" else 0):
            raise checklib.CheckError(f"{where}: '{k}' must be an integer")
    y = out["min_yield"]
    if isinstance(y, bool) or not isinstance(y, (int, float)) \
            or not 0 <= y <= 1 or not math.isfinite(y):
        raise checklib.CheckError(f"{where}: min_yield must be in [0, 1]")
    return out
