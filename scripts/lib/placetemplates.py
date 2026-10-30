"""placetemplates - stage-3 placement template library (T6 p4).

First entry: the CRYSTAL ISLAND.  placement.groups[] entries may carry an
optional one-word "template" tag (P2 emits it; absent -> generic satellite
slotting, fully backward compatible):

    {"name": "xtal", "anchor": "Y1", "members": ["C7", "C8", "R35"],
     "template": "crystal"}

layout(model, cluster, warnings) returns a slots dict in the
place_seed.layout_satellites shape (ref -> ((x, y) anchor-local, rel_deg))
or None to fall back to generic slotting.  The annealer inherits the island
for free as a rigid Body - no annealer changes.

Roles derive DETERMINISTICALLY from the netlist scoped to the cluster (no
LLM classification):
  osc nets = nets on the crystal's own pads, gnd-ish names excluded
  load cap = 2-pad member with one pad on an osc net and one on a gnd-ish net
  resistor = member on an osc net without a gnd-ish pad (series/feedback)
Geometry: load caps OUTBOARD ALONG THE PAD AXIS, one per crystal pad,
mirrored about the pad-pair midpoint (symmetric loading), shared-net pad
facing its crystal pad so the GND pad lands outboard; resistors flank the
island on the perpendicular axis.  Any inference failure warns and falls
back - the template can never be worse than generic slotting.

audit_groups(model, placement, warnings): WARN-ONLY membership audit -
any movable 2-pad part on an oscillator net that is not a group member is
named.  This single check would have caught the carrier's eth_xtal R35/R36
omission (R36 annealed 28 mm from its driver pin; 115.6 mm of oscillator
copper, -71% after the scoped P6/P7 backward edge).
"""
from __future__ import annotations

import math

from shapely import affinity

import placelib
from geom import _rot

SAT_GAP = 0.3          # mm clearance satellite <-> crystal courtyard
EPS = placelib.EPS_AREA


def _is_gndish(net: str | None) -> bool:
    if not net:
        return False
    u = net.upper().lstrip("/")
    return "GND" in u or u == "VSS"


def _unit(v):
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n) if n > 1e-9 else (1.0, 0.0)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _centered(poly):
    c = poly.centroid
    return affinity.translate(poly, -c.x, -c.y)


def _circumradius(poly) -> float:
    minx, miny, maxx, maxy = poly.bounds
    return math.hypot(maxx - minx, maxy - miny) / 2.0


def _facing(fp, net: str | None, toward) -> float:
    """Rel rotation pointing fp's `net` pad along `toward` (2-pad parts)."""
    if net is None or len(fp.pads) != 2:
        return 0.0
    facing = [p for p in fp.pads if p.net == net]
    if not facing:
        return 0.0
    cc = fp.center_local()
    off = (facing[0].local[0] - cc[0], facing[0].local[1] - cc[1])
    if math.hypot(*off) < 1e-6:
        return 0.0
    offu = _unit(off)
    return max((0.0, 90.0, 180.0, 270.0),
               key=lambda r: _dot(_rot(offu[0], offu[1], -r), toward))


def _push_out(poly0, pivot, direction, r_sat, placed):
    """March outward from pivot along direction until collision-free."""
    for push in range(14):
        cand = (pivot[0] + direction[0] * (r_sat + SAT_GAP + push * 0.4),
                pivot[1] + direction[1] * (r_sat + SAT_GAP + push * 0.4))
        poly = affinity.translate(poly0, cand[0], cand[1])
        if all(poly.intersection(p).area <= EPS for p in placed):
            return cand, poly
    return None


# ---------------------------------------------------------------- crystal

def _crystal(model, cluster, warnings):
    anchor = model.footprints[cluster.anchor]
    osc_pads = [p for p in anchor.pads if p.net and not _is_gndish(p.net)]
    reps: dict[str, object] = {}
    for p in sorted(osc_pads, key=lambda p: p.number):
        reps.setdefault(p.net, p)
    if len(reps) != 2:
        warnings.append(
            f"template crystal: {cluster.anchor} exposes "
            f"{sorted(reps)} oscillator nets (need exactly 2) - "
            "generic slotting used")
        return None
    net_a, net_b = sorted(reps)
    pa, pb = reps[net_a], reps[net_b]
    axis = _unit((pb.local[0] - pa.local[0], pb.local[1] - pa.local[1]))
    if math.dist(pa.local, pb.local) < 1e-6:
        warnings.append(f"template crystal: {cluster.anchor} pad axis is "
                        "degenerate - generic slotting used")
        return None
    mid = ((pa.local[0] + pb.local[0]) / 2, (pa.local[1] + pb.local[1]) / 2)
    perp = (-axis[1], axis[0])

    caps, resistors, others = [], [], []
    for s in sorted(cluster.satellites, key=lambda s: s.ref):
        fp = model.footprints[s.ref]
        nets = [p.net for p in fp.pads if p.net]
        on_osc = sorted({n for n in nets if n in (net_a, net_b)})
        if len(fp.pads) == 2 and on_osc and any(_is_gndish(n) for n in nets):
            caps.append((s, on_osc[0]))
        elif on_osc:
            resistors.append((s, on_osc[0]))
        else:
            others.append(s)
    if not caps:
        warnings.append(f"template crystal: no load caps identified in group "
                        f"of {cluster.anchor} - generic slotting used")
        return None

    ext = anchor.extents_local()
    placed = [ext]
    slots: dict[str, tuple] = {}

    # --- load caps: outboard along the axis, symmetric about the midpoint,
    #     shared-net pad facing its crystal pad (GND pad lands outboard)
    cap_geo = []       # (ref, net, dir, poly0, r_sat)
    for s, net in caps:
        fp = model.footprints[s.ref]
        pad = reps.get(net, pa)
        outward = _unit((pad.local[0] - mid[0], pad.local[1] - mid[1]))
        rel = _facing(fp, net, (-outward[0], -outward[1]))
        poly0 = affinity.rotate(_centered(fp.extents_local()), -rel,
                                origin=(0, 0))
        cap_geo.append((s.ref, net, outward, poly0,
                        _circumradius(poly0), rel, pad))
    dists = []
    for ref, net, outward, poly0, r_sat, rel, pad in cap_geo:
        hit = _push_out(poly0, pad.local, outward, r_sat, placed)
        if hit is None:
            cand = (pad.local[0] + outward[0] * (r_sat + 5.0),
                    pad.local[1] + outward[1] * (r_sat + 5.0))
            hit = (cand, affinity.translate(poly0, *cand))
            warnings.append(f"template crystal: {ref} could not be slotted "
                            f"collision-free beside {cluster.anchor}")
        dists.append(math.dist(hit[0], mid))
    if len(cap_geo) == 2 and cap_geo[0][1] != cap_geo[1][1]:
        dists = [max(dists)] * 2      # mirror: symmetric loading
    for (ref, net, outward, poly0, r_sat, rel, pad), d in zip(cap_geo, dists):
        cand = (mid[0] + outward[0] * d, mid[1] + outward[1] * d)
        slots[ref] = (cand, rel)
        placed.append(affinity.translate(poly0, *cand))

    # --- resistors: flank on the perpendicular axis, alternating sides
    for i, (s, net) in enumerate(resistors):
        fp = model.footprints[s.ref]
        sign = 1.0 if i % 2 == 0 else -1.0
        direction = (perp[0] * sign, perp[1] * sign)
        pad = reps.get(net, pa)
        rel0 = _centered(fp.extents_local())
        r_sat = _circumradius(rel0)
        hit = _push_out(rel0, mid, direction, r_sat, placed)
        if hit is None:
            cand = (mid[0] + direction[0] * (r_sat + 5.0),
                    mid[1] + direction[1] * (r_sat + 5.0))
            hit = (cand, affinity.translate(rel0, *cand))
            warnings.append(f"template crystal: {s.ref} could not be slotted "
                            f"collision-free beside {cluster.anchor}")
        toward = _unit((pad.local[0] - hit[0][0], pad.local[1] - hit[0][1]))
        rel = _facing(fp, net, toward)
        poly = affinity.translate(
            affinity.rotate(rel0, -rel, origin=(0, 0)), *hit[0])
        slots[s.ref] = (hit[0], rel)
        placed.append(poly)

    # --- anything else: generic perimeter spread (defensive fallback)
    rc = _circumradius(ext)
    ac = anchor.center_local()
    for i, s in enumerate(others):
        fp = model.footprints[s.ref]
        spread = i * (360.0 / max(1, len(others)))
        direction = _unit(_rot(1.0, 0.0, spread))
        pivot = (ac[0] + direction[0] * rc, ac[1] + direction[1] * rc)
        rel0 = _centered(fp.extents_local())
        r_sat = _circumradius(rel0)
        hit = _push_out(rel0, pivot, direction, r_sat, placed)
        if hit is None:
            cand = (pivot[0] + direction[0] * (r_sat + 5.0),
                    pivot[1] + direction[1] * (r_sat + 5.0))
            hit = (cand, affinity.translate(rel0, *cand))
            warnings.append(f"template crystal: {s.ref} could not be slotted "
                            f"collision-free around {cluster.anchor}")
        slots[s.ref] = (hit[0], 0.0)
        placed.append(hit[1])
    return slots


TEMPLATES = {"crystal": _crystal}


def layout(model, cluster, warnings):
    """Template dispatch for place_seed.layout_satellites. None = fallback."""
    fn = TEMPLATES.get(cluster.template)
    if fn is None:
        warnings.append(f"unknown placement template '{cluster.template}' on "
                        f"{cluster.anchor} - generic slotting used")
        return None
    return fn(model, cluster, warnings)


def audit_groups(model, placement, warnings) -> None:
    """Warn-only membership audit for template groups (T6 p4)."""
    for g in (placement or {}).get("groups", []):
        if g.get("template") != "crystal":
            continue
        anchor = model.footprints.get(g.get("anchor"))
        if anchor is None:
            continue
        osc_nets = {p.net for p in anchor.pads
                    if p.net and not _is_gndish(p.net)}
        if not osc_nets:
            continue
        members = set(g.get("members") or []) | {g.get("anchor")}
        name = g.get("name") or g.get("anchor")
        for ref in sorted(model.footprints):
            fp = model.footprints[ref]
            if ref in members or not fp.is_movable or len(fp.pads) != 2:
                continue
            hit = sorted({p.net for p in fp.pads if p.net in osc_nets})
            if hit:
                warnings.append(
                    f"template crystal group '{name}': {ref} sits on "
                    f"oscillator net {hit[0]} but is not a member - the "
                    "annealer will treat it as unrelated (carrier R35/R36 "
                    "precedent: 28 mm scatter, -71% copper after the fix)")
