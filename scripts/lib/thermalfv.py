"""thermalfv.py - finite-volume heat spreading in a board, and the electro-
thermal (Joule heating) coupling, on a rectangular grid.

The board is a plane: each cell carries an in-plane sheet conductance
(k*t summed over its copper layers and the laminate, W/K per square) and
loses heat to ambient through both faces (h_top + h_bot, W/m^2K, with
radiation linearised into h by the caller). Parts are heat sources (W in a
cell or spread over a footprint); cells may be held at a fixed temperature.
Between neighbours the conductance is the series (harmonic) mean of the two
half-cells, so a copper edge into bare FR4 is resolved, not smeared. This
is the plane model the closed-form check_thermal screen stands in for: it
sees where the copper actually goes, and does not need a calibrated curve.

The electro-thermal solve (`joule`) puts a DC current through a copper
region (fixed potential on two electrode sets), turns the per-link I^2 R
into heat, solves the temperature, re-rates copper's resistivity at that
temperature (rho = rho0 (1 + alpha (T - T0)), alpha_Cu = 0.00393/K) and
repeats until the temperature stops moving: the coupling a trace or a
plane neck under real current needs, and a fixed-R IR-drop solve misses.

Validation (sim_solve.py validate, tests/test_solvers.py), each against a
closed form:
  * the 1D fin: theta(L)/theta(0) = 1/cosh(mL), m = sqrt(h/(k t));
  * the 2D point source on a large plate: theta(r) = P/(2 pi k t) K0(m r);
  * the coupled strip: a uniform strip carrying I settles where
    theta = I^2 R0 / (h A - I^2 R0 alpha), the closed form of
    h A theta = I^2 R0 (1 + alpha theta).

SI units throughout (m, W, K, ohm).
"""
from __future__ import annotations

import math

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

K_CU = 385.0          # W/mK
K_FR4 = 0.3           # in-plane is ~0.8 for woven glass; 0.3 is the safe side
RHO_CU = 1.72e-8      # ohm m at 20 C
ALPHA_CU = 0.00393    # 1/K


def _links(nx: int, ny: int):
    """(a, b) cell index pairs of every x and y neighbour."""
    idx = np.arange(nx * ny).reshape(ny, nx)
    ax = np.stack([idx[:, :-1].ravel(), idx[:, 1:].ravel()], 1)
    ay = np.stack([idx[:-1, :].ravel(), idx[1:, :].ravel()], 1)
    return ax, ay


def _laplacian(gsheet: np.ndarray, dx: float, dy: float):
    """Conductance matrix of the in-plane links (W/K, or S for electrical),
    and the per-link conductances: g = sheet * (width / length), harmonic
    mean of the two half-cells."""
    ny, nx = gsheet.shape
    ax, ay = _links(nx, ny)
    s = gsheet.ravel()

    def harm(a, b):
        with np.errstate(divide="ignore", invalid="ignore"):
            g = np.where((a > 0) & (b > 0), 2 * a * b / (a + b), 0.0)
        return g

    gx = harm(s[ax[:, 0]], s[ax[:, 1]]) * (dy / dx)
    gy = harm(s[ay[:, 0]], s[ay[:, 1]]) * (dx / dy)
    links = np.concatenate([ax, ay])
    g = np.concatenate([gx, gy])
    n = nx * ny
    i, j = links[:, 0], links[:, 1]
    L = sp.coo_matrix((np.concatenate([g, g, -g, -g]),
                       (np.concatenate([i, j, i, j]), np.concatenate([i, j, j, i]))),
                      shape=(n, n)).tocsr()
    return L, links, g


def solve_plane(ksheet, h, q, dx: float, dy: float | None = None,
                fixed=None, t_amb: float = 0.0) -> np.ndarray:
    """Steady temperature (ny, nx) of a plane.

    ksheet  W/K per square (k t, summed over layers), array (ny, nx)
    h       W/m^2K to ambient, both faces together; scalar or (ny, nx)
    q       W into each cell, (ny, nx)
    fixed   optional (ny, nx) array, NaN where free, the held temperature else
    """
    dy = dy or dx
    ksheet = np.asarray(ksheet, float)
    ny, nx = ksheet.shape
    L, _, _ = _laplacian(ksheet, dx, dy)
    hv = np.broadcast_to(np.asarray(h, float), (ny, nx)).ravel() * dx * dy
    A = (L + sp.diags(hv)).tolil()
    b = np.asarray(q, float).ravel() + hv * t_amb
    if fixed is not None:
        f = np.asarray(fixed, float).ravel()
        held = np.flatnonzero(~np.isnan(f))
        A = A.tocsr()
        keep = np.ones(A.shape[0], bool)
        keep[held] = False
        free = np.flatnonzero(keep)
        T = np.empty(A.shape[0])
        T[held] = f[held]
        Aff = A[free][:, free]
        rhs = b[free] - A[free][:, held] @ f[held]
        T[free] = spla.spsolve(Aff.tocsc(), rhs)
        return T.reshape(ny, nx)
    return spla.spsolve(A.tocsc(), b).reshape(ny, nx)


def potential(sigma_sheet, v_fixed, dx: float, dy: float | None = None):
    """DC potential (ny, nx) of a conducting sheet (S per square) held at
    v_fixed (NaN where free). Returns (V, links, g) so callers can read the
    per-link current and heat."""
    dy = dy or dx
    s = np.asarray(sigma_sheet, float)
    ny, nx = s.shape
    L, links, g = _laplacian(s, dx, dy)
    f = np.asarray(v_fixed, float).ravel()
    held = np.flatnonzero(~np.isnan(f))
    keep = np.ones(nx * ny, bool)
    keep[held] = False
    conducting = np.zeros(nx * ny, bool)
    conducting[links[g > 0].ravel()] = True
    free = np.flatnonzero(keep & conducting)
    V = np.full(nx * ny, np.nan)
    V[held] = f[held]
    if free.size:
        Aff = L[free][:, free]
        rhs = -(L[free][:, held] @ f[held])
        V[free] = spla.spsolve(Aff.tocsc(), rhs)
    return V.reshape(ny, nx), links, g


def joule(cu_mask, t_cu: float, v_fixed, ksheet, h, dx: float,
          q_extra=None, t_amb: float = 25.0, rho0: float = RHO_CU,
          alpha: float = ALPHA_CU, t0: float = 20.0, tol: float = 1e-4,
          max_iter: int = 60) -> dict:
    """Coupled DC current + heat in one copper layer (cu_mask, thickness
    t_cu m) with electrodes at v_fixed (V; NaN elsewhere). Returns the
    temperature, the potential, the current through the first electrode,
    the Joule heat and how many passes it took."""
    cu = np.asarray(cu_mask, bool)
    ny, nx = cu.shape
    T = np.full((ny, nx), t_amb, float)
    q_extra = np.zeros((ny, nx)) if q_extra is None else np.asarray(q_extra, float)
    fixed = np.asarray(v_fixed, float)
    for it in range(1, max_iter + 1):
        rho = rho0 * (1 + alpha * (T - t0))
        sig = np.where(cu, t_cu / rho, 0.0)
        V, links, g = potential(sig, fixed, dx)
        v = V.ravel()
        dv = np.nan_to_num(v[links[:, 0]] - v[links[:, 1]])
        p_link = g * dv * dv
        q = np.zeros(nx * ny)
        np.add.at(q, links[:, 0], p_link / 2)
        np.add.at(q, links[:, 1], p_link / 2)
        Tn = solve_plane(ksheet, h, q.reshape(ny, nx) + q_extra, dx, t_amb=t_amb)
        done = float(np.max(np.abs(Tn - T))) < tol
        T = Tn
        if done:
            break
    src = np.isclose(fixed, np.nanmax(fixed)) & ~np.isnan(fixed)
    src_i = np.flatnonzero(src.ravel())
    out = np.isin(links[:, 0], src_i) & ~np.isin(links[:, 1], src_i)
    inn = np.isin(links[:, 1], src_i) & ~np.isin(links[:, 0], src_i)
    current = float(np.sum(g[out] * dv[out]) - np.sum(g[inn] * dv[inn]))
    return {"T": T, "V": V, "current": current, "heat": float(p_link.sum()),
            "iterations": it, "converged": done}


# ---------------------------------------------------------------- closed forms

def fin_tip_ratio(L: float, ksheet: float, h: float) -> float:
    """theta(L)/theta(0) of a 1D fin (adiabatic tip), m = sqrt(h/(k t))."""
    return 1 / math.cosh(math.sqrt(h / ksheet) * L)


def point_source_theta(P: float, r: float, ksheet: float, h: float) -> float:
    """Temperature rise at r from P on an infinite plate: P/(2 pi kt) K0(m r)."""
    from scipy.special import k0
    return P / (2 * math.pi * ksheet) * float(k0(math.sqrt(h / ksheet) * r))


def strip_selfheat(I: float, R0: float, hA: float, alpha: float = ALPHA_CU) -> float:
    """Rise of a uniform strip whose R = R0 (1 + alpha theta) carries I:
    hA theta = I^2 R0 (1 + alpha theta). R0 is at ambient."""
    d = hA - I * I * R0 * alpha
    if d <= 0:
        return math.inf          # thermal runaway: no steady state
    return I * I * R0 / d


def strip_selfheat_v(V: float, R0: float, hA: float, alpha: float = ALPHA_CU) -> float:
    """The same strip held at V across it: hA theta = V^2 / (R0 (1 + alpha
    theta)), so alpha theta^2 + theta - V^2/(R0 hA) = 0."""
    c = V * V / (R0 * hA)
    if alpha == 0:
        return c
    return (-1 + math.sqrt(1 + 4 * alpha * c)) / (2 * alpha)
