"""emboard.py - a board's copper as an openEMS N-port deck (#455).

`extract` cuts a region of a .kicad_pcb into plain data (JSON-able):
every copper layer of the board's stackup with its z, each layer's copper
per net (tracks, pads as drawn, vias, zone fill), the drilled barrels
(vias and plated pads) and, when asked, 2-pad parts as lumped resistors.
`deck` turns that and a list of ports into a CSXCAD/openEMS deck (lib/
emsolve.Csx) and the probe map its readers need; `waves` and `sparams`
read a finished run; `study` does extract -> deck -> openEMS -> S.

The model:
  - every copper layer is a zero-thickness PEC sheet at its own z (the
    dielectric heights from the board's stackup; bottom layer at z = 0).
    Every sheet plane is a pinned mesh line and the deck refuses to write
    when one is not (emsolve.Csx.sheets_off_mesh: CSXCAD drops such a
    sheet silently, which read a 50 ohm CPWG as 65 ohm on vna-verify);
  - the dielectric is each gap's slab (er from the stackup), lossless: a
    conductivity is a loss tangent at one frequency only, which makes a
    frequency-flat loss (0.3 dB on 50 mm at 100 MHz on vna-verify). Add
    dielectric and conductor loss in the circuit model, not here;
  - barrels are PEC posts (square, the drill's area) between the layers
    they span;
  - a port sits on the region's edge (the region is cut at its plane): the
    copper of every layer that crosses the edge near it is carried straight
    out as a feed (`feed_len`, half a wavelength), the coplanar ground on
    the port's layer is stitched to the reference layer along the feed,
    and the feed ends in a 50 ohm lumped port (the source on the excited
    one). Three voltage probes and two current probes on the feed give its
    own Z and gamma exactly (the telegrapher relations emsolve's
    microstrip_line uses), so V and I are moved to the port's plane and
    the waves are referenced to 50 ohm there, whatever the feed's own
    impedance: a line drawn at the wrong width reads as mismatched;
  - a derivative-of-Gaussian pulse (no DC; emsolve.Csx.dgauss) and MUR
    absorbing walls (PML_8 grew without bound on vna-verify's deck); the
    floor is a PEC wall at the bottom layer unless a port is on it.

Building blocks for 3D studies (#479 via/launch, #481 antenna): `Stack`,
`extract`, `mesh_axis` and `deck(..., extra=fn)`, where fn(csx, ctx) adds
primitives and mesh lines before the mesh is smoothed.

Closed forms for the known answers: `line_s` (S of a uniform line from its
Z0 and eps_eff), `line_from_s` (Z0 and eps_eff back from a measured 2-port),
`open_end_dl` (Hammerstad-Bekkadal open-end extension), `notch_f`.

Lengths mm, frequencies Hz.
"""
from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import emsolve as em

C0 = em.C0
ZREF = 50.0
_DIRS = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}


# ================================================================ stack

@dataclass
class Stack:
    """Copper layers top to bottom, their z (bottom layer at 0) and the
    dielectric slab under each but the last: [(z0, z1, er)]."""
    layers: list[str]
    z: dict[str, float]
    slabs: list[tuple[float, float, float]]

    @classmethod
    def from_stackup(cls, st) -> "Stack":
        layers = list(st.copper_layers)
        hs = [d["height"] for d in st.dielectrics]
        z, acc = {}, 0.0
        for i in range(len(layers) - 1, -1, -1):
            z[layers[i]] = round(acc, 6)
            if i > 0:
                acc += hs[i - 1]
        slabs = [(z[layers[i + 1]], z[layers[i]], float(st.dielectrics[i]["epsilon_r"]))
                 for i in range(len(layers) - 1)]
        return cls(layers, z, slabs)

    @classmethod
    def from_dict(cls, d: dict) -> "Stack":
        return cls(list(d["layers"]), dict(d["z"]), [tuple(s) for s in d["slabs"]])

    def as_dict(self) -> dict:
        return {"layers": self.layers, "z": self.z, "slabs": [list(s) for s in self.slabs]}

    def er_max(self) -> float:
        return max([s[2] for s in self.slabs] or [1.0])

    def gap(self, a: str, b: str) -> float:
        return abs(self.z[a] - self.z[b])

    def er_between(self, a: str, b: str) -> float:
        lo, hi = sorted((self.z[a], self.z[b]))
        ss = [s for s in self.slabs if s[0] >= lo - 1e-9 and s[1] <= hi + 1e-9]
        h = sum(s[1] - s[0] for s in ss)
        return sum((s[1] - s[0]) * s[2] for s in ss) / h if h else 1.0


# ================================================================ extract

def no_holes(poly) -> list:
    """A shapely polygon as hole-free pieces (CSXCAD's Polygon has none):
    cut along x at each hole's centre, recursively."""
    from shapely.geometry import LineString
    from shapely.ops import split
    if not poly.interiors:
        return [poly]
    cx = poly.interiors[0].centroid.x
    x0, y0, x1, y1 = poly.bounds
    out = []
    for piece in split(poly, LineString([(cx, y0 - 1), (cx, y1 + 1)])).geoms:
        out += no_holes(piece)
    return out


def _polys(geom, simplify=0.005) -> list[list[list[float]]]:
    out = []
    for g in (geom.geoms if hasattr(geom, "geoms") else [geom]):
        if g.geom_type != "Polygon" or g.area < 1e-4:
            continue
        for piece in no_holes(g.simplify(simplify)):
            out.append([[round(x, 4), round(y, 4)] for x, y in list(piece.exterior.coords)[:-1]])
    return out


def lumped_from_part(bg, ref: str, r: float) -> dict:
    """A 2-pad part (0402 resistor ...) as a lumped resistor between its
    pads' inner edges, as wide as the narrower pad, on the pads' layer."""
    pads = [p for p in bg.pads_of(ref=ref) if p.kind != "np_thru_hole"]
    if len(pads) != 2:
        raise ValueError(f"{ref}: a lumped part needs exactly 2 pads, has {len(pads)}")
    a, b = pads
    (ax, ay), (bx, by) = a.copper_center, b.copper_center
    axis = 0 if abs(bx - ax) >= abs(by - ay) else 1
    if min(abs(bx - ax), abs(by - ay)) > 1e-3:
        raise ValueError(f"{ref}: pads are not on one axis (rotate it to 0/90)")
    ba, bb = a.poly.bounds, b.poly.bounds
    if (a.copper_center[axis] > b.copper_center[axis]):
        ba, bb = bb, ba
    lo, hi = ba[2 + axis], bb[axis]
    o = 1 - axis
    w = min(ba[2 + o] - ba[o], bb[2 + o] - bb[o])
    c = (a.copper_center[o] + b.copper_center[o]) / 2
    layer = next(l for l in a.layers if l.endswith(".Cu"))
    return {"ref": ref, "axis": "xy"[axis], "lo": round(lo, 4), "hi": round(hi, 4),
            "c": round(c, 4), "w": round(w, 4), "layer": layer, "r": float(r)}


def extract(pcb, region, parts: dict | None = None, layers: list[str] | None = None) -> dict:
    """The copper of every layer (or `layers`) of a board inside region
    (x0, y0, x1, y1), per net, plus barrels, as plain data. parts:
    {ref: ohms} turns 2-pad footprints into lumped resistors."""
    from shapely.geometry import box as sbox
    from shapely.ops import unary_union
    from . import geom as G
    bg = G.load_board(pcb)
    stack = Stack.from_stackup(bg.stackup)
    want = layers or stack.layers
    clip = sbox(*region)
    copper = []
    for layer in want:
        per_net: dict[str, list] = {}
        for t in bg.tracks_of(layer=layer):
            per_net.setdefault(t.net or "", []).append(t.poly)
        for p in bg.pads_of(layer=layer):
            if p.kind != "np_thru_hole":
                per_net.setdefault(p.net or f"~{p.ref}.{p.number}", []).append(p.poly)
        for v in bg.vias_of(layer=layer):
            per_net.setdefault(v.net or "", []).append(v.poly)
        for z in bg.zones_of(layer=layer):
            per_net.setdefault(z.net or "", []).append(z.fill_on(layer))
        for net, items in sorted(per_net.items()):
            cu = unary_union([g for g in items if g is not None and not g.is_empty])
            cut = cu.intersection(clip)
            for pts in _polys(cut):
                copper.append({"layer": layer, "net": net, "pts": pts})
    drills = []
    x0, y0, x1, y1 = region
    inside = lambda x, y: x0 < x < x1 and y0 < y < y1
    for v in bg.vias_of():
        span = [l for l in v.layers if l in want]
        if len(span) >= 2 and inside(*v.at):
            drills.append({"x": v.at[0], "y": v.at[1], "d": v.drill, "net": v.net or "",
                           "top": span[0], "bot": span[-1]})
    for p in bg.pads_of():
        if p.drill and p.kind == "thru_hole" and inside(*p.center):
            span = [l for l in stack.layers if l in want and (p.on(l) or "*.Cu" in p.layers)]
            if len(span) >= 2:
                drills.append({"x": p.center[0], "y": p.center[1], "d": min(p.drill),
                               "net": p.net or "", "top": span[0], "bot": span[-1]})
    lumped = [lumped_from_part(bg, ref, r) for ref, r in (parts or {}).items()]
    return {"source": str(pcb), "region": list(region), "stack": stack.as_dict(),
            "copper": copper, "drills": drills, "lumped": lumped}


# ================================================================ mesh

def merge_lines(vals, min_step: float, pinned=()) -> list[float]:
    """Sorted lines at least min_step apart; a pinned line never moves and
    wins over an unpinned neighbour."""
    pin = sorted({round(float(v), 6) for v in pinned})
    allv = sorted({round(float(v), 6) for v in vals} | set(pin))
    pset = set(pin)
    out: list[float] = []
    for v in allv:
        if out and v - out[-1] < min_step:
            if v in pset and out[-1] not in pset:
                out[-1] = v
            continue
        out.append(v)
    return out


def _fill_gap(a, b, sl, sr, max_step, ratio):
    """Lines strictly inside (a, b): cells growing by `ratio` from the
    neighbour cells sl (left) and sr (right) up to max_step."""
    L, R, inner_l, inner_r = a, b, [], []
    for _ in range(100000):
        g = R - L
        if g <= min(max_step, ratio * sl, ratio * sr) * 1.1:     # slack: no sliver cells
            break
        nl, nr = min(max_step, ratio * sl), min(max_step, ratio * sr)
        if g < nl + nr:                      # two more would overshoot: split the rest
            k = max(2, math.ceil(g / max(nl, nr)))
            inner_l += [L + g * j / k for j in range(1, k)]
            break
        if sl <= sr:
            L += nl
            sl = nl
            inner_l.append(L)
        else:
            R -= nr
            sr = nr
            inner_r.append(R)
    return inner_l + inner_r[::-1]


def smooth_lines(lines, max_step: float, ratio: float = 1.4) -> list[float]:
    """Insert lines until no cell exceeds max_step nor grows faster than
    `ratio` from its neighbours (CSXCAD's SmoothMeshLines in spirit)."""
    ls = sorted(lines)
    if len(ls) < 2:
        return ls
    d = [b - a for a, b in zip(ls[:-1], ls[1:])]
    out = [ls[0]]
    for i, g in enumerate(d):
        sl = min([d[i - 1]] if i > 0 else [g]) if i > 0 else max_step
        sr = d[i + 1] if i + 1 < len(d) else max_step
        sl, sr = min(sl, g), min(sr, g)
        out += _fill_gap(ls[i], ls[i + 1], sl, sr, max_step, ratio)
        out.append(ls[i + 1])
    return [round(v, 6) for v in out]


def mesh_axis(fixed, fine_zones, lo, hi, fine, coarse, pinned=()) -> list[float]:
    """One axis: the fixed lines, uniform `fine` lines inside each fine
    zone (a, b), the domain ends, merged at fine/3 (pinned lines kept) and
    smoothed out to `coarse`."""
    vals = list(fixed) + [lo, hi]
    for a, b in fine_zones:
        a, b = max(a, lo), min(b, hi)
        n = max(1, math.ceil((b - a) / fine))
        vals += [a + (b - a) * i / n for i in range(n + 1)]
    vals = [v for v in vals if lo - 1e-9 <= v <= hi + 1e-9]
    ls = merge_lines(vals, fine / 3, [p for p in pinned if lo - 1e-9 <= p <= hi + 1e-9])
    return smooth_lines(ls, coarse)


def _zones(vals, half) -> list[tuple[float, float]]:
    out = []
    for v in sorted(vals):
        a, b = v - half, v + half
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# ================================================================ ports

@dataclass
class Port:
    """A port on the region's edge: its plane through (x, y), the line
    leaving the circuit toward dir on `layer`. w (the line's width) and
    ref (the reference layer) are found from the copper when not given."""
    x: float
    y: float
    dir: str
    layer: str = "F.Cu"
    w: float | None = None
    ref: str | None = None
    net: str | None = None
    lo: float = 0.0          # filled: the strip's edges across the port
    hi: float = 0.0

    @classmethod
    def parse(cls, s: str) -> "Port":
        """X,Y,DIR[,LAYER[,W]]"""
        f = s.split(",")
        if len(f) < 3 or f[2] not in _DIRS:
            raise ValueError(f"port {s!r}: X,Y,DIR[,LAYER[,W]] with DIR +x|-x|+y|-y")
        return cls(float(f[0]), float(f[1]), f[2], f[3] if len(f) > 3 and f[3] else "F.Cu",
                   float(f[4]) if len(f) > 4 and f[4] else None)

    @property
    def axis(self) -> int:
        return 0 if self.dir[1] == "x" else 1

    @property
    def sign(self) -> int:
        return 1 if self.dir[0] == "+" else -1

    @property
    def plane(self) -> float:
        return self.x if self.axis == 0 else self.y

    @property
    def across(self) -> float:
        return self.y if self.axis == 0 else self.x


def _cut_region(region, ports):
    x0, y0, x1, y1 = region
    for p in ports:
        if p.dir == "-x":
            x0 = p.x
        elif p.dir == "+x":
            x1 = p.x
        elif p.dir == "-y":
            y0 = p.y
        else:
            y1 = p.y
    if not (x0 < x1 and y0 < y1):
        raise ValueError(f"ports cut the region {region} to nothing")
    return (x0, y0, x1, y1)


def _edge_intervals(geom, port: Port, window: float) -> list[tuple[float, float]]:
    """Where `geom` crosses the port's plane, within +-window of the port,
    as intervals along the plane (from a strip a hair inside the edge)."""
    from shapely.geometry import box as sbox
    e, s, c = port.plane, port.sign, port.across
    a, b = sorted((e, e - s * 0.002))
    strip = sbox(a, c - window, b, c + window) if port.axis == 0 else sbox(c - window, a, c + window, b)
    cut = geom.intersection(strip)
    out = []
    for g in (cut.geoms if hasattr(cut, "geoms") else [cut]):
        if g.is_empty or g.area <= 0:
            continue
        bx = g.bounds
        out.append((bx[1], bx[3]) if port.axis == 0 else (bx[0], bx[2]))
    out.sort()
    merged = []
    for lo, hi in out:
        if merged and lo <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _thirds(g, fine, region):
    """Mesh lines for a signal conductor's edges by openEMS's thirds rule:
    no line on an axis-aligned edge, one fine/3 inside it and one 2 fine/3
    outside (a line on the edge overstates a sheet's width: a 50 ohm line
    read 48 ohm). Edges on the region's boundary (the port planes) are not
    edges. Returns (x lines, y lines, vertices of the other edges)."""
    from shapely.geometry import Point
    ex, ey, other = [], [], []
    rx, ry = (region[0], region[2]), (region[1], region[3])
    rings = []
    for part in (g.geoms if hasattr(g, "geoms") else [g]):
        if hasattr(part, "exterior"):
            rings += [list(part.exterior.coords)] + [list(h.coords) for h in part.interiors]
    d = min(fine / 10, 1e-3)
    for ring in rings:
        for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]):
            if abs(x1 - x0) < 1e-6 and abs(y1 - y0) > fine / 2:
                if any(abs(x0 - r) < 1e-6 for r in rx):
                    continue
                s_in = 1 if g.contains(Point(x0 + d, (y0 + y1) / 2)) else -1
                ex += [x0 + s_in * fine / 3, x0 - s_in * 2 * fine / 3]
            elif abs(y1 - y0) < 1e-6 and abs(x1 - x0) > fine / 2:
                if any(abs(y0 - r) < 1e-6 for r in ry):
                    continue
                s_in = 1 if g.contains(Point((x0 + x1) / 2, y0 + d)) else -1
                ey += [y0 + s_in * fine / 3, y0 - s_in * 2 * fine / 3]
            elif abs(x1 - x0) > 1e-6 and abs(y1 - y0) > 1e-6:
                other += [(x0, y0), (x1, y1)]
    return ex, ey, other


@dataclass
class _Ctx:
    """What `deck` knows while it builds; handed to an `extra` hook."""
    stack: Stack
    region: tuple
    ports: list
    fine: float
    coarse: float
    lam_min: float
    fixed: dict = field(default_factory=lambda: {"x": [], "y": [], "z": []})
    zones: dict = field(default_factory=lambda: {"x": [], "y": []})
    pinned: dict = field(default_factory=lambda: {"x": [], "y": [], "z": []})


def deck(geo: dict, ports, f_max: float, excite: int = 0, *, fine: float | None = None,
         feed_len: float | None = None, pad: float | None = None, floor: str | None = None,
         thirds: bool = True, zcells: int = 6, extra=None):
    """(Csx, where) for the region of `geo` (from `extract`) with `ports`
    (Port or "X,Y,DIR[,LAYER[,W]]"), the source on port `excite`."""
    from shapely.geometry import Polygon, box as sbox
    from shapely.ops import unary_union
    ports = [p if isinstance(p, Port) else Port.parse(p) for p in ports]
    ports = [Port(**{k: getattr(p, k) for k in ("x", "y", "dir", "layer", "w", "ref")}) for p in ports]
    stack = Stack.from_dict(geo["stack"])
    region = _cut_region(geo["region"], ports)
    clip = sbox(*region)
    lam_min = C0 / f_max * 1e3 / math.sqrt(stack.er_max())
    # copper per layer per net, cut at the port planes
    cu: dict[str, dict[str, object]] = {}
    for item in geo["copper"]:
        if item["layer"] not in stack.z:
            continue
        g = Polygon(item["pts"]).buffer(0).intersection(clip)
        if g.is_empty:
            continue
        d = cu.setdefault(item["layer"], {})
        d[item["net"]] = unary_union([d[item["net"]], g]) if item["net"] in d else g
    layer_all = {l: unary_union(list(d.values())) for l, d in cu.items()}

    # each port's net, width, reference layer
    for k, p in enumerate(ports):
        if p.layer not in cu:
            raise ValueError(f"port {k}: no copper on {p.layer} in the region")
        found = None
        for net, g in cu[p.layer].items():
            for lo, hi in _edge_intervals(g, p, 50.0):
                if lo - 1e-6 <= p.across <= hi + 1e-6:
                    found = (net, lo, hi)
        if found is None:
            raise ValueError(f"port {k}: no copper on {p.layer} crosses the region edge at "
                             f"({p.x}, {p.y})")
        p.net = found[0]
        if p.w is None:
            p.lo, p.hi = found[1], found[2]
            p.w = round(p.hi - p.lo, 6)
        else:
            p.lo, p.hi = p.across - p.w / 2, p.across + p.w / 2
        if p.ref is None:
            inner = p.plane - p.sign * 0.01
            pt = (inner, p.across) if p.axis == 0 else (p.across, inner)
            from shapely.geometry import Point
            cands = [l for l in stack.layers if l != p.layer and l in layer_all
                     and layer_all[l].buffer(1e-6).contains(Point(pt))]
            if not cands:
                raise ValueError(f"port {k}: no reference copper under ({p.x}, {p.y}) on any layer")
            p.ref = min(cands, key=lambda l: stack.gap(l, p.layer))
    h_sig = min(stack.gap(p.layer, p.ref) for p in ports)
    w_min = min(p.w for p in ports)
    fine = fine or min(w_min / 5, h_sig / 2.5, lam_min / 40)
    coarse = min(lam_min / 20, 3.0)
    feed_len = feed_len or max(lam_min / 2, 12 * h_sig)
    ctx = _Ctx(stack, region, ports, fine, coarse, lam_min)

    # feeds: every layer's copper crossing each port's edge, carried out
    feeds = []          # (layer, rect polygon) added copper
    stitch = []         # (x, y, top, bot) PEC posts along the feed's coplanar ground
    dfeed = min(coarse, lam_min / 30, feed_len / 24)
    for k, p in enumerate(ports):
        window = max(6 * p.w, 10 * stack.gap(p.layer, p.ref), 3.0)
        e, s = p.plane, p.sign
        a, b = sorted((e, e + s * feed_len))
        for layer, nets in cu.items():
            for net, g in nets.items():
                for lo, hi in _edge_intervals(g, p, window):
                    if layer == p.layer and net == p.net:
                        lo, hi = p.lo, p.hi
                    r = sbox(a, lo, b, hi) if p.axis == 0 else sbox(lo, a, hi, b)
                    feeds.append((layer, r))
                    if layer == p.layer and net != p.net and hi - lo > 0.2:
                        near = hi if hi <= p.lo else lo
                        cpos = (near - min(0.4, (hi - lo) / 2) if near == hi
                                else near + min(0.4, (hi - lo) / 2))
                        n = max(2, int(feed_len / max(lam_min / 10, 1.0)))
                        for j in range(n):
                            along = e + s * feed_len * (j + 0.5) / n
                            stitch.append(((along, cpos) if p.axis == 0 else (cpos, along),
                                           p.layer, p.ref, k))
                    if not (layer == p.layer and net == p.net):
                        ctx.fixed["xy"[1 - p.axis]] += [lo, hi]
        ctx.pinned["xy"[p.axis]] += [e + s * dfeed * j for j in range(0, int(feed_len / dfeed) + 1)]
        ctx.pinned["xy"[1 - p.axis]] += ([p.lo + fine / 3, p.lo - 2 * fine / 3,
                                          p.hi - fine / 3, p.hi + 2 * fine / 3]
                                         if thirds else [p.lo, p.hi])
        ctx.zones["xy"[1 - p.axis]] += _zones([p.lo, p.hi], 2.5 * fine)

    # mesh: copper edges near the signal nets, fine around them
    sig = unary_union([g for l, d in cu.items() for n, g in d.items()
                       if any(n == p.net and l == p.layer for p in ports)])
    near = sig.buffer(max(1.0, 4 * h_sig))
    for layer, nets in cu.items():
        for net, g in nets.items():
            is_sig = any(net == p.net and layer == p.layer for p in ports)
            gg = g if is_sig else g.boundary.intersection(near)
            if gg.is_empty:
                continue
            pts = []
            for part in (gg.geoms if hasattr(gg, "geoms") else [gg]):
                if hasattr(part, "exterior"):
                    pts += list(part.exterior.coords)
                    for hole in part.interiors:
                        pts += list(hole.coords)
                elif hasattr(part, "coords"):
                    pts += list(part.coords)
            ex, ey = [], []
            if is_sig and thirds:
                ex, ey, pts = _thirds(g, fine, region)
            ex += [q[0] for q in pts]
            ey += [q[1] for q in pts]
            ctx.fixed["x"] += ex
            ctx.fixed["y"] += ey
            if is_sig:
                ctx.zones["x"] += _zones(ex, 2 * fine)
                ctx.zones["y"] += _zones(ey, 2 * fine)
    for d in geo.get("drills", []):
        if d["top"] in stack.z and d["bot"] in stack.z:
            a = d["d"] * math.sqrt(math.pi) / 4
            ctx.fixed["x"] += [d["x"] - a, d["x"] + a] if 2 * a > fine else [d["x"]]
            ctx.fixed["y"] += [d["y"] - a, d["y"] + a] if 2 * a > fine else [d["y"]]
    for (xy, top, bot, k) in stitch:
        ctx.fixed["xy"[1 - ports[k].axis]].append(xy[1 - ports[k].axis])
    for lp in geo.get("lumped", []):
        ax = 0 if lp["axis"] == "x" else 1
        ctx.fixed["xy"[ax]] += [lp["lo"], lp["hi"]]
        ctx.fixed["xy"[1 - ax]] += [lp["c"] - lp["w"] / 2, lp["c"] + lp["w"] / 2]
        ctx.zones["xy"[ax]] += [(lp["lo"] - fine, lp["hi"] + fine)]

    # z: every copper plane pinned; cells per slab; air above (and below)
    port_layers = {p.layer for p in ports}
    floor = floor or ("open" if stack.layers[-1] in port_layers else "pec")
    zs = sorted(stack.z.values())
    zl = list(zs)
    for z0, z1, er in stack.slabs:
        h = z1 - z0
        touches = any(abs(stack.z[l] - z0) < 1e-9 or abs(stack.z[l] - z1) < 1e-9 for l in port_layers)
        n = zcells if touches else max(2, min(5, math.ceil(h / (2 * fine))))
        zl += [z0 + h * i / n for i in range(n + 1)]
    air = max(lam_min / 6, 10 * h_sig)
    fz = h_sig / zcells
    ztop = zs[-1] + air
    zl += em.graded(zs[-1], ztop, fz, coarse)
    zbot = zs[0]
    if floor == "open":
        zbot = zs[0] - air
        zl += [-v for v in em.graded(-zs[0], -zbot, fz, coarse)]
    ctx.pinned["z"] += zs

    pad = pad if pad is not None else max(lam_min / 8, 5 * (zs[-1] - zs[0]), 3.0)
    ext = list(region)
    for p in ports:
        i = p.axis + (2 if p.sign > 0 else 0)
        ext[i] = p.plane + p.sign * feed_len
    dom = (ext[0] - pad, ext[1] - pad, ext[2] + pad, ext[3] + pad)

    csx = em.Csx(1e-3)
    csx.dgauss(f_max)
    csx.bc = ["MUR", "MUR", "MUR", "MUR", "PEC" if floor == "pec" else "MUR", "MUR"]
    csx.fdtd["endCriteria"] = 1e-4
    for i, (z0, z1, er) in enumerate(stack.slabs):
        m = csx.material(f"slab{i}", er)
        csx.box(m, (dom[0], dom[1], z0), (dom[2], dom[3], z1))
    cuprop = csx.metal("copper")
    added: dict[str, list] = {}
    for layer, g in layer_all.items():
        added.setdefault(layer, []).append(g)
    for layer, r in feeds:
        added.setdefault(layer, []).append(r)
    for layer, gs in added.items():
        for pts in _polys(unary_union(gs), simplify=0.001):
            csx.polygon(cuprop, pts, stack.z[layer], prio=10)
    post = csx.metal("barrels") if (geo.get("drills") or stitch) else None
    for d in geo.get("drills", []):
        if d["top"] in stack.z and d["bot"] in stack.z:
            a = d["d"] * math.sqrt(math.pi) / 4
            za, zb = sorted((stack.z[d["top"]], stack.z[d["bot"]]))
            csx.box(post, (d["x"] - a, d["y"] - a, za), (d["x"] + a, d["y"] + a, zb), prio=20)
    for (xy, top, bot, k) in stitch:
        za, zb = sorted((stack.z[top], stack.z[bot]))
        csx.box(post, (xy[0] - 0.15, xy[1] - 0.15, za), (xy[0] + 0.15, xy[1] + 0.15, zb), prio=20)
    for lp in geo.get("lumped", []):
        ax = 0 if lp["axis"] == "x" else 1
        z = stack.z[lp["layer"]]
        p1, p2 = [0.0, 0.0, z], [0.0, 0.0, z]
        p1[ax], p2[ax] = lp["lo"], lp["hi"]
        p1[1 - ax], p2[1 - ax] = lp["c"] - lp["w"] / 2, lp["c"] + lp["w"] / 2
        r = csx.lumped(f"R_{lp['ref']}", ax, lp["r"])
        csx.box(r, p1, p2, prio=30)
    if extra:
        extra(csx, ctx)

    lines = {}
    for a, (lo, hi) in (("x", (dom[0], dom[2])), ("y", (dom[1], dom[3]))):
        lines[a] = mesh_axis(ctx.fixed[a] + list(region[0::2] if a == "x" else region[1::2]),
                             ctx.zones[a], lo, hi, fine, coarse, ctx.pinned[a])
    zfix = [v for v in zl + ctx.fixed["z"] if zbot - 1e-9 <= v <= ztop + 1e-9]
    lines["z"] = smooth_lines(merge_lines(zfix + [zbot, ztop], fz / 1.6, ctx.pinned["z"]), coarse)
    for a in "xyz":
        csx.add_lines(a, lines[a])

    # ports: a 50 ohm end one cell in from each feed's end, the probes
    where = {"ports": [], "excite": excite, "region": list(region), "fine": fine,
             "feed_len": feed_len, "floor": floor,
             "mesh": [len(lines["x"]), len(lines["y"]), len(lines["z"])],
             "cells": len(lines["x"]) * len(lines["y"]) * len(lines["z"])}
    for k, p in enumerate(ports):
        al = lines["xy"[p.axis]]
        tl = lines["xy"[1 - p.axis]]
        zsig, zref = stack.z[p.layer], stack.z[p.ref]
        e, s = p.plane, p.sign
        end = em._snap(al, e + s * (feed_len - dfeed))

        def pt(along, acr, z):
            return (along, acr, z) if p.axis == 0 else (acr, along, z)
        pa, pb = pt(end, p.lo, min(zsig, zref)), pt(end, p.hi, max(zsig, zref))
        if k == excite:
            v = [0, 0, -1 if zsig > zref else 1]
            csx.box(csx.excitation(f"src{k}", v), pa, pb, prio=5)
        csx.box(csx.lumped(f"port{k}", 2, ZREF), pa, pb, prio=5)
        # voltage probes on three feed lines `span` cells apart around the
        # feed's middle; current probes on the dual lines between them
        feed_ls = [q for q in al if (q - e) * s > 3 * dfeed and (e + s * (feed_len - 3 * dfeed) - q) * s > 0]
        feed_ls.sort(key=lambda q: (q - e) * s)
        mid = len(feed_ls) // 2
        span = max(1, min(len(feed_ls) // 6, mid - 1)) | 1
        u = [feed_ls[mid - span], feed_ls[mid], feed_ls[mid + span]]
        steps = [b - a for a, b in zip(sorted(al), sorted(al)[1:])
                 if min(u) - 1e-9 <= a and b <= max(u) + 1e-9]
        if max(steps) - min(steps) > 1e-5:
            raise ValueError(f"port {k}: feed mesh is not uniform where the probes sit: {u} {steps}")
        dist = [abs(q - e) for q in u]
        uz0, uz1 = sorted((zsig, zref))
        for j, q in enumerate(u):
            csx.probe(f"p{k}u{j}", 0, 1.0 if zsig > zref else -1.0)
            csx.box(f"p{k}u{j}", pt(q, p.across, uz0), pt(q, p.across, uz1))
        # the loop around the strip: half a cell outside each edge, half a
        # cell above and below the sheet
        ia = max(q for q in tl if q <= p.lo + 1e-9)
        ib = min(q for q in tl if q >= p.hi - 1e-9)
        lo_d = ia - (ia - max(q for q in tl if q < ia - 1e-9)) / 2
        hi_d = ib + (min(q for q in tl if q > ib + 1e-9) - ib) / 2
        zl_ = lines["z"]
        jz = min(range(len(zl_)), key=lambda i: abs(zl_[i] - zsig))
        z_lo = zl_[jz] - (zl_[jz] - zl_[jz - 1]) / 2
        z_hi = zl_[jz] + (zl_[jz + 1] - zl_[jz]) / 2
        ipos = [(u[0] + u[1]) / 2, (u[1] + u[2]) / 2]
        for j, q in enumerate(ipos):
            csx.probe(f"p{k}i{j}", 1)
            csx.box(f"p{k}i{j}", pt(q, lo_d, z_lo), pt(q, hi_d, z_hi))
        where["ports"].append({"x": p.x, "y": p.y, "dir": p.dir, "layer": p.layer, "ref": p.ref,
                               "net": p.net, "w": p.w, "dist": dist,
                               "h": round(stack.gap(p.layer, p.ref), 6),
                               "er": round(stack.er_between(p.layer, p.ref), 4)})
    return csx, where


# ================================================================ readers

def port_vi(sim: Path, where: dict, k: int, freqs) -> list[dict]:
    """V and I (into the circuit) at port k's plane, and the feed's own Z
    and gamma, per frequency. With s the distance out from the plane,
    V(s) = a e^(gamma s) + b e^(-gamma s) and I = (a e^(gamma s) - b
    e^(-gamma s)) / Z; Z from u1..u3 and i1, i2 as in emsolve's
    microstrip_z0_from_probes (exact on a uniform line), gamma from
    cosh(gamma d) = (u1 + u3) / (2 u2), (a, b) from the three voltages."""
    import numpy as np
    us = [em.dft(*em.read_probe(sim / f"p{k}u{j}"), freqs) for j in range(3)]
    ii = [em.dft(*em.read_probe(sim / f"p{k}i{j}"), freqs) for j in range(2)]
    s = [d * 1e-3 for d in where["ports"][k]["dist"]]
    d = (s[2] - s[0]) / 2
    out = []
    for n in range(len(freqs)):
        u1, u2, u3 = (us[j][n] for j in range(3))
        i1, i2 = ii[0][n], ii[1][n]
        z = cmath.sqrt(u2 * (u1 - u3) / (i1 * i1 - i2 * i2))
        if z.real < 0:
            z = -z
        g = cmath.acosh((u1 + u3) / (2 * u2)) / d
        if g.imag < 0:
            g = -g
        A = np.array([[cmath.exp(g * sj), cmath.exp(-g * sj)] for sj in s])
        (a, b), *_ = np.linalg.lstsq(A, np.array([u1, u2, u3]), rcond=None)
        out.append({"v": complex(a + b), "i": complex((a - b) / z), "z": z, "gamma": g})
    return out


def waves(sim: Path, where: dict, freqs, zref: float = ZREF) -> list[list[tuple[complex, complex]]]:
    """[(a, b) per port] per frequency: the 50 ohm waves at each port's
    plane, a = (V + Zref I)/2 going in, b = (V - Zref I)/2 coming out."""
    vi = [port_vi(sim, where, k, freqs) for k in range(len(where["ports"]))]
    return [[((r[n]["v"] + zref * r[n]["i"]) / 2, (r[n]["v"] - zref * r[n]["i"]) / 2) for r in vi]
            for n in range(len(freqs))]


def sparams(runs: dict, n: int, symmetric: bool = False) -> list[list[list[complex]]]:
    """S per frequency from the waves of each run ({excite: waves(...)}).
    With every port excited, S = B A^-1 (exact whatever the 50 ohm ends
    reflect). A 2-port excited once and symmetric=True (a mirror-symmetric
    reciprocal circuit: S22 = S11, S12 = S21) solves b1 = S11 a1 + S21 a2,
    b2 = S21 a1 + S11 a2."""
    import numpy as np
    nf = len(next(iter(runs.values())))
    out = []
    for m in range(nf):
        if len(runs) == n:
            A = np.array([[runs[e][m][k][0] for e in range(n)] for k in range(n)])
            B = np.array([[runs[e][m][k][1] for e in range(n)] for k in range(n)])
            out.append(np.asarray(B @ np.linalg.inv(A)).tolist())
        elif n == 2 and symmetric and len(runs) == 1:
            (a1, b1), (a2, b2) = next(iter(runs.values()))[m]
            s11, s21 = np.linalg.solve(np.array([[a1, a2], [a2, a1]]), np.array([b1, b2]))
            out.append([[complex(s11), complex(s21)], [complex(s21), complex(s11)]])
        elif n == 1:
            (a1, b1), = next(iter(runs.values()))[m]
            out.append([[b1 / a1]])
        else:
            raise ValueError(f"{len(runs)} run(s) for {n} ports: excite every port, "
                             "or a 2-port once with symmetric=True")
    return out


def touchstone(freqs, S, comments=()) -> str:
    """Touchstone v1 (Hz S RI R 50) from full S matrices per frequency."""
    n = len(S[0])
    lines = [f"! {c}" for c in comments] + ["# Hz S RI R 50"]
    order = ([(0, 0), (1, 0), (0, 1), (1, 1)] if n == 2
             else [(i, j) for i in range(n) for j in range(n)])
    for f, s in zip(freqs, S):
        row = [f"{f:.6e}"]
        for i, j in order:
            v = complex(s[i][j])
            row += [f"{v.real:.6e}", f"{v.imag:.6e}"]
        lines.append(" ".join(row))
    return "\n".join(lines) + "\n"


# ================================================================ run

def study(geo: dict, ports, f_max: float, f_min: float | None = None, points: int = 201,
          symmetric: bool = False, excites=None, keep=None, run=None, **kw) -> dict:
    """extract'ed geometry -> openEMS run(s) -> S. run(csx, where, work)
    is the runner (default lib/solvers openEMS); keep=DIR keeps each run."""
    import tempfile
    import time
    from . import solvers
    n = len(ports)
    excites = excites if excites is not None else ([0] if (symmetric and n == 2) or n == 1
                                                   else list(range(n)))
    f_min = f_min or f_max / 20
    freqs = [f_min + (f_max - f_min) * i / (points - 1) for i in range(points)]
    runs, info, where = {}, [], None
    for e in excites:
        csx, where = deck(geo, ports, f_max, e, **kw)
        ctx = tempfile.TemporaryDirectory(prefix="hwde-emboard-") if not keep else None
        work = Path(keep) / f"port{e}" if keep else Path(ctx.name)
        work.mkdir(parents=True, exist_ok=True)
        (work / "deck.xml").write_text(csx.xml())
        t0 = time.time()
        if run:
            run(csx, where, work)
        else:
            r = solvers.run("openems", ["deck.xml"], cwd=work, timeout=4 * 3600)
            if not (work / "p0u0").exists():
                raise solvers.SolverError(f"openEMS wrote no probes: {(r.stdout + r.stderr)[-400:]}")
        runs[e] = waves(work, where, freqs)
        zf = [abs(port_vi(work, where, k, [f_max / 2])[0]["z"]) for k in range(n)]
        info.append({"excite": e, "cells": where["cells"], "mesh": where["mesh"],
                     "seconds": round(time.time() - t0, 1), "z_feed": [round(z, 2) for z in zf]})
        if ctx:
            ctx.cleanup()
    S = sparams(runs, n, symmetric)
    return {"freqs": freqs, "S": S, "ports": where["ports"], "runs": info,
            "region": where["region"], "fine": where["fine"], "floor": where["floor"]}


# ================================================================ closed forms

def line_s(freqs, z0: float, eps_eff: float, length: float, zref: float = ZREF,
           alpha_np_per_m=0.0) -> list[list[list[complex]]]:
    """S of a uniform line (Z0, eps_eff, length mm) between zref ports,
    from its ABCD matrix; alpha_np_per_m(f) adds a loss."""
    out = []
    for f in freqs:
        a = alpha_np_per_m(f) if callable(alpha_np_per_m) else alpha_np_per_m
        g = complex(a, 2 * math.pi * f * math.sqrt(eps_eff) / C0) * length * 1e-3
        A, B, C, D = cmath.cosh(g), z0 * cmath.sinh(g), cmath.sinh(g) / z0, cmath.cosh(g)
        den = A + B / zref + C * zref + D
        s11 = (A + B / zref - C * zref - D) / den
        s21 = 2 / den
        out.append([[s11, s21], [s21, s11]])
    return out


def abcd_from_s(s, zref: float = ZREF):
    s11, s12, s21, s22 = s[0][0], s[0][1], s[1][0], s[1][1]
    A = ((1 + s11) * (1 - s22) + s12 * s21) / (2 * s21)
    B = zref * ((1 + s11) * (1 + s22) - s12 * s21) / (2 * s21)
    C = ((1 - s11) * (1 - s22) - s12 * s21) / (2 * s21 * zref)
    D = ((1 - s11) * (1 + s22) + s12 * s21) / (2 * s21)
    return A, B, C, D


def line_from_s(freqs, S, length: float, zref: float = ZREF) -> list[dict]:
    """Z0 and eps_eff of a uniform line back from its measured 2-port:
    Z0 = sqrt(B/C), e^(-gamma l) = 1/(A + B/Z0), the phase unwrapped
    across the band (start low enough that the first step is under pi)."""
    out, prev = [], 0.0
    for f, s in zip(freqs, S):
        A, B, C, D = abcd_from_s(s, zref)
        z = cmath.sqrt(B / C)
        if z.real < 0:
            z = -z
        t = 1 / (A + B / z)
        ph = -cmath.phase(t)
        while ph < prev - math.pi:
            ph += 2 * math.pi
        prev = ph
        beta = ph / (length * 1e-3)
        out.append({"f": f, "z0": z.real, "eps_eff": (beta * C0 / (2 * math.pi * f)) ** 2,
                    "loss_db": -20 * math.log10(abs(t))})
    return out


def open_end_dl(w: float, h: float, eps_eff: float) -> float:
    """Open-end length extension of a microstrip (Hammerstad-Bekkadal)."""
    return 0.412 * h * (eps_eff + 0.3) * (w / h + 0.264) / ((eps_eff - 0.258) * (w / h + 0.8))


def notch_f(length: float, eps_eff: float) -> float:
    """First notch of an open quarter-wave stub length mm long."""
    return C0 / (4 * length * 1e-3 * math.sqrt(eps_eff))
