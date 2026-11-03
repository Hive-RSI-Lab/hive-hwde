"""copperpath.py - shortest copper path between two pads of a net, in mm.

netconn gives tracks, vias and pads. A pour has no length there, so a walk over
it costs 0, which is wrong for a decoupling loop. Here a hop across one fill
polygon costs the straight line between two things on it, and is refused if the
line leaves the fill for more than MAX_GAP_MM: that's a slot, not a via's
antipad. A refused hop can still go round through other vias and pads on the
same fill, so a slot's detour shows up as long as something sits along it.

Copper can bend round an obstacle where this line can't, so the walk can read
long. slots=False ignores the refusal, which gives a floor on the length: only
that floor is proof that the copper really is long.

Hops are checked when they're popped, not when they're pushed: most of a ground
net's vias are never reached, and clipping a line against a big fill is slow.
"""
from __future__ import annotations

import heapq
import itertools
import math

from shapely.geometry import LineString, Point
from shapely.prepared import prep

import netconn

MAX_GAP_MM = 1.5   # wider than a via antipad or a pad's clearance


class CopperPaths:
    """Walks one net. Build once per net: the fills are slow to index."""

    def __init__(self, bg, net: str):
        self.g = netconn.build(bg, net)
        self.bg = bg
        self.fills: list = []
        self._hops: dict = {}
        self.on_fill: dict = {}
        self.pos: dict = {}
        self.via_nodes = set()
        for i, v in enumerate(bg.vias_of(net)):
            self.pos[("via", i)] = tuple(v.at)
            self.via_nodes.add(("via", i))
        for p in bg.pads_of(net=net):
            self.pos[("pad", p.ref, p.number)] = tuple(p.center)
        for t in self.g.tracks.values():
            for xy in (t.shape.coords[0], t.shape.coords[-1]):
                n = ("pt", netconn._snap(xy, netconn.SNAP))
                self.pos[n] = n[1]
        self._index_fills(net)
        self._find_islands()

    def _find_islands(self) -> None:
        """Which nodes are joined at all, so a walk to an unrouted pad gives up
        at once instead of visiting a whole ground net."""
        self.island: dict = {}
        hops: dict = {}
        for _, members, _ in self.fills:
            for n in members:
                hops.setdefault(n, []).extend(members)
        for start in list(self.g.adj) + list(hops):
            if start in self.island:
                continue
            self.island[start] = start
            stack = [start]
            while stack:
                u = stack.pop()
                for v in [e[0] for e in self.g.adj.get(u, ())] + hops.pop(u, []):
                    if v not in self.island:
                        self.island[v] = start
                        stack.append(v)

    def _index_fills(self, net: str) -> None:
        bg = self.bg
        vias = bg.vias_of(net)
        pads = bg.pads_of(net=net)
        tracks = self.g.tracks.values()
        for z in bg.zones_of(net):
            for layer, polys in z.fills.items():
                for poly in polys:
                    members = []
                    for i, v in enumerate(vias):
                        if v.spans(layer) and poly.distance(Point(v.at)) <= v.diameter / 2:
                            members.append(("via", i))
                    for p in pads:
                        if layer in p.layers and poly.intersects(p.poly):
                            members.append(("pad", p.ref, p.number))
                    for t in tracks:
                        if t.layer != layer:
                            continue
                        for xy in (t.shape.coords[0], t.shape.coords[-1]):
                            if poly.distance(Point(xy)) <= t.width / 2:
                                members.append(("pt", netconn._snap(xy, netconn.SNAP)))
                    if len(members) < 2:
                        continue
                    k = len(self.fills)
                    self.fills.append((poly, members, prep(poly)))
                    for n in members:
                        self.on_fill.setdefault(n, []).append(k)

    def _hop_ok(self, k: int, a, b) -> bool:
        pa, pb = self.pos[a], self.pos[b]
        if pa == pb:
            return True
        key = (k, pa, pb) if pa < pb else (k, pb, pa)
        if key not in self._hops:
            poly, _, fast = self.fills[k]
            line = LineString([pa, pb])
            ok = fast.covers(line)
            if not ok:
                outside = line.difference(poly)
                ok = all(s.length <= MAX_GAP_MM
                         for s in getattr(outside, "geoms", [outside]))
            self._hops[key] = ok
        return self._hops[key]

    def between(self, a, b, slots: bool = True) -> dict | None:
        """{"mm", "vias", "path"} from node a to node b, or to the nearest of
        a set of nodes, or None when they aren't joined (not routed yet)."""
        targets = b if isinstance(b, (set, frozenset)) else {b}
        home = self.island.get(a)
        targets = {t for t in targets if home is not None
                   and self.island.get(t) == home}
        if not targets:
            return None
        tie = itertools.count()
        done: dict = {}
        came: dict = {}
        best: dict = {}   # floor only: a refused hop can't lose its better rival
        heap = [(0.0, next(tie), a, None, None)]
        while heap:
            d, _, u, prev, fill = heapq.heappop(heap)
            if u in done:
                continue
            if slots and fill is not None and not self._hop_ok(fill, prev, u):
                continue
            done[u] = d
            came[u] = prev
            if u in targets:
                path, n = [], u
                while n is not None:
                    path.append(n)
                    n = came[n]
                path.reverse()
                return {"mm": d, "path": path,
                        "vias": sum(n in self.via_nodes for n in path)}
            for v, w, _eid in self.g.adj.get(u, ()):
                if v not in done:
                    heapq.heappush(heap, (d + w, next(tie), v, u, None))
            for k in self.on_fill.get(u, ()):
                ux, uy = self.pos[u]
                for v in self.fills[k][1]:
                    if v in done:
                        continue
                    vx, vy = self.pos[v]
                    nd = d + math.hypot(ux - vx, uy - vy)
                    if slots or nd < best.get(v, math.inf):
                        best[v] = nd
                        heapq.heappush(heap, (nd, next(tie), v, u, k))
        # same island, so copper joins them: every line across a pour was
        # refused, and the floor is all that can be said
        return self.between(a, b, slots=False)
