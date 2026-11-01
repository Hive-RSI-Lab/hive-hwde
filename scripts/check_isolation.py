"""check_isolation.py - isolation barriers, judged as barriers (hive-hwde #353).

check_creepage answers a different question: IPC-2221 spacing for net pairs
more than 30 V apart, copper item against copper item in a plane. It has no idea
of a domain, so two 5 V domains 5 kV apart read 0 V apart, and a pour touching
across the barrier passes. This check sits beside it rather than inside it
because it shares neither input (declared domains, not net voltages), nor table
(IEC, not IPC), nor geometry (a surface path around slots), and rules_gen builds
DRU rules from check_creepage's table.

Nothing is assumed. A board with no `isolation` block isn't judged, and every
value of a barrier is the board's own declaration (schema: constraints_schema):

  "isolation": {
    "domains": {"USB": {"nets": ["VBUS", "GND_USB", "/usb/*"]},
                "MCU": {"nets": ["+3V3", "GND", "/mcu/*"]}},
    "barriers": [{"name": "usb", "between": ["USB", "MCU"],
                  "standard": "iec60664-1",    // or iec62368-1
                  "grade": "reinforced",       // functional|basic|supplementary
                  "working_v_rms": 250,
                  "impulse_v": 4000,           // or mains_v_rms + overvoltage_category
                  "withstand_v_rms": 5000,     // optional; peak_v likewise
                  "pollution_degree": 2, "material_group": "IIIa",
                  "altitude_m": 2000,
                  "min_clearance_mm": 8.0,     // optional, the board's own
                  "min_creepage_mm": 8.0,      //   floor over the tables' answer
                  "inner_min_mm": 1.0,         // optional
                  "solid_min_mm": 0.4,         // optional
                  "column": "general"}],       // or printed_wiring
    "parts": {"U1": {"kind": "digital_isolator", "grade": "reinforced",
                     "working_v_rms": 400, "impulse_v": 8000,
                     "withstand_v_rms": 5000, "creepage_mm": 8.0,
                     "source": "ADuM4160 datasheet"}},
    "floating": ["/CHASSIS"]                   // known non-domain copper
  }

How each thing is judged, per barrier:

  Clearance, outer layers: straight line through air between the two domains'
  copper on that layer. A slot does not raise it. A slot is air, so the line
  still runs; only a solid wall could block it, and the board file has no solid
  wall to read (nor its height).
  Creepage, outer layers: the shortest path along that face of the board. Holes
  in the surface are Edge.Cuts faces inside the outline and NPTH drills, when
  wider than the standard's X (0.25/1.0/1.5 mm by pollution degree, IEC 60664-1
  clause 4.2); narrower, they earn nothing. The path doesn't go down a hole wall
  or round the board's edge to the other face. Solder mask earns no credit:
  the tables are for bare board surface, and coating credit lives in IEC 60664-3,
  which isn't implemented.
  Inner layers: no air and no surface, so neither table applies. A gap inside
  cured laminate is judged as solid insulation, a cemented joint: inner_min_mm
  when declared (error), otherwise the printed-wiring PD1 column (warning: using
  the column this way is from memory, not read).
  Between layers: the straight distance through the laminate against
  solid_min_mm when declared (error), otherwise 0.4 mm for supplementary and
  reinforced (warning, from memory). Basic has no thickness rule: test it.
  Table rows: reference/iso_tables.yaml, each marked verified or not. A finding
  on an unread row or rule carries table_verified: false and says so.

Findings:
  iso_touching        error    copper of the two domains touches (both nets named)
  iso_clearance       error    outer-layer air gap under the requirement
  iso_creepage        error    outer-layer surface path under the requirement
  iso_inner           error/w  inner-layer gap under the rule above
  iso_solid           error/w  through-laminate distance under the rule above
  iso_floating_bridge error    copper in no domain whose two gaps add to less
                               than the creepage requirement (it shortens the
                               path: a stitching via, a netless pour)
  iso_part_unrated    error    a part with pins in both domains, no declared rating
  iso_part_rating_short error  a rated part whose rating doesn't cover the barrier
  iso_part_pin_gap    warning  a rated part's own pins closer than the requirement
                               (its gap is always in the facts as pin_gap_mm)
  iso_net_undeclared  warning  a net in no domain and not in `floating`
  iso_part_absent     warning  a rated part that isn't on the board

A declaration the check can't read (missing value, unknown domain, one net in
two domains, a voltage off the table) is exit 2, never a pass.

CLI: bin/hwde check_isolation --pcb board.kicad_pcb --constraints c.json [--out F]
"""
from __future__ import annotations

import argparse
import fnmatch
import heapq
import math
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
import isotables  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "check_isolation"
SOURCE = SCRIPT
EPS = 1e-6
SAMPLE_MM = 0.1        # boundary sampling for legs the nearest point can't take
MAX_SAMPLES = 4000
BARRIER_KEYS = ("name", "between", "standard", "grade", "working_v_rms",
                "pollution_degree", "material_group", "altitude_m")


# ---------------------------------------------------------------- the surface

def surface_of(bg: geom.BoardGeom) -> tuple[Polygon, list[Polygon]]:
    """(the board body, every hole in it): Edge.Cuts faces inside the largest
    face, and NPTH drills. A face outside the body is another board in the file,
    and isn't a hole."""
    faces = list(getattr(bg, "edge_faces", []) or [])
    body = faces[0] if faces else bg.outline
    holes = [f for f in faces[1:] if body.contains(f.representative_point())]
    holes += [p.drill_poly for p in bg.npth
              if not p.drill_poly.is_empty
              and body.contains(p.drill_poly.representative_point())]
    return body, holes


def wide_holes(holes: list[Polygon], x_mm: float) -> list[Polygon]:
    """The parts of each hole at least X wide. Eroding by X/2 removes anything
    narrower; a slot exactly X wide stays (the standard says "equal to or
    larger"). Mitred regrowth keeps a rectangular slot's corners exact."""
    r = x_mm / 2.0 - EPS
    out = []
    for h in holes:
        core = h.buffer(-r, join_style=2)
        if core.is_empty:
            continue
        grown = core.buffer(r, join_style=2, mitre_limit=2.0).intersection(h)
        out.extend(getattr(grown, "geoms", [grown]))
    return [g for g in out if isinstance(g, Polygon) and not g.is_empty]


def _corner_nodes(surface: Polygon) -> list[tuple[float, float]]:
    """A shortest path bends only at corners that stick into free space: the
    outline's reflex corners, and every convex corner of a hole."""
    out = []
    for k, ring in enumerate([surface.exterior, *surface.interiors]):
        pts = list(shapely.simplify(ring, 0.002).coords)[:-1]
        if len(pts) < 3:
            continue
        sense = 1.0 if ring.is_ccw else -1.0     # + = turning left round the ring
        for i, (bx, by) in enumerate(pts):
            ax, ay = pts[i - 1]
            cx, cy = pts[(i + 1) % len(pts)]
            turn = sense * ((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
            if (turn < 0) if k == 0 else (turn > 0):
                out.append((bx, by))
    return out


class Surface:
    """One outer face's surface for shortest-path queries."""

    def __init__(self, body: Polygon, holes: list[Polygon]):
        self.surface = body.difference(unary_union(holes)) if holes else body
        if isinstance(self.surface, MultiPolygon):
            # a slot cutting the board in two: both parts stay, paths can't cross
            self.parts = list(self.surface.geoms)
        else:
            self.parts = [self.surface]
        self.free = self.surface.buffer(EPS, join_style=2)
        shapely.prepare(self.free)
        self.nodes = [n for part in self.parts for n in _corner_nodes(part)]

    def clear(self, a, b) -> bool:
        if abs(a[0] - b[0]) < 1e-12 and abs(a[1] - b[1]) < 1e-12:
            return True
        return bool(shapely.covers(self.free, LineString([a, b])))

    def _clear_many(self, origin, pts: np.ndarray) -> np.ndarray:
        return shapely.covers(self.free, shapely.linestrings(
            [[origin, tuple(p)] for p in pts]))

    def leg(self, poly, v, limit: float):
        """Shortest clear straight leg from `poly` to point `v`, or None. The
        nearest point usually sees v; if not, sample the near boundary."""
        pa = nearest_points(poly, Point(v))[0]
        d0 = pa.distance(Point(v))
        if d0 > limit:
            return None
        if self.clear((pa.x, pa.y), v):
            return d0, (pa.x, pa.y)
        best = None
        pts = _boundary_samples(poly, Point(v).buffer(limit, quad_segs=4))
        if len(pts):
            ds = np.hypot(pts[:, 0] - v[0], pts[:, 1] - v[1])
            order = np.argsort(ds)
            ok = self._clear_many(v, pts[order])
            if ok.any():
                i = order[int(np.argmax(ok))]
                best = float(ds[i]), (float(pts[i, 0]), float(pts[i, 1]))
        return best


def _boundary_samples(poly, within=None) -> np.ndarray:
    line = poly.boundary
    if within is not None:
        line = line.intersection(within)
    if line.is_empty:
        return np.empty((0, 2))
    step = SAMPLE_MM
    if line.length / step > MAX_SAMPLES:
        step = line.length / MAX_SAMPLES
    dense = shapely.segmentize(line, step)
    return shapely.get_coordinates(dense)


def creepage_path(a, b, surf: Surface) -> tuple[float | None, list]:
    """Shortest path from copper `a` to copper `b` over the surface: (length,
    points), or (None, []) when the surface doesn't connect them. Dijkstra over
    the corners that can bend a path. Exact where the path's ends are a
    polygon's nearest points; a leg that needs sampling is within SAMPLE_MM."""
    pa, pb = nearest_points(a, b)
    straight = pa.distance(pb)
    if straight <= EPS:
        return 0.0, [(pa.x, pa.y), (pb.x, pb.y)]
    if surf.clear((pa.x, pa.y), (pb.x, pb.y)):
        return straight, [(pa.x, pa.y), (pb.x, pb.y)]
    limit = max(4.0 * straight, straight + 20.0)
    nodes = surf.nodes
    if not nodes:
        return None, []
    far = max(a.hausdorff_distance(b), 1.0) + sum(
        p.length for p in surf.parts)
    while True:
        got = _dijkstra(a, b, surf, limit)
        direct = _direct(a, b, surf, min(limit, got[0]) if got[0] else limit)
        if direct[0] is not None and (got[0] is None or direct[0] < got[0]):
            got = direct
        if got[0] is not None and got[0] <= limit:
            return got
        if limit > far:
            return None, []
        limit *= 2.0


def _direct(a, b, surf: Surface, limit: float):
    """A straight path that isn't the global nearest pair (that one is blocked,
    another pair sees each other). Sampled on both boundaries."""
    best: tuple = (None, [])
    for src, dst in ((a, b), (b, a)):
        pts = _boundary_samples(src, dst.buffer(limit, quad_segs=4))
        if not len(pts):
            continue
        feet = shapely.get_coordinates(
            shapely.shortest_line(shapely.points(pts), dst))[1::2]
        ds = np.hypot(*(pts - feet).T)
        for i in np.argsort(ds)[:MAX_SAMPLES]:
            d = float(ds[i])
            if best[0] is not None and d >= best[0]:
                break
            p, q = tuple(pts[i]), tuple(feet[i])
            if surf.clear(p, q):
                best = (d, [p, q])
                break
    return best


def _dijkstra(a, b, surf: Surface, limit: float):
    """Corners within `limit` of both coppers are the only ones a path of at
    most `limit` can use: smaller graph, same answer when one comes back."""
    pts = [n for n in surf.nodes
           if Point(n).distance(a) + Point(n).distance(b) <= limit]
    n = len(pts)
    if not n:
        return None, []
    dist = [math.inf] * n
    prev: list = [None] * n
    heap = []
    for i, v in enumerate(pts):
        leg = surf.leg(a, v, limit)
        if leg:
            dist[i] = leg[0]
            prev[i] = ("src", leg[1])
            heapq.heappush(heap, (leg[0], i))
    best, best_end = None, None
    done = [False] * n
    while heap:
        d, i = heapq.heappop(heap)
        if done[i] or d > dist[i]:
            continue
        done[i] = True
        if best is not None and d >= best:
            break
        v = pts[i]
        leg = surf.leg(b, v, limit)
        if leg and (best is None or d + leg[0] < best):
            best, best_end = d + leg[0], (i, leg[1])
        for j in range(n):
            if done[j]:
                continue
            step = math.hypot(pts[j][0] - v[0], pts[j][1] - v[1])
            if d + step < dist[j] and surf.clear(v, pts[j]):
                dist[j] = d + step
                prev[j] = ("node", i)
                heapq.heappush(heap, (dist[j], j))
    if best is None:
        return None, []
    i, end = best_end
    path = [end, pts[i]]
    while True:
        kind, val = prev[i]
        if kind == "src":
            path.append(val)
            break
        i = val
        path.append(pts[i])
    path.reverse()
    return best, path


# ------------------------------------------------------------- the declaration

def _matches(net: str, pats: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(net, p) for p in pats)


def domain_map(iso: dict, nets) -> tuple[dict[str, str], dict[str, list]]:
    """net -> domain, for the board's nets; one net in two domains refuses."""
    domains = iso.get("domains")
    if not isinstance(domains, dict) or not domains:
        raise CheckError("isolation.domains must name at least two domains")
    out: dict[str, str] = {}
    empty: dict[str, list] = {}
    for name, d in domains.items():
        pats = d.get("nets") if isinstance(d, dict) else None
        if not isinstance(pats, list) or not pats:
            raise CheckError(f"isolation domain {name!r} needs nets (a list of globs)")
        hit = [n for n in nets if _matches(n, pats)]
        if not hit:
            empty[name] = pats
        for n in hit:
            if n in out:
                raise CheckError(f"net {n!r} is in domains {out[n]!r} and {name!r}: "
                                 "a net on both sides IS a crossing, fix the "
                                 "declaration or the schematic")
            out[n] = name
    return out, empty


def _check_barrier(b: dict, domains: dict) -> None:
    missing = [k for k in BARRIER_KEYS if b.get(k) in (None, "")]
    if missing:
        raise CheckError(f"isolation barrier {b.get('name', b)!r} is missing "
                         f"{', '.join(missing)}: every value is the board's own "
                         "declaration, none is assumed")
    pair = b["between"]
    if not (isinstance(pair, list) and len(pair) == 2 and pair[0] != pair[1]):
        raise CheckError(f"barrier {b['name']!r}: between must be two domains")
    for d in pair:
        if d not in domains:
            raise CheckError(f"barrier {b['name']!r}: unknown domain {d!r}")


# ----------------------------------------------------------- the copper, keyed

def _copper_by_key(bg, layer, netmap, spanning):
    """{(net, ref-or-None): geometry} on a layer. A pad of a part spanning the
    domains is keyed by its part: its own pin-to-pin gap belongs to the part."""
    parts: dict = {}
    for t in bg.tracks_of(None, layer):
        parts.setdefault((t.net or "", None), []).append(t.poly)
    for v in bg.vias_of(None, layer):
        parts.setdefault((v.net or "", None), []).append(v.poly)
    for p in bg.pads_of(None, layer):
        ref = p.ref if p.ref in spanning and netmap.get(p.net or "") else None
        parts.setdefault((p.net or "", ref), []).append(p.poly)
    for z in bg.zones_of(None, layer):
        fills = z.fills.get(layer) or []
        if fills:
            parts.setdefault((z.net or "", None), []).extend(fills)
    return {k: unary_union(v) for k, v in parts.items()}


def _mid(path) -> tuple[float, float]:
    (x0, y0), (x1, y1) = path[0], path[-1]
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _trust(req: dict) -> tuple[dict, str]:
    if req.get("verified"):
        return {"table_verified": True}, ""
    return {"table_verified": False}, " (table row or rule not read from a document)"


def _layer_pass(bg, layer, keys, b, req, netmap, surf, viols, report):
    """One barrier on one layer: every (net, net) pair within the requirement."""
    da, db = b["between"]
    side_a = {k: g for k, g in keys.items() if netmap.get(k[0]) == da}
    side_b = {k: g for k, g in keys.items() if netmap.get(k[0]) == db}
    outer = bg.stackup.is_outer(layer)
    fact = {"layer": layer, "kind": "outer" if outer else "inner"}
    if not side_a or not side_b:
        report.append({**fact, "note": "one domain has no copper here"})
        return
    limit = req["creepage"]["mm"] if outer else req["inner"]["mm"]
    kb = list(side_b)
    tree = STRtree([side_b[k] for k in kb])
    worst: dict = {}
    for ka, ga in side_a.items():
        for j in tree.query(ga, predicate="dwithin", distance=limit + EPS):
            kbb = kb[int(j)]
            if ka[1] is not None and ka[1] == kbb[1]:
                continue    # a part's own pins: the part's finding, not a gap
            gb = side_b[kbb]
            pair = (ka[0], kbb[0])
            d = ga.distance(gb)
            if pair not in worst or d < worst[pair][0]:
                worst[pair] = (d, ga, gb, [r for r in (ka[1], kbb[1]) if r])
    for (na, nb), (d, ga, gb, refs) in sorted(worst.items()):
        pa, pb = nearest_points(ga, gb)
        where = ((pa.x + pb.x) / 2.0, (pa.y + pb.y) / 2.0)
        common = {"other_net": nb, "barrier": b["name"], "domains": [da, db],
                  "spacing_mm": checklib.rnd(d)}
        if d <= EPS:
            viols.append(violation(
                SCRIPT, "error", where, layer, na, refs,
                f"{na} and {nb} touch across barrier {b['name']} ({da}/{db}) "
                f"on {layer}", SOURCE, kind="iso_touching", required_mm=
                checklib.rnd(req['clearance']['mm'] if outer else req['inner']['mm']),
                **common))
            continue
        if not outer:
            r = req["inner"]
            if d + EPS < r["mm"]:
                ex, tail = _trust(r)
                viols.append(violation(
                    SCRIPT, "error" if r["declared"] else "warning", where, layer,
                    na, refs,
                    f"{na} to {nb} on inner layer {layer}: {d:.3f} mm, barrier "
                    f"{b['name']} wants {r['mm']:.3f} mm as solid insulation: "
                    f"{r['basis']}{tail}", SOURCE, kind="iso_inner",
                    required_mm=checklib.rnd(r["mm"]), **common, **ex))
            continue
        r = req["clearance"]
        if d + EPS < r["mm"]:
            ex, tail = _trust(r)
            viols.append(violation(
                SCRIPT, "error", where, layer, na, refs,
                f"{na} to {nb} on {layer}: {d:.3f} mm clearance through air, "
                f"barrier {b['name']} needs {r['mm']:.3f} mm: {r['basis']}{tail}",
                SOURCE, kind="iso_clearance", required_mm=checklib.rnd(r["mm"]),
                **common, **ex))
        r = req["creepage"]
        length, path = creepage_path(ga, gb, surf)
        if length is not None and length + EPS < r["mm"]:
            ex, tail = _trust(r)
            viols.append(violation(
                SCRIPT, "error", _mid(path), layer, na, refs,
                f"{na} to {nb} on {layer}: {length:.3f} mm creepage along the "
                f"surface, barrier {b['name']} needs {r['mm']:.3f} mm: "
                f"{r['basis']}{tail}", SOURCE, kind="iso_creepage",
                required_mm=checklib.rnd(r["mm"]), creepage_mm=checklib.rnd(length),
                path=[[checklib.rnd(x), checklib.rnd(y)] for x, y in path],
                **common, **ex))

    # the layer's reading, for the report, on copper outside the spanning parts'
    # own pads (with those in, a 2 mm pin pitch would hide an 8 mm barrier)
    ua = unary_union([g for k, g in side_a.items() if k[1] is None])
    ub = unary_union([g for k, g in side_b.items() if k[1] is None])
    if ua.is_empty or ub.is_empty:
        report.append({**fact, "note": "only spanning parts' pads here"})
        return
    gap = ua.distance(ub)
    if outer:
        length, path = creepage_path(ua, ub, surf)
        report.append({**fact, "clearance_mm": checklib.rnd(gap),
                       "creepage_mm": None if length is None else checklib.rnd(length),
                       "path": [[checklib.rnd(x), checklib.rnd(y)] for x, y in path]})
    else:
        report.append({**fact, "gap_mm": checklib.rnd(gap)})


def _solid_pass(bg, per_layer, b, req, netmap, viols, report):
    """Copper of the two domains on different layers, through the laminate."""
    r = req["solid"]
    da, db = b["between"]
    layers = bg.copper_layers
    unions = {}
    for layer in layers:
        keys = per_layer[layer]
        unions[layer] = (
            unary_union([g for k, g in keys.items() if netmap.get(k[0]) == da and k[1] is None]),
            unary_union([g for k, g in keys.items() if netmap.get(k[0]) == db and k[1] is None]))
    best = None
    for la in layers:
        for lb in layers:
            if la == lb:
                continue
            ga, gb = unions[la][0], unions[lb][1]
            if ga.is_empty or gb.is_empty:
                continue
            h = bg.stackup.height_between(la, lb)
            plan = ga.distance(gb)
            through = math.hypot(plan, h)
            if best is None or through < best["through_mm"]:
                best = {"layers": [la, lb], "through_mm": through, "height_mm": h,
                        "plan_gap_mm": plan}
    if best is None:
        return
    report.append({"kind": "through_laminate", **{
        k: checklib.rnd(v) if isinstance(v, float) else v for k, v in best.items()},
        "rule": r["basis"] if r else "none for this grade: prove it by test"})
    if r and best["through_mm"] + EPS < r["mm"]:
        la, lb = best["layers"]
        ga, gb = unions[la][0], unions[lb][1]
        pa, pb = nearest_points(ga, gb)
        ex, tail = _trust(r)
        viols.append(violation(
            SCRIPT, "error" if r["declared"] else "warning",
            ((pa.x + pb.x) / 2, (pa.y + pb.y) / 2), la, None, [],
            f"barrier {b['name']}: {best['through_mm']:.3f} mm through the laminate "
            f"between {la} and {lb}, wants {r['mm']:.3f} mm: {r['basis']}{tail}",
            SOURCE, kind="iso_solid", barrier=b["name"],
            required_mm=checklib.rnd(r["mm"]),
            spacing_mm=checklib.rnd(best["through_mm"]), **ex))


def _floating_pass(bg, per_layer, b, req, netmap, viols, report):
    """Copper in no domain shortens the path: its two gaps add. Netless copper
    counts; so does a declared `floating` net (declaring it doesn't remove it)."""
    da, db = b["between"]
    limit = req["creepage"]["mm"]
    for layer, keys in per_layer.items():
        if not bg.stackup.is_outer(layer):
            continue
        ga = unary_union([g for k, g in keys.items() if netmap.get(k[0]) == da])
        gb = unary_union([g for k, g in keys.items() if netmap.get(k[0]) == db])
        if ga.is_empty or gb.is_empty:
            continue
        for key, whole in sorted(keys.items(), key=lambda kv: kv[0][0]):
            if key[0] in netmap:
                continue
            # a named net is one conductor however it's drawn; netless copper
            # is many unrelated islands, so each is judged alone
            pieces = [whole] if key[0] else list(getattr(whole, "geoms", [whole]))
            name = key[0] or "(no net)"
            hits = [(g.distance(ga) + g.distance(gb), g) for g in pieces]
            total, g = min(hits, key=lambda h: h[0])
            if total + EPS >= limit:
                continue
            d_a, d_b = g.distance(ga), g.distance(gb)
            pa = nearest_points(g, ga)[0]
            viols.append(violation(
                SCRIPT, "error", (pa.x, pa.y), layer, key[0] or None, [],
                f"{name} is in no domain and sits {d_a:.3f} + {d_b:.3f} mm from "
                f"{da} and {db} on {layer}: it shortens barrier {b['name']} to "
                f"{total:.3f} mm, under {limit:.3f} mm", SOURCE,
                kind="iso_floating_bridge", barrier=b["name"],
                spacing_mm=checklib.rnd(total), required_mm=checklib.rnd(limit)))
            report.append({"layer": layer, "kind": "floating", "net": name,
                           "gaps_mm": [checklib.rnd(d_a), checklib.rnd(d_b)]})


# ---------------------------------------------------------------------- parts

def spanning_parts(bg, netmap) -> dict[str, dict[str, list]]:
    """ref -> {domain: [pads]} for every part with pads in at least two domains."""
    by_ref: dict = {}
    for p in bg.pads_of():
        dom = netmap.get(p.net or "")
        if dom:
            by_ref.setdefault(p.ref, {}).setdefault(dom, []).append(p)
    return {r: d for r, d in by_ref.items() if len(d) > 1}


def rating_shortfalls(rating: dict, b: dict, req: dict) -> list[str]:
    """Why a part's declared rating doesn't cover a barrier; [] when it does. A
    barrier value the rating doesn't state isn't covered."""
    short = []
    grade = rating.get("grade")
    if grade not in isotables.GRADES:
        short.append(f"no grade declared (barrier: {b['grade']})")
    elif isotables.GRADES[grade] < isotables.GRADES[b["grade"]]:
        short.append(f"grade {grade} under {b['grade']}")
    def over(key, need, unit):
        have = rating.get(key)
        if have is None:
            short.append(f"no {key} declared (barrier: {need:g} {unit})")
        elif float(have) < need:
            short.append(f"{key} {float(have):g} under {need:g} {unit}")
    over("working_v_rms", float(b["working_v_rms"]), "V rms")
    over("impulse_v", req["impulse_v"], "V")
    if b.get("withstand_v_rms") is not None:
        over("withstand_v_rms", float(b["withstand_v_rms"]), "V rms")
    if b.get("peak_v") is not None and rating.get("peak_v") is not None:
        over("peak_v", float(b["peak_v"]), "V")
    if rating.get("creepage_mm") is not None and \
            float(rating["creepage_mm"]) + EPS < req["creepage"]["mm"]:
        short.append(f"creepage_mm {float(rating['creepage_mm']):g} under "
                     f"{req['creepage']['mm']:.3f} mm")
    return short


def _pin_gap(pads_a, pads_b) -> tuple[float | None, tuple | None]:
    best, where = None, None
    for pa in pads_a:
        for pb in pads_b:
            if not set(pa.layers) & set(pb.layers):
                continue
            d = pa.poly.distance(pb.poly)
            if best is None or d < best:
                best = d
                where = (pa, pb)
    return best, where


def _part_pass(ref, doms, b, req, rated, viols, report):
    da, db = b["between"]
    if da not in doms or db not in doms:
        return
    pads_a, pads_b = doms[da], doms[db]
    nets = sorted({p.net for p in pads_a + pads_b if p.net})
    gap, where = _pin_gap(pads_a, pads_b)
    pos = None
    if where:
        pa, pb = nearest_points(where[0].poly, where[1].poly)
        pos = ((pa.x + pb.x) / 2.0, (pa.y + pb.y) / 2.0)
    rating = rated.get(ref)
    row = {"ref": ref, "barrier": b["name"], "rated": rating is not None,
           "pin_gap_mm": None if gap is None else checklib.rnd(gap), "nets": nets}
    report.append(row)
    if rating is None:
        viols.append(violation(
            SCRIPT, "error", pos, None, nets[0] if nets else None, [ref],
            f"{ref} has pins in {da} and {db} ({', '.join(nets)}) and no declared "
            f"rating in isolation.parts: nothing but a rated part may cross "
            f"barrier {b['name']}", SOURCE, kind="iso_part_unrated",
            barrier=b["name"], nets=nets))
        return
    short = rating_shortfalls(rating, b, req)
    row["covers"] = not short
    if short:
        viols.append(violation(
            SCRIPT, "error", pos, None, nets[0] if nets else None, [ref],
            f"{ref}'s declared rating doesn't cover barrier {b['name']}: "
            + "; ".join(short), SOURCE, kind="iso_part_rating_short",
            barrier=b["name"], shortfalls=short))
    need = req["creepage"]["mm"]
    if gap is not None and gap + EPS < need:
        viols.append(violation(
            SCRIPT, "warning", pos, None, nets[0] if nets else None, [ref],
            f"{ref}'s own pins are {gap:.3f} mm apart across barrier {b['name']}, "
            f"under its {need:.3f} mm: the part may be rated for it, but the "
            "lands on this board are not", SOURCE, kind="iso_part_pin_gap",
            barrier=b["name"], spacing_mm=checklib.rnd(gap),
            required_mm=checklib.rnd(need)))


# ----------------------------------------------------------------------- check

def check(pcb: Path, cons: dict | None) -> tuple[list[dict], dict]:
    iso = (cons or {}).get("isolation")
    if not iso:
        return [], {"judged": False, "why": "no isolation block in constraints"}
    bg = geom.load_board(pcb)
    bg.assert_fresh()
    netmap, empty = domain_map(iso, sorted(bg.nets))
    domains = iso["domains"]
    barriers = iso.get("barriers")
    if not isinstance(barriers, list) or not barriers:
        raise CheckError("isolation.barriers must list at least one barrier")
    reqs = []
    for b in barriers:
        _check_barrier(b, domains)
        try:
            reqs.append(isotables.requirement(b))
        except isotables.IsoTableError as exc:
            raise CheckError(f"barrier {b['name']!r}: {exc}") from exc

    viols: list[dict] = []
    rated = iso.get("parts") or {}
    span = spanning_parts(bg, netmap)
    refs_on_board = {p.ref for p in bg.pads_of()}
    for ref in sorted(set(rated) - refs_on_board):
        viols.append(violation(
            SCRIPT, "warning", None, None, None, [ref],
            f"isolation.parts names {ref}, which isn't on the board", SOURCE,
            kind="iso_part_absent"))

    floating = iso.get("floating") or []
    undeclared = sorted(n for n in bg.nets if n not in netmap
                        and not _matches(n, floating))
    if undeclared:
        viols.append(violation(
            SCRIPT, "warning", None, None, None, [],
            f"{len(undeclared)} net(s) in no domain and not in isolation.floating: "
            + ", ".join(undeclared[:12]) + ("..." if len(undeclared) > 12 else ""),
            SOURCE, kind="iso_net_undeclared", nets=undeclared))

    per_layer = {layer: _copper_by_key(bg, layer, netmap, span)
                 for layer in bg.copper_layers}
    body, holes = surface_of(bg)
    facts_b = []
    for b, req in zip(barriers, reqs):
        kept = wide_holes(holes, req["groove_x_mm"])
        surf = Surface(body, kept)
        layers: list[dict] = []
        parts: list[dict] = []
        for layer in bg.copper_layers:
            _layer_pass(bg, layer, per_layer[layer], b, req, netmap, surf, viols, layers)
        _solid_pass(bg, per_layer, b, req, netmap, viols, layers)
        _floating_pass(bg, per_layer, b, req, netmap, viols, layers)
        for ref, doms in sorted(span.items()):
            _part_pass(ref, doms, b, req, rated, viols, parts)
        facts_b.append({
            "name": b["name"], "between": b["between"], "standard": b["standard"],
            "grade": b["grade"], "required": req, "layers": layers, "parts": parts,
            "holes_with_credit": len(kept), "holes_ignored": len(holes) - len(kept),
            "mask_credit": False})
    pairs = {frozenset(b["between"]) for b in barriers}
    no_barrier = [{"ref": ref, "domains": sorted(doms)}
                  for ref, doms in sorted(span.items())
                  if not any(p <= set(doms) for p in pairs)]
    return viols, {"judged": True, "barriers": facts_b,
                   "domains_without_copper": sorted(empty),
                   "spanning_without_barrier": no_barrier,
                   "nets_undeclared": undeclared}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    cons = (checklib.load_json(args.constraints, "constraints")
            if args.constraints else None)
    viols, facts = check(pcb, cons)
    return checklib.report(SCRIPT, pcb, viols, checked=[facts]), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
