#!/usr/bin/env python3
"""sim_solve.py - hwde's open solvers: what is there, whether each one is
right, and the board-facing studies built on them.

    hwde sim_solve probe [--json]
        every solver lib/solvers.py drives (ngspice, Xyce, openEMS, Palace,
        gmsh, FastHenry, FasterCap) and its version, from the Hive's
        IIC-OSIC-TOOLS tree.

    hwde sim_solve validate [--only NAME,...] [--json] [--out FILE] [--strict]
        run each solver on a case with a closed-form answer and compare.
        A solver is trusted for board work only while its case passes (a
        result from a solver whose case fails, or never ran, is a guess).
        A missing solver is reported as missing, not failed, unless
        --strict. Exit 1 when a case that ran is outside its tolerance.

    hwde sim_solve line --w W --h H --er ER [--t T] [--f-max HZ]
                        [--z-target OHM] [--json]
        full-wave (openEMS) characteristic impedance and eps_eff of a
        bare microstrip (mm), beside impedance.py's 2D quasi-static field
        solve of the same cross-section. The two answer the same question
        two ways: a disagreement past 3% means one of them is wrong for
        this geometry. --z-target adds the error against the target.

    hwde sim_solve planar --geom GEOM.json [--excite 0,1] [--f-max HZ]
                          [--f-min HZ] [--points N] [--fine MM]
                          [--out RES.json] [--touchstone FILE] [--keep DIR]
        full-wave (openEMS) S-parameters of a planar microstrip circuit:
        top copper polygons over an infinite ground, ports at reference
        planes with straight 50 ohm-terminated feeds, waves split from
        three voltage probes per feed. GEOM.json: {"h", "er", "tand"?,
        "polys": [[[x, y], ...], ...], "ports": [{"x", "y", "dir", "w"}]}
        (mm; dir "+x"|"-x"|"+y"|"-y", pointing out of the circuit). One
        openEMS run per excited port, one at a time. The S it writes is
        checked by lib/sparam (IEEE P370 passivity/reciprocity/causality,
        failing points; `quality` in the result): exit 1 when it fails.

    hwde sim_solve planar-geom --pcb BOARD --region X0,Y0,X1,Y1
                          --port X,Y,DIR,W [--port ...] [--layer F.Cu]
                          [--h MM --er ER --tand TD] [--out GEOM.json]
        cut the copper of one layer inside a region of a board (all nets,
        custom pads as drawn) into a GEOM.json for `planar`; h/er default
        to the board's own stackup (dielectric under the layer).

    hwde sim_solve board --pcb BOARD --region X0,Y0,X1,Y1
                         --port X,Y,DIR[,LAYER[,W]] [--port ...]
                         [--part REF=OHMS ...] [--f-max HZ] [--f-min HZ]
                         [--points N] [--symmetric] [--fine MM]
                         [--touchstone FILE] [--out RES.json] [--keep DIR]
                         [--deck-only DIR] [--json]
        full-wave (openEMS) S-parameters of a region of a board with every
        copper layer of its stackup (lib/emboard.py): CPWG and coplanar
        ground, reference-plane cut-outs, via fences and plated holes as
        drawn, 2-pad parts as lumped resistors (--part). Each port sits on
        the region's edge (the region is cut at its plane) and the line
        leaving there is carried out as a feed with V/I probes, so S is
        referenced to 50 ohm at the plane whatever the line's own Z.
        --symmetric: a mirror-symmetric 2-port is excited once. --deck-only
        writes the deck(s) and the probe map without running. Its S is
        checked by lib/sparam like planar's (`quality`; exit 1 on a fail).

The cases (docs/solvers.md has the measured errors):

    circuit_ngspice     RC low-pass |H(fc)| = 1/sqrt2 and v(tau) = 1 - 1/e
    circuit_xyce        the same deck in Xyce
    circuit_xspice      XSPICE code models: d_and truth table and a 4-bit
                        counter (exact), adc_bridge -> d_dff -> dac_bridge
                        delay, 1st-order sigma-delta density = Vin/Vref
    circuit_osdi        an OpenVAF-compiled Verilog-A resistor vs a plain R
    circuit_xspice_kicad  the XSPICE decks (and the OSDI divider) through
                        KiCad's libngspice, the engine sim_run uses
    openems_microstrip  openEMS Z0 vs impedance.py's field solver (and
                        Hammerstad-Jensen), 1.5 mm on 0.8 mm of er 4
    palace_cavity       Palace eigenmodes of a 40x30x20 mm PEC box vs
                        f = c/2 sqrt((m/a)^2 + (n/b)^2 + (p/d)^2)
    fasthenry_bar       a 10 x 1 x 0.035 mm copper bar: R = l/(sigma w t),
                        L vs Ruehli's partial self-inductance
    fastercap_cube      a 1 mm cube's capacitance vs 0.66067813 * 4 pi eps0 a
    thermal_fin         thermalfv's 1D fin vs 1/cosh(mL)
    thermal_point       thermalfv's point source on a plate vs P/(2 pi kt) K0(mr)
    electrothermal      the coupled strip (copper's R rising with T under
                        held voltage) vs its closed-form quadratic

Solves go through `hive heavy` (bin/hwde lists sim_solve as heavy), so a
full-wave run never lands beside another heavy job on the machine.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from lib import emsolve as em  # noqa: E402
from lib import solvers  # noqa: E402

SCRIPT = "sim_solve"


def _case(name, solver, quantity, measured, expected, tol_pct, reference, **extra):
    err = (measured - expected) / expected * 100 if expected else math.inf
    return {"name": name, "solver": solver, "quantity": quantity,
            "measured": measured, "expected": expected,
            "err_pct": round(err, 4), "tol_pct": tol_pct,
            "status": "pass" if abs(err) <= tol_pct else "fail",
            "reference": reference, **extra}


def _worst(name, solver, quantity, pairs, tol_pct, reference, **extra):
    """One case from several (measured, expected) pairs: the worst error rules."""
    errs = [(m - e) / e * 100 for m, e in pairs]
    k = max(range(len(errs)), key=lambda i: abs(errs[i]))
    return _case(name, solver, quantity, pairs[k][0], pairs[k][1], tol_pct,
                 reference, all=[{"measured": m, "expected": e} for m, e in pairs],
                 **extra)


# ---------------------------------------------------------------- circuit

RC_R, RC_C = 1e3, 159.1549e-9          # fc = 1/(2 pi R C) = 1.0000 kHz
RC_TAU = RC_R * RC_C


def _rc_body() -> str:
    return (f"V1 in 0 DC 0 AC 1 PULSE(0 1 0 1n 1n 1 2)\n"
            f"R1 in out {RC_R}\nC1 out 0 {RC_C}\n")


def _interp(xs, ys, x):
    for i in range(1, len(xs)):
        if xs[i] >= x:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + f * (ys[i] - ys[i - 1])
    raise ValueError(f"{x} past the data")


def _rc_expected():
    return 1 / math.sqrt(2), 1 - math.exp(-1)


def case_circuit_ngspice(work: Path) -> dict:
    deck = ("* hwde rc\n" + _rc_body() +
            ".control\ntran 1u 2m\nwrdata tran.txt v(out)\n"
            "ac lin 3 500 1500\nwrdata ac.txt vm(out)\n.endc\n.end\n")
    (work / "rc.cir").write_text(deck)
    r = solvers.run("ngspice", ["-b", "rc.cir"], cwd=work, timeout=1200)
    if not (work / "ac.txt").exists():
        raise solvers.SolverError(f"ngspice wrote nothing: {r.stderr[-300:]}")

    def cols(p):
        rows = [list(map(float, ln.split())) for ln in p.read_text().splitlines() if ln.strip()]
        return [r_[0] for r_ in rows], [r_[1] for r_ in rows]
    fa, ha = cols(work / "ac.txt")
    tt, vt = cols(work / "tran.txt")
    h_fc = _interp(fa, ha, 1000.0)
    v_tau = _interp(tt, vt, RC_TAU)
    e_h, e_v = _rc_expected()
    return _worst("circuit_ngspice", "ngspice", "|H(fc)|, v(tau)",
                  [(h_fc, e_h), (v_tau, e_v)], 0.5,
                  "RC low-pass: |H(1/(2 pi RC))| = 1/sqrt(2), step v(RC) = 1 - 1/e")


def _xyce_csv(p: Path):
    lines = p.read_text().splitlines()
    rows = [list(map(float, ln.split(","))) for ln in lines[1:] if ln.strip()]
    return [r_[0] for r_ in rows], [r_[1] for r_ in rows]


def case_circuit_xyce(work: Path) -> dict:
    (work / "tran.cir").write_text("* hwde rc tran\n" + _rc_body() +
                                   ".tran 1u 2m\n.print tran format=csv file=tran.csv v(out)\n.end\n")
    (work / "ac.cir").write_text("* hwde rc ac\n" + _rc_body() +
                                 ".ac lin 3 500 1500\n.print ac format=csv file=ac.csv vm(out)\n.end\n")
    for deck in ("tran.cir", "ac.cir"):
        r = solvers.run("xyce", [deck], cwd=work, timeout=1200)
        if not r.ok:
            raise solvers.SolverError(f"Xyce {deck}: {(r.stdout + r.stderr)[-300:]}")
    fa, ha = _xyce_csv(work / "ac.csv")
    tt, vt = _xyce_csv(work / "tran.csv")
    e_h, e_v = _rc_expected()
    return _worst("circuit_xyce", "xyce", "|H(fc)|, v(tau)",
                  [(_interp(fa, ha, 1000.0), e_h), (_interp(tt, vt, RC_TAU), e_v)], 0.5,
                  "RC low-pass: |H(1/(2 pi RC))| = 1/sqrt(2), step v(RC) = 1 - 1/e")


# ---------------------------------------------------------------- mixed-signal (XSPICE, OSDI)

# One clock for the digital decks: rising edges at 0.5 us + k us (1 ns ramps).
XS_CLK = "vc clk 0 pulse(0 5 0.5u 1n 1n 0.5u 1u)\n"
XS_MODELS = (".model adc1 adc_bridge(in_low=1 in_high=2)\n"
             ".model and1 d_and(rise_delay=1n fall_delay=1n)\n"
             ".model ff1 d_dff(clk_delay=10n set_delay=10n reset_delay=10n "
             "rise_delay=1n fall_delay=1n)\n"
             ".model dac1 dac_bridge(out_low=0 out_high=1)\n")
# a = 0101 and b = 0011 over 0-8 us (2 us each): AND sampled mid-slot.
XS_AND = {"and1": (1e-6, 0), "and2": (3e-6, 0), "and3": (5e-6, 0), "and4": (7e-6, 1)}
# the ripple counter's value just after edge n (edges at 0.5 + k us)
XS_CNT = {f"cnt{n}": (n + 0.2, n % 16) for n in (5, 11, 16, 21)}
# d_dff clocked at 1 MHz: q follows a's 2nd rise (6 us) and 2nd fall (8 us)
# at the next clock edge (6.5, 8.5 us) plus the bridge and flop delays:
# clock to adc threshold 0.4 ns + adc 1 ns + clk_delay 10 ns + the flop's
# rise/fall 1 ns + dac to half swing 0.5 ns; a's 2.5 V point is 0.5 ns into
# its ramp. One clock late (or early) would be 1 us off: 200% here.
XS_DELAY = 0.5e-6 + (0.4 + 1 + 10 + 1 + 0.5 - 0.5) * 1e-9
SD_VIN = (0.3137, 0.7071)              # first-order sigma-delta inputs, Vref = 1
SD_CLOCKS = 4096


def xspice_logic_deck() -> str:
    meas = [f".meas tran {k} find v(yand) at={t}" for k, (t, _) in XS_AND.items()]
    meas += [f".meas tran {k} find v(cnt) at={t}u" for k, (t, _) in XS_CNT.items()]
    meas += [".meas tran tdr when v(a)=2.5 rise=2", ".meas tran tqr when v(yq)=0.5 rise=2",
             ".meas tran tdf when v(a)=2.5 fall=2", ".meas tran tqf when v(yq)=0.5 fall=2"]
    return ("* hwde xspice: d_and truth table, d_dff, 4-bit ripple counter\n"
            "va a 0 pulse(0 5 2u 1n 1n 2u 4u)\nvb b 0 pulse(0 5 4u 1n 1n 4u 8u)\n" + XS_CLK +
            "aadc [a b clk] [da db dclk] adc1\n"
            "aand [da db] dand and1\n"
            "aff da dclk null null dq dqb ff1\n"
            "* each stage toggles (d = qbar) on the falling q of the one before\n"
            "a0 q0b dclk null null q0 q0b ff1\na1 q1b q0b null null q1 q1b ff1\n"
            "a2 q2b q1b null null q2 q2b ff1\na3 q3b q2b null null q3 q3b ff1\n"
            "adac [dand dq q0 q1 q2 q3] [yand yq c0 c1 c2 c3] dac1\n"
            "bcnt cnt 0 v = v(c0) + 2*v(c1) + 4*v(c2) + 8*v(c3)\n" + XS_MODELS +
            ".tran 1n 22u 0 1n\n" + "\n".join(meas) + "\n.end\n")


def sigma_delta_deck(vin: float) -> str:
    """First-order sigma-delta: an ideal integrator of (vin - fb), a
    comparator, a d_dff sampling it at 1 MHz and a 1-bit DAC (0/1 V) as fb.
    The integrator stays bounded, so over N clocks the ones density is
    vin/Vref within ~(swing/step)/N; the mean of fb is that density."""
    t_end = 10 + SD_CLOCKS
    return ("* hwde xspice: first-order sigma-delta on DC\n"
            f"vin in 0 dc {vin}\n" + XS_CLK.replace("pulse(0 5", "pulse(0 1") +
            "bint 0 int i = 1e-6*(v(in) - v(fb))\ncint int 0 1n\n"
            "bcmp cmp 0 v = v(int) > 0 ? 1 : 0\n"
            "aadc [cmp clk] [dcmp dclk] adcsd\n"
            ".model adcsd adc_bridge(in_low=0.4 in_high=0.6)\n"
            "aff dcmp dclk null null dq dqb ff1\nadac [dq] [fb] dac1\n" +
            XS_MODELS + ".ic v(int)=0\n"
            f".tran 0.1u {t_end}u 0 0.1u uic\n"
            f".meas tran dens avg v(fb) from=10u to={t_end}u\n.end\n")


OSDI_VA = """`include "disciplines.vams"
module hwde_res(p, n);
  inout p, n; electrical p, n;
  parameter real r = 1k from (0:inf);
  analog I(p, n) <+ V(p, n) / r;
endmodule
"""


def osdi_deck(osdi: Path | str) -> str:
    """A 2k Verilog-A resistor over a plain 1k: v(b) = 1/3 of v(a)."""
    return ("* hwde osdi: Verilog-A resistor divider\n"
            f".control\npre_osdi {osdi}\n.endc\n"
            "v1 a 0 dc 1\nn1 a b rva\n.model rva hwde_res r=2k\nr2 b 0 1k\n"
            ".dc v1 0 1 0.5\n.meas dc vb find v(b) at=1\n.end\n")


def _xspice_pairs(meas: dict, tag: str) -> list[tuple[str, float, float]]:
    """(what, measured, expected) from the logic deck's measures."""
    def get(k):
        if k not in meas:
            raise solvers.SolverError(f"{tag}: measure {k} missing")
        return meas[k]
    bits = [(k, e) for k, (_, e) in XS_AND.items()] + [(k, e) for k, (_, e) in XS_CNT.items()]
    right = sum(1 for k, e in bits if abs(get(k) - e) < 0.05)
    return [("logic samples right", right / len(bits), 1.0),
            ("d_dff rise delay", get("tqr") - get("tdr"), XS_DELAY),
            ("d_dff fall delay", get("tqf") - get("tdf"), XS_DELAY)]


def _ngspice_measures(work: Path, name: str, deck: str) -> dict:
    import simlib
    (work / f"{name}.cir").write_text(deck)
    r = solvers.run("ngspice", ["-b", f"{name}.cir"], cwd=work, timeout=1200)
    meas = simlib.parse_measures(r.stdout)
    if not meas:
        raise solvers.SolverError(f"ngspice {name}: no measures: {(r.stdout + r.stderr)[-400:]}")
    return meas


def _xspice_case(name, solver, measure) -> dict:
    """The XSPICE checks through one engine: measure(tag, deck) -> {name: value}."""
    pairs = _xspice_pairs(measure("logic", xspice_logic_deck()), name)
    for k, vin in enumerate(SD_VIN):
        d = measure(f"sd{k}", sigma_delta_deck(vin))["dens"]
        pairs.append((f"sigma-delta density, vin {vin}", d, vin))
    c = _worst(name, solver, "logic, d_dff delay, sigma-delta density",
               [(m, e) for _, m, e in pairs], 0.5,
               "d_and truth table + 4-bit counter (exact); adc_bridge -> d_dff -> dac_bridge "
               "delays a square wave to the next clock edge + bridge/flop delays; "
               f"1st-order sigma-delta ones density = Vin/Vref over {SD_CLOCKS} clocks")
    c["checks"] = [{"what": w, "measured": m, "expected": e} for w, m, e in pairs]
    return c


def case_circuit_xspice(work: Path) -> dict:
    return _xspice_case("circuit_xspice", "ngspice",
                        lambda tag, deck: _ngspice_measures(work, tag, deck))


def case_circuit_osdi(work: Path) -> dict:
    (work / "hwde_res.va").write_text(OSDI_VA)
    osdi = solvers.compile_va(work / "hwde_res.va", work / "hwde_res.osdi")
    vb = _ngspice_measures(work, "osdi", osdi_deck(osdi))["vb"]
    return _case("circuit_osdi", "ngspice", "v(b) of a Verilog-A 2k over 1k", vb, 1 / 3,
                 0.01, "OpenVAF-compiled resistor matches a plain R: 1k/(2k + 1k)")


def _kicad_measures(work: Path, dll: Path, tag: str, deck: str) -> dict:
    """One deck through sim_run's worker on KiCad's libngspice."""
    import subprocess
    cir = work / f"kicad_{tag}.cir"
    cir.write_text(deck)
    p = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "sim_run.py"),
                        "--exec-one", str(cir), "--dll", str(dll)],
                       capture_output=True, text=True, timeout=1200)
    try:
        res = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise solvers.SolverError(f"KiCad ngspice {tag}: {(p.stderr or p.stdout)[-400:]}")
    if res.get("status") != "ok" or not res.get("measures"):
        raise solvers.SolverError(f"KiCad ngspice {tag}: {res.get('error') or res.get('log_tail', '')[-300:]}")
    return res["measures"]


def case_circuit_xspice_kicad(work: Path) -> dict:
    """The same decks through KiCad's bundled libngspice (sim_run's engine),
    which gets its code models from simlib.codemodel_files; plus the OSDI
    divider when the tree's OpenVAF is there to compile it."""
    from lib import env as hwenv
    dll = hwenv.find_ngspice_dll()
    if dll is None:
        return {"name": "circuit_xspice_kicad", "solver": "kicad-ngspice", "status": "missing",
                "detail": "no KiCad libngspice (install KiCad 10 or set HWDE_NGSPICE_DLL)"}
    c = _xspice_case("circuit_xspice_kicad", "kicad-ngspice",
                     lambda tag, deck: _kicad_measures(work, dll, tag, deck))
    c["dll"] = str(dll)
    if solvers.tool_path("openvaf") is not None:
        (work / "hwde_res.va").write_text(OSDI_VA)
        vb = _kicad_measures(work, dll, "osdi", osdi_deck("hwde_res.va"))["vb"]
        c["checks"].append({"what": "OSDI divider (pre_osdi .va)", "measured": vb, "expected": 1 / 3})
        err = (vb - 1 / 3) * 3 * 100
        if abs(err) > abs(c["err_pct"]):
            c.update(measured=vb, expected=1 / 3, err_pct=round(err, 4),
                     status="pass" if abs(err) <= c["tol_pct"] else "fail")
    return c

# ---------------------------------------------------------------- full-wave

def openems_line(work: Path, w, h, er, t=0.0, f_max=4e9, freqs=None) -> dict:
    csx, where = em.microstrip_line(w, h, er, t=t, f_max=f_max)
    (work / "line.xml").write_text(csx.xml())
    t0 = time.time()
    r = solvers.run("openems", ["line.xml"], cwd=work, timeout=3600)
    if not (work / "u2").exists():
        raise solvers.SolverError(f"openEMS wrote no probes: {(r.stdout + r.stderr)[-400:]}")
    # the band's upper half carries the pulse's energy; below f_max/4 the
    # probes' DFT is noise-limited (eps_eff wanders)
    freqs = freqs or [f_max * k / 8 for k in (2, 3, 4)]
    rows = em.microstrip_z0_from_probes(work, where, freqs)
    return {"rows": rows, "cells": where["cells"], "seconds": round(time.time() - t0, 1),
            "z0": sum(r_["z0"] for r_ in rows) / len(rows),
            "eps_eff": sum(r_["eps_eff"] for r_ in rows) / len(rows)}


def _port_arg(s: str) -> dict:
    x, y, d, w = s.split(",")
    if d not in ("+x", "-x", "+y", "-y"):
        raise SystemExit(f"--port {s}: dir must be +x, -x, +y or -y")
    return {"x": float(x), "y": float(y), "dir": d, "w": float(w)}


def _no_holes(poly) -> list:
    """A shapely polygon as hole-free pieces (CSXCAD's Polygon has no holes):
    cut along x at each hole's centre, recursively."""
    from shapely.geometry import box as sbox
    from shapely.ops import split
    from shapely.geometry import LineString
    if not poly.interiors:
        return [poly]
    cx = poly.interiors[0].centroid.x
    x0, y0, x1, y1 = poly.bounds
    out = []
    for piece in split(poly, LineString([(cx, y0 - 1), (cx, y1 + 1)])).geoms:
        out += _no_holes(piece)
    return out


def planar_geom(pcb, region, ports, layer="F.Cu", h=None, er=None, tand=None) -> dict:
    """GEOM.json for `planar` from a board: every copper item on `layer`
    (tracks, pads incl. custom ones, vias, zone fill; any net) clipped to
    the region. h/er/tand: the dielectric below the layer in the board's
    stackup unless given."""
    from lib import geom as G
    from shapely.geometry import box as sbox
    from shapely.ops import unary_union
    bg = G.load_board(pcb)
    items = [t.poly for t in bg.tracks_of(layer=layer)]
    items += [p.poly for p in bg.pads_of(layer=layer) if p.kind != "np_thru_hole"]
    items += [v.poly for v in bg.vias_of(layer=layer)]
    items += [z.fill_on(layer) for z in bg.zones_of(layer=layer)]
    cu = unary_union([g for g in items if g is not None and not g.is_empty])
    cut = cu.intersection(sbox(*region))
    polys = []
    for g in (cut.geoms if hasattr(cut, "geoms") else [cut]):
        if g.geom_type != "Polygon" or g.area < 1e-4:
            continue
        for piece in _no_holes(g.simplify(0.005)):
            polys.append([[round(x, 4), round(y, 4)] for x, y in list(piece.exterior.coords)[:-1]])
    if h is None or er is None:
        st = bg.stackup
        above, below = st.adjacent(layer)
        other = below or above
        if other:
            h = h if h is not None else st.height_between(layer, other)
            er = er if er is not None else st.epsilon_between(layer, other)
            if tand is None:
                i, j = sorted((st.index(layer), st.index(other)))
                ts = [d.get("loss_tangent") for d in st.dielectrics[i:j] if d.get("loss_tangent")]
                tand = sum(ts) / len(ts) if ts else None
    if h is None or er is None:
        raise SystemExit("planar-geom: no dielectric found under the layer; pass --h and --er")
    return {"source": str(pcb), "layer": layer, "region": region, "h": h, "er": er,
            "tand": tand or 0.0, "polys": polys, "ports": ports}


def _features(freqs, s11, s21) -> dict:
    """Where the response's features sit: |S21| peak (band-pass centre),
    its -3 dB points from the peak, the deepest |S21| (a notch) and the
    deepest |S11| (best match)."""
    import math as m
    db = lambda v: 20 * m.log10(max(abs(v), 1e-12))
    d21 = [db(v) for v in s21] if s21 else []
    d11 = [db(v) for v in s11]
    out = {"s11_min": {"f": freqs[d11.index(min(d11))], "db": round(min(d11), 2)}}
    if d21:
        k = d21.index(max(d21))
        out["s21_max"] = {"f": freqs[k], "db": round(d21[k], 2)}
        lo = next((freqs[i] for i in range(k, -1, -1) if d21[i] < d21[k] - 3), None)
        hi = next((freqs[i] for i in range(k, len(d21)) if d21[i] < d21[k] - 3), None)
        out["s21_3db"] = {"lo": lo, "hi": hi}
        j = d21.index(min(d21))
        out["s21_min"] = {"f": freqs[j], "db": round(d21[j], 2)}
    return out


def planar(geo: dict, excites, f_max, f_min=None, points=201, fine=None, keep=None) -> dict:
    n = len(geo["ports"])
    f_min = f_min or f_max / 40
    freqs = [f_min + (f_max - f_min) * i / (points - 1) for i in range(points)]
    cols, runs = {}, []
    for e in excites:
        csx, where = em.planar_deck(geo["polys"], geo["ports"], geo["h"], geo["er"], f_max,
                                    excite=e, tand=geo.get("tand", 0.0), fine=fine)
        ctx = tempfile.TemporaryDirectory(prefix="hwde-planar-") if not keep else None
        work = Path(keep) / f"port{e}" if keep else Path(ctx.name)
        work.mkdir(parents=True, exist_ok=True)
        (work / "planar.xml").write_text(csx.xml())
        t0 = time.time()
        r = solvers.run("openems", ["planar.xml"], cwd=work, timeout=4 * 3600)
        if r.returncode:
            raise solvers.SolverError(f"openEMS exited {r.returncode} in {work}: "
                                      f"{(r.stdout + r.stderr)[-400:]}")
        if not (work / f"p{e}u0").exists():
            raise solvers.SolverError(f"openEMS wrote no probes: {(r.stdout + r.stderr)[-400:]}")
        cols[e] = em.planar_sparams(work, where, e, freqs)
        runs.append({"excite": e, "cells": where["cells"], "seconds": round(time.time() - t0, 1)})
        if ctx:
            ctx.cleanup()
    e0 = excites[0]
    s11 = [c[e0] for c in cols[e0]]
    s21 = [c[1 - e0] for c in cols[e0]] if n == 2 else None
    res = {"script": SCRIPT, "cmd": "planar", "ports": n, "freqs": freqs, "runs": runs,
           "s": {f"S{i + 1}{j + 1}": [[round(cols[j][m][i].real, 6), round(cols[j][m][i].imag, 6)]
                                       for m in range(len(freqs))]
                 for j in cols for i in range(n)},
           "features": _features(freqs, s11, s21),
           "quality": _quality(freqs, cols, n),
           "_touchstone": em.touchstone(freqs, cols, n)}
    return res


def board_study(pcb, region, ports, parts=None, f_max=6e9, f_min=None, points=201,
                symmetric=False, fine=None, keep=None, deck_only=None) -> dict:
    """`hwde sim_solve board`: extract -> deck -> openEMS -> S (lib/emboard)."""
    from lib import emboard as eb
    geo = eb.extract(pcb, region, parts)
    if deck_only:
        out = Path(deck_only)
        out.mkdir(parents=True, exist_ok=True)
        n = len(ports)
        decks = []
        for e in ([0] if symmetric and n == 2 else range(n)):
            csx, where = eb.deck(geo, ports, f_max, e, fine=fine)
            d = out / f"port{e}"
            d.mkdir(exist_ok=True)
            (d / "deck.xml").write_text(csx.xml())
            (d / "where.json").write_text(json.dumps(where, indent=1) + "\n")
            decks.append({"excite": e, "dir": str(d), "cells": where["cells"], "mesh": where["mesh"]})
        return {"script": SCRIPT, "cmd": "board", "decks": decks, "ports": where["ports"]}
    res = eb.study(geo, ports, f_max, f_min, points, symmetric=symmetric, keep=keep, fine=fine)
    S, freqs = res.pop("S"), res["freqs"]
    n = len(res["ports"])
    res["s"] = {f"S{i + 1}{j + 1}": [[round(s[i][j].real, 6), round(s[i][j].imag, 6)] for s in S]
                for i in range(n) for j in range(n)}
    res["features"] = _features(freqs, [s[0][0] for s in S], [s[1][0] for s in S] if n > 1 else None)
    res["_touchstone"] = eb.touchstone(freqs, S, [f"hwde sim_solve board, openEMS: {pcb} region {region}",
                                                  "ports: " + "; ".join(
                                                      f"{p['x']},{p['y']},{p['dir']} {p['layer']} ref {p['ref']} "
                                                      f"net {p['net']} w {p['w']}" for p in res["ports"])])
    res["quality"] = _quality(freqs, {j: [[s[i][j] for i in range(n)] for s in S] for j in range(n)}, n)
    res.update(script=SCRIPT, cmd="board", source=str(pcb))
    return res


def _quality(freqs, cols: dict, n: int) -> dict:
    """lib/sparam.check on the solve's own S (#483): only the excited
    columns hold data, so passivity is judged per column and reciprocity
    only once both S_ij and S_ji were run."""
    import numpy as np
    from lib import sparam
    s = np.zeros((len(freqs), n, n), dtype=complex)
    for j, col in cols.items():
        for m, row in enumerate(col):
            s[m, :, j] = row
    q = sparam.check(freqs, s, terms=[(i, j) for j in cols for i in range(n)])
    return json.loads(json.dumps(q, default=float))


def _quasi_static(w, h, er, t):
    from lib import impedance as imp
    return imp.field_microstrip(w, h, max(t, 1e-4), er, mask=None)


def _hammerstad(w, h, er):
    """Hammerstad-Jensen zero-thickness microstrip (accurate to ~0.2%)."""
    u = w / h
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 \
        + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    ee = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    z_air = 60 * math.log(f / u + math.sqrt(1 + (2 / u) ** 2))
    return z_air / math.sqrt(ee), ee


MS_W, MS_H, MS_ER = 1.5, 0.8, 4.0


def case_openems_microstrip(work: Path) -> dict:
    got = openems_line(work, MS_W, MS_H, MS_ER)
    qs = _quasi_static(MS_W, MS_H, MS_ER, 0.0)
    hj_z, hj_e = _hammerstad(MS_W, MS_H, MS_ER)
    c = _worst("openems_microstrip", "openems", "Z0 (ohm), eps_eff",
               [(got["z0"], hj_z), (got["eps_eff"], hj_e)], 2.0,
               f"Hammerstad-Jensen, {MS_W} mm on {MS_H} mm of er {MS_ER}, 1-2 GHz",
               quasi_static={"z0": qs["z0"], "eps_eff": qs["eps_eff"]},
               rows=got["rows"], cells=got["cells"], seconds=got["seconds"])
    return c


CAV = (40.0, 30.0, 20.0)


def case_palace_cavity(work: Path) -> dict:
    a, b, d = CAV
    ref = em.cavity_modes(a, b, d, 4)
    (work / "box.geo").write_text(em.box_mesh_geo(a, b, d, size=6.0))
    r = solvers.run("gmsh", ["box.geo", "-3", "-o", "box.msh"], cwd=work, timeout=1200)
    if not (work / "box.msh").exists():
        raise solvers.SolverError(f"gmsh: {(r.stdout + r.stderr)[-300:]}")
    (work / "cavity.json").write_text(json.dumps(
        em.palace_eigen_config("box.msh", len(ref), ref[0] * 0.9), indent=1))
    t0 = time.time()
    r = solvers.run("palace", ["cavity.json"], cwd=work, timeout=1800)
    if not (work / "postpro" / "eig.csv").exists():
        raise solvers.SolverError(f"Palace: {(r.stdout + r.stderr)[-400:]}")
    got = em.palace_eigs(work / "postpro")[:len(ref)]
    return _worst("palace_cavity", "palace", "eigenfrequency (GHz)",
                  list(zip(got, ref)), 0.5,
                  f"closed PEC box {a}x{b}x{d} mm: TM110, TE101, TE/TM111 ...",
                  seconds=round(time.time() - t0, 1))


# ---------------------------------------------------------------- extraction

BAR = (10.0, 1.0, 0.035)


def case_fasthenry_bar(work: Path) -> dict:
    l, w, t = BAR
    (work / "bar.inp").write_text(em.fasthenry_bar(l, w, t, f=1e3, nw=3))
    r = solvers.run("fasthenry", ["bar.inp"], cwd=work, timeout=1200)
    if not (work / "Zc.mat").exists():
        raise solvers.SolverError(f"FastHenry: {(r.stdout + r.stderr)[-300:]}")
    f, z = em.read_zc(work / "Zc.mat")
    L = z.imag / (2 * math.pi * f)
    R_dc = l * 1e-3 / (5.8e7 * w * 1e-3 * t * 1e-3)
    return _worst("fasthenry_bar", "fasthenry", "R (ohm), L (H)",
                  [(z.real, R_dc), (L, em.bar_partial_inductance(l, w, t))], 2.0,
                  f"{l}x{w}x{t} mm Cu bar at 1 kHz: R = l/(sigma w t); "
                  "L = mu0 l/2pi (ln(2l/(w+t)) + 1/2 + 0.2235 (w+t)/l)")


def case_fastercap_cube(work: Path) -> dict:
    a = 1.0
    (work / "cube.txt").write_text(em.fastercap_cube(a, 4))
    r = solvers.run("fastercap", ["-b", "cube.txt", "-a0.001"], cwd=work, timeout=1200)
    got = em.read_fastercap(r.stdout)
    ref = em.CUBE_C * 4 * math.pi * em.EPS0 * a * 1e-3
    return _case("fastercap_cube", "fastercap", "C (F)", got, ref, 1.5,
                 "isolated cube: C = 0.66067813 * 4 pi eps0 a (Hwang & Mascagni 2004)")


# ---------------------------------------------------------------- thermal

def case_thermal_fin(work: Path) -> dict:
    import numpy as np
    from lib import thermalfv as th
    dx, L, h = 0.25e-3, 50e-3, 20.0
    nx = int(round(L / dx)) + 1
    ks = th.K_CU * 35e-6 + th.K_FR4 * 1.6e-3
    fixed = np.full((1, nx), np.nan)
    fixed[0, 0] = 10.0
    T = th.solve_plane(np.full((1, nx), ks), h, np.zeros((1, nx)), dx, fixed=fixed)
    # the base cell's centre is x=0 and the last cell ends half a cell past
    # its centre, so the fin is (nx - 1/2) dx long; the tip cell sits dx/2 in
    exact = math.cosh(math.sqrt(h / ks) * dx / 2) / math.cosh(math.sqrt(h / ks) * (nx - 0.5) * dx)
    return _case("thermal_fin", "thermalfv", "theta(tip)/theta(base)",
                 float(T[0, -1] / 10.0), exact, 0.5,
                 "1D fin, adiabatic tip: theta(x)/theta0 = cosh(m(L-x))/cosh(mL)")


def case_thermal_point(work: Path) -> dict:
    import numpy as np
    from lib import thermalfv as th
    dx, n, P, h = 0.5e-3, 401, 1.0, 20.0
    ks = th.K_CU * 35e-6 + th.K_FR4 * 1.6e-3
    q = np.zeros((n, n))
    c = n // 2
    q[c, c] = P
    T = th.solve_plane(np.full((n, n), ks), h, q, dx)
    pairs = [(float(T[c, c + k]), th.point_source_theta(P, k * dx, ks, h)) for k in (6, 10, 20)]
    return _worst("thermal_point", "thermalfv", "theta at r = 3, 5, 10 mm (K)",
                  pairs, 1.0, "1 W on a 1 oz + 1.6 mm FR4 plate, h 20: P/(2 pi kt) K0(mr)")


def case_electrothermal(work: Path) -> dict:
    import numpy as np
    from lib import thermalfv as th
    dx, W, Ls, h = 0.2e-3, 2e-3, 40e-3, 15.0
    ny, nx = int(round(W / dx)), int(round(Ls / dx))
    cu = np.ones((ny, nx), bool)
    R0 = th.RHO_CU * (nx - 1) * dx / (35e-6 * W)
    V = 5.0 * R0                     # 5 A cold
    v = np.full((ny, nx), np.nan)
    v[:, 0], v[:, -1] = V, 0.0
    ks = th.K_CU * 35e-6 + th.K_FR4 * 1.6e-3
    r = th.joule(cu, 35e-6, v, np.full((ny, nx), ks), h, dx, t_amb=20.0, t0=20.0)
    rise = float(r["T"].mean() - 20.0)
    exact = th.strip_selfheat_v(V, R0, h * W * nx * dx)
    return _case("electrothermal", "thermalfv", "mean rise (K)", rise, exact, 1.0,
                 "2x40 mm 1 oz strip held at 5 A-cold volts, h 15: "
                 "hA theta = V^2/(R0(1 + alpha theta))",
                 iterations=r["iterations"], current_a=r["current"])


CASES = {
    "circuit_ngspice": ("ngspice", case_circuit_ngspice),
    "circuit_xyce": ("xyce", case_circuit_xyce),
    "circuit_xspice": ("ngspice", case_circuit_xspice),
    "circuit_osdi": ("openvaf", case_circuit_osdi),
    "circuit_xspice_kicad": (None, case_circuit_xspice_kicad),
    "openems_microstrip": ("openems", case_openems_microstrip),
    "palace_cavity": ("palace", case_palace_cavity),
    "fasthenry_bar": ("fasthenry", case_fasthenry_bar),
    "fastercap_cube": ("fastercap", case_fastercap_cube),
    "thermal_fin": (None, case_thermal_fin),
    "thermal_point": (None, case_thermal_point),
    "electrothermal": (None, case_electrothermal),
}
NEEDS = {"palace_cavity": ("gmsh",), "circuit_osdi": ("ngspice",)}


def validate(only=None, strict=False) -> dict:
    root = solvers.toolchain_root()
    out = []
    for name, (tool, fn) in CASES.items():
        if only and name not in only:
            continue
        missing = [t for t in (tool, *NEEDS.get(name, ())) if t and solvers.tool_path(t, root) is None]
        if missing:
            out.append({"name": name, "solver": tool, "status": "missing",
                        "detail": f"{', '.join(missing)} not found (no IIC-OSIC-TOOLS tree, "
                                  "or set HWDE_EDA_TOOLCHAIN)"})
            continue
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix=f"hwde-{name}-") as d:
            try:
                c = fn(Path(d))
            except (solvers.SolverError, ValueError, OSError) as e:
                c = {"name": name, "solver": tool, "status": "error", "detail": str(e)}
        c.setdefault("seconds", round(time.time() - t0, 1))
        out.append(c)
    bad = [c for c in out if c["status"] in ("fail", "error") or (strict and c["status"] == "missing")]
    return {"script": SCRIPT, "toolchain": str(root) if root else None,
            "cases": out, "ok": not bad}


def _print_validate(res: dict) -> None:
    print(f"toolchain: {res['toolchain'] or '(none)'}")
    for c in res["cases"]:
        if c["status"] in ("pass", "fail"):
            print(f"  {c['status']:5} {c['name']:20} {c['quantity']}: {c['measured']:.6g} vs "
                  f"{c['expected']:.6g} ({c['err_pct']:+.3f}%, tol {c['tol_pct']}%) "
                  f"[{c['seconds']} s]")
        else:
            print(f"  {c['status']:5} {c['name']:20} {c.get('detail', '')}")
    print("ok" if res["ok"] else "FAILED")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hwde sim_solve", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--json", action="store_true")
    v = sub.add_parser("validate")
    v.add_argument("--only", default="")
    v.add_argument("--json", action="store_true")
    v.add_argument("--out")
    v.add_argument("--strict", action="store_true")
    ln = sub.add_parser("line")
    ln.add_argument("--w", type=float, required=True)
    ln.add_argument("--h", type=float, required=True)
    ln.add_argument("--er", type=float, required=True)
    ln.add_argument("--t", type=float, default=0.0)
    ln.add_argument("--f-max", type=float, default=4e9)
    ln.add_argument("--z-target", type=float)
    ln.add_argument("--json", action="store_true")
    pl = sub.add_parser("planar")
    pl.add_argument("--geom", required=True)
    pl.add_argument("--excite", default="0")
    pl.add_argument("--f-max", type=float, default=6e9)
    pl.add_argument("--f-min", type=float)
    pl.add_argument("--points", type=int, default=201)
    pl.add_argument("--fine", type=float)
    pl.add_argument("--out")
    pl.add_argument("--touchstone")
    pl.add_argument("--keep")
    pl.add_argument("--json", action="store_true")
    pg = sub.add_parser("planar-geom")
    pg.add_argument("--pcb", required=True)
    pg.add_argument("--region", required=True)
    pg.add_argument("--port", action="append", required=True)
    pg.add_argument("--layer", default="F.Cu")
    pg.add_argument("--h", type=float)
    pg.add_argument("--er", type=float)
    pg.add_argument("--tand", type=float)
    pg.add_argument("--out")
    bd = sub.add_parser("board")
    bd.add_argument("--pcb", required=True)
    bd.add_argument("--region", required=True)
    bd.add_argument("--port", action="append", required=True)
    bd.add_argument("--part", action="append", default=[])
    bd.add_argument("--f-max", type=float, default=6e9)
    bd.add_argument("--f-min", type=float)
    bd.add_argument("--points", type=int, default=201)
    bd.add_argument("--symmetric", action="store_true")
    bd.add_argument("--fine", type=float)
    bd.add_argument("--touchstone")
    bd.add_argument("--out")
    bd.add_argument("--keep")
    bd.add_argument("--deck-only")
    bd.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "board":
        parts = {}
        for s_ in args.part:
            ref, _, ohm = s_.partition("=")
            parts[ref] = float(ohm)
        res = board_study(args.pcb, [float(v) for v in args.region.split(",")], args.port, parts,
                          args.f_max, args.f_min, args.points, args.symmetric, args.fine,
                          args.keep, args.deck_only)
        ts = res.pop("_touchstone", None)
        if args.touchstone and ts:
            Path(args.touchstone).write_text(ts)
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1) + "\n")
        if args.json:
            print(json.dumps(res, indent=1))
        else:
            for p in res["ports"]:
                print(f"port {p['x']},{p['y']},{p['dir']}: {p['layer']} over {p['ref']} "
                      f"(h {p['h']}, er {p['er']}), net {p['net']}, w {p['w']}")
            for d in res.get("decks", []):
                print(f"deck port{d['excite']}: {d['cells']} cells {d['mesh']} -> {d['dir']}")
            for r_ in res.get("runs", []):
                print(f"run port{r_['excite']}: {r_['cells']} cells, {r_['seconds']} s, "
                      f"feed Z {r_['z_feed']} ohm")
            for name, ft in res.get("features", {}).items():
                print(f"  {name}: {ft}")
        q = res.get("quality")
        if q is None:   # --deck-only
            return 0
        from lib import sparam
        if not args.json or not q["ok"]:
            print(sparam.summary({**q, "file": args.touchstone or "board"}),
                  file=sys.stdout if q["ok"] else sys.stderr)
        return 0 if q["ok"] else 1

    if args.cmd == "planar":
        geo = json.loads(Path(args.geom).read_text())
        res = planar(geo, [int(k) for k in args.excite.split(",")], args.f_max,
                     args.f_min, args.points, args.fine, args.keep)
        if args.touchstone:
            Path(args.touchstone).write_text(res.pop("_touchstone"))
        else:
            res.pop("_touchstone")
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1) + "\n")
        if args.json:
            print(json.dumps(res, indent=1))
        else:
            for run in res["runs"]:
                print(f"port {run['excite']}: {run['cells']} cells, {run['seconds']} s")
            for name, ft in res["features"].items():
                print(f"  {name}: {ft}")
        from lib import sparam
        q = res["quality"]
        if not args.json or not q["ok"]:
            print(sparam.summary({**q, "file": args.touchstone or "planar"}),
                  file=sys.stdout if q["ok"] else sys.stderr)
        return 0 if q["ok"] else 1

    if args.cmd == "planar-geom":
        geo = planar_geom(args.pcb, [float(v) for v in args.region.split(",")],
                          [_port_arg(p) for p in args.port], args.layer,
                          args.h, args.er, args.tand)
        txt = json.dumps(geo, indent=1) + "\n"
        if args.out:
            Path(args.out).write_text(txt)
        else:
            print(txt)
        return 0

    if args.cmd == "probe":
        res = solvers.probe()
        if args.json:
            print(json.dumps(res, indent=1))
        else:
            print(f"toolchain: {res['toolchain'] or '(none: set HWDE_EDA_TOOLCHAIN)'}")
            for k, t in res["tools"].items():
                print(f"  {k:10} {t['version'] or 'missing':28} {t['kind']}")
        return 0 if res["toolchain"] else 1

    if args.cmd == "validate":
        only = {s.strip() for s in args.only.split(",") if s.strip()} or None
        unknown = (only or set()) - set(CASES)
        if unknown:
            ap.error(f"unknown case(s): {', '.join(sorted(unknown))}; have {', '.join(CASES)}")
        res = validate(only, args.strict)
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1) + "\n")
        if args.json:
            print(json.dumps(res, indent=1))
        else:
            _print_validate(res)
        return 0 if res["ok"] else 1

    # line
    with tempfile.TemporaryDirectory(prefix="hwde-line-") as d:
        got = openems_line(Path(d), args.w, args.h, args.er, args.t, args.f_max)
    qs = _quasi_static(args.w, args.h, args.er, args.t)
    diff = (got["z0"] - qs["z0"]) / qs["z0"] * 100
    res = {"script": SCRIPT, "geometry": {"w": args.w, "h": args.h, "er": args.er, "t": args.t},
           "full_wave": got, "quasi_static": qs, "z0_diff_pct": round(diff, 3),
           "agree": abs(diff) <= 3.0}
    if args.z_target:
        res["z0_target"] = args.z_target
        res["z0_err_pct"] = round((got["z0"] - args.z_target) / args.z_target * 100, 3)
    if args.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"openEMS: Z0 {got['z0']:.2f} ohm, eps_eff {got['eps_eff']:.3f} "
              f"({got['cells']} cells, {got['seconds']} s)")
        for r_ in got["rows"]:
            print(f"   {r_['f'] / 1e9:5.2f} GHz  Z0 {r_['z0']:.2f}  eps_eff {r_['eps_eff']:.3f}")
        print(f"quasi-static: Z0 {qs['z0']:.2f} ohm, eps_eff {qs['eps_eff']:.3f}; "
              f"full-wave is {diff:+.2f}% ({'agree' if res['agree'] else 'DISAGREE'})")
        if args.z_target:
            print(f"target {args.z_target} ohm: {res['z0_err_pct']:+.2f}%")
    return 0 if res["agree"] else 1


if __name__ == "__main__":
    sys.exit(main())
