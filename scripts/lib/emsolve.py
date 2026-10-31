"""emsolve.py - decks for the field solvers in lib/solvers.py, and their readers.

openEMS (FDTD) reads a CSXCAD XML deck. The image has no CSXCAD Python
bindings, so `Csx` below writes the XML the way CSXCAD's own matlab
front end does (share/openEMS/matlab, share/CSXCAD/matlab in the tree):
properties (Metal, Material, LumpedElement, Excitation, ProbeBox) each with
their Box primitives, and a rectilinear grid. Probes are written as text
(time, value) files in the sim folder.

`microstrip_line` is the board-facing study: a stackup's microstrip as a
straight line, fed by a lumped source, run into a PML, and read with three
voltage probes and two current probes in its middle. From the telegrapher
equations dV/dx = -Z'I and dI/dx = -Y'V,

    Z0^2 = V2 (V1 - V3) / (I1^2 - I2^2),   cosh(gamma d) = (V1 + V3) / (2 V2)

with V1..V3 at x - d, x, x + d and I1, I2 at x -+ d/2, hold exactly on a
uniform line for any d and whatever its standing wave, so the answer does
not hang on how well the far end is matched or on the probe spacing.

Palace (FEM) reads a JSON config and a gmsh mesh: `box_mesh_geo` and
`palace_eigen_config` build the closed-cavity eigenmode case the validation
uses; Palace's eigen frequencies come back in postpro/eig.csv.

FastHenry and FasterCap read their own text decks: `fasthenry_bar` (a
straight rectangular bar) and `fastercap_cube` (a unit cube's six faces as
panels), with parsers for their outputs.

Lengths here are mm unless named otherwise.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from xml.sax.saxutils import quoteattr

C0 = 299792458.0
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * math.pi


# ================================================================ openEMS

def _fmt(v) -> str:
    if isinstance(v, (list, tuple)):
        return ",".join(_fmt(x) for x in v)
    if isinstance(v, float):
        return repr(round(v, 12))
    return str(v)


class Csx:
    """A CSXCAD + openEMS deck, written by `xml()`."""

    def __init__(self, unit: float = 1e-3):
        self.unit = unit
        self.props: list[tuple[str, dict, dict | None, list]] = []
        self.lines = {"x": set(), "y": set(), "z": set()}
        self.fdtd = {"NumberOfTimesteps": 200000, "endCriteria": 1e-5}
        self.exc = {"Type": 0, "f0": 1e9, "fc": 1e9}
        self.bc = ["PEC"] * 6

    def prop(self, kind: str, name: str, attrs: dict | None = None,
             inner: dict | None = None) -> str:
        self.props.append((kind, {"Name": name, **(attrs or {})}, inner, []))
        return name

    def box(self, name: str, p1, p2, prio: int = 0) -> None:
        for kind, attrs, inner, boxes in self.props:
            if attrs["Name"] == name:
                boxes.append((prio, tuple(map(float, p1)), tuple(map(float, p2))))
                return
        raise KeyError(name)

    def polygon(self, name: str, pts, z: float, prio: int = 0) -> None:
        """A zero-thickness polygon in the plane z (CSXCAD's Polygon
        primitive, normal +z): board copper drawn as it is on the board."""
        for kind, attrs, inner, boxes in self.props:
            if attrs["Name"] == name:
                boxes.append(("poly", prio, float(z), [(float(x), float(y)) for x, y in pts]))
                return
        raise KeyError(name)

    def metal(self, name):
        return self.prop("Metal", name)

    def material(self, name, eps_r=1.0, kappa=0.0):
        inner = {"Epsilon": eps_r}
        if kappa:
            inner["Kappa"] = kappa
        return self.prop("Material", name, None, inner)

    def lumped(self, name, direction: int, r: float):
        return self.prop("LumpedElement", name, {"Direction": direction, "Caps": 1, "R": r})

    def excitation(self, name, vec):
        return self.prop("Excitation", name, {"Type": 0, "Excite": vec})

    def probe(self, name, kind: int, weight: float = 1.0):
        """kind 0 voltage (E integrated along the box), 1 current (H around it)."""
        return self.prop("ProbeBox", name, {"Type": kind, "Weight": weight})

    def gauss(self, f0: float, fc: float):
        self.exc = {"Type": 0, "f0": f0, "fc": fc}
        self.fdtd["f_max"] = f0 + fc

    def add_lines(self, axis: str, vals) -> None:
        self.lines[axis].update(round(float(v), 9) for v in vals)

    def xml(self) -> str:
        bcn = ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax"]
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>', "<openEMS>"]
        fd = " ".join(f"{k}={quoteattr(_fmt(v))}" for k, v in self.fdtd.items())
        out.append(f"  <FDTD {fd}>")
        ex = " ".join(f"{k}={quoteattr(_fmt(v))}" for k, v in self.exc.items())
        out.append(f"    <Excitation {ex}/>")
        bc = " ".join(f'{n}="{b}"' for n, b in zip(bcn, self.bc))
        out.append(f"    <BoundaryCond {bc}/>")
        out.append("  </FDTD>")
        out.append('  <ContinuousStructure CoordSystem="0">')
        out.append("    <Properties>")
        for kind, attrs, inner, boxes in self.props:
            a = " ".join(f"{k}={quoteattr(_fmt(v))}" for k, v in attrs.items())
            out.append(f"      <{kind} {a}>")
            if inner:
                ia = " ".join(f"{k}={quoteattr(_fmt(v))}" for k, v in inner.items())
                out.append(f"        <Property {ia}/>")
            out.append("        <Primitives>")
            for prim in boxes:
                if prim[0] == "poly":
                    _, prio, z, pts = prim
                    vs = "".join(f'<Vertex X1="{_fmt(x)}" X2="{_fmt(y)}"/>' for x, y in pts)
                    out.append(f'          <Polygon Priority="{prio}" NormDir="2" Elevation="{_fmt(z)}"'
                               f' QtyVertices="{len(pts)}">{vs}</Polygon>')
                    continue
                prio, p1, p2 = prim
                out.append(f'          <Box Priority="{prio}">'
                           f'<P1 X="{_fmt(p1[0])}" Y="{_fmt(p1[1])}" Z="{_fmt(p1[2])}"/>'
                           f'<P2 X="{_fmt(p2[0])}" Y="{_fmt(p2[1])}" Z="{_fmt(p2[2])}"/></Box>')
            out.append("        </Primitives>")
            out.append(f"      </{kind}>")
        out.append("    </Properties>")
        out.append(f'    <RectilinearGrid DeltaUnit="{self.unit}" CoordSystem="0">')
        for ax in "xyz":
            out.append(f"      <{ax.upper()}Lines>{_fmt(sorted(self.lines[ax]))}</{ax.upper()}Lines>")
        out.append("    </RectilinearGrid>")
        out.append("  </ContinuousStructure>")
        out.append("</openEMS>")
        return "\n".join(out) + "\n"


def graded(a: float, b: float, fine: float, coarse: float, grow: float = 1.4) -> list[float]:
    """Mesh lines from a to b (a < b), `fine` at a, growing by `grow` up to
    `coarse`, ending exactly at b."""
    out = [a]
    d = fine
    x = a
    while x + d < b - 1e-9:
        x += d
        out.append(x)
        d = min(d * grow, coarse)
    if b - out[-1] < 0.3 * d and len(out) > 1:
        out[-1] = b
    else:
        out.append(b)
    return out


def uniform(a: float, b: float, step: float) -> list[float]:
    n = max(1, math.ceil((b - a) / step - 1e-9))
    return [a + (b - a) * i / n for i in range(n + 1)]


def read_probe(path: Path) -> tuple[list[float], list[float]]:
    ts, vs = [], []
    for line in Path(path).read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        parts = s.split()
        ts.append(float(parts[0]))
        vs.append(float(parts[1]))
    return ts, vs


def dft(ts, vs, freqs):
    """Discrete-time Fourier transform of a probe at `freqs` (Hz)."""
    import numpy as np
    t = np.asarray(ts)
    v = np.asarray(vs)
    dt = float(np.mean(np.diff(t)))
    return [complex(np.sum(v * np.exp(-2j * math.pi * f * t)) * dt) for f in freqs]


def microstrip_line(w: float, h: float, er: float, t: float = 0.0,
                    f_max: float = 4e9, length: float | None = None,
                    cells_per_w: int = 12, cells_per_h: int = 8) -> tuple[Csx, dict]:
    """The deck for a straight microstrip w wide on h of er (mm), and where
    its probes are (for `microstrip_z0_from_probes`). Ground is the zmin PEC
    boundary; the strip is a zero-thickness PEC sheet at z=h unless t > 0."""
    lam_min = C0 / f_max * 1e3 / math.sqrt(er)          # mm, in the substrate
    length = length or max(40.0, 1.2 * lam_min)
    side = max(10 * w, 12 * h, 8.0)                     # strip edge to y wall
    top = max(12 * h, 8.0)
    coarse = min(lam_min / 20, 2.0)
    csx = Csx(1e-3)
    csx.gauss(f_max / 2, f_max / 2)
    csx.bc = ["PEC", "PML_8", "MUR", "MUR", "PEC", "MUR"]
    csx.fdtd["endCriteria"] = 1e-4
    sub = csx.material("substrate", er)
    csx.box(sub, (0, -side - w / 2, 0), (length, side + w / 2, h))
    strip = csx.metal("strip")
    csx.box(strip, (0, -w / 2, h), (length, w / 2, h + t), prio=10)
    # mesh: x uniform; y fine across the strip, graded out; z fine in the
    # substrate, graded above (openEMS's thirds rule at the strip's edges)
    dx = min(coarse, lam_min / 30)
    csx.add_lines("x", uniform(0, length, dx))
    fy = w / cells_per_w
    # the thirds rule: no line on the edge itself, one a third of a cell
    # inside and one two thirds outside (openEMS's own advice for a sheet)
    edge_in, edge_out = w / 2 - fy / 3, w / 2 + 2 * fy / 3
    ys = set(uniform(-edge_in, edge_in, fy)) | {edge_out, -edge_out}
    for y in graded(edge_out, side + w / 2, fy, coarse):
        ys |= {y, -y}
    csx.add_lines("y", ys)
    fz = h / cells_per_h
    zs = uniform(0, h, fz) + graded(h, h + top, fz, coarse)
    if t > 0:
        zs += [h + t]
    csx.add_lines("z", zs)
    # feed: a 50 ohm lumped source from ground to the strip at x=dx
    xf = sorted(csx.lines["x"])[1]
    exc = csx.excitation("feed", (0, 0, -1))
    csx.box(exc, (xf, -w / 2, 0), (xf, w / 2, h), prio=5)
    res = csx.lumped("feed_r", 2, 50.0)
    csx.box(res, (xf, -w / 2, 0), (xf, w / 2, h), prio=5)
    # probes at the middle: V at x1, x2, x3, `span` (odd) cells apart, so
    # the current probes halfway between fall on H (dual) lines; the reader
    # is exact for any spacing, and a wide one conditions gamma well
    xl = sorted(csx.lines["x"])
    k = len(xl) // 2
    span = max(1, min(int(lam_min / 6 / dx), k - 2)) | 1
    x1, x2, x3 = xl[k - span], xl[k], xl[k + span]
    for name, x in (("u1", x1), ("u2", x2), ("u3", x3)):
        csx.probe(name, 0)
        csx.box(name, (x, 0, 0), (x, 0, h))
    ylines = sorted(csx.lines["y"])
    zlines = sorted(csx.lines["z"])
    # the loop around the strip runs on dual lines (half a cell past the
    # lines at the strip's edge and either side of it), as AddMSLPort does
    j = min(range(len(ylines)), key=lambda i: abs(ylines[i] - w / 2))
    iy = ylines[j] + (ylines[j + 1] - ylines[j]) / 2
    jb = min(range(len(zlines)), key=lambda i: abs(zlines[i] - h))
    jt = min(range(len(zlines)), key=lambda i: abs(zlines[i] - (h + t)))
    iz0 = zlines[jb - 1] - (zlines[jb - 1] - zlines[jb - 2]) / 2
    iz1 = zlines[jt + 1] + (zlines[jt + 2] - zlines[jt + 1]) / 2
    for name, x in (("i1", (x1 + x2) / 2), ("i2", (x2 + x3) / 2)):
        csx.probe(name, 1)
        csx.box(name, (x, -iy, iz0), (x, iy, iz1))
    where = {"x": [x1, x2, x3], "xi": [(x1 + x2) / 2, (x2 + x3) / 2],
             "length": length, "cells": len(xl) * len(ylines) * len(zlines)}
    return csx, where


def microstrip_z0_from_probes(sim: Path, where: dict, freqs) -> list[dict]:
    """Z0 and eps_eff at each frequency from the probes `microstrip_line` set."""
    import cmath
    u = [dft(*read_probe(sim / n), freqs) for n in ("u1", "u2", "u3")]
    i = [dft(*read_probe(sim / n), freqs) for n in ("i1", "i2")]
    d = (where["x"][2] - where["x"][0]) / 2 * 1e-3
    out = []
    for k, f in enumerate(freqs):
        u1, u2, u3 = u[0][k], u[1][k], u[2][k]
        i1, i2 = i[0][k], i[1][k]
        # V = A e^-gx + B e^gx, Z0 I = A e^-gx - B e^gx, x2 = 0, currents at
        # -d/2 and +d/2:  u1 - u3 = 2 Z0 (i1 + i2) sinh(gd/2) and
        # i1 - i2 = 2 u2 sinh(gd/2) / Z0, so their ratio is gamma-free:
        z = cmath.sqrt(u2 * (u1 - u3) / (i1 * i1 - i2 * i2))
        gamma = cmath.acosh((u1 + u3) / (2 * u2)) / d
        beta = abs(gamma.imag)
        eps = (beta * C0 / (2 * math.pi * f)) ** 2
        out.append({"f": f, "z0": abs(z.real), "z0_imag": z.imag, "eps_eff": eps})
    return out


# ---------------------------------------------------------- planar circuits

def _axis_lines(fixed, lo, hi, fine, coarse, pad):
    """Mesh lines along one axis: every copper edge in `fixed`, `fine`
    steps across the copper's extent [lo, hi], graded out to `pad` beyond
    it at up to `coarse`. Lines closer than fine/3 to an edge are dropped so
    the edges keep their own lines."""
    fixed = sorted(set(round(v, 6) for v in fixed))
    core = set(fixed)
    x = lo
    for v in uniform(lo, hi, fine):
        if all(abs(v - f) > fine / 3 for f in fixed):
            core.add(round(v, 6))
    out = set(core)
    out |= set(graded(hi, hi + pad, fine, coarse))
    out |= set(-v for v in graded(-lo, -lo + pad, fine, coarse))
    return sorted(out)


def _snap(lines, v):
    return min(lines, key=lambda q: abs(q - v))


def planar_deck(polys, ports, h: float, er: float, f_max: float,
                excite: int = 0, tand: float = 0.0, fine: float | None = None,
                feed_len: float | None = None):
    """The deck for a planar microstrip circuit and where its probes are.

    polys: the top copper, a list of [(x, y), ...] outlines (mm), drawn as
    zero-thickness PEC at z = h over an infinite ground (the zmin PEC wall)
    through h of er (tan delta as a conductivity at f_max/2). ports: one
    dict per port, {"x", "y", "dir": "+x"|"-x"|"+y"|"-y", "w"}: its
    reference plane at (x, y), the line w wide leaving the circuit toward
    dir. Each port gets a straight feed of that width, feed_len long, ending
    in a 50 ohm lumped port (the source on port `excite`), and three voltage
    probes along it that `planar_waves` splits into the wave going in and
    the wave coming out, moved to the reference plane. A port's line must
    run straight out of the circuit for feed_len (the deck adds that copper,
    so the circuit should stop at its planes)."""
    lam_min = C0 / f_max * 1e3 / math.sqrt(er)
    feed_len = feed_len or max(lam_min / 2, 10 * h)
    ws = [p["w"] for p in ports]
    fine = fine or min(min(ws) / 6, lam_min / 40, h / 2)
    coarse = min(lam_min / 15, 3.0)
    copper = [list(map(tuple, pl)) for pl in polys]
    feeds = []
    for p in ports:
        x, y, w, d = p["x"], p["y"], p["w"], p["dir"]
        sx = {"+x": 1, "-x": -1}.get(d, 0)
        sy = {"+y": 1, "-y": -1}.get(d, 0)
        ex, ey = x + sx * feed_len, y + sy * feed_len
        if sx:
            copper.append([(min(x, ex), y - w / 2), (max(x, ex), y - w / 2),
                           (max(x, ex), y + w / 2), (min(x, ex), y + w / 2)])
        else:
            copper.append([(x - w / 2, min(y, ey)), (x + w / 2, min(y, ey)),
                           (x + w / 2, max(y, ey)), (x - w / 2, max(y, ey))])
        feeds.append((x, y, ex, ey, sx, sy, w))
    xs = [q[0] for pl in copper for q in pl]
    ys = [q[1] for pl in copper for q in pl]
    pad = max(lam_min / 6, 12 * h, 6.0)
    csx = Csx(1e-3)
    csx.gauss(f_max / 2, f_max / 2)
    csx.bc = ["MUR", "MUR", "MUR", "MUR", "PEC", "MUR"]
    csx.fdtd["endCriteria"] = 1e-4
    kappa = 2 * math.pi * (f_max / 2) * EPS0 * er * tand if tand else 0.0
    sub = csx.material("substrate", er, kappa)
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    csx.box(sub, (x0, y0, 0), (x1, y1, h))
    cu = csx.metal("copper")
    for pl in copper:
        csx.polygon(cu, pl, h, prio=10)
    csx.add_lines("x", _axis_lines(xs, min(xs), max(xs), fine, coarse, pad))
    csx.add_lines("y", _axis_lines(ys, min(ys), max(ys), fine, coarse, pad))
    fz = h / 6
    top = max(12 * h, lam_min / 6)
    csx.add_lines("z", uniform(0, h, fz) + graded(h, h + top, fz, coarse))
    xl, yl = sorted(csx.lines["x"]), sorted(csx.lines["y"])
    where = {"ports": [], "cells": len(xl) * len(yl) * len(csx.lines["z"])}
    for k, (x, y, ex, ey, sx, sy, w) in enumerate(feeds):
        # the port: a 50 ohm lumped element from ground to the feed's end
        # (and the source on the excited port), one cell in from the end
        if sx:
            xe = _snap(xl, ex - sx * fine)
            pa, pb = (xe, y - w / 2, 0), (xe, y + w / 2, h)
        else:
            ye = _snap(yl, ey - sy * fine)
            pa, pb = (x - w / 2, ye, 0), (x + w / 2, ye, h)
        if k == excite:
            e = csx.excitation(f"src{k}", (0, 0, -1))
            csx.box(e, pa, pb, prio=5)
        r = csx.lumped(f"port{k}", 2, 50.0)
        csx.box(r, pa, pb, prio=5)
        # three probes a third of the feed apart, the first a quarter in
        pr = []
        for j, frac in enumerate((0.25, 0.5, 0.75)):
            if sx:
                px = _snap(xl, x + sx * frac * feed_len)
                csx.probe(f"p{k}u{j}", 0)
                csx.box(f"p{k}u{j}", (px, y, 0), (px, y, h))
                pr.append(abs(px - x))
            else:
                py = _snap(yl, y + sy * frac * feed_len)
                csx.probe(f"p{k}u{j}", 0)
                csx.box(f"p{k}u{j}", (x, py, 0), (x, py, h))
                pr.append(abs(py - y))
        where["ports"].append({"dist": pr})
    return csx, where


def planar_waves(sim: Path, where: dict, k: int, freqs) -> list[tuple[complex, complex]]:
    """(a, b) at port k's reference plane per frequency: a the wave going
    into the circuit, b the wave coming out, from the port's three voltage
    probes at distances s0 < s1 < s2 from the plane. With s measured
    outward, V(s) = a e^(gamma s) + b e^(-gamma s); the probes need not be
    evenly spaced, so gamma comes from the first and last pair by solving
    the exact relation numerically, then (a, b) by least squares."""
    import cmath
    import numpy as np
    us = [dft(*read_probe(sim / f"p{k}u{j}"), freqs) for j in range(3)]
    s = [d * 1e-3 for d in where["ports"][k]["dist"]]
    out = []
    for i, f in enumerate(freqs):
        u = [us[j][i] for j in range(3)]
        # evenly spaced probes (snapped to the mesh, so nearly): the middle
        # one gives cosh(gamma d) = (u0 + u2) / (2 u1)
        d = (s[2] - s[0]) / 2
        g = cmath.acosh((u[0] + u[2]) / (2 * u[1])) / d
        if g.imag < 0:
            g = -g
        A = np.array([[cmath.exp(g * sj), cmath.exp(-g * sj)] for sj in s])
        sol, *_ = np.linalg.lstsq(A, np.array(u), rcond=None)
        out.append((complex(sol[0]), complex(sol[1])))
    return out


def planar_sparams(sim: Path, where: dict, excite: int, freqs) -> list[list[complex]]:
    """Column `excite` of S: S[i][excite] = b_i / a_excite, per frequency.
    The other ports' 50 ohm ends reflect a little (a_i != 0); that error is
    about |S_ii * Gamma_end|, small for a 50 ohm line ending in 50 ohm."""
    waves = [planar_waves(sim, where, k, freqs) for k in range(len(where["ports"]))]
    return [[waves[i][n][1] / waves[excite][n][0] for i in range(len(waves))]
            for n in range(len(freqs))]


def touchstone(freqs, cols: dict, n: int) -> str:
    """A Touchstone v1 file (Hz, S, RI, R 50) from S columns cols[j] =
    [[S_0j..S_(n-1)j] per freq]; columns not run are written as 0."""
    lines = [f"! hwde sim_solve planar, openEMS", "# Hz S RI R 50"]
    # v1 order: a 2-port is S11 S21 S12 S22; n > 2 is row by row
    order = ([(0, 0), (1, 0), (0, 1), (1, 1)] if n == 2
             else [(i, j) for i in range(n) for j in range(n)])
    for m, f in enumerate(freqs):
        row = [f"{f:.6e}"]
        for i, j in order:
            v = cols[j][m][i] if j in cols else 0j
            row += [f"{v.real:.6e}", f"{v.imag:.6e}"]
        lines.append(" ".join(row))
    return "\n".join(lines) + "\n"


# ================================================================ Palace

def box_mesh_geo(a: float, b: float, d: float, size: float, order: int = 1) -> str:
    """A gmsh .geo for a closed box a x b x d (mm): volume 1, walls 2."""
    return (f'SetFactory("OpenCASCADE");\n'
            f"Box(1) = {{0, 0, 0, {a}, {b}, {d}}};\n"
            f'Physical Volume("air", 1) = {{1}};\n'
            f'Physical Surface("walls", 2) = {{1, 2, 3, 4, 5, 6}};\n'
            f"Mesh.MeshSizeMax = {size};\nMesh.MeshSizeMin = {size / 4};\n"
            f"Mesh.ElementOrder = {order};\nMesh.MshFileVersion = 2.2;\n")


def palace_eigen_config(mesh: str, n: int, target_ghz: float,
                        eps_r: float = 1.0, order: int = 2) -> dict:
    return {
        "Problem": {"Type": "Eigenmode", "Verbose": 1, "Output": "postpro"},
        "Model": {"Mesh": mesh, "L0": 1e-3},
        "Domains": {"Materials": [{"Attributes": [1], "Permeability": 1.0,
                                   "Permittivity": eps_r}]},
        "Boundaries": {"PEC": {"Attributes": [2]}},
        "Solver": {"Order": order,
                   "Eigenmode": {"N": n, "Tol": 1e-8, "Target": target_ghz, "Save": 0},
                   "Linear": {"Type": "Default", "KSPType": "GMRES", "Tol": 1e-8,
                              "MaxIts": 500}},
    }


def palace_eigs(postpro: Path) -> list[float]:
    """Re{f} in GHz from Palace's postpro/eig.csv, ascending."""
    rows = Path(postpro, "eig.csv").read_text().splitlines()
    out = []
    for line in rows[1:]:
        cols = [c.strip() for c in line.split(",")]
        if len(cols) > 1:
            out.append(float(cols[1]))
    return sorted(out)


def cavity_modes(a: float, b: float, d: float, n: int, eps_r: float = 1.0) -> list[float]:
    """The first n resonances (GHz) of a closed a x b x d (mm) PEC cavity,
    TE and TM counted apart (degenerate pairs appear twice)."""
    fs = []
    for m in range(0, 6):
        for p in range(0, 6):
            for q in range(0, 6):
                if (m > 0) + (p > 0) + (q > 0) < 2:
                    continue
                f = C0 / (2 * math.sqrt(eps_r)) * math.sqrt(
                    (m / a) ** 2 + (p / b) ** 2 + (q / d) ** 2) * 1e3 / 1e9
                # TE_mpq exists with q>0 and (m or p)>0; TM_mpq with m,p>0.
                te = q > 0 and (m > 0 or p > 0)
                tm = m > 0 and p > 0
                fs += [f] * (int(te) + int(tm))
    return sorted(fs)[:n]


# ================================================================ FastHenry / FasterCap

def fasthenry_bar(l: float, w: float, t: float, sigma_s_per_m: float = 5.8e7,
                  f: float = 1e3, nw: int = 1, nh: int = 1) -> str:
    """A straight bar l x w x t (mm) along x, its two ends the port."""
    sig = sigma_s_per_m * 1e-3          # FastHenry wants 1/(unit*ohm), unit mm
    return ("* hwde bar\n.units mm\n"
            "N1 x=0 y=0 z=0\n"
            f"N2 x={l} y=0 z=0\n"
            f"E1 N1 N2 w={w} h={t} sigma={sig} nwinc={nw} nhinc={nh}\n"
            ".external N1 N2\n"
            f".freq fmin={f} fmax={f} ndec=1\n.end\n")


def read_zc(path: Path) -> tuple[float, complex]:
    """(frequency, Z11) from FastHenry's Zc.mat (the first port), e.g.
    'Impedance matrix for frequency = 1000 1 x 1' then '  0.00492 +4.38e-05j'."""
    txt = Path(path).read_text()
    m = re.search(r"frequency\s*=\s*([0-9.eE+\-]+)[^\n]*\n\s*([0-9.eE+\-]+)\s*([+\-])\s*([0-9.eE+\-]+)j", txt)
    if not m:
        raise ValueError(f"no impedance in {path}")
    im = float(m.group(4)) * (1 if m.group(3) == "+" else -1)
    return float(m.group(1)), complex(float(m.group(2)), im)


def bar_partial_inductance(l: float, w: float, t: float) -> float:
    """Partial self-inductance (H) of a straight rectangular bar (mm),
    Ruehli's / Grover's approximation (good to ~1% for l >> w + t):
    L = mu0 l / 2pi * (ln(2l/(w+t)) + 1/2 + 0.2235 (w+t)/l)."""
    lm = l * 1e-3
    return MU0 * lm / (2 * math.pi) * (math.log(2 * l / (w + t)) + 0.5 + 0.2235 * (w + t) / l)


def fastercap_cube(a: float, n: int) -> str:
    """FastCap2-format panels for a cube of side a (mm): each face cut into
    n x n quadrilaterals, conductor 1. FasterCap reads metres and refines
    from there."""
    lines = ["0 hwde cube"]
    a = a * 1e-3
    h = a / n

    def quad(p):
        return "Q 1 " + " ".join(f"{c:.9g}" for pt in p for c in pt)

    for i in range(n):
        for j in range(n):
            u0, u1, v0, v1 = i * h, (i + 1) * h, j * h, (j + 1) * h
            for fixed in (0.0, a):
                lines.append(quad([(fixed, u0, v0), (fixed, u1, v0), (fixed, u1, v1), (fixed, u0, v1)]))
                lines.append(quad([(u0, fixed, v0), (u1, fixed, v0), (u1, fixed, v1), (u0, fixed, v1)]))
                lines.append(quad([(u0, v0, fixed), (u1, v0, fixed), (u1, v1, fixed), (u0, v1, fixed)]))
    return "\n".join(lines) + "\n"


# The capacitance of a unit cube, in units of 4 pi eps0 a: 0.66067813 (Hwang
# & Mascagni 2004, random-walk; Read 1997's boundary-element 0.6606785).
CUBE_C = 0.66067813


def read_fastercap(text: str) -> float:
    """The self-capacitance (F) of conductor 1 from FasterCap's output; the
    last 'Capacitance matrix' block wins (it is the refined one)."""
    blocks = re.split(r"Capacitance matrix is:", text)
    if len(blocks) < 2:
        raise ValueError("no capacitance matrix in FasterCap's output")
    last = blocks[-1]
    m = re.search(r"Dimension\s+\d+\s*x\s*\d+\s*\n\s*g?\d*\S*\s+([0-9.eE+\-]+)", last)
    if not m:
        m = re.search(r"([0-9.]+e[+\-]\d+)", last)
    return float(m.group(1))
