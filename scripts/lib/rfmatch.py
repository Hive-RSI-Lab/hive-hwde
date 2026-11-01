"""rfmatch.py - impedance-matching networks: synthesis, real parts, return loss.

A network is a ladder written from the LOAD toward the SOURCE: a list of
elements {"where": "shunt" | "series", "kind": "L" | "C" | "R", "value":
H | F | ohm} (R, ideal, only for a board's populated network).
The source is a real Z0 (50 ohm by default); the load is a `Load` (a point
impedance at f0, or a .s1p).

  l_sections(zl, z0, f)     every valid 2-element L-section (Pozar 5.1:
                            shunt-first when G_L <= 1/Z0, series-first when
                            R_L <= Z0; each has two signs, so up to four)
  pi_networks / t_networks  3-element networks for a loaded Q: two back-to-
                            back L-sections through a virtual resistance
                            R_v = R_high / (Q^2 + 1) (pi) or R_low (Q^2 + 1)
                            (T), Bowick ch. 4; four sign choices each
  zin / gamma / rl_db       the ladder's input impedance, reflection and
                            return loss over frequency, ideal or with the
                            parts' parasitics (`PartModel`)
  snap                      the E-series combination around the ideal values
                            with the best worst-case RL across the band
  deck                      an ngspice AC deck of many copies of the network
                            at one frequency (the tolerance Monte Carlo runs
                            through it; lib/tolerance.py is the engine)

Parasitics (`PartModel`, the generic 0402 one unless a part model says
otherwise): an inductor is (R_s + jwL) || C_p with R_s = wL/Q (constant Q)
and C_p from its self-resonance; a capacitor is ESR + jw ESL + 1/(jwC).
Generic 0402 numbers, ASSUMED (rough typical values for a multilayer chip
inductor and a C0G chip cap, not fitted to or checked against any datasheet;
give a part model for a real part): inductor Q 20, SRF ~ 11 GHz / sqrt(L/nH);
cap ESL 0.4 nH, ESR 0.2 ohm.

A point load (R + jX at f0) is modelled across the band as R in series with
the inductor or capacitor that gives X at f0 (`Load(..., model="lumped")`,
the default) or as a constant Z (`model="const"`).
"""
from __future__ import annotations

import cmath
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

TWO_PI = 2 * math.pi
Z0_DEFAULT = 50.0

# --------------------------------------------------------------- values

_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
       "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}


def parse_si(text, unit: str = "") -> float:
    """'2.45G', '500MHz', '3.9n', '3n9', '0.92pF', '1e-12' -> a float.
    Mega is M (a frequency, a schematic value); milli is m."""
    s = str(text).strip().replace(" ", "")
    for u in sorted({unit, "Hz", "H", "F", "ohm", "Ω"} - {""}, key=len,
                    reverse=True):
        if s.endswith(u) and len(s) > len(u):
            s = s[:-len(u)]
            break
    try:
        return float(s)
    except ValueError:
        pass
    m = re.fullmatch(r"([-+]?\d*\.?\d+)\s*([fpnuµmkKMGT])(\d*)", s)
    if m:
        num = m.group(1) + ("." + m.group(3) if m.group(3) else "")
        if m.group(3) and "." in m.group(1):
            raise ValueError(f"not a value: {text!r}")
        return float(num) * _SI[m.group(2)]
    raise ValueError(f"not a value: {text!r}")


def parse_z(text) -> tuple[complex, float | None]:
    """'200-j100', '200-100j', '35+j20@2.45G', '50' -> (Z, f or None)."""
    s = str(text).strip().replace(" ", "")
    f = None
    if "@" in s:
        s, ftxt = s.split("@", 1)
        f = parse_si(ftxt, "Hz")
    s = s.replace("i", "j")
    m = re.fullmatch(r"([-+]?\d*\.?\d+(?:e[-+]?\d+)?)?(?:([-+])j"
                     r"(\d*\.?\d+(?:e[-+]?\d+)?)|([-+]\d*\.?\d+"
                     r"(?:e[-+]?\d+)?)j)?", s, re.I)
    if not m or not s:
        raise ValueError(f"not an impedance: {text!r} (want R+jX, R-jX)")
    r = float(m.group(1) or 0.0)
    if m.group(3) is not None:
        x = float(m.group(3)) * (-1 if m.group(2) == "-" else 1)
    elif m.group(4) is not None:
        x = float(m.group(4))
    else:
        x = 0.0
    return complex(r, x), f


def fmt_si(v: float, unit: str, digits: int = 3) -> str:
    """3.9e-9, 'H' -> '3.9nH'."""
    if v == 0:
        return f"0{unit}"
    for p, mult in (("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1.0),
                    ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12),
                    ("f", 1e-15)):
        if abs(v) >= mult * 0.9995:
            return f"{float(f'{v / mult:.{digits}g}'):g}{p}{unit}"
    return f"{v:.{digits}g}{unit}"


# E-series (IEC 60063). E3..E24 are the historic lists; E48+ are computed.
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6,
       3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]


def e_series(name: str) -> list[float]:
    n = int(str(name).upper().lstrip("E"))
    if n in (3, 6, 12, 24):
        return E24[::24 // n]
    if n in (48, 96, 192):
        return [round(10 ** (i / n), 2) for i in range(n)]
    raise ValueError(f"unknown E-series {name!r} (E3, E6, E12, E24, E48, "
                     "E96, E192)")


def e_neighbours(v: float, series: list[float], k: int = 1) -> list[float]:
    """The series values nearest v: the one below, the one above, and k-1
    more on each side (log spacing)."""
    dec = math.floor(math.log10(v))
    vals = sorted({round(m * 10 ** d, 15) for d in (dec - 1, dec, dec + 1)
                   for m in series})
    below = [x for x in vals if x <= v * (1 + 1e-9)]
    above = [x for x in vals if x > v * (1 + 1e-9)]
    return below[-k:] + above[:k]


def e_nearest(v: float, series: list[float]) -> float:
    return min(e_neighbours(v, series, 1), key=lambda x: abs(math.log(x / v)))


# --------------------------------------------------------------- loads


@dataclass
class Load:
    """What the network drives: a point impedance at f0 or a table."""
    z0: complex | None = None
    f0: float | None = None
    model: str = "lumped"           # lumped | const (point loads)
    freqs: list = field(default_factory=list)
    zs: list = field(default_factory=list)
    source: str = ""

    def z(self, f: float) -> complex:
        if self.freqs:
            return _interp_z(self.freqs, self.zs, f)
        z = self.z0
        if self.model == "const" or not self.f0 or z.imag == 0:
            return z
        if z.imag > 0:          # a series L at f0
            return complex(z.real, z.imag * f / self.f0)
        return complex(z.real, z.imag * self.f0 / f)  # a series C

    def at_f0(self, f0: float) -> complex:
        return self.z(f0)


def _interp_z(freqs, zs, f):
    if f <= freqs[0]:
        return zs[0]
    if f >= freqs[-1]:
        return zs[-1]
    lo, hi = 0, len(freqs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if freqs[mid] <= f:
            lo = mid
        else:
            hi = mid
    t = (f - freqs[lo]) / (freqs[hi] - freqs[lo])
    return zs[lo] + (zs[hi] - zs[lo]) * t


def read_s1p(path) -> Load:
    """A Touchstone v1 one-port: '# <Hz|kHz|MHz|GHz> <S|Z|Y> <RI|MA|DB> R
    <z0>' then 'f a b' rows -> a table Load of impedances."""
    unit, par, fmt, ref = 1e9, "S", "MA", 50.0
    mult = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
    freqs, zs = [], []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            tok = line[1:].upper().split()
            for i, t in enumerate(tok):
                if t in mult:
                    unit = mult[t]
                elif t in ("S", "Z", "Y"):
                    par = t
                elif t in ("RI", "MA", "DB"):
                    fmt = t
                elif t == "R" and i + 1 < len(tok):
                    ref = float(tok[i + 1])
            continue
        v = [float(x) for x in line.split()]
        if len(v) < 3:
            continue
        a, b = v[1], v[2]
        if fmt == "RI":
            x = complex(a, b)
        elif fmt == "MA":
            x = cmath.rect(a, math.radians(b))
        else:
            x = cmath.rect(10 ** (a / 20), math.radians(b))
        if par == "S":
            z = ref * (1 + x) / (1 - x) if x != 1 else complex(1e12, 0)
        elif par == "Z":
            z = x * ref
        else:
            z = ref / x if x else complex(1e12, 0)
        freqs.append(v[0] * unit)
        zs.append(z)
    if not freqs:
        raise ValueError(f"no data rows in {path}")
    order = sorted(range(len(freqs)), key=freqs.__getitem__)
    return Load(freqs=[freqs[i] for i in order], zs=[zs[i] for i in order],
                source=str(path))


def write_s1p(freqs, zs, z0: float = 50.0) -> str:
    out = [f"# Hz S RI R {z0:g}"]
    for f, z in zip(freqs, zs):
        g = (z - z0) / (z + z0)
        out.append(f"{f:.9g} {g.real:.12g} {g.imag:.12g}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------- parts


@dataclass
class PartModel:
    """Parasitics of the chip parts. kind 'ideal' turns them off. per_value
    maps a value (float, H or F) to overrides {srf_hz, q, esr_ohm, esl_h}."""
    kind: str = "generic0402"
    l_q: float = 20.0
    l_srf_1nh_hz: float = 11e9      # SRF of a 1 nH part; ~ 1/sqrt(L)
    c_esl_h: float = 0.4e-9
    c_esr_ohm: float = 0.2
    per_value: dict = field(default_factory=dict)

    def _over(self, kind, value):
        for v, o in self.per_value.items():
            if o.get("kind", kind) == kind and abs(v - value) <= 1e-3 * value:
                return o
        return {}

    def params(self, kind: str, value: float) -> dict:
        """The parasitics one part gets, with its SRF."""
        if self.kind == "ideal":
            return {"srf_hz": None}
        o = self._over(kind, value)
        if kind == "L":
            srf = o.get("srf_hz") or self.l_srf_1nh_hz / math.sqrt(
                value / 1e-9)
            return {"q": o.get("q", self.l_q), "srf_hz": srf,
                    "cp_f": 1 / ((TWO_PI * srf) ** 2 * value),
                    "esr_ohm": o.get("esr_ohm")}
        esl = o.get("esl_h", self.c_esl_h)
        return {"esl_h": esl, "esr_ohm": o.get("esr_ohm", self.c_esr_ohm),
                "srf_hz": o.get("srf_hz") or 1 / (TWO_PI * math.sqrt(
                    esl * value))}


IDEAL = PartModel(kind="ideal")
GENERIC_0402 = PartModel()


def element_z(kind: str, value: float, f: float,
              model: PartModel = IDEAL) -> complex:
    w = TWO_PI * f
    if kind == "R":
        return complex(value, 0)
    p = model.params(kind, value)
    if kind == "L":
        zl = complex(0, w * value)
        if model.kind == "ideal":
            return zl
        rs = p["esr_ohm"] if p.get("esr_ohm") is not None else \
            w * value / p["q"]
        zs = zl + rs
        yc = complex(0, w * p["cp_f"])
        return zs / (1 + zs * yc)
    zc = complex(0, -1 / (w * value))
    if model.kind == "ideal":
        return zc
    return zc + complex(p["esr_ohm"], w * p["esl_h"])


# --------------------------------------------------------------- eval


def zin(elements: list[dict], zl: complex, f: float,
        model: PartModel = IDEAL) -> complex:
    """The ladder's input impedance at the source end."""
    z = zl
    for e in elements:
        ze = element_z(e["kind"], e["value"], f, model)
        if e["where"] == "series":
            z = z + ze
        else:
            z = 1 / (1 / z + 1 / ze) if z != 0 else 0j
    return z


def gamma(z: complex, z0: float = Z0_DEFAULT) -> complex:
    return (z - z0) / (z + z0)


def rl_db(g: complex) -> float:
    m = abs(g)
    return 200.0 if m < 1e-10 else -20 * math.log10(m)


def band(f0: float, bw: float = 0.0, n: int = 21) -> list[float]:
    if not bw or n < 2:
        return [f0]
    return [f0 - bw / 2 + bw * i / (n - 1) for i in range(n)]


def rl_band(elements, load: Load, freqs, z0=Z0_DEFAULT,
            model: PartModel = IDEAL) -> dict:
    rls = [rl_db(gamma(zin(elements, load.z(f), f, model), z0))
           for f in freqs]
    i = min(range(len(rls)), key=rls.__getitem__)
    return {"min_db": round(rls[i], 3), "worst_hz": freqs[i],
            "mean_db": round(sum(rls) / len(rls), 3), "per_f": rls}


# --------------------------------------------------------------- synthesis


def _element_series(x: float, f: float) -> dict:
    w = TWO_PI * f
    if x >= 0:
        return {"where": "series", "kind": "L", "value": x / w, "x": x}
    return {"where": "series", "kind": "C", "value": -1 / (w * x), "x": x}


def _element_shunt(b: float, f: float) -> dict:
    w = TWO_PI * f
    if b >= 0:
        return {"where": "shunt", "kind": "C", "value": b / w, "b": b}
    return {"where": "shunt", "kind": "L", "value": -1 / (w * b), "b": b}


def _tiny(v: float) -> bool:
    return abs(v) < 1e-12


def l_sections(zl: complex, z0: float, f: float) -> list[dict]:
    """Every valid L-section from zl to the real z0 at f, Pozar sec. 5.1.
    Elements are listed from the load; a reactance that comes out zero is
    left out (a 1-element match)."""
    rl, xl = zl.real, zl.imag
    if rl <= 0:
        raise ValueError(f"load resistance must be positive, got {zl}")
    out = []
    mag2 = rl * rl + xl * xl
    # shunt next to the load, series toward the source: G_L <= 1/Z0
    arg = rl * (mag2 - z0 * rl)
    if arg >= -1e-12 * mag2 * rl:
        root = math.sqrt(max(arg, 0.0) / z0)
        for sgn in (1, -1):
            b = (xl + sgn * root) / mag2
            if _tiny(b):
                x = -xl  # Re(zl) is already z0: a series element only
                els = [] if _tiny(x) else [_element_series(x, f)]
            else:
                x = 1 / b + xl * z0 / rl - z0 / (b * rl)
                els = [_element_shunt(b, f)]
                if not _tiny(x):
                    els.append(_element_series(x, f))
            out.append({"topology": "L", "form": "shunt-series",
                        "sign": sgn, "elements": els})
    # series next to the load, shunt toward the source: R_L <= Z0
    if rl <= z0 * (1 + 1e-12):
        root = math.sqrt(max(rl * (z0 - rl), 0.0))
        for sgn in (1, -1):
            x = sgn * root - xl
            b = sgn * math.sqrt(max((z0 - rl) / rl, 0.0)) / z0
            els = []
            if not _tiny(x):
                els.append(_element_series(x, f))
            if not _tiny(b):
                els.append(_element_shunt(b, f))
            out.append({"topology": "L", "form": "series-shunt",
                        "sign": sgn, "elements": els})
    return _dedupe(out, zl, z0, f)


def _dedupe(nets, zl, z0, f):
    seen, out = set(), []
    for n in nets:
        key = tuple((e["where"], e["kind"], round(e["value"], 18))
                    for e in n["elements"])
        if key in seen:
            continue
        # every solution must actually match (guards the closed forms)
        g = abs(gamma(zin(n["elements"], zl, f), z0))
        if g > 1e-6:
            continue
        seen.add(key)
        out.append(n)
    return out


def _merge(a: dict, b: dict, f: float) -> dict:
    """Two adjacent elements of the same position combined (pi: the two
    series reactances; T: the two shunt susceptances)."""
    if a["where"] == "series":
        return _element_series(a["x"] + b["x"], f)
    return _element_shunt(a["b"] + b["b"], f)


def q_min(zl: complex, z0: float, topology: str = "pi") -> float:
    """The smallest loaded Q a 3-element network can have here: that of the
    L-section between z0 and the load's parallel-equivalent resistance (pi)
    or its series resistance (T)."""
    rp = abs(zl) ** 2 / zl.real if topology == "pi" else zl.real
    rh, rlo = max(rp, z0), min(rp, z0)
    return math.sqrt(max(rh / rlo - 1, 0.0))


def pi_networks(zl: complex, z0: float, f: float, q: float) -> list[dict]:
    """Pi (shunt-series-shunt) networks of loaded Q q: load -> R_v (a
    shunt-first L) then R_v -> z0 (series-first), the series arms merged.
    R_v = R_high / (q^2 + 1) with R_high the larger of z0 and the load's
    parallel resistance; it must sit below both."""
    rp = abs(zl) ** 2 / zl.real
    rv = max(rp, z0) / (q * q + 1)
    if rv >= min(rp, z0):
        raise ValueError(f"Q {q:g} too low for a pi here: needs Q > "
                         f"{q_min(zl, z0):.3g}")
    out = []
    for a in l_sections(zl, rv, f):
        if a["form"] != "shunt-series" or len(a["elements"]) != 2:
            continue
        for b in l_sections(complex(rv, 0), z0, f):
            if b["form"] != "series-shunt" or len(b["elements"]) != 2:
                continue
            ser = _merge(a["elements"][1], b["elements"][0], f)
            els = [a["elements"][0], ser, b["elements"][1]]
            out.append({"topology": "pi", "q": q, "r_virtual": rv,
                        "form": "shunt-series-shunt",
                        "sign": [a["sign"], b["sign"]], "elements": els})
    return _dedupe(out, zl, z0, f)


def t_networks(zl: complex, z0: float, f: float, q: float) -> list[dict]:
    """T (series-shunt-series) networks of loaded Q q: load -> R_v (a
    series-first L) then R_v -> z0 (shunt-first), the shunt arms merged.
    R_v = R_low (q^2 + 1), above both z0 and the load's resistance."""
    rv = min(zl.real, z0) * (q * q + 1)
    if rv <= max(zl.real, z0):
        need = math.sqrt(max(zl.real, z0) / min(zl.real, z0) - 1)
        raise ValueError(f"Q {q:g} too low for a T here: needs Q > "
                         f"{need:.3g}")
    out = []
    for a in l_sections(zl, rv, f):
        if a["form"] != "series-shunt" or len(a["elements"]) != 2:
            continue
        for b in l_sections(complex(rv, 0), z0, f):
            if b["form"] != "shunt-series" or len(b["elements"]) != 2:
                continue
            sh = _merge(a["elements"][1], b["elements"][0], f)
            els = [a["elements"][0], sh, b["elements"][1]]
            out.append({"topology": "T", "q": q, "r_virtual": rv,
                        "form": "series-shunt-series",
                        "sign": [a["sign"], b["sign"]], "elements": els})
    return _dedupe(out, zl, z0, f)


# --------------------------------------------------------------- snapping


def snap(elements: list[dict], load: Load, freqs, z0=Z0_DEFAULT,
         series: str = "E24", model: PartModel = GENERIC_0402,
         k: int = 1) -> list[dict]:
    """Real values: every combination of each element's E-series
    neighbours (k each side), the one with the best worst-case RL across
    the band with the parts' parasitics (ties: the smaller log error)."""
    vals = e_series(series)
    cands = [e_neighbours(e["value"], vals, k) for e in elements]
    best, best_key = None, None
    for combo in _product(cands):
        els = [dict(e, value=v) for e, v in zip(elements, combo)]
        r = rl_band(els, load, freqs, z0, model)["min_db"]
        err = sum(abs(math.log(v / e["value"])) for e, v in
                  zip(elements, combo))
        key = (-round(r, 6), err)
        if best_key is None or key < best_key:
            best, best_key = els, key
    return [{k2: v for k2, v in e.items() if k2 not in ("x", "b")}
            for e in best]


def _product(lists):
    if not lists:
        yield ()
        return
    for v in lists[0]:
        for rest in _product(lists[1:]):
            yield (v,) + rest


# --------------------------------------------------------------- tolerance


def default_tol(kind: str, value: float, tol_l=None, tol_c=None) -> tuple:
    """(lo, hi) absolute edges for a chip part. Defaults are ASSUMED, not
    read from a part: C under 10 pF +-0.1 pF, else +-5 %; L +-5 %. Pass
    --tol-l / --tol-c with the real parts' tolerances."""
    def rel(spec):
        s = str(spec).strip()
        if s.endswith("%"):
            t = float(s[:-1]) / 100
            return value * (1 - t), value * (1 + t)
        a = parse_si(s, "F" if kind == "C" else "H")
        return value - a, value + a
    if kind == "C":
        if tol_c is not None:
            return rel(tol_c)
        if value < 10e-12:
            return max(value - 0.1e-12, value * 0.05), value + 0.1e-12
        return value * 0.95, value * 1.05
    if tol_l is not None:
        return rel(tol_l)
    return value * 0.95, value * 1.05


def elements_with(elements, point: dict) -> list[dict]:
    """The ladder with element i's value from point['e<i>'] when given."""
    return [dict(e, value=point.get(f"e{i}", e["value"]))
            for i, e in enumerate(elements)]


# --------------------------------------------------------------- ngspice


def _spice_element(name, kind, value, f, model, a, b, lines):
    """One part (with parasitics) between nodes a and b."""
    if kind == "R":
        lines.append(f"R{name} {a} {b} {max(value, 1e-6):.12g}")
        return
    p = model.params(kind, value)
    w = TWO_PI * f
    if kind == "L":
        if model.kind == "ideal":
            lines.append(f"L{name} {a} {b} {value:.12g}")
            return
        rs = p["esr_ohm"] if p.get("esr_ohm") is not None else \
            w * value / p["q"]
        lines += [f"L{name} {a} {a}_{name} {value:.12g}",
                  f"R{name} {a}_{name} {b} {rs:.12g}",
                  f"C{name}p {a} {b} {p['cp_f']:.12g}"]
        return
    if model.kind == "ideal":
        lines.append(f"C{name} {a} {b} {value:.12g}")
        return
    lines += [f"C{name} {a} {a}_{name}a {value:.12g}",
              f"L{name}s {a}_{name}a {a}_{name}b {p['esl_h']:.12g}",
              f"R{name}s {a}_{name}b {b} {p['esr_ohm']:.12g}"]


def deck(variants: list[list[dict]], zl: complex, f: float,
         z0: float = Z0_DEFAULT, model: PartModel = IDEAL) -> str:
    """One AC deck at f holding every variant as its own copy: V (1 V) ->
    R z0 -> port p<k> -> the ladder -> the load (R + the L or C that gives
    Im zl at f). Then Gamma_k = 2 v(p<k>) - 1."""
    w = TWO_PI * f
    lines = [f"* rfmatch: {len(variants)} variants at {f:.9g} Hz"]
    for k, els in enumerate(variants):
        lines += [f"V{k} s{k} 0 DC 0 AC 1", f"R{k}s s{k} p{k} {z0:.12g}"]
        # walk from the source: the ladder is listed from the load
        node = f"p{k}"
        chain = list(reversed(els))
        for j, e in enumerate(chain):
            name = f"{k}x{j}"
            if e["where"] == "shunt":
                _spice_element(name, e["kind"], e["value"], f, model, node,
                               "0", lines)
            else:
                nxt = f"n{k}x{j}"
                _spice_element(name, e["kind"], e["value"], f, model, node,
                               nxt, lines)
                node = nxt
        r = max(zl.real, 1e-6)
        if abs(zl.imag) < 1e-12:
            lines.append(f"R{k}l {node} 0 {r:.12g}")
        else:
            lines.append(f"R{k}l {node} l{k} {r:.12g}")
            if zl.imag > 0:
                lines.append(f"L{k}l l{k} 0 {zl.imag / w:.12g}")
            else:
                lines.append(f"C{k}l l{k} 0 {-1 / (w * zl.imag):.12g}")
    lines += [f".ac lin 1 {f:.12g} {f:.12g}", ".end"]
    return "\n".join(lines) + "\n"
