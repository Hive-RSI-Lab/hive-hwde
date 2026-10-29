"""route_finish - close the last unconnected items with a grid maze router (S11).

route_auto's Freerouting ladder can stall at 88-98% on a dense board, and
its KRT pass may short the board or lay power nets under their width rule.
This pass routes what DRC still reports as unconnected, one connection at a
time, on a fine grid (default 0.1 mm) with 8-way moves and through vias:

- The two sides of a connection are copper ISLANDS of the net (pads, tracks,
  vias and zone fill joined through vias), found from the board's geometry,
  so a stranded pour fragment, a boxed-in pad or a track stub are all just
  islands. A side on a plane layer is reached by dropping a via into it.
- The rules are the board's own: track width per net from the .kicad_dru
  (rules_gen's power widths), clearance from the .kicad_dru baseline or the
  netclass, per-net clearance floors, hole-to-hole, hole clearance, copper to
  edge, rule-area keepouts. Vias match the board's most common via.
- Other-net zone fills are not walls (they refill around new copper) but cost
  more to cross, so a route only splits a pour when it must; a piece it cuts
  off is joined by the next pass.
- When no path exists: rip-up and reroute of this run's own routes; a wider
  window; a finer grid at the net's floor width and the smallest legal via;
  then, for a pad walled in by other nets' tracks, a search where their
  board copper is passable at a price, ripping the fewest signal nets in the
  way (never a pour net or a net with a width rule; at most 4 nets). Ripped
  nets come back unconnected and the next pass routes them again.
- Every route is applied through route_edit, the board refilled and DRC run.
  A connection whose copper DRC names in an error is taken out again. A pass
  is kept when the unconnected count drops and no error type grows; a pass
  that ripped board copper may hold provisionally, and the run must then
  beat the board as it was before it. Stubs the rips leave hanging are swept
  (route_cleanup's dangling pass). Everything runs on a staged copy.
- --rip-errors (route_auto passes it) first takes out the tracks and vias DRC
  names in a short, clearance, crossing, hole or width error, and routes
  them again; kept only if errors + unconnected end lower with no error type
  growing.

Contract:
  route_finish.py --pcb B.kicad_pcb [--nets A,B] [--layers auto|F.Cu,B.Cu]
      [--grid 0.1] [--margin 4] [--min-width 0.2] [--clearance-margin 0.02]
      [--via-size MM --via-drill MM] [--max-conns N] [--rip-errors]
      [--no-rip-board] [--dry-run] [--work-dir DIR] [--out-report r.json]
  exit 0 = nothing left unconnected and DRC clean; 1 = some remain or other
  violations (board updated with what was kept); 2 = error (board untouched).

finish_staged() is the importable form route_auto calls on its staged board.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import numpy as np  # noqa: E402
import shapely  # noqa: E402
import shapely.ops  # noqa: E402
from shapely.geometry import LineString, Point, box  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402

import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import kc  # noqa: E402
import routelib  # noqa: E402
from checklib import CheckError  # noqa: E402

KICAD_HOLE_CLEARANCE = 0.25   # board setup default min hole clearance
KICAD_HOLE_TO_HOLE = 0.25     # board setup default min hole to hole


# ------------------------------------------------------------------ rules

@dataclass
class Rules:
    clearance: float
    hole_clearance: float
    hole_to_hole: float
    edge_clearance: float
    min_width: float
    via_size: float
    via_drill: float
    net_width: dict = field(default_factory=dict)
    net_clearance: dict = field(default_factory=dict)
    margin: float = 0.02
    track_floor: float = 0.1
    via_floor: tuple = (0.45, 0.2)   # the smallest via DRC passes

    def width(self, net: str) -> float:
        w = max(self.net_width.get(net, 0.0) + 0.001, self.min_width)
        return math.ceil(w * 1000 - 1e-6) / 1000

    def width_floor(self, net: str) -> float:
        """The narrowest width DRC passes for the net (the pro floor or the
        net's .kicad_dru rule)."""
        w = max(self.net_width.get(net, 0.0), self.track_floor) + 0.001
        return min(self.width(net), math.ceil(w * 1000 - 1e-6) / 1000)

    def clr(self, a: str | None, b: str | None) -> float:
        return max(self.clearance, self.net_clearance.get(a or "", 0.0),
                   self.net_clearance.get(b or "", 0.0)) + self.margin


def board_rules(pcb: Path, bg: geom.BoardGeom, *, min_width: float = 0.2,
                margin: float = 0.02, via_size: float | None = None,
                via_drill: float | None = None) -> Rules:
    """The rules DRC grades this board at: the .kicad_dru's unconditioned
    rules win over the .kicad_pro netclass floors (KiCad custom rules take
    precedence), per-net rules on top."""
    import route_critical as rc
    pro = pcb.with_suffix(".kicad_pro")
    dru = pcb.with_suffix(".kicad_dru")
    floors = rc.grading_floors(pro)
    base: dict[str, float] = {}
    if dru.is_file():
        for r in rc.parse_dru_rules(dru.read_text(encoding="utf-8")):
            if not r["nets"] and r["condition"] is None:
                base[r["constraint"]] = max(base.get(r["constraint"], 0.0),
                                            r["min_mm"])
    pro_rules = {}
    try:
        pro_rules = json.loads(pro.read_text(encoding="utf-8")).get(
            "board", {}).get("design_settings", {}).get("rules", {})
    except (OSError, ValueError):
        pass
    vias = {}
    for v in bg.vias_of():
        k = (round(v.diameter, 3), round(v.drill, 3))
        vias[k] = vias.get(k, 0) + 1
    common = max(vias, key=lambda k: (vias[k], k)) if vias else None
    vs = via_size or (common[0] if common else max(
        floors["via_diameter"], base.get("via_diameter", 0.0), 0.6))
    vd = via_drill or (common[1] if common else max(
        floors["via_drill"], base.get("hole_size", 0.0), 0.3))
    return Rules(
        clearance=base.get("clearance", floors["clearance"]),
        hole_clearance=max(base.get("hole_clearance", 0.0), float(
            pro_rules.get("min_hole_clearance", KICAD_HOLE_CLEARANCE))),
        hole_to_hole=max(base.get("hole_to_hole", 0.0), float(
            pro_rules.get("min_hole_to_hole", KICAD_HOLE_TO_HOLE))),
        edge_clearance=base.get("edge_clearance", floors["edge_clearance"]),
        min_width=max(min_width, floors["track_width"]),
        track_floor=max(floors["track_width"], base.get("track_width", 0.0)),
        via_floor=(min(vs, max(base.get("via_diameter", floors["via_diameter"]),
                               base.get("hole_size", floors["via_drill"])
                               + 2 * base.get("annular_width", 0.1))),
                   min(vd, base.get("hole_size", floors["via_drill"]))),
        via_size=vs, via_drill=vd,
        net_width=rc.dru_net_floors(dru, "track_width"),
        net_clearance=rc.dru_net_floors(dru, "clearance"),
        margin=margin)


# ---------------------------------------------------------------- islands

def net_islands(bg: geom.BoardGeom, net: str) -> list[dict]:
    """The net's copper as islands: [{layer: [Polygon, ...]}], polygons on
    different layers joined when a via or plated hole of the net touches
    both."""
    polys: list[tuple[str, object]] = []
    for layer in bg.copper_layers:
        g = bg.net_copper(net, layer)
        if g.is_empty:
            continue
        for p in getattr(g, "geoms", [g]):
            if not p.is_empty:
                polys.append((layer, p))
    parent = list(range(len(polys)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    links = [Point(v.at) for v in bg.vias_of(net=net)]
    links += [Point(p.center) for p in bg.pads_of(net=net) if p.drill]
    for pt in links:
        hit = [i for i, (_, p) in enumerate(polys) if p.distance(pt) < 1e-3]
        for i in hit[1:]:
            parent[find(i)] = find(hit[0])
    groups: dict[int, dict] = {}
    for i, (layer, p) in enumerate(polys):
        groups.setdefault(find(i), {}).setdefault(layer, []).append(p)
    return list(groups.values())


_LAYER_RE = re.compile(r" on ([A-Za-z0-9]+\.Cu)\b")


def pick_island(islands: list[dict], pos, msg: str = "") -> int | None:
    """The island a DRC item names: nearest to its position, on the layer
    the message names when it names one (a zone is reported by a corner)."""
    if not pos or not islands:
        return None
    pt = Point(pos)
    m = _LAYER_RE.search(msg or "")
    hint = m.group(1) if m and "F.Cu - B.Cu" not in (msg or "") else None
    best, best_d = None, float("inf")
    for i, isl in enumerate(islands):
        for layer, ps in isl.items():
            if hint and layer != hint:
                continue
            d = min(p.distance(pt) for p in ps)
            if d < best_d:
                best, best_d = i, d
    if best is None and hint:
        return pick_island(islands, pos, "")
    return best


# -------------------------------------------------------------- obstacles

@dataclass
class Item:
    net: str
    layers: frozenset
    shape: object          # centreline / point / polygon
    half: float            # copper beyond the shape (track/via radius)
    hole: object = None    # drill extent polygon, or None
    conn: int = -1         # route_finish connection that added it
    uuid: str | None = None  # a board track or via route_finish may rip


class Obstacles:
    """The board's copper and holes, indexed for windowed rasterising."""

    def __init__(self, bg: geom.BoardGeom):
        self.bg = bg
        cu = frozenset(bg.copper_layers)
        items: list[Item] = []
        for t in bg.tracks_of():
            items.append(Item(t.net or "", frozenset({t.layer}), t.shape,
                              t.width / 2, uuid=t.uuid))
        for v in bg.vias_of():
            items.append(Item(v.net or "", frozenset(v.layers) or cu,
                              Point(v.at), v.diameter / 2,
                              Point(v.at).buffer(v.drill / 2, quad_segs=4),
                              uuid=v.uuid))
        for p in bg.pads_of():
            dp = p.drill_poly if p.drill else None
            items.append(Item(p.net or "", frozenset(p.layers) & cu, p.poly,
                              0.0, dp if dp is not None and not dp.is_empty
                              else None))
        self.items = items
        self.tree = STRtree([shapely.envelope(
            it.shape if it.hole is None else shapely.union(it.shape, it.hole))
            for it in items])
        self.added: list[Item] = []
        self.hidden: set = set()        # board copper ripped this pass
        self.soft_board = None   # set of nets that stay walls: every other
        #                          net's board tracks/vias cost, not block
        self.fills = [(z.net, layer, p) for z in bg.zones_of()
                      for layer, ps in z.fills.items() for p in ps]
        self.fill_tree = STRtree([p for _, _, p in self.fills]) \
            if self.fills else None

    def near(self, win):
        out = [self.items[i] for i in self.tree.query(win)
               if self.items[i].uuid not in self.hidden]

        out += [it for it in self.added if it.shape.intersects(win)
                or it.shape.distance(win) < 2.0]
        return out

    def fills_near(self, win):
        if self.fill_tree is None:
            return []
        return [self.fills[i] for i in self.fill_tree.query(win)]


# ------------------------------------------------------------------- grid

@dataclass
class Grid:
    x0: float
    y0: float
    nx: int
    ny: int
    g: float
    layers: list            # routable layer names, index = k
    blk: list               # per k: bytes, 1 = no track centre here
    vblk: bytes             # 1 = no via centre here
    via: tuple              # (size, drill) the via mask was built for
    cost: list              # per k: list of float step multipliers
    in_a: list              # per k: bytes, cell inside side A's copper
    in_b: list
    a_plane: bytes          # cell inside side A's copper on a plane layer
    b_plane: bytes

    def xy(self, i: int, j: int) -> tuple[float, float]:
        return (round(self.x0 + i * self.g, 4), round(self.y0 + j * self.g, 4))


def _mask(shape, r, X, Y, P, x0, y0, g, nx, ny):
    """Bool mask of grid points within r of shape (sub-window only)."""
    b = shape.bounds
    i0 = max(0, int(math.floor((b[0] - r - x0) / g)))
    i1 = min(nx - 1, int(math.ceil((b[2] + r - x0) / g)))
    j0 = max(0, int(math.floor((b[1] - r - y0) / g)))
    j1 = min(ny - 1, int(math.ceil((b[3] + r - y0) / g)))
    if i0 > i1 or j0 > j1:
        return None
    sub = P[j0:j1 + 1, i0:i1 + 1]
    return (slice(j0, j1 + 1), slice(i0, i1 + 1)), shapely.dwithin(shape, sub, r)


def build_grid(obs: Obstacles, rules: Rules, net: str, side_a: dict,
               side_b: dict, layers: list, window, g: float,
               pour_cost: float = 3.0, inner_cost: float = 2.0,
               width: float | None = None, via=None,
               rip_cost: float = 40.0) -> Grid:
    bg = obs.bg
    minx, miny, maxx, maxy = window
    x0 = math.floor(minx / g) * g
    y0 = math.floor(miny / g) * g
    nx = int(math.ceil((maxx - x0) / g)) + 1
    ny = int(math.ceil((maxy - y0) / g)) + 1
    xs = x0 + np.arange(nx) * g
    ys = y0 + np.arange(ny) * g
    X, Y = np.meshgrid(xs, ys)
    P = shapely.points(X, Y)
    win = box(x0 - 1, y0 - 1, x0 + nx * g + 1, y0 + ny * g + 1)
    w = rules.width(net) if width is None else width
    via_size, via_drill = via or (rules.via_size, rules.via_drill)
    vr = via_size / 2
    K = len(layers)
    blk = [np.zeros((ny, nx), bool) for _ in range(K)]
    vblk = np.zeros((ny, nx), bool)
    cost = [np.full((ny, nx), inner_cost if layer not in (
        bg.copper_layers[0], bg.copper_layers[-1]) else 1.0) for layer in layers]
    args = (X, Y, P, x0, y0, g, nx, ny)

    soft = obs.soft_board
    for it in obs.near(win):
        other = it.net != net
        if other and soft is not None and it.uuid and it.net not in soft:
            # crossing it means ripping it: passable, at a price
            on = [k for k, layer in enumerate(layers) if layer in it.layers]
            m = _mask(it.shape, it.half + w / 2 + rules.clr(net, it.net),
                      *args)
            if m:
                for k in on:
                    cost[k][m[0]][m[1]] += rip_cost
            other = False   # its copper is passable; its hole still counts
        if other:
            c = rules.clr(net, it.net)
            on = [k for k, layer in enumerate(layers) if layer in it.layers]
            if on:
                m = _mask(it.shape, it.half + w / 2 + c, *args)
                if m:
                    for k in on:
                        blk[k][m[0]] |= m[1]
            if it.layers:
                # the via's ring keeps the clearance, its hole the hole
                # clearance, from other-net copper
                m = _mask(it.shape, it.half + max(
                    vr + c, via_drill / 2 + rules.hole_clearance
                    + rules.margin), *args)
                if m:
                    vblk[m[0]] |= m[1]
        if it.hole is not None:
            if other:
                m = _mask(it.hole, rules.hole_clearance + w / 2 + rules.margin,
                          *args)
                if m:
                    for k in range(K):
                        blk[k][m[0]] |= m[1]
                m = _mask(it.hole, rules.hole_clearance + vr + rules.margin,
                          *args)
                if m:
                    vblk[m[0]] |= m[1]
            m = _mask(it.hole, rules.hole_to_hole + via_drill / 2
                      + rules.margin, *args)
            if m:
                vblk[m[0]] |= m[1]

    for znet, layer, poly in obs.fills_near(win):
        if znet == net or layer not in layers:
            continue
        k = layers.index(layer)
        inside = shapely.contains_xy(poly, X, Y)
        cost[k][inside] *= pour_cost

    for ra in bg.rule_areas:
        fl = ra.get("flags", {})
        poly = ra["outline"]
        if poly.is_empty or not poly.intersects(win):
            continue
        if fl.get("tracks") == "not_allowed":
            for k, layer in enumerate(layers):
                if layer in ra["layers"]:
                    m = _mask(poly, w / 2 + rules.margin, *args)
                    if m:
                        blk[k][m[0]] |= m[1]
        if fl.get("vias") == "not_allowed":
            m = _mask(poly, vr + rules.margin, *args)
            if m:
                vblk[m[0]] |= m[1]

    outline = bg.outline
    if outline is not None and not outline.is_empty:
        tin = shapely.contains_xy(
            outline.buffer(-(rules.edge_clearance + w / 2 + rules.margin)), X, Y)
        vin = shapely.contains_xy(
            outline.buffer(-(rules.edge_clearance + vr + rules.margin)), X, Y)
        for k in range(K):
            blk[k] |= ~tin
        vblk |= ~vin

    def side(sd, plane):
        if plane:
            polys = [p for layer, ps in sd.items() if layer not in layers
                     for p in ps]
            if not polys:
                return np.zeros((ny, nx), bool)
            return shapely.contains_xy(shapely.union_all(polys), X, Y)
        out = []
        for layer in layers:
            ps = sd.get(layer, [])
            out.append(shapely.contains_xy(shapely.union_all(ps), X, Y)
                       if ps else np.zeros((ny, nx), bool))
        return out

    a, b = side(side_a, False), side(side_b, False)
    return Grid(
        x0=x0, y0=y0, nx=nx, ny=ny, g=g, layers=list(layers),
        blk=[m.ravel().astype(np.uint8).tobytes() for m in blk],
        vblk=vblk.ravel().astype(np.uint8).tobytes(),
        via=(via_size, via_drill),
        cost=[c.ravel().tolist() for c in cost],
        in_a=[m.ravel().astype(np.uint8).tobytes() for m in a],
        in_b=[m.ravel().astype(np.uint8).tobytes() for m in b],
        a_plane=side(side_a, True).ravel().astype(np.uint8).tobytes(),
        b_plane=side(side_b, True).ravel().astype(np.uint8).tobytes())


_MOVES = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
          (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)),
          (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2))]


def astar(gr: Grid, target, via_cost: float = 15.0,
          max_nodes: int = 3_000_000):
    """Multi-source search from side A's free cells to side B's.

    Nodes are (cell, k); a via (layer change, or a drop into a plane-layer
    side) costs via_cost grid steps. Returns [(i, j, k, via_here)] from A to
    B, where a final/initial entry with k = -1 is a via into a plane, or None.
    """
    nx, ny, K = gr.nx, gr.ny, len(gr.layers)
    N = nx * ny
    ti, tj = target
    dist: dict[int, float] = {}
    prev: dict[int, int] = {}
    pq: list = []

    def h(c):
        di, dj = abs(c % nx - ti), abs(c // nx - tj)
        return max(di, dj) + (math.sqrt(2) - 1) * min(di, dj)

    def via_ok(c):
        if gr.vblk[c]:
            return False
        return not any(gr.blk[k][c] for k in range(K))

    START = -1
    for k in range(K):
        ina, blk = gr.in_a[k], gr.blk[k]
        for c in range(N):
            if ina[c] and not blk[c]:
                n = k * N + c
                dist[n] = 0.0
                prev[n] = START
                heapq.heappush(pq, (h(c), 0.0, n))
    for c in range(N):
        if gr.a_plane[c] and via_ok(c):
            for k in range(K):
                n = k * N + c
                if via_cost < dist.get(n, 1e18):
                    dist[n] = via_cost
                    prev[n] = -2 - c          # starts with a via at c
                    heapq.heappush(pq, (via_cost + h(c), via_cost, n))
    expanded = 0
    found = None
    while pq:
        f, gcost, n = heapq.heappop(pq)
        if n < 0:                      # via drop into side B's plane
            found = n
            break
        if gcost > dist.get(n, 1e18):
            continue
        k, c = divmod(n, N)
        if gr.in_b[k][c]:
            found = n
            break
        expanded += 1
        if expanded > max_nodes:
            break
        i, j = c % nx, c // nx
        blk, cost = gr.blk[k], gr.cost[k]
        for di, dj, step in _MOVES:
            ii, jj = i + di, j + dj
            if not (0 <= ii < nx and 0 <= jj < ny):
                continue
            c2 = jj * nx + ii
            if blk[c2]:
                continue
            if di and dj and (blk[j * nx + ii] and blk[jj * nx + i]):
                continue               # no squeezing between two walls
            ng = gcost + step * cost[c2]
            n2 = k * N + c2
            if ng < dist.get(n2, 1e18):
                dist[n2] = ng
                prev[n2] = n
                heapq.heappush(pq, (ng + h(c2), ng, n2))
        if via_ok(c):
            for k2 in range(K):
                if k2 == k:
                    continue
                n2 = k2 * N + c
                ng = gcost + via_cost
                if ng < dist.get(n2, 1e18):
                    dist[n2] = ng
                    prev[n2] = n
                    heapq.heappush(pq, (ng + h(c), ng, n2))
            if gr.b_plane[c]:
                n2 = -2 - (k * N + c)
                ng = gcost + via_cost
                if ng < dist.get(n2, 1e18):
                    dist[n2] = ng
                    prev[n2] = n
                    heapq.heappush(pq, (ng, ng, n2))
    if found is None:
        return None
    path = []
    n = found
    if n < 0:                          # ends with a via into B's plane
        last = -2 - n
        k, c = divmod(last, N)
        path.append((c % nx, c // nx, -1))
        n = last
    while True:
        k, c = divmod(n, N)
        path.append((c % nx, c // nx, k))
        p = prev[n]
        if p == START:
            break
        if p <= -2:                    # started with a via at c
            path.append((c % nx, c // nx, -1))
            break
        n = p
    path.reverse()
    return path


def path_to_ops(gr: Grid, path, net: str, width: float, rules: Rules) -> list:
    """Grid path -> route_edit ops: one track per straight run, a via at
    every layer change and at a plane drop (k = -1)."""
    ops = []
    vias = []
    runs: list[list] = []
    for i, j, k in path:
        if k == -1:
            vias.append(gr.xy(i, j))
            continue
        if runs and runs[-1][0][2] == k:
            runs[-1].append((i, j, k))
        else:
            if runs:
                vias.append(gr.xy(i, j))
            runs.append([(i, j, k)])
    for run in runs:
        corners = [run[0]]
        for q0, q1, q2 in zip(run, run[1:], run[2:]):
            if (q1[0] - q0[0], q1[1] - q0[1]) != (q2[0] - q1[0], q2[1] - q1[1]):
                corners.append(q1)
        if len(run) > 1:
            corners.append(run[-1])
        layer = gr.layers[run[0][2]]
        for c0, c1 in zip(corners, corners[1:]):
            if c0[:2] == c1[:2]:
                continue
            ops.append({"op": "add_track", "start": list(gr.xy(*c0[:2])),
                        "end": list(gr.xy(*c1[:2])), "width": width,
                        "layer": layer, "net": net})
    seen = set()
    for at in vias:
        if at in seen:
            continue
        seen.add(at)
        ops.append({"op": "add_via", "at": list(at), "size": gr.via[0],
                    "drill": gr.via[1], "net": net})
    return ops


def ops_items(ops: list, conn: int, copper_layers) -> list[Item]:
    out = []
    for op in ops:
        if op["op"] == "add_track":
            out.append(Item(op["net"], frozenset({op["layer"]}),
                            LineString([op["start"], op["end"]]),
                            op["width"] / 2, conn=conn))
        else:
            out.append(Item(op["net"], frozenset(copper_layers),
                            Point(op["at"]), op["size"] / 2,
                            Point(op["at"]).buffer(op["drill"] / 2,
                                                   quad_segs=4), conn=conn))
    return out


# ----------------------------------------------------------------- driver

def routable_layers(bg: geom.BoardGeom, spec: str = "auto") -> list[str]:
    """auto: every copper layer but inner planes (a fill and no tracks)."""
    if spec and spec != "auto":
        want = [s.strip() for s in spec.split(",") if s.strip()]
        bad = [w for w in want if w not in bg.copper_layers]
        if bad:
            raise CheckError(f"not copper layers of this board: {bad}")
        return want
    import route_auto
    planes = set(route_auto._auto_power_layers(bg))
    return [layer for layer in bg.copper_layers if layer not in planes]


def unconnected_pairs(drc: dict, nets: set | None = None) -> list[dict]:
    out = []
    for v in drc["violations"]:
        if v.get("source") != "unconnected" or not v.get("net"):
            continue
        if nets and v["net"] not in nets:
            continue
        items = v.get("items") or []
        if len(items) < 2:
            continue
        out.append({"net": v["net"], "a": items[0], "b": items[1]})
    return out


def err_counts(report: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in report["violations"]:
        if v.get("severity") == "error" and v.get("source") != "unconnected":
            key = f"{v.get('source')}:{v.get('check')}"
            out[key] = out.get(key, 0) + 1
    return out


def unconnected_by_net(report: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in report["violations"]:
        if v.get("source") == "unconnected":
            out[v.get("net") or ""] = out.get(v.get("net") or "", 0) + 1
    return out


def plan_routes(bg: geom.BoardGeom, rules: Rules, pairs: list[dict],
                layers: list[str], *, grid: float = 0.1, margin: float = 4.0,
                max_conns: int | None = None, max_nodes: int = 3_000_000,
                via_cost_mm: float = 1.5, rip_board: bool = False,
                rip_board_nets: int = 4, ripped_out: list | None = None,
                log=None) -> list[dict]:
    """Find a route for each pair, shortest first; each found route is an
    obstacle for the ones after it. Returns [{net, ops, status, ...}]."""
    obs = Obstacles(bg)
    isl_cache: dict[str, list] = {}
    jobs = []
    seen: set = set()

    def add_job(net, isl, ia, ib, a_pos, b_pos):
        job = {"net": net, "a": a_pos, "b": b_pos}
        if ia is None or ib is None:
            job.update(status="skipped", reason="item not on the net's copper")
        elif ia == ib:
            job.update(status="skipped",
                       reason="geometry already joins both items")
        elif (net, min(ia, ib), max(ia, ib)) in seen:
            return
        else:
            seen.add((net, min(ia, ib), max(ia, ib)))
            ua = shapely.union_all([p for ps in isl[ia].values() for p in ps])
            ub = shapely.union_all([p for ps in isl[ib].values() for p in ps])
            pa, pb = shapely.ops.nearest_points(ua, ub)
            job.update(side_a=isl[ia], side_b=isl[ib], pa=(pa.x, pa.y),
                       pb=(pb.x, pb.y), dist=pa.distance(pb))
            job["a"], job["b"] = [round(pa.x, 3), round(pa.y, 3)], \
                [round(pb.x, 3), round(pb.y, 3)]
        jobs.append(job)

    zone_nets = []
    for pr in pairs:
        net = pr["net"]
        if net not in isl_cache:
            isl_cache[net] = net_islands(bg, net)
        isl = isl_cache[net]
        if any(pr[s].get("msg", "").startswith("Zone") for s in ("a", "b")):
            # KiCad names a zone by its outline's first corner, not by the
            # fill piece that is cut off: join every island to the largest
            if net not in zone_nets:
                zone_nets.append(net)
            continue
        ia = pick_island(isl, pr["a"].get("pos"), pr["a"].get("msg", ""))
        ib = pick_island(isl, pr["b"].get("pos"), pr["b"].get("msg", ""))
        add_job(net, isl, ia, ib, pr["a"].get("pos"), pr["b"].get("pos"))
    for net in zone_nets:
        isl = isl_cache[net]
        area = [sum(p.area for ps in i.values() for p in ps) for i in isl]
        main = max(range(len(isl)), key=lambda i: area[i])
        for i in range(len(isl)):
            if i != main:
                add_job(net, isl, i, main, None, None)
    todo = sorted((j for j in jobs if "side_a" in j), key=lambda j: j["dist"])
    if max_conns is not None:
        for j in todo[max_conns:]:
            j.update(status="skipped", reason="over --max-conns")
        todo = todo[:max_conns]
    def search(job, hide=False, board=False):
        """(path, grid, width) over the ladder: the window, a wider window,
        then a finer grid at the net's floor width for a pad boxed in by
        its neighbours. hide: leave this pass's own routes out."""
        net = job["net"]
        width = rules.width(net)
        held = obs.added
        if hide:
            obs.added = []
        if board:
            obs.soft_board = keep_nets
        try:
            for m, g, w, v in (
                    (margin, grid, width, None),
                    (margin * 2.5, grid, width, None),
                    (margin, grid / 2, rules.width_floor(net), rules.via_floor)):
                (ax, ay), (bx, by) = job["pa"], job["pb"]
                win = (min(ax, bx) - m, min(ay, by) - m,
                       max(ax, bx) + m, max(ay, by) + m)
                gr = build_grid(obs, rules, net, job["side_a"],
                                job["side_b"], layers, win, g, width=w,
                                via=v)
                target = (int(round((bx - gr.x0) / g)),
                          int(round((by - gr.y0) / g)))
                path = astar(gr, target, via_cost=via_cost_mm / g,
                             max_nodes=max_nodes)
                if path:
                    return path, gr, w
        finally:
            obs.added = held
            obs.soft_board = None
        return None, None, None

    def commit(n, job, path, gr, w):
        ops = path_to_ops(gr, path, job["net"], w, rules)
        job.update(status="routed", ops=ops, width=w,
                   vias=sum(op["op"] == "add_via" for op in ops))
        obs.added.extend(ops_items(ops, n, bg.copper_layers))

    # power and pour nets are never ripped: their copper is sized by rule
    keep_nets = {z.net for z in bg.zones_of()} | set(rules.net_width) | {""}
    queue = list(range(len(todo)))
    ripped_once: set[int] = set()
    ripped_board: set[int] = set()
    steps = 0
    while queue:
        n = queue.pop(0)
        job = todo[n]
        steps += 1
        path, gr, w = search(job)
        if path:
            commit(n, job, path, gr, w)
        elif n not in ripped_once:
            # rip-up and reroute: when only this pass's own routes are in
            # the way, take out the ones the free path runs into, route
            # this one, and queue them again (each job rips once)
            ripped_once.add(n)
            path, gr, w = search(job, hide=True)
            if path:
                mine = ops_items(path_to_ops(gr, path, job["net"], w, rules),
                                 n, bg.copper_layers)
                block = set()
                for it in obs.added:
                    if it.net == job["net"]:
                        continue
                    c = rules.clr(job["net"], it.net)
                    for m in mine:
                        if not (it.layers & m.layers):
                            continue
                        if it.shape.distance(m.shape) < it.half + m.half + c:
                            block.add(it.conn)
                            break
                if block:
                    obs.added = [it for it in obs.added
                                 if it.conn not in block]
                    for c in sorted(block):
                        todo[c].update(status="ripped")
                        todo[c].pop("ops", None)
                        queue.append(c)
                    commit(n, job, path, gr, w)
                    job["ripped"] = len(block)
        if job.get("status") != "routed" and rip_board \
                and n not in ripped_board:
            # still walled in: the tracks and vias of other nets in the
            # way (pads, pours and holes stay) are ripped, and their nets
            # come back unconnected for the next pass to route again
            ripped_board.add(n)
            path, gr, w = search(job, hide=True, board=True)
            if path:
                mine = ops_items(path_to_ops(gr, path, job["net"], w, rules),
                                 n, bg.copper_layers)
                rip, nets = [], set()
                win = shapely.union_all([m.shape for m in mine]).buffer(1.0)
                for it in obs.near(win) + obs.added:
                    if it.net == job["net"]:
                        continue
                    c = rules.clr(job["net"], it.net)
                    if any(((it.layers & m.layers) and it.shape.distance(
                            m.shape) < it.half + m.half + c) or (
                            it.hole is not None and m.hole is not None
                            and it.hole.distance(m.hole)
                            < rules.hole_to_hole + rules.margin)
                            for m in mine):
                        if it.uuid:
                            rip.append(it.uuid)
                            nets.add(it.net)
                        elif it.conn >= 0:
                            rip.append(("conn", it.conn))
                own = {u[1] for u in rip if isinstance(u, tuple)}
                board_rip = [u for u in rip if not isinstance(u, tuple)]
                fixed = sorted(nt for nt in nets if nt in keep_nets)
                if fixed or len(nets) > rip_board_nets:
                    job["rip_refused"] = fixed or sorted(nets)
                else:
                    obs.hidden.update(board_rip)
                    obs.added = [it for it in obs.added if it.conn not in own]
                    for c in sorted(own):
                        todo[c].update(status="ripped")
                        todo[c].pop("ops", None)
                        queue.append(c)
                    commit(n, job, path, gr, w)
                    job["rip_uuids"] = sorted(set(board_rip))
                    job["ripped_nets"] = sorted(nets)
        if job.get("status") not in ("routed",):
            job.update(status="failed", reason="no path on the grid")
        if log:
            log(f"{steps} {job['net']}: {job['status']}")
    for j in jobs:
        for k in ("side_a", "side_b"):
            j.pop(k, None)
    if ripped_out is not None:
        ripped_out.extend(sorted(obs.hidden))
    return jobs


def _named_conns(after: dict, uuid_conn: dict) -> dict[int, list]:
    """Connections whose copper DRC names in an error -> the checks."""
    bad: dict[int, list] = {}
    for v in after["violations"]:
        if v.get("severity") != "error" or v.get("source") == "unconnected":
            continue
        for it in v.get("items", []):
            if it.get("uuid") in uuid_conn:
                checks = bad.setdefault(uuid_conn[it["uuid"]], [])
                if v.get("check") not in checks:
                    checks.append(v.get("check"))
    return bad


def _one_pass(cli, staged, drc, opts, *, parity, refill, log):
    """Plan, apply, DRC, take out connections DRC names in an error.
    Returns (jobs, drc_after or None when nothing was applied)."""
    import route_edit

    bg = geom.BoardGeom.from_file(staged)
    rules = board_rules(staged, bg, min_width=opts["min_width"],
                        margin=opts["clearance_margin"],
                        via_size=opts["via_size"], via_drill=opts["via_drill"])
    lays = routable_layers(bg, opts["layers"])
    pairs = unconnected_pairs(drc, opts["nets"])
    rip: list = []
    jobs = plan_routes(bg, rules, pairs, lays, grid=opts["grid"],
                       margin=opts["margin"], max_conns=opts["max_conns"],
                       rip_board=opts["rip_board"], ripped_out=rip, log=log)
    all_ops, op_conn = [], []
    for c, j in enumerate(jobs):
        if j["status"] == "routed":
            all_ops += j["ops"]
            op_conn += [c] * len(j["ops"])
    if not all_ops:
        return jobs, None, rules, lays
    res = route_edit.apply_ops(staged, all_ops + [
        {"op": "remove", "uuid": u} for u in rip])
    uuid_conn: dict[str, int] = {}
    conn_uuids: dict[int, list] = {}
    for c, r in zip(op_conn, res):
        if r.get("uuid"):
            uuid_conn[r["uuid"]] = c
            conn_uuids.setdefault(c, []).append(r["uuid"])
    after = None
    for _ in range(3):
        if refill:
            kc.run_drc(cli, staged, refill=True, save_board=True)
        after = kc.run_drc(cli, staged, parity=parity, all_track_errors=True)
        bad = _named_conns(after, uuid_conn)
        if not bad:
            break
        route_edit.apply_ops(staged, [{"op": "remove", "uuid": u}
                                      for c in bad for u in conn_uuids[c]])
        for c in bad:
            jobs[c].update(status="rolled_back",
                           reason="DRC after refill: " + ", ".join(bad[c]))
            for u in conn_uuids.pop(c):
                uuid_conn.pop(u, None)
        after = None
    if after is None:
        if refill:
            kc.run_drc(cli, staged, refill=True, save_board=True)
        after = kc.run_drc(cli, staged, parity=parity, all_track_errors=True)
    return jobs, after, rules, lays


def finish_staged(cli: Path, staged: Path, drc: dict, *, nets=None,
                  layers: str = "auto", grid: float = 0.1,
                  margin: float = 4.0, min_width: float = 0.2,
                  clearance_margin: float = 0.02, via_size=None,
                  via_drill=None, max_conns=None, parity: bool = False,
                  refill: bool = True, dry_run: bool = False,
                  passes: int = 4, rip_errors: bool = False,
                  rip_board: bool = True,
                  log=None) -> dict:
    """Route the staged board's unconnected pairs in place, in passes: a
    route can split another net's pour, and the next pass joins the piece.
    A pass is kept only when the unconnected count drops and no error type
    grows against the start of the pass. Returns facts with "kept" and,
    when kept, "drc" (the report after)."""
    import shutil

    opts = dict(nets=set(nets) if nets else None, layers=layers, grid=grid,
                margin=margin, min_width=min_width,
                clearance_margin=clearance_margin, via_size=via_size,
                via_drill=via_drill, max_conns=max_conns,
                rip_board=rip_board)
    start_un = sum(unconnected_by_net(drc).values())
    facts: dict = {"unconnected_before": start_un,
                   "errors_before": err_counts(drc), "passes": []}
    if dry_run:
        bg = geom.BoardGeom.from_file(staged)
        rules = board_rules(staged, bg, min_width=min_width,
                            margin=clearance_margin, via_size=via_size,
                            via_drill=via_drill)
        lays = routable_layers(bg, layers)
        jobs = plan_routes(bg, rules, unconnected_pairs(drc, opts["nets"]),
                           lays, grid=grid, margin=margin,
                           max_conns=max_conns, rip_board=rip_board, log=log)
        facts.update(kept=False, rules=_rules_facts(rules), layers=lays,
                     connections=jobs,
                     routed=sum(j["status"] == "routed" for j in jobs))
        return facts
    backup = staged.with_name("prefinish_maze_" + staged.name)
    origin = staged.with_name("prerip_maze_" + staged.name)
    cur = drc
    kept_any = False
    checkpoint = None
    touched: set = set()   # nets whose board copper a pass ripped
    rip_nets: set = set()
    ckpt_file = staged.with_name("ckpt_maze_" + staged.name)
    ripped = []
    if rip_errors:
        # an autorouter's shorts and clearance misses: take out the tracks
        # and vias DRC names, and route those connections again
        ripped = _error_copper(staged, drc)
        rip_nets = {v.get("net") for v in drc["violations"]
                    if v.get("check") in RIP_CHECKS and v.get("net")}
        if ripped:
            import route_edit
            shutil.copy2(staged, origin)
            try:
                route_edit.apply_ops(staged, [{"op": "remove", "uuid": u}
                                              for u in ripped])
                if refill:
                    kc.run_drc(cli, staged, refill=True, save_board=True)
                cur = kc.run_drc(cli, staged, parity=parity,
                                 all_track_errors=True)
            except CheckError as exc:
                shutil.copy2(origin, staged)
                origin.unlink(missing_ok=True)
                facts["rip_error"] = str(exc)[:300]
                ripped, cur = [], drc
        facts["ripped"] = len(ripped)
    try:
        for n in range(passes):
            if not unconnected_pairs(cur, opts["nets"]):
                break
            shutil.copy2(staged, backup)
            try:
                jobs, after, rules, lays = _one_pass(
                    cli, staged, cur, opts, parity=parity, refill=refill,
                    log=log)
            except CheckError as exc:
                shutil.copy2(backup, staged)
                facts["passes"].append({"error": str(exc)[:300]})
                break
            facts.setdefault("rules", _rules_facts(rules))
            facts.setdefault("layers", lays)
            st: dict[str, int] = {}
            for j in jobs:
                st[j["status"]] = st.get(j["status"], 0) + 1
            entry = {"pass": n + 1, "pairs": len(jobs), "by_status": st,
                     "connections": [{k: v for k, v in j.items() if k != "ops"}
                                     for j in jobs]}
            facts["passes"].append(entry)
            if after is None:
                break
            b_un = sum(unconnected_by_net(cur).values())
            a_un = sum(unconnected_by_net(after).values())
            b_err, a_err = err_counts(cur), err_counts(after)
            grew = {k: [b_err.get(k, 0), v] for k, v in a_err.items()
                    if v > b_err.get(k, 0)}
            entry.update(unconnected_before=b_un, unconnected_after=a_un,
                         grew=grew)
            board_rip = any(j.get("rip_uuids") for j in jobs)
            for j in jobs:
                touched.update(j.get("ripped_nets", []))
                if board_rip and j["status"] == "routed":
                    touched.add(j["net"])
            if a_un < b_un and not grew:
                entry["kept"] = True
                cur = after
                kept_any = True
            elif board_rip and not grew and n + 1 < passes:
                # ripping other nets' copper opens them up: the next passes
                # route them again, and the whole run must beat the board as
                # it was before this pass, or it goes back to that
                entry["kept"] = "provisional"
                if checkpoint is None:
                    shutil.copy2(backup, ckpt_file)
                    checkpoint = (cur, kept_any)
                cur = after
            else:
                entry["kept"] = False
                shutil.copy2(backup, staged)
                break
        if checkpoint is not None:
            c_drc, c_kept = checkpoint
            c_un = sum(unconnected_by_net(c_drc).values())
            c_err, a_err = err_counts(c_drc), err_counts(cur)
            if sum(unconnected_by_net(cur).values()) >= c_un or any(
                    v > c_err.get(k, 0) for k, v in a_err.items()):
                shutil.copy2(ckpt_file, staged)
                cur, kept_any = c_drc, c_kept
                facts["provisional_undone"] = True
            else:
                kept_any = True
    finally:
        backup.unlink(missing_ok=True)
        ckpt_file.unlink(missing_ok=True)
    if kept_any and (ripped or touched):
        cur = _sweep_dangling(cli, staged, touched | rip_nets, cur,
                              parity=parity, refill=refill, facts=facts)
    if ripped:
        # the rip is kept only if the board ends up better than it started:
        # no error type grows, and errors + unconnected drop
        b_err, a_err = err_counts(drc), err_counts(cur)
        b_tot = sum(b_err.values()) + start_un
        a_tot = sum(a_err.values()) + sum(unconnected_by_net(cur).values())
        grew = [k for k, v in a_err.items() if v > b_err.get(k, 0)]
        if grew or a_tot >= b_tot:
            shutil.copy2(origin, staged)
            cur, kept_any = drc, False
            facts["rip_kept"] = False
        else:
            kept_any = True
            facts["rip_kept"] = True
        origin.unlink(missing_ok=True)
    facts.update(kept=kept_any,
                 unconnected_after=sum(unconnected_by_net(cur).values()),
                 errors_after=err_counts(cur))
    if kept_any:
        facts["drc"] = cur
    return facts


def _sweep_dangling(cli, staged, nets, cur, *, parity, refill, facts):
    """Ripping a segment out of another net leaves the rest of its old path
    hanging: remove the free-ended tracks and vias of the nets touched (the
    route_cleanup dangling pass), kept only if DRC holds."""
    import route_cleanup
    import route_edit
    import shutil

    bg = geom.BoardGeom.from_file(staged)
    segs, vias = route_cleanup.parse_items(staged, bg.copper_layers)[:2]
    pads = [route_cleanup.PadItem(p.net, tuple(p.layers), p.center, p.poly)
            for p in bg.pads_of()]
    cache: dict = {}

    def fillfn(net, layer):
        if (net, layer) not in cache:
            cache[(net, layer)] = bg.zone_fill(net, layer)
        return cache[(net, layer)]

    segs = [x for x in segs if x.net in nets]
    vias = [x for x in vias if x.net in nets]
    gone_s, gone_v = route_cleanup.find_dangling(segs, vias, pads, fillfn)
    if not gone_s and not gone_v:
        return cur
    keep = staged.with_name("predangle_maze_" + staged.name)
    shutil.copy2(staged, keep)
    try:
        route_edit.apply_ops(staged, [{"op": "remove", "uuid": u}
                                      for u in gone_s + gone_v])
        if refill:
            kc.run_drc(cli, staged, refill=True, save_board=True)
        after = kc.run_drc(cli, staged, parity=parity, all_track_errors=True)
        b_err, a_err = err_counts(cur), err_counts(after)
        if sum(unconnected_by_net(after).values()) > sum(
                unconnected_by_net(cur).values()) or any(
                v > b_err.get(k, 0) for k, v in a_err.items()):
            shutil.copy2(keep, staged)
            facts["dangling_removed"] = 0
            return cur
        facts["dangling_removed"] = len(gone_s) + len(gone_v)
        return after
    except CheckError as exc:
        shutil.copy2(keep, staged)
        facts["dangling_error"] = str(exc)[:200]
        return cur
    finally:
        keep.unlink(missing_ok=True)


RIP_CHECKS = {"shorting_items", "clearance", "tracks_crossing",
              "hole_clearance", "hole_to_hole", "track_width"}


def _error_copper(staged: Path, drc: dict) -> list[str]:
    """uuids of the tracks and vias DRC names in a copper error."""
    bg = geom.BoardGeom.from_file(staged)
    mine = {t.uuid for t in bg.tracks_of()} | {v.uuid for v in bg.vias_of()}
    out: list[str] = []
    for v in drc["violations"]:
        if v.get("severity") != "error" or v.get("check") not in RIP_CHECKS:
            continue
        for it in v.get("items", []):
            u = it.get("uuid")
            if u in mine and u not in out and (
                    it.get("msg", "").startswith(("Track", "Via"))):
                out.append(u)
    return out


def _rules_facts(rules: Rules) -> dict:
    return {k: v for k, v in rules.__dict__.items() if not isinstance(v, dict)}


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--nets", default=None, help="only these nets (comma list)")
    ap.add_argument("--layers", default="auto")
    ap.add_argument("--grid", type=float, default=0.1)
    ap.add_argument("--margin", type=float, default=4.0,
                    help="search window beyond the two sides, mm")
    ap.add_argument("--min-width", type=float, default=0.2)
    ap.add_argument("--clearance-margin", type=float, default=0.02)
    ap.add_argument("--via-size", type=float, default=None)
    ap.add_argument("--via-drill", type=float, default=None)
    ap.add_argument("--max-conns", type=int, default=None)
    ap.add_argument("--rip-errors", action="store_true",
                    help="first take out the tracks/vias DRC names in a short,"
                         " clearance, crossing or width error, and route those"
                         " again (kept only if the board ends better)")
    ap.add_argument("--no-rip-board", action="store_true",
                    help="never rip other nets' existing tracks/vias to free"
                         " a walled-in pad (only this run's own routes)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--out-report", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    cli = env.find_kicad_cli()
    if cli is None:
        raise CheckError("kicad-cli not found (check_env.py)")
    import route_auto
    work = routelib.fresh_work_dir(Path(args.work_dir).resolve()
                                   if args.work_dir
                                   else pcb.parent / "route_finish")
    staged = route_auto._stage_board(pcb, work)
    parity = staged.with_suffix(".kicad_sch").is_file()
    has_zones = bool(geom.BoardGeom.from_file(staged).zones_of())
    if has_zones:
        kc.run_drc(cli, staged, refill=True, save_board=True)
    drc = kc.run_drc(cli, staged, parity=parity, all_track_errors=True)
    log = (lambda s: print(s, file=sys.stderr)) if args.verbose else None
    facts = finish_staged(
        cli, staged, drc,
        nets=args.nets.split(",") if args.nets else None,
        layers=args.layers, grid=args.grid, margin=args.margin,
        min_width=args.min_width, clearance_margin=args.clearance_margin,
        via_size=args.via_size, via_drill=args.via_drill,
        max_conns=args.max_conns, parity=parity, refill=has_zones,
        dry_run=args.dry_run, rip_errors=args.rip_errors,
        rip_board=not args.no_rip_board, log=log)
    final = facts.pop("drc", None) or drc
    if facts.get("kept") and not args.dry_run:
        routelib.swap_in(staged, pcb)
    left = unconnected_pairs(final)
    status = "pass" if final["counts"]["total"] == 0 else "violations"
    payload = {"script": "route_finish", "status": status, "board": str(pcb),
               "counts": final["counts"],
               "violations": final["violations"],
               "facts": {**facts, "unconnected_left": len(left),
                         "work_dir": str(work)}}
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("route_finish", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
