"""emantenna.py - a board's PCB antenna as an openEMS deck with a radiation
boundary and a near-field to far-field transform (#481).

`board_geo` cuts a board into plain data: the copper of every net inside the
antenna's region, the ground nets' copper on the whole board (the finite
ground is part of the antenna), and the board outline. `free_geo` writes the
same data for a structure drawn by hand (the known answers). `deck` turns it
and a `Feed` into a CSXCAD deck (lib/emsolve.Csx); `study` runs it, then runs
nf2ff, and `converge` repeats it on a finer mesh.

The model:
  - copper is a zero-thickness sheet on a mesh plane: PEC, or with `Loss` a
    ConductingSheet (skin effect for the copper's thickness);
  - the dielectric is the board's outline extruded, so the air around a
    notched board is air. A loss tangent is a conductivity, which is right at
    one frequency only: `Loss` fixes it at the band's centre, and a run with
    loss should be read near there;
  - the feed is a lumped 50 ohm port with its own V and I probes. A port
    between two layers (a probe, or a line to its ground) is one mesh edge, an
    in-plane port across a gap can be as wide as the strip;
  - the source is a Gaussian, centre f0, -20 dB at f0 +- fc. With fc <= f0/2
    its DC is -80 dB;
  - the boundary is PML_8 on all six faces. Its 8 cells are outside the air
    gap and nothing crosses them, unlike emboard's decks, whose substrate and
    feeds run through the walls (PML_8 grew without bound on vna-verify,
    emboard uses MUR there). A diverging record raises `Diverged`;
  - the NF2FF box sits 4 cells inside the PML, in the air, around everything.
    The dump is in the frequency domain at the `pattern` frequencies;
  - steps are set by the deck, not by the energy criterion (openEMS tests that
    only at its wall-clock progress report, #585). A record that has not rung
    down is rerun with the steps its own decay says it needs (`_steps_needed`).

The mesh's `fine` is the real cell over the whole of the antenna's own copper
(not only near its edges), with openEMS's thirds rule on its axis-aligned
edges, so halving `fine` halves the cells where the current runs. A ground
pour's edges get mesh lines near the antenna only; further out the ground is
meshed at the board cell. A post (drill) is a thin wire on the nearest mesh
line, because CSXCAD silently drops a box smaller than a cell.

Lengths mm, frequencies Hz. Far field in W/m2 at 1 m as written by nf2ff.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from . import emboard as eb
from . import emsolve as em

C0 = em.C0
ZREF = 50.0
PML_CELLS = 8
NF2FF_INSET = 4         # cells between the PML and the NF2FF box
RECORD_TRANSITS = 3     # corner-to-corner trips of the domain after the pulse
MERGE = 0.6             # mesh lines nearer than this many `fine` merge: the time step follows the least cell
TAIL_MAX = 1e-3         # the last tenth of a record against its peak
STEPS_MAX = 600000
FACES = ("xn", "xp", "yn", "yp", "zn", "zp")


class Diverged(RuntimeError):
    """The record grew instead of ringing down: an unstable boundary."""


@dataclass
class Loss:
    """Conductor and dielectric loss. tand None takes each slab's own from
    the geometry's stack, else this default."""
    tand: float | None = None
    sigma: float = 5.8e7        # S/m
    t_cu: float = 35e-6         # m
    tand_default: float = 0.02


@dataclass
class Feed:
    """A lumped port from point a to point b, axis-aligned. Between layers:
    a, b are on `layer` and `ref` at (x, y) (axis z). In-plane: across a gap on
    `layer`, from (x, y) to (x2, y2), w wide across the gap (0 = one edge)."""
    x: float
    y: float
    layer: str = "F.Cu"
    ref: str | None = None
    x2: float | None = None
    y2: float | None = None
    w: float = 0.0

    @classmethod
    def parse(cls, s: str) -> "Feed":
        """X,Y[,LAYER[,REF]] between layers; X,Y,X2,Y2[,LAYER[,W]] in-plane."""
        f = s.split(",")
        try:
            float(f[2]) if len(f) > 2 else None
            planar = len(f) >= 4
        except ValueError:
            planar = False
        if planar:
            return cls(float(f[0]), float(f[1]), f[4] if len(f) > 4 and f[4] else "F.Cu", None,
                       float(f[2]), float(f[3]), float(f[5]) if len(f) > 5 and f[5] else 0.0)
        if len(f) < 2:
            raise ValueError(f"feed {s!r}: X,Y[,LAYER[,REF]] or X,Y,X2,Y2[,LAYER[,W]]")
        return cls(float(f[0]), float(f[1]), f[2] if len(f) > 2 and f[2] else "F.Cu",
                   f[3] if len(f) > 3 and f[3] else None)

    @property
    def planar(self) -> bool:
        return self.x2 is not None

    @property
    def axis(self) -> int:
        if not self.planar:
            return 2
        dx, dy = abs(self.x2 - self.x), abs(self.y2 - self.y)
        if min(dx, dy) > 1e-9 or max(dx, dy) < 1e-9:
            raise ValueError(f"in-plane feed must lie along x or y: {self}")
        return 0 if dx > dy else 1


# ================================================================ geometry

def free_geo(layers: dict, slabs=(), copper=(), drills=(), outline=None, grounds=("GND",)) -> dict:
    """Geometry by hand: layers {name: z}, slabs [(z0, z1, er[, tand])],
    copper [(layer, net, pts)], outline [(x, y), ...] for the slabs."""
    names = sorted(layers, key=lambda k: -layers[k])
    stack = {"layers": names, "z": dict(layers), "slabs": [list(s[:3]) for s in slabs]}
    return {"stack": stack, "tand": [(s[3] if len(s) > 3 else None) for s in slabs],
            "copper": [{"layer": l, "net": n, "pts": [list(p) for p in pts]} for l, n, pts in copper],
            "drills": [dict(d) for d in drills], "outline": [list(p) for p in outline] if outline else None,
            "grounds": list(grounds), "source": "synthetic"}


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _resolve_grounds(grounds, nets) -> tuple:
    """The board's own names for `grounds`, with or without KiCad's "/"."""
    out = []
    for g in grounds:
        hits = [n for n in nets if n == g or n.lstrip("/") == g.lstrip("/")]
        if not hits:
            raise ValueError(f"ground net {g!r} has no copper on this board "
                             f"(nets: {sorted(n for n in nets if 'GND' in n.upper())[:20]})")
        out += hits
    return tuple(out)


def board_geo(pcb, region=None, grounds=("GND",), cut=(), shorts=(), outline=None) -> dict:
    """The board's copper for `deck`. region (x0, y0, x1, y1): the antenna's
    own copper; other nets outside it are dropped, ground nets are kept on the
    whole board. None = everything. cut: boxes (x0, y0, x1, y1[, layer]) of
    non-ground copper to remove (what lies behind a feed: the launch, an SMA).
    shorts: boxes (x0, y0, x1, y1, layer) of copper to add, to close a 0 ohm
    part or a net tie drawn only on the fab layers. outline (x0, y0, x1, y1):
    the dielectric and every net stop there (one coupon of a panel; its ground
    net is then only what lies inside). A ground may be named without KiCad's
    sheet slash (GND_A9 for /GND_A9); one that matches no copper is an error,
    since the deck would otherwise run with no ground plane at all."""
    from shapely.geometry import box as sbox
    from . import geom as G
    bg = G.load_board(pcb)
    x0, y0, x1, y1 = bg.outline.bounds
    geo = eb.extract(pcb, (x0 - 1, y0 - 1, x1 + 1, y1 + 1))
    grounds = _resolve_grounds(grounds, {item["net"] for item in geo["copper"]})
    clip = sbox(*region) if region else None
    board = bg.outline if outline is None else bg.outline.intersection(sbox(*outline))
    if board.is_empty or board.geom_type != "Polygon":
        raise ValueError(f"outline {outline}: not one piece of the board")
    keep = []
    for item in geo["copper"]:
        from shapely.geometry import Polygon
        g = Polygon(item["pts"]).buffer(0).intersection(board)
        if item["net"] not in grounds:
            if clip is not None:
                g = g.intersection(clip)
            for c in cut:
                if len(c) < 5 or c[4] == item["layer"]:
                    g = g.difference(sbox(*c[:4]))
        for pts in eb._polys(g):
            keep.append({"layer": item["layer"], "net": item["net"], "pts": pts})
    for s in shorts:
        keep.append({"layer": s[4], "net": "~short", "pts": [list(p) for p in _rect(*s[:4])]})
    from shapely.geometry import Point

    def inside(d):
        p = Point(d["x"], d["y"])
        return board.contains(p) and (clip is None or d["net"] in grounds or clip.contains(p))
    drills = [d for d in geo["drills"] if inside(d)]
    tand = [d.get("loss_tangent") for d in bg.stackup.dielectrics]
    return {"stack": geo["stack"], "tand": tand, "copper": keep, "drills": drills,
            "outline": [[round(a, 4), round(b, 4)] for a, b in list(board.simplify(0.005).exterior.coords)[:-1]],
            "grounds": list(grounds), "source": str(pcb), "region": list(region) if region else None}


# ================================================================ deck

@dataclass
class _Mesh:
    fixed: dict = field(default_factory=lambda: {"x": [], "y": []})
    zones: dict = field(default_factory=lambda: {"x": [], "y": []})
    pinned: dict = field(default_factory=lambda: {"x": [], "y": [], "z": []})
    edges: dict = field(default_factory=lambda: {"x": [], "y": []})     # thirds-rule cells


def default_fine(geo, feed: Feed, f_max: float) -> float:
    """A third of the narrowest signal copper, at most lam_d/40, from the
    widths of its axis-aligned pieces."""
    from shapely.geometry import Polygon
    stack = eb.Stack.from_dict(geo["stack"])
    lam = C0 / f_max * 1e3 / math.sqrt(stack.er_max())
    w = lam / 40 * 3
    for it in geo["copper"]:
        if it["net"] in geo["grounds"]:
            continue
        g = Polygon(it["pts"]).buffer(0)
        if g.is_empty:
            continue
        a, b, c, d = g.bounds
        w = min(w, max(min(c - a, d - b), 0.1))
    return round(w / 3, 4)


def _board_lines(lo, hi, step):
    n = max(1, math.ceil((hi - lo) / step))
    return [lo + (hi - lo) * i / n for i in range(n + 1)]


def deck(geo: dict, feed, f0: float, fc: float, pattern=(), *, fine: float | None = None,
         air: float | None = None, boundary: str = "PML_8", loss: Loss | None = None,
         zcells: int | None = None, max_cells: int = 8_000_000):
    """(Csx, where). f0, fc: the pulse; pattern: NF2FF frequencies."""
    from shapely.geometry import Polygon, box as sbox
    from shapely.ops import unary_union
    feed = feed if isinstance(feed, Feed) else Feed.parse(feed)
    stack = eb.Stack.from_dict(geo["stack"])
    f_max = f0 + fc
    if fc > f0 / 2 * 1.0001:
        raise ValueError(f"fc {fc:g} over f0/2 {f0 / 2:g}: the pulse would carry DC")
    lam0 = C0 / f_max * 1e3
    lam_d = lam0 / math.sqrt(stack.er_max())
    lam_c = C0 / f0 * 1e3
    fine = fine or default_fine(geo, feed, f_max)
    coarse = lam0 / 20
    board_cell = min(lam_d / 20, 5 * fine)
    air = air if air is not None else lam_c / 4
    grounds = set(geo.get("grounds") or ())

    # copper per layer: signal and ground
    per: dict[str, dict[str, list]] = {}
    for it in geo["copper"]:
        if it["layer"] in stack.z:
            g = Polygon(it["pts"]).buffer(0)
            if not g.is_empty:
                per.setdefault(it["layer"], {}).setdefault(
                    "gnd" if it["net"] in grounds else "sig", []).append(g)
    sig = {l: unary_union(d["sig"]) for l, d in per.items() if d.get("sig")}
    gnd = {l: unary_union(d["gnd"]) for l, d in per.items() if d.get("gnd")}
    everything = unary_union(list(sig.values()) + list(gnd.values()))
    if everything.is_empty:
        raise ValueError("no copper in the geometry")

    # the feed
    if feed.layer not in stack.z:
        raise ValueError(f"feed layer {feed.layer} not in {stack.layers}")
    if not feed.planar and feed.ref is None:
        from shapely.geometry import Point
        pt = Point(feed.x, feed.y)
        cands = [l for l in stack.layers if l != feed.layer and l in gnd
                 and gnd[l].buffer(1e-6).contains(pt)]
        if not cands:
            raise ValueError(f"no ground copper under the feed at ({feed.x}, {feed.y}): "
                             "give REF, or an in-plane feed")
        feed.ref = min(cands, key=lambda l: stack.gap(l, feed.layer))

    # mesh: the antenna's own copper uniformly fine, thirds on its edges
    m = _Mesh()
    bx0, by0, bx1, by1 = everything.bounds
    if geo.get("outline"):
        ox = Polygon(geo["outline"]).bounds
        bx0, by0, bx1, by1 = min(bx0, ox[0]), min(by0, ox[1]), max(bx1, ox[2]), max(by1, ox[3])
    domain_box = (bx0 - 1e4, by0 - 1e4, bx1 + 1e4, by1 + 1e4)
    feed_pts = [(feed.x, feed.y)] + ([(feed.x2, feed.y2)] if feed.planar else [])
    allsig = unary_union(list(sig.values())) if sig else None
    if allsig is None or allsig.is_empty:
        zone = sbox(feed.x, feed.y, feed.x, feed.y).buffer(5 * fine)
    else:
        zone = allsig
    cu = {"x": [], "y": []}
    for l, g in sig.items():
        ex, ey, other, (kx, ky) = eb._thirds(g, fine, domain_box)
        m.edges["x"] += ex
        m.edges["y"] += ey
        cu["x"] += kx
        cu["y"] += ky
        m.fixed["x"] += [q[0] for q in other]
        m.fixed["y"] += [q[1] for q in other]
    zb = zone.bounds
    pad = 2 * fine
    m.zones["x"].append((zb[0] - pad, zb[2] + pad))
    m.zones["y"].append((zb[1] - pad, zb[3] + pad))
    for px, py in feed_pts:
        m.pinned["x"].append(px)
        m.pinned["y"].append(py)
        if not (zb[0] - pad <= px <= zb[2] + pad):
            m.zones["x"].append((px - 3 * fine, px + 3 * fine))
        if not (zb[1] - pad <= py <= zb[3] + pad):
            m.zones["y"].append((py - 3 * fine, py + 3 * fine))
    if feed.planar and feed.w:
        i = 1 - feed.axis
        c = (feed.y if feed.axis == 0 else feed.x)
        m.pinned["xy"[i]] += [c - feed.w / 2, c + feed.w / 2]
    # ground edges: every long straight edge, and any vertex near the antenna
    near = (zone.buffer(max(4 * board_cell, 2.0)) if not zone.is_empty else None)
    for l, g in gnd.items():
        for part in (g.geoms if hasattr(g, "geoms") else [g]):
            ring = list(part.exterior.coords)
            for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]):
                seg = sbox(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                if near is not None and seg.intersects(near):
                    if abs(y1 - y0) > 1e-6 and abs(x1 - x0) < 1e-6:
                        m.fixed["x"].append(x0)
                    elif abs(x1 - x0) > 1e-6 and abs(y1 - y0) < 1e-6:
                        m.fixed["y"].append(y0)
                elif abs(x1 - x0) < 1e-6 and abs(y1 - y0) > 5:
                    m.fixed["x"].append(x0)
                elif abs(y1 - y0) < 1e-6 and abs(x1 - x0) > 5:
                    m.fixed["y"].append(y0)
    # a drill near the antenna pins its lines, one beyond snaps to the mesh
    drills = [d for d in geo.get("drills", []) if d["top"] in stack.z and d["bot"] in stack.z]
    for d in drills:
        if near is not None and near.contains(sbox(d["x"], d["y"], d["x"], d["y"]).centroid):
            m.fixed["x"].append(d["x"])
            m.fixed["y"].append(d["y"])
    # the board's own cell outside the fine zones: inside them two uniform
    # grids would merge into slivers
    for a, lo, hi in (("x", bx0, bx1), ("y", by0, by1)):
        m.fixed[a] += [v for v in _board_lines(lo, hi, board_cell)
                       if not any(p <= v <= q for p, q in m.zones[a])]

    # z: every copper plane, cells per slab, then air
    zs = sorted(stack.z.values())
    zl = list(zs)
    n_h = zcells or max(3, math.ceil(max([s[1] - s[0] for s in stack.slabs] or [0]) / (2 * fine)))
    for z0, z1, er in stack.slabs:
        h = z1 - z0
        n = zcells or max(2, math.ceil(h / (2 * fine)))
        zl += [z0 + h * i / n for i in range(n + 1)]
    fz = min([(s[1] - s[0]) / n_h for s in stack.slabs] or [fine])
    zhi = zs[-1] + air
    zlo = zs[0] - air
    zl += em.graded(zs[-1], zhi, fz, coarse)
    zl += [-v for v in em.graded(-zs[0], -zlo, fz, coarse)]

    # the domain: air gap + inset + PML, in coarse cells
    out = (PML_CELLS + NF2FF_INSET) * coarse
    dom = {"x": (bx0 - air - out, bx1 + air + out), "y": (by0 - air - out, by1 + air + out)}
    lines = {}
    for a in "xy":
        lo, hi = dom[a]
        fixed = [v for v in m.fixed[a] if lo < v < hi]
        lines[a] = eb.mesh_axis(fixed, m.zones[a], lo, hi, fine, coarse, m.pinned[a], m.edges[a],
                                min_step=MERGE * fine, cu_edges=sorted({round(e, 6) for e in cu[a]}))
    zfix = sorted(set(round(v, 6) for v in zl))
    zmin, zmax = zlo - out, zhi + out
    zgrow = eb.smooth_lines(eb.merge_lines(zfix + [zmin, zmax], fz / 1.6, zs), coarse)
    lines["z"] = zgrow
    cells = len(lines["x"]) * len(lines["y"]) * len(lines["z"])
    if cells > max_cells:
        raise ValueError(f"{cells} cells over the limit {max_cells}: "
                         f"mesh {[len(lines[a]) for a in 'xyz']} with fine {fine}: "
                         "use a coarser fine or a smaller board region")
    for a in "xyz":
        if max(q - p for p, q in zip(lines[a][-PML_CELLS - 1:], lines[a][-PML_CELLS:])) < coarse / 4:
            raise ValueError(f"the PML cells on {a} are too small: the air gap's grading did not finish")

    csx = em.Csx(1e-3)
    csx.gauss(f0, fc)
    csx.fdtd["endCriteria"] = eb.RUN_ALL_STEPS
    csx.bc = [boundary] * 6
    for a in "xyz":
        csx.add_lines(a, lines[a])

    # dielectric
    outline = geo.get("outline")
    if stack.slabs and not outline:
        raise ValueError("slabs need a board outline")
    tands = geo.get("tand") or [None] * len(stack.slabs)
    for i, (z0, z1, er) in enumerate(stack.slabs):
        kappa = 0.0
        if loss is not None:
            t = loss.tand if loss.tand is not None else (tands[i] if i < len(tands) and tands[i] is not None
                                                         else loss.tand_default)
            kappa = 2 * math.pi * f0 * em.EPS0 * er * t
        name = csx.material(f"slab{i}", er, kappa)
        for piece in eb.no_holes(Polygon(outline).buffer(0)):
            csx.linpoly(name, list(piece.exterior.coords)[:-1], z0, z1 - z0, prio=1)

    # copper
    cu = (csx.sheet("copper", loss.sigma, loss.t_cu) if loss is not None else csx.metal("copper"))
    added = {l: [g] for l, g in sig.items()}
    for l, g in gnd.items():
        added.setdefault(l, []).append(g)
    for layer, gs in added.items():
        for pts in eb._polys(unary_union(gs), simplify=0.001):
            csx.polygon(cu, pts, stack.z[layer], prio=10)
    if drills:
        post = csx.metal("barrels")
        for d in drills:
            x, y = em._snap(lines["x"], d["x"]), em._snap(lines["y"], d["y"])
            za, zb_ = sorted((stack.z[d["top"]], stack.z[d["bot"]]))
            csx.box(post, (x, y, za), (x, y, zb_), prio=20)

    # the port (openEMS's own LumpedPort, in XML)
    z = stack.z[feed.layer]
    if feed.planar:
        i = feed.axis
        o = 1 - i
        a_, b_ = [feed.x, feed.y, z], [feed.x2, feed.y2, z]
        if feed.w:
            c = a_[o]
            a_[o], b_[o] = c - feed.w / 2, c + feed.w / 2
        else:
            a_[o] = b_[o] = em._snap(lines["xy"[o]], a_[o])
        ny = i
    else:
        zr = stack.z[feed.ref]
        a_, b_ = [feed.x, feed.y, zr], [feed.x, feed.y, z]
        ny = 2
    sgn = 1 if b_[ny] > a_[ny] else -1
    vec = [0, 0, 0]
    vec[ny] = -sgn
    csx.box(csx.excitation("src", vec), a_, b_, prio=5)
    csx.box(csx.lumped("port", ny, ZREF), a_, b_, prio=5)
    mid = [(p + q) / 2 for p, q in zip(a_, b_)]
    u0, u1 = list(mid), list(mid)
    u0[ny], u1[ny] = a_[ny], b_[ny]
    csx.prop("ProbeBox", "ut", {"Type": 0, "Weight": -sgn})
    csx.box("ut", u0, u1)
    i0, i1 = list(a_), list(b_)
    i0[ny] = i1[ny] = mid[ny]
    csx.prop("ProbeBox", "it", {"Type": 1, "Weight": sgn, "NormDir": ny})
    csx.box("it", i0, i1)

    # NF2FF: a box of six faces in the air, inset from the PML
    if boundary.startswith("PML"):
        k = PML_CELLS + NF2FF_INSET
    else:
        k = NF2FF_INSET
    box_lo = [lines[a][k] for a in "xyz"]
    box_hi = [lines[a][-k - 1] for a in "xyz"]
    struct_lo = [bx0, by0, zs[0]]
    struct_hi = [bx1, by1, zs[-1]]
    for j in range(3):
        if box_lo[j] >= struct_lo[j] or box_hi[j] <= struct_hi[j]:
            raise ValueError("the NF2FF box does not enclose the structure")
    if pattern:
        for j, face in enumerate(FACES):
            ax = j // 2
            lo, hi = list(box_lo), list(box_hi)
            if j % 2:
                lo[ax] = hi[ax]
            else:
                hi[ax] = lo[ax]
            for tag, dtype in (("E", 10), ("H", 11)):
                name = csx.dump(f"nf2ff_{tag}_{face}", dtype, pattern)
                csx.box(name, lo, hi)
    span = [max(lines[a]) - min(lines[a]) for a in "xyz"]
    least = [min(q - p for p, q in zip(lines[a], lines[a][1:])) for a in "xyz"]
    dt = 1e-3 / (C0 * math.sqrt(sum(1 / d ** 2 for d in least)))
    pulse = 18 / (2 * math.pi * fc)
    trip = math.sqrt(sum(s * s for s in span)) * 1e-3 * math.sqrt(stack.er_max()) / C0
    csx.fdtd["NumberOfTimesteps"] = min(STEPS_MAX, math.ceil((pulse + RECORD_TRANSITS * trip) / dt))
    where = {"fine": fine, "coarse": round(coarse, 4), "air": round(air, 3), "board_cell": round(board_cell, 4),
             "mesh": [len(lines[a]) for a in "xyz"], "cells": cells, "boundary": boundary,
             "min_cell": [round(v, 5) for v in least], "feed_ref": feed.ref,
             "nf2ff_center": [round((a + b) / 2 * 1e-3, 6) for a, b in zip(box_lo, box_hi)],
             "pattern": [float(f) for f in pattern], "loss": loss is not None,
             "bounds": [bx0, by0, bx1, by1]}
    return csx, where


# ================================================================ readers

def feed_vi(sim: Path, freqs):
    """(V, I) of the feed per frequency, by openEMS's convention of 2 dt per
    sample: P = Re(V I*)/2 is then in the units of the NF2FF's Prad."""
    us = [2 * v for v in em.dft(*em.read_probe(sim / "ut"), freqs)]
    ii = [2 * v for v in em.dft(*em.read_probe(sim / "it"), freqs)]
    return us, ii


def record_tail(sim: Path) -> float:
    """The largest of the feed's V and I in the record's last tenth, each
    over its own peak."""
    import numpy as np
    worst = 0.0
    for name in ("ut", "it"):
        v = np.abs(em.read_probe(sim / name)[1])
        if not np.all(np.isfinite(v)):
            return float("inf")
        pk = float(v.max())
        if pk:
            worst = max(worst, float(v[-max(1, v.size // 10):].max()) / pk)
    return worst


def _steps_needed(sim: Path, steps: int) -> int:
    """Steps for the last tenth of the record to reach TAIL_MAX, from the decay
    of the feed's envelope over the record's second half, with a margin. Between
    1.25 and 4 times `steps`; twice `steps` if the record has no decay to read."""
    import numpy as np
    need = 0.0
    for name in ("ut", "it"):
        v = np.abs(em.read_probe(sim / name)[1])
        n = v.size // 10
        pk = float(v.max()) if v.size else 0.0
        if not n or not pk:
            return 2 * steps
        late, early = float(v[-n:].max()), float(v[-5 * n:-4 * n].max())
        if late <= TAIL_MAX * pk:
            continue
        if not 0 < late < early:
            return 2 * steps
        per_step = math.log(early / late) / (0.4 * steps)
        need = max(need, steps + math.log(late / (TAIL_MAX * pk)) / per_step)
    return math.ceil(max(1.25 * steps, min(4 * steps, 1.2 * need)))


def nf2ff_xml(where: dict, freqs, dtheta: float = 5.0, dphi: float = 5.0) -> str:
    """The nf2ff tool's input: every plane found by name, theta and phi in
    radians, phase centre in metres."""
    th = [math.radians(i * dtheta) for i in range(int(180 / dtheta) + 1)]
    ph = [math.radians(i * dphi) for i in range(int(360 / dphi) + 1)]
    planes = "".join(f'  <Planes E_Field="nf2ff_E_{f}.h5" H_Field="nf2ff_H_{f}.h5"/>\n' for f in FACES)
    center = ",".join(repr(c) for c in where["nf2ff_center"])
    fs = ",".join(repr(float(f)) for f in freqs)
    return (f'<nf2ff Outfile="nf2ff.h5" freq="{fs}" Center="{center}" Radius="1">\n{planes}'
            f"  <theta>{','.join(repr(t) for t in th)}</theta>\n"
            f"  <phi>{','.join(repr(p) for p in ph)}</phi>\n</nf2ff>\n")


def read_nf2ff(path: Path) -> dict:
    """{freq, theta, phi (rad), Dmax, Prad, p_rad [f][theta, phi]}. Both
    h5 layouts of the tool (openEMS nf2ff.py)."""
    import h5py
    import numpy as np
    with h5py.File(path, "r") as h:
        d = h["nf2ff"]
        out = {"theta": np.array(h["Mesh/theta"]), "phi": np.array(h["Mesh/phi"]),
               "freq": np.array(d.attrs["Frequency"], dtype=float),
               "Dmax": np.array(d.attrs["Dmax"], dtype=float),
               "Prad": np.array(d.attrs["Prad"], dtype=float)}
        grp = h["/nf2ff/P_rad/FD"]
        ps = []
        for n in range(out["freq"].size):
            a = np.array(grp[f"f{n}"])
            if a.shape != (out["theta"].size, out["phi"].size):
                a = np.swapaxes(a, 0, 1)
            ps.append(a)
        out["p_rad"] = ps
    return out


def s11_features(freqs, z, band=None) -> dict:
    """Resonance (deepest |S11| in the band, else anywhere), its -10 dB span,
    Z there, and where X crosses zero going up."""
    gam = [(v - ZREF) / (v + ZREF) for v in z]
    db = [20 * math.log10(max(abs(g), 1e-9)) for g in gam]
    idx = range(len(freqs))
    if band:
        inb = [i for i in idx if band[0] <= freqs[i] <= band[1]]
        idx = inb or idx
    k = min(idx, key=lambda i: db[i])
    lo, hi = k, k
    while lo > 0 and db[lo - 1] < -10:
        lo -= 1
    while hi + 1 < len(db) and db[hi + 1] < -10:
        hi += 1
    x0 = []
    for a in range(len(freqs) - 1):
        if z[a].imag < 0 <= z[a + 1].imag:
            t = -z[a].imag / (z[a + 1].imag - z[a].imag)
            x0.append(round(freqs[a] + t * (freqs[a + 1] - freqs[a]), 1))
    deep = db[k] < -10
    res = {"f_res": round(freqs[k], 1), "s11_db": round(db[k], 2),
           "z_res": [round(z[k].real, 2), round(z[k].imag, 2)],
           "bw_10db": [round(freqs[lo], 1), round(freqs[hi], 1)] if deep else None,
           "bw_10db_hz": round(freqs[hi] - freqs[lo], 1) if deep else 0.0,
           "x_zero_up": x0}
    return res


def touchstone(freqs, z, comments=()) -> str:
    """Touchstone v1 one-port, Z referenced to 50 ohm."""
    lines = [f"! {c}" for c in comments] + ["# Hz S RI R 50"]
    for f, v in zip(freqs, z):
        g = (v - ZREF) / (v + ZREF)
        lines.append(f"{f:.6e} {g.real:.6e} {g.imag:.6e}")
    return "\n".join(lines) + "\n"


def pattern_rows(ff: dict, n: int, eff: float) -> list[dict]:
    """theta, phi in degrees and gain (dBi) at frequency index n."""
    import numpy as np
    d = ff["p_rad"][n] * 4 * math.pi / float(ff["Prad"][n])
    rows = []
    th, ph = np.degrees(ff["theta"]), np.degrees(ff["phi"])
    for i, t in enumerate(th):
        for j, p in enumerate(ph):
            rows.append({"theta": round(float(t), 3), "phi": round(float(p), 3),
                         "gain_dbi": round(10 * math.log10(max(float(d[i, j]) * eff, 1e-12)), 3)})
    return rows


def write_pattern_png(rows, path: Path, title: str) -> None:
    """Three polar cuts: phi 0 (xz), phi 90 (yz), theta 90 (xy)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    cuts = [("xz (phi 0/180)", lambda r: r["phi"] in (0.0, 180.0)),
            ("yz (phi 90/270)", lambda r: r["phi"] in (90.0, 270.0)),
            ("xy (theta 90)", lambda r: r["theta"] == 90.0)]
    top = max(r["gain_dbi"] for r in rows)
    fig, axs = plt.subplots(1, 3, subplot_kw={"projection": "polar"}, figsize=(11, 4))
    for ax, (name, want) in zip(axs, cuts):
        pts = []
        for r in rows:
            if not want(r):
                continue
            if name.startswith("xy"):
                ang = r["phi"]
            else:
                ang = r["theta"] if r["phi"] < 180 else 360 - r["theta"]
            pts.append((math.radians(ang), r["gain_dbi"]))
        pts.sort()
        a = np.array(pts)
        ax.plot(a[:, 0], np.maximum(a[:, 1], top - 40), color="#1f6fb2")
        ax.set_rlim(top - 40, top + 2)
        ax.set_title(name, fontsize=10)
    fig.suptitle(f"{title}: gain, peak {top:.1f} dBi")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ================================================================ run

def study(geo: dict, feed, band, *, f_points: int = 401, pattern=None, fine=None, air=None,
          boundary="PML_8", loss: Loss | None = None, keep=None, timeout: float = 3 * 3600,
          f0: float | None = None, fc: float | None = None, max_cells: int = 8_000_000,
          threads: int | None = None) -> dict:
    """geometry -> openEMS -> nf2ff -> Zin, S11, Dmax, gain, efficiency.
    band (f_lo, f_hi): where the answer is read. pattern: NF2FF frequencies
    (default five across the band)."""
    import tempfile
    import time
    from . import solvers
    f_lo, f_hi = band
    fm = (f_lo + f_hi) / 2
    f0 = f0 or fm
    fc = fc or min(f0 / 2, max(0.5 * f0, (f_hi - f_lo)))
    span_lo, span_hi = max(f0 - fc, f0 / 20), f0 + fc
    pat = list(pattern) if pattern is not None else [f_lo + (f_hi - f_lo) * i / 4 for i in range(5)]
    freqs = [span_lo + (span_hi - span_lo) * i / (f_points - 1) for i in range(f_points)]
    csx, where = deck(geo, feed, f0, fc, pat, fine=fine, air=air, boundary=boundary, loss=loss,
                      max_cells=max_cells)
    tmp = tempfile.TemporaryDirectory(prefix="hwde-emantenna-") if not keep else None
    work = Path(keep) if keep else Path(tmp.name)
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    prev = None
    args = ["deck.xml"] + ([f"--numThreads={threads}"] if threads else [])
    while True:
        (work / "deck.xml").write_text(csx.xml())
        r = solvers.run("openems", args, cwd=work, timeout=timeout)
        if not (work / "ut").exists():
            raise solvers.SolverError(f"openEMS wrote no probes: {(r.stdout + r.stderr)[-400:]}")
        tail = record_tail(work)
        steps = csx.fdtd["NumberOfTimesteps"]
        if not math.isfinite(tail) or (prev is not None and tail > 2 * prev and tail > 0.01):
            raise Diverged(f"{boundary}: the record's tail went {prev} -> {tail} at {steps} steps")
        if tail <= TAIL_MAX or steps >= STEPS_MAX:
            break
        prev = tail
        csx.fdtd["NumberOfTimesteps"] = min(STEPS_MAX, _steps_needed(work, steps))
    (work / "nf2ff_in.xml").write_text(nf2ff_xml(where, pat))
    r = solvers.run("nf2ff", ["nf2ff_in.xml"], cwd=work, timeout=1800)
    if not (work / "nf2ff.h5").exists():
        raise solvers.SolverError(f"nf2ff wrote nothing: {(r.stdout + r.stderr)[-400:]}")
    ff = read_nf2ff(work / "nf2ff.h5")
    u, i_ = feed_vi(work, freqs)
    z = [a / b for a, b in zip(u, i_)]
    uf, of = feed_vi(work, pat)
    rows = []
    for n, f in enumerate(pat):
        pacc = 0.5 * (uf[n] * of[n].conjugate()).real
        g = (uf[n] / of[n] - ZREF) / (uf[n] / of[n] + ZREF)
        eff = float(ff["Prad"][n]) / pacc if pacc > 0 else float("nan")
        d = float(ff["Dmax"][n])
        rows.append({"f": f, "dmax_dbi": round(10 * math.log10(d), 3), "prad": float(ff["Prad"][n]),
                     "pacc": pacc, "efficiency": round(eff, 4),
                     "gain_dbi": round(10 * math.log10(max(d * eff, 1e-12)), 3),
                     "realized_gain_dbi": round(10 * math.log10(max(d * eff * (1 - abs(g) ** 2), 1e-12)), 3)})
    feat = s11_features(freqs, z)
    inb = [abs((v - ZREF) / (v + ZREF)) for f, v in zip(freqs, z) if f_lo <= f <= f_hi]
    if inb:
        feat["band_worst_s11_db"] = round(20 * math.log10(max(max(inb), 1e-9)), 2)
    res = {"freqs": freqs, "z": z, "features": feat, "far": rows, "where": where,
           "steps": csx.fdtd["NumberOfTimesteps"], "tail": tail, "rung_down": tail <= TAIL_MAX,
           "seconds": round(time.time() - t0, 1), "_ff": ff, "band": list(band)}
    if tmp:
        tmp.cleanup()
    return res


def at_band(res: dict) -> dict:
    """The NF2FF row nearest the resonance when that is in the band, else
    nearest the band's centre."""
    f = res["features"]["f_res"]
    lo, hi = res["band"]
    want = f if lo <= f <= hi else (lo + hi) / 2
    return min(res["far"], key=lambda r: abs(r["f"] - want))


# What a refinement may move and still count as converged.
CONV_F_PCT = 1.0        # resonance, %, or a quarter of the -10 dB width if wider
CONV_D_DB = 0.5         # directivity
CONV_ETA = 0.05         # efficiency


def converge(geo: dict, feed, band, *, fine=None, ratio: float = 1.5, keep=None, **kw) -> dict:
    """study() at `fine` and at fine/ratio. The finer run is the answer and
    `convergence` says what moved. The first run's fine defaults by the rule
    in default_fine, on f_max of the same pulse."""
    feed = feed if isinstance(feed, Feed) else Feed.parse(feed)
    lo, hi = band
    f0 = kw.get("f0") or (lo + hi) / 2
    fc = kw.get("fc") or min(f0 / 2, max(0.5 * f0, hi - lo))
    fine = fine or default_fine(geo, feed, f0 + fc)
    k1 = Path(keep) / "coarse" if keep else None
    k2 = Path(keep) / "fine" if keep else None
    a = study(geo, feed, band, fine=fine, keep=k1, **kw)
    b = study(geo, feed, band, fine=round(fine / ratio, 5), keep=k2, **kw)
    fa, fb = a["features"], b["features"]
    rb = at_band(b)
    ra = min(a["far"], key=lambda r: abs(r["f"] - rb["f"]))
    width = fb.get("bw_10db_hz") or 0.0
    df = abs(fa["f_res"] - fb["f_res"])
    limit = max(CONV_F_PCT / 100 * fb["f_res"], width / 4)
    moved = {"f_res_hz": round(df, 1), "f_res_pct": round(100 * df / fb["f_res"], 3),
             "dmax_db": round(abs(ra["dmax_dbi"] - rb["dmax_dbi"]), 3),
             "efficiency": round(abs(ra["efficiency"] - rb["efficiency"]), 4)}
    ok = df <= limit and moved["dmax_db"] <= CONV_D_DB and moved["efficiency"] <= CONV_ETA
    b["convergence"] = {"converged": bool(ok and a["rung_down"] and b["rung_down"]), "moved": moved,
                        "limits": {"f_res_hz": round(limit, 1), "dmax_db": CONV_D_DB, "efficiency": CONV_ETA},
                        "runs": [{"fine": a["where"]["fine"], "cells": a["where"]["cells"],
                                  "f_res": fa["f_res"], "s11_db": fa["s11_db"], "dmax_dbi": ra["dmax_dbi"],
                                  "efficiency": ra["efficiency"], "seconds": a["seconds"]},
                                 {"fine": b["where"]["fine"], "cells": b["where"]["cells"],
                                  "f_res": fb["f_res"], "s11_db": fb["s11_db"], "dmax_dbi": rb["dmax_dbi"],
                                  "efficiency": rb["efficiency"], "seconds": b["seconds"]}]}
    return b


def findings(res: dict, band_hz=None, min_efficiency=None) -> list[dict]:
    """What a solve says against a band and an efficiency floor (#481): off
    the band by more than its own -10 dB width, under the floor, not rung
    down, or (when a convergence study ran) not converged."""
    out = []
    ft = res["features"]
    far = at_band(res)
    if band_hz:
        lo, hi = band_hz
        off = max(lo - ft["f_res"], ft["f_res"] - hi, 0.0)
        if off > ft.get("bw_10db_hz", 0.0) or ft["s11_db"] > -10:
            out.append({"kind": "antenna_detuned", "message":
                        f"resonance {ft['f_res'] / 1e9:.3f} GHz (S11 {ft['s11_db']} dB, -10 dB width "
                        f"{ft.get('bw_10db_hz', 0) / 1e6:.0f} MHz) against {lo / 1e9:.3f}-{hi / 1e9:.3f} GHz"})
    if min_efficiency is not None and far["efficiency"] < min_efficiency:
        out.append({"kind": "antenna_low_efficiency", "message":
                    f"radiation efficiency {far['efficiency']:.2f} at {far['f'] / 1e9:.3f} GHz, "
                    f"floor {min_efficiency:g}"})
    if not res.get("rung_down"):
        out.append({"kind": "antenna_not_rung_down", "message":
                    f"the record ended at tail {res['tail']:.2g}: S11 is not to be read"})
    conv = res.get("convergence")
    if conv and not conv["converged"]:
        out.append({"kind": "antenna_not_converged", "message": f"refining the mesh moved {conv['moved']}"})
    return out


def report(res: dict) -> dict:
    """The JSON-able result."""
    r = {k: v for k, v in res.items() if not k.startswith("_") and k not in ("freqs", "z")}
    r["z_in"] = [[round(f, 1), round(v.real, 3), round(v.imag, 3)] for f, v in zip(res["freqs"], res["z"])]
    r["at_band"] = at_band(res)
    return r


def write_outputs(res: dict, out: Path, title: str = "antenna") -> dict:
    """<out>/antenna.s1p, pattern.csv (gain at the band's row), pattern.png,
    result.json."""
    import json
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "antenna.s1p").write_text(touchstone(res["freqs"], res["z"], [title, "hwde sim_solve antenna, openEMS"]))
    far = at_band(res)
    n = res["far"].index(far)
    rows = pattern_rows(res["_ff"], n, max(far["efficiency"], 1e-6))
    with (out / "pattern.csv").open("w") as fh:
        fh.write("theta_deg,phi_deg,gain_dbi\n")
        for row in rows:
            fh.write(f"{row['theta']},{row['phi']},{row['gain_dbi']}\n")
    write_pattern_png(rows, out / "pattern.png", f"{title} at {far['f'] / 1e9:.3f} GHz")
    rep = report(res)
    (out / "result.json").write_text(json.dumps(rep, indent=1) + "\n")
    return rep


# ================================================================ Palace cross-check

def patch_quarter_geo(W, L, h, G, A, lc_fine, lc_coarse) -> str:
    """A gmsh .geo for a quarter of a rectangular patch on a G x G board: the
    corner at the patch's centre, L along x, W along y. TM010's E_z is odd in x
    and even in y, so x = 0 is a PEC wall and y = 0 a magnetic wall (Palace's
    natural boundary): TM010 is the lowest patch mode this model has, and the
    orthogonal TM001, which sits below it on a W > L patch, is gone.
    Volume 1 dielectric, 2 air; surface 3 PEC, 4 absorbing."""
    X, Y, zt = G / 2 + A, G / 2 + A, h + A
    e = 1e-4
    return f"""SetFactory("OpenCASCADE");
Box(1) = {{0, 0, -{A}, {X}, {Y}, {h + 2 * A}}};
Box(2) = {{0, 0, 0, {G / 2}, {G / 2}, {h}}};
Rectangle(100) = {{0, 0, {h}, {L / 2}, {W / 2}}};
out[] = BooleanFragments{{ Volume{{1}}; Delete; }}{{ Volume{{2}}; Surface{{100}}; Delete; }};
sub[] = Volume In BoundingBox{{{-e}, {-e}, {-e}, {G / 2 + e}, {G / 2 + e}, {h + e}}};
all[] = Volume "*";
air[] = all[];
air[] -= sub[];
Physical Volume("dielectric", 1) = {{sub[]}};
Physical Volume("air", 2) = {{air[]}};
patch[] = Surface In BoundingBox{{{-e}, {-e}, {h - e}, {L / 2 + e}, {W / 2 + e}, {h + e}}};
gnd[] = Surface In BoundingBox{{{-e}, {-e}, {-e}, {G / 2 + e}, {G / 2 + e}, {e}}};
wall[] = Surface In BoundingBox{{{-e}, {-e}, {-A - e}, {e}, {Y + e}, {zt + e}}};
Physical Surface("pec", 3) = {{patch[], gnd[], wall[]}};
xo[] = Surface In BoundingBox{{{X - e}, {-e}, {-A - e}, {X + e}, {Y + e}, {zt + e}}};
yo[] = Surface In BoundingBox{{{-e}, {Y - e}, {-A - e}, {X + e}, {Y + e}, {zt + e}}};
zo[] = Surface In BoundingBox{{{-e}, {-e}, {-A - e}, {X + e}, {Y + e}, {-A + e}}};
zp[] = Surface In BoundingBox{{{-e}, {-e}, {zt - e}, {X + e}, {Y + e}, {zt + e}}};
Physical Surface("abc", 4) = {{xo[], yo[], zo[], zp[]}};
Field[1] = Box;
Field[1].VIn = {lc_fine};
Field[1].VOut = {lc_coarse};
Field[1].XMin = 0;
Field[1].XMax = {L / 2 + 2};
Field[1].YMin = 0;
Field[1].YMax = {W / 2 + 2};
Field[1].ZMin = -0.5;
Field[1].ZMax = {h + 2};
Field[1].Thickness = {A / 2};
Background Field = 1;
Mesh.MeshSizeFromPoints = 0;
Mesh.MeshSizeExtendFromBoundary = 0;
Mesh.MeshSizeFromCurvature = 0;
Mesh.MshFileVersion = 2.2;
"""


def patch_tm010(modes, lo=1.9, hi=2.8) -> tuple[float, float]:
    """(GHz, Q) of the highest-Q mode in lo..hi GHz."""
    return max((m for m in modes if lo <= m[0] <= hi), key=lambda m: m[1])


def palace_patch(work, W=38.0, L=29.5, h=1.6, er=4.4, G=70.0, A=40.0, *, lc=1.6, order=2,
                 target_ghz=2.1, n=3, timeout=3000.0) -> dict:
    """Palace eigenmode on the quarter patch: {modes: [(GHz, Q)], tets, seconds}.
    TM010 is the mode with the highest Q near the patch's cavity estimate
    (`patch_tm010`): the absorbing wall adds box modes of Q about 1."""
    import csv
    import json as _json
    import time
    from . import solvers
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    (work / "q.geo").write_text(patch_quarter_geo(W, L, h, G, A, lc, 3 * lc + 6))
    r = solvers.run("gmsh", ["q.geo", "-3", "-o", "q.msh"], cwd=work, timeout=1200)
    if not (work / "q.msh").exists():
        raise solvers.SolverError(f"gmsh: {(r.stdout + r.stderr)[-400:]}")
    tets = 0
    m = __import__("re").search(r"(\d+) tetrahedra", r.stdout + r.stderr)
    if m:
        tets = int(m.group(1))
    cfg = {"Problem": {"Type": "Eigenmode", "Verbose": 1, "Output": "pp"},
           "Model": {"Mesh": "q.msh", "L0": 1e-3},
           "Domains": {"Materials": [{"Attributes": [1], "Permittivity": er},
                                     {"Attributes": [2], "Permittivity": 1.0}]},
           "Boundaries": {"PEC": {"Attributes": [3]}, "Absorbing": {"Attributes": [4], "Order": 1}},
           "Solver": {"Order": order,
                      "Eigenmode": {"Target": target_ghz, "N": n, "Save": 0, "Tol": 1e-6},
                      "Linear": {"Type": "Default", "KSPType": "GMRES", "Tol": 1e-8, "MaxIts": 400}}}
    (work / "cfg.json").write_text(_json.dumps(cfg, indent=1))
    t0 = time.time()
    r = solvers.run("palace", ["cfg.json"], cwd=work, timeout=timeout)
    eig = work / "pp" / "eig.csv"
    if not eig.exists():
        raise solvers.SolverError(f"Palace wrote no eig.csv: {(r.stdout + r.stderr)[-500:]}")
    rows = list(csv.reader(eig.open()))
    head = [c.strip() for c in rows[0]]
    fi = next(i for i, c in enumerate(head) if c.startswith("Re{f}"))
    qi = next(i for i, c in enumerate(head) if c == "Q")
    modes = [(float(x[fi]), float(x[qi])) for x in rows[1:]]
    return {"modes": modes, "tets": tets, "seconds": round(time.time() - t0, 1), "order": order, "lc": lc}

