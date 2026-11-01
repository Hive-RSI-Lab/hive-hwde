"""pairlaunch.py - put a diff pair's BGA escape where a coupled router can use it.

route_escape (KRT dog-bone) escapes each ball toward the NEARER field edge, on
whichever layer had room. KRT's route_diff then needs both tips of a terminal
on one layer, side by side (#546 on linux-computer):

  SDQS0: U100 tips on In2 and B.Cu       -> endpoint-resolution-failure
         U200 tips 5 mm apart on two sides
  SDCLK0: U200 tips on the far side of the field from U100 -> no-escape-path
  SDQS1: U100 tips on F.Cu: KRT flips the stubs to In2 and adds pair vias
         sized for ITS transition spacing (0.575), which clash in a 0.8 field

plan_launch() re-lays such a terminal on the pair's stripline layer: the ball's
own via stays (or a via-in-pad is added at the floor via), the old stub goes,
and each net gets a new stub from its via to the field edge, side by side at
the pair's pitch, on the side that is cheapest toward the other terminal. The
two terminals are ordered so the pair needs no P/N crossing.

Pure planning: returns route_edit ops. No KiCad, no KRT.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

GRID = 0.05            # mm, launch search grid (anchored on the ball's via)
EPS = 0.01             # mm, extra inflation so the grid path clears exactly
TOL = 0.02             # mm, same-point tolerance when walking copper
SIDES = {"N": (0.0, -1.0), "E": (1.0, 0.0), "S": (0.0, 1.0), "W": (-1.0, 0.0)}
SQRT2 = math.sqrt(2.0)


@dataclass
class Island:
    """The copper of one net hanging off one ball."""
    net: str
    ball: object
    tracks: list = field(default_factory=list)
    vias: list = field(default_factory=list)
    tips: list = field(default_factory=list)   # (x, y, layer)
    neck: list = field(default_factory=list)   # tracks ball -> first via
    via: object = None                         # first via from the ball
    routed: bool = False                       # reaches another pad of the net


def _ends(track) -> tuple[tuple, tuple]:
    c = list(track.shape.coords)
    return c[0], c[-1]


def _near(a, b, tol: float = TOL) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def island(bg, net: str, ball) -> Island:
    """Walk net's copper out from `ball`: every track and via connected to it
    (a pad centre, a via joining layers), the free tips, and the shortest
    track chain from the ball to the first via."""
    tracks = bg.tracks_of(net)
    vias = bg.vias_of(net)
    start = tuple(ball.center)
    isl = Island(net, ball)
    # KRT's necks can end anywhere on a via's ring (0.026 mm off its centre on
    # linux-computer), so a via or ball joins what ends within its copper.
    # Frontier: (point, layer, how near counts, chain of tracks so far).
    ball_r = max(getattr(ball, "size", (0.0, 0.0))) / 2.0
    frontier = [(start, ly, max(ball_r, TOL), []) for ly in ball.layers]
    seen_t, seen_v = set(), set()
    while frontier:
        pt, ly, near, chain = frontier.pop(0)
        for v in vias:
            if id(v) not in seen_v and ly in v.layers \
                    and _near(v.at, pt, max(v.diameter / 2.0, near)):
                seen_v.add(id(v))
                isl.vias.append(v)
                if isl.via is None:
                    isl.via, isl.neck = v, list(chain)
                frontier += [(tuple(v.at), other, v.diameter / 2.0, chain)
                             for other in v.layers]
        for t in tracks:
            if id(t) in seen_t or t.layer != ly:
                continue
            a, b = _ends(t)
            if _near(a, pt, near):
                far = b
            elif _near(b, pt, near):
                far = a
            else:
                continue
            seen_t.add(id(t))
            isl.tracks.append(t)
            frontier.append((far, ly, TOL, chain + [t]))
    others = [q for q in bg.pads_of(net=net) if q is not ball]
    isl.routed = any(_near(e, q.center, 0.05) and q.on(t.layer)
                     for t in isl.tracks for e in _ends(t) for q in others)
    for t in isl.tracks:
        for e in _ends(t):
            touching = sum(_near(e, q) for u in isl.tracks if u.layer == t.layer
                           for q in _ends(u))
            if touching == 1 and not _near(e, start, max(ball_r, TOL)) \
                    and not any(_near(e, v.at, v.diameter / 2.0) for v in isl.vias):
                isl.tips.append((e[0], e[1], t.layer))
    return isl


def chirality(side: str, p_xy, n_xy) -> int:
    """Which hand P is on, looking out of the field: +1 or -1. Along a pair
    with no crossing P keeps its hand, and the far terminal is entered looking
    the other way, so two terminals of one pair need OPPOSITE chirality."""
    ex, ey = SIDES[side]
    cross = ex * (p_xy[1] - n_xy[1]) - ey * (p_xy[0] - n_xy[0])
    return 1 if cross > 0 else -1


def tip_side(bbox, xy) -> str:
    """The field side a tip left by: the one it is farthest past."""
    x0, y0, x1, y1 = bbox
    past = {"N": y0 - xy[1], "S": xy[1] - y1, "W": x0 - xy[0], "E": xy[0] - x1}
    return max(past, key=past.get)


class Grid:
    """Free cells of the region for one track, anchored so a cell sits on
    the via."""

    def __init__(self, region, anchor, obstacles, inflate: float):
        self.x0 = anchor[0] - math.floor((anchor[0] - region[0]) / GRID) * GRID
        self.y0 = anchor[1] - math.floor((anchor[1] - region[1]) / GRID) * GRID
        self.nx = int(math.floor((region[2] - self.x0) / GRID)) + 1
        self.ny = int(math.floor((region[3] - self.y0) / GRID)) + 1
        xs, ys = self.xy_arrays()
        grown = obstacles.buffer(inflate + EPS) if not obstacles.is_empty else None
        self.free = (np.ones(xs.shape, bool) if grown is None
                     else ~shapely.contains_xy(grown, xs, ys))

    def xy_arrays(self):
        xs = self.x0 + np.arange(self.nx) * GRID
        ys = self.y0 + np.arange(self.ny) * GRID
        return np.meshgrid(xs, ys)

    def xy(self, ix: int, iy: int) -> tuple[float, float]:
        return (round(self.x0 + ix * GRID, 4), round(self.y0 + iy * GRID, 4))

    def cell(self, pt) -> tuple[int, int]:
        return (int(round((pt[0] - self.x0) / GRID)),
                int(round((pt[1] - self.y0) / GRID)))

    def on_side(self, side: str):
        if side == "N":
            return [(ix, 0) for ix in range(self.nx)]
        if side == "S":
            return [(ix, self.ny - 1) for ix in range(self.nx)]
        if side == "W":
            return [(0, iy) for iy in range(self.ny)]
        return [(self.nx - 1, iy) for iy in range(self.ny)]


def dijkstra(grid: Grid, start) -> tuple[dict, dict]:
    """Length to every reachable free cell (8-connected) from `start`, which
    may itself be inside an obstacle's margin only if it is the via."""
    dist, prev = {start: 0.0}, {}
    heap = [(0.0, start)]
    steps = [(1, 0, GRID), (-1, 0, GRID), (0, 1, GRID), (0, -1, GRID),
             (1, 1, GRID * SQRT2), (1, -1, GRID * SQRT2),
             (-1, 1, GRID * SQRT2), (-1, -1, GRID * SQRT2)]
    while heap:
        d, cur = heapq.heappop(heap)
        if d > dist[cur] + 1e-9:
            continue
        for dx, dy, w in steps:
            ix, iy = cur[0] + dx, cur[1] + dy
            if not (0 <= ix < grid.nx and 0 <= iy < grid.ny) or not grid.free[iy, ix]:
                continue
            if d + w < dist.get((ix, iy), math.inf) - 1e-9:
                dist[(ix, iy)] = d + w
                prev[(ix, iy)] = cur
                heapq.heappush(heap, (d + w, (ix, iy)))
    return dist, prev


def path_points(grid: Grid, prev: dict, end, start_xy) -> list[tuple[float, float]]:
    """The route start -> end with collinear runs merged. Grid steps are all
    0/45/90 degrees, and merging keeps them so."""
    cells = [end]
    while cells[-1] in prev:
        cells.append(prev[cells[-1]])
    cells.reverse()
    pts = [grid.xy(*c) for c in cells]
    pts[0] = (round(start_xy[0], 4), round(start_xy[1], 4))
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) > 1e-6:
            out.append(b)
    if len(pts) > 1:
        out.append(pts[-1])
    return out


@dataclass
class Spec:
    """Geometry of one launch, all from the board's rules (mm)."""
    layer: str
    width: float
    pitch: float
    clearance: float
    via_d: float
    via_drill: float
    hole_to_hole: float


def _obstacles(bg, layer: str, skip: tuple[str, str], extra=()):
    parts = [bg.net_copper(net, layer) for net in sorted(bg.nets) if net not in skip]
    parts += list(extra)
    return unary_union([q for q in parts if not q.is_empty])


def via_fits(bg, at, spec: Spec, skip: tuple[str, str], placed=()) -> str | None:
    """None when a floor via fits at `at`; else why not. Checks the via ring
    against foreign copper on every signal layer it crosses and the drill
    against every other drill. `placed` = via centres already planned."""
    at = tuple(at)
    ring = Point(at).buffer(spec.via_d / 2.0)
    pour_layers = {ly for z in bg.zones_of() for ly in z.layers}
    for ly in bg.copper_layers:
        if ly in pour_layers:
            continue   # a pour refills around the via
        for net in sorted(bg.nets):
            if net in skip:
                continue
            q = bg.net_copper(net, ly)
            if not q.is_empty and q.distance(ring) < spec.clearance - 1e-4:
                return f"via ring {q.distance(ring):.3f} mm from {net} on {ly}"
    centres = [(v.at, v.drill) for v in bg.vias_of()] + \
              [(c, spec.via_drill) for c in placed]
    for c, drill in centres:
        if _near(c, at, 1e-3):
            continue
        gap = math.hypot(c[0] - at[0], c[1] - at[1]) - (drill + spec.via_drill) / 2.0
        if gap < spec.hole_to_hole - 1e-4:
            return f"drill {gap:.3f} mm from the drill at {tuple(c)}"
    return None


def _lay_pair(first_xy, second_xy, region, obstacles, spec: Spec,
              side: str, sign: int | None, toward, first_is_p: bool,
              first_via_poly, second_via_poly):
    """Route the first net to its cheapest cell on `side`, then the second to
    the cell one pitch along the side from it. sign = wanted chirality (None =
    either). Returns (cost, first_pts, second_pts) or None."""
    inflate = spec.clearance + spec.width / 2.0
    tang = (abs(SIDES[side][1]), abs(SIDES[side][0]))
    g1 = Grid(region, first_xy,
              unary_union([obstacles, second_via_poly]), inflate)
    d1, prev1 = dijkstra(g1, g1.cell(first_xy))
    cand = sorted((d1[c] + math.hypot(g1.xy(*c)[0] - toward[0],
                                      g1.xy(*c)[1] - toward[1]), c)
                  for c in g1.on_side(side) if c in d1)
    for cost1, end1 in cand[:12]:
        pts1 = path_points(g1, prev1, end1, first_xy)
        line1 = LineString(pts1).buffer(spec.width / 2.0)
        if line1.distance(unary_union([obstacles, second_via_poly])) \
                < spec.clearance - 1e-4:
            continue
        g2 = Grid(region, second_xy,
                  unary_union([obstacles, first_via_poly, line1]), inflate)
        d2, prev2 = dijkstra(g2, g2.cell(second_xy))
        e1 = g1.xy(*end1)
        best = None
        for s2 in (1, -1):
            goal = (e1[0] + s2 * tang[0] * spec.pitch, e1[1] + s2 * tang[1] * spec.pitch)
            c2 = g2.cell(goal)
            if c2 not in d2:
                c2 = None
                for off in (1, -1, 2):
                    alt = (goal[0] + s2 * off * tang[0] * GRID,
                           goal[1] + s2 * off * tang[1] * GRID)
                    if g2.cell(alt) in d2:
                        c2 = g2.cell(alt)
                        break
                if c2 is None:
                    continue
            pts2 = path_points(g2, prev2, c2, second_xy)
            p_xy, n_xy = (pts1[-1], pts2[-1]) if first_is_p else (pts2[-1], pts1[-1])
            if sign is not None and chirality(side, p_xy, n_xy) != sign:
                continue
            line2 = LineString(pts2).buffer(spec.width / 2.0)
            if line2.distance(unary_union([obstacles, first_via_poly, line1])) \
                    < spec.clearance - 1e-4:
                continue
            total = cost1 + d2[c2]
            if best is None or total < best[0]:
                best = (total, pts1, pts2)
        if best:
            return best
    return None


def relay_terminal(bg, p_isl: Island, n_isl: Island, bga, spec: Spec, toward,
                   sign: int | None) -> dict:
    """New stubs for one terminal. Returns {ops, tips, side, via_in_pad, cost}
    or {why} when nothing fits."""
    skip = (p_isl.net, n_isl.net)
    placed, vip = [], []
    anchors = {}
    for isl in (p_isl, n_isl):
        if isl.via is not None:
            anchors[isl.net] = tuple(isl.via.at)
            continue
        why = via_fits(bg, isl.ball.center, spec, skip, placed)
        if why:
            return {"why": f"{isl.net} at {isl.ball.ref}.{isl.ball.number}: {why}"}
        anchors[isl.net] = tuple(isl.ball.center)
        placed.append(tuple(isl.ball.center))
        vip.append(isl)
    x0, y0, x1, y1 = bga.bbox
    margin = bga.pitch / 2.0 + 0.05
    region = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    obstacles = _obstacles(bg, spec.layer, skip)
    via_poly = {net: Point(at).buffer(spec.via_d / 2.0) for net, at in anchors.items()}
    p, n = p_isl.net, n_isl.net
    best, why = None, "no free exit on any side"
    for side in SIDES:
        for first, second in ((p, n), (n, p)):
            got = _lay_pair(anchors[first], anchors[second], region,
                            obstacles, spec, side, sign, toward, first == p,
                            via_poly[first], via_poly[second])
            if got and (best is None or got[0] < best[0][0]):
                best = (got, first, second, side)
            if got:
                break   # this side works; the other order is only a fallback
    if best is None:
        return {"why": why}
    (cost, pts1, pts2), first, second, side = best
    paths = {first: pts1, second: pts2}
    ops = []
    for isl in (p_isl, n_isl):
        keep = {id(t) for t in isl.neck}
        ops += [{"op": "remove", "uuid": t.uuid} for t in isl.tracks if id(t) not in keep]
        ops += [{"op": "remove", "uuid": v.uuid} for v in isl.vias
                if isl.via is None or v is not isl.via]
        if isl in vip:
            ops += [{"op": "remove", "uuid": t.uuid} for t in isl.neck]
            ops.append({"op": "add_via", "at": list(anchors[isl.net]),
                        "size": spec.via_d, "drill": spec.via_drill, "net": isl.net})
        ops.append({"op": "add_path", "points": [list(q) for q in paths[isl.net]],
                    "width": spec.width, "layer": spec.layer, "net": isl.net})
    return {"ops": ops, "side": side, "cost": cost,
            "tips": {p: paths[p][-1], n: paths[n][-1]},
            "via_in_pad": [f"{i.ball.ref}.{i.ball.number}" for i in vip]}


def tip_transition(bg, p_isl: Island, n_isl: Island, bga, spec: Spec,
                   sign: int | None) -> dict:
    """The fallback when a via-in-pad doesn't fit: keep the outer-layer stubs
    and change layer just past their tips, in the open beyond the field. The
    two vias sit a drill-to-drill apart, each net steps out to its via, and
    short stubs on the stripline layer come back to the pair's pitch. The
    order of P and N stays what the stubs have."""
    skip = (p_isl.net, n_isl.net)
    if len(p_isl.tips) != 1 or len(n_isl.tips) != 1:
        return {"why": "no single stub tip to change layer at"}
    a, b = p_isl.tips[0], n_isl.tips[0]
    if a[2] != b[2] or a[2] == spec.layer:
        return {"why": "tips not on one outer layer"}
    side = tip_side(bga.bbox, a)
    if sign is not None and chirality(side, a, b) != sign:
        return {"why": "tips in the wrong P/N order for the other terminal"}
    ex, ey = SIDES[side]
    ux, uy = a[0] - b[0], a[1] - b[1]
    un = math.hypot(ux, uy)
    if un < 1e-6:
        return {"why": "tips coincide"}
    ux, uy = ux / un, uy / un              # from N's tip toward P's
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    half = (spec.via_drill + spec.hole_to_hole) / 2.0
    outer = _obstacles(bg, a[2], skip)
    inner = _obstacles(bg, spec.layer, skip)
    why = "no room past the tips"
    for d in (0.45, 0.6, 0.8, 1.0, 1.25):
        at_p = (mx + ex * d + ux * half, my + ey * d + uy * half)
        at_n = (mx + ex * d - ux * half, my + ey * d - uy * half)
        d2 = d + half - spec.pitch / 2.0
        tip_p = (mx + ex * d2 + ux * spec.pitch / 2.0, my + ey * d2 + uy * spec.pitch / 2.0)
        tip_n = (mx + ex * d2 - ux * spec.pitch / 2.0, my + ey * d2 - uy * spec.pitch / 2.0)
        why_p = via_fits(bg, at_p, spec, skip) or via_fits(bg, at_n, spec, skip, [at_p])
        if why_p:
            why = why_p
            continue
        rings = [Point(q).buffer(spec.via_d / 2.0) for q in (at_p, at_n)]
        out_p = LineString([a[:2], at_p]).buffer(spec.width / 2.0)
        out_n = LineString([b[:2], at_n]).buffer(spec.width / 2.0)
        in_p = LineString([at_p, tip_p]).buffer(spec.width / 2.0)
        in_n = LineString([at_n, tip_n]).buffer(spec.width / 2.0)
        c = spec.clearance - 1e-4
        if outer.distance(out_p) < c or outer.distance(out_n) < c \
                or inner.distance(in_p) < c or inner.distance(in_n) < c \
                or in_p.distance(in_n) < c or in_p.distance(rings[1]) < c \
                or in_n.distance(rings[0]) < c or out_p.distance(rings[1]) < c \
                or out_n.distance(rings[0]) < c:
            why = "stubs past the tips clash"
            continue
        ops = []
        for net, tip, at, new_tip, isl in ((p_isl.net, a, at_p, tip_p, p_isl),
                                           (n_isl.net, b, at_n, tip_n, n_isl)):
            w = next(t.width for t in isl.tracks if t.layer == tip[2]
                     and any(_near(e, tip) for e in _ends(t)))
            ops.append({"op": "add_track", "start": [tip[0], tip[1]],
                        "end": [round(at[0], 4), round(at[1], 4)], "width": w,
                        "layer": tip[2], "net": net})
            ops.append({"op": "add_via", "at": [round(at[0], 4), round(at[1], 4)],
                        "size": spec.via_d, "drill": spec.via_drill, "net": net})
            ops.append({"op": "add_track", "start": [round(at[0], 4), round(at[1], 4)],
                        "end": [round(new_tip[0], 4), round(new_tip[1], 4)],
                        "width": spec.width, "layer": spec.layer, "net": net})
        return {"ops": ops, "side": side, "cost": 0.0, "via_in_pad": [],
                "tips": {p_isl.net: tip_p, n_isl.net: tip_n}}
    return {"why": why}


def needs_relay(bbox, pitch: float, p_isl: Island, n_isl: Island, layer: str,
                toward, both_bga: bool) -> str | None:
    """Why this terminal can't launch a coupled pair as it stands, or None."""
    if len(p_isl.tips) != 1 or len(n_isl.tips) != 1:
        return "no single stub tip"
    a, b = p_isl.tips[0], n_isl.tips[0]
    if a[2] != b[2]:
        return "tips on different layers"
    if math.hypot(a[0] - b[0], a[1] - b[1]) > 3.0 * pitch:
        return "tips apart"
    if both_bga and a[2] != layer:
        return "tips off the stripline layer"
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    bx = (p_isl.ball.center[0] + n_isl.ball.center[0]) / 2.0
    by = (p_isl.ball.center[1] + n_isl.ball.center[1]) / 2.0
    if math.hypot(mid[0] - toward[0], mid[1] - toward[1]) \
            > math.hypot(bx - toward[0], by - toward[1]):
        return "tips point away from the other terminal"
    return None


def _terminal_centre(pads) -> tuple[float, float]:
    return (sum(q.center[0] for q in pads) / len(pads),
            sum(q.center[1] for q in pads) / len(pads))


def plan_launch(bg, bgas, p: str, n: str, spec: Spec, skip_refs=()) -> dict:
    """Route_edit ops that make every BGA terminal of pair p/n launchable.
    Returns {ops, terminals: [...], failed: [...]}; ops is empty when nothing
    needs doing or the pair is already routed. skip_refs: parts that are not
    terminals (a termination R). When one terminal can't be laid, the other
    terminal's ops stand: the failure names it, and route_critical undoes the
    whole pair if KRT can't route what is left."""
    out = {"ops": [], "terminals": [], "failed": []}
    terminals = []
    for b in sorted(bgas, key=lambda b: b.balls):
        nums = {net: [k for k, v in b.nets.items() if v == net] for net in (p, n)}
        if len(nums[p]) != 1 or len(nums[n]) != 1:
            continue
        pp = next(q for q in bg.pads_of(net=p, ref=b.ref) if q.number == nums[p][0])
        nn = next(q for q in bg.pads_of(net=n, ref=b.ref) if q.number == nums[n][0])
        terminals.append((b, pp, nn))
    if not terminals:
        return out
    refs = {b.ref for b, _, _ in terminals}
    others = [q for net in (p, n) for q in bg.pads_of(net=net)
              if q.ref not in refs and q.ref not in skip_refs]
    both_bga = len(terminals) == 2
    done: dict[str, tuple] = {}   # ref -> (side, tip_p, tip_n)
    # the field with the fewer balls has the fewer ways out: it chooses first
    for i, (b, pp, nn) in enumerate(terminals):
        if both_bga:
            ob, op_, on_ = terminals[1 - i]
            toward = _terminal_centre([op_, on_])
        elif others:
            toward = _terminal_centre(others)
        else:
            continue
        if b.ref in done:
            continue
        p_isl, n_isl = island(bg, p, pp), island(bg, n, nn)
        if p_isl.routed or n_isl.routed:
            return {"ops": [], "terminals": [], "failed": []}   # already routed
        sign = None
        if both_bga and terminals[1 - i][0].ref in done:
            side2, tp, tn = done[terminals[1 - i][0].ref]
            toward = ((tp[0] + tn[0]) / 2.0, (tp[1] + tn[1]) / 2.0)
            sign = -chirality(side2, tp, tn)
        why = needs_relay(b.bbox, b.pitch, p_isl, n_isl, spec.layer, toward, both_bga)
        if why is None and sign is not None:
            a, c = p_isl.tips[0], n_isl.tips[0]
            side_here = tip_side(b.bbox, a)
            if chirality(side_here, a, c) != sign:
                why = "tips in the wrong P/N order for the other terminal"
        if why is None:
            a, c = p_isl.tips[0], n_isl.tips[0]
            done[b.ref] = (tip_side(b.bbox, a), a[:2], c[:2])
            continue
        got = relay_terminal(bg, p_isl, n_isl, b, spec, toward, sign)
        if "why" in got:
            fell = tip_transition(bg, p_isl, n_isl, b, spec, sign)
            if "why" in fell:
                out["failed"].append({"ref": b.ref, "need": why, "why": got["why"],
                                      "tip_transition": fell["why"]})
                continue
            got = fell
        out["ops"] += got["ops"]
        done[b.ref] = (got["side"], got["tips"][p], got["tips"][n])
        out["terminals"].append({"ref": b.ref, "need": why, "side": got["side"],
                                 "tips": {k: list(v) for k, v in got["tips"].items()},
                                 "via_in_pad": got["via_in_pad"]})
    return out
