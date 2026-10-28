"""plane_repair - detect plane regions split by routing; repair or flag (S11, SPEC P7.4).

Detection, per (net, layer) carrying zone fill: explode the union of that
net's fill polygons on that layer into connected components and mark each
component ANCHORED when it contains a same-net pad centre or via. A multi-
component fill is only a DEFECT when the anchored components are electrically
separate: components stitched together through OTHER layers (vias/thru-pads
into an intact inner plane - the clean rf4 golden has exactly this on F.Cu)
are healthy facts, not violations. Electrical grouping = union-find over the
net's per-layer copper components, connected by the net's vias and multi-layer
pads. Unanchored components are dead-copper islands (facts only; KiCad island
removal owns them).

Repair (--repair, the default) reconnects the two largest electrically
separate anchored components. Strategies, tried over an escalating ladder
(coarse 0.5 mm grid -> fine 0.25 mm grid -> fine grid at a thinner bridge):
 1. Same-layer track bridge on the plane layer between the two groups' copper.
 2. Track bridge on another copper layer between the two groups' copper there
    (each group usually owns pads/tracks/via annuli on other layers).
 3. Two-via jumper: a clear 0.6/0.3 via spot inside each fill component plus
    a clear track path between the spots on another copper layer.
Paths come from a multi-source Dijkstra grid search: a cell is free when a
bridge-width track centered there keeps clearance from foreign copper (plus
an auto margin that keeps the corridor between adjacent free cells clear)
and either lies on the net's own copper or stays inside the outline inset
and off rule areas. Found paths are simplified to maximal straight segments
whose exact swept corridor stays clear. Ops apply atomically via
route_edit.apply_ops, zones refill (kicad-cli drc --refill-zones
--save-board), then the board is RE-ANALYZED; success = the electrical group
count for that (net, layer) dropped.

Violations (checklib schema, source "check.plane_repair"):
  plane_split               anchored fill components electrically separate
  plane_split_unrepairable  no legal bridge/jumper found (or bridge no-op)

CLI: plane_repair.py --pcb B.kicad_pcb [--net GND] [--layer In1.Cu]
     [--repair | --flag-only] [--bridge-width 0.5] [--out-report r.json]
JSON to stdout or --out-report; exit 0 pass / 1 violations / 2 error
(SPEC section 6). --flag-only never writes the board.
"""
from __future__ import annotations

import argparse
import heapq
import math
import sys
from itertools import product
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "plane_repair"
SOURCE = "check.plane_repair"

GRID_STEP = 0.5        # mm, coarse pathfinding grid pitch
FINE_STEP = 0.25       # mm, escalation grid pitch
BRIDGE_WIDTH = 0.5     # mm, default bridge track width
THIN_WIDTH = 0.25      # mm, last-resort bridge width (dense boards)
CLEARANCE = 0.2        # mm, copper clearance kept by bridges/vias
EDGE_INSET = 0.3       # mm, copper keep-in from the board outline
ANCHOR_INSET = 0.3     # mm, bridge endpoints sit this far inside the fill
VIA_SIZE = 0.6         # mm, jumper via diameter
VIA_DRILL = 0.3        # mm, jumper via drill
MIN_HOLE_DIST = 0.5    # mm, drill-to-drill centre floor (jlc_capabilities)
MAX_VIA_SPOTS = 6      # candidate via spots per component
MAX_VIA_PAIRS = 12     # spot pairs tried per candidate layer


# ============================================================ pure geometry

def explode(g) -> list[Polygon]:
    """Non-empty polygon parts of a (Multi)Polygon."""
    return [p for p in getattr(g, "geoms", [g])
            if isinstance(p, Polygon) and not p.is_empty]


def fill_components(fill) -> list[Polygon]:
    """Connected components of a fill union, largest first (deterministic:
    ties broken by bounds). Input polygons are unioned first so touching
    filled_polygon islands merge."""
    parts = explode(fill)
    if len(parts) > 1:
        parts = explode(unary_union(parts))
    return sorted(parts, key=lambda p: (-p.area, p.bounds))


class _UnionFind:
    def __init__(self):
        self._p: dict = {}

    def add(self, k):
        self._p.setdefault(k, k)

    def find(self, k):
        p = self._p
        while p[k] != k:
            p[k] = p[p[k]]
            k = p[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._p[max(ra, rb)] = min(ra, rb)


def connectivity_groups(layer_comps: dict[str, list[Polygon]],
                        connectors: list[tuple[tuple[float, float], tuple]],
                        ) -> dict[tuple[str, int], tuple[str, int]]:
    """Electrical grouping of per-layer copper components.

    layer_comps: {layer: [component polygons]} - ONE net's copper per layer.
    connectors:  [(point, layers)] - vias / multi-layer pads of that net;
                 each unions the components covering `point` on its layers.
    Returns {(layer, idx): group root key}.
    """
    uf = _UnionFind()
    for layer, comps in layer_comps.items():
        for i in range(len(comps)):
            uf.add((layer, i))
    for (x, y), layers in connectors:
        pt = Point(x, y)
        hit: list[tuple[str, int]] = []
        for layer in layers:
            for i, comp in enumerate(layer_comps.get(layer, [])):
                if comp.covers(pt):
                    hit.append((layer, i))
                    break
        for other in hit[1:]:
            uf.union(hit[0], other)
    return {k: uf.find(k) for layer, comps in layer_comps.items()
            for k in ((layer, i) for i in range(len(comps)))}


def erode_terminal(g, inset: float = ANCHOR_INSET):
    """Per-part adaptive erosion: bridge endpoints should sit `inset` inside
    fat copper (fills) but thin parts (tracks) erode to nothing - fall back
    to a shallow inset, then to the part itself."""
    parts = []
    for p in explode(g):
        for d in (inset, 0.05):
            e = p.buffer(-d)
            if not e.is_empty:
                parts.append(e)
                break
        else:
            parts.append(p)
    if not parts:
        return Polygon()
    return unary_union(parts)


# ============================================================ grid pathfinder

class PathScene:
    """One layer's routing scene: grid + multi-source Dijkstra + line-of-
    sight simplification.

    Cell free rule: a bridge-width track centered there keeps `clearance`
    (plus the auto inter-cell margin) from foreign copper AND either lies on
    the net's own copper or stays inside the outline inset and off rule
    areas. The margin is sqrt(R^2 + step^2/2) - R (R = width/2 + clearance),
    which guarantees the swept corridor between any two adjacent (8-conn)
    free cells still keeps full clearance - so simplified segments that fail
    the exact corridor check may safely fall back to single grid steps.
    """

    def __init__(self, outline: Polygon, foreign, keepouts=None, own=None,
                 width: float = BRIDGE_WIDTH, clearance: float = CLEARANCE,
                 step: float = GRID_STEP, edge_inset: float = EDGE_INSET):
        self.width = width
        self.clearance = clearance
        self.step = step
        self.foreign = foreign if foreign is not None else Polygon()
        self.keepouts = keepouts if keepouts is not None else Polygon()
        self.own = own if own is not None else Polygon()
        self.allowed = outline.buffer(-(edge_inset + width / 2.0))
        r = width / 2.0 + clearance
        self.margin = math.sqrt(r * r + step * step / 2.0) - r
        blocked = []
        if not self.foreign.is_empty:
            blocked.append(self.foreign.buffer(r + self.margin, quad_segs=8))
        if not self.keepouts.is_empty:
            blocked.append(self.keepouts.buffer(
                width / 2.0 + self.margin, quad_segs=8))
        self.blocked = unary_union(blocked) if blocked else Polygon()

        # grid aligned to absolute multiples of step (via spots land on it)
        minx, miny, maxx, maxy = outline.bounds
        self.ix0 = int(math.floor(minx / step))
        self.iy0 = int(math.floor(miny / step))
        self.nx = int(math.ceil(maxx / step)) - self.ix0 + 1
        self.ny = int(math.ceil(maxy / step)) - self.iy0 + 1
        xs = (self.ix0 + np.arange(self.nx)) * step
        ys = (self.iy0 + np.arange(self.ny)) * step
        X, Y = np.meshgrid(xs, ys)          # shape (ny, nx)
        self._fx, self._fy = X.ravel(), Y.ravel()
        free = self._mask(self.own) | self._mask(self.allowed)
        if not self.blocked.is_empty:
            free &= ~self._mask(self.blocked)
        self.free = free.reshape(self.ny, self.nx)

    def _mask(self, g) -> np.ndarray:
        if g is None or g.is_empty:
            return np.zeros(self._fx.shape, dtype=bool)
        return shapely.contains_xy(g, self._fx, self._fy)

    def _xy(self, cell: tuple[int, int]) -> tuple[float, float]:
        iy, ix = cell
        return ((self.ix0 + ix) * self.step, (self.iy0 + iy) * self.step)

    def cells_on(self, g) -> list[tuple[int, int]]:
        """Free cells covered by geometry g, sorted (deterministic)."""
        m = self._mask(g).reshape(self.ny, self.nx) & self.free
        return [tuple(c) for c in np.argwhere(m)]

    # -------------------------------------------------- Dijkstra
    _NBR = ((-1, -1, 7), (-1, 0, 5), (-1, 1, 7), (0, -1, 5),
            (0, 1, 5), (1, -1, 7), (1, 0, 5), (1, 1, 7))

    def _search(self, sources: list[tuple[int, int]],
                targets: set[tuple[int, int]]
                ) -> list[tuple[int, int]] | None:
        """Multi-source Dijkstra (straight cost 5, diagonal 7); deterministic
        (heap ties break on (y, x))."""
        free = self.free
        dist = np.full(free.shape, -1, dtype=np.int64)
        parent: dict[tuple[int, int], tuple[int, int]] = {}
        heap = []
        for (y, x) in sorted(sources):
            dist[y, x] = 0
            heap.append((0, y, x))
        heapq.heapify(heap)
        while heap:
            d, y, x = heapq.heappop(heap)
            if d > dist[y, x] >= 0:
                continue
            if (y, x) in targets:
                path = [(y, x)]
                while (y, x) in parent:
                    y, x = parent[(y, x)]
                    path.append((y, x))
                return path[::-1]
            for dy, dx, c in self._NBR:
                ny_, nx_ = y + dy, x + dx
                if not (0 <= ny_ < self.ny and 0 <= nx_ < self.nx):
                    continue
                if not free[ny_, nx_]:
                    continue
                nd = d + c
                if dist[ny_, nx_] < 0 or nd < dist[ny_, nx_]:
                    dist[ny_, nx_] = nd
                    parent[(ny_, nx_)] = (y, x)
                    heapq.heappush(heap, (nd, ny_, nx_))
        return None

    # -------------------------------------------------- corridor check
    def corridor_clear(self, a: tuple[float, float],
                       b: tuple[float, float]) -> bool:
        """True if a straight bridge a->b is legal: full clearance from
        foreign copper everywhere; centerline parts outside the net's own
        copper additionally stay inside the outline inset and off keepouts."""
        seg = LineString([a, b])
        if seg.length <= 1e-9:
            return True
        if not self.foreign.is_empty and seg.buffer(
                self.width / 2.0 + self.clearance, quad_segs=8
                ).intersects(self.foreign):
            return False
        out = seg.difference(self.own) if not self.own.is_empty else seg
        if out.is_empty:
            return True
        if not self.allowed.covers(out):
            return False
        if not self.keepouts.is_empty and out.buffer(
                self.width / 2.0, quad_segs=8).intersects(self.keepouts):
            return False
        return True

    def _simplify(self, pts: list[tuple[float, float]]
                  ) -> list[tuple[float, float]]:
        """Greedy line-of-sight: keep the farthest point whose straight
        corridor stays clear. Single grid steps are margin-guaranteed and
        kept even when the exact check declines them (own-copper hops)."""
        kept = [pts[0]]
        i = 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self.corridor_clear(pts[i], pts[j]):
                j -= 1
            kept.append(pts[j])
            i = j
        return kept

    # -------------------------------------------------- public routing
    def route_between(self, src_geom, dst_geom
                      ) -> list[tuple[float, float]] | None:
        """Simplified point path from a free cell on src_geom to a free cell
        on dst_geom, or None. Endpoints lie ON the terminal geometries."""
        sources = self.cells_on(src_geom)
        targets = set(self.cells_on(dst_geom))
        if not sources or not targets:
            return None
        cells = self._search(sources, targets)
        if cells is None:
            return None
        pts = [self._xy(c) for c in cells]
        if len(pts) == 1:  # source cell already on the target geometry
            return None
        return self._simplify(pts)

    def route_points(self, a: tuple[float, float], b: tuple[float, float]
                     ) -> list[tuple[float, float]] | None:
        """Path between two exact points (must sit on free cells)."""
        r = self.step * 0.51
        return self.route_between(Point(a).buffer(r), Point(b).buffer(r))


def path_length(pts: list[tuple[float, float]]) -> float:
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def track_ops(pts: list[tuple[float, float]], net: str, layer: str,
              width: float) -> list[dict]:
    ops = []
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        if math.hypot(x2 - x1, y2 - y1) <= 1e-9:
            continue
        ops.append({"op": "add_track",
                    "start": [checklib.rnd(x1), checklib.rnd(y1)],
                    "end": [checklib.rnd(x2), checklib.rnd(y2)],
                    "width": width, "layer": layer, "net": net})
    return ops


def iter_via_spots(comp: Polygon, blocked, allowed,
                   toward: tuple[float, float], step: float = GRID_STEP,
                   max_spots: int = MAX_VIA_SPOTS
                   ) -> list[tuple[float, float]]:
    """Grid-aligned legal via spots inside `comp`, nearest-to-`toward` first
    (deterministic ties by (y, x)). Spot rule: fill covers the whole via
    (comp eroded by VIA_SIZE/2) and `blocked`/`allowed` (all-layer copper
    clearance / outline inset) pass."""
    eroded = comp.buffer(-VIA_SIZE / 2.0)
    if eroded.is_empty:
        eroded = comp.buffer(-VIA_SIZE / 4.0)
    if eroded.is_empty:
        return []
    minx, miny, maxx, maxy = eroded.bounds
    ix0, ix1 = int(math.ceil(minx / step)), int(math.floor(maxx / step))
    iy0, iy1 = int(math.ceil(miny / step)), int(math.floor(maxy / step))
    cand = [(ix * step, iy * step)
            for iy in range(iy0, iy1 + 1) for ix in range(ix0, ix1 + 1)]
    cand.sort(key=lambda p: (math.hypot(p[0] - toward[0], p[1] - toward[1]),
                             p[1], p[0]))
    out = []
    for p in cand:
        pt = Point(p)
        if not eroded.covers(pt):
            continue
        if allowed is not None and not allowed.covers(pt):
            continue
        if blocked is not None and not blocked.is_empty and blocked.covers(pt):
            continue
        out.append(p)
        if len(out) >= max_spots:
            break
    return out


# ============================================================ board analysis

def keepouts_on(bg: geom.BoardGeom, layer: str):
    return unary_union([ra["outline"] for ra in bg.rule_areas
                        if layer in ra["layers"]
                        and not ra["outline"].is_empty] or [Polygon()])


def all_keepouts(bg: geom.BoardGeom):
    return unary_union([ra["outline"] for ra in bg.rule_areas
                        if ra["layers"] and not ra["outline"].is_empty]
                       or [Polygon()])


def fill_pairs(bg: geom.BoardGeom, net: str | None,
               layer: str | None) -> list[tuple[str, str]]:
    """(net, layer) pairs carrying zone fill, filtered, deterministic."""
    pairs = set()
    for z in bg.zones_of():
        if not z.net:
            continue
        for fl, polys in z.fills.items():
            if polys:
                pairs.add((z.net, fl))
    if net is not None:
        pairs = {p for p in pairs if p[0] == net}
    if layer is not None:
        pairs = {p for p in pairs if p[1] == layer}
    return sorted(pairs)


def net_groups(bg: geom.BoardGeom, net: str):
    """(layer_comps, groups) - electrical grouping of `net`'s copper.

    layer_comps: {layer: [copper components]} (net_copper exploded);
    groups: {(layer, idx): root} via the net's vias and multi-layer pads.
    """
    layer_comps = {}
    for lyr in bg.copper_layers:
        comps = explode(bg.net_copper(net, lyr))
        if comps:
            layer_comps[lyr] = sorted(comps, key=lambda p: (-p.area, p.bounds))
    connectors = [(v.at, v.layers) for v in bg.vias_of(net)]
    connectors += [(p.center, p.layers) for p in bg.pads_of(net)
                   if len(p.layers) > 1]
    return layer_comps, connectivity_groups(layer_comps, connectors)


def group_of(comp: Polygon, layer: str, layer_comps, groups):
    """Group root of the net-copper component containing a fill component."""
    rp = comp.representative_point()
    for i, node in enumerate(layer_comps.get(layer, [])):
        if node.covers(rp) or node.intersects(comp):
            return groups[(layer, i)]
    return None  # fill comp not found in net copper (should not happen)


def group_copper(layer_comps, groups, layer: str, root):
    """Union of one group's copper on one layer (may be empty)."""
    parts = [c for i, c in enumerate(layer_comps.get(layer, []))
             if groups[(layer, i)] == root]
    return unary_union(parts) if parts else Polygon()


def analyze_pair(bg: geom.BoardGeom, net: str, layer: str,
                 layer_comps, groups) -> dict:
    """Detection state for one (net, layer): facts + runtime polygons."""
    comps = fill_components(bg.zone_fill(net, layer))
    anchor_pts = [p.center for p in bg.pads_of(net=net, layer=layer)]
    anchor_pts += [v.at for v in bg.vias_of(net=net, layer=layer)]
    facts, runtime = [], []
    for c in comps:
        n_anchor = sum(1 for pt in anchor_pts if c.covers(Point(pt)))
        grp = group_of(c, layer, layer_comps, groups)
        rp = c.representative_point()
        facts.append({"area_mm2": checklib.rnd(c.area),
                      "pos": [checklib.rnd(rp.x), checklib.rnd(rp.y)],
                      "anchors": n_anchor, "anchored": n_anchor > 0})
        runtime.append({"poly": c, "anchored": n_anchor > 0, "group": grp})
    anchored = [r for r in runtime if r["anchored"]]
    groups_present = sorted({str(r["group"]) for r in anchored})
    split = len(groups_present) > 1
    islands = [f for f, r in zip(facts, runtime) if not r["anchored"]]
    return {
        "net": net, "layer": layer,
        "components": len(comps),
        "anchored_components": len(anchored),
        "groups": len(groups_present),
        "split": split,
        "stitched_elsewhere": len(comps) > 1 and not split
                              and len(anchored) > 1,
        "component_facts": facts,
        "dead_islands": islands,
        "_runtime": runtime,
    }


def analyze_board(bg: geom.BoardGeom, net: str | None = None,
                  layer: str | None = None) -> list[dict]:
    pairs = fill_pairs(bg, net, layer)
    cache: dict[str, tuple] = {}
    out = []
    for n, lyr in pairs:
        if n not in cache:
            cache[n] = net_groups(bg, n)
        layer_comps, groups = cache[n]
        out.append(analyze_pair(bg, n, lyr, layer_comps, groups))
    return out


def split_components(state: dict) -> tuple[Polygon, Polygon] | None:
    """The two largest anchored fill components in different groups."""
    anchored = [r for r in state["_runtime"] if r["anchored"]]
    if not anchored:
        return None
    comp_a = anchored[0]
    for r in anchored[1:]:
        if r["group"] != comp_a["group"]:
            return comp_a["poly"], r["poly"]
    return None


def split_violation(state: dict, kind: str = "plane_split",
                    extra_msg: str = "") -> dict:
    """Normalized violation for a split (net, layer)."""
    pair = split_components(state)
    if pair is None:  # defensive; split states always have a pair
        a = b = state["_runtime"][0]["poly"]
    else:
        a, b = pair
    pa, pb = nearest_points(a, b)
    pos = ((pa.x + pb.x) / 2.0, (pa.y + pb.y) / 2.0)
    gap = pa.distance(pb)
    msg = (f"{state['net']} plane on {state['layer']} is split into "
           f"{state['groups']} electrically separate regions "
           f"({state['anchored_components']} anchored components); "
           f"gap {gap:.2f} mm{extra_msg}")
    return violation(
        SCRIPT, "error", pos, state["layer"], state["net"], [], msg, SOURCE,
        kind=kind, components=state["components"],
        anchored_components=state["anchored_components"],
        groups=state["groups"], gap_mm=checklib.rnd(gap),
        gap_points=[[checklib.rnd(pa.x), checklib.rnd(pa.y)],
                    [checklib.rnd(pb.x), checklib.rnd(pb.y)]],
        areas_mm2=[f["area_mm2"] for f in state["component_facts"]])


# ============================================================ repair planning

def plan_track_bridge(bg: geom.BoardGeom, net: str, layer: str,
                      term_a, term_b, width: float, step: float
                      ) -> dict | None:
    """Track bridge on `layer` between two terminal copper geometries."""
    if term_a.is_empty or term_b.is_empty:
        return None
    scene = PathScene(bg.outline, bg.layer_copper(layer, exclude=net),
                      keepouts=keepouts_on(bg, layer),
                      own=bg.net_copper(net, layer), width=width, step=step)
    pts = scene.route_between(erode_terminal(term_a), erode_terminal(term_b))
    if pts is None:
        return None
    ops = track_ops(pts, net, layer, width)
    if not ops:
        return None
    return {"method": "track", "net": net, "layer": layer, "width": width,
            "length_mm": checklib.rnd(path_length(pts)), "ops": ops}


def via_blocked_union(bg: geom.BoardGeom, net: str):
    """Where a jumper via center may NOT go: foreign WIRED copper (tracks/
    pads/vias - zone fills re-flow around a new via at refill, so they are
    not obstacles; S11 review finding) on ANY copper layer buffered to via
    clearance, every rule area (via barrel spans all layers), and every
    existing DRILL EXTENT (vias at their real drill, multi-layer pad
    barrels incl. slot stadiums via geom.Pad.drill_poly - same-net drills
    too; hole-to-hole DRC is net-agnostic) buffered edge-to-edge:
    (MIN_HOLE_DIST - 0.3) + VIA_DRILL/2 (T6, P7A-4 - identical to the old
    MIN_HOLE_DIST centre disc for standard 0.3 mm drills, honest for
    bigger/slotted ones)."""
    parts = []
    for lyr in bg.copper_layers:
        wired = [t.poly for t in bg.tracks_of(layer=lyr) if t.net != net]
        wired += [p.poly for p in bg.pads_of(layer=lyr) if p.net != net]
        wired += [v.poly for v in bg.vias_of(layer=lyr) if v.net != net]
        if wired:
            parts.append(unary_union(wired).buffer(
                VIA_SIZE / 2.0 + CLEARANCE, quad_segs=8))
    holes = [Point(v.at).buffer(max(v.drill, 0.3) / 2.0, quad_segs=8)
             for v in bg.vias_of()]
    for p in bg.pads_of():
        if len(p.layers) > 1:
            dp = p.drill_poly
            holes.append(dp if not dp.is_empty
                         else Point(p.center).buffer(0.15, quad_segs=8))
    if holes:
        parts.append(unary_union(holes).buffer(
            (MIN_HOLE_DIST - 0.3) + VIA_DRILL / 2.0, quad_segs=8))
    ko = all_keepouts(bg)
    if not ko.is_empty:
        parts.append(ko.buffer(VIA_SIZE / 2.0, quad_segs=8))
    return unary_union(parts) if parts else Polygon()


def plan_via_jumper(bg: geom.BoardGeom, net: str, layer: str,
                    comp_a: Polygon, comp_b: Polygon,
                    width: float, step: float) -> dict | None:
    """Two new vias (one per fill component) + track path on another layer."""
    blocked = via_blocked_union(bg, net)
    allowed = bg.outline.buffer(-(EDGE_INSET + VIA_SIZE / 2.0))
    ra = comp_a.representative_point()
    rb = comp_b.representative_point()
    spots_a = iter_via_spots(comp_a, blocked, allowed,
                             toward=(rb.x, rb.y), step=step)
    spots_b = iter_via_spots(comp_b, blocked, allowed,
                             toward=(ra.x, ra.y), step=step)
    if not spots_a or not spots_b:
        return None
    pairs = sorted(product(spots_a, spots_b),
                   key=lambda ab: (math.hypot(ab[0][0] - ab[1][0],
                                              ab[0][1] - ab[1][1]), ab))
    pairs = pairs[:MAX_VIA_PAIRS]
    for other in bg.copper_layers:
        if other == layer:
            continue
        scene = PathScene(bg.outline, bg.layer_copper(other, exclude=net),
                          keepouts=keepouts_on(bg, other),
                          own=bg.net_copper(net, other),
                          width=width, step=step)
        for va, vb in pairs:
            pts = scene.route_points(va, vb)
            if pts is None:
                continue
            ops = [{"op": "add_via", "at": [checklib.rnd(va[0]),
                                            checklib.rnd(va[1])],
                    "size": VIA_SIZE, "drill": VIA_DRILL, "net": net}]
            ops += track_ops([va] + pts + [vb], net, other, width)
            ops += [{"op": "add_via", "at": [checklib.rnd(vb[0]),
                                             checklib.rnd(vb[1])],
                     "size": VIA_SIZE, "drill": VIA_DRILL, "net": net}]
            return {"method": "via_jumper", "net": net, "layer": layer,
                    "via_layer": other, "width": width,
                    "length_mm": checklib.rnd(path_length(pts)), "ops": ops}
    return None


def plan_bridge(bg: geom.BoardGeom, state: dict,
                width: float = BRIDGE_WIDTH) -> dict | None:
    """Bridge plan for the two largest electrically-separate anchored
    components of a split (net, layer). Escalation ladder over (width, grid);
    per rung: plane-layer track, other-layer track, then two-via jumper."""
    net, layer = state["net"], state["layer"]
    pair = split_components(state)
    if pair is None:
        return None
    comp_a, comp_b = pair
    layer_comps, groups = net_groups(bg, net)
    root_a = group_of(comp_a, layer, layer_comps, groups)
    root_b = group_of(comp_b, layer, layer_comps, groups)
    if root_a is None or root_b is None or root_a == root_b:
        return None

    ladder = [(width, GRID_STEP), (width, FINE_STEP)]
    if THIN_WIDTH < width:
        ladder.append((THIN_WIDTH, FINE_STEP))
    layers = [layer] + [l for l in bg.copper_layers if l != layer]
    for w, step in ladder:
        for lyr in layers:
            plan = plan_track_bridge(
                bg, net, lyr,
                group_copper(layer_comps, groups, lyr, root_a),
                group_copper(layer_comps, groups, lyr, root_b), w, step)
            if plan is not None:
                return plan
        plan = plan_via_jumper(bg, net, layer, comp_a, comp_b, w, step)
        if plan is not None:
            return plan
    return None


# ============================================================ repair driver

def repair_board(pcb: Path, bg: geom.BoardGeom, planes: list[dict],
                 width: float) -> tuple[geom.BoardGeom, list, list]:
    """Fix every split in `planes`. Returns (fresh bg, bridges, violations).
    Each bridge: apply ops -> refill -> re-analyze; success = the electrical
    group count for that (net, layer) dropped."""
    import kc
    import route_edit

    cli = env.find_kicad_cli()
    if cli is None:
        raise CheckError("kicad-cli not found (env.py) - cannot refill zones")

    bridges: list[dict] = []
    violations: list[dict] = []
    for state0 in [p for p in planes if p["split"]]:
        net, layer = state0["net"], state0["layer"]
        for _ in range(max(1, state0["groups"] - 1)):
            fresh = analyze_board(bg, net, layer)
            cur = fresh[0] if fresh else None
            if cur is None or not cur["split"]:
                break
            plan = plan_bridge(bg, cur, width)
            if plan is None:
                violations.append(split_violation(
                    cur, kind="plane_split_unrepairable",
                    extra_msg="; no legal same-layer bridge or via jumper"))
                break
            route_edit.apply_ops(pcb, plan["ops"])
            try:
                kc.run_drc(cli, pcb, refill=True, save_board=True)
            except Exception as exc:  # noqa: BLE001
                raise CheckError(
                    "a bridge was APPLIED but the refill failed - the board "
                    "HAS been modified (earlier bridges persist); re-run "
                    "'kicad-cli pcb drc --refill-zones --save-board' and "
                    f"re-run plane_repair. Cause: {exc}") from exc
            bg = geom.load_board(pcb, refresh=True)
            new = analyze_board(bg, net, layer)
            nxt = new[0] if new else None
            if nxt is None or nxt["groups"] >= cur["groups"]:
                violations.append(split_violation(
                    cur, kind="plane_split_unrepairable",
                    extra_msg="; bridge applied but regions did not merge"))
                bridges.append({**plan, "merged": False})
                break
            bridges.append({**plan, "merged": True})
    return bg, bridges, violations


# ============================================================ CLI

def _public(state: dict) -> dict:
    return {k: v for k, v in state.items() if not k.startswith("_")}


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Detect plane regions split by routing; repair or flag.")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--net", default=None, help="restrict to this net")
    ap.add_argument("--layer", default=None, help="restrict to this layer")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--repair", action="store_true", default=None,
                      help="bridge splits (default)")
    mode.add_argument("--flag-only", action="store_true",
                      help="detect and report only; never writes")
    ap.add_argument("--bridge-width", type=float, default=BRIDGE_WIDTH,
                    help="bridge track width mm (default 0.5)")
    ap.add_argument("--out-report", default=None,
                    help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    do_repair = not args.flag_only

    bg = geom.load_board(pcb, refresh=True)
    bg.assert_fresh()
    if args.net is not None and args.net not in bg.nets:
        raise CheckError(f"net {args.net!r} not on board")
    if args.layer is not None and args.layer not in bg.copper_layers:
        raise CheckError(f"layer {args.layer!r} is not a copper layer")

    planes = analyze_board(bg, args.net, args.layer)
    if not planes:
        raise CheckError("no zone fill found for the requested net/layer")
    splits = [p for p in planes if p["split"]]

    bridges: list[dict] = []
    if splits and do_repair:
        before = {(p["net"], p["layer"]): _public(p) for p in planes}
        bg, bridges, violations = repair_board(pcb, bg, planes,
                                               args.bridge_width)
        after = analyze_board(bg, args.net, args.layer)
        report_planes = []
        for p in after:
            entry = dict(before.get((p["net"], p["layer"]), {}))
            entry.update({"components_after": p["components"],
                          "groups_after": p["groups"],
                          "repaired": entry.get("split", False)
                                      and not p["split"]})
            report_planes.append(entry)
    else:
        violations = [split_violation(p) for p in splits]
        report_planes = [_public(p) for p in planes]

    payload = checklib.report(
        SCRIPT, str(pcb), violations,
        mode="repair" if do_repair else "flag-only",
        planes=report_planes,
        bridges=bridges,
        splits_found=len(splits),
        splits_repaired=sum(1 for b in bridges if b.get("merged")))
    return payload, args.out_report


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
