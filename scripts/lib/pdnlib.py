"""pdnlib.py - lumped PDN impedance chain and target-impedance mask (#490).

The pieces a power-integrity tool puts between a regulator and a die, as
complex impedances on a frequency grid, and the mask they must stay under.
check_pdn_z uses it per rail; the decap optimiser (#493) is meant to call
`chain_z` / `mask_margin` in its inner loop, so everything here is pure
numpy on plain numbers (no board, no files except the package table).

TARGET (Smith & Bogatin, Principles of Power Integrity ch.1-3; Sandler,
Keysight "target impedance" note; Novak, DesignCon "frequency-domain target
impedance"):

    Z_t = V * ripple% / 100 / dI_transient            (flat)

flat from DC up to f_hi, where the board stops being responsible:

    f_knee = 0.35 / t_edge        (the load's current edge, when given)
    f_pkg  = 1 / (2 pi sqrt(L_pkg * C_die))   (package/die take over)
    f_hi   = min of the ones known

ELEMENTS, each a complex Z(f):

    capacitor   ESR + j w (ESL_body + L_mount) + 1/(j w C_eff)
                C_eff = mlcc.c_eff at the rail's DC bias (class-II derate)
    VRM         R_out + j w L_out ; L_out = R_out / (2 pi f_bw) when only
                the loop bandwidth is known (flat to f_bw, inductive above:
                Sandler's two-element regulator model)
    spreading   R + j w L in series between a cap and the load node
    plane       1/(j w C_plane) (lumped; check_pdn_z has the modal cavity)
    package     R_pkg + j w L_pkg in series, C_die (+ESR) across the die

CHAIN: every board element sits in parallel at the board node (each with its
own series spreading L); the die sees (Z_board + Z_pkg) || Z_die.

CLOSED FORMS (the tests' known answers):
    series RLC: |Z| min = ESR exactly at f_srf = 1/(2 pi sqrt(L C))
    two branches in parallel: lossless loop resonance where X1 + X2 = 0,
        w_ar^2 = (1/C1 + 1/C2) / (L1 + L2), and the peak
        |Z| ~ X1(w_ar)^2 / (R1 + R2)   (high-Q limit)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

TWO_PI = 2.0 * math.pi
REF_PKG = Path(__file__).resolve().parents[2] / "reference" / "pkg_pdn.yaml"

# Body ESL of a 2-terminal MLCC by EIA size (nH): the part alone, without
# its pads/vias (those are the mounting inductance). Class values from the
# makers' SPICE models (Murata SimSurfing / KEMET K-SIM give 0.3-0.5 nH for
# 0402, 0.5-0.7 for 0603, ~0.7-1 for 0805/1206).
ESL_BODY_NH = {"0201": 0.25, "0402": 0.35, "0603": 0.5, "0805": 0.6,
               "1206": 0.8, "1210": 0.9, "1812": 1.0, "2220": 1.1}
ESL_BODY_DEFAULT_NH = 0.6           # unknown package: an 0805-class part
ESL_BULK_NH = 3.0                   # electrolytic / polymer / tantalum can
# Ceramic ESR at its SRF by capacitance decade (ohm): makers' curves put a
# 100 nF X7R near 20-40 mOhm and a 10 uF X5R near 3-8 mOhm.
ESR_CERAMIC = ((10e-9, 0.10), (100e-9, 0.03), (1e-6, 0.015),
               (10e-6, 0.006), (math.inf, 0.004))
ESR_BULK_OHM = {"electrolytic": 0.10, "tantalum": 0.10, "polymer": 0.02,
                "film": 0.01}
RIPPLE_PCT_DEFAULT = 5.0
# A regulator with no data: R_out ~ 10 mOhm flat to a 50 kHz loop
# bandwidth (a typical small buck/LDO closed loop); flagged when used.
VRM_DEFAULT = {"r_out_mohm": 10.0, "f_bw_khz": 50.0}
SPREAD_NH_PER_MM = 0.4      # path from a cap to the load with no plane pair


# ============================================================ elements

def omega(freqs) -> np.ndarray:
    return TWO_PI * np.asarray(freqs, dtype=float)


def rlc_z(freqs, c_f: float | None, l_h: float = 0.0,
          r_ohm: float = 0.0) -> np.ndarray:
    """Series R + jwL + 1/(jwC); c_f None/0 means no capacitor (an R-L)."""
    w = omega(freqs)
    z = r_ohm + 1j * w * l_h
    if c_f:
        z = z + 1.0 / (1j * w * c_f)
    return z


def parallel(*zs) -> np.ndarray:
    """Parallel combination of complex impedance arrays (same grid)."""
    y = sum(1.0 / np.asarray(z, dtype=complex) for z in zs)
    return 1.0 / y


@dataclass
class Cap:
    """One decoupling branch. `l_mount_h` is pads + vias + any spreading
    path to the load node; `esl_h` the part's body inductance."""
    name: str
    c_f: float
    esr_ohm: float
    esl_h: float
    l_mount_h: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def l_total_h(self) -> float:
        return self.esl_h + self.l_mount_h

    def z(self, freqs) -> np.ndarray:
        return rlc_z(freqs, self.c_f, self.l_total_h, self.esr_ohm)

    def srf_hz(self) -> float:
        return srf_hz(self.c_f, self.l_total_h)


@dataclass
class VRM:
    r_out_ohm: float
    l_out_h: float
    assumed: bool = False
    source: str = ""

    @classmethod
    def from_spec(cls, spec: dict | None) -> "VRM":
        """{r_out_mohm, l_out_nh | f_bw_khz}; None/{} -> flagged default."""
        assumed = not spec
        spec = dict(VRM_DEFAULT if not spec else spec)
        r = float(spec.get("r_out_mohm", VRM_DEFAULT["r_out_mohm"])) * 1e-3
        if spec.get("l_out_nh") is not None:
            l_h = float(spec["l_out_nh"]) * 1e-9
            src = "r_out + l_out"
        else:
            f_bw = float(spec.get("f_bw_khz", VRM_DEFAULT["f_bw_khz"])) * 1e3
            l_h = vrm_l_from_bw(r, f_bw)
            src = f"r_out flat to f_bw {f_bw / 1e3:g} kHz"
        return cls(r, l_h, assumed, "default " + src if assumed else src)

    def z(self, freqs) -> np.ndarray:
        return rlc_z(freqs, None, self.l_out_h, self.r_out_ohm)


@dataclass
class Package:
    l_pkg_h: float
    c_die_f: float
    r_pkg_ohm: float = 0.0
    r_die_ohm: float = 0.0
    assumed: bool = False
    source: str = ""

    def z_series(self, freqs) -> np.ndarray:
        return rlc_z(freqs, None, self.l_pkg_h, self.r_pkg_ohm)

    def z_die(self, freqs) -> np.ndarray:
        return rlc_z(freqs, self.c_die_f, 0.0, self.r_die_ohm)

    def f_corner_hz(self) -> float:
        return srf_hz(self.c_die_f, self.l_pkg_h)


def vrm_l_from_bw(r_out_ohm: float, f_bw_hz: float) -> float:
    """The inductance whose reactance equals R_out at the loop bandwidth."""
    return r_out_ohm / (TWO_PI * f_bw_hz)


# ============================================================ closed forms

def srf_hz(c_f: float, l_h: float) -> float:
    return 1.0 / (TWO_PI * math.sqrt(l_h * c_f))


def antiresonance(c1: float, l1: float, r1: float,
                  c2: float, l2: float, r2: float) -> tuple[float, float]:
    """(f_ar, |Z|_peak) of two series-RLC branches in parallel, high-Q
    closed form: X1 + X2 = 0 at w_ar, peak = X1^2 / (R1 + R2)."""
    w = math.sqrt((1.0 / c1 + 1.0 / c2) / (l1 + l2))
    x1 = w * l1 - 1.0 / (w * c1)
    return w / TWO_PI, x1 * x1 / (r1 + r2)


def target_ohm(v: float, ripple_pct: float, i_step_a: float) -> float:
    """Z_t = V * ripple% / dI."""
    if v <= 0 or ripple_pct <= 0 or i_step_a <= 0:
        raise ValueError("target needs V, ripple% and dI all > 0")
    return v * ripple_pct / 100.0 / i_step_a


def knee_hz(edge_ns: float) -> float:
    """0.35 / t_rise: the bandwidth of a current edge."""
    return 0.35 / (edge_ns * 1e-9)


def target_band(z_t: float, edge_ns: float | None = None,
                f_max_hz: float | None = None,
                pkg: Package | None = None) -> dict:
    """{z_t_ohm, f_hi_hz, f_hi_from, terms} - the flat mask and where the
    board's responsibility ends (min of knee, explicit f_max, package)."""
    cands = []
    if edge_ns:
        cands.append((knee_hz(edge_ns), "edge 0.35/t_r"))
    if f_max_hz:
        cands.append((float(f_max_hz), "f_max"))
    if pkg is not None:
        cands.append((pkg.f_corner_hz(), "package L_pkg*C_die"))
    if not cands:
        return {"z_t_ohm": z_t, "f_hi_hz": None, "f_hi_from": None,
                "terms": {}}
    f_hi, why = min(cands)
    return {"z_t_ohm": z_t, "f_hi_hz": f_hi, "f_hi_from": why,
            "terms": {w: f for f, w in cands}}


# ============================================================ chain

def chain_z(freqs, caps: list[Cap] = (), vrm: VRM | None = None,
            plane_c_f: float | None = None, pkg: Package | None = None,
            extra_board=None) -> dict:
    """{'board': Z at the board node, 'die': Z seen by the die (None
    without a package)}. `extra_board` = further complex Z arrays in
    parallel at the node (check_pdn_z passes its loaded cavity there)."""
    freqs = np.asarray(freqs, dtype=float)
    branches = [c.z(freqs) for c in caps]
    if vrm is not None:
        branches.append(vrm.z(freqs))
    if plane_c_f:
        branches.append(rlc_z(freqs, plane_c_f))
    if extra_board is not None:
        branches.extend(extra_board)
    if not branches:
        raise ValueError("chain_z needs at least one branch")
    board = parallel(*branches)
    die = None
    if pkg is not None:
        die = parallel(board + pkg.z_series(freqs), pkg.z_die(freqs))
    return {"board": board, "die": die}


def mask_margin(freqs, z, z_t: float, f_lo: float | None = None,
                f_hi: float | None = None) -> dict:
    """How |z| sits against a flat target over [f_lo, f_hi]:
    {worst_ratio, f_worst_hz, z_worst_ohm, over: [[f_from, f_to, z_max]]}.
    worst_ratio > 1 means the mask is broken."""
    freqs = np.asarray(freqs, dtype=float)
    mag = np.abs(np.asarray(z))
    sel = np.ones_like(freqs, dtype=bool)
    if f_lo is not None:
        sel &= freqs >= f_lo
    if f_hi is not None:
        sel &= freqs <= f_hi
    idx = np.nonzero(sel)[0]
    if not len(idx):
        return {"worst_ratio": None, "f_worst_hz": None, "z_worst_ohm": None,
                "over": []}
    i = idx[int(np.argmax(mag[idx]))]
    over, start = [], None
    for k in idx:
        if mag[k] > z_t and start is None:
            start = k
        if start is not None and (mag[k] <= z_t or k == idx[-1]):
            end = k if mag[k] > z_t else k - 1
            over.append([float(freqs[start]), float(freqs[end]),
                         float(mag[start:end + 1].max())])
            start = None
    return {"worst_ratio": float(mag[i] / z_t), "f_worst_hz": float(freqs[i]),
            "z_worst_ohm": float(mag[i]), "over": over}


# ============================================================ parts

def esr_default(c_f: float, kind: str = "class2") -> float:
    if kind in ESR_BULK_OHM:
        return ESR_BULK_OHM[kind]
    for c_max, esr in ESR_CERAMIC:
        if c_f <= c_max:
            return esr
    return ESR_CERAMIC[-1][1]


def cap_from_part(name: str, value: str, c_f: float, v_rail: float, *,
                  footprint: str = "", mpn: str = "",
                  esr_ohm: float | None = None, esl_nh: float | None = None,
                  l_mount_nh: float = 0.0) -> Cap:
    """A Cap with C derated at the rail's DC bias (mlcc.c_eff), ESR/ESL from
    the class tables unless given. meta says where each number came from."""
    import mlcc  # noqa: PLC0415
    ce = mlcc.c_eff(c_f, v_rail, footprint=footprint, value=value or "",
                    mpn=mpn)
    kind = ce["kind"]
    if esl_nh is None:
        if kind in ("electrolytic", "tantalum"):
            esl_nh, esl_src = ESL_BULK_NH, "bulk default"
        else:
            esl_nh = ESL_BODY_NH.get(ce["package"] or "", ESL_BODY_DEFAULT_NH)
            esl_src = ("body " + ce["package"]) if ce["package"] \
                else "body default"
    else:
        esl_src = "given"
    esr_src = "given" if esr_ohm is not None else "class default"
    if esr_ohm is None:
        esr_ohm = esr_default(c_f, kind)
    return Cap(name, ce["c_eff"], esr_ohm, esl_nh * 1e-9, l_mount_nh * 1e-9,
               meta={"c_nom_f": c_f, "c_eff_f": ce["c_eff"],
                     "derate": round(ce["fraction"], 4), "kind": kind,
                     "dielectric": ce["dielectric"], "package": ce["package"],
                     "curve": ce["curve"], "esr_src": esr_src,
                     "esl_src": esl_src})


def load_pkg_table(path: Path = REF_PKG) -> list[dict]:
    if not path.is_file():
        return []
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(
        "packages") or []


def package_for(texts: list[str], footprint: str = "",
                spec: dict | None = None,
                table: list[dict] | None = None) -> Package:
    """The package/die model: an explicit `spec` {l_pkg_nh, c_die_nf,
    r_pkg_mohm, r_die_mohm}, else the first table row whose `match` hits a
    part text (MPN/value), else the first `footprint` class row, else the
    table's `default` row - flagged assumed unless the row is verified."""
    def build(row: dict, src: str, assumed: bool) -> Package:
        return Package(float(row["l_pkg_nh"]) * 1e-9,
                       float(row["c_die_nf"]) * 1e-9,
                       float(row.get("r_pkg_mohm", 0.0)) * 1e-3,
                       float(row.get("r_die_mohm", 0.0)) * 1e-3,
                       assumed, src)
    if spec:
        return build(spec, "constraints", False)
    rows = load_pkg_table() if table is None else table
    for r in rows:
        if r.get("match") and any(re.search(r["match"], t or "", re.I)
                                  for t in texts):
            return build(r, r.get("source", r["match"]),
                         not r.get("verified", False))
    for r in rows:
        if r.get("footprint") and re.search(r["footprint"], footprint or "",
                                            re.I):
            return build(r, "class " + r["footprint"], True)
    for r in rows:
        if r.get("default"):
            return build(r, "generic default", True)
    return build({"l_pkg_nh": 0.5, "c_die_nf": 5.0}, "generic default", True)


# ============================================================ ngspice

def ladder_spice(caps: list[Cap], vrm: VRM | None = None,
                 plane_c_f: float | None = None, pkg: Package | None = None,
                 f_points=(), title: str = "pdn ladder") -> str:
    """The same chain as a SPICE AC deck: 1 A AC into the board node (and
    the die node with a package), `.meas ac zb<k> find vm(nb) at=f`
    (and zd<k> on vm(nd)) per point. For cross-checking chain_z."""
    lines = [f"* {title}", "Iin 0 nb dc 0 ac 1"]
    if pkg is not None:
        lines = [f"* {title}", "Iin 0 nd dc 0 ac 1",
                 f"Rpkg nd npk {max(pkg.r_pkg_ohm, 1e-9):.9g}",
                 f"Lpkg npk nb {pkg.l_pkg_h:.9g}",
                 f"Cdie nd ndr {pkg.c_die_f:.9g}",
                 f"Rdie ndr 0 {max(pkg.r_die_ohm, 1e-9):.9g}"]
    for k, c in enumerate(caps):
        lines += [f"Rc{k} nb nc{k}a {c.esr_ohm:.9g}",
                  f"Lc{k} nc{k}a nc{k}b {c.l_total_h:.9g}",
                  f"Cc{k} nc{k}b 0 {c.c_f:.9g}"]
    if vrm is not None:
        lines += [f"Rv nb nv {vrm.r_out_ohm:.9g}",
                  f"Lv nv 0 {vrm.l_out_h:.9g}"]
    if plane_c_f:
        lines += [f"Cpl nb 0 {plane_c_f:.9g}"]
    lines.append(".ac dec 2000 1e3 1e9")
    node = "nd" if pkg is not None else "nb"
    for k, f in enumerate(f_points):
        lines.append(f".meas ac z{k} find vm({node}) at={f:.9g}")
    lines.append(".end")
    return "\n".join(lines) + "\n"
