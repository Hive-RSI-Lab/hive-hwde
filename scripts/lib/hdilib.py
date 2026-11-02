#!/usr/bin/env python
"""hdilib.py - routing on the via spans of a board's HDI build (#477).

check_vias judges spans; this module uses them. It answers, from a board's own
stack block and its stackups.yaml entry:

  board_build(bg)            -> (name, entry) when the board is an HDI build
  spans_of(entry, cu)        -> the micro/buried/blind spans, sized from
                                jlc_capabilities.yaml `hdi:`
  stack_to(...)              -> the shortest stack of spans from one copper
                                layer to another
  plan_field(...)            -> a whole ball field's escape, as route_edit ops
  dru_rules(...)             -> the via-type and in-field DRU rules, the one
                                text route_escape and rules_gen both write

The escape is a peel, the standard HDI fan-out: outer rings of balls leave on
the ball's own layer, the next rings through a microvia in the pad on the next
layer, the next through a stack of two, and so on, through a buried via when
the build has one. A ball that has not escaped yet keeps a via on every layer
it could still leave on, so each layer is swept outer ring to inner, and a ball
takes the first clear path: straight out, or a 45 degree jog into the channel
between two vias further out. How many rings a layer holds comes out of the
geometry, not a table: 0.4 mm holds one (no track fits between 0.25 mm via pads
at JLC's 3 mil), 0.5 mm holds two, a sparse field more.

Diff pairs aren't escaped coupled here: each ball escapes alone.

Not a CLI - library only.
"""
from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REFERENCE = Path(__file__).resolve().parents[2] / "reference"
EPS = 1e-6
PLANE_MIN_BALLS = 6     # KRT's plane-ball rule, without a plane to reach
LASER_DRILL_MM = 0.1    # JLC's default laser hole (jlc_capabilities hdi:)
HDI_RULE_PREFIX = "aiee_hdi_"
KEEP_LAST = ("aiee_hv_", "aiee_pkg_rating_", "aiee_iso_")


def load_stackups() -> dict:
    return yaml.safe_load((REFERENCE / "stackups.yaml")
                          .read_text("utf-8"))["stackups"]


def load_hdi_caps() -> dict:
    return yaml.safe_load((REFERENCE / "jlc_capabilities.yaml")
                          .read_text("utf-8"))["hdi"]


def is_hdi(entry: dict | None) -> bool:
    return any(r.get("kind") != "through" for r in (entry or {}).get("vias") or [])


def board_build(bg, name: str | None = None) -> tuple[str | None, dict | None]:
    """(entry name, entry) when the board's stack block matches an HDI build
    of stackups.yaml, else (None, None). Never a guess from the layer count: a
    standard build drills through vias only."""
    import check_vias
    stackups = load_stackups()
    if name:
        e = stackups.get(name)
        return (name, e) if is_hdi(e) else (None, None)
    nm, e = check_vias.match_entry(bg.stackup, stackups)
    return (nm, e) if is_hdi(e) else (None, None)


@dataclass(frozen=True)
class Span:
    kind: str           # micro | buried | blind
    top: str
    bot: str
    i: int              # copper index of top
    j: int              # copper index of bot, > i
    drill: float
    size: float

    def touches(self, k: int) -> bool:
        return self.i <= k <= self.j

    def as_op(self, at, net: str) -> dict:
        return {"op": "add_via", "at": list(at), "size": self.size,
                "drill": self.drill, "net": net, "kind": self.kind,
                "top": self.top, "bot": self.bot}


def _round_up(v: float, step: float = 0.005) -> float:
    return round(math.ceil(v / step - 1e-9) * step, 4)


def spans_of(entry: dict, cu: list[str], gaps: list[float] | None = None,
             caps: dict | None = None) -> list[Span]:
    """The build's non-through spans, each at the smallest size JLC makes. A
    laser hole's drill is at least its dielectric (1:1 at most), so an 8-layer
    build's 0.116 mm prepreg gets 0.12, not the default 0.1. gaps: dielectric
    per copper gap (default: the entry's own stack)."""
    import check_vias
    caps = caps or load_hdi_caps()
    gaps = gaps if gaps is not None else check_vias._entry_gaps(entry)
    out = []
    for r in entry.get("vias") or []:
        if r["kind"] == "through" or r["from"] not in cu or r["to"] not in cu:
            continue
        i, j = sorted((cu.index(r["from"]), cu.index(r["to"])))
        if r["kind"] == "micro":
            depth = sum(gaps[i:j])
            drill = max(LASER_DRILL_MM, _round_up(depth / caps["laser_max_aspect"]))
            size = round(drill + caps["laser_pad_over_drill_mm"], 4)
        else:
            drill = float(caps["buried_drill_mm"][0])
            size = round(drill + caps["buried_pad_over_drill_mm"], 4)
        out.append(Span(r["kind"], cu[i], cu[j], i, j, drill, size))
    return sorted(out, key=lambda s: (s.i, s.j))


def stack_to(spans: list[Span], start: int, target: int) -> list[Span] | None:
    """The stack from copper start to copper target with the fewest vias, or
    None. A buried via reaches every layer it crosses."""
    seen, q = {start}, deque([(start, [])])
    while q:
        k, path = q.popleft()
        if k == target:
            return path
        for s in spans:
            if not s.touches(k):
                continue
            # along a span every layer it crosses is reachable
            for n in range(s.i, s.j + 1):
                if n not in seen:
                    seen.add(n)
                    q.append((n, path + [s]))
    return None


def reach_order(spans: list[Span], start: int, usable: list[int]) -> list[tuple[int, list[Span]]]:
    """(copper index, stack) of every usable layer the build reaches from
    start, nearest first."""
    out = []
    for k in usable:
        st = [] if k == start else stack_to(spans, start, k)
        if st is not None:
            out.append((k, st))
    return sorted(out, key=lambda t: (len(t[1]), abs(t[0] - start)))


# ------------------------------------------------------------ geometry

def _seg_pt(ax, ay, bx, by, px, py) -> float:
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = 0.0 if ll <= 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
    return math.hypot(ax + t * dx - px, ay + t * dy - py)


def _cross(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def seg_seg(a, b, c, d) -> float:
    """Shortest distance between two segments."""
    d1 = _cross(*a, *b, *c)
    d2 = _cross(*a, *b, *d)
    d3 = _cross(*c, *d, *a)
    d4 = _cross(*c, *d, *b)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return 0.0
    return min(_seg_pt(*a, *b, *c), _seg_pt(*a, *b, *d),
               _seg_pt(*c, *d, *a), _seg_pt(*c, *d, *b))


@dataclass
class Obstacle:
    a: tuple[float, float]
    b: tuple[float, float]
    half: float         # copper half width
    net: str | None
    hole: float = 0.0   # hole radius; 0 = no hole

    def clear(self, p, q, half: float, c: float, hole_c: float, net) -> bool:
        if net and self.net == net:
            return True
        d = seg_seg(p, q, self.a, self.b)
        if d < self.half + half + c - EPS:
            return False
        return not self.hole or d >= self.hole + half + hole_c - EPS


def _local_axes(angle: float):
    """(to_local, to_board) for a footprint at angle degrees (geom's frame,
    bgalib._local's inverse)."""
    a = math.radians(angle or 0.0)
    ca, sa = math.cos(a), math.sin(a)
    return ((lambda dx, dy: (dx * ca - dy * sa, dx * sa + dy * ca)),
            (lambda u, v: (u * ca + v * sa, -u * sa + v * ca)))


@dataclass
class Ball:
    number: str
    net: str | None
    at: tuple[float, float]    # board frame
    uv: tuple[float, float]    # footprint frame
    ij: tuple[int, int]
    ring: int
    role: str = "signal"       # signal | plane | held
    layer: int | None = None   # copper index it escapes on
    path: list = field(default_factory=list)


def _sides(ball: Ball, rows: int, cols: int):
    """(outward unit vector in the footprint frame, balls to the edge) per
    side, nearest edge first; ties go up, down, left, right."""
    i, j = ball.ij
    s = [(j, 0, (0.0, -1.0)), (rows - 1 - j, 1, (0.0, 1.0)),
         (i, 2, (-1.0, 0.0)), (cols - 1 - i, 3, (1.0, 0.0))]
    return [(d, n) for n, _, d in sorted(s, key=lambda t: t[:2])]


def _candidates(ball: Ball, rows: int, cols: int, pitch: float, stub: float,
                centre: tuple[float, float]):
    """Paths in the footprint frame, cheapest first: straight out, then a 45
    degree jog of half a pitch into the channel beside it (away from the
    field's centre line first, so two rings share a side without crossing),
    after 0, 1, 2 ... pitches straight."""
    u, v = ball.uv
    for d, n in _sides(ball, rows, cols):
        lat = (-d[1], d[0])
        reach = (n + 0.5) * pitch + stub + pitch / 2
        straight = [(u, v), (u + d[0] * reach, v + d[1] * reach)]
        yield straight
        off = (u - centre[0]) * lat[0] + (v - centre[1]) * lat[1]
        signs = (1.0, -1.0) if off >= 0 else (-1.0, 1.0)
        for steps in range(0, n + 1):
            for sg in signs:
                h = pitch / 2
                p1 = (u + d[0] * steps * pitch, v + d[1] * steps * pitch)
                p2 = (p1[0] + d[0] * h + lat[0] * sg * h,
                      p1[1] + d[1] * h + lat[1] * sg * h)
                rest = reach - steps * pitch - h
                if rest <= 0:
                    continue
                p3 = (p2[0] + d[0] * rest, p2[1] + d[1] * rest)
                yield ([(u, v), p2, p3] if steps == 0 else [(u, v), p1, p2, p3])


@dataclass
class FieldPlan:
    ops: list[dict]
    balls: list[Ball]
    per_layer: dict[str, int]
    vias: dict[str, int]
    plane_balls: int
    left: list[str]            # nets of balls with no escape
    held: list[str]

    def fact(self) -> dict:
        return {"per_layer": self.per_layer, "vias": self.vias,
                "plane_balls": self.plane_balls, "left": self.left}


def plan_field(bga, pads: list, fp_at, fp_angle: float, cu: list[str],
               spans: list[Span], usable: list[str], *, track: float,
               clearance: float, hole_clearance: float,
               plane_of: dict[str, str] | None = None,
               held: set[str] | None = None,
               existing: dict[str, list[Obstacle]] | None = None,
               stub: float | None = None) -> FieldPlan:
    """The peel of one ball field (module docstring). pads: the field's ball
    pads (geom.Pad); plane_of: net -> inner plane layer the ball just needs to
    reach; held: nets given no escape, whose balls keep room for a via;
    existing: other copper already on the board, per layer."""
    plane_of, held, existing = plane_of or {}, held or set(), existing or {}
    to_local, to_board = _local_axes(fp_angle)
    start = cu.index(bga.layer)
    p = bga.pitch
    stub = p + 0.3 if stub is None else stub
    half = track / 2
    pts = [(pd, to_local(pd.center[0] - fp_at[0], pd.center[1] - fp_at[1]))
           for pd in pads]
    u0 = min(q[0] for _, q in pts)
    v0 = min(q[1] for _, q in pts)
    cols, rows = bga.cols, bga.rows
    centre = (u0 + (cols - 1) * p / 2, v0 + (rows - 1) * p / 2)
    balls = []
    for pd, (u, v) in pts:
        i, j = round((u - u0) / p), round((v - v0) / p)
        ball = Ball(pd.number, pd.net, tuple(pd.center), (u, v), (i, j),
                    min(i, j, cols - 1 - i, rows - 1 - j))
        if pd.net in plane_of and stack_to(spans, start, cu.index(plane_of[pd.net])) is not None:
            ball.role = "plane"
        elif pd.net in held:
            ball.role = "held"
        balls.append(ball)
    balls.sort(key=lambda b: (b.ring, b.ij[1], b.ij[0]))

    order = reach_order(spans, start, [cu.index(x) for x in usable])
    deepest = order[-1][1] if order else []
    ball_half = bga.pad_d / 2

    def via_at(k: int, stack: list[Span]) -> tuple[float, float] | None:
        """(copper radius, hole radius) of a stack on copper k."""
        on = [s for s in stack if s.touches(k)]
        if not on:
            return None
        return (max(s.size for s in on) / 2, max(s.drill for s in on) / 2)

    ops: list[dict] = []
    per_layer: dict[str, int] = {}
    placed: dict[int, list[Obstacle]] = {}
    for b in balls:
        if b.role == "plane":
            b.layer = cu.index(plane_of[b.net])

    for k, stack in order:
        lname = cu[k]
        obs = list(existing.get(lname, [])) + placed.setdefault(k, [])
        # what a track on this layer must keep clear of: the balls on their
        # own layer; below it, the via of every ball not gone yet
        for b in balls:
            if k == start:
                obs.append(Obstacle(b.at, b.at, ball_half, b.net))
                continue
            if b.role == "plane":
                st = stack_to(spans, start, b.layer)
            elif b.layer is None:
                st = deepest
            else:
                continue
            r = via_at(k, st or [])
            if r:
                obs.append(Obstacle(b.at, b.at, r[0], b.net, r[1]))
        for b in balls:
            if b.layer is not None or b.role != "signal" or not b.net:
                continue
            own = via_at(k, stack) if k != start else None
            for path in _candidates(b, rows, cols, p, stub, centre):
                seg = [(fp_at[0] + to_board(*q)[0], fp_at[1] + to_board(*q)[1])
                       for q in path]
                seg[0] = b.at
                # the ball's own via replaces its deeper one on this layer;
                # its copper is the ball's net, so it never blocks its own path
                if all(o.clear(a, c, half, clearance, hole_clearance, b.net)
                       for a, c in zip(seg, seg[1:]) for o in obs):
                    b.layer, b.path = k, seg
                    for a, c in zip(seg, seg[1:]):
                        obs.append(Obstacle(a, c, half, b.net))
                        ops.append({"op": "add_track",
                                    "start": [round(a[0], 5), round(a[1], 5)],
                                    "end": [round(c[0], 5), round(c[1], 5)],
                                    "width": track, "layer": lname,
                                    "net": b.net})
                    obs.append(Obstacle(b.at, b.at, (own or (ball_half,))[0], b.net,
                                        own[1] if own else 0.0))
                    per_layer[lname] = per_layer.get(lname, 0) + 1
                    break

    vias: dict[str, int] = {}
    for b in balls:
        if b.layer is None or b.layer == start or not b.net:
            continue
        for s in stack_to(spans, start, b.layer) or []:
            ops.append(s.as_op(b.at, b.net))
            kind = f"{s.kind} {s.top}-{s.bot}"
            vias[kind] = vias.get(kind, 0) + 1
    left = sorted({b.net for b in balls
                   if b.net and b.layer is None and b.role == "signal"})
    return FieldPlan(ops, balls, per_layer, vias,
                     sum(1 for b in balls if b.role == "plane"), left,
                     sorted({b.net for b in balls if b.role == "held" and b.net}))


def obstacles_from_board(bg, region, skip_ref: str) -> dict[str, list[Obstacle]]:
    """Copper already on the board near a field, per layer."""
    from shapely.geometry import Point
    out: dict[str, list[Obstacle]] = {}
    for t in bg.tracks_of():
        if region.intersects(t.poly):
            c = list(t.shape.coords)
            for a, b in zip(c, c[1:]):
                out.setdefault(t.layer, []).append(Obstacle(tuple(a), tuple(b), t.width / 2, t.net))
    for v in bg.vias_of():
        if region.contains(Point(v.at)):
            for ly in v.layers:
                out.setdefault(ly, []).append(
                    Obstacle(v.at, v.at, v.diameter / 2, v.net, v.drill / 2))
    for pd in bg.pads_of():
        if pd.ref != skip_ref and region.contains(Point(pd.center)):
            hole = max(pd.drill) / 2 if pd.drill else 0.0
            for ly in pd.layers:
                out.setdefault(ly, []).append(
                    Obstacle(pd.center, pd.center, max(pd.size) / 2, pd.net, hole))
    return out


# ------------------------------------------------------------ DRU rules

def _rule(name: str, constraint: str, value: float, cond: str) -> str:
    return (f'(rule "{HDI_RULE_PREFIX}{name}"\n'
            f'\t(constraint {constraint} (min {value:.4f}mm))\n'
            f'\t(condition "{cond}")\n)\n')


def via_type_rules(entry: dict, caps: dict | None = None) -> list[tuple[str, str, float, str]]:
    """(name, constraint, min, condition) of the via-type rules: JLC's HDI
    numbers for what its HDI line makes, in place of the through-via floors.
    Only the kinds this build has."""
    caps = caps or load_hdi_caps()
    kinds = {r["kind"] for r in entry.get("vias") or []}
    ring = caps["laser_pad_over_drill_mm"] / 2
    out = []
    a_or_b = "A.Via_Type == '{0}' || B.Via_Type == '{0}'"
    if "micro" in kinds:
        d = float(caps["laser_drill_mm"][0])
        c = "A.Via_Type == 'Micro'"
        out += [("micro_hole", "hole_size", d, c),
                ("micro_dia", "via_diameter", d + caps["laser_pad_over_drill_mm"], c),
                ("micro_ring", "annular_width", ring, c),
                ("micro_hole_clearance", "hole_clearance", caps["hole_to_trace_mm"],
                 a_or_b.format("Micro"))]
    for kind, word in (("buried", "Buried"), ("blind", "Blind")):
        if kind not in kinds:
            continue
        d = float(caps["buried_drill_mm"][0])
        pad = caps["buried_pad_over_drill_mm"]
        c = f"A.Via_Type == '{word}'"
        out += [(f"{kind}_hole", "hole_size", d, c),
                (f"{kind}_dia", "via_diameter", d + pad, c),
                (f"{kind}_ring", "annular_width", pad / 2, c),
                (f"{kind}_hole_clearance", "hole_clearance", caps["hole_to_trace_mm"],
                 a_or_b.format(word)),
                (f"{kind}_hole_to_hole", "hole_to_hole", caps["hole_to_hole_diff_net_mm"],
                 a_or_b.format(word))]
    return out


def field_rules(ref: str, track: float, clearance: float, condition: str
                ) -> list[tuple[str, str, float, str]]:
    """A field's own rules: the escape track and clearance, the HDI line's,
    for what touches the part's courtyard."""
    tag = "".join(c if c.isalnum() else "_" for c in ref)
    return [(f"field_track_{tag}", "track_width", track, condition),
            (f"field_clearance_{tag}", "clearance", clearance, condition)]


def ensure_rules(dru_text: str, rules: list[tuple[str, str, float, str]]
                 ) -> tuple[str, list[str]]:
    """dru_text with each rule present once, ahead of the HV, package and
    isolation rules, which KiCad's later-wins must leave in force."""
    changed = []
    if not dru_text.strip():
        dru_text = "(version 1)\n"
    for name, constraint, value, cond in rules:
        new = _rule(name, constraint, value, cond)
        full = HDI_RULE_PREFIX + name
        pat = re.compile(r'\(rule "' + re.escape(full) + r'"\n(?:[ \t].*\n)*\)(?:\n|$)')
        m = pat.search(dru_text)
        if m:
            if m.group(0) != new:
                dru_text = dru_text[:m.start()] + new + dru_text[m.end():]
                changed.append(full)
            continue
        hv = re.search(r'^\(rule "(?:' + "|".join(KEEP_LAST) + ')', dru_text, re.M)
        if hv:
            dru_text = dru_text[:hv.start()] + new + dru_text[hv.start():]
        else:
            dru_text = dru_text.rstrip("\n") + "\n" + new
        changed.append(full)
    return dru_text, changed
