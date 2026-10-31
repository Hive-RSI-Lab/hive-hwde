"""check_rf.py - RF layout checks on declared RF nets, blocks, shield cans
and reference-plane cutouts (P8 verify suite; hive-hwde #335).

The gap (hw-lab radar-detector survey, 2026-10-31): nothing in ERC, DRC or
the suite knew an RF net from any other copper. A dangling branch on a feed,
a line laid longer than its match, an LO trace beside the receive path, a
shield can with no ground ring, a plane left solid under a launch: all
passed. Each finding below is one of those, measured at the net's own
frequency.

Inputs (constraints.json; every section optional, a board with none passes
with nothing checked):

  rf          [{net, f_ghz, impedance_ohm?, path?: ["U1.3", "J1.1"],
                max_deg?, length_deg?, tol_deg?, max_stub_deg?,
                allow_stubs?}]
  rf_phase    [{nets: [...], tol_deg, f_ghz?, reason?}]
  rf_blocks   [{name, f_ghz, refs?: [...], nets?: [...], ground?: "GND"}]
  rf_isolation [{a, b, min_mm?, wall?, reason?}]  (default: every pair of
                blocks, no wall required)
  shields     [{name, ref? | rect? | poly?, refs?: [...], side?, net?,
                ring_mm?, fence_pitch_mm?, f_ghz?, max_height_mm?,
                heights?: {ref: mm}}]
  rf_cutouts  [{ref + pads? + margin_mm? | rect | poly, layers: [...],
                name?, reason?}]  (keepout_gen draws them as rule areas)

Electrical length is sum(L * sqrt(eeff)) over the path: eeff from
impedance.microstrip_eeff_closed_form on an outer layer (h to the adjacent
copper layer, the stackup's er), er on an inner one; via barrels between
the layers they join add their height at the dielectric's er. Quasi-static:
dispersion and the design-vs-process Dk are brief #2's (single-ended RF
lines), so phases above ~10 GHz read slightly short.

Findings:
  rf_stub            error: an open branch of an rf net (track or via that
                     ends on no pad) longer than max_stub_deg (default 18,
                     lambda/20) at f
  rf_via_stub        error: a via's unused barrel beyond the last layer it
                     joins is longer than max_stub_deg (backdrill or a
                     blind via)
  rf_length          error: the path's electrical length is over max_deg,
                     or off length_deg by more than tol_deg (default 5)
  rf_phase_mismatch  error: an rf_phase group's paths spread wider than
                     tol_deg
  rf_path_open       warning: a length or phase limit with no routed path
                     between its terminals (or no path declared on a net
                     with more than two pads)
  rf_isolation       error: two declared RF blocks' copper on one layer
                     closer than min_mm (default ISO_H_MULT x the layer's
                     dielectric height) with no ground-via wall between
  rf_isolation_wall  error: a pair declared wall: true whose facing copper
                     isn't fenced by ground vias at lambda_g/10 of the
                     higher frequency
  shield_part_outside error: a part the shield must cover has pads outside
                     the can outline
  shield_ring_gap    error: the can outline's ground ring (the can's own
                     pads, or exposed ground pads on its side) has a gap
                     longer than the fence pitch
  shield_fence_gap   error: ground vias along the ring are further apart
                     than the fence pitch (lambda_g/10 at f, or declared)
  shield_tall_part   error: a part inside the can is taller than
                     max_height_mm
  shield_height_unknown warning: max_height_mm is set and a part's height
                     can't be read (3D model or heights)
  rf_cutout_copper   error: copper on a declared reference cutout's layer
                     inside it

CLI: --pcb board.kicad_pcb --constraints c.json [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import heapq
import math
import sys
from pathlib import Path
from types import SimpleNamespace

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, substring, unary_union

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
import impedance  # noqa: E402

SCRIPT = "check_rf"
SOURCE = SCRIPT
C0_MM_GHZ = 299.792458    # c in mm * GHz: lambda0 (mm) = C0_MM_GHZ / f_ghz
STUB_DEG = 18.0           # lambda/20: an open stub past this is reactive
TOL_DEG = 5.0             # default length_deg tolerance
FENCE_DIV = 10.0          # via fence / wall pitch <= lambda_g / FENCE_DIV
DEFAULT_PITCH_MM = 3.0    # a shield with no frequency anywhere: lambda/10
                          # at ~5.5 GHz on FR4
ISO_H_MULT = 5.0          # edge spacing >= 5 h: coupled microstrips sit
                          # near -30 dB there
RING_MM = 0.5             # can ring (solder land) width when not declared
SAMPLE_MM = 0.2           # perimeter / boundary sampling step
TOL_MM2 = 0.01            # copper area below this is arc sampling
SNAP = 3                  # endpoint rounding (decimals of a mm)
GROUND = "GND"


def _deg(le_mm: float, f_ghz: float) -> float:
    """Electrical length in degrees of `le_mm` (already times sqrt(eeff))."""
    return 360.0 * le_mm * f_ghz / C0_MM_GHZ


def _lambda_g(f_ghz: float, eeff: float) -> float:
    return C0_MM_GHZ / f_ghz / math.sqrt(eeff)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ============================================================ medium

class Medium:
    """sqrt(eeff) per (layer, width), from the board's stackup."""

    def __init__(self, bg):
        self.st = bg.stackup
        self._cache = {}

    def h(self, layer: str) -> float:
        """Dielectric height from `layer` to its nearest copper neighbour."""
        above, below = self.st.adjacent(layer)
        hs = [self.st.height_between(layer, n) for n in (above, below) if n]
        return min(hs) if hs else 1.0

    def er(self, layer: str) -> float:
        above, below = self.st.adjacent(layer)
        ers = [self.st.epsilon_between(layer, n) for n in (above, below) if n]
        return sum(ers) / len(ers) if ers else 4.5

    def eeff(self, layer: str, width: float) -> float:
        key = (layer, round(width, 4))
        if key not in self._cache:
            er = self.er(layer)
            if self.st.is_outer(layer):
                t = self.st.copper_thickness.get(layer, 0.035)
                self._cache[key] = impedance.microstrip_eeff_closed_form(
                    max(width, 1e-3), self.h(layer), t, er)
            else:
                self._cache[key] = er
        return self._cache[key]

    def barrel(self, a: str, b: str) -> float:
        """A via barrel from copper layer a to b, as L * sqrt(er)."""
        if a == b:
            return 0.0
        return self.st.height_between(a, b) * math.sqrt(
            self.st.epsilon_between(a, b))

    def er_board(self) -> float:
        return max(d["epsilon_r"] for d in self.st.dielectrics) \
            if self.st.dielectrics else 4.5


# ============================================================ net graph

class NetGraph:
    """An rf net's copper as a graph. Nodes: a pad ("P", ref, num), a via on
    one layer ("V", i, layer), or a free track end ("X", layer, x, y).
    Edges carry the electrical length (mm * sqrt(eeff)) and the copper
    length; via nodes on adjacent spanned layers join by their barrel."""

    def __init__(self, bg, net: str, med: Medium):
        self.net = net
        self.pads = bg.pads_of(net=net)
        self.vias = bg.vias_of(net=net)
        self.adj: dict = {}
        self.pos: dict = {}
        self.via_used: dict[int, set] = {i: set() for i in range(len(self.vias))}
        segs = []
        for t in bg.tracks_of(net=net):
            segs.append([t.layer, t.width, t.shape])
        segs = self._split_tees(segs)
        for layer, width, line in segs:
            a = self._node(layer, line.coords[0])
            b = self._node(layer, line.coords[-1])
            if a == b:
                continue
            le = line.length * math.sqrt(med.eeff(layer, width))
            self._edge(a, b, le, line.length)
        # pads a via sits in (via-in-pad) join the via on the pad's layers
        for i, v in enumerate(self.vias):
            for p in self.pads:
                for layer in p.layers:
                    if v.spans(layer) and p.poly.contains(Point(v.at)):
                        self._edge(("P", p.ref, p.number), ("V", i, layer),
                                   0.0, 0.0)
                        self.via_used[i].add(layer)
        for i, v in enumerate(self.vias):
            for layer in v.layers:
                if self.adj.get(("V", i, layer)):
                    self.via_used[i].add(layer)
            # zone fills of the net at the via count as a connection
            for z in bg.zones_of(net=net):
                for layer in z.layers:
                    if v.spans(layer) and z.fill_on(layer).intersects(
                            Point(v.at)):
                        self.via_used[i].add(layer)
            used = [l for l in v.layers if l in self.via_used[i]]
            for la, lb in zip(used, used[1:]):
                self._edge(("V", i, la), ("V", i, lb), med.barrel(la, lb),
                           0.0)

    def _split_tees(self, segs):
        """A track end that lands on another track's middle splits it there
        (KiCad doesn't always), so the tee is a junction."""
        changed = True
        while changed:
            changed = False
            ends = [(s[0], Point(s[2].coords[k])) for s in segs
                    for k in (0, -1)]
            for layer, p in ends:
                for j, (ly, w, line) in enumerate(segs):
                    if ly != layer or line.distance(p) > w / 2:
                        continue
                    d = line.project(p)
                    if d < 1e-3 or d > line.length - 1e-3:
                        continue
                    if any(self._on_item(layer, (p.x, p.y))):
                        continue
                    segs[j:j + 1] = [[ly, w, substring(line, 0, d)],
                                     [ly, w, substring(line, d, line.length)]]
                    changed = True
                    break
                if changed:
                    break
        return segs

    def _on_item(self, layer, xy):
        pt = Point(xy)
        for p in self.pads:
            if p.on(layer) and p.poly.buffer(0.01).contains(pt):
                yield ("P", p.ref, p.number)
        for i, v in enumerate(self.vias):
            if v.spans(layer) and pt.distance(Point(v.at)) <= v.diameter / 2:
                yield ("V", i, layer)

    def _node(self, layer, xy):
        n = next(self._on_item(layer, xy), None)
        if n is None:
            n = ("X", layer, round(xy[0], SNAP), round(xy[1], SNAP))
        self.pos.setdefault(n, (xy[0], xy[1]))
        return n

    def _edge(self, a, b, le, length):
        self.adj.setdefault(a, []).append((b, le, length))
        self.adj.setdefault(b, []).append((a, le, length))

    def where(self, n):
        if n in self.pos:
            return self.pos[n]
        if n[0] == "P":
            p = next(p for p in self.pads if (p.ref, p.number) == n[1:])
            return p.center
        return self.vias[n[1]].at

    # -------------------------------------------------- stubs
    def _dead(self, n) -> bool:
        if len(self.adj.get(n, [])) != 1:
            return False
        if n[0] == "X":
            return True
        if n[0] == "V":     # a via that joins nothing else on any layer
            return len(self.via_used[n[1]]) <= 1
        return False

    def open_stubs(self):
        """[(dead-end node, electrical length, copper length)] for every open
        branch: walked from the dead end to the first pad or junction."""
        out = []
        for n in list(self.adj):
            if not self._dead(n):
                continue
            prev, cur, le, ln = None, n, 0.0, 0.0
            seen = {n}
            while True:
                nxt = [e for e in self.adj[cur] if e[0] != prev]
                if not nxt:
                    break
                b, e_le, e_ln = nxt[0]
                le += e_le
                ln += e_ln
                prev, cur = cur, b
                if cur in seen or cur[0] == "P" or \
                        len(self.adj.get(cur, [])) != 2:
                    break
                seen.add(cur)
            out.append((n, le, ln))
        return out

    def via_stubs(self, med: Medium):
        """[(via index, unused barrel as L*sqrt(er), from-layer, to-layer)]."""
        out = []
        for i, v in enumerate(self.vias):
            used = [l for l in v.layers if l in self.via_used[i]]
            if len(used) < 2:
                continue
            span = list(v.layers)
            for end, last in ((span[0], used[0]), (span[-1], used[-1])):
                le = med.barrel(last, end)
                if le > 0:
                    out.append((i, le, last, end))
        return out

    # -------------------------------------------------- paths
    def terminals(self, path):
        """The two pad nodes the electrical length runs between."""
        if path:
            nodes = []
            for t in path:
                ref, _, num = str(t).partition(".")
                nodes.append(("P", ref, num))
            return nodes
        pads = sorted({("P", p.ref, p.number) for p in self.pads})
        return pads if len(pads) == 2 else None

    def length(self, a, b):
        """Shortest electrical length a -> b (Dijkstra), or None."""
        best = {a: 0.0}
        heap = [(0.0, a)]
        while heap:
            d, n = heapq.heappop(heap)
            if n == b:
                return d
            if d > best.get(n, math.inf):
                continue
            for m, le, _ in self.adj.get(n, []):
                nd = d + le
                if nd < best.get(m, math.inf):
                    best[m] = nd
                    heapq.heappush(heap, (nd, m))
        return None


# ============================================================ rf nets

def _rf_entries(cons: dict, nets: set) -> list[dict]:
    out = []
    for i, e in enumerate(cons.get("rf") or []):
        if not isinstance(e, dict) or not isinstance(e.get("net"), str):
            raise checklib.CheckError(f"constraints rf[{i}]: needs a net")
        if e["net"] not in nets:
            raise checklib.CheckError(
                f"constraints rf[{i}]: net {e['net']!r} is not on the board")
        f = e.get("f_ghz")
        if f is not None and not (_num(f) and f > 0):
            raise checklib.CheckError(
                f"constraints rf[{i}]: f_ghz must be a positive number")
        out.append(e)
    return out


def check_nets(bg, med, cons) -> tuple[list, list, dict]:
    """Stubs and electrical length per rf net, then rf_phase groups."""
    viols, facts, lengths = [], [], {}
    entries = _rf_entries(cons, bg.nets)
    by_net = {e["net"]: e for e in entries}
    graphs = {}
    for e in entries:
        net, f = e["net"], e.get("f_ghz")
        g = graphs[net] = NetGraph(bg, net, med)
        fact = {"net": net, "f_ghz": f}
        facts.append(fact)
        if f is None:
            fact["note"] = "no f_ghz: stubs and length not checked"
            continue
        limit = float(e.get("max_stub_deg", STUB_DEG))
        stubs = []
        for n, le, ln in g.open_stubs():
            deg = _deg(le, f)
            stubs.append({"at": [checklib.rnd(v, 3) for v in g.where(n)],
                          "mm": checklib.rnd(ln, 2),
                          "deg": checklib.rnd(deg, 1)})
            if deg > limit and not e.get("allow_stubs"):
                viols.append(checklib.violation(
                    SCRIPT, "error", g.where(n),
                    n[1] if n[0] == "X" else None, net, [],
                    f"{net}: an open stub {ln:.2f} mm long ({deg:.1f} deg at "
                    f"{f:g} GHz, over {limit:g} = lambda/20) ends at "
                    f"({g.where(n)[0]:.2f}, {g.where(n)[1]:.2f}) on no pad. "
                    "Cut it back to the junction, or declare allow_stubs if "
                    "it is a tuned stub", SOURCE, kind="rf_stub",
                    length_mm=checklib.rnd(ln, 3), deg=checklib.rnd(deg, 2)))
        fact["stubs"] = stubs
        for i, le, la, lb in g.via_stubs(med):
            deg = _deg(le, f)
            if deg > limit:
                v = g.vias[i]
                viols.append(checklib.violation(
                    SCRIPT, "error", v.at, None, net, [],
                    f"{net}: the via at ({v.at[0]:.2f}, {v.at[1]:.2f}) runs on "
                    f"past {la} to {lb}, an unused barrel of {deg:.1f} deg at "
                    f"{f:g} GHz (over {limit:g}). Backdrill it, or use a "
                    "blind via that stops at the layer it joins", SOURCE,
                    kind="rf_via_stub", deg=checklib.rnd(deg, 2),
                    layers=[la, lb]))
        wants = any(k in e for k in ("max_deg", "length_deg")) or any(
            net in (grp.get("nets") or []) for grp in cons.get("rf_phase") or [])
        if not wants:
            continue
        term = g.terminals(e.get("path"))
        le = g.length(*term) if term and len(term) == 2 else None
        if le is None:
            viols.append(checklib.violation(
                SCRIPT, "warning", None, None, net, [],
                f"{net}: a length or phase limit is declared, but "
                + ("no routed path joins " + " and ".join(
                    f"{t[1]}.{t[2]}" for t in term) if term else
                   "the net has more than two pads and no 'path'")
                + ", so its electrical length can't be measured", SOURCE,
                kind="rf_path_open"))
            continue
        lengths[net] = le
        deg = _deg(le, f)
        fact.update(path=[f"{t[1]}.{t[2]}" for t in term],
                    electrical_mm=checklib.rnd(le, 3),
                    deg=checklib.rnd(deg, 2))
        at = g.where(term[0])
        if "max_deg" in e and deg > float(e["max_deg"]):
            viols.append(checklib.violation(
                SCRIPT, "error", at, None, net, [term[0][1], term[1][1]],
                f"{net}: {term[0][1]}.{term[0][2]} to {term[1][1]}."
                f"{term[1][2]} is {deg:.1f} deg at {f:g} GHz, over its "
                f"max_deg {float(e['max_deg']):g}", SOURCE, kind="rf_length",
                deg=checklib.rnd(deg, 2)))
        if "length_deg" in e:
            want, tol = float(e["length_deg"]), float(e.get("tol_deg", TOL_DEG))
            if abs(deg - want) > tol:
                viols.append(checklib.violation(
                    SCRIPT, "error", at, None, net,
                    [term[0][1], term[1][1]],
                    f"{net}: {deg:.1f} deg at {f:g} GHz, {deg - want:+.1f} "
                    f"from its length_deg {want:g} (tolerance {tol:g})",
                    SOURCE, kind="rf_length", deg=checklib.rnd(deg, 2)))
    groups = []
    for i, grp in enumerate(cons.get("rf_phase") or []):
        nets = grp.get("nets") or []
        if len(nets) < 2 or not _num(grp.get("tol_deg")):
            raise checklib.CheckError(
                f"constraints rf_phase[{i}]: needs two or more nets and "
                "tol_deg")
        degs = {}
        for n in nets:
            if n not in by_net:
                raise checklib.CheckError(
                    f"constraints rf_phase[{i}]: {n!r} has no rf entry")
            f = grp.get("f_ghz") or by_net[n].get("f_ghz")
            if not f:
                raise checklib.CheckError(
                    f"constraints rf_phase[{i}]: no f_ghz for {n}")
            if n in lengths:
                degs[n] = _deg(lengths[n], f)
        groups.append({"nets": nets, "deg": {n: checklib.rnd(d, 2)
                                             for n, d in degs.items()}})
        if len(degs) < 2:
            continue
        lo, hi = min(degs, key=degs.get), max(degs, key=degs.get)
        spread = degs[hi] - degs[lo]
        if spread > float(grp["tol_deg"]):
            g = graphs[hi]
            viols.append(checklib.violation(
                SCRIPT, "error", g.where(g.terminals(
                    by_net[hi].get("path"))[0]), None, hi, [],
                f"rf_phase {', '.join(nets)}: {hi} is {degs[hi]:.1f} deg and "
                f"{lo} {degs[lo]:.1f} deg, {spread:.1f} apart (tolerance "
                f"{float(grp['tol_deg']):g})", SOURCE,
                kind="rf_phase_mismatch", nets=sorted(degs),
                spread_deg=checklib.rnd(spread, 2)))
    return viols, facts, {"phase_groups": groups}


# ============================================================ isolation

def _ground(bg, net: str):
    """Ground-wall items: vias and plated through pads of `net`, as points."""
    pts = [Point(v.at) for v in bg.vias_of(net=net)]
    pts += [Point(p.center) for p in bg.pads_of(net=net)
            if p.drill is not None and len(p.layers) > 1]
    return pts


def _block_copper(bg, blk: dict, layer: str, grounds: set):
    shapes = []
    for n in blk.get("nets") or []:
        shapes += [t.poly for t in bg.tracks_of(net=n, layer=layer)]
        shapes += [v.poly for v in bg.vias_of(net=n, layer=layer)]
        shapes += [p.poly for p in bg.pads_of(net=n, layer=layer)]
    for r in blk.get("refs") or []:
        shapes += [p.poly for p in bg.pads_of(ref=r, layer=layer)
                   if p.net not in grounds]
    return unary_union(shapes) if shapes else None


def _samples(g, step: float = SAMPLE_MM):
    out = []
    for poly in getattr(g, "geoms", [g]):
        ring = poly.exterior
        n = max(int(ring.length / step), 4)
        out += [ring.interpolate(k * ring.length / n) for k in range(n)]
    return out


def wall_gaps(a, b, posts, pitch: float, reach: float):
    """Points of `a`'s edge within `reach` of `b` whose straight line to
    `b` has no ground post on it: a post counts when it stands between the
    two (it projects inside the line) within half the pitch of it, and no
    further off than the line is long (a via well to one side of a narrow
    gap fences nothing, however long lambda is)."""
    gaps = []
    posts = [q for q in posts
             if q.distance(a) <= reach and q.distance(b) <= reach]
    for p in _samples(a):
        if p.distance(b) > reach:
            continue
        q = nearest_points(p, b)[1]
        seg = LineString([p, q]) if p.distance(q) > 1e-6 else None
        r = min(pitch / 2, max(seg.length if seg else 0.0, 0.3))
        hit = False
        for post in posts:
            if seg is None:
                hit = post.distance(p) <= r
            else:
                t = seg.project(post)
                hit = 0 < t < seg.length and seg.distance(post) <= r
            if hit:
                break
        if not hit:
            gaps.append(p)
    return gaps


def check_isolation(bg, med, cons) -> tuple[list, dict]:
    viols = []
    blocks = {}
    for i, b in enumerate(cons.get("rf_blocks") or []):
        if not isinstance(b, dict) or not b.get("name") or \
                not (_num(b.get("f_ghz")) and b["f_ghz"] > 0):
            raise checklib.CheckError(
                f"constraints rf_blocks[{i}]: needs a name and f_ghz")
        for r in b.get("refs") or []:
            if r not in bg.footprints:
                raise checklib.CheckError(
                    f"constraints rf_blocks[{i}]: {r} is not on the board")
        blocks[b["name"]] = b
    pairs = cons.get("rf_isolation")
    if pairs is None:
        names = sorted(blocks)
        pairs = [{"a": x, "b": y} for k, x in enumerate(names)
                 for y in names[k + 1:]]
    facts = []
    for i, pr in enumerate(pairs):
        A, B = blocks.get(pr.get("a")), blocks.get(pr.get("b"))
        if A is None or B is None:
            raise checklib.CheckError(
                f"constraints rf_isolation[{i}]: a and b must name rf_blocks")
        gnd = A.get("ground", GROUND)
        grounds = {A.get("ground", GROUND), B.get("ground", GROUND)}
        f = max(A["f_ghz"], B["f_ghz"])
        pitch = _lambda_g(f, med.er_board()) / FENCE_DIV
        posts = _ground(bg, gnd)
        fact = {"a": A["name"], "b": B["name"], "pitch_mm":
                checklib.rnd(pitch, 3), "layers": {}}
        facts.append(fact)
        for layer in bg.copper_layers:
            ca = _block_copper(bg, A, layer, grounds)
            cb = _block_copper(bg, B, layer, grounds)
            if ca is None or cb is None or ca.is_empty or cb.is_empty:
                continue
            d = ca.distance(cb)
            need = float(pr.get("min_mm", ISO_H_MULT * med.h(layer)))
            fact["layers"][layer] = {"spacing_mm": checklib.rnd(d, 3),
                                     "min_mm": checklib.rnd(need, 3)}
            if d >= need and not pr.get("wall"):
                continue
            gaps = wall_gaps(ca, cb, posts, pitch, d + max(need, pitch))
            fact["layers"][layer]["wall_open"] = len(gaps)
            if not gaps:
                continue
            pa, pb = nearest_points(ca, cb)
            mid = ((pa.x + pb.x) / 2, (pa.y + pb.y) / 2)
            refs = sorted(set(A.get("refs") or []) | set(B.get("refs") or []))
            if d < need:
                viols.append(checklib.violation(
                    SCRIPT, "error", mid, layer, None, refs,
                    f"RF blocks {A['name']} ({A['f_ghz']:g} GHz) and "
                    f"{B['name']} ({B['f_ghz']:g} GHz): {d:.2f} mm apart on "
                    f"{layer} (at least {need:.2f} mm) with no {gnd} via wall "
                    f"between at {pitch:.2f} mm pitch. Move them apart or "
                    "fence the gap with ground vias", SOURCE,
                    kind="rf_isolation", spacing_mm=checklib.rnd(d, 3),
                    blocks=[A["name"], B["name"]]))
            else:
                g0 = gaps[len(gaps) // 2]
                viols.append(checklib.violation(
                    SCRIPT, "error", (g0.x, g0.y), layer, None, refs,
                    f"RF blocks {A['name']} and {B['name']} must have a "
                    f"{gnd} via wall between them on {layer}, and "
                    f"{len(gaps)} line(s) from one to the other pass no via "
                    f"within {pitch / 2:.2f} mm (lambda_g/{FENCE_DIV:g} at "
                    f"{f:g} GHz is {pitch:.2f} mm)", SOURCE,
                    kind="rf_isolation_wall", blocks=[A["name"], B["name"]]))
    return viols, {"isolation": facts}


# ============================================================ shields

def _region(bg, e: dict, where: str):
    """A declared rect / poly, or a footprint's pads (named, or all) grown
    by margin_mm."""
    if "rect" in e:
        x1, y1, x2, y2 = (float(v) for v in e["rect"])
        return box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if "poly" in e:
        return Polygon([(float(x), float(y)) for x, y in e["poly"]])
    ref = e.get("ref")
    if ref not in bg.footprints:
        raise checklib.CheckError(
            f"constraints {where}: needs rect, poly or a ref on the board")
    names = {str(p) for p in e.get("pads") or []}
    pads = [p for p in bg.pads_of(ref=ref)
            if not names or p.number in names]
    if not pads:
        raise checklib.CheckError(
            f"constraints {where}: {ref} has no pad {sorted(names)}")
    return unary_union([p.poly for p in pads]).buffer(
        float(e.get("margin_mm", 0.0)))


def _heights(pcb: Path, bg, refs, declared: dict) -> dict:
    import matinglib  # noqa: PLC0415 - only a shield with max_height_mm
    models = matinglib.model_paths(pcb)
    out = {}
    for r in refs:
        if r in declared:
            out[r] = float(declared[r])
            continue
        fp = SimpleNamespace(fpid=(bg.footprints.get(r) or {}).get("lib") or "")
        out[r] = matinglib.part_height(fp, models.get(r), pcb)
    return out


def _gaps_along(ring, covered: list[bool], step: float):
    """Runs of uncovered samples round a closed ring: [(length, midpoint)]."""
    n = len(covered)
    if all(covered):
        return []
    if not any(covered):
        return [(ring.length, ring.interpolate(0))]
    start = covered.index(True)
    runs, k, run = [], 0, []
    while k < n:
        i = (start + k) % n
        if not covered[i]:
            run.append(i)
        elif run:
            runs.append(run)
            run = []
        k += 1
    if run:
        runs.append(run)
    return [(len(r) * step, ring.interpolate(r[len(r) // 2] * step))
            for r in runs]


def check_shields(pcb, bg, med, cons, rf_f: dict) -> tuple[list, dict]:
    viols, facts = [], []
    for i, s in enumerate(cons.get("shields") or []):
        where = f"shields[{i}]"
        name = s.get("name") or f"shield{i}"
        outline = _region(bg, s, where)
        if "rect" not in s and "poly" not in s:
            outline = outline.convex_hull     # a can's own pads: its frame
        ref = s.get("ref")
        side = s.get("side")
        if side is None and ref:
            lay = {l for p in bg.pads_of(ref=ref) for l in p.layers}
            side = "back" if bg.copper_layers[-1] in lay and \
                bg.copper_layers[0] not in lay else "front"
        layer = bg.copper_layers[-1] if side == "back" else bg.copper_layers[0]
        net = s.get("net", GROUND)
        ring_w = float(s.get("ring_mm", RING_MM))
        inside_nets = [n for n, f in rf_f.items() if any(
            t.shape.intersects(outline) for t in bg.tracks_of(net=n))]
        f = s.get("f_ghz") or max([rf_f[n] for n in inside_nets], default=None)
        pitch = float(s["fence_pitch_mm"]) if "fence_pitch_mm" in s else (
            _lambda_g(f, med.er_board()) / FENCE_DIV if f else DEFAULT_PITCH_MM)
        ring = outline.exterior
        fact = {"name": name, "layer": layer, "pitch_mm": checklib.rnd(pitch, 3),
                "f_ghz": f, "perimeter_mm": checklib.rnd(ring.length, 2)}
        facts.append(fact)
        refs = [ref] if ref else []
        # parts it must cover
        for r in s.get("refs") or []:
            pads = bg.pads_of(ref=r)
            if r not in bg.footprints or not pads:
                raise checklib.CheckError(f"constraints {where}: {r} is not "
                                          "on the board")
            out = unary_union([p.poly for p in pads]).difference(outline)
            if out.area > TOL_MM2:
                c = out.centroid
                viols.append(checklib.violation(
                    SCRIPT, "error", (c.x, c.y), layer, None, refs + [r],
                    f"shield {name}: {r} has {out.area:.2f} mm2 of pads "
                    "outside the can outline, so the can doesn't cover it",
                    SOURCE, kind="shield_part_outside", shield=name))
        # the solder ring
        if ref:
            land = [p.poly for p in bg.pads_of(ref=ref, layer=layer)]
        else:
            band = ring.buffer(ring_w)
            land = [p.poly for p in bg.pads_of(net=net, layer=layer)
                    if p.poly.intersects(band)]
        land = unary_union(land) if land else None
        n = max(int(ring.length / SAMPLE_MM), 8)
        step = ring.length / n
        pts = [ring.interpolate(k * step) for k in range(n)]
        covered = [land is not None and p.distance(land) <= ring_w / 2 + 0.05
                   for p in pts]
        fact["ring_covered"] = checklib.rnd(sum(covered) / n, 3)
        for length, mid in _gaps_along(ring, covered, step):
            if length > pitch:
                viols.append(checklib.violation(
                    SCRIPT, "error", (mid.x, mid.y), layer, net, refs,
                    f"shield {name}: {length:.1f} mm of the can outline has "
                    f"no exposed {net} land on {layer} to solder the can "
                    f"to (gaps up to the fence pitch {pitch:.2f} mm pass)",
                    SOURCE, kind="shield_ring_gap", shield=name,
                    gap_mm=checklib.rnd(length, 2)))
        # the via fence
        reach = ring_w / 2 + 1.0
        at = sorted(ring.project(Point(v.at)) for v in bg.vias_of(net=net)
                    if ring.distance(Point(v.at)) <= reach)
        fact["fence_vias"] = len(at)
        if not at:
            gaps = [(ring.length, 0.0)]
        else:
            gaps = [(b - a, (a + b) / 2) for a, b in zip(at, at[1:])]
            wrap = ring.length - at[-1] + at[0]
            gaps.append((wrap, (at[-1] + wrap / 2) % ring.length))
        for length, s_mid in gaps:
            if length > pitch + 1e-6:
                mid = ring.interpolate(s_mid)
                viols.append(checklib.violation(
                    SCRIPT, "error", (mid.x, mid.y), None, net, refs,
                    f"shield {name}: {length:.1f} mm between {net} fence vias "
                    f"along the can (at most {pitch:.2f} mm, lambda_g/"
                    f"{FENCE_DIV:g}" + (f" at {f:g} GHz)" if f else ")"),
                    SOURCE, kind="shield_fence_gap", shield=name,
                    gap_mm=checklib.rnd(length, 2)))
        # nothing tall inside
        if "max_height_mm" in s:
            lim = float(s["max_height_mm"])
            inside = sorted(r for r in bg.footprints if r != ref and any(
                outline.contains(Point(p.center)) for p in bg.pads_of(ref=r)))
            hs = _heights(Path(pcb), bg, inside, s.get("heights") or {})
            fact["heights"] = hs
            for r, h in hs.items():
                c = unary_union([p.poly for p in bg.pads_of(ref=r)]).centroid
                if h is None:
                    viols.append(checklib.violation(
                        SCRIPT, "warning", (c.x, c.y), None, None, refs + [r],
                        f"shield {name}: {r}'s height can't be read (no 3D "
                        "model height): give it in the shield's heights",
                        SOURCE, kind="shield_height_unknown", shield=name))
                elif h > lim:
                    viols.append(checklib.violation(
                        SCRIPT, "error", (c.x, c.y), None, None, refs + [r],
                        f"shield {name}: {r} stands {h:.2f} mm, over the "
                        f"can's {lim:g} mm inside height", SOURCE,
                        kind="shield_tall_part", shield=name,
                        height_mm=checklib.rnd(h, 2)))
    return viols, {"shields": facts}


# ============================================================ cutouts

def check_cutouts(bg, cons) -> tuple[list, dict]:
    viols, facts = [], []
    for i, c in enumerate(cons.get("rf_cutouts") or []):
        where = f"rf_cutouts[{i}]"
        region = _region(bg, c, where)
        layers = c.get("layers") or []
        bad = [l for l in layers if l not in bg.copper_layers]
        if not layers or bad:
            raise checklib.CheckError(
                f"constraints {where}: layers must name copper layers "
                f"(got {layers})")
        own = {(c.get("ref"), str(p)) for p in c.get("pads") or []} \
            if c.get("ref") else set()
        if c.get("ref") and not own:
            own = {(c["ref"], p.number) for p in bg.pads_of(ref=c["ref"])}
        name = c.get("name") or (c.get("ref") and f"{c['ref']} launch") or \
            f"cutout{i}"
        fact = {"name": name, "layers": layers,
                "bbox": [checklib.rnd(v, 3) for v in region.bounds]}
        facts.append(fact)
        for layer in layers:
            shapes, nets = [], set()
            for z in bg.zones_of(layer=layer):
                hit = z.fill_on(layer).intersection(region)
                if hit.area > TOL_MM2:
                    shapes.append(hit)
                    nets.add(z.net)
            for item in bg.tracks_of(layer=layer) + bg.vias_of(layer=layer):
                if item.poly.intersects(region):
                    shapes.append(item.poly.intersection(region))
                    nets.add(item.net)
            for p in bg.pads_of(layer=layer):
                if (p.ref, p.number) not in own and p.poly.intersects(region):
                    shapes.append(p.poly.intersection(region))
                    nets.add(p.net or "(no net)")
            hit = unary_union(shapes) if shapes else None
            if hit is None or hit.area <= TOL_MM2:
                continue
            ctr = hit.centroid
            viols.append(checklib.violation(
                SCRIPT, "error", (ctr.x, ctr.y), layer,
                next(iter(nets)) if len(nets) == 1 else None,
                [c["ref"]] if c.get("ref") else [],
                f"reference cutout {name}: {hit.area:.2f} mm2 of copper on "
                f"{layer} (nets {', '.join(sorted(n for n in nets if n))}) "
                "where the plane must be cleared (run keepout_gen, then "
                "refill)", SOURCE, kind="rf_cutout_copper",
                area_mm2=checklib.rnd(hit.area, 3), cutout=name))
    return viols, {"cutouts": facts}


# ============================================================ entry

def check(pcb: Path, cons: dict) -> tuple[list, dict]:
    bg = geom.load_board(pcb)
    med = Medium(bg)
    viols, facts = [], {"stackup": bg.stackup.source}
    v, nets, more = check_nets(bg, med, cons)
    viols += v
    facts["nets"] = nets
    facts.update(more)
    rf_f = {e["net"]: e["f_ghz"] for e in cons.get("rf") or []
            if isinstance(e, dict) and e.get("f_ghz")}
    for v, more in (check_isolation(bg, med, cons),
                    check_shields(pcb, bg, med, cons, rf_f),
                    check_cutouts(bg, cons)):
        viols += v
        facts.update(more)
    return viols, facts


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", required=True)
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    cons = checklib.load_json(args.constraints, "constraints")
    viols, facts = check(pcb, cons)
    return checklib.report(SCRIPT, pcb, viols, checked=[facts]), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
