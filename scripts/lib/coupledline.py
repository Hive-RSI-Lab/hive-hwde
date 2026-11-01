"""coupledline.py - two coupled lossless-ish lines in a transient deck.

A Deck is a flat list of elements: resistors, capacitors, one ramp source,
single (T) lines and coupled sections, each coupled section a 2x2 L, C (and
optionally R) per metre over a length. It writes itself for an engine:

    cpl    ngspice's CPL device, the coupled multiconductor line.
    modal  the same section as its two modes: a T line per mode between ideal
           V = T Vm, I = inv(T)' Im transformers (E and F sources), with
           LC T = T diag(lambda), Z_m = sqrt(L_m / C_m), delay = sqrt(lambda) len.
           Lossless. Xyce 7.10 has no multiconductor line (only T, O and
           YTRANSLINE), so this is how Xyce runs a coupled section; ngspice
           runs it too, which checks CPL against an independent network.
    ladder LC sections with K and mutual C. Slow; a check on the other two.

kb, kf closed forms (Bogatin, Signal and Power Integrity - Simplified, ch. 10):
NEXT = kb V, FEXT = kf len / (v t_r) V, with V the step launched on the line,
not the source's EMF. Checked here against the solvers (tests/test_sim_si.py).
"""
from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import solvers

C0 = 299792458.0
RHO_CU = 1.72e-8   # ohm m
# ngspice 47's CPL goes silently wrong as R -> 0: at 1e-6 ohm/m a 100 mm pair's
# NEXT comes out with the wrong sign and 20 mm's 4x too big (no error, step
# size doesn't matter); 1e-3 and up agree with the modal network. Real copper
# is 2-5 ohm/m and 1 ohm/m costs a lossless deck 0.03 dB over 300 mm. sim_si
# therefore checks every CPL run against the modal network.
CPL_MIN_R = 1.0    # ohm/m
# ngspice's T line sets a breakpoint for every slope change it passes on, so a
# few lines with reflections between them multiply them until the deck never
# ends (hifi core's I2S pair: 3 of 4 orientations past 30 s, 283 rows without).
# These raise the threshold so the tran's tmax alone sets the step.
T_OPTS_NGSPICE = " REL=100 ABS=100"


def closed_form(lc: dict, length_m: float, t_r_s: float) -> dict:
    """Bogatin's coefficients of an L, C matrix pair, with the saturation
    length, and NEXT / FEXT as fractions of the launched step. Line 1's
    self terms; the two lines are taken as alike."""
    L, C = lc["L"], lc["C"]
    kc, kl = -C[0][1] / C[0][0], L[0][1] / L[0][0]
    kb, kf = (kc + kl) / 4.0, (kc - kl) / 2.0
    v = 1.0 / math.sqrt(L[0][0] * C[0][0])
    l_sat = v * t_r_s / 2.0
    return {"kC": kc, "kL": kl, "kb": kb, "kf": kf, "v_m_s": v, "l_sat_m": l_sat,
            "next": kb * min(1.0, length_m / l_sat), "fext": kf * length_m / (v * t_r_s),
            "z0": math.sqrt(L[0][0] / C[0][0])}


def modes(lc: dict) -> dict:
    """{T, z, delay_s_m} of the section: V = T Vm, I = inv(T)' Im. With
    C = S S' and S' L S = Q diag(lambda) Q', T = inv(S') Q makes C_m = 1 and
    L_m = lambda exactly, even where the modes are degenerate (homogeneous
    stripline: L C is a multiple of 1, and a plain eig of L C returns an
    identity T that diagonalises neither matrix)."""
    L, C = np.array(lc["L"], float), np.array(lc["C"], float)
    S = np.linalg.cholesky(C)
    lam, Q = np.linalg.eigh(S.T @ L @ S)
    T = np.linalg.solve(S.T, Q)
    return {"T": T, "z": np.sqrt(lam), "delay_s_m": np.sqrt(lam)}


@dataclass
class Deck:
    title: str = "coupled lines"
    elements: list = field(default_factory=list)
    probes: list = field(default_factory=list)
    tstop: float = 10e-9
    tstep: float = 25e-12
    _n: int = 0
    _t_opts: str = ""

    def _id(self) -> int:
        self._n += 1
        return self._n

    def r(self, a, b, ohm):
        self.elements.append(("r", a, b, float(ohm)))

    def c(self, a, b, farad):
        self.elements.append(("c", a, b, float(farad)))

    def ramp(self, node, v, t0, t_r):
        """A step of v volts starting at t0 with a linear edge t_r long."""
        self.elements.append(("v", node, float(v), float(t0), float(t_r)))

    def line(self, a, b, z0, td):
        self.elements.append(("t", a, b, float(z0), float(td)))

    def coupled(self, a1, a2, b1, b2, lc, length_m, r=(0.0, 0.0)):
        """Lines a1-b1 and a2-b2 coupled over length_m; r = ohm/m of each."""
        self.elements.append(("k", a1, a2, b1, b2, lc, float(length_m), tuple(r)))

    def _coupled_cpl(self, e, out):
        a1, a2, b1, b2, lc, length, r = e[1:]
        i = self._id()
        L, C = lc["L"], lc["C"]
        out.append(f"P{i} {a1} {a2} 0 {b1} {b2} 0 cpl{i}")
        out.append(f".model cpl{i} CPL R={max(r[0], CPL_MIN_R):.6g} 0 {max(r[1], CPL_MIN_R):.6g} "
                   f"L={L[0][0]:.8g} {L[0][1]:.8g} {L[1][1]:.8g} G=0 0 0 "
                   f"C={C[0][0]:.8g} {C[0][1]:.8g} {C[1][1]:.8g} length={length:.8g}")

    def _coupled_modal(self, e, out):
        a1, a2, b1, b2, lc, length, _ = e[1:]
        i = self._id()
        m = modes(lc)
        T = m["T"]
        for j in (0, 1):
            ma, mb = f"m{i}a{j}", f"m{i}b{j}"
            out.append(f"T{i}m{j} {ma} 0 {mb} 0 Z0={m['z'][j]:.8g} "
                       f"TD={m['delay_s_m'][j] * length:.8g}{self._t_opts}")
        for side, ports in (("a", (a1, a2)), ("b", (b1, b2))):
            for k, p in enumerate(ports):
                x = f"x{i}{side}{k}"
                y = f"y{i}{side}{k}"
                sense = f"Vs{i}{side}{k}"
                out.append(f"{sense} {p} {x} 0")
                out.append(f"E{i}{side}{k}0 {x} {y} m{i}{side}0 0 {T[k, 0]:.10g}")
                out.append(f"E{i}{side}{k}1 {y} 0 m{i}{side}1 0 {T[k, 1]:.10g}")
                for j in (0, 1):
                    out.append(f"F{i}{side}{k}{j} 0 m{i}{side}{j} {sense} {T[k, j]:.10g}")

    def _ladder_line(self, a, b, z0, td, out, seg_s=2e-12):
        n = max(4, math.ceil(td / seg_s))
        i = self._id()
        prev = a
        for k in range(n):
            nxt = b if k == n - 1 else f"l{i}n{k}"
            out.append(f"L{i}k{k} {prev} {nxt} {z0 * td / n:.8g}")
            out.append(f"C{i}k{k} {nxt} 0 {td / z0 / n:.8g}")
            prev = nxt

    def _coupled_ladder(self, e, out, seg_m=0.5e-3):
        """LC sections with K and a mutual C, every seg_m: slow, no modes, no
        delay device, so a check on the other two."""
        a1, a2, b1, b2, lc, length, _ = e[1:]
        i = self._id()
        L, C = lc["L"], lc["C"]
        n = max(4, math.ceil(length / seg_m))
        dl = length / n
        k12 = L[0][1] / math.sqrt(L[0][0] * L[1][1])
        pa, pb = a1, a2
        for k in range(n):
            na = b1 if k == n - 1 else f"q{i}a{k}"
            nb = b2 if k == n - 1 else f"q{i}b{k}"
            out.append(f"L{i}a{k} {pa} {na} {L[0][0] * dl:.8g}")
            out.append(f"L{i}b{k} {pb} {nb} {L[1][1] * dl:.8g}")
            out.append(f"K{i}{k} L{i}a{k} L{i}b{k} {k12:.8g}")
            out.append(f"C{i}a{k} {na} 0 {(C[0][0] + C[0][1]) * dl:.8g}")
            out.append(f"C{i}b{k} {nb} 0 {(C[1][1] + C[0][1]) * dl:.8g}")
            out.append(f"C{i}m{k} {na} {nb} {-C[0][1] * dl:.8g}")
            pa, pb = na, nb

    def text(self, mode: str, engine: str, stem: str = "out") -> str:
        """The deck. mode: cpl | modal | ladder; engine: ngspice | xyce."""
        if mode == "cpl" and engine != "ngspice":
            raise ValueError("Xyce has no multiconductor line device: use mode=modal")
        self._n = 0
        self._t_opts = T_OPTS_NGSPICE if engine == "ngspice" else ""
        out = [f"* {self.title}"]
        for e in self.elements:
            kind = e[0]
            if kind == "r":
                out.append(f"R{self._id()} {e[1]} {e[2]} {e[3]:.8g}")
            elif kind == "c":
                out.append(f"C{self._id()} {e[1]} {e[2]} {e[3]:.8g}")
            elif kind == "v":
                _, node, v, t0, t_r = e
                width = 10.0 * max(self.tstop, 1e-9)
                out.append(f"V{self._id()} {node} 0 PULSE(0 {v:.6g} {t0:.6g} {t_r:.6g} "
                           f"{t_r:.6g} {width:.6g} {2.5 * width:.6g})")
            elif kind == "t" and mode == "ladder":
                self._ladder_line(e[1], e[2], e[3], e[4], out)
            elif kind == "t":
                out.append(f"T{self._id()} {e[1]} 0 {e[2]} 0 Z0={e[3]:.8g} "
                           f"TD={e[4]:.8g}{self._t_opts}")
            elif mode == "ladder":
                self._coupled_ladder(e, out)
            elif mode == "cpl":
                self._coupled_cpl(e, out)
            else:
                self._coupled_modal(e, out)
        vs = " ".join(f"v({p})" for p in self.probes)
        tran = f"{self.tstep:.6g} {self.tstop:.6g} 0 {self.tstep:.6g}"
        if engine == "xyce":
            out += [f".tran {tran}", f".print tran format=csv file={stem}.csv {vs}"]
        else:
            out += [".control", "set wr_singlescale", f"tran {tran}",
                    f"wrdata {stem}.txt {vs}", ".endc"]
        out.append(".end")
        return "\n".join(out) + "\n"

    def run(self, mode: str = "cpl", engine: str = "ngspice", timeout: float = 60) -> dict:
        """{time: array, <probe>: array} from the engine, or SolverError."""
        with tempfile.TemporaryDirectory(prefix="hwde-xt-") as d:
            deck = Path(d) / "x.cir"
            deck.write_text(self.text(mode, engine, "x"), encoding="utf-8")
            args = [deck.name] if engine == "xyce" else ["-b", deck.name]
            r = solvers.run(engine, args, cwd=d, timeout=timeout)
            out = Path(d) / ("x.csv" if engine == "xyce" else "x.txt")
            if not r.ok or not out.is_file():
                raise solvers.SolverError(f"{engine}: {(r.stdout + r.stderr)[-400:]}")
            if engine == "xyce":
                a = np.loadtxt(out, delimiter=",", skiprows=1, ndmin=2)
            else:
                a = np.loadtxt(out, ndmin=2)
        res = {"time": a[:, 0]}
        for k, p in enumerate(self.probes):
            res[p] = a[:, k + 1]
        return res


def peak(wave) -> float:
    """The value of largest magnitude, signed."""
    w = np.asarray(wave)
    return float(w[np.argmax(np.abs(w))])


def copper_r(w_mm: float, t_mm: float) -> float:
    """DC resistance per metre of a trace (the skin effect isn't in the model)."""
    return RHO_CU / (w_mm * 1e-3 * t_mm * 1e-3)
