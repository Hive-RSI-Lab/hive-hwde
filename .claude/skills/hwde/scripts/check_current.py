"""check_current.py - trace width vs current per power net (SPEC 6.3).

Per net in constraints.json["power"] with a budgeted current:
 - every track segment's width is compared against the IPC-2152 minimum for
   the full budget at dT (worst case: no per-branch current attribution -
   the whole rail may flow through any segment; override per region below);
 - pour neckdowns: each zone fill is eroded by required_width/2 - if the
   net's via attachment points then fall in separate components, the pour
   necks below the requirement between them (polygon-erosion equivalent of
   the spec's medial-axis min-width);
 - layer transitions: net vias are clustered (<= 2 mm) and each cluster needs
   ceil(I / via_amps) vias (default 0.5 A per via, per spec).

Stitch vias (PCB-0023-A /SW: one pour on four layers, ~120 stitch vias, 112
one-via "transitions" each asked for 24 vias at 12 A). A via is a STITCH
when no same-net track touches it, every same-net pad it touches is in the
net's pour on that layer (the fill covers >= PAD_IN_POUR_FRAC of the pad's
area), the via itself touches fill on every layer where it touches such a
pad, and it touches fill on >= 2 layers. A thermal-relief pad is not in the
pour: it reaches the pour only through spokes, so its current crosses the
via, even when part of the via's ring straddles the relief gap into the
fill (PCB-0023-A D201). A cluster made
only of stitches is not judged as a transition. Instead every fill piece
pair on two layers is judged once, counting every via and plated
through-hole joining those two pieces (the per-path count of a pour-to-pour
hop, same ceil(I / via_amps) threshold, override at the group centroid,
extras stitch=true). A pair is skipped when one piece is a dead end - no pad
or track touches it and every barrel on it joins the other piece - because
then it only parallels the other pour and no load current crosses into it
and back out. A cluster with any via a track or a loose pad feeds keeps the
per-cluster rule, so a track that changes layers is judged as before.

Pad-exit necks (PCB-0019-A SW_L1/SW_L2: 0.30 mm off a TPS63001's 0.24 mm
pads at 0.5 mm pitch, where a 0.5 mm track breaks clearance to the next pin,
so no layout clears the finding). A run of undersized same-net, same-layer
segments touching end to end is accepted, not reported, when all hold:
 1. one of its segments ends on a same-net pad on that layer;
 2. every segment is at least min(required, w_pad) wide, where w_pad is the
    short side of the narrowest pad the run touches - the pad already
    constricts the current to w_pad, so a track no narrower than the copper
    it leaves adds no tighter section (a pad as wide as the requirement
    leaves nothing to excuse, so the full rule applies);
 3. the run's centerline outside the net's other copper on that layer (pads,
    vias, zone fill, the tracks not in the run) totals <= PAD_EXIT_MAX_MM.
    This bounds the neck to the pad's escape: a sub-millimetre neck between
    wide copper at both ends is heat-sunk by it, which IPC-2152's
    long-conductor charts do not describe; a longer one is a routed trace
    and must meet the full width.
Accepted runs are listed in facts["pad_exit_necks"] for the reviewer.

Pour connectivity is judged across layers (PCB-0017-B GND: the F.Cu fill is
split into islands, each stitched to one unbroken B.Cu pour). When a fill on
one layer is eroded, the net's fills on every other layer are eroded by
their own required_width/2, and the pieces are joined through every same-net
via and plated through-hole whose layers both pieces sit on. The attachment
vias of the fill under test must all land in one joined component. Pieces on
the SAME layer are never joined through a via alone (a via touching two
same-layer pieces and nothing else is not a path), so a single-layer pour is
judged exactly as before. Via ampacity at those stitches stays the
transition-via rule's job. A via joins the pieces its copper disk touches;
a plated pad, whose centre no fill reaches (the fill stops at its thermal
relief), joins on each layer the fill that touches its copper, within
THT_JOIN_MM of the pad (PCB-0021-A GND: J4's shell pins never joined).

Leaf branches (PCB-0021-A +SYS: a leg of the pour, bridged over /CHG_A by a
B.Cu strip and two vias, feeds only R6, a 470R LED resistor, and was judged
at the rail's 2.2 A). A finding that still fails after any override is
re-judged at the current its far side can draw, when that is less:
 - a part's draw is bounded only for a two-pad chip resistor (ref R<n>):
   sqrt(P/R) from its Value and the imperial size in its footprint id, P the
   highest common rating for that size (RES_RATING_W). Any other part may
   draw the whole budget, and so may a neck side that reaches no pad: copper
   leading nowhere known is not a leaf. A via-cluster side reaching no pad
   is passive (it only joins cluster vias), but some bounded part must lie
   past the cluster;
 - pour neck: the fill's attachment vias are grouped by the components they
   land in at the failing width. Each group floods the net's raw copper on
   every layer (through vias and plated pads) with the other groups'
   territories (eroded pieces and vias) as walls, and collects the pads it
   reaches. At most one group may reach an unbounded part; every other group
   must meet the rest at one place only (else it may carry a bypass). The
   neck is re-tested at the summed bound of those groups' parts;
 - via cluster: the cluster's vias are cut and the net's raw copper falls
   into components. At most one holds an unbounded part, no cluster via
   lies wholly inside it, and every other side reaches it through exactly
   one cluster via. The cluster needs ceil(bound / via_amps) vias.
Re-judged findings that pass are listed in facts["leaf_branches"]; ones that
still fail carry extras leaf_loads.

Override reach (LEARNINGS 2026-07-28/29: overrides used to feed only track
widths, making the via rule net-wide and unsatisfiable for branch taps):
 - a via CLUSTER whose centroid falls inside an override region is judged at
   the override current (need = ceil(override / via_amps));
 - a pour NECK is first evaluated at the full budget; if the failing neck's
   reported position falls inside an override region the fill is re-tested at
   the override requirement - passing drops the violation, failing re-emits
   the re-test's neck at the override current. Approximation: the re-test's
   neck position may itself land outside the region (the reported point is a
   representative sample of the split, not the whole neck).

Every undersized_track violation carries extras "bridge": true|false - true
when the segment is a cut edge of the net's connectivity graph (tracks + vias
+ pads + zone fills, lib/netconn.py), i.e. the sole path: the whole judged
current really crosses it. false = a parallel same-net path exists (which may
still be jointly undersized - severity is NOT reduced; the label is for the
fixer, LEARNINGS 2026-07-29 "no bridge awareness").

Two non-bridge cases are dropped instead (PCB-0018-A: signal taps that
leave FET pins sitting in the PHASE/LS_SRC pour that carries the phase
current), and listed by position in facts["pour_taps"]:
 - pour-shunted: both ends sit on the net's pour - inside its fill on the
   track's layer, or on a same-net pad or via whose copper touches a
   same-net fill on any layer. The track is in parallel with that pour, so
   the pour carries the current, and the pour-neck pass judges the pour;
 - inside pad copper: the track lies wholly inside same-net pad and via
   copper on its layer (a stub drawn over a pad adds no section).
A bridge is never dropped: when it is the sole path, the whole judged
current may cross it, and only a declared override can say otherwise.

Plane-fed rails ("plane_fed": true on the entry): the rail's trunk is its
zone fill, so every via is a single-pin leaf tap by construction and the
net-wide budget is unattributable per cluster/segment (LEARNINGS 2026-07-29:
27 one-via clusters on +3V3, doubling infeasible). Semantics:
 - no zone fill anywhere on the net -> error "plane_missing", then the entry
   is checked as if plane_fed were false;
 - pour necks: unchanged, error at the full budget (a trunk neck is real);
 - via clusters / track segments OUTSIDE any override region: still checked
   at the full budget but downgraded to severity "warning" with extras
   advisory=true (a labeled worst-case screen);
 - INSIDE an override region: error at the override current (a declared
   regulator-feed tap stays enforceable).

IPC-2152 basis: 10 degC chart readings at 1 oz outer copper (interpolation
table vendored from kicad-happy, MIT), converted to copper cross-section so
inner layers scale by their (thinner) copper thickness from geom's stackup.
Below the first table row the requirement interpolates linearly to (0, 0),
consistent with published chart readings (~0.20 mm at 0.4 A). dT != 10 scales
the equivalent current by (10/dT)^0.44 (IPC curve-family approximation).

Derived return-net coverage (T6; the pd-trigger 5A GND choke class): no board
ever declares its return net, so return-path ampacity went unchecked - the
worst real defect of any run (5A return choked through 0.2 mm GND necks rated
0.80 A, caught only by the reviewer AFTER verify passed 8/8). When the largest
declared rail is >= RETURN_SYNTH_MIN_A (3.0 A) and the return net (optional
per-entry "return_net", default "GND") exists on the board with a zone fill
and is not itself declared, ONE entry is synthesized for it at the max rail's
budget with plane-fed semantics. ALL derived findings - pour necks included -
are advisory warnings with extras derived=true: the budget is a heuristic
(max of declared rails), so error severity is not justified (measured: the
SHIPPED pd-trigger board necks 0.86 mm vs the 1.75 mm 5 A requirement on a
plane fed from multiple directions). Below the threshold the screen is noise:
the rf4 golden fires a 0.07 mm sliver neck at a 0.15 A budget.

CLI: --pcb board.kicad_pcb --constraints constraints.json [--out report.json]
     exit 0/1/2 per SPEC section 6.

constraints.json["power"] entries:
    {"net": "+3V3",              # required, exact board net name
     "current_a": 0.4,           # required, budgeted rail current
     "dt_c": 10,                 # optional temperature rise (default 10)
     "via_amps": 0.5,            # optional per-via ampacity (default 0.5)
     "plane_fed": true,          # optional, see plane-fed semantics above
     "overrides": [              # optional per-region current attribution
        {"near": [x, y], "radius_mm": 2.0, "current_a": 0.1}]}
Segments whose midpoint (via clusters: centroid; pour necks: reported neck
position) falls in an override region are checked against the override
current instead of the full budget (branch traces / taps).
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
import netconn  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "check_current"
# IPC-2152 minimum trace width (mm) at 1 oz outer copper, dT = 10 degC.
# Chart-reading interpolation table vendored from kicad-happy (MIT).
IPC2152_1OZ_10C = [(0.0, 0.0), (0.5, 0.25), (1.0, 0.50), (2.0, 1.10),
                   (3.0, 1.80), (5.0, 3.50), (7.0, 5.50), (10.0, 9.0)]
OZ1_MM = 0.035                 # 1 oz copper thickness the table assumes
VIA_AMPS_DEFAULT = 0.5         # spec: >= 1 via per 0.5 A at transitions
VIA_CLUSTER_MM = 2.0           # vias within this distance share current
WIDTH_TOL_MM = 1e-3
RETURN_SYNTH_MIN_A = 3.0       # derive return-net coverage at/above this rail
PLANE_HINT_SINGLE_VIA_FRAC = 0.8  # plane_fed_candidate hint threshold
PAD_EXIT_MAX_MM = 1.0          # longest accepted pad-exit neck (docstring)
TOUCH_MM = 1e-3                # end-to-end / end-on-pad contact tolerance
THT_JOIN_MM = 1.0              # plated pad joins fill this far out (relief)
PAD_IN_POUR_FRAC = 0.9         # fill covers this much of a pad "in the pour"


def width_1oz_10c(current_a: float) -> float:
    """Table interpolation; linear extrapolation beyond the last row."""
    pts = IPC2152_1OZ_10C
    if current_a <= 0:
        return 0.0
    for (i0, w0), (i1, w1) in zip(pts, pts[1:]):
        if current_a <= i1:
            return w0 + (current_a - i0) / (i1 - i0) * (w1 - w0)
    (i0, w0), (i1, w1) = pts[-2], pts[-1]
    return w1 + (current_a - i1) / (i1 - i0) * (w1 - w0)


def required_width_mm(current_a: float, dt_c: float, cu_mm: float) -> float:
    """Minimum width on a layer with `cu_mm` copper for current at dT."""
    i_equiv = current_a * (10.0 / dt_c) ** 0.44 if dt_c > 0 else current_a
    area_mm2 = width_1oz_10c(i_equiv) * OZ1_MM
    return area_mm2 / cu_mm


def cluster_vias(vias: list, max_gap: float = VIA_CLUSTER_MM) -> list[list]:
    """Union-find grouping of vias by center distance <= max_gap."""
    parent = list(range(len(vias)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(vias)):
        for j in range(i + 1, len(vias)):
            d = math.hypot(vias[i].at[0] - vias[j].at[0],
                           vias[i].at[1] - vias[j].at[1])
            if d <= max_gap:
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i, v in enumerate(vias):
        groups.setdefault(find(i), []).append(v)
    return list(groups.values())


def region_current(entry: dict, pos) -> float | None:
    """Override current for a position, or None when no region covers it."""
    for ov in entry.get("overrides", []):
        near = ov.get("near")
        if near and math.hypot(pos[0] - near[0],
                               pos[1] - near[1]) <= ov.get("radius_mm", 2.0):
            return float(ov["current_a"])
    return None


def segment_current(entry: dict, midpoint) -> float:
    ov = region_current(entry, midpoint)
    return float(entry["current_a"]) if ov is None else ov


def pad_exit_necks(bg: geom.BoardGeom, net: str, thin: list) -> dict:
    """Accepted pad-exit necks (module docstring). `thin` lists
    (Track, required_mm) for every undersized segment of `net`; returns
    {id(Track): info} for the segments of each accepted run."""
    accepted: dict[int, dict] = {}
    by_layer: dict[str, list] = {}
    for t, req in thin:
        by_layer.setdefault(t.layer, []).append((t, req))
    for layer, items in by_layer.items():
        # runs = undersized segments touching end to end (union-find)
        parent = list(range(len(items)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i][0].shape.distance(items[j][0].shape) <= TOUCH_MM:
                    parent[find(i)] = find(j)
        runs: dict[int, list] = {}
        for i, it in enumerate(items):
            runs.setdefault(find(i), []).append(it)
        pads = bg.pads_of(net, layer)
        for run in runs.values():
            ends = [Point(c) for t, _ in run
                    for c in (t.shape.coords[0], t.shape.coords[-1])]
            touched = [p for p in pads
                       if any(p.poly.distance(e) <= TOUCH_MM for e in ends)]
            if not touched:
                continue                        # 1. not a pad exit
            w_pad = min(min(p.size) for p in touched)
            if any(t.width + WIDTH_TOL_MM < min(req, w_pad) for t, req in run):
                continue                        # 2. narrower than the pad
            ids = {id(t) for t, _ in run}
            anchor = [p.poly for p in pads] + \
                [v.poly for v in bg.vias_of(net, layer)] + \
                [t.poly for t in bg.tracks_of(net, layer) if id(t) not in ids] + \
                [z.fill_on(layer) for z in bg.zones_of(net, layer)]
            line = unary_union([t.shape for t, _ in run])
            free = line.difference(unary_union(anchor)).length
            if free > PAD_EXIT_MAX_MM + WIDTH_TOL_MM:
                continue                        # 3. longer than an escape
            pad = min(touched, key=lambda p: min(p.size))
            info = {"pad": f"{pad.ref}.{pad.number}", "layer": layer,
                    "pad_width_mm": checklib.rnd(w_pad),
                    "neck_mm": checklib.rnd(min(t.width for t, _ in run)),
                    "length_mm": checklib.rnd(free)}
            for t, _ in run:
                accepted[id(t)] = info
    return accepted


def pour_shunted(bg: geom.BoardGeom, net: str, t) -> bool:
    """True when both ends of track `t` sit on the net's pour: inside its
    fill on t's layer, or on a same-net pad or via whose copper touches a
    same-net fill on any layer (module docstring, pour taps)."""
    fills = {l: bg.zone_fill(net, l) for l in bg.layers_with_zone(net)}
    if not fills:
        return False
    own = fills.get(t.layer)
    items = bg.pads_of(net, t.layer) + bg.vias_of(net, t.layer)

    def anchored(xy) -> bool:
        p = Point(xy)
        if own is not None and own.buffer(TOUCH_MM).contains(p):
            return True
        return any(it.poly.buffer(TOUCH_MM).contains(p)
                   and any(fills[l].intersects(it.poly)
                           for l in it.layers if l in fills)
                   for it in items)
    return anchored(t.shape.coords[0]) and anchored(t.shape.coords[-1])


def inside_pad_copper(bg: geom.BoardGeom, net: str, t) -> bool:
    """True when track `t` lies wholly inside same-net pad and via copper
    on its layer: a stub drawn over a pad adds no section of its own."""
    cover = [p.poly for p in bg.pads_of(net, t.layer)] + \
        [v.poly for v in bg.vias_of(net, t.layer)]
    if not cover:
        return False
    return t.shape.difference(unary_union(cover)).length <= TOUCH_MM


def _stitches(bg: geom.BoardGeom, net: str, fills: dict) -> list[dict]:
    """Per same-net via and plated through-hole, {layer: geometry} of the
    copper it joins on that layer: the places where fills on different
    layers join. A via joins whatever its copper disk touches (a fill can
    end on the via's ring short of its centre). A plated pad's centre sits
    in no fill - the fill stops at its thermal relief -
    so it joins, on each layer whose raw fill (`fills[layer]`) touches its
    copper, that touching fill within THT_JOIN_MM of the pad: the ring
    around its relief. The spokes' own ampacity is not this check's to
    judge, as the via barrel's is the transition-via rule's."""
    out = [{l: v.poly for l in v.layers} for v in bg.vias_of(net)]
    for p in bg.pads_of(net):
        if p.drill is None or len(p.layers) < 2:
            continue
        ring = p.poly.buffer(THT_JOIN_MM)
        joins = {}
        for l in p.layers:
            f = fills.get(l)
            if f is None or f.is_empty:
                continue
            touching = [g for g in getattr(f, "geoms", [f])
                        if g.distance(p.poly) <= TOUCH_MM]
            if touching:
                joins[l] = unary_union(touching).intersection(ring)
        out.append(joins)
    return out


def _pieces(fill, radius: float) -> list:
    eroded = fill.buffer(-radius) if radius > 0 else fill
    return [] if eroded.is_empty else list(getattr(eroded, "geoms", [eroded]))


def fill_pieces(bg: geom.BoardGeom, net: str) -> dict[str, list]:
    """{layer: [polygon, ...]} - the net's merged zone fill on each layer,
    split into its connected pieces (abutting zones merge into one)."""
    out = {}
    for layer in bg.layers_with_zone(net):
        fill = bg.zone_fill(net, layer)
        if not fill.is_empty:
            out[layer] = list(getattr(fill, "geoms", [fill]))
    return out


def via_pieces(bg: geom.BoardGeom, net: str, pieces: dict) -> list:
    """Per via of `net` (bg.vias_of order): (is_stitch, frozenset of the
    (layer, piece index) fill pieces its copper touches). A via is a stitch
    when no same-net track touches it, every same-net pad it touches is in
    the net's pour on that layer (fill covers >= PAD_IN_POUR_FRAC of it, so
    not a thermal-relief pad), the via touches fill on each layer where it
    touches such a pad, and it touches fill on >= 2 layers - it only joins
    pour to pour (module docstring, stitch vias)."""
    fill = {l: unary_union(ps) for l, ps in pieces.items()}

    def in_pour(p, l) -> bool:
        f = fill.get(l)
        return f is not None and p.poly.area > 0 and \
            f.intersection(p.poly).area >= PAD_IN_POUR_FRAC * p.poly.area

    out = []
    for v in bg.vias_of(net):
        touched = frozenset(
            (l, i) for l in v.layers for i, p in enumerate(pieces.get(l, ()))
            if p.intersects(v.poly))
        tracked = any(t.poly.intersects(v.poly)
                      for l in v.layers for t in bg.tracks_of(net, l))
        on_fill = {l for l, _ in touched}
        loose_pad = any(
            p.poly.intersects(v.poly)
            and (l not in on_fill or not in_pour(p, l))
            for l in v.layers for p in bg.pads_of(net, l))
        stitch = not tracked and not loose_pad and len(on_fill) >= 2
        out.append((stitch, touched))
    return out


def pth_pieces(bg: geom.BoardGeom, net: str, pieces: dict) -> list:
    """(pad, frozenset of touched fill pieces) per plated multi-layer pad
    of `net`: its barrel joins pours like a via does."""
    return [(p, frozenset(
        (l, i) for l in p.layers for i, q in enumerate(pieces.get(l, ()))
        if q.intersects(p.poly)))
        for p in bg.pads_of(net) if p.drill is not None and len(p.layers) > 1]


def anchored_pieces(bg: geom.BoardGeom, net: str, pieces: dict) -> set:
    """Fill pieces that a same-net pad or track touches on their layer:
    the only places load current can enter or leave a pour."""
    out = set()
    for l, polys in pieces.items():
        cop = [p.poly for p in bg.pads_of(net, l)] + \
            [t.poly for t in bg.tracks_of(net, l)]
        out |= {(l, i) for i, q in enumerate(polys)
                if any(q.intersects(c) for c in cop)}
    return out


def stitch_bundles(links: list, judged: set, anchored: set) -> list[list]:
    """Groups of barrels (vias, then plated pads; `links` holds (via or
    pad, touched pieces) for each) that join the same two fill pieces on
    different layers - the path a pour-to-pour current takes. Returned as
    lists of those vias and pads, once per distinct group (one through-via set joining 3+
    layers is one group), and only when a member is in `judged` (stitch
    vias left out of the cluster rule). A pair is skipped when one of its
    pieces is a dead end: no pad or track touches it and every barrel on it
    is in the group, so no load current can cross into it and back out."""
    pairs: dict[frozenset, list[int]] = {}
    on_piece: dict[tuple, set[int]] = {}
    for i, (_, touched) in enumerate(links):
        for a in touched:
            on_piece.setdefault(a, set()).add(i)
            for b in touched:
                if a < b and a[0] != b[0]:
                    pairs.setdefault(frozenset((a, b)), []).append(i)
    seen, out = set(), []
    for pair, members in pairs.items():
        key = frozenset(members)
        if key in seen or not key & judged:
            continue
        if any(pc not in anchored and on_piece[pc] <= key for pc in pair):
            continue
        seen.add(key)
        out.append([links[i][0] for i in members])
    return out


def _neck_graph(bg: geom.BoardGeom, net: str, layer: str, fill,
                others: dict | None):
    """(attachment vias, components) for one fill; components(radius)
    returns (nodes, root) where nodes[i] = (layer, reach polygon), the
    fill's own pieces first, and root(i) its joined component."""
    vias = [v for v in bg.vias_of(net, layer)
            if fill.buffer(0.01).contains(Point(v.at))]
    others = {l: o for l, o in (others or {}).items()
              if l != layer and not o[0].is_empty}
    fills = {layer: fill, **{l: o[0] for l, o in others.items()}}
    stitches = [s for s in _stitches(bg, net, fills)
                if layer in s and any(l in s for l in others)]
    # other layers' pieces are fixed at their own requirement: erode once
    other_nodes = []                     # (layer, reach polygon)
    for l, (ofill, oreq) in others.items():
        r = oreq / 2.0
        other_nodes += [(l, part.buffer(r + 0.01)) for part in _pieces(ofill, r)]

    def components(radius: float):
        own = [part.buffer(radius + 0.01) for part in _pieces(fill, radius)]
        nodes = [(layer, g) for g in own] + other_nodes
        parent = list(range(len(nodes)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        if own and stitches and other_nodes:
            tree = STRtree([g for _, g in nodes])
            for joins in stitches:
                hit = [int(i) for l, g in joins.items()
                       for i in tree.query(g, predicate="intersects")
                       if nodes[int(i)][0] == l]
                if len({nodes[i][0] for i in hit}) < 2:
                    continue             # same-layer pieces only: no path
                for i in hit[1:]:
                    parent[find(i)] = find(hit[0])
        return nodes, find, len(own)
    return vias, components


def pour_neck(bg: geom.BoardGeom, net: str, layer: str, fill,
              required: float, others: dict | None = None):
    """None if the pour carries `required` width between all via attachments,
    else (neck_width_mm, pos) of the tightest failing neck.

    `others` maps every other copper layer with a fill of this net to
    (fill, required_mm on that layer); pieces join across layers through
    same-net vias and plated through-holes (module docstring)."""
    vias, components = _neck_graph(bg, net, layer, fill, others)
    pts = [Point(v.at) for v in vias]
    if len(pts) < 2:
        return None

    def connected(radius: float) -> bool:
        nodes, find, n_own = components(radius)
        if not n_own:
            return False
        common = None
        for p in pts:
            comps = {find(i) for i in range(n_own) if nodes[i][1].contains(p)}
            common = comps if common is None else common & comps
            if not common:
                return False
        return True
    if connected(required / 2.0):
        return None
    lo, hi = 0.0, required / 2.0    # lo connected, hi not
    for _ in range(12):
        mid = (lo + hi) / 2.0
        if connected(mid):
            lo = mid
        else:
            hi = mid
    # locate approximately: centroid gap between split components
    eroded = fill.buffer(-hi)
    parts = list(getattr(eroded, "geoms", [eroded])) if not eroded.is_empty else []
    pos = parts[0].representative_point().coords[0] if parts else \
        fill.representative_point().coords[0]
    return 2.0 * lo, pos


# ---- leaf branches (module docstring) -------------------------------------

# Highest common rating per imperial chip-resistor size (W). Generous on
# purpose: the bound sqrt(P/R) grows with P, so it errs high.
RES_RATING_W = {"0201": 0.1, "0402": 0.2, "0603": 0.25, "0805": 0.5,
                "1206": 0.5, "1210": 0.75, "1812": 1.0, "2010": 1.0,
                "2512": 3.0}
_RES_SIZE = re.compile(r"(?<!\d)(" + "|".join(RES_RATING_W) + r")(?!\d)")
_OHM_MULT = {"": 1.0, "R": 1.0, "r": 1.0, "k": 1e3, "K": 1e3, "M": 1e6,
             "m": 1e-3}


def parse_ohms(value) -> float | None:
    """Ohms from a Value field's first token (470R, 4k7, 10k, 2.2K, 1M,
    100, 10kOhm), else None. "m" is milli: reading a megohm as milliohms
    only raises the bound."""
    toks = str(value or "").split()
    if not toks:
        return None
    tok = toks[0].replace("Ω", "R").replace("Ω", "R")
    tok = re.sub(r"(?i)ohms?$", "R", tok)
    m = re.fullmatch(r"(\d+)([RrKkMm])(\d+)", tok)
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * _OHM_MULT[m.group(2)]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KkMm]?)[Rr]?", tok)
    if m:
        return float(m.group(1)) * _OHM_MULT[m.group(2)]
    return None


def load_bound(bg: geom.BoardGeom, ref: str) -> float | None:
    """Most current part `ref` can pass (A), or None when unknown. Only a
    two-pad chip resistor is bounded: sqrt(P_rated / R) from its Value and
    the imperial size in its footprint id. Every other part may draw the
    net's whole budget."""
    if not re.fullmatch(r"R\d+", ref) or len(bg.pads_of(ref=ref)) != 2:
        return None
    fp = bg.footprints.get(ref) or {}
    ohms = parse_ohms(fp.get("value"))
    size = _RES_SIZE.search(fp.get("lib") or "")
    if not ohms or ohms <= 0 or size is None:
        return None
    return math.sqrt(RES_RATING_W[size.group(1)] / ohms)


def _loads(bg: geom.BoardGeom, refs) -> float | None:
    """Summed load_bound of `refs`, None if any is unbounded or there are
    none: copper that reaches no pad is not known to be a leaf."""
    if not refs:
        return None
    total = 0.0
    for r in sorted(set(refs)):
        b = load_bound(bg, r)
        if b is None:
            return None
        total += b
    return total


def _copper_graph(bg: geom.BoardGeom, net: str, walls: dict | None = None,
                  skip: frozenset = frozenset()):
    """The net's raw copper as parts per layer (minus `walls[layer]`),
    joined through every via and plated pad whose id() is not in `skip`.
    Returns (nodes [(layer, part)], root(i), hits(item) -> indices of the
    nodes its copper touches on its own layers)."""
    nodes = []
    for l in bg.copper_layers:
        cop = bg.net_copper(net, l)
        if walls and l in walls and not cop.is_empty:
            cop = cop.difference(walls[l])
        if cop.is_empty:
            continue
        nodes += [(l, g) for g in getattr(cop, "geoms", [cop])
                  if g.geom_type == "Polygon" and not g.is_empty]
    parent = list(range(len(nodes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    tree = STRtree([g for _, g in nodes]) if nodes else None

    def hits(item) -> list[int]:
        if tree is None:
            return []
        return [int(i) for i in tree.query(item.poly, predicate="intersects")
                if nodes[int(i)][0] in item.layers]
    links = [v for v in bg.vias_of(net) if id(v) not in skip]
    links += [p for p in bg.pads_of(net) if id(p) not in skip
              and p.drill is not None and len(p.layers) > 1]
    for it in links:
        hole = Point(getattr(it, "at", None) or it.center)
        if walls and any(l in walls and walls[l].contains(hole)
                         for l in it.layers):
            continue                     # its barrel belongs to a wall
        h = hits(it)
        for i in h[1:]:
            parent[find(i)] = find(h[0])
    return nodes, find, hits


def neck_leaf_bound(bg: geom.BoardGeom, net: str, layer: str, fill,
                    required: float, others: dict | None):
    """(amps, refs) a pour neck failing at `required` can carry at most,
    when every side of the split but one reaches only bounded loads;
    else None (module docstring, leaf branches)."""
    vias, components = _neck_graph(bg, net, layer, fill, others)
    nodes, find, n_own = components(required / 2.0)
    roots = [{find(i) for i in range(n_own)
              if nodes[i][1].contains(Point(v.at))} for v in vias]
    gp = list(range(len(vias)))

    def gfind(i):
        while gp[i] != i:
            gp[i] = gp[gp[i]]
            i = gp[i]
        return i
    for i in range(len(vias)):
        for j in range(i + 1, len(vias)):
            if roots[i] & roots[j]:
                gp[gfind(i)] = gfind(j)
    groups: dict[int, dict] = {}
    for i, v in enumerate(vias):
        g = groups.setdefault(gfind(i), {"vias": [], "roots": set()})
        g["vias"].append(v)
        g["roots"] |= roots[i]
    if len(groups) < 2:
        return None
    walls = {}                           # group -> {layer: its territory}
    for k, g in groups.items():
        by_layer: dict[str, list] = {}
        for i, (l, poly) in enumerate(nodes):
            if find(i) in g["roots"]:
                by_layer.setdefault(l, []).append(poly)
        for v in g["vias"]:              # a side eroded away keeps its vias
            for l in v.layers:
                by_layer.setdefault(l, []).append(v.poly)
        walls[k] = {l: unary_union(ps) for l, ps in by_layer.items()}
    links = bg.vias_of(net) + [p for p in bg.pads_of(net)
                               if p.drill is not None and len(p.layers) > 1]
    mains, leaf_refs = 0, set()
    for k, g in groups.items():
        rest = [walls[h] for h in groups if h != k]
        wall = {l: unary_union([w[l] for w in rest if l in w])
                for l in {l for w in rest for l in w}}
        cn, cfind, chits = _copper_graph(bg, net, wall)
        start = {cfind(i) for v in g["vias"] for i in chits(v)}
        reached = {i for i in range(len(cn)) if cfind(i) in start}
        refs = {p.ref for p in bg.pads_of(net) if set(chits(p)) & reached}
        if _loads(bg, refs) is None:
            mains += 1
            continue
        # where this side meets the others: same-layer edges and stitches
        touched, contacts = set(), []
        for h in groups:
            if h == k:
                continue
            for i in reached:
                l, part = cn[i]
                w = walls[h].get(l)
                if w is not None and part.buffer(TOUCH_MM).intersects(w):
                    touched.add(h)
                    contacts.append(part.buffer(TOUCH_MM).intersection(w))
            for it in links:
                if set(chits(it)) & reached and any(
                        l in walls[h] and it.poly.intersects(walls[h][l])
                        for l in it.layers):
                    touched.add(h)
                    contacts.append(it.poly)
        joints = unary_union(contacts).buffer(required / 2.0) \
            if contacts else None
        if len(touched) > 1 or (joints is not None and
                                len(getattr(joints, "geoms", [joints])) > 1):
            return None                  # may bypass the neck: no bound
        leaf_refs |= refs
    if mains > 1:
        return None
    return _loads(bg, leaf_refs), sorted(leaf_refs)


def cluster_leaf_bound(bg: geom.BoardGeom, net: str, group: list):
    """(amps, refs) a via cluster or stitch bundle (vias and plated pads)
    can carry at most, when cutting its barrels leaves one side with
    unbounded loads and every other side reaching it through exactly one
    of them; else None (module docstring, leaf branches)."""
    nodes, find, hits = _copper_graph(bg, net,
                                      skip=frozenset(id(b) for b in group))
    ends = [{find(i) for i in hits(v)} for v in group]
    touched = set().union(*ends)
    refs_of = {c: set() for c in touched}
    for p in bg.pads_of(net):
        for c in {find(i) for i in hits(p)} & touched:
            refs_of[c].add(p.ref)
    # a side reaching no pad is passive: it only joins the cluster's vias
    main = [c for c in touched
            if refs_of[c] and _loads(bg, refs_of[c]) is None]
    if len(main) > 1:
        return None
    rest = touched - set(main)
    if main:
        m = main[0]
        if any(e <= {m} for e in ends):
            return None                  # a via inside the main side
        parent = {c: c for c in rest}

        def cfind(c):
            while parent[c] != c:
                c = parent[c]
            return c
        for e in ends:
            side = sorted(e & rest)
            for c in side[1:]:
                parent[cfind(c)] = cfind(side[0])
        into_main: dict = {}
        for e in ends:
            if m in e:
                for side in {cfind(c) for c in e & rest}:
                    into_main[side] = into_main.get(side, 0) + 1
        if any(n > 1 for n in into_main.values()):
            return None                  # a side bridged twice: may bypass
    refs = set().union(*(refs_of[c] for c in rest))
    if not refs:
        return None                      # no known load past the cluster
    return _loads(bg, refs), sorted(refs)


def check_net(bg: geom.BoardGeom, entry: dict):
    net = entry.get("net")
    if not net:
        raise CheckError('power entry without "net"')
    if net not in bg.nets:
        raise CheckError(f"power net {net!r} not on board "
                         f"(nets: {sorted(n for n in bg.nets if n)})")
    if "current_a" not in entry:
        raise CheckError(f'power entry {net!r} without "current_a"')
    budget = float(entry["current_a"])
    dt_c = float(entry.get("dt_c", 10.0))
    via_amps = float(entry.get("via_amps", VIA_AMPS_DEFAULT))
    derived = bool(entry.get("derived", False))
    cu = bg.stackup.copper_thickness
    violations: list[dict] = []

    # ---- plane-fed rail: the declared trunk must exist
    plane_fed = bool(entry.get("plane_fed", False))
    if plane_fed and not bg.layers_with_zone(net):
        pos = None
        for layer in bg.copper_layers:
            cop = bg.net_copper(net, layer)
            if not cop.is_empty:
                pos = cop.representative_point().coords[0]
                break
        violations.append(violation(
            SCRIPT, "error", pos, None, net, [],
            f"{net} is declared plane_fed but has no zone fill on any layer",
            SCRIPT, kind="plane_missing"))
        plane_fed = False       # rest of the check runs at full semantics

    # ---- track segments
    min_seen: dict[str, float] = {}
    undersized: list[tuple[dict, object]] = []   # (violation, Track)
    pour_taps: list = []                         # dropped: pour-shunted taps
    thin = []                                    # (Track, mid, ov, amps, req)
    for t in bg.tracks_of(net):
        mid = t.shape.interpolate(0.5, normalized=True).coords[0]
        ov = region_current(entry, mid)
        amps = budget if ov is None else ov
        req = required_width_mm(amps, dt_c, cu[t.layer])
        min_seen[t.layer] = min(min_seen.get(t.layer, 9e9), t.width)
        if t.width + WIDTH_TOL_MM < req:
            thin.append((t, mid, ov, amps, req))
    exits = pad_exit_necks(bg, net, [(x[0], x[4]) for x in thin]) \
        if thin else {}
    for t, mid, ov, amps, req in thin:
        if id(t) in exits:
            continue
        advisory = plane_fed and ov is None
        msg = (f"{net} track {t.width:.3f} mm wide on {t.layer}; IPC-2152 "
               f"needs {req:.3f} mm for {amps:.2f} A at dT={dt_c:.0f}C")
        extras = {}
        if advisory:
            msg += ("; advisory: plane-fed rail, full-budget worst-case "
                    "screen (per-segment current unattributed)")
            extras["advisory"] = True
        x0, y0 = t.shape.coords[0]
        x1, y1 = t.shape.coords[-1]
        v = violation(
            SCRIPT, "warning" if advisory else "error", mid, t.layer,
            net, [], msg, SCRIPT, kind="undersized_track",
            width_mm=checklib.rnd(t.width), required_mm=checklib.rnd(req),
            current_a=amps, segment={"start": [checklib.rnd(x0),
                                               checklib.rnd(y0)],
                                     "end": [checklib.rnd(x1),
                                             checklib.rnd(y1)]},
            **extras)
        violations.append(v)
        undersized.append((v, t))

    # ---- bridge labeling (LEARNINGS 2026-07-29: cut edge = sole path)
    if undersized:
        g = netconn.build(bg, net, include_zones=True)
        bridges = netconn.bridge_tracks(g)
        # g.tracks maps edge_id -> the SAME Track objects bg.tracks_of yields
        # (geom filters one cached list; netconn stores them unchanged), so
        # identity lookup is sound. Zero-length tracks are absent from the
        # graph: label those bridge=true (sole-path is the conservative call).
        edge_of = {id(trk): eid for eid, trk in g.tracks.items()}
        for v, t in undersized:
            eid = edge_of.get(id(t))
            v["bridge"] = True if eid is None else eid in bridges
        # non-bridge taps inside their own pour carry no load current
        shunted = [(v, t) for v, t in undersized
                   if not v["bridge"] and (pour_shunted(bg, net, t)
                                           or inside_pad_copper(bg, net, t))]
        if shunted:
            drop = {id(v) for v, _ in shunted}
            violations = [v for v in violations if id(v) not in drop]
            undersized = [(v, t) for v, t in undersized if id(v) not in drop]
            pour_taps = [v["pos"] for v, _ in shunted]

    # ---- pour neckdowns (always at the full budget; plane_fed keeps error)
    leaf_branches: list[dict] = []           # dropped: bounded leaf loads
    zone_layers = bg.layers_with_zone(net)

    def others_at(amps, layer):
        # the net's fills on every other layer, at their own requirement
        return {l: (bg.zone_fill(net, l), required_width_mm(amps, dt_c, cu[l]))
                for l in zone_layers if l != layer}

    for z in bg.zones_of(net):
        for layer in z.fills:
            fill = z.fill_on(layer)
            if fill.is_empty:
                continue
            amps = budget
            req = required_width_mm(budget, dt_c, cu[layer])
            neck = pour_neck(bg, net, layer, fill, req,
                             others_at(budget, layer))
            if neck is not None:
                ov = region_current(entry, neck[1])
                if ov is not None:
                    # failing neck sits in an override region: re-test the
                    # fill at the override requirement; passing drops it
                    amps = ov
                    req = required_width_mm(ov, dt_c, cu[layer])
                    neck = pour_neck(bg, net, layer, fill, req,
                                     others_at(ov, layer))
            leaf = neck_leaf_bound(bg, net, layer, fill, req,
                                   others_at(amps, layer)) \
                if neck is not None else None
            if leaf is not None and leaf[0] < amps:
                # far side feeds only bounded loads: judge it at their sum
                at = neck[1]
                amps, refs = leaf
                req = required_width_mm(amps, dt_c, cu[layer])
                neck = pour_neck(bg, net, layer, fill, req,
                                 others_at(amps, layer)) if amps > 0 else None
                if neck is None:
                    leaf_branches.append({
                        "kind": "pour_neckdown", "layer": layer,
                        "pos": [checklib.rnd(at[0]), checklib.rnd(at[1])],
                        "current_a": checklib.rnd(amps), "loads": refs})
            else:
                leaf = None
            if neck is not None:
                width, pos = neck
                msg = (f"{net} pour on {layer} necks to ~{width:.2f} mm "
                       f"between via attachments; IPC-2152 needs {req:.3f} mm "
                       f"for {amps:.2f} A at dT={dt_c:.0f}C")
                extras = {}
                if leaf is not None:
                    msg += (f"; leaf branch: {', '.join(leaf[1])} draw at "
                            f"most {amps:.3f} A")
                    extras["leaf_loads"] = leaf[1]
                if derived:
                    # derived return entry: the budget itself is a heuristic
                    # (max declared rail), so the neck is a labeled screen,
                    # not an error - see module docstring (T6).
                    msg += ("; advisory: derived return-net coverage "
                            "(no declared entry; budget = max declared rail)")
                    extras["advisory"] = True
                violations.append(violation(
                    SCRIPT, "warning" if derived else "error", pos, layer,
                    net, [], msg, SCRIPT,
                    kind="pour_neckdown", neck_mm=checklib.rnd(width),
                    required_mm=checklib.rnd(req), current_a=amps, **extras))

    # ---- transition via count (per-cluster override via centroid);
    # all-stitch clusters are judged per pour pair instead (docstring)
    vias = bg.vias_of(net)
    pieces = fill_pieces(bg, net) if vias else {}
    links = via_pieces(bg, net, pieces)
    stitch_ids = {id(v) for v, (s, _) in zip(vias, links) if s}
    clusters = cluster_vias(vias)
    judged = []                          # (vias / plated pads, stitch bundle?)
    pooled: set[int] = set()             # via indices left to the pair rule
    index = {id(v): i for i, v in enumerate(vias)}
    for group in clusters:
        if all(id(v) in stitch_ids for v in group):
            pooled |= {index[id(v)] for v in group}
        else:
            judged.append((group, False))
    if pooled:
        barrels = [(v, t) for v, (_, t) in zip(vias, links)] + \
            pth_pieces(bg, net, pieces)
        judged += [(g, True) for g in stitch_bundles(
            barrels, pooled, anchored_pieces(bg, net, pieces))]
    for group, bundle in judged:
        at = [getattr(b, "at", None) or b.center for b in group]
        cx = sum(x for x, _ in at) / len(at)
        cy = sum(y for _, y in at) / len(at)
        ov = region_current(entry, (cx, cy))
        amps = budget if ov is None else ov
        need = max(1, math.ceil(amps / via_amps))
        leaf = cluster_leaf_bound(bg, net, group) \
            if len(group) < need else None
        if leaf is not None and leaf[0] < amps:
            # every side but one feeds only bounded loads: their sum
            amps = leaf[0]
            need = max(1, math.ceil(amps / via_amps))
            if len(group) >= need:
                leaf_branches.append({
                    "kind": "insufficient_transition_vias",
                    "pos": [checklib.rnd(cx), checklib.rnd(cy)],
                    "current_a": checklib.rnd(amps), "loads": leaf[1]})
        else:
            leaf = None
        if len(group) < need:
            advisory = plane_fed and ov is None
            if bundle:
                msg = (f"{net} pour-to-pour transition near ({cx:.2f}, "
                       f"{cy:.2f}) has {len(group)} via(s) or plated "
                       f"hole(s) joining the same two pours; {amps:.2f} A "
                       f"needs {need} "
                       f"(>= 1 via per {via_amps} A)")
            else:
                msg = (f"{net} layer transition at ({cx:.2f}, {cy:.2f}) has "
                       f"{len(group)} via(s); {amps:.2f} A needs {need} "
                       f"(>= 1 via per {via_amps} A)")
            extras = {"stitch": True} if bundle else {}
            if leaf is not None:
                msg += (f"; leaf branch: {', '.join(leaf[1])} draw at most "
                        f"{amps:.3f} A")
                extras["leaf_loads"] = leaf[1]
            if advisory:
                msg += ("; advisory: plane-fed rail, per-cluster current "
                        "unattributed (each via is a leaf tap off the plane)")
                extras["advisory"] = True
            violations.append(violation(
                SCRIPT, "warning" if advisory else "error", (cx, cy), None,
                net, [], msg, SCRIPT, kind="insufficient_transition_vias",
                vias=len(group), required=need, **extras))

    facts = {"net": net, "current_a": budget, "dt_c": dt_c,
             "required_mm_by_layer": {
                 l: checklib.rnd(required_width_mm(budget, dt_c, cu[l]))
                 for l in bg.copper_layers},
             "min_track_mm_by_layer": {l: checklib.rnd(w)
                                       for l, w in min_seen.items()},
             "via_clusters": len(clusters)}
    if stitch_ids:
        facts["stitch_vias"] = len(stitch_ids)
        facts["stitch_bundles"] = sum(1 for _, b in judged if b)
    if undersized or pour_taps:
        facts["bridge_labeled"] = True
    if pour_taps:
        facts["pour_taps"] = pour_taps
    if leaf_branches:
        facts["leaf_branches"] = leaf_branches
    if exits:
        seen = []
        for info in exits.values():
            if info not in seen:
                seen.append(info)
        facts["pad_exit_necks"] = seen
    if entry.get("plane_fed"):
        facts["plane_fed"] = True
        facts["advisory_violations"] = sum(
            1 for v in violations if v.get("advisory"))
    if derived:
        facts["derived"] = True
        for v in violations:
            v["derived"] = True
    elif (not entry.get("plane_fed") and clusters
            and bg.layers_with_zone(net)):
        # sidecar-adoption lint (T6, facts only): a rail with a zone fill
        # whose via clusters are almost all single-via is the plane-fed
        # shape - the entry probably wants "plane_fed": true (the carrier
        # +3V3 gap: T2 built the key, no sidecar ever adopted it).
        single = sum(1 for g in clusters if len(g) == 1)
        if single >= PLANE_HINT_SINGLE_VIA_FRAC * len(clusters):
            facts["plane_fed_candidate"] = True
    return violations, facts


def derived_return_entry(entries: list[dict], bg: geom.BoardGeom) -> dict | None:
    """Synthesize the return-net entry (module docstring, T6), or None.

    Only when: a declared rail reaches RETURN_SYNTH_MIN_A, the return net
    (per-entry "return_net", default "GND") is on the board with a zone fill,
    and it is not already a declared power entry."""
    real = [e for e in entries if e.get("net") and "current_a" in e]
    if not real:
        return None
    mx = max(real, key=lambda e: float(e["current_a"]))
    if float(mx["current_a"]) < RETURN_SYNTH_MIN_A:
        return None
    ret = mx.get("return_net") or next(
        (e["return_net"] for e in real if e.get("return_net")), "GND")
    if ret in {e["net"] for e in real}:
        return None                      # already declared - owner's numbers win
    if ret not in bg.nets or not bg.layers_with_zone(ret):
        return None                      # routed-only return: not judgeable here
    return {"net": ret, "current_a": float(mx["current_a"]),
            "dt_c": float(mx.get("dt_c", 10.0)),
            "via_amps": float(mx.get("via_amps", VIA_AMPS_DEFAULT)),
            "plane_fed": True, "derived": True}


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Trace width / pour neck / via count vs current (IPC-2152).")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--constraints", required=True,
                    help="constraints.json with a power list")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    cons = checklib.load_json(args.constraints, "constraints")
    entries = cons.get("power", [])
    bg = geom.load_board(Path(args.pcb))
    bg.assert_fresh()

    violations: list[dict] = []
    checked: list[dict] = []
    for entry in entries:
        vs, facts = check_net(bg, entry)
        violations.extend(vs)
        checked.append(facts)
    ret_entry = derived_return_entry(entries, bg)
    if ret_entry is not None:
        vs, facts = check_net(bg, ret_entry)
        violations.extend(vs)
        checked.append(facts)

    payload = checklib.report(SCRIPT, args.pcb, violations, checked=checked,
                              stackup_assumed=bg.stackup.assumed)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
