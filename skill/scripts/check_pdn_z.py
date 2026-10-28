"""check_pdn_z.py - plane-pair cavity PDN impedance |Z|(f) (layout sim).

Rectangular plane-pair cavity-resonator model (Lei/Techentin/Gilbert TMTT
47(5) 1999; Novak & Miller ch.4; Xu/Wang/Fan NSF 10213240 - formulas
reimplemented from the published equations, no third-party code):

  Z_ij(w) = (j*w*mu0*d / (a*b)) * SUM_{m=0..M} SUM_{n=0..N}
            [ c_m^2 * c_n^2 * f_i * f_j ] / ( k_xm^2 + k_yn^2 - k^2 )

  k_xm = m*pi/a ; k_yn = n*pi/b ; c_m^2 = 1 (m=0) else 2  => weights 1/2/4
  f_i  = cos(k_xm*x_i)*cos(k_yn*y_i)*sinc(k_xm*t_xi/2)*sinc(k_yn*t_yi/2)
  sinc(x) = sin(x)/x, sinc(0) = 1 (point ports: sinc -> 1)

Light-loss wavenumber with the MANDATORY low-frequency clip (Novak-Miller
eq 4.29) - without the clip the low end departs from the true 1/(j*w*C00):

  k = w*sqrt(mu0*eps0*eps_r) * (1 - j*(tan_delta + delta_mod/d)/2)
  delta_s   = sqrt(2/(w*mu0*sigma_cu)),  sigma_cu = 5.8e7 S/m
  delta_mod = 1/(1/delta_s + 1/t),  t = plane copper thickness

The (0,0) term is exactly 1/(j*w*C00), C00 = eps0*eps_r*a*b/d. The model is
quasi-static (only (m,n,0) modes) - valid through the whole PCB PDN band for
0.1-1 mm gaps - and non-causal (fine for |Z|(f) profiles).

TRUNCATION: M=N = max(mode-coverage bound for f_max with 2.5x margin, 30);
peaks converge fast, MINIMA slowly, so the sum is recomputed once at 2M/2N
and the first-impedance-minimum shift is reported (warning kind
cavity_unconverged above 5%).

PLANE PAIRS (v1): adjacent copper layers where two DIFFERENT nets both carry
zone fills >= 15% of the board outline area; one of them must be a rail named
in decoupling.json. Geometry = bounding rectangle of the overlapping fill,
flagged `"geometry": "bounding_rect_assumed"`; a x b x d from geom's stackup.

PORTS: port 0 = the observation port at the served IC (the most-associated IC
for the rail in decoupling.json; position = centroid of its associated rail
pin pads), then one port per decap (rail pad center). Port dims = pad bounds
(the cos*sinc port factors above).

DECAP BRANCHES: Z_c = ESR + j*w*ESL + 1/(j*w*C). C parses from the
association value (check_decoupling.parse_farads). ESL uses check_decoupling's
loop-inductance heuristic constants (kicad-happy DC-003: 0.7 nH/mm of leg +
1 nH per via): leg = distance from the cap's rail/return pad to the nearest
same-net via (each leg that ends in a via counts one via). ESR defaults to
20 mOhm (documented; X5R/X7R MLCC class). Loading identity (NSF eqs 4-5):
Z_loaded = (E + Z*Y_C)^-1 * Z with Y_C diagonal, 0 at the observation port.

OUTPUT: |Z|(f) facts on a log grid 10 kHz - 200 MHz (301 points): peaks
(antiresonances) with frequency/magnitude, C00, first minimum. The full curve
goes to a sidecar JSON via --curve-out (never inline). Violations only when
the rail's constraints power entry carries "pdn_target_mohm" (kind
pdn_z_excess); otherwise facts-only advisory.

LICENSE NOTE: KiPIDA (AGPL) was consulted as approach validation only; all
code here is written from the published equations above.

CLI: --pcb board.kicad_pcb --metadata decoupling.json
     [--constraints constraints.json] [--curve-out curve.json] [--out report.json]
     exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import checklib  # noqa: E402
import geom  # noqa: E402
from check_decoupling import (NH_PER_MM, NH_PER_VIA, nearest_via_mm,  # noqa: E402
                              parse_farads)
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "check_pdn_z"

MU0 = 4e-7 * math.pi
EPS0 = 8.8541878128e-12
C_LIGHT = 299792458.0
SIGMA_CU = 5.8e7            # S/m
TAN_DELTA = 0.02            # FR4 default loss tangent
ESR_DEFAULT_OHM = 0.020     # documented MLCC ESR default
F_MIN_HZ = 1e4
F_MAX_HZ = 2e8
N_FREQ = 301
LARGE_FILL_FRAC = 0.15      # "large fill" = this fraction of outline area
M_FLOOR = 30                # truncation floor per the contract recipe
COVERAGE_MARGIN = 2.5       # mode-coverage margin over f_max
FMIN_SHIFT_WARN = 0.05      # first-minimum shift gate on the 2M check


# ============================================================ cavity model

def sinc(x):
    """sin(x)/x with sinc(0)=1 (np.sinc is the normalized variant)."""
    return np.sinc(np.asarray(x, dtype=float) / np.pi)


def skin_depth_m(w: float) -> float:
    return math.sqrt(2.0 / (w * MU0 * SIGMA_CU))


def delta_mod_m(w: float, t_cu_m: float) -> float:
    """Novak-Miller eq 4.29 low-frequency clip of the skin depth."""
    ds = skin_depth_m(w)
    return 1.0 / (1.0 / ds + 1.0 / t_cu_m)


def lossy_k(w: float, eps_r: float, d_m: float, t_cu_m: float,
            tan_delta: float = TAN_DELTA) -> complex:
    dm = delta_mod_m(w, t_cu_m)
    return (w * math.sqrt(MU0 * EPS0 * eps_r)
            * (1.0 - 1j * (tan_delta + dm / d_m) / 2.0))


def cavity_z(freqs_hz, a_m: float, b_m: float, d_m: float, eps_r: float,
             ports, m_max: int, n_max: int, tan_delta: float = TAN_DELTA,
             t_cu_m: float = 35e-6) -> np.ndarray:
    """Bare plane-pair impedance matrix, shape (F, P, P) complex.

    `ports` = [(x_m, y_m, tx_m, ty_m), ...] in the rectangle frame (meters).
    """
    freqs = np.asarray(freqs_hz, dtype=float)
    kx = np.arange(m_max + 1) * math.pi / a_m
    ky = np.arange(n_max + 1) * math.pi / b_m
    c2m = np.where(np.arange(m_max + 1) > 0, 2.0, 1.0)
    c2n = np.where(np.arange(n_max + 1) > 0, 2.0, 1.0)
    sqw = np.sqrt(np.outer(c2m, c2n))          # sqrt(c_m^2 * c_n^2)
    b_rows = []
    for (px, py, tx, ty) in ports:
        f_p = (np.outer(np.cos(kx * px) * sinc(kx * tx / 2.0),
                        np.cos(ky * py) * sinc(ky * ty / 2.0)))
        b_rows.append((f_p * sqw).ravel())
    bmat = np.vstack(b_rows)                   # (P, modes)
    kx2ky2 = (kx[:, None] ** 2 + ky[None, :] ** 2).ravel()
    n_p = len(ports)
    z = np.empty((len(freqs), n_p, n_p), dtype=complex)
    for i, f in enumerate(freqs):
        w = 2.0 * math.pi * f
        k = lossy_k(w, eps_r, d_m, t_cu_m, tan_delta)
        amp = 1.0 / (kx2ky2 - k * k)
        z[i] = (1j * w * MU0 * d_m / (a_m * b_m)) * ((bmat * amp) @ bmat.T)
    return z


def branch_z(freqs_hz, c_f: float, esl_h: float, esr_ohm: float) -> np.ndarray:
    w = 2.0 * math.pi * np.asarray(freqs_hz, dtype=float)
    return esr_ohm + 1j * w * esl_h + 1.0 / (1j * w * c_f)


def load_decaps(z: np.ndarray, y_ports: np.ndarray) -> np.ndarray:
    """Z_loaded = (E + Z*Y_C)^-1 * Z per frequency (NSF eqs 4-5).

    y_ports: (F, P) branch admittances, 0 at observation ports."""
    n_p = z.shape[1]
    eye = np.eye(n_p, dtype=complex)
    a = eye[None, :, :] + z * y_ports[:, None, :]   # column-scaled Z
    return np.linalg.solve(a, z)


def c00_farads(a_m: float, b_m: float, d_m: float, eps_r: float) -> float:
    return EPS0 * eps_r * a_m * b_m / d_m


def mode_count(dim_m: float, eps_r: float, f_max_hz: float) -> int:
    """Truncation per axis: coverage bound with margin, floored at M_FLOOR."""
    m = math.ceil(2.0 * dim_m * math.sqrt(eps_r)
                  * f_max_hz * COVERAGE_MARGIN / C_LIGHT)
    return max(m, M_FLOOR)


def local_maxima(zmag: np.ndarray) -> list[int]:
    out = []
    for i in range(1, len(zmag) - 1):
        if zmag[i] > zmag[i - 1] and zmag[i] >= zmag[i + 1]:
            out.append(i)
    return out


def first_local_min(zmag: np.ndarray):
    for i in range(1, len(zmag) - 1):
        if zmag[i] < zmag[i - 1] and zmag[i] <= zmag[i + 1]:
            return i
    return None


# ============================================================ board -> pairs

def find_pairs(bg: geom.BoardGeom, rails: set[str]) -> list[dict]:
    """Adjacent-layer (rail, reference) plane pairs with large fills."""
    outline_area = bg.outline.area
    if outline_area <= 0:
        outline_area = max((bg.zone_fill(n, l).area for n in bg.nets
                            for l in bg.layers_with_zone(n)), default=0.0)
    if outline_area <= 0:
        return []
    floor = LARGE_FILL_FRAC * outline_area
    fills: dict[str, list[tuple[str, object]]] = {}
    for net in sorted(n for n in bg.nets if n):
        for layer in bg.layers_with_zone(net):
            f = bg.zone_fill(net, layer)
            if f.area >= floor:
                fills.setdefault(layer, []).append((net, f))
    pairs = []
    for la, lb in zip(bg.copper_layers, bg.copper_layers[1:]):
        for na, fa in fills.get(la, []):
            for nb, fb in fills.get(lb, []):
                if na == nb:
                    continue
                if na in rails and nb not in rails:
                    rail, ref = na, nb
                    rail_layer, ref_layer = la, lb
                    rail_fill, ref_fill = fa, fb
                elif nb in rails and na not in rails:
                    rail, ref = nb, na
                    rail_layer, ref_layer = lb, la
                    rail_fill, ref_fill = fb, fa
                else:
                    continue
                inter = rail_fill.intersection(ref_fill)
                if inter.is_empty or inter.area < 0.25 * min(rail_fill.area,
                                                             ref_fill.area):
                    continue
                minx, miny, maxx, maxy = inter.bounds
                if maxx - minx < 1.0 or maxy - miny < 1.0:
                    continue
                pairs.append({
                    "rail": rail, "reference": ref,
                    "rail_layer": rail_layer, "ref_layer": ref_layer,
                    "origin": (minx, miny),
                    "a_mm": maxx - minx, "b_mm": maxy - miny,
                    "d_mm": bg.stackup.height_between(la, lb),
                    "eps_r": bg.stackup.epsilon_between(la, lb),
                    "t_cu_mm": min(bg.stackup.copper_thickness[la],
                                   bg.stackup.copper_thickness[lb]),
                })
    return pairs


def cap_esl_nh(bg: geom.BoardGeom, cap: str, rail: str, ref_net: str):
    """Mounting ESL from the check_decoupling heuristic constants
    (0.7 nH/mm leg + 1 nH per via; kicad-happy DC-003 + spec via heuristic)."""
    legs = 0.0
    vias = 0
    detail = {}
    for net, key in ((rail, "rail_leg_mm"), (ref_net, "return_leg_mm")):
        pads = bg.pads_of(net=net, ref=cap)
        leg = nearest_via_mm(bg, net, pads[0].center) if pads else None
        if leg is not None:
            legs += leg
            vias += 1
            detail[key] = checklib.rnd(leg)
        else:
            detail[key] = None
    return NH_PER_MM * legs + NH_PER_VIA * vias, detail


def build_ports(bg: geom.BoardGeom, pair: dict, assocs: list[dict]):
    """(ports_m, obs, decaps, skipped) - port 0 is the observation port."""
    rail = pair["rail"]
    ox, oy = pair["origin"]
    a_m = pair["a_mm"] * 1e-3
    b_m = pair["b_mm"] * 1e-3

    def to_port(center, size) -> tuple[float, float, float, float]:
        x = min(max((center[0] - ox) * 1e-3, 0.0), a_m)
        y = min(max((center[1] - oy) * 1e-3, 0.0), b_m)
        return (x, y, size[0] * 1e-3, size[1] * 1e-3)

    rail_assocs = [a for a in assocs if a.get("rail") == rail]
    if not rail_assocs:
        return None, None, [], []
    counts: dict[str, int] = {}
    for a in rail_assocs:
        counts[a["ic"]] = counts.get(a["ic"], 0) + 1
    obs_ic = sorted(counts, key=lambda r: (-counts[r], r))[0]
    pin_nums = {str(a.get("pin")) for a in rail_assocs if a["ic"] == obs_ic}
    pin_pads = [p for p in bg.pads_of(net=rail, ref=obs_ic)
                if p.number in pin_nums] or bg.pads_of(net=rail, ref=obs_ic)
    if not pin_pads:
        return None, None, [], []
    cx = sum(p.center[0] for p in pin_pads) / len(pin_pads)
    cy = sum(p.center[1] for p in pin_pads) / len(pin_pads)
    psize = (max(p.size[0] for p in pin_pads), max(p.size[1] for p in pin_pads))
    ports = [to_port((cx, cy), psize)]
    obs = {"ic": obs_ic, "pos": [checklib.rnd(cx), checklib.rnd(cy)],
           "pins": sorted(pin_nums)}

    decaps: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for a in rail_assocs:
        cap = a.get("cap")
        if not cap or cap in seen:
            continue
        seen.add(cap)
        pads = bg.pads_of(net=rail, ref=cap)
        c_f = parse_farads(a.get("value"))
        if not pads or not c_f:
            skipped.append({"cap": cap,
                            "reason": "no rail pad" if not pads
                            else f"unparseable value {a.get('value')!r}"})
            continue
        pad = pads[0]
        esl_nh, esl_detail = cap_esl_nh(bg, cap, rail, pair["reference"])
        ports.append(to_port(pad.center, pad.size))
        decaps.append({"cap": cap, "value": a.get("value"),
                       "c_f": c_f, "esl_nh": checklib.rnd(esl_nh),
                       "esr_mohm": checklib.rnd(ESR_DEFAULT_OHM * 1e3),
                       "pos": [checklib.rnd(pad.center[0]),
                               checklib.rnd(pad.center[1])],
                       **esl_detail})
    return ports, obs, decaps, skipped


# ============================================================ analysis

def _zin_mohm(freqs, pair, ports, decaps, m, n) -> tuple[np.ndarray, np.ndarray]:
    """(|Z_loaded|, |Z_bare|) at the observation port, in mOhm."""
    z = cavity_z(freqs, pair["a_mm"] * 1e-3, pair["b_mm"] * 1e-3,
                 pair["d_mm"] * 1e-3, pair["eps_r"], ports, m, n,
                 t_cu_m=pair["t_cu_mm"] * 1e-3)
    y = np.zeros((len(freqs), len(ports)), dtype=complex)
    for k, dc in enumerate(decaps):
        zb = branch_z(freqs, dc["c_f"], dc["esl_nh"] * 1e-9,
                      ESR_DEFAULT_OHM)
        y[:, k + 1] = 1.0 / zb
    zl = load_decaps(z, y)
    return (np.abs(zl[:, 0, 0]) * 1e3, np.abs(z[:, 0, 0]) * 1e3)


def analyze_pair(bg: geom.BoardGeom, pair: dict, assocs: list[dict],
                 target_mohm):
    """(facts, violations, curve) for one plane pair; facts is None when the
    pair has no observation port (no associations for its rail)."""
    ports, obs, decaps, skipped = build_ports(bg, pair, assocs)
    if ports is None:
        return None, [], None
    freqs = np.logspace(math.log10(F_MIN_HZ), math.log10(F_MAX_HZ), N_FREQ)
    m = mode_count(pair["a_mm"] * 1e-3, pair["eps_r"], F_MAX_HZ)
    n = mode_count(pair["b_mm"] * 1e-3, pair["eps_r"], F_MAX_HZ)
    zin, zbare = _zin_mohm(freqs, pair, ports, decaps, m, n)
    zin2, _ = _zin_mohm(freqs, pair, ports, decaps, 2 * m, 2 * n)

    i_min = first_local_min(zin)
    i_min2 = first_local_min(zin2)
    shift = None
    if i_min is not None and i_min2 is not None:
        shift = abs(freqs[i_min2] - freqs[i_min]) / freqs[i_min]
    peaks = [{"f_hz": checklib.rnd(freqs[i], 1),
              "z_mohm": checklib.rnd(zin[i])}
             for i in local_maxima(zin)][:8]
    i_worst = int(np.argmax(zin))
    c00 = c00_farads(pair["a_mm"] * 1e-3, pair["b_mm"] * 1e-3,
                     pair["d_mm"] * 1e-3, pair["eps_r"])

    violations: list[dict] = []
    if shift is not None and shift > FMIN_SHIFT_WARN:
        violations.append(violation(
            SCRIPT, "warning", None, None, pair["rail"], [],
            f"{pair['rail']}/{pair['reference']} cavity sum not converged: "
            f"first |Z| minimum moved {shift:.1%} between M={m} and M={2 * m}",
            SCRIPT, kind="cavity_unconverged",
            shift=checklib.rnd(shift)))
    # The target gates the modeled ANTIRESONANCE PEAKS only: with no VRM
    # branch (low end) and no package/die capacitance (high end), the
    # band-max always sits at a band edge - a model validity limit, not a
    # layout property (adversarial finding B-1).
    if target_mohm is not None:
        for pk in peaks:
            if pk["z_mohm"] > float(target_mohm):
                violations.append(violation(
                    SCRIPT, "error", tuple(obs["pos"]), pair["rail_layer"],
                    pair["rail"], [obs["ic"]],
                    f"{pair['rail']} PDN antiresonance |Z| = "
                    f"{pk['z_mohm']:.1f} mOhm at {pk['f_hz'] / 1e6:.2f} MHz "
                    f"exceeds pdn_target_mohm {float(target_mohm):.1f} "
                    f"(plane pair {pair['rail_layer']}/{pair['ref_layer']}, "
                    f"{len(decaps)} decaps)", SCRIPT,
                    kind="pdn_z_excess", z_mohm=pk["z_mohm"],
                    f_hz=pk["f_hz"], target_mohm=float(target_mohm)))

    facts = {
        "rail": pair["rail"], "reference": pair["reference"],
        "layers": [pair["rail_layer"], pair["ref_layer"]],
        "geometry": "bounding_rect_assumed",
        "origin": [checklib.rnd(pair["origin"][0]),
                   checklib.rnd(pair["origin"][1])],
        "a_mm": checklib.rnd(pair["a_mm"]), "b_mm": checklib.rnd(pair["b_mm"]),
        "d_mm": checklib.rnd(pair["d_mm"], 5),
        "epsilon_r": checklib.rnd(pair["eps_r"]),
        "t_cu_mm": checklib.rnd(pair["t_cu_mm"], 5),
        "c00_nf": checklib.rnd(c00 * 1e9, 5),
        "modes": {"m": m, "n": n, "m_check": 2 * m,
                  "first_min": None if i_min is None else
                  {"f_hz": checklib.rnd(freqs[i_min], 1),
                   "z_mohm": checklib.rnd(zin[i_min])},
                  "first_min_check": None if i_min2 is None else
                  {"f_hz": checklib.rnd(freqs[i_min2], 1),
                   "z_mohm": checklib.rnd(zin2[i_min2])},
                  "first_min_shift": None if shift is None
                  else checklib.rnd(shift)},
        "obs": obs,
        "decaps": decaps,
        "skipped_caps": skipped,
        "peaks": peaks,
        "z_max": {"mohm": checklib.rnd(zin[i_worst]),
                  "f_hz": checklib.rnd(freqs[i_worst], 1),
                  "note": "band-edge model artifact (no VRM/package model) "
                          "- judge peaks/first_min, not this"},
        "target_mohm": None if target_mohm is None else float(target_mohm),
    }
    curve = {
        "rail": pair["rail"], "reference": pair["reference"],
        "layers": [pair["rail_layer"], pair["ref_layer"]],
        "freq_hz": [checklib.rnd(f, 3) for f in freqs],
        "z_mohm": [checklib.rnd(v) for v in zin],
        "z_bare_mohm": [checklib.rnd(v) for v in zbare],
    }
    return facts, violations, curve


# ============================================================ CLI

def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Plane-pair cavity PDN impedance |Z|(f).")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--metadata", required=True,
                    help="decoupling.json (cap<->pin associations)")
    ap.add_argument("--constraints",
                    help="constraints.json (optional pdn_target_mohm per rail)")
    ap.add_argument("--curve-out",
                    help="write the |Z|(f) curves to this sidecar JSON")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    meta = checklib.load_json(args.metadata, "decoupling metadata")
    assocs = meta.get("associations", [])
    targets: dict[str, float] = {}
    if args.constraints:
        cons = checklib.load_json(args.constraints, "constraints")
        for entry in cons.get("power", []):
            if entry.get("pdn") is False:
                continue
            if entry.get("net") and entry.get("pdn_target_mohm") is not None:
                targets[entry["net"]] = float(entry["pdn_target_mohm"])

    bg = geom.load_board(Path(args.pcb))
    bg.assert_fresh()

    rails = {a.get("rail") for a in assocs if a.get("rail")}
    pairs = find_pairs(bg, rails)
    paired_rails = {p["rail"] for p in pairs}
    skipped_rails = [{"rail": r,
                      "reason": "no plane pair detected (rail has decoupling "
                                "associations but no adjacent-layer fill "
                                "pair)"}
                     for r in sorted(rails - paired_rails)]
    violations: list[dict] = []
    checked: list[dict] = []
    curves: list[dict] = []
    skipped_pairs: list[dict] = []
    for pair in pairs:
        facts, vs, curve = analyze_pair(bg, pair, assocs,
                                        targets.get(pair["rail"]))
        if facts is None:
            skipped_pairs.append({"rail": pair["rail"],
                                  "reference": pair["reference"],
                                  "layers": [pair["rail_layer"],
                                             pair["ref_layer"]],
                                  "reason": "no decoupling associations "
                                            "for this rail"})
            continue
        violations.extend(vs)
        checked.append(facts)
        curves.append(curve)

    if args.curve_out:
        Path(args.curve_out).write_text(
            json.dumps({"script": SCRIPT, "board": Path(args.pcb).name,
                        "f_min_hz": F_MIN_HZ, "f_max_hz": F_MAX_HZ,
                        "points": N_FREQ, "pairs": curves}, indent=1),
            encoding="utf-8")

    payload = checklib.report(
        SCRIPT, args.pcb, violations, pairs=checked,
        skipped_pairs=skipped_pairs, skipped_rails=skipped_rails,
        stackup_assumed=bg.stackup.assumed,
        model={"kind": "rectangular plane-pair cavity sum",
               "tan_delta": TAN_DELTA, "sigma_cu_s_per_m": SIGMA_CU,
               "esr_default_mohm": ESR_DEFAULT_OHM * 1e3,
               "band_hz": [F_MIN_HZ, F_MAX_HZ], "points": N_FREQ,
               "curve_out": args.curve_out or None})
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
