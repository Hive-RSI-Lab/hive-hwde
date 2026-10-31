"""route_tune - length tuning of routed length groups (P7, hive-hwde #324).

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

Per net that needs +D mm (single-ended members only; a diff pair's halves are
check_diffpair's and are reported, not tuned):
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
    plus --margin from every other net's tracks, vias and pads on that
    layer, from same-net copper that is not the run's own neighbour, from
    copper and track keepouts, and stay min_copper_edge_clearance inside the
    outline; bumps already placed this run are obstacles for the next;
  * a net that is too LONG cannot be fixed by lengthening it and is reported
    (route it shorter, or lengthen the reference).
The ops (remove the run, add_path the meander) go through route_edit.apply_ops
(the pipeline's only routing writer). Groups whose members are another
group's reference are tuned first (DQS before its byte lane, the clock
before address/command), and the whole pass repeats while it still gains
(at most --passes). Then kicad-cli DRC runs on the result against the board
before tuning; any grown error type rolls the board back (exit 2).

CLI:
  route_tune.py --pcb B.kicad_pcb --constraints c.json [--groups A,B]
      [--amplitude MM] [--spacing MM] [--margin MM] [--passes N]
      [--dry-run] [--ops-out ops.json] [--no-drc] [--out-report r.json]
  exit 0 = every group passes check_length_groups after tuning; 1 = some
  net is still out (board keeps what was tuned); 2 = error (board untouched).
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
from shapely.ops import unary_union  # noqa: E402

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
                   side: int) -> list[tuple[float, float]] | None:
    """Centreline from a to b with k rectangular bumps of height h on `side`
    (+1 left of a->b, -1 right), legs `spacing` apart and bumps `spacing`
    apart, the train centred between the two leads. None when it can't fit."""
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

    def __init__(self, bg, clearance: float, edge: float, margin: float):
        self.bg = bg
        self.clear = clearance + margin
        self.edge = edge + margin
        self.inside = bg.outline.buffer(-self.edge) if not bg.outline.is_empty \
            else None
        self.extra: dict[str, list] = {}     # layer -> [(net, poly)] placed
        self._cache: dict[str, list] = {}

    def _layer_items(self, layer):
        if layer not in self._cache:
            bg = self.bg
            items = [(t.net, t.poly, t) for t in bg.tracks_of(layer=layer)]
            items += [(v.net, v.poly, None) for v in bg.vias_of()
                      if v.spans(layer)]
            items += [(p.net, p.poly, None) for p in bg.pads_of(layer=layer)]
            for ra in bg.rule_areas:
                fl = ra.get("flags") or {}
                if (layer in ra["layers"] and not ra["outline"].is_empty
                        and fl.get("tracks") == "not_allowed"):
                    items.append((None, ra["outline"], None))
            self._cache[layer] = items
        return self._cache[layer]

    def clears(self, poly, layer: str, net: str, own) -> bool:
        """True when `poly` (a meander's copper) keeps clearance from all but
        the run itself and the same-net copper it joins (`own`: tracks)."""
        if self.inside is not None and not self.inside.contains(poly):
            return False
        reach = poly.buffer(self.clear)
        for onet, opoly, trk in self._layer_items(layer):
            if trk is not None and trk in own:
                continue
            if onet == net and trk is None:
                continue          # own pads and vias: touching is a joint
            if reach.intersects(opoly):
                return False
        for onet, opoly in self.extra.get(layer, []):
            if reach.intersects(opoly):
                return False
        return True

    def add(self, layer: str, net: str, poly) -> None:
        self.extra.setdefault(layer, []).append((net, poly))

    def drop(self, track) -> None:
        """A run replaced by its meander stops being an obstacle."""
        for items in self._cache.values():
            items[:] = [it for it in items if it[2] is not track]


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


def plan_run(obs: Obstacles, track, need: float, *, amplitude: float,
             spacing: float):
    """(points, added_mm) for the meander on one straight run adding up to
    `need`, or None. Tries both sides; picks the side that holds the most."""
    a, b = track.shape.coords[0], track.shape.coords[-1]
    w = track.width
    lead = max(0.5, 2 * w)
    L = track.length
    kmax = max_bumps(L, spacing, lead)
    if kmax < 1:
        return None
    own = _neighbours(obs.bg, track)

    def ok(pts):
        poly = LineString(pts).buffer(w / 2, cap_style="round",
                                      join_style="round")
        return obs.clears(poly, track.layer, track.net, own)

    best = None
    for side in (1, -1):
        for f in LADDER:
            h = amplitude * f
            if h < w - EPS and f != LADDER[0]:
                break             # a bump lower than the track is a nub
            pts = meander_points(a, b, kmax, h, spacing, lead, side)
            if pts and ok(pts):
                cap = 2 * h * kmax
                if best is None or cap > best[0]:
                    best = (cap, side, h)
                break
    if best is None:
        return None
    cap, side, hcap = best
    use = min(need, cap)
    k = max(1, math.ceil(use / (2 * hcap) - EPS))
    k = min(k, kmax)
    for kk in (k, kmax):
        h = use / (2 * kk)
        if kk > 1 and h < max(MIN_BUMP_MM, w) - EPS:
            continue              # many nubs: one low bump, or fewer tall
        pts = meander_points(a, b, kk, h, spacing, lead, side)
        if pts and ok(pts):
            return pts, 2 * h * kk
    return None


# ------------------------------------------------------------ targets

def pair_nets(bg, cons) -> set[str]:
    out: set[str] = set()
    for spec in check_diffpair.resolve_pairs(bg, cons):
        out.update((spec["p"], spec["n"]))
    return out


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


# ------------------------------------------------------------ one pass

def plan_pass(bg, groups, cons, opts, sel) -> tuple[list, list, list]:
    """(ops, tuned facts, findings) for one pass over the selected groups on
    board `bg` (measured once; later groups see earlier ops as obstacles)."""
    floors = _floors(bg)
    obs = Obstacles(bg, floors["clearance"], floors["edge_clearance"],
                    opts.margin)
    pairs = pair_nets(bg, cons)
    cache: dict = {}
    ops, tuned, findings = [], [], []
    used: set = set()
    planned: dict[str, float] = {}       # net -> mm added this pass
    for i in group_order(groups, bg.nets):
        g = groups[i]
        if sel and g.get("name") not in sel:
            continue
        _, facts = clg.check_group(bg, g, cache)
        for net, t in sorted(targets(facts, g).items()):
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
            if net in pairs:
                findings.append(violation(
                    SCRIPT, "warning", None, None, net, [],
                    f"length group {g.get('name')}: {net} needs "
                    f"{t['aim'] - L:+.3f} mm but is half of a diff pair; "
                    f"pairs are tuned as pairs, not here",
                    SCRIPT, kind="tune_pair_member", group=g.get("name")))
                continue
            need = t["aim"] - L
            m = cache[(net, tuple(g.get("terminals") or ()))]
            runs = sorted((tr for tr in m.get("path_tracks") or []
                           if len(tr.shape.coords) == 2 and tr.uuid
                           and tr not in used),
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
    return ops, tuned, findings


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
    ap.add_argument("--passes", type=int, default=3)
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

    all_ops, tuned, findings, passes = [], [], [], 0
    backup = None
    drc_before = None
    if not args.dry_run and not args.no_drc:
        drc_before = _drc(pcb)
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
    violations = findings + ([] if args.dry_run else left)
    payload = checklib.report(
        SCRIPT, str(pcb), violations, dry_run=bool(args.dry_run),
        passes=passes, ops=len(all_ops), tuned=tuned,
        groups=[{"name": g["name"], "spread_mm": g["spread_mm"],
                 "reference_mm": g["reference_mm"]} for g in check["groups"]],
        drc=drc if not args.dry_run else None)
    return payload, args.out_report


def _drc(pcb: Path) -> dict:
    import env  # noqa: PLC0415
    import kc  # noqa: PLC0415
    cli = env.find_kicad_cli()
    if cli is None:
        raise CheckError("kicad-cli not found (env.py); pass --no-drc to skip")
    return kc.run_drc(cli, pcb, all_track_errors=True)


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
