"""route_quality.py - how good the routing is, per net class, not only whether it is legal.

One concern: copper that passes DRC and every constraint can still be a bad
layout - a DDR byte lane matched to picoseconds as one long zig-zag, a signal
that wanders across the board and back, a net that changes layer five times.
DRC, check_route_style and check_diffpair see none of that. This scores each
routed net against the shortest route it could have had, and rolls the nets
up per net class, so a routing round can be judged better or worse by a number
and route_improve.py can stop re-routing when the number stops rising.

Per net (geom.load_board, every copper layer):
  ideal_mm   the octilinear (0/45/90 degree) minimum spanning tree over the
             net's pad copper centres - the shortest route a 45-degree router
             could lay, within a Steiner factor. For a length_match group or
             a diff pair the ideal is the group's LONGEST member's tree: a
             matched net may be as long as its longest sibling needs to be,
             and no longer, so meander added to reach the target is free and
             meander past it (the long zig-zag) is charged.
  length_mm  the net's routed track length (arcs by their centreline).
  vias       its vias; one is free when no copper layer holds all its pads.
  bends      bare joints (two of the net's segments meet on one layer)
             where the heading turns; one per connection (pads - 1) is free.
  cost_mm    length + VIA_MM * excess vias + BEND_MM * excess bends.
  quality    min(1, ideal / cost): 1.0 is the shortest straight route.

A net is not judged (quality None, reason given) when it has fewer than two
pads, no tracks, a pour (its zones carry it: the tracks are taps), or tracks
shorter than PARTIAL x ideal (unrouted, or carried by copper this can't see).

Classes, first match wins: plane (has a zone, or a constraints `planes` net),
diff_pair (constraints diff_pairs, or a pair found by name with
check_diffpair.discover_pairs), matched (constraints length_match), rf
(constraints rf, or a high_speed net with impedance_ohm), high_speed,
power (constraints power, or a Pwr_* netclass in the .kicad_pro), signal.

A class's score is sum(ideal) / sum(cost) over its judged nets (long nets
weigh more), and the board's the same over every judged net. The report
lists each class's worst nets, which is where a re-route round starts.

Report facts: `quality` = {score, judged, nets_total, classes: {name:
{score, nets, judged, ideal_mm, length_mm, vias, bends, worst: [...]}},
nets: {net: {...}}, weights}. No violations unless --min-quality Q: then
each judged net under Q is a warning (kind route_quality; it is a measure of
taste, not of failure, so it never errors). It is not in the verify suite:
it judges, the router and route_improve act on it.

CLI: --pcb board.kicad_pcb [--constraints c.json] [--min-quality Q]
     [--out report.json]   exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402

SCRIPT = "route_quality"
KIND = "route_quality"
VIA_MM = 1.5      # a via costs as much as this much track (inductance, stub,
                  # a hole in the planes)
BEND_MM = 0.25    # an excess bend costs this much: a zig-zag is many bends
                  # whose length alone undersells it
PARTIAL = 0.5     # tracks under this share of the ideal: not routed by tracks
KEY_ND = 3        # joint matching resolution (mm decimals)
TURN_DEG = 1.0    # heading change under this is not a bend
WORST_N = 5
CLASSES = ("plane", "diff_pair", "matched", "rf", "high_speed", "power",
           "signal")
SQ2M1 = math.sqrt(2.0) - 1.0


def octi(a, b) -> float:
    """Shortest 0/45/90-degree path length between two points."""
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return max(dx, dy) + SQ2M1 * min(dx, dy)


def mst_length(pts: list) -> float:
    """Prim's MST over pts under the octilinear metric."""
    if len(pts) < 2:
        return 0.0
    best = [math.inf] * len(pts)
    used = [False] * len(pts)
    best[0] = 0.0
    total = 0.0
    for _ in range(len(pts)):
        i = min((j for j in range(len(pts)) if not used[j]),
                key=lambda j: best[j])
        used[i] = True
        total += best[i]
        for j in range(len(pts)):
            if not used[j]:
                d = octi(pts[i], pts[j])
                if d < best[j]:
                    best[j] = d
    return total


def _heading(a, b) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def count_bends(tracks) -> int:
    """Bare joints where two of the net's straight segments meet on one
    layer and the heading turns (a joint with three or more is a tee)."""
    ends: dict[tuple, list] = {}
    for t in tracks:
        pts = list(t.shape.coords)
        if len(pts) != 2 or math.dist(pts[0], pts[1]) < 1e-6:
            continue
        a, b = pts
        for p, q in ((a, b), (b, a)):
            key = (t.layer, round(p[0], KEY_ND), round(p[1], KEY_ND))
            ends.setdefault(key, []).append(_heading(p, q))
    bends = 0
    for hs in ends.values():
        if len(hs) != 2:
            continue
        # both headings point away from the joint: straight-through is 180
        d = abs(hs[0] - hs[1]) % 360.0
        d = min(d, 360.0 - d)
        if abs(180.0 - d) > TURN_DEG:
            bends += 1
    return bends


def _pad_points(pads) -> list:
    # one point per distinct pad (a THT pad repeats per layer in some boards)
    seen, out = set(), []
    for p in pads:
        k = (p.ref, p.number, round(p.copper_center[0], 4),
             round(p.copper_center[1], 4))
        if k not in seen:
            seen.add(k)
            out.append(p.copper_center)
    return out


def _free_vias(pads) -> int:
    common = None
    for p in pads:
        s = set(p.layers)
        common = s if common is None else common & s
    return 0 if common else 1


def _netclass_of(pro: dict, net: str) -> str | None:
    ns = (pro or {}).get("net_settings") or {}
    assigned = (ns.get("netclass_assignments") or {}).get(net)
    if assigned:
        return assigned if isinstance(assigned, str) else assigned[0]
    for p in ns.get("netclass_patterns") or []:
        pat = p.get("pattern", "")
        if pat == net or fnmatch.fnmatchcase(net, pat):
            return p.get("netclass")
    return None


def classify(bg, constraints: dict | None, pro: dict | None) -> tuple[dict, list]:
    """(net -> class, groups) where groups are the matched sets (diff pairs
    and length_match / length_groups groups, globs resolved) whose members
    share one ideal."""
    import check_diffpair

    c = constraints or {}
    cls: dict[str, str] = {}
    groups: list[list[str]] = []
    zone_nets = {z.net for z in bg.zones_of() if z.net}
    plane = zone_nets | {p.get("net") for p in c.get("planes", []) or []}
    pairs = [(d.get("p"), d.get("n")) for d in c.get("diff_pairs", []) or []]
    named = {n for pr in pairs for n in pr}
    pairs += [pr for pr in check_diffpair.discover_pairs(bg.nets)
              if not (set(pr) & named)]
    hs = {h.get("net"): h for h in c.get("high_speed", []) or []}
    rf = {r.get("net") for r in c.get("rf", []) or []}
    rf |= {n for n, h in hs.items() if h.get("impedance_ohm")}
    power = {p.get("net") for p in c.get("power", []) or []}

    def put(net, name):
        if net and net in bg.nets and net not in cls:
            cls[net] = name

    for n in plane:
        put(n, "plane")
    for p, n in pairs:
        put(p, "diff_pair")
        put(n, "diff_pair")
        groups.append([x for x in (p, n) if x])
    for g in c.get("length_match", []) or []:
        nets = [n for n in g.get("nets", []) or []]
        for n in nets:
            put(n, "matched")
        groups.append(nets)
    for g in c.get("length_groups", []) or []:
        if not isinstance(g, dict):
            continue
        import check_length_groups as clg
        pats = list(g.get("nets") or []) + clg._ref_nets(g)
        nets = clg.match_nets(pats, bg.nets)[0]
        for n in nets:
            put(n, "matched")
        groups.append(nets)
    for n in rf:
        put(n, "rf")
    for n in hs:
        put(n, "high_speed")
    for n in power:
        put(n, "power")
    for n in sorted(bg.nets):
        if n not in cls:
            nc = _netclass_of(pro, n) or ""
            cls[n] = "power" if nc.startswith("Pwr_") else "signal"
    return cls, groups


def net_facts(bg, net: str, cls: str) -> dict:
    pads = bg.pads_of(net=net)
    tracks = bg.tracks_of(net=net)
    vias = bg.vias_of(net=net)
    pts = _pad_points(pads)
    length = sum(t.length for t in tracks)
    bends = count_bends(tracks)
    f = {"class": cls, "pads": len(pts), "own_ideal_mm": mst_length(pts),
         "length_mm": length, "vias": len(vias), "bends": bends,
         "free_vias": _free_vias(pads), "free_bends": max(0, len(pts) - 1)}
    return f


def judge(f: dict) -> None:
    """Fill quality/cost into f (ideal_mm already set) or a reason."""
    f["quality"] = None
    if f["class"] == "plane":
        f["reason"] = "pour net: its zones carry it"
    elif f["pads"] < 2:
        f["reason"] = "fewer than two pads"
    elif f["length_mm"] <= 0:
        f["reason"] = "no tracks"
    elif f["own_ideal_mm"] <= 1e-6:
        f["reason"] = "pads coincide"
    elif f["length_mm"] < PARTIAL * f["own_ideal_mm"]:
        f["reason"] = "tracks cover under half the ideal: not routed by tracks"
    if f.get("reason"):
        return
    ev = max(0, f["vias"] - f["free_vias"])
    eb = max(0, f["bends"] - f["free_bends"])
    cost = f["length_mm"] + VIA_MM * ev + BEND_MM * eb
    f["cost_mm"] = cost
    f["quality"] = min(1.0, f["ideal_mm"] / cost) if cost > 0 else 1.0


def score_board(bg, constraints: dict | None = None,
                pro: dict | None = None) -> dict:
    cls, groups = classify(bg, constraints, pro)
    nets = {n: net_facts(bg, n, cls[n]) for n in sorted(bg.nets) if n}
    for f in nets.values():
        f["ideal_mm"] = f["own_ideal_mm"]
    for g in groups:
        members = [n for n in g if n in nets]
        if not members:
            continue
        target = max(nets[n]["own_ideal_mm"] for n in members)
        for n in members:
            nets[n]["ideal_mm"] = max(nets[n]["own_ideal_mm"], target)
            nets[n]["group"] = members
    for f in nets.values():
        judge(f)

    def roll(fs: list) -> dict:
        judged = [f for f in fs if f["quality"] is not None]
        ideal = sum(f["ideal_mm"] for f in judged)
        cost = sum(max(f["cost_mm"], f["ideal_mm"]) for f in judged)
        return {"score": checklib.rnd(ideal / cost, 4) if cost > 0 else None,
                "nets": len(fs), "judged": len(judged),
                "ideal_mm": checklib.rnd(ideal, 2),
                "length_mm": checklib.rnd(sum(f["length_mm"] for f in judged), 2),
                "vias": sum(f["vias"] for f in judged),
                "bends": sum(f["bends"] for f in judged)}

    classes = {}
    for name in CLASSES:
        members = {n: f for n, f in nets.items() if f["class"] == name}
        if not members:
            continue
        r = roll(list(members.values()))
        worst = sorted((n for n, f in members.items() if f["quality"] is not None),
                       key=lambda n: (members[n]["quality"], n))[:WORST_N]
        r["worst"] = [{"net": n, "quality": checklib.rnd(members[n]["quality"], 3),
                       "length_mm": checklib.rnd(members[n]["length_mm"], 2),
                       "ideal_mm": checklib.rnd(members[n]["ideal_mm"], 2),
                       "vias": members[n]["vias"], "bends": members[n]["bends"]}
                      for n in worst]
        classes[name] = r
    total = roll(list(nets.values()))
    for f in nets.values():
        for k in ("own_ideal_mm", "ideal_mm", "length_mm", "cost_mm", "quality"):
            if f.get(k) is not None:
                f[k] = checklib.rnd(f[k], 4)
    return {"score": total["score"], "judged": total["judged"],
            "nets_total": total["nets"], "classes": classes, "nets": nets,
            "weights": {"via_mm": VIA_MM, "bend_mm": BEND_MM,
                        "partial": PARTIAL}}


def worst_nets(quality: dict, classes=None, below: float = 1.0) -> list[str]:
    """Judged nets under `below`, worst first by the cost they waste
    (cost - ideal), optionally only of some classes."""
    rows = [(f["cost_mm"] - f["ideal_mm"], n) for n, f in quality["nets"].items()
            if f["quality"] is not None and f["quality"] < below
            and (classes is None or f["class"] in classes)]
    return [n for _w, n in sorted(rows, key=lambda r: (-r[0], r[1]))]


def _sidecar(pcb: Path, arg: str | None, name: str) -> dict | None:
    p = Path(arg) if arg else pcb.parent / name
    if not p.is_file():
        return None
    return checklib.load_json(p, name)


def load_context(pcb: Path, constraints: str | None = None):
    cons = _sidecar(pcb, constraints, "constraints.json")
    pro_p = pcb.with_suffix(".kicad_pro")
    try:
        pro = json.loads(pro_p.read_text(encoding="utf-8")) if pro_p.is_file() else None
    except (OSError, ValueError):
        pro = None
    return cons, pro


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (default: beside the board)")
    ap.add_argument("--min-quality", type=float, default=None,
                    help="warn on each judged net under this quality")
    ap.add_argument("--out", help="write the report JSON here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.exists():
        raise checklib.CheckError(f"board not found: {pcb}")
    cons, pro = load_context(pcb, args.constraints)
    q = score_board(geom.BoardGeom.from_file(pcb), cons, pro)
    violations = []
    if args.min_quality is not None:
        for n in worst_nets(q, below=args.min_quality):
            f = q["nets"][n]
            violations.append(checklib.violation(
                SCRIPT, "warning", None, None, n, [],
                f"{n} ({f['class']}): route quality {f['quality']:.2f} under "
                f"{args.min_quality:.2f} - {f['length_mm']:.1f} mm against "
                f"{f['ideal_mm']:.1f} mm ideal, {f['vias']} vias, "
                f"{f['bends']} bends", SCRIPT, kind=KIND,
                quality=f["quality"]))
    return checklib.report(SCRIPT, pcb, violations, quality=q), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
