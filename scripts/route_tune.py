"""route_tune - length tuning of length groups and diff-pair skew (P7, #324, #476).

check_length_groups judges each constraints.json length_groups net against
its group's reference; this adds trombone meanders to the nets that are too
SHORT until they sit in the middle of their allowed range. It runs on copper
already on the board (Freerouting's, KRT's, a hand route), so it is a post-
route step: route_auto / route_critical first, then route_tune, then verify.

Why not KRT's own matcher (route.py --length-match-group, KRT 0.19.0's
length_matching.py)? It tunes only the nets KRT routes in that same run,
always to the group's longest member, and counts each via's whole span as
barrel. Groups here are held to a REFERENCE net (DQS, the clock), a window
below it, or a cap, and the length is the one check_length_groups measures
(the barrel the signal travels, pad to pad). The tuner measures with that
same function, so what it tunes to is exactly what verify judges.

Per single-ended net that needs +D mm (pairs: see below):
  * candidate runs are the STRAIGHT tracks on the net's measured pad-to-pad
    path, longest first; a lead of max(0.5 mm, 2 w) is left at each end;
  * a run becomes a train of k rectangular bumps of height h on one side
    (each adds 2 h): legs spaced `spacing` (default 3 w, the self-coupling
    rule for a meandered DDR line), bumps `spacing` apart, centred on the
    run; the tallest h up to --amplitude that clears is found per side (no
    lower than the track width: a lower bump is a nub, except one bump for
    a need under 2 w), then k and h are picked to add exactly what is left;
  * clearance: the new centreline buffered by w/2 must keep the board's
    clearance (the loosest netclass in .kicad_pro, KiCad's 0.2 mm default)
    plus --margin from every other net's tracks, vias, pads and zone fills
    on that layer (a bump into a pour can cut it apart; --through-pours
    allows it, DRC still guards), from same-net copper that is not the run's own neighbour, from
    copper and track keepouts, and stay min_copper_edge_clearance inside the
    outline; bumps already placed this run are obstacles for the next;
  * a net that is too LONG cannot be fixed by lengthening it and is reported
    (route it shorter, or lengthen the reference).
Diff pairs (#476), measured with check_diffpair's own trunk and running-
skew functions, so the tuner and the check agree:
  * a pair in a length group is tuned AS A PAIR: coupled bumps on both legs
    (the outer leg's bump is spacing + 2 g wide, the inner one nests inside
    it at the pair pitch g, so both legs gain exactly 2 h and the skew does
    not move) for what both need, then single-leg bumps for the rest;
  * every pair's SKEW (end skew over max_skew_mm, or a running-skew
    excursion check_diffpair flags as diffpair_phase) is tuned on the short
    leg next to where the skew builds (the dynamic-phase rule: pro tools put
    the bump by the bend, not at one end): the profile's first excursion
    gives the place and the plateau it reaches the amount; the train sits
    on the nearest straight trunk run (a phase fix within half a phase
    window of it, sliding up to SLIDE_MM to find room), faces away from the
    partner, and every point of it stays within the coupling threshold
    minus PAIR_MARGIN_MM of the partner, so no sample turns uncoupled and
    the impedance (median gap) does not move. One fix per pair per pass; an
    excursion with no room falls through to the next, then the end skew.
    The partner is held to "no closer than this run already is" instead of
    the board clearance (a pair's gap is under it). --no-skew skips this,
    --pairs limits it; a --groups run leaves other pairs alone.
  * a run with same-net copper joined along its body (a T into a pull-up
    stub, a via) is never a candidate: a meander would cut the tap off.
The ops (remove the run, add_path the meander) go through route_edit.apply_ops
(the pipeline's only routing writer). Groups whose members are another
group's reference are tuned first (DQS before its byte lane, the clock
before address/command), and the whole pass repeats while it still gains
(at most --passes). Then kicad-cli DRC runs on the result against the board
before tuning; any grown error type rolls the board back (exit 2).

CLI:
  route_tune.py --pcb B.kicad_pcb --constraints c.json [--groups A,B]
      [--pairs NET,..] [--no-skew] [--amplitude MM] [--spacing MM]
      [--margin MM] [--passes N] [--dry-run] [--ops-out ops.json]
      [--no-drc] [--out-report r.json]
  exit 0 = every group passes check_length_groups and every tuned pair
  passes check_diffpair's skew and phase after tuning; 1 = something is
  still out (board keeps what was tuned); 2 = error (board untouched).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from shapely.geometry import LineString, Point  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402

import check_diffpair  # noqa: E402
import check_length_groups as clg  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "route_tune"
AMPLITUDE_MM = 1.0       # tallest bump tried
MIN_BUMP_MM = 0.05       # a smaller bump is not worth a corner pair
MARGIN_MM = 0.02         # over the clearance, against pcbnew's nm rounding
LADDER = (1.0, 0.8, 0.6, 0.45, 0.3, 0.2, 0.12, 0.07)   # x amplitude
EPS = 1e-6


# ------------------------------------------------------------ geometry

def meander_points(a, b, k: int, h: float, spacing: float, lead: float,
                   side: int, centre: float | None = None
                   ) -> list[tuple[float, float]] | None:
    """Centreline from a to b with k rectangular bumps of height h on `side`
    (+1 left of a->b, -1 right), legs `spacing` apart and bumps `spacing`
    apart, the train centred between the two leads (or on `centre`, mm from
    a, slid to fit). None when it can't fit."""
    ax, ay = a
    L = math.hypot(b[0] - ax, b[1] - ay)
    if k < 1 or L <= 0:
        return None
    ux, uy = (b[0] - ax) / L, (b[1] - ay) / L
    nx, ny = -uy * side, ux * side
    train = (2 * k - 1) * spacing
    room = L - 2 * lead
    if train > room + EPS:
        return None
    x = lead + (room - train) / 2
    if centre is not None:
        x = min(max(centre - train / 2, lead), lead + room - train)

    def at(s, t):
        return (round(ax + ux * s + nx * t, 6), round(ay + uy * s + ny * t, 6))

    pts = [(ax, ay)]
    for _ in range(k):
        pts += [at(x, 0), at(x, h), at(x + spacing, h), at(x + spacing, 0)]
        x += 2 * spacing
    pts.append((b[0], b[1]))
    return pts


def max_bumps(length: float, spacing: float, lead: float) -> int:
    room = length - 2 * lead
    return max(0, int(math.floor((room + spacing) / (2 * spacing) + EPS)))


class Obstacles:
    """Copper and keepouts a meander on one layer must clear, per layer."""

    def __init__(self, bg, clearance: float, edge: float, margin: float,
                 through_pours: bool = False):
        self.bg = bg
        self.through_pours = through_pours
        self.clear = clearance + margin
        self.edge = edge + margin
        self.inside = bg.outline.buffer(-self.edge) if not bg.outline.is_empty \
            else None
        self.extra: dict[str, list] = {}     # layer -> [(net, poly)] placed
        self._cache: dict[str, tuple] = {}
        self.dropped: set[int] = set()

    def _layer_items(self, layer):
        """(items, STRtree) for a layer; items are (net, poly, track|None)."""
        if layer not in self._cache:
            bg = self.bg
            items = [(t.net, t.poly, t) for t in bg.tracks_of(layer=layer)]
            items += [(v.net, v.poly, None) for v in bg.vias_of()
                      if v.spans(layer)]
            items += [(p.net, p.poly, None) for p in bg.pads_of(layer=layer)]
            if not self.through_pours:
                # another net's pour: a bump into it can cut it apart (core:
                # GND and VBUS_IN islands, 16 unconnected) - stay out
                for z in bg.zones_of(layer=layer):
                    if z.net:
                        fill = z.fill_on(layer)
                        if not fill.is_empty:
                            items.append((z.net, fill, None))
            for ra in bg.rule_areas:
                fl = ra.get("flags") or {}
                if (layer in ra["layers"] and not ra["outline"].is_empty
                        and fl.get("tracks") == "not_allowed"):
                    items.append((None, ra["outline"], None))
            tree = STRtree([it[1] for it in items]) if items else None
            self._cache[layer] = (items, tree)
        return self._cache[layer]

    def clears(self, poly, layer: str, net: str, own, soft=None) -> bool:
        """True when `poly` (a meander's copper) keeps clearance from all but
        the run itself and the same-net copper it joins (`own`: tracks).
        `soft` {net: run polygon} holds a diff pair's partner to "no closer
        than the run already is" (capped at the board clearance) instead of
        the board clearance: the pair's own gap is under it."""
        if self.inside is not None and not self.inside.contains(poly):
            return False
        reach = poly.buffer(self.clear)
        items, tree = self._layer_items(layer)
        hits = tree.query(reach, predicate="intersects") if tree is not None else []
        for i in hits:
            onet, opoly, trk = items[int(i)]
            if trk is not None and (trk in own or id(trk) in self.dropped):
                continue
            if onet == net and trk is None:
                continue          # own pads and vias: touching is a joint
            if soft and onet in soft and self._soft_ok(poly, opoly, soft[onet]):
                continue
            return False
        for onet, opoly in self.extra.get(layer, []):
            if soft and onet in soft:
                if not self._soft_ok(poly, opoly, soft[onet]):
                    return False
            elif reach.intersects(opoly):
                return False
        return True

    def _soft_ok(self, poly, opoly, ref) -> bool:
        need = min(self.clear, ref.distance(opoly) - 0.002)
        return poly.distance(opoly) >= max(0.0, need)

    def add(self, layer: str, net: str, poly) -> None:
        self.extra.setdefault(layer, []).append((net, poly))

    def drop(self, track) -> None:
        """A run replaced by its meander stops being an obstacle."""
        self.dropped.add(id(track))


def _neighbours(bg, track) -> set:
    """Same-net tracks sharing an end with `track` (they meet at its ends,
    which the meander keeps)."""
    ends = [track.shape.coords[0], track.shape.coords[-1]]
    out = {track}
    for t in bg.tracks_of(track.net):
        if t is track:
            continue
        for e in (t.shape.coords[0], t.shape.coords[-1]):
            if any(math.hypot(e[0] - x[0], e[1] - x[1]) <= (t.width + track.width) / 2
                   for x in ends):
                out.add(t)
    return out


def tapped(bg, track) -> bool:
    """True when same-net copper joins `track` along its BODY, not at its
    ends (a T into a stub, a via on the run): a meander there would cut the
    tap off, so the run is not a candidate."""
    a, b = track.shape.coords[0], track.shape.coords[-1]
    body = track.shape

    def inner(pt, reach):
        if body.distance(Point(pt)) > reach:
            return False
        return min(math.hypot(pt[0] - a[0], pt[1] - a[1]),
                   math.hypot(pt[0] - b[0], pt[1] - b[1])) > reach + 0.01
    for t in bg.tracks_of(track.net):
        if t is track or t.layer != track.layer:
            continue
        for e in (t.shape.coords[0], t.shape.coords[-1]):
            if inner(e, (t.width + track.width) / 2):
                return True
    for v in bg.vias_of(track.net):
        if v.spans(track.layer) and inner(v.at, (v.diameter + track.width) / 2):
            return True
    return False


def plan_run(obs: Obstacles, track, need: float, *, amplitude: float,
             spacing: float, side: int | None = None, soft=None,
             centre: float | None = None, keep=None, slide: float = 0.0):
    """(points, added_mm) for the meander on one straight run adding up to
    `need`, or None. Tries both sides (or only `side`); picks the side that
    holds the most. `soft` passes through to Obstacles.clears; `centre` (mm
    from the run's start) places the train there instead of mid-run; `keep`
    (pts -> bool) is an extra test every train must pass; `slide` lets a
    centred train that doesn't fit move up to that far along the run,
    nearest first."""
    a, b = track.shape.coords[0], track.shape.coords[-1]
    w = track.width
    lead = max(0.5, 2 * w)
    L = track.length
    kmax = max_bumps(L, spacing, lead)
    if kmax < 1:
        return None
    own = _neighbours(obs.bg, track)

    def ok(pts):
        if keep is not None and not keep(pts):
            return False
        poly = LineString(pts).buffer(w / 2, cap_style="round",
                                      join_style="round")
        return obs.clears(poly, track.layer, track.net, own, soft)

    centres: list = [centre]
    if centre is not None and slide > 0:
        step = 2 * spacing
        for m in range(1, int(slide / step) + 1):
            centres += [centre + m * step, centre - m * step]
    best = None
    for sd in ((1, -1) if side is None else (side,)):
        for f in LADDER:
            h = amplitude * f
            if h < w - EPS and f != LADDER[0]:
                break             # a bump lower than the track is a nub
            # the train this need takes at this height (fewer if it hits
            # something), where it is wanted - not the whole run
            kk = min(kmax, max(1, math.ceil(need / (2 * h) - EPS)))
            at = None
            while kk >= 1:
                at = next((c for c in centres
                           if (pts := meander_points(a, b, kk, h, spacing, lead,
                                                     sd, c)) and ok(pts)), False)
                if at is not False:
                    break
                kk = kk // 2
            if kk < 1:
                continue
            cap = 2 * h * kk
            if best is None or cap > best[0] + EPS:
                best = (cap, sd, h, kk, at)
            if cap >= need - EPS:
                break
    if best is None:
        return None
    cap, sd, hcap, kk, at = best
    use = min(need, cap)
    k2 = kk
    while k2 > 1 and use / (2 * k2) < max(MIN_BUMP_MM, w) - EPS:
        k2 -= 1                   # many nubs: fewer, taller bumps
    for kq in (k2, kk):
        h = use / (2 * kq)
        if h > hcap + EPS:
            continue
        for c in ([at] + [c for c in centres if c != at]):
            pts = meander_points(a, b, kq, h, spacing, lead, sd, c)
            if pts and ok(pts):
                return pts, 2 * h * kq
    return None


# ------------------------------------------------------------ targets

def targets(facts: dict, group: dict) -> dict[str, dict]:
    """Per member net: {length, lo, hi, aim} absolute mm, from a group's
    check_length_groups facts. lo/hi None = unbounded."""
    ref = facts.get("reference_mm")
    win = facts.get("window_mm")
    cap = facts.get("not_longer_than_mm")
    refs = set(facts.get("ref_nets") or [])
    caps = set(facts.get("cap_nets") or [])
    out = {}
    for row in facts["nets"]:
        net = row["net"]
        if not row.get("connected"):
            continue
        lo, hi = -math.inf, math.inf
        if ref is not None and win and net not in refs:
            lo, hi = ref + win[0], ref + win[1]
        if facts.get("min_mm") is not None:
            lo = max(lo, float(facts["min_mm"]))
        if facts.get("max_mm") is not None:
            hi = min(hi, float(facts["max_mm"]))
        if cap is not None and net not in caps:
            hi = min(hi, cap)
        if lo == -math.inf:
            continue                         # nothing asks it to be longer
        aim = (lo + hi) / 2 if hi != math.inf else lo + 0.1
        out[net] = {"length": row["length_mm"], "lo": lo, "hi": hi, "aim": aim}
    return out


def group_order(groups: list[dict], board_nets) -> list[int]:
    """Indices with every group that holds another's reference nets as
    members first (DQS0 before BYTE0)."""
    members = [set(clg.match_nets(g["nets"], board_nets)[0]) for g in groups]
    refs = [set(clg.match_nets(clg._ref_nets(g)
                               + list(g.get("not_longer_than") or []),
                               board_nets)[0]) for g in groups]
    order, left = [], list(range(len(groups)))
    while left:
        ready = [i for i in left
                 if not any(j != i and members[j] & refs[i] for j in left)]
        pick = ready[0] if ready else left[0]       # a cycle: config order
        order.append(pick)
        left.remove(pick)
    return order


# ------------------------------------------------------------ pairs (#476)

PAIR_MARGIN_MM = 0.05    # a skew bump's top stays this far inside coupling
SETTLE_MM = 1.0          # running skew flat this long (coupled) = plateau
SLIDE_MM = 5.0           # a skew train may move this far from its target
PARALLEL = 1e-3          # |sin| between a pair's two runs


def _unit(a, b):
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    return ((b[0] - a[0]) / L, (b[1] - a[1]) / L, L) if L > 0 else (0, 0, 0)


def away_side(bg, track, partner: str, reach: float) -> int | None:
    """The meander side (+1 left of the run, -1 right) facing AWAY from the
    partner's copper on this layer, or None when the partner is not within
    `reach` of the run's middle (an uncoupled run: either side)."""
    a, b = track.shape.coords[0], track.shape.coords[-1]
    ux, uy, L = _unit(a, b)
    mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    near = [t.shape for t in bg.tracks_of(partner) if t.layer == track.layer]
    if not L or not near:
        return None
    line = min(near, key=lambda ln: ln.distance(mid))
    if line.distance(mid) > reach:
        return None
    q = line.interpolate(line.project(mid))
    left = -uy * (q.x - mid.x) + ux * (q.y - mid.y)
    return -1 if left > 0 else 1


def partner_soft(track, partner: str) -> dict:
    """Obstacles.clears `soft` for a pair leg's run: new copper may come no
    closer to the partner than this run already is."""
    return {partner: track.poly}


def stays_coupled(bg, track, partner: str, cmax: float):
    """keep-test for plan_run: every point of the bump train (the path
    between the run's two ends) stays within cmax - PAIR_MARGIN_MM of the
    partner's centreline on this layer, so no sample turns uncoupled."""
    from shapely.ops import unary_union  # noqa: PLC0415
    geo = unary_union([t.shape for t in bg.tracks_of(partner)
                       if t.layer == track.layer])
    lim = cmax - PAIR_MARGIN_MM

    def keep(pts) -> bool:
        if geo.is_empty:
            return False
        line = LineString(pts[1:-1])
        L = line.length
        k = max(1, int(math.ceil(L / check_diffpair.SAMPLE_MM)))
        return all(geo.distance(line.interpolate(i * L / k)) <= lim + 1e-6
                   for i in range(k + 1))
    return keep


def coupled_points(a, b, c, d, k: int, h: float, spacing: float, lead: float):
    """(outer, inner) centrelines for k coupled bumps on a pair's two
    parallel runs: outer a->b, inner c->d at offset g on one side; the bumps
    go to the OTHER side (away from inner). The outer bump is spacing + 2 g
    wide, the inner one nests in it at g, so each bump adds exactly 2 h to
    BOTH legs (two convex and two concave corners each: the skew is
    unchanged). None when the train doesn't fit the runs' overlap."""
    ux, uy, L = _unit(a, b)
    if k < 1 or not L:
        return None
    e = -uy * (c[0] - a[0]) + ux * (c[1] - a[1])
    side = -1 if e > 0 else 1
    g = abs(e)
    nx, ny = -uy * side, ux * side
    tc = (c[0] - a[0]) * ux + (c[1] - a[1]) * uy
    td = (d[0] - a[0]) * ux + (d[1] - a[1]) * uy
    lo = max(0.0, min(tc, td)) + lead + g
    hi = min(L, max(tc, td)) - lead - g
    W = spacing + 2 * g
    train = k * W + (k - 1) * spacing
    if train > hi - lo + EPS:
        return None
    x = lo + (hi - lo - train) / 2

    def at(s, t):
        return (round(a[0] + ux * s + nx * t, 6), round(a[1] + uy * s + ny * t, 6))

    outer, inner = [tuple(a)], []
    for _ in range(k):
        outer += [at(x, 0), at(x, h), at(x + W, h), at(x + W, 0)]
        inner += [at(x + g, -g), at(x + g, h - g), at(x + W - g, h - g),
                  at(x + W - g, -g)]
        x += W + spacing
    outer.append(tuple(b))
    if td < tc:
        inner.reverse()
    return outer, [tuple(c)] + inner + [tuple(d)]


def coupled_bumps(L, g, spacing, lead) -> int:
    room = L - 2 * (lead + g)
    return max(0, int(math.floor((room + spacing) / (2 * spacing + 2 * g) + EPS)))


def _parallel_partner(run, cands, reach: float):
    """The partner run lying parallel beside `run` (offset <= reach) with
    the longest overlap, or None."""
    a, b = run.shape.coords[0], run.shape.coords[-1]
    ux, uy, L = _unit(a, b)
    best = None
    for t in cands:
        if t.layer != run.layer or len(t.shape.coords) != 2:
            continue
        c, d = t.shape.coords[0], t.shape.coords[-1]
        vx, vy, M = _unit(c, d)
        if not M or abs(ux * vy - uy * vx) > PARALLEL:
            continue
        e1 = -uy * (c[0] - a[0]) + ux * (c[1] - a[1])
        e2 = -uy * (d[0] - a[0]) + ux * (d[1] - a[1])
        if abs(e1 - e2) > 0.005 or abs(e1) > reach or abs(e1) < 1e-6:
            continue
        tc = (c[0] - a[0]) * ux + (c[1] - a[1]) * uy
        td = (d[0] - a[0]) * ux + (d[1] - a[1]) * uy
        ov = min(L, max(tc, td)) - max(0.0, min(tc, td))
        if ov > 0 and (best is None or ov > best[0]):
            best = (ov, t)
    return best[1] if best else None


def plan_coupled(obs, run, mate, need: float, *, amplitude: float,
                 spacing: float):
    """(outer, inner, outer_track, added_each) for coupled bumps on the pair
    runs `run` and `mate`, adding up to `need` to each, or None. Tries both
    legs as the outer one (bumps away from the other)."""
    w = max(run.width, mate.width)
    lead = max(0.5, 2 * w)
    own = _neighbours(obs.bg, run) | _neighbours(obs.bg, mate)
    best = None
    for out, inn in ((run, mate), (mate, run)):
        a, b = out.shape.coords[0], out.shape.coords[-1]
        c, d = inn.shape.coords[0], inn.shape.coords[-1]
        ux, uy, L = _unit(a, b)
        g = abs(-uy * (c[0] - a[0]) + ux * (c[1] - a[1]))
        kmax = coupled_bumps(L, g, spacing, lead)
        if kmax < 1:
            continue

        def ok(k, h, out=out, inn=inn, a=a, b=b, c=c, d=d):
            got = coupled_points(a, b, c, d, k, h, spacing, lead)
            if got is None:
                return None
            po = LineString(got[0]).buffer(out.width / 2)
            pi = LineString(got[1]).buffer(inn.width / 2)
            # each leg vs the other's OTHER copper: no closer than now
            if (obs.clears(po, out.layer, out.net, own, partner_soft(out, inn.net))
                    and obs.clears(pi, inn.layer, inn.net, own,
                                   partner_soft(inn, out.net))):
                return got
            return None
        for f in LADDER:
            h = amplitude * f
            if h < w - EPS and f != LADDER[0]:
                break
            kk = min(kmax, max(1, math.ceil(need / (2 * h) - EPS)))
            while kk >= 1 and not ok(kk, h):
                kk //= 2
            if kk < 1:
                continue
            cap = 2 * h * kk
            if best is None or cap > best[0] + EPS:
                best = (cap, out, h, kk, ok)
            if cap >= need - EPS:
                break
    if best is None:
        return None
    cap, out, hcap, kk, ok = best
    use = min(need, cap)
    k2 = kk
    while k2 > 1 and use / (2 * k2) < max(MIN_BUMP_MM, w) - EPS:
        k2 -= 1
    for kq in (k2, kk):
        h = use / (2 * kq)
        if h > hcap + EPS:
            continue
        got = ok(kq, h)
        if got:
            return got[0], got[1], out, 2 * h * kq
    return None


def _plateau(samples, i0: int, sign: int, step=check_diffpair.SAMPLE_MM) -> float:
    """|running skew| where an excursion starting at sample i0 stops
    growing: the skew the bend there built (not the next bend's too)."""
    best = sign * samples[i0][2]
    quiet = 0.0
    for i in range(i0 + 1, len(samples)):
        r = sign * samples[i][2]
        if r > best + 0.01:
            best, quiet = r, 0.0
        else:
            quiet += min(samples[i][0] - samples[i - 1][0], 1.5 * step)
            if quiet > SETTLE_MM:
                break
    return best


def skew_targets(ph, max_skew: float) -> list[tuple]:
    """[(i0, sign, amount, phase?)] to fix on a pair's profile, in order:
    each flagged phase excursion (at its start, the plateau it builds), then
    an end skew over max_skew (where it last built up)."""
    smp = ph["samples"]
    out = [(e["i0"], e["sign"], _plateau(smp, e["i0"], e["sign"]), True)
           for e in ph["flagged"]]
    E = ph["end_skew_mm"]
    if abs(E) > max_skew and smp:
        sign = 1 if E > 0 else -1
        i0 = len(smp) - 1
        while i0 > 0 and sign * smp[i0 - 1][2] > abs(E) / 2:
            i0 -= 1
        out.append((i0, sign, abs(E), False))
    return out


def pair_specs(bg, cons) -> list[dict]:
    return [s for s in check_diffpair.resolve_pairs(bg, cons)
            if not check_diffpair.is_sense(s)
            and s["p"] in bg.nets and s["n"] in bg.nets]


def pair_geometry(bg, spec) -> dict | None:
    """What the skew tuner needs from check_diffpair for one pair (its own
    functions, so the tuner and the check agree): nominal pitch, coupling
    threshold and the running-skew profile. None for an unrouted/open pair."""
    p, n = spec["p"], spec["n"]
    if not bg.tracks_of(p) or not bg.tracks_of(n):
        return None
    nom = spec.get("gap_mm")
    if nom is None:
        _, _, s0 = check_diffpair.coupling_stats(bg, p, n, 1e9)
        nom = s0["nominal_gap_mm"] or 0.2
    cmax = _cmax(bg, {**spec, "gap_mm": nom})
    term = check_diffpair.matched_terminals(
        bg, p, n, float(spec.get("term_pair_mm", check_diffpair.TERM_PAIR_MM)))
    eps = check_diffpair._pair_epsilon(bg, p, n)
    ph = check_diffpair.pair_phase(bg, spec, term, cmax, eps)
    if ph is None:
        return None
    return {"nom": float(nom), "coupling_max": cmax, "phase": ph}


def plan_skew(bg, specs, opts, obs, used) -> tuple[list, list, list]:
    """One fix per pair per pass: bumps on the SHORT leg's straight trunk
    runs nearest (along the trunk) to where the excursion starts, facing away
    from the partner, no taller than keeps the leg coupled (check_diffpair's
    coupling threshold minus PAIR_MARGIN_MM), so the uncoupled length and the
    impedance don't move."""
    ops, tuned, findings = [], [], []
    for spec in specs:
        p, n = spec["p"], spec["n"]
        geo = pair_geometry(bg, spec)
        if geo is None:
            continue
        ph = geo["phase"]
        max_skew = float(spec.get("max_skew_mm", check_diffpair.MAX_SKEW_MM))
        tried = []
        for i0, sign, amount, phase in skew_targets(ph, max_skew):
            got = _fix_skew(bg, spec, geo, i0, sign, amount, phase, opts, obs, used)
            tried.append(got)
            if got["added_mm"] > 0:
                break
        if not tried:
            continue
        for got in tried:
            ops += got.pop("ops")
            pos = got.pop("pos")
            tuned.append({"pair": [p, n], "kind": "skew", **got,
                          "end_skew_mm": checklib.rnd(ph["end_skew_mm"]),
                          "peak_mm": checklib.rnd(ph["peak_mm"])})
            if got["added_mm"] < got["need_mm"] - opts.resolution:
                findings.append(violation(
                    SCRIPT, "warning", pos, None, got["net"], [],
                    f"diff pair {p}/{n}: {got['net']} needs {got['need_mm']:.3f} mm "
                    f"of skew bumps near {got['at_mm']:.1f} mm along its trunk; "
                    f"only {got['added_mm']:.3f} mm fit (bumps <= "
                    f"{got['hcap_mm']:.2f} mm tall to stay coupled"
                    + (f", within {got['reach_mm']:.1f} mm" if got["phase"] else "")
                    + ")", SCRIPT, kind="tune_skew_no_room", pair=[p, n],
                    need_mm=got["need_mm"], added_mm=got["added_mm"]))
    return ops, tuned, findings


def _fix_skew(bg, spec, geo, i0, sign, amount, phase, opts, obs, used) -> dict:
    """Plan one skew fix (plan_skew's doc); returns its tuned row with
    "ops" and "pos" for the caller."""
    p, n = spec["p"], spec["n"]
    ph = geo["phase"]
    leg, mate = (n, p) if sign > 0 else (p, n)
    smp = ph["samples"][i0]
    s_at = smp[1] if leg == n else smp[0]
    pieces = ph["trunks"]["n" if leg == n else "p"]
    hcap = min(opts.amplitude, geo["coupling_max"] - geo["nom"] - PAIR_MARGIN_MM)
    # a phase fix placed farther than half a window from where the skew
    # builds only moves the excursion; an end-skew fix may go anywhere
    reach = ph["window_mm"] / 2 if phase else math.inf
    cands = []
    for line, _layer, s0, tr in pieces:
        if (len(tr.shape.coords) != 2 or not tr.uuid or tr in used
                or tapped(bg, tr)):
            continue
        lo, hi = s0, s0 + line.length
        dist = 0.0 if lo <= s_at <= hi else min(abs(s_at - lo), abs(s_at - hi))
        # the train goes on the trunk point nearest s_at (the run's coords
        # may run against the trunk)
        here = line.interpolate(min(max(s_at - lo, 0.0), line.length))
        cands.append((dist, -tr.length, id(tr), tr, tr.shape.project(here)))
    cands.sort(key=lambda x: x[:3])
    ops, added, where = [], 0.0, []
    for dist, _, _, tr, centre in cands:
        if amount - added <= opts.resolution or hcap <= MIN_BUMP_MM or dist > reach:
            break
        side = away_side(bg, tr, mate, geo["coupling_max"])
        got = plan_run(obs, tr, amount - added, amplitude=hcap,
                       spacing=opts.spacing or 3 * tr.width, side=side,
                       soft=partner_soft(tr, mate), centre=centre,
                       keep=stays_coupled(bg, tr, mate, geo["coupling_max"]),
                       slide=min(reach, SLIDE_MM))
        if got is None:
            continue
        pts, mm = got
        used.add(tr)
        obs.drop(tr)
        obs.add(tr.layer, leg, LineString(pts).buffer(tr.width / 2))
        ops.append({"op": "remove", "uuid": tr.uuid})
        ops.append({"op": "add_path", "points": [list(q) for q in pts],
                    "width": tr.width, "layer": tr.layer, "net": leg})
        added += mm
        where.append(checklib.rnd(dist))
    return {"net": leg, "phase": phase, "need_mm": checklib.rnd(amount),
            "added_mm": checklib.rnd(added), "at_mm": checklib.rnd(s_at),
            "run_offsets_mm": where, "hcap_mm": checklib.rnd(hcap),
            "reach_mm": reach if phase else None, "ops": ops, "pos": smp[3]}


def plan_pair_group(bg, obs, used, g, spec, needs: dict, paths: dict, opts,
                    cmax: float) -> tuple[list, list, list, dict]:
    """A diff pair in a length group, tuned AS A PAIR: coupled bumps on both
    legs for what both need (the skew stays), then single-leg bumps (away
    from the partner) for what one leg needs more. `needs` {net: mm},
    `paths` {net: path tracks}. Returns ops, tuned, findings, added {net: mm}."""
    p, n = spec["p"], spec["n"]
    ops, findings = [], []
    added = {p: 0.0, n: 0.0}
    both = min(needs[p], needs[n])
    mate_cands = [t for t in paths[n] if t not in used]
    runs = sorted((tr for tr in paths[p] if len(tr.shape.coords) == 2
                   and tr.uuid and tr not in used and not tapped(bg, tr)),
                  key=lambda tr: -tr.length)
    for tr in runs:
        if both - added[p] <= opts.resolution:
            break
        mate = _parallel_partner(tr, [t for t in mate_cands if t not in used
                                      and t.uuid and not tapped(bg, t)], cmax)
        if mate is None:
            continue
        got = plan_coupled(obs, tr, mate, both - added[p], amplitude=opts.amplitude,
                           spacing=opts.spacing or 3 * max(tr.width, mate.width))
        if got is None:
            continue
        outer, inner, out_tr, mm = got
        inn_tr = mate if out_tr is tr else tr
        for t, pts in ((out_tr, outer), (inn_tr, inner)):
            used.add(t)
            obs.drop(t)
            obs.add(t.layer, t.net, LineString(pts).buffer(t.width / 2))
            ops.append({"op": "remove", "uuid": t.uuid})
            ops.append({"op": "add_path", "points": [list(q) for q in pts],
                        "width": t.width, "layer": t.layer, "net": t.net})
        added[p] += mm
        added[n] += mm
    coupled = added[p]
    for leg, mate in ((p, n), (n, p)):
        left = needs[leg] - added[leg]
        for tr in sorted((t for t in paths[leg] if len(t.shape.coords) == 2
                          and t.uuid and t not in used and not tapped(bg, t)),
                         key=lambda t: -t.length):
            if left <= opts.resolution:
                break
            side = away_side(bg, tr, mate, cmax)
            got = plan_run(obs, tr, left, amplitude=opts.amplitude,
                           spacing=opts.spacing or 3 * tr.width, side=side,
                           soft=partner_soft(tr, mate))
            if got is None:
                continue
            pts, mm = got
            used.add(tr)
            obs.drop(tr)
            obs.add(tr.layer, leg, LineString(pts).buffer(tr.width / 2))
            ops.append({"op": "remove", "uuid": tr.uuid})
            ops.append({"op": "add_path", "points": [list(q) for q in pts],
                        "width": tr.width, "layer": tr.layer, "net": leg})
            added[leg] += mm
            left -= mm
    tuned = [{"group": g.get("name"), "pair": [p, n], "kind": "pair_length",
              "net": leg, "need_mm": checklib.rnd(needs[leg]),
              "coupled_mm": checklib.rnd(coupled),
              "added_mm": checklib.rnd(added[leg])} for leg in (p, n)]
    for leg in (p, n):
        if added[leg] < needs[leg] - opts.resolution:
            findings.append(violation(
                SCRIPT, "warning", clg._rep_point(bg, leg), None, leg, [],
                f"length group {g.get('name')}: pair {p}/{n} could take only "
                f"{added[leg]:.3f} of {needs[leg]:.3f} mm on {leg} "
                f"({coupled:.3f} mm coupled); no room on its straight runs",
                SCRIPT, kind="tune_no_room", group=g.get("name"),
                added_mm=checklib.rnd(added[leg]),
                need_mm=checklib.rnd(needs[leg])))
    return ops, tuned, findings, added


# ------------------------------------------------------------ one pass

def plan_pass(bg, groups, cons, opts, sel) -> tuple[list, list, list]:
    """(ops, tuned facts, findings) for one pass over the selected groups on
    board `bg` (measured once; later groups see earlier ops as obstacles)."""
    floors = _floors(bg)
    obs = Obstacles(bg, floors["clearance"], floors["edge_clearance"],
                    opts.margin, through_pours=opts.through_pours)
    specs = pair_specs(bg, cons)
    pair_of = {}
    for sp in specs:
        pair_of[sp["p"]] = pair_of[sp["n"]] = sp
    cache: dict = {}
    ops, tuned, findings = [], [], []
    used: set = set()
    planned: dict[str, float] = {}       # net -> mm added this pass
    for i in group_order(groups, bg.nets):
        g = groups[i]
        if sel and g.get("name") not in sel:
            continue
        _, facts = clg.check_group(bg, g, cache)
        tg = targets(facts, g)
        members = {r["net"] for r in facts["nets"]}
        pair_need: dict[str, float] = {}
        for net, t in sorted(tg.items()):
            L = t["length"] + planned.get(net, 0.0)
            if L > t["hi"] + EPS:
                findings.append(violation(
                    SCRIPT, "error", None, None, net, [],
                    f"length group {g.get('name')}: {net} is {L:.3f} mm, over "
                    f"its {t['hi']:.3f} mm limit; a meander only lengthens - "
                    f"route it shorter or lengthen the reference",
                    SCRIPT, kind="tune_too_long", group=g.get("name"),
                    length_mm=checklib.rnd(L), hi_mm=checklib.rnd(t["hi"])))
                continue
            if L >= t["lo"] - EPS:
                continue
            if net in pair_of:
                pair_need[net] = t["aim"] - L     # tuned as a pair below
                continue
            need = t["aim"] - L
            m = cache[(net, tuple(g.get("terminals") or ()))]
            runs = sorted((tr for tr in m.get("path_tracks") or []
                           if len(tr.shape.coords) == 2 and tr.uuid
                           and tr not in used and not tapped(bg, tr)),
                          key=lambda tr: -tr.length)
            added = 0.0
            for tr in runs:
                if need - added <= opts.resolution:
                    break
                got = plan_run(obs, tr, need - added, amplitude=opts.amplitude,
                               spacing=opts.spacing or 3 * tr.width)
                if got is None:
                    continue
                pts, mm = got
                used.add(tr)
                obs.drop(tr)
                obs.add(tr.layer, net, LineString(pts).buffer(tr.width / 2))
                ops.append({"op": "remove", "uuid": tr.uuid})
                ops.append({"op": "add_path", "points": [list(p) for p in pts],
                            "width": tr.width, "layer": tr.layer, "net": net})
                added += mm
            planned[net] = planned.get(net, 0.0) + added
            tuned.append({"group": g.get("name"), "net": net,
                          "length_mm": checklib.rnd(L),
                          "aim_mm": checklib.rnd(t["aim"]),
                          "added_mm": checklib.rnd(added),
                          "short_by_mm": checklib.rnd(max(0.0, t["lo"] - L - added))})
            if L + added < t["lo"] - EPS:
                findings.append(violation(
                    SCRIPT, "warning", clg._rep_point(bg, net), None, net, [],
                    f"length group {g.get('name')}: {net} could take only "
                    f"{added:.3f} of {need:.3f} mm of meander on its straight "
                    f"runs (no room); still {t['lo'] - L - added:.3f} mm short",
                    SCRIPT, kind="tune_no_room", group=g.get("name"),
                    added_mm=checklib.rnd(added), need_mm=checklib.rnd(need)))
        done: set = set()
        term = tuple(g.get("terminals") or ())
        for net in sorted(pair_need):
            spec = pair_of[net]
            p, n = spec["p"], spec["n"]
            if p in done:
                continue
            done.update((p, n))
            needs = {}
            for leg in (p, n):
                if leg in pair_need:
                    needs[leg] = pair_need[leg]
                elif leg in members:
                    needs[leg] = 0.0      # a member already in range
                else:
                    needs[leg] = pair_need[n if leg == p else p]  # keep skew
            paths = {}
            for leg in (p, n):
                m = cache.get((leg, term)) or clg.net_length(bg, leg, list(term) or None)
                paths[leg] = m.get("path_tracks") or []
            o, t, f, add = plan_pair_group(bg, obs, used, g, spec, needs, paths,
                                           opts, _cmax(bg, spec))
            ops += o
            tuned += t
            findings += f
            for leg, mm in add.items():
                planned[leg] = planned.get(leg, 0.0) + mm
    if getattr(opts, "skew", False):
        o, t, f = plan_skew(bg, [sp for sp in specs if not opts.pairs
                                 or sp["p"] in opts.pairs or sp["n"] in opts.pairs],
                            opts, obs, used)
        ops += o
        tuned += t
        findings += f
    return ops, tuned, findings


def _cmax(bg, spec) -> float:
    nom = spec.get("gap_mm")
    if nom is None:
        _, _, s0 = check_diffpair.coupling_stats(bg, spec["p"], spec["n"], 1e9)
        nom = s0["nominal_gap_mm"] or 0.2
    factor = float(spec.get("coupling_factor", check_diffpair.COUPLING_FACTOR))
    return max(factor * nom, nom + 0.5)


def _floors(bg) -> dict:
    import route_critical  # noqa: PLC0415 - lazy: pulls KRT helpers
    pro = Path(bg.path).with_suffix(".kicad_pro")
    return route_critical.grading_floors(pro)


# ------------------------------------------------------------ CLI

def run(argv=None):
    ap = argparse.ArgumentParser(description="Length-tune routed length groups.")
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", required=True)
    ap.add_argument("--groups", help="comma list of group names (default all)")
    ap.add_argument("--amplitude", type=float, default=AMPLITUDE_MM,
                    help="tallest meander bump, mm")
    ap.add_argument("--spacing", type=float,
                    help="meander leg spacing, mm (default 3 x track width)")
    ap.add_argument("--margin", type=float, default=MARGIN_MM,
                    help="added to the board clearance, mm")
    ap.add_argument("--resolution", type=float, default=0.01,
                    help="stop adding below this much, mm")
    ap.add_argument("--passes", type=int, default=4)
    ap.add_argument("--no-skew", dest="skew", action="store_false",
                    help="leave diff-pair skew / running skew alone")
    ap.add_argument("--pairs", help="comma list of pair nets (P or N) to "
                    "skew-tune (default every pair; none when --groups is set)")
    ap.add_argument("--through-pours", action="store_true",
                    help="let meanders push into other nets' zone fills "
                         "(default: a fill is an obstacle; DRC still guards)")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan only: write no board (see --ops-out)")
    ap.add_argument("--ops-out", help="write the planned ops here")
    ap.add_argument("--no-drc", action="store_true",
                    help="skip the kicad-cli DRC before/after")
    ap.add_argument("--out-report")
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    cons = checklib.load_json(args.constraints, "constraints")
    groups = cons.get("length_groups") or []
    if not isinstance(groups, list):
        raise CheckError("constraints length_groups is not a list")
    for i, g in enumerate(groups):
        clg.validate_group(i, g)
    sel = set(filter(None, (args.groups or "").split(","))) or None
    args.pairs = set(filter(None, (args.pairs or "").split(","))) or None
    if sel and not args.pairs:
        args.skew = False             # a group run leaves other pairs alone

    all_ops, tuned, findings, passes = [], [], [], 0
    backup = None
    drc_before = None
    if not args.dry_run and not args.no_drc:
        # route_edit refills the zones after the ops, so the baseline is
        # the board refilled too (not saved): a board whose stored fills
        # are stale would otherwise read as damage (pinned core: 16
        # unconnected from the refill alone)
        drc_before = _drc(pcb, refill=bool(geom.load_board(pcb).zones_of()))
    try:
        for passes in range(1, max(1, args.passes) + 1):
            bg = geom.load_board(pcb)
            ops, t, f = plan_pass(bg, groups, cons, args, sel)
            findings = f                     # the last pass's open findings
            tuned.extend({**x, "pass": passes} for x in t)
            if not ops:
                break
            all_ops.extend(ops)
            if args.dry_run:
                break
            if backup is None:
                backup = Path(tempfile.mkdtemp(prefix=".aiee_tune_",
                                               dir=pcb.parent)) / pcb.name
                shutil.copy2(pcb, backup)
            import route_edit  # noqa: PLC0415 - lazy: SWIG worker plumbing
            route_edit.apply_ops(pcb, ops, refill=bool(bg.zones_of()))
        drc = None
        if backup is not None and drc_before is not None:
            after = _drc(pcb)
            import route_critical  # noqa: PLC0415
            drc = route_critical.drc_delta(drc_before, after)
            if drc["new"]:
                shutil.copy2(backup, pcb)
                raise CheckError(f"tuning grew DRC errors {drc['new']}; "
                                 "board rolled back")
    finally:
        if backup is not None:
            shutil.rmtree(backup.parent, ignore_errors=True)

    if args.ops_out:
        Path(args.ops_out).write_text(json.dumps(
            {"version": 1, "ops": all_ops}, indent=1), encoding="utf-8")
    bg = geom.load_board(pcb)
    check = clg.run(["--pcb", str(pcb), "--constraints", args.constraints])[0]
    left = [v for v in check["violations"] if v["severity"] == "error"
            and (not sel or v.get("group") in sel)]
    tuned_pairs = {tuple(t["pair"]) for t in tuned if t.get("pair")}
    pair_rows = []
    if tuned_pairs and not args.dry_run:
        import check_diffpair as cdp  # noqa: PLC0415
        dp = cdp.run(["--pcb", str(pcb), "--constraints", args.constraints])[0]
        left += [v for v in dp["violations"] if tuple(v.get("pair") or ()) in tuned_pairs
                 and v.get("kind") in ("diffpair_skew", "diffpair_phase")]
        pair_rows = [{"pair": r["pair"], "skew_mm": r["skew_mm"],
                      "uncoupled_p_mm": r["uncoupled_p_mm"],
                      "uncoupled_n_mm": r["uncoupled_n_mm"],
                      "phase_peak_mm": (r.get("phase") or {}).get("peak_mm")}
                     for r in dp["checked"] if tuple(r["pair"]) in tuned_pairs]
    violations = findings + ([] if args.dry_run else left)
    payload = checklib.report(
        SCRIPT, str(pcb), violations, dry_run=bool(args.dry_run),
        passes=passes, ops=len(all_ops), tuned=tuned,
        groups=[{"name": g["name"], "spread_mm": g["spread_mm"],
                 "reference_mm": g["reference_mm"]} for g in check["groups"]],
        pairs=pair_rows,
        drc=drc if not args.dry_run else None)
    return payload, args.out_report


def _drc(pcb: Path, refill: bool = False) -> dict:
    import env  # noqa: PLC0415
    import kc  # noqa: PLC0415
    cli = env.find_kicad_cli()
    if cli is None:
        raise CheckError("kicad-cli not found (env.py); pass --no-drc to skip")
    return kc.run_drc(cli, pcb, all_track_errors=True, refill=refill)


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
