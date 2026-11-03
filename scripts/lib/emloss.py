"""emloss.py - wideband causal loss for board S-parameters (#553).

A full-wave deck here is lossless on purpose (lib/emboard's header): a
conductivity is a loss tangent at ONE frequency, so it is a frequency-flat
loss and reads 0.3 dB on 50 mm at 100 MHz. This module holds the loss, as
per-unit-length line parameters, and puts it back on a lossless S:

  Dielectric: Djordjevic-Sarkar (DS). A flat distribution of Debye
    relaxations between f1 and f2, set by er and tan d at one frequency.
    It is causal by construction and its er falls with frequency.
  Conductor: the series impedance of a skin-effect conductor,
    Rdc sqrt(1 + j f/fc). It is analytic in the right half plane, so causal,
    gives Rdc at low f and (1+j) Rs G at high f. G is Wheeler's incremental
    inductance, (1/eta0) dZ_air/dn with every conductor wall receded by dn.
  Roughness: Hammerstad (rms height) or Huray (cannonball) K(f) multiplies R.
    A real K is not causal on its own, so the reactance that goes with the
    extra R comes from the Kramers-Kronig integral (`causal_x`).

`section` measures a uniform line with lib/impedance's field solver and
`line_gamma_z` gives its gamma and Z0 per frequency. `add_line_loss` is the
post-processing: at each port it cascades lossy(l) * lossless(l)^-1 onto a
lossless N-port. On a uniform line split l/2, l/2 that gives the lossy line
exactly, dispersion included. It knows only about the uniform sections it is
given: a stub or a resonator off that path keeps its lossless Q, which is what
`sections_for_ports` warns about and `dielectric_props` (dielectric loss in
the deck itself, Lorentz poles fitted to DS) is for.

Not modelled: solder mask loss, the mask's er, radiation in the correction,
and roughness per surface (K is applied to the whole R). Foil rms heights are
typical values, not any fab's data: fabs publish no foil class, so `FOILS`
"std" is an assumption and results say so.

Lengths mm, frequencies Hz, alpha Np/m unless named otherwise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

C0 = 299792458.0
MU0 = 4e-7 * math.pi
EPS0 = 1 / (MU0 * C0 ** 2)
ETA0 = MU0 * C0
SIGMA_CU = 5.8e7        # S/m, annealed copper (IACS)
NP_TO_DB = 20 / math.log(10)
DF_F_REF = 1e9          # a stackups.yaml loss_tangent is at 1 GHz unless the row says

# Foil classes, rms height um (Hammerstad) and the cannonball's a um and surface
# ratio (Huray). Typical published ranges; no fab here publishes its foil.
FOILS = {
    "smooth": {"rq_um": 0.0, "a_um": 0.0, "sr": 0.0},
    "hvlp": {"rq_um": 0.4, "a_um": 0.3, "sr": 1.0},
    "vlp": {"rq_um": 0.8, "a_um": 0.4, "sr": 1.5},
    "rtf": {"rq_um": 1.2, "a_um": 0.5, "sr": 2.0},
    "std": {"rq_um": 2.0, "a_um": 0.5, "sr": 3.0},
}
FOIL_DEFAULT = "std"


def df_ref_hz(material: str | None) -> float:
    """The frequency a board's tan d is quoted at. KiCad's stackup block has
    no field for it: Rogers quotes 10 GHz, stackups.yaml's FR-4 rows 1 GHz."""
    return 1e10 if material and "rogers" in material.lower() else DF_F_REF


# ================================================================ dielectric

@dataclass(frozen=True)
class DS:
    """Djordjevic-Sarkar: eps(f) = einf + de/(m2-m1) log10((f2 + jf)/(f1 + jf)),
    meaning eps' - j eps'', with eps(f_ref) = er (1 - j tand)."""
    er: float
    tand: float
    f_ref: float = DF_F_REF
    f1: float = 1e3
    f2: float = 1e12

    def _k(self, f):
        f = np.asarray(f, dtype=float)
        return np.log((self.f2 + 1j * f) / (self.f1 + 1j * f)) / math.log(self.f2 / self.f1)

    @property
    def de(self) -> float:
        return float(-self.er * self.tand / complex(self._k(self.f_ref)).imag)

    @property
    def einf(self) -> float:
        return float(self.er - self.de * complex(self._k(self.f_ref)).real)

    def eps(self, f):
        """Complex permittivity (eps' - j eps'') at f."""
        return self.einf + self.de * self._k(f)

    def df(self, f):
        e = self.eps(f)
        return -e.imag / e.real

    def poles(self, f_lo: float, f_hi: float, per_decade: float = 1.5) -> dict:
        """einf and Debye poles [(fr, de)] that hold DS over f_lo..f_hi: the
        distribution sampled on a log grid over the band and two decades either
        side; what lies below or above goes into the static part."""
        lo, hi = max(self.f1, f_lo / 100), min(self.f2, f_hi * 100)
        n = max(2, int(math.ceil(math.log10(hi / lo) * per_decade)))
        ln = np.linspace(math.log(lo), math.log(hi), n + 1)
        rate = self.de / math.log(self.f2 / self.f1)
        fr = np.exp((ln[:-1] + ln[1:]) / 2)
        de = rate * np.diff(ln)
        return {"einf": self.einf + rate * (math.log(self.f2) - math.log(hi)),
                "f_static_above": hi, "poles": [(float(a), float(b)) for a, b in zip(fr, de)],
                "static_below": rate * (math.log(lo) - math.log(self.f1))}


def poles_eps(fit: dict, f):
    """Complex permittivity of a `poles` fit, dropping the part that relaxed below the band."""
    f = np.asarray(f, dtype=float)
    return fit["einf"] + sum(d / (1 + 1j * f / r) for r, d in fit["poles"])


def kk_real(eps_im, f, einf: float, f_lo: float = 1e-2, f_hi: float = 1e18, n: int = 20001):
    """Kramers-Kronig: eps'(f) from eps''(f') (a callable, positive),
    eps' - einf = (2/pi) P int f' eps''(f') / (f'^2 - f^2) df'."""
    out = []
    u = np.linspace(math.log(f_lo), math.log(f_hi), n)
    x = np.exp(u)
    im = eps_im(x)
    for fi in np.atleast_1d(f):
        here = float(eps_im(np.array([fi]))[0])
        d = x ** 2 - fi ** 2
        d = np.where(np.abs(d) < 1e-9 * fi ** 2, np.nan, d)
        y = x * (x * im - fi * here) / d        # the subtraction removes the pole
        y = np.nan_to_num(y)
        out.append(einf + 2 / math.pi * float(np.trapezoid(y, u)))
    return np.array(out)


# ================================================================ conductor

def skin_depth_m(f, sigma: float = SIGMA_CU):
    return np.sqrt(1 / (math.pi * np.asarray(f, dtype=float) * MU0 * sigma))


def surface_r(f, sigma: float = SIGMA_CU):
    """Rs, ohm per square."""
    return np.sqrt(math.pi * np.asarray(f, dtype=float) * MU0 / sigma)


def hammerstad_k(f, rq_um: float, sigma: float = SIGMA_CU):
    d = skin_depth_m(f, sigma) * 1e6
    return 1 + 2 / math.pi * np.arctan(1.4 * (rq_um / d) ** 2)


def huray_k(f, a_um: float, sr: float, sigma: float = SIGMA_CU):
    """Cannonball-Huray: K = 1 + 1.5 SR / (1 + d/a + d^2/(2 a^2)), where SR is
    the spheres' surface over the flat area they sit on."""
    if a_um <= 0 or sr <= 0:
        return np.ones_like(np.asarray(f, dtype=float))
    d = skin_depth_m(f, sigma) * 1e6 / a_um
    return 1 + 1.5 * sr / (1 + d + d ** 2 / 2)


def roughness_k(f, foil: str = FOIL_DEFAULT, model: str = "hammerstad", sigma: float = SIGMA_CU):
    c = FOILS[foil]
    if model == "huray":
        return huray_k(f, c["a_um"], c["sr"], sigma)
    if model == "hammerstad":
        return hammerstad_k(f, c["rq_um"], sigma)
    raise ValueError(f"roughness model {model!r}: hammerstad or huray")


def causal_x(r_of_f, f, f_lo: float = 1.0, f_hi: float = 1e16, n: int = 6001):
    """The reactance a causal series impedance must have for resistance
    r_of_f, which may grow as sqrt(f) at most:
    X(w) = (2w/pi) P int (R(w') - R(w)) / (w'^2 - w^2) dw'.
    Above f_hi R is taken as continuing in sqrt(f)."""
    f = np.atleast_1d(np.asarray(f, dtype=float))
    u = np.linspace(math.log(f_lo), math.log(f_hi), n)
    x = np.exp(u)
    r = r_of_f(x)
    here = r_of_f(f)
    out = np.empty_like(f)
    for i, fi in enumerate(f):
        d = x ** 2 - fi ** 2
        d = np.where(np.abs(d) < 1e-9 * fi ** 2, np.nan, d)
        y = np.nan_to_num(x * (r - here[i]) / d)
        tail = 2 * r[-1] / f_hi - here[i] / f_hi
        out[i] = 2 * fi / math.pi * (float(np.trapezoid(y, u)) + tail)
    return out


def series_z(f, rdc: float, g_per_m: float, foil: str = "smooth",
             model: str = "hammerstad", sigma: float = SIGMA_CU):
    """Series impedance (ohm/m) of the conductors, internal inductance
    included. rdc: the strip's DC R (ohm/m). g_per_m: R / Rs at high f."""
    f = np.asarray(f, dtype=float)
    a = surface_r(1.0, sigma) * g_per_m          # R = a sqrt(f) at high f
    fc = rdc ** 2 / (2 * a ** 2)

    def smooth(x):
        return rdc * np.sqrt(1 + 1j * x / fc)
    z = smooth(f)
    if foil == "smooth" or FOILS[foil]["rq_um"] == 0:
        return z

    def extra(x):
        return smooth(x).real * (roughness_k(x, foil, model, sigma) - 1)
    return z + extra(f) + 1j * causal_x(extra, f)


# ================================================================ a line

def _geom_z(kind: str, w, h, t, er, g=None, h2=None) -> dict:
    from . import impedance as imp
    if kind == "microstrip":
        return imp.field_microstrip(w, h, t, er, mask=None)
    if kind == "cpwg":
        return imp.field_cpwg(w, g, h, t, er, mask=None)
    if kind == "stripline":
        return imp.field_stripline(w, h, h2, t, er, er)
    raise ValueError(f"line kind {kind!r}")


def _g_wheeler(kind, w, h, t, g, h2, dn: float) -> float:
    """R / Rs (1/m) by Wheeler: (1/eta0) dZ_air/dn, every wall receding by
    dn (a central difference, mm)."""
    z = []
    for s in (-1, 1):
        d = s * dn
        gg = None if g is None else g + 2 * d
        hh = None if h2 is None else h2 + 2 * d
        z.append(_geom_z(kind, w - 2 * d, h + 2 * d, t - 2 * d, 1.0, gg, hh)["z0"])
    return (z[1] - z[0]) / (2 * dn * 1e-3) / ETA0


def section(w: float, h: float, er: float, tand: float, *, t: float = 0.035, g: float | None = None,
            h2: float | None = None, f_ref: float = DF_F_REF, sigma: float = SIGMA_CU,
            foil: str = FOIL_DEFAULT, roughness: str = "hammerstad", length: float = 0.0,
            kind: str | None = None) -> dict:
    """A uniform line as data: lib/impedance's Z0 and eps_eff, the dielectric
    filling factor q = dln(eps_eff)/dln(er) (the fraction of the electric
    energy in the dielectric, so it is the factor on tan d), and the
    conductor's Rdc and Wheeler factor. g: a coplanar ground's gap (CPWG). h2:
    the fill above (stripline)."""
    kind = kind or ("stripline" if h2 else "cpwg" if g else "microstrip")
    base = _geom_z(kind, w, h, t, er, g, h2)
    e = 0.02 * er
    hi = _geom_z(kind, w, h, t, er + e, g, h2)["eps_eff"]
    lo = _geom_z(kind, w, h, t, er - e, g, h2)["eps_eff"]
    q = (hi - lo) / (2 * e) * er / base["eps_eff"]
    dn = min(0.01, t / 8, w / 40, h / 20)
    return {"kind": kind, "w": w, "h": h, "t": t, "g": g, "h2": h2, "er": er, "tand": tand,
            "f_ref": f_ref, "sigma": sigma, "foil": foil, "roughness": roughness,
            "length": length, "z0": base["z0"], "eps_eff": base["eps_eff"], "q": q,
            "rdc": 1 / (sigma * w * t * 1e-6),
            "g_per_m": _g_wheeler(kind, w, h, t, g, h2, dn)}


def line_gamma_z(sec: dict, freqs, dielectric: bool = True, conductor: bool = True):
    """(gamma, Z0) of the lossy line over freqs. Lossy C = C + q C (eps(f)/er - 1):
    the deck's er is DS's er at f_ref, and the conductor adds its series Z."""
    f = np.asarray(freqs, dtype=float)
    w = 2 * math.pi * f
    n = math.sqrt(sec["eps_eff"])
    ll, cc = sec["z0"] * n / C0, n / (C0 * sec["z0"])
    c = cc + 0j * f
    if dielectric and sec["tand"]:
        c = cc * (1 + sec["q"] * (DS(sec["er"], sec["tand"], sec["f_ref"]).eps(f) / sec["er"] - 1))
    z = 1j * w * ll
    if conductor:
        z = z + series_z(f, sec["rdc"], sec["g_per_m"], sec["foil"], sec["roughness"], sec["sigma"])
    y = 1j * w * c
    return np.sqrt(z * y), np.sqrt(z / y)


def alpha_db_per_m(sec: dict, freqs, dielectric: bool = True, conductor: bool = True):
    g, _ = line_gamma_z(sec, freqs, dielectric, conductor)
    return g.real * NP_TO_DB


# ---------------------------------------------------------------- closed forms

def hammerstad_eps_z(w: float, h: float, er: float) -> tuple[float, float]:
    """Hammerstad-Jensen zero-thickness microstrip: (Z0, eps_eff)."""
    u = w / h
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 \
        + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    ee = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)
    ff = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    z_air = 60 * math.log(ff / u + math.sqrt(1 + (2 / u) ** 2))
    return z_air / math.sqrt(ee), ee


def alpha_d_closed(f, er: float, eps_eff: float, tand: float):
    """Pozar 3.198 in dB/m: the secant filling factor (eps_eff - 1)/(er - 1)."""
    return (NP_TO_DB * math.pi * np.asarray(f, dtype=float) / C0 * er / math.sqrt(eps_eff)
            * (eps_eff - 1) / (er - 1) * tand)


def alpha_c_wide(f, z0: float, w: float, sigma: float = SIGMA_CU):
    """Pozar 3.199, Rs/(Z0 W), a wide line's plates, dB/m."""
    return NP_TO_DB * surface_r(f, sigma) / (z0 * w * 1e-3)


def _hj_w_eff(w: float, h: float, t: float) -> float:
    """Hammerstad-Jensen's thickness correction, in air: the width that gives
    a zero-thickness strip the same Z. u1 = u + (t/h)/pi ln(1 + 4e/((t/h) coth^2 sqrt(6.517 u)))."""
    if t <= 0:
        return w
    u, tt = w / h, t / h
    return h * (u + tt / math.pi * math.log(
        1 + 4 * math.e / (tt / math.tanh(math.sqrt(6.517 * u)) ** 2)))


def alpha_c_wheeler_ms(f, w: float, h: float, t: float, z0: float, sigma: float = SIGMA_CU):
    """Wheeler's rule on Hammerstad-Jensen's closed-form Z_air, thickness
    included (the strip's corners carry much of the R), dB/m. This is the field
    solver's independent reference: no mesh."""
    def zair(ww, hh, tt):
        return hammerstad_eps_z(_hj_w_eff(ww, hh, tt), hh, 1.0 + 1e-9)[0]
    dn = min(1e-3, t / 8)
    dz = (zair(w - 2 * dn, h + 2 * dn, t - 2 * dn)
          - zair(w + 2 * dn, h - 2 * dn, t + 2 * dn)) / (2 * dn * 1e-3)
    return NP_TO_DB * surface_r(f, sigma) * dz / (2 * z0 * ETA0)


# ================================================================ S

def _line_abcd(g, z, l_m):
    gl = g * l_m
    ch, sh = np.cosh(gl), np.sinh(gl)
    return ch, z * sh, sh / z, ch


def correction(sec: dict, freqs, zref: float = 50.0, dielectric: bool = True,
               conductor: bool = True) -> np.ndarray:
    """S (nf, 2, 2) of lossy(l) * lossless(l)^-1 in zref: port 1 outward, port
    2 toward the circuit."""
    f = np.asarray(freqs, dtype=float)
    l_m = sec["length"] * 1e-3
    g, z = line_gamma_z(sec, f, dielectric, conductor)
    a1, b1, c1, d1 = _line_abcd(g, z, l_m)
    # the inverse of the lossless line is the same line of length -l
    a2, b2, c2, d2 = _line_abcd(1j * 2 * np.pi * f * math.sqrt(sec["eps_eff"]) / C0,
                                sec["z0"] + 0j * f, -l_m)
    a = a1 * a2 + b1 * c2
    b = a1 * b2 + b1 * d2
    c = c1 * a2 + d1 * c2
    d = c1 * b2 + d1 * d2
    den = a + b / zref + c * zref + d
    s = np.empty((len(f), 2, 2), dtype=complex)
    s[:, 0, 0] = (a + b / zref - c * zref - d) / den
    s[:, 0, 1] = 2 * (a * d - b * c) / den
    s[:, 1, 0] = 2 / den
    s[:, 1, 1] = (-a + b / zref - c * zref + d) / den
    return s


def add_line_loss(S, freqs, sections, zref: float = 50.0, dielectric: bool = True,
                  conductor: bool = True) -> np.ndarray:
    """A lossless N-port S (nf, n, n) with each port's uniform section's loss
    cascaded on. sections: one per port, a `section` dict (with its `length`)
    or None. A line of length L between two ports takes L/2 on each."""
    S = np.array(S, dtype=complex)
    for k, sec in enumerate(sections):
        if sec is None or not sec.get("length"):
            continue
        c = correction(sec, freqs, zref, dielectric, conductor)
        for m in range(S.shape[0]):
            s = S[m]
            den = 1 - c[m, 1, 1] * s[k, k]
            new = s + np.outer(s[:, k], s[k, :]) * c[m, 1, 1] / den
            new[k, :] = s[k, :] * c[m, 0, 1] / den
            new[:, k] = s[:, k] * c[m, 1, 0] / den
            new[k, k] = c[m, 0, 0] + c[m, 0, 1] * c[m, 1, 0] * s[k, k] / den
            S[m] = new
    return S


# ================================================================ from a board

def _shortest_path(poly, a, b, pitch: float) -> float | None:
    """The shortest way from a to b inside poly on a pitch grid (8-neighbour,
    mm), or None. a and b may sit on the edge. b=None gives the longest
    shortest path from a: a one-port net's run to its far end."""
    import heapq
    from shapely import contains_xy
    x0, y0, x1, y1 = poly.bounds
    nx, ny = int((x1 - x0) / pitch) + 2, int((y1 - y0) / pitch) + 2
    xs, ys = x0 + np.arange(nx) * pitch, y0 + np.arange(ny) * pitch
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    ok = contains_xy(poly, xx, yy)
    if not ok.any():
        return None

    def near(p):
        d = (xx - p[0]) ** 2 + (yy - p[1]) ** 2 + np.where(ok, 0, 1e12)
        i, j = np.unravel_index(int(np.argmin(d)), d.shape)
        return i, j, math.sqrt(float(d[i, j]))
    ia, ja, da = near(a)
    dist = np.full(ok.shape, np.inf)
    dist[ia, ja] = 0.0
    heap = [(0.0, ia, ja)]
    steps = [(di, dj, math.hypot(di, dj) * pitch) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]
    while heap:
        d, i, j = heapq.heappop(heap)
        if d > dist[i, j]:
            continue
        for di, dj, c in steps:
            u, v = i + di, j + dj
            if 0 <= u < nx and 0 <= v < ny and ok[u, v] and d + c < dist[u, v]:
                dist[u, v] = d + c
                heapq.heappush(heap, (d + c, u, v))
    if b is None:
        far = dist[np.isfinite(dist)]
        return float(far.max()) + da
    ib, jb, db = near(b)
    return None if not np.isfinite(dist[ib, jb]) else float(dist[ib, jb]) + da + db


def gap_at_port(geo: dict, port: dict, window: float) -> float | None:
    """The gap from a port's strip to coplanar copper of any other net, or
    None when nothing is within `window`."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    x, y, w = port["x"], port["y"], port["w"]
    # a little inside the region: the plane itself is the cut's edge
    d = 0.02 * (1 if port["dir"][0] == "-" else -1)
    if port["dir"][1] == "x":
        cut = LineString([(x + d, y - window), (x + d, y + window)])
        at = y
    else:
        cut = LineString([(x - window, y + d), (x + window, y + d)])
        at = x
    other = [Polygon(c["pts"]).buffer(0) for c in geo["copper"]
             if c["layer"] == port["layer"] and c["net"] != port["net"]]
    if not other:
        return None
    hit = unary_union(other).intersection(cut)
    if hit.is_empty:
        return None
    best = None
    for s in (hit.geoms if hasattr(hit, "geoms") else [hit]):
        for c in s.coords:
            v = (c[1] if port["dir"][1] == "x" else c[0]) - at
            g = abs(v) - w / 2
            if g > 0 and (best is None or g < best):
                best = g
    return best


def port_length(poly, p: dict, others: list, warnings: list, k: int) -> float:
    """The uniform length charged to port k. With another port on the same
    copper, half the shortest path to the nearest; alone on its copper (a stub,
    or a coupled section's island), its longest run."""
    from shapely.geometry import Point
    pitch = max(p["w"] / 3, 0.05)
    pa = (p["x"], p["y"])
    near = [q for q in others if poly.distance(Point(q["x"], q["y"])) < pitch]
    paths = [v for v in (_shortest_path(poly, pa, (q["x"], q["y"]), pitch) for q in near)
             if v is not None]
    if not paths:
        return _shortest_path(poly, pa, None, pitch) or 0.0
    path = min(paths)
    if len(near) > 1:
        warnings.append(f"port {k}: {len(near) + 1} ports on one copper; half the way to the "
                        f"nearest is taken, not a real split")
    extra = poly.area / p["w"] - path
    if extra > 0.2 * path:
        warnings.append(f"port {k}: about {extra:.1f} mm-equivalent of copper is off the "
                        f"{path:.1f} mm through path (stubs, wider parts): it keeps its lossless Q")
    return path / 2


def sections_for_ports(geo: dict, ports: list[dict], stackup, *, foil: str = FOIL_DEFAULT,
                       roughness: str = "hammerstad", lengths=None, sigma: float = SIGMA_CU) -> dict:
    """{"sections": [...], "warnings": [...]}: each port's uniform section
    from the extracted copper. ports: the study's `ports` (x, y, dir, layer,
    ref, net, w, h, er). lengths: a length per port, mm, in place of what
    `port_length` measures."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    warnings, out = [], []
    for k, p in enumerate(ports):
        if lengths is not None:
            length = float(lengths[k])
        else:
            cu = [Polygon(c["pts"]).buffer(0) for c in geo["copper"]
                  if c["layer"] == p["layer"] and c["net"] == p["net"]]
            others = [q for q in ports if q is not p and q["net"] == p["net"]
                      and q["layer"] == p["layer"]]
            length = port_length(unary_union(cu), p, others, warnings, k) if cu else 0.0
        h = stackup.height_between(p["layer"], p["ref"])
        tand, material = stackup.tand_between(p["layer"], p["ref"])
        if tand is None:
            warnings.append(f"port {k}: the stackup has no loss_tangent between {p['layer']} and "
                            f"{p['ref']}: tan d 0.02 assumed")
            tand = 0.02
        # copper on the far side makes it a stripline
        above, below = stackup.adjacent(p["layer"])
        far = above if p["ref"] == below else below
        kw = {}
        if not stackup.is_outer(p["layer"]) and far and any(c["layer"] == far for c in geo["copper"]):
            kw["h2"] = stackup.height_between(p["layer"], far)
        else:
            g = gap_at_port(geo, p, max(5 * p["w"], 5 * h))
            if g is not None and g < 3 * h:
                kw["g"] = g
        out.append(section(p["w"], h, stackup.epsilon_between(p["layer"], p["ref"]), tand,
                           t=stackup.copper_thickness.get(p["layer"], 0.035),
                           f_ref=df_ref_hz(material), foil=foil, roughness=roughness,
                           sigma=sigma, length=length, **kw))
    return {"sections": out, "warnings": warnings}


def sections_for_planar(geo: dict, *, foil: str = FOIL_DEFAULT, roughness: str = "hammerstad",
                        lengths=None, sigma: float = SIGMA_CU) -> dict:
    """The same for a `sim_solve planar` GEOM: one microstrip layer. tan d None
    (nothing in the stackup) is taken as FR-4's 0.02 and said so; an explicit
    0 stays lossless in the dielectric."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    warnings, out = [], []
    tand = geo.get("tand")
    if tand is None:
        warnings.append("GEOM has no tan d: 0.02 at 1 GHz assumed")
        tand = 0.02
    polys = [Polygon(p).buffer(0) for p in geo["polys"]]
    for k, p in enumerate(geo["ports"]):
        if lengths is not None:
            length = float(lengths[k])
        else:
            from shapely.geometry import Point
            touch = [g for g in polys if g.distance(Point(p["x"], p["y"])) < max(p["w"] / 3, 0.05)]
            others = [q for q in geo["ports"] if q is not p]
            length = port_length(unary_union(touch), p, others, warnings, k) if touch else 0.0
        out.append(section(p["w"], geo["h"], geo["er"], tand, t=geo.get("t", 0.035),
                           f_ref=geo.get("f_ref", DF_F_REF), foil=foil, roughness=roughness,
                           sigma=sigma, length=length))
    return {"sections": out, "warnings": warnings}


def summary(sections, freqs) -> list[dict]:
    """What a Touchstone's reader needs to know, per port."""
    f = [float(freqs[len(freqs) // 2]), float(freqs[-1])]
    out = []
    for s in sections:
        if s is None:
            out.append(None)
            continue
        ad = alpha_db_per_m(s, f, True, False)
        ac = alpha_db_per_m(s, f, False, True)
        out.append({"kind": s["kind"], "length_mm": round(s["length"], 3), "w": s["w"],
                    "h": round(s["h"], 5), "er": round(s["er"], 4), "tand": s["tand"],
                    "f_ref": s["f_ref"], "z0": round(s["z0"], 2), "eps_eff": round(s["eps_eff"], 3),
                    "q": round(s["q"], 3), "foil": s["foil"], "roughness": s["roughness"],
                    "foil_assumed": True, "f_hz": f,
                    "alpha_d_db_per_m": [round(float(v), 3) for v in ad],
                    "alpha_c_db_per_m": [round(float(v), 3) for v in ac]})
    return out


# ================================================================ in the deck

def dielectric_props(csx, name: str, ds: DS, f_lo: float, f_hi: float, per_decade: float = 1.5):
    """DS as an openEMS material: overdamped Lorentz poles. openEMS's engine
    has no Debye term (its dispersive extension is Operator_Ext_LorentzMaterial),
    but 1 - wp^2/(w^2 - wL^2 - jw/tau) is a Debye term of step wp^2/wL^2 and
    corner tau wL^2 for w << 1/tau. Written for the deck's `dielectric` hook."""
    fit = ds.poles(f_lo, f_hi, per_decade)
    einf = fit["einf"]
    inner = {"Epsilon": einf}
    attrs = {"Order": len(fit["poles"])}
    w_hi = 2 * math.pi * f_hi
    tau = 1 / (40 * w_hi)
    for k, (fr, de) in enumerate(fit["poles"], 1):
        wl = math.sqrt(2 * math.pi * fr / tau)
        inner[f"EpsilonPlasmaFrequency_{k}"] = wl * math.sqrt(de / einf) / (2 * math.pi)
        inner[f"EpsilonLorPoleFrequency_{k}"] = wl / (2 * math.pi)
        inner[f"EpsilonRelaxTime_{k}"] = tau
    return csx.prop("LorentzMaterial", name, attrs, inner)
