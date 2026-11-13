"""zone_patch - add, reshape or remove a same-net copper patch (PATCH_<net> zone) with a via array.

  hwde zone_patch --pcb B.kicad_pcb --net NET --region X0,Y0,X1,Y1
                  [--poly "x,y x,y x,y ..."] [--layer F.Cu] [--vias N]
                  [--via-size 0.6] [--via-drill 0.3] [--remove] [--dry-run]
  hwde zone_patch --pcb B.kicad_pcb --net NET --from END --to END
                  [--half-width 0.5] [--layer F.Cu] ...

A patch is a solid zone named PATCH_<net> that sits above the pour
(priority 5, solid pad connection, clearance 0.2, min width 0.25). It joins a
power pad to a set of vias down to the inner planes: review-2's POWER-1 asks
for at least 6 x 0.3 mm vias at every layer change of a 5 A path.

  - The patch outline is the --region rectangle, or the --poly polygon
    (--region defaults to the polygon's bounding box).
  - Any PATCH_<net> zone on --layer whose bounding box lies inside --region
    is replaced. Running the command twice gives the same board, and
    reshaping a patch is the same call with a new --poly.
  - --vias N makes sure N vias of the net sit inside the outline. Vias that
    are already there count towards N. New vias go on a 0.2 mm grid, and only
    where they clear other-net copper on every layer by 0.2 mm, stay off every
    SMD pad (no via in pad), keep the 0.5 mm hole-to-hole floor to every hole,
    and stay 0.6 mm inside the board edge.
  - --remove drops the patch zones in --region and keeps their vias.
  - --from/--to is corridor mode (#926): the outline is the path a maze
    router (lib/mazeroute.py) finds from one END to the other on --layer,
    around other nets' copper with the board's clearances, buffered by
    --half-width. An END is "U1.3", "U1" (its pads on NET) or "x,y". Use it
    to pour a wide supply strip through a gap in a fence of vias, where a
    hand-drawn polygon would clip a via. --region, when given, only bounds
    which old patches are replaced.

The edit runs on a staged copy. The zone goes in through the SWIG worker
(route_swig add_zones) and the vias through route_edit. The copy is then
refilled against the board's own rules and checked: the patch is there with
its outline and fills copper, and the vias are in. Only then is it swapped in.
On a failure the board is left untouched. Status is violations when fewer
than N vias fit or the patch fills nothing.

Replaces the board-local scripts via_array.py, patch_outline.py and
drop_patch.py (pinned core).
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import routelib  # noqa: E402
from checklib import CheckError  # noqa: E402

PRIORITY, CLEARANCE, MIN_WIDTH = 5, 0.2, 0.25
CLR, H2H, PITCH, EDGE = 0.2, 0.5, 0.2, 0.6


def _nums(text: str, n: int | None, what: str) -> list[float]:
    try:
        vals = [float(v) for v in text.replace(",", " ").split()]
    except ValueError as e:
        raise CheckError(f"{what}: not numbers: {text!r}") from e
    if n is not None and len(vals) != n:
        raise CheckError(f"{what}: want {n} numbers, got {len(vals)}")
    return vals


def _outline(args) -> tuple[list[list[float]], list[float]]:
    """(outline points, selection box) from --poly / --region."""
    region = _nums(args.region, 4, "--region") if args.region else None
    if region:
        x0, y0, x1, y1 = region
        region = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    if args.poly:
        v = _nums(args.poly, None, "--poly")
        if len(v) < 6 or len(v) % 2:
            raise CheckError("--poly: want at least 3 x,y points")
        pts = [[v[i], v[i + 1]] for i in range(0, len(v), 2)]
        if region is None:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            region = [min(xs), min(ys), max(xs), max(ys)]
    elif region:
        x0, y0, x1, y1 = region
        pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    else:
        raise CheckError("give --region and/or --poly")
    from shapely.geometry import Polygon
    if not Polygon(pts).is_valid or Polygon(pts).area <= 0:
        raise CheckError("patch outline is not a simple polygon")
    return pts, region


def corridor_outline(bg, args) -> tuple[list[list[float]], list[float]]:
    """(outline, selection box) of the corridor from --from to --to."""
    import mazeroute
    poly = mazeroute.Router(bg, layers=[args.layer]).corridor(
        args.net, args.from_, args.to, args.half_width)[0].get(args.layer)
    if poly is None or poly.is_empty:
        raise CheckError(f"corridor {args.from_} -> {args.to}: no copper on "
                         f"{args.layer}")
    poly = poly.simplify(0.01)
    if poly.geom_type != "Polygon":   # one strip: keep its biggest piece
        poly = max(poly.geoms, key=lambda g: g.area)
    pts = [[round(x, 4), round(y, 4)] for x, y in poly.exterior.coords[:-1]]
    box = _nums(args.region, 4, "--region") if args.region else None
    if box:
        box = [min(box[0], box[2]), min(box[1], box[3]),
               max(box[0], box[2]), max(box[1], box[3])]
    else:
        box = list(poly.bounds)
    return pts, box


def _in_box(pts, box, slack=0.05) -> bool:
    return all(box[0] - slack <= x <= box[2] + slack
               and box[1] - slack <= y <= box[3] + slack for x, y in pts)


def patches(bg, net: str, layer: str, box) -> list:
    """The PATCH_<net> zones on layer whose outline lies inside box."""
    return [z for z in bg.zones_of(net=net, layer=layer)
            if z.name == f"PATCH_{net}" and z.outline
            and _in_box(z.outline, box)]


def plan_vias(bg, net: str, pts, want: int, size: float, drill: float):
    """(existing same-net vias inside the outline, new via centres)."""
    from shapely.geometry import Point, Polygon, box as sbox
    from shapely.ops import unary_union
    outline = Polygon(pts)
    have = [v for v in bg.vias_of(net=net)
            if v.kind == "through" and outline.contains(Point(v.at))]
    need = max(0, want - len(have))
    if not need:
        return have, []
    r = size / 2
    near = sbox(*outline.buffer(r + CLR + 2).bounds)
    obst = []
    for t in bg.tracks_of():
        if t.net != net and t.poly.intersects(near):
            obst.append(t.poly)
    for v in bg.vias_of():
        if v.net != net and v.poly.intersects(near):
            obst.append(v.poly)
    holes = [(v.at, v.drill / 2) for v in bg.vias_of()]
    for p in bg.pads_of():
        if p.drill:
            holes.append((p.center, max(p.drill) / 2))
        # other nets for clearance; same-net SMD pads too (open vias in a
        # pad wick the solder away)
        if (p.net != net or not p.net or p.drill is None) \
                and p.poly.intersects(near):
            obst.append(p.poly)
    for p in bg.npth:
        if p.drill:
            holes.append((p.center, max(p.drill) / 2))
    blocked = unary_union(obst) if obst else None
    inside = bg.outline.buffer(-EDGE)
    m = r + 0.1
    room = outline.buffer(-m)
    x0, y0, x1, y1 = outline.bounds
    gx = [x0 + m + i * PITCH for i in range(int((x1 - x0 - 2 * m) / PITCH) + 1)]
    gy = [y0 + m + j * PITCH for j in range(int((y1 - y0 - 2 * m) / PITCH) + 1)]
    placed: list[tuple[float, float]] = []
    for y in gy:
        for x in gx:
            if len(placed) >= need:
                return have, placed
            c = Point(x, y)
            if not room.covers(c) or not inside.covers(c):
                continue
            if blocked is not None and blocked.distance(c) < r + CLR - 1e-9:
                continue
            if any(math.hypot(x - hx, y - hy) - hr - drill / 2 < H2H - 1e-9
                   for (hx, hy), hr in holes):
                continue
            placed.append((round(x, 4), round(y, 4)))
            holes.append(((x, y), drill / 2))
    return have, placed


def _stage(pcb: Path, work: Path) -> Path:
    """Board + same-stem sidecars (pro/dru): the refill needs the rules."""
    for src in pcb.parent.glob(pcb.stem + ".*"):
        if src.is_file() and not src.name.endswith(".lck"):
            shutil.copy2(src, work / src.name)
    return work / pcb.name


def _same_ring(a, b, tol=1e-3) -> bool:
    a, b = [tuple(p) for p in a], [tuple(p) for p in b]
    return len(a) == len(b) and all(
        abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol
        for p, q in zip(a, b))


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--net", required=True)
    ap.add_argument("--region", default=None, help="X0,Y0,X1,Y1 mm")
    ap.add_argument("--poly", default=None, help='"x,y x,y x,y ..." mm')
    ap.add_argument("--from", dest="from_", default=None,
                    help="corridor mode: END the strip starts at")
    ap.add_argument("--to", default=None, help="corridor mode: END it ends at")
    ap.add_argument("--half-width", type=float, default=0.5,
                    help="corridor mode: half the strip's width, mm")
    ap.add_argument("--layer", default="F.Cu")
    ap.add_argument("--vias", type=int, default=0)
    ap.add_argument("--via-size", type=float, default=0.6)
    ap.add_argument("--via-drill", type=float, default=0.3)
    ap.add_argument("--remove", action="store_true",
                    help="drop the patch zones in --region, keep the vias")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)
    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    if args.vias < 0 or not 0 < args.via_drill < args.via_size:
        raise CheckError("--vias >= 0 and 0 < --via-drill < --via-size")
    corridor = bool(args.from_ or args.to)
    if corridor:
        if not (args.from_ and args.to):
            raise CheckError("corridor mode wants both --from and --to")
        if args.poly or args.remove:
            raise CheckError("--from/--to make the outline: no --poly or "
                             "--remove with them")
        if args.half_width <= 0:
            raise CheckError("--half-width must be > 0")
    else:
        pts, box = _outline(args)
    bg = geom.load_board(pcb, refresh=True)
    if args.net not in bg.nets:
        raise CheckError(f"net '{args.net}' not on board")
    if args.layer not in bg.copper_layers:
        raise CheckError(f"'{args.layer}' is not a copper layer")
    if corridor:
        pts, box = corridor_outline(bg, args)
    name = f"PATCH_{args.net}"
    old = patches(bg, args.net, args.layer, box)
    if args.remove:
        have, new = [], []
    else:
        have, new = plan_vias(bg, args.net, pts, args.vias, args.via_size,
                              args.via_drill)
    facts = {"corridor": [args.from_, args.to] if corridor else None,
             "net": args.net, "layer": args.layer, "zone": name,
             "outline": None if args.remove else pts,
             "replaced" if not args.remove else "removed": len(old),
             "vias_wanted": args.vias, "vias_had": len(have),
             "vias_added": [list(p) for p in new]}
    violations = []
    if not args.remove and len(have) + len(new) < args.vias:
        violations.append(checklib.violation(
            "zone_patch_vias", "error", pts[0], args.layer, args.net, [],
            f"only {len(have) + len(new)} of {args.vias} vias fit in the "
            f"{name} outline (0.2 mm clearance, 0.5 mm hole-to-hole, no via "
            f"in an SMD pad): widen the outline", "zone_patch"))
    if args.remove and not old:
        raise CheckError(f"no {name} zone on {args.layer} inside {box}")
    if args.dry_run:
        facts["dry_run"] = True
        return checklib.report("zone_patch", str(pcb), violations,
                               **facts), args.out_report

    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    if bp is None:
        raise CheckError("KiCad bundled python not found (env.py)")
    import kc
    import route_edit
    stage = Path(tempfile.mkdtemp(prefix=".aiee_patch_", dir=pcb.parent))
    try:
        staged = _stage(pcb, stage)
        zones = [] if args.remove else [{
            "net": args.net, "layer": args.layer, "poly": pts,
            "priority": PRIORITY, "clearance": CLEARANCE,
            "min_width": MIN_WIDTH, "connect": "solid", "name": name}]
        got = routelib.run_worker(bp, {
            "verb": "add_zones", "board": str(staged), "out": str(staged),
            "zones": zones, "remove": [{"name": name, "layer": args.layer,
                                        "inside": box}]}, stage)
        if new:
            route_edit.apply_ops(staged, [
                {"op": "add_via", "at": list(p), "size": args.via_size,
                 "drill": args.via_drill, "net": args.net} for p in new])
        kc.run_drc(cli, staged, refill=True, save_board=True)

        after = geom.BoardGeom.from_file(staged)
        left = patches(after, args.net, args.layer, box)
        problems = []
        if args.remove:
            if left:
                problems.append(f"{len(left)} {name} zone(s) still in box")
        else:
            mine = [z for z in left if _same_ring(z.outline, pts)]
            if len(left) != 1 or not mine:
                problems.append(f"want one {name} with the new outline, "
                                f"found {len(left)}")
            else:
                area = mine[0].fill_area(args.layer)
                facts["fill_mm2"] = round(area, 3)
                if area <= 0:
                    violations.append(checklib.violation(
                        "zone_patch_fill", "error", pts[0], args.layer,
                        args.net, [], f"{name} fills no copper", "zone_patch"))
        at = {(round(v.at[0], 3), round(v.at[1], 3))
              for v in after.vias_of(net=args.net)}
        miss = [p for p in new if (round(p[0], 3), round(p[1], 3)) not in at]
        if miss:
            problems.append(f"vias not on the board: {miss}")
        if problems:
            raise CheckError("zone_patch verify failed (rolled back): "
                             + "; ".join(problems))
        facts["zones_removed"] = len(got.get("removed", []))
        os.replace(staged, pcb)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return checklib.report("zone_patch", str(pcb), violations,
                           **facts), args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("zone_patch", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
