"""impedance.py - controlled-impedance geometry (SPEC P5; V12, V18).

Turns an impedance TARGET (constraints.json / stackups.yaml) into a trace
width + differential gap, and a drawn width/gap back into ohms. Used by
rules_gen.py, route_critical.py, check_diffpair.py and the stackups.yaml
`controlled_impedance` tables (tests/test_board_setup.py regenerates them).

Two methods, chosen per call by `method=` ("field" | "closed_form"), default
"field" whenever numpy + scipy import (they are in requirements.lock, so a
normal venv always has them; check_env probes the solver):

field - a 2D quasi-static finite-volume Laplace solver (below). The cross-
  section is solved twice, with the dielectrics and in air, for the per-unit-
  length capacitance C and C_air; Z0 = 1/(c0*sqrt(C*C_air)), eps_eff =
  C/C_air. Differential pairs are solved in odd mode (+1/-1 V):
  Zdiff = 2*Zodd. Handles what the formulas cannot: finite copper thickness,
  JLC's solder-mask coating on outer layers (JLC_MASK, coated microstrip) and
  inner-layer asymmetric stripline with a different er above and below.
  Accuracy (tests/test_impedance.py): within 1% of Cohn's exact symmetric
  stripline and of Hammerstad-Jensen microstrip, and within JLC's own
  calculator figures recorded in tests/fixtures/jlc_impedance/ to the
  tolerance stated there.

closed_form - the pre-solver fallback, kept verbatim: IPC-2141A surface
  microstrip  Z0 = (87/sqrt(er+1.41))*ln(5.98h/(0.8w+t))  (valid ~0.1 <= w/h
  <= 3) and the published edge-coupled correction
  Zdiff = 2*Z0*(1 - 0.48*exp(-0.96*s/h)). Uncoated, outer layer only.

All lengths mm. No I/O, no toolchain.
"""
from __future__ import annotations

import functools
import math

try:
    import numpy as np
    import scipy.sparse as sp
    import scipy.sparse.linalg as spla
    SOLVER_OK = True
except ImportError:  # pragma: no cover - requirements.lock always has both
    SOLVER_OK = False

# Copper foil finished thickness by weight (mm) - JLC: 1 oz ~= 0.035, 0.5 oz ~= 0.0175.
CU_OZ_MM = {1.0: 0.035, 0.5: 0.0175, 2.0: 0.070}

# JLC's solder-mask coating as its calculator assumes it (jlcpcb.com/impedance,
# "Coating Above Substrate C1: 1.2mil, Coating Above Trace C2: 0.6mil,
# Coating Between Traces C3: 1.2mil, Coating Dielectric CEr: 3.8").
MIL = 0.0254
JLC_MASK = {"c1": 1.2 * MIL, "c2": 0.6 * MIL, "er": 3.8}


def default_method() -> str:
    return "field" if SOLVER_OK else "closed_form"


# ================================================================ closed form

def microstrip_z0_closed_form(w: float, h: float, t: float, er: float) -> float:
    """Single-ended surface-microstrip Z0 (ohms), IPC-2141A. w,h,t in mm."""
    if w <= 0 or h <= 0:
        raise ValueError("w and h must be positive")
    return (87.0 / math.sqrt(er + 1.41)) * math.log(5.98 * h / (0.8 * w + t))


def _zdiff(w: float, s: float, h: float, t: float, er: float) -> float:
    """Closed-form edge-coupled differential microstrip Zdiff (ohms)."""
    return 2.0 * microstrip_z0_closed_form(w, h, t, er) * (1.0 - 0.48 * math.exp(-0.96 * s / h))


# ============================================================ 2D field solver
#
# Quasi-static TEM finite-volume solve on a graded tensor grid. Reference
# plane at y=0, dielectric slabs stacked upward, rectangular conductors,
# rectangular dielectric overrides (the mask coating), and either an upper
# reference plane (stripline) or a far grounded lid (microstrip). Side walls
# are Neumann. Every node-to-node edge is a conductance eps*dual_len/len with
# cell-constant eps, so the discrete energy sum g*dphi^2 at 1 V is C/eps0.
# The discrete solve minimises that energy over a smaller function space, so
# C comes out high and Z low; RICHARDSON corrects the leading term from two
# grids (h and h/2) - measured, not assumed, in tests/test_impedance.py.

EPS0 = 8.8541878128e-12        # F/m
C0 = 299792458.0               # m/s
LID_FACTOR = 30.0              # microstrip lid height / structure height
SIDE_FACTOR = 30.0             # side-wall distance / max(structure height, span)
GROW = 1.3                     # neighbouring-cell size ratio
DMIN_DIV = 6.0                 # finest cell = min(width, gap, dielectric) / DMIN_DIV;
                               # thinner layers (copper, mask) are one cell thick
RICHARDSON = True              # extrapolate from the grid and its half


def _axis(breaks, dmin: float, dmax: float, grow: float):
    """Grid lines through every breakpoint, dmin next to each, graded by
    `grow` toward the middle of each span and capped at dmax."""
    b = np.unique(np.round(np.asarray(breaks, float), 9))
    pts = [b[0]]
    for a, c in zip(b[:-1], b[1:]):
        half, x, d = [], 0.0, dmin
        while x + d < (c - a) / 2:
            x += d
            half.append(x)
            d = min(d * grow, dmax)
        pts += [a + v for v in half] + [(a + c) / 2] + [c - v for v in half] + [c]
    return np.unique(np.round(np.asarray(pts), 9))


def _energy(xs, ys, eps_cell, fixed, volts) -> float:
    """sum g*dphi^2 (C/eps0 x sum V^2) for one excitation."""
    nx, ny = len(xs), len(ys)
    dx, dy = np.diff(xs), np.diff(ys)
    idx = np.arange(nx * ny).reshape(ny, nx)
    # horizontal edge (i,j)-(i+1,j): cells below (j-1) and above (j)
    epad = np.zeros((ny + 1, nx - 1))
    epad[1:-1, :] = eps_cell
    hpad = np.zeros(ny + 1)
    hpad[1:-1] = dy
    gh = (epad[:-1] * hpad[:-1, None] + epad[1:] * hpad[1:, None]) / (2 * dx[None, :])
    # vertical edge (i,j)-(i,j+1): cells left (i-1) and right (i)
    epv = np.zeros((ny - 1, nx + 1))
    epv[:, 1:-1] = eps_cell
    wpad = np.zeros(nx + 1)
    wpad[1:-1] = dx
    gv = (epv[:, :-1] * wpad[None, :-1] + epv[:, 1:] * wpad[None, 1:]) / (2 * dy[:, None])
    a = np.concatenate([idx[:, :-1].ravel(), idx[:-1, :].ravel()])
    b = np.concatenate([idx[:, 1:].ravel(), idx[1:, :].ravel()])
    g = np.concatenate([gh.ravel(), gv.ravel()])
    n = nx * ny
    lap = sp.coo_matrix((np.concatenate([-g, -g, g, g]),
                         (np.concatenate([a, b, a, b]), np.concatenate([b, a, a, b]))),
                        shape=(n, n)).tocsr()
    fx = fixed.ravel()
    phi = np.where(fx, volts.ravel(), 0.0)
    free = ~fx
    rhs = -(lap[free][:, fx] @ phi[fx])
    phi[free] = spla.spsolve(lap[free][:, free].tocsc(), rhs, permc_spec="MMD_AT_PLUS_A")
    return float(np.sum(g * (phi[a] - phi[b]) ** 2))


def _section_once(conductors, slabs, masks, top_plane, excite, refine):
    """(C, C_air) in eps0-units per line for one grid resolution.

    Every cross-section here is mirror-symmetric about x=0 (_traces), so only
    x >= 0 is meshed: the mirror plane is Neumann for a single trace or the
    even mode and a 0 V wall for the odd mode (+1/-1). Half the nodes, and
    the energy of the full section is twice the half's."""
    odd = len(excite) == 2 and excite[0] == -excite[1]
    struct = max([s[1] for s in slabs] + [c[3] for c in conductors]
                 + [m[3] for m in masks])
    ytop = top_plane if top_plane is not None else struct * LID_FACTOR
    span = max(c[1] for c in conductors) * 2
    side = SIDE_FACTOR * max(top_plane or struct, span) + span / 2
    feat = [c[1] - c[0] for c in conductors] + [s[1] - s[0] for s in slabs]
    xe = sorted({x for c in conductors for x in c[:2]})
    feat += [b - a for a, b in zip(xe[:-1], xe[1:])]
    dmin = min(f for f in feat if f > 1e-9) / (DMIN_DIV * refine)
    grow = GROW ** (1.0 / refine)
    xb = [0.0, side] + xe + [x for m in masks for x in m[:2]]
    yb = [0.0, ytop] + [y for s in slabs for y in s[:2]] \
        + [y for c in conductors for y in c[2:]] + [y for m in masks for y in m[2:]]
    xs = _axis([x for x in xb if 0.0 <= x <= side], dmin, side / 10, grow)
    ys = _axis([y for y in yb if 0.0 <= y <= ytop], dmin, ytop / 10, grow)
    xm, ym = (xs[:-1] + xs[1:]) / 2, (ys[:-1] + ys[1:]) / 2
    eps = np.ones((len(ym), len(xm)))
    for y0, y1, er in slabs:
        eps[(ym > y0) & (ym < y1), :] = er
    for x0, x1, y0, y1, er in masks:
        eps[np.ix_((ym > y0) & (ym < y1), (xm > x0) & (xm < x1))] = er
    gx, gy = np.meshgrid(xs, ys)
    fixed = (gy <= 1e-12) | (gy >= ytop - 1e-12)
    if odd:
        fixed |= gx <= 1e-12
    volts = np.zeros_like(gx)
    tol = 1e-9
    for (x0, x1, y0, y1), v in zip(conductors, excite):
        if x1 <= tol:
            continue  # the mirrored half
        inside = (gx >= x0 - tol) & (gx <= x1 + tol) & (gy >= y0 - tol) & (gy <= y1 + tol)
        fixed |= inside
        volts[inside] = v
    nv = sum(v * v for v in excite) / 2.0
    return (_energy(xs, ys, eps, fixed, volts) / nv,
            _energy(xs, ys, np.ones_like(eps), fixed, volts) / nv)


@functools.lru_cache(maxsize=4096)
def _section(conductors, slabs, masks, top_plane, excite):
    """Per-line (Z, eps_eff) for one cross-section. Tuple args (cacheable)."""
    c1, a1 = _section_once(conductors, slabs, masks, top_plane, excite, 1)
    if RICHARDSON:
        c2, a2 = _section_once(conductors, slabs, masks, top_plane, excite, 2)
        c1, a1 = 2 * c2 - c1, 2 * a2 - a1
    return 1.0 / (C0 * EPS0 * math.sqrt(c1 * a1)), c1 / a1


def _r(v: float) -> float:
    return round(float(v), 7)


def _traces(w: float, s: float | None, y0: float, t: float):
    if s is None:
        return ((_r(-w / 2), _r(w / 2), _r(y0), _r(y0 + t)),)
    return ((_r(-s / 2 - w), _r(-s / 2), _r(y0), _r(y0 + t)),
            (_r(s / 2), _r(s / 2 + w), _r(y0), _r(y0 + t)))


def _result(conductors, slabs, masks, top_plane, even: bool) -> dict:
    if len(conductors) == 1:
        z, ee = _section(conductors, slabs, masks, top_plane, (1.0,))
        return {"z0": z, "eps_eff": ee}
    zo, eo = _section(conductors, slabs, masks, top_plane, (1.0, -1.0))
    out = {"zdiff": 2 * zo, "zodd": zo, "eps_eff_odd": eo}
    if even:
        ze, ee = _section(conductors, slabs, masks, top_plane, (1.0, 1.0))
        out.update(zeven=ze, zcommon=ze / 2, eps_eff_even=ee)
    return out


def field_microstrip(w: float, h: float, t: float, er: float, s: float | None = None,
                     mask: dict | None = JLC_MASK, even: bool = False) -> dict:
    """Outer-layer (coated) microstrip, single (s=None) or edge-coupled pair
    with edge gap s (odd mode; even=True adds the even/common mode).
    mask: {c1: on substrate, c2: on trace, er} or None for bare copper."""
    if w <= 0 or h <= 0 or (s is not None and s <= 0):
        raise ValueError("w, h (and s) must be positive")
    if not SOLVER_OK:
        raise RuntimeError("field solver needs numpy + scipy")
    cond = _traces(w, s, h, t)
    masks = ()
    if mask:
        c1, c2, mer = float(mask["c1"]), float(mask["c2"]), float(mask["er"])
        # substrate coating everywhere, plus a conformal cap c2 thick over
        # each trace's top and sides (JLC's C1/C2/C3 model with C3 = C1)
        masks = ((-1e3, 1e3, _r(h), _r(h + c1), mer),) + tuple(
            (_r(x0 - c2), _r(x1 + c2), _r(h), _r(h + t + c2), mer)
            for x0, x1, _, _ in cond)
    return _result(cond, ((0.0, _r(h), float(er)),), masks, None, even)


def field_stripline(w: float, h_base: float, h_fill: float, t: float,
                    er_base: float, er_fill: float, s: float | None = None,
                    even: bool = False) -> dict:
    """Inner-layer asymmetric stripline. The trace sits on the dielectric it
    was etched on (h_base, er_base - the core) and is embedded in the one
    pressed over it (h_fill, er_fill - prepreg, measured from the trace's base
    to the far plane, so the trace top clears that plane by h_fill - t)."""
    if w <= 0 or h_base <= 0 or h_fill <= t or (s is not None and s <= 0):
        raise ValueError("w, h_base, s must be positive and h_fill > t")
    if not SOLVER_OK:
        raise RuntimeError("field solver needs numpy + scipy")
    slabs = ((0.0, _r(h_base), float(er_base)),
             (_r(h_base), _r(h_base + h_fill), float(er_fill)))
    return _result(_traces(w, s, h_base, t), slabs, (), _r(h_base + h_fill), even)


# ============================================================ public helpers

def microstrip_z0(w: float, h: float, t: float, er: float, method: str | None = None,
                  mask: dict | None = JLC_MASK) -> float:
    """Single-ended outer-layer Z0 (ohms). w,h,t in mm."""
    if (method or default_method()) == "closed_form":
        return microstrip_z0_closed_form(w, h, t, er)
    return field_microstrip(w, h, t, er, None, mask)["z0"]


def zdiff(w: float, s: float, h: float, t: float, er: float, method: str | None = None,
          mask: dict | None = JLC_MASK) -> float:
    """Edge-coupled differential outer-layer Zdiff (ohms); s = edge gap."""
    if (method or default_method()) == "closed_form":
        return _zdiff(w, s, h, t, er)
    return field_microstrip(w, h, t, er, s, mask)["zdiff"]


def _bisect(f, lo: float, hi: float, tol: float = 1e-4) -> float:
    flo = f(lo)
    if flo * f(hi) > 0:  # target unreachable in range: clamp to nearer endpoint
        return lo if abs(flo) < abs(f(hi)) else hi
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if flo * f(mid) <= 0:
            hi = mid
        else:
            lo, flo = mid, f(mid)
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def _secant_log(z_of, target: float, x0: float, lo: float, hi: float,
                rel: float = 1e-4) -> float:
    """Solve z_of(x) == target for a monotonic decreasing z, secant in
    log-log space from the closed-form guess x0 (each z_of is a field solve,
    so this converges in ~4 solves where bisection would take ~15)."""
    x0 = min(max(x0, lo), hi)
    x1 = min(max(x0 * 1.1, lo), hi)
    if x1 == x0:
        x1 = x0 * 0.9
    f0 = math.log(z_of(x0) / target)
    f1 = math.log(z_of(x1) / target)
    for _ in range(30):
        if f1 == f0:
            break
        x2 = math.exp(math.log(x1) - f1 * (math.log(x1) - math.log(x0)) / (f1 - f0))
        x2 = min(max(x2, lo), hi)
        x0, f0, x1 = x1, f1, x2
        f1 = math.log(z_of(x1) / target)
        if abs(f1) < 1e-5 or abs(x1 - x0) <= rel * x1 or (x1 in (lo, hi) and x0 in (lo, hi)):
            break
    return x1


def solve_width(z0_target: float, h: float, t: float, er: float,
                lo: float = 0.02, hi: float = 20.0, tol: float = 1e-4,
                method: str | None = None, mask: dict | None = JLC_MASK) -> float:
    """Trace width (mm) whose single-ended outer Z0 == z0_target.

    Out of reach within [lo, hi]: clamps to the nearer endpoint rather than
    raise (callers want a usable width)."""
    cf = functools.partial(microstrip_z0_closed_form, h=h, t=t, er=er)
    w_cf = _bisect(lambda w: cf(w) - z0_target, lo, hi, tol)
    if (method or default_method()) == "closed_form":
        return w_cf
    hi = min(hi, 30 * h)
    return _secant_log(lambda w: microstrip_z0(w, h, t, er, "field", mask),
                       z0_target, w_cf, lo, hi)


def diff_pair(zdiff_target: float, h: float, t: float, er: float,
              width: float | None = None, gap: float | None = None,
              method: str | None = None, mask: dict | None = JLC_MASK) -> tuple[float, float]:
    """(width, gap) mm for an edge-coupled differential outer-layer pair.

    Real pairs are drawn tightly coupled (small gap for noise immunity), so the
    default pins a manufacturable gap = clamp(h, 0.13, 0.30) mm and solves the
    width for the target - NOT the loosely-coupled "width for Z0=Zdiff/2, huge
    gap" solution, which is impedance-valid but nobody routes it. Pin `width`
    to solve the gap instead, or pin `gap` to solve the width explicitly.
    """
    field = (method or default_method()) == "field"
    if width is not None and gap is None:
        smin, smax = max(h * 0.1, 0.05), h * 5.0
        s = _bisect(lambda ss: _zdiff(width, ss, h, t, er) - zdiff_target, smin, smax)
        if field:  # Zdiff grows with the gap
            s = _secant_log(lambda ss: 1.0 / zdiff(width, ss, h, t, er, "field", mask),
                            1.0 / zdiff_target, s, smin, smax)
        return round(width, 4), round(s, 4)
    s = gap if gap is not None else min(max(h, 0.13), 0.30)
    w = _bisect(lambda ww: _zdiff(ww, s, h, t, er) - zdiff_target, 0.05, 5.0)
    if field:
        w = _secant_log(lambda ww: zdiff(ww, s, h, t, er, "field", mask),
                        zdiff_target, w, 0.02, 5.0)
    return round(w, 4), round(s, 4)


def geometry_for(profile: dict, h: float, er: float, cu_oz: float = 1.0,
                 method: str | None = None) -> dict:
    """Resolve one impedance profile against a physical stackup gap.

    profile: {impedance_ohm, kind: "single"|"diff", [width_mm]}. Returns the
    profile augmented with computed width_mm (+ gap_mm for diff).
    """
    t = CU_OZ_MM.get(cu_oz, 0.035)
    z = float(profile["impedance_ohm"])
    out = dict(profile)
    if profile.get("kind") == "diff":
        w, s = diff_pair(z, h, t, er, profile.get("width_mm"), method=method)
        out["width_mm"], out["gap_mm"] = w, s
    else:
        out["width_mm"] = round(solve_width(z, h, t, er, method=method), 4)
    return out


def solver_status() -> dict:
    """check_env probe: solve Cohn's exact symmetric stripline and report the
    error. ok iff numpy+scipy import and the error is under 1%."""
    if not SOLVER_OK:
        return {"ok": False, "detail": "numpy/scipy not importable"}
    from scipy.special import ellipk
    w, b, er, t = 0.2, 0.5, 4.3, 0.001
    k = 1.0 / math.cosh(math.pi * w / (2 * b))
    exact = 30 * math.pi / math.sqrt(er) * ellipk(k * k) / ellipk(1 - k * k)
    got = field_stripline(w, (b - t) / 2, (b + t) / 2, t, er, er)["z0"]
    err = abs(got - exact) / exact * 100
    return {"ok": err < 1.0, "z0": round(got, 3), "exact": round(exact, 3),
            "err_pct": round(err, 3)}
