"""check_route_style.py - routing style score: straight and 45-degree copper.

One concern: copper that works but is not routed the way the owner wants it -
"straight and 45-degree traces, no needless arcs" (owner, g0-sense render).
Deterministic geometry from the .kicad_pcb; the verify-reviewer looks at the
review renders (render.py --views review) for what this cannot see.

Three styles are counted per copper track (geom.load_board, every copper layer):
 - arc:   a track arc (geom samples it into a >2-point centreline). Every arc
          counts - an arc an RF or diff-pair net needs is a waiver, not a pass.
 - angle: a straight segment whose heading is more than ANGLE_TOL_DEG off a
          multiple of 45 degrees AND whose far end lands more than LATERAL_MM
          off that nearest 45 heading (so coordinate rounding on a short
          segment is not a finding). Segments shorter than MIN_LEN_MM skip.
 - jog:   segments s1, s2, s3 chained through two bare joints (exactly two
          tracks of the net meet there, no pad or via) where s3 resumes s1's
          heading and the sidestep between their lines is under
          max(track width, JOG_MM) - a kink too small to clear anything.

Severity (owner, 2026-10-08: straight and 45-degree copper, no needless
arcs, is a rule, not a preference): `arc` and `angle` findings are ERRORS, so
they fail the verify gate until they are fixed or waived; `jog` stays a
warning. route_cleanup.py's snap pass (pass 3, run in the routing stage)
rewrites every off-angle segment it can, so what reaches verify is what it
had to leave. One violation per (net, layer, style) with every instance in
`items` (pos, detail, and the track `uuid` route_edit.py removes by), kind
`route_style` (remediation: reference/remediations/route_style.md). The
violation carries the net, so a net that needs its geometry - an RF feed's
arc, a length-matched or diff-pair net's meander - is waived per net in
reports/verify-waivers.json ({"check": "check_route_style", "kind":
"route_style", "net": ..., "reason": ..., "approved": ...}).

Report facts: `style` = {segments, arcs, off_angle, jogs, flagged, score}
where score = 1 - flagged / (segments + arcs), 1.0 on a board with no tracks.

CLI: --pcb board.kicad_pcb [--out report.json]   exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
from checklib import violation  # noqa: E402

SCRIPT = "check_route_style"
KIND = "route_style"
ANGLE_TOL_DEG = 1.0   # heading this far off a 45 multiple is off-angle ...
LATERAL_MM = 0.01     # ... when the end also lands this far off that heading
MIN_LEN_MM = 0.05     # shorter segments carry no heading worth judging
JOG_MM = 0.25         # sidestep floor under which a jog clears nothing
KEY_ND = 4            # joint matching resolution (mm decimals)

SEVERITY = {"arc": "error", "angle": "error", "jog": "warning"}

MSG = {
    "arc": "track arc(s) - owner style is straight and 45-degree copper",
    "angle": "segment(s) off the 45-degree grid",
    "jog": "needless jog(s): a sidestep smaller than the track clears nothing",
}


def _heading(a, b) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def _off45(deg: float) -> float:
    """Degrees from the nearest multiple of 45."""
    r = deg % 45.0
    return min(r, 45.0 - r)


def _key(p) -> tuple[float, float]:
    return (round(p[0], KEY_ND), round(p[1], KEY_ND))


def _same_heading(h1: float, h2: float) -> bool:
    d = abs(h1 - h2) % 360.0
    return min(d, 360.0 - d) <= ANGLE_TOL_DEG


def off_angle(a, b) -> bool:
    length = math.dist(a, b)
    if length < MIN_LEN_MM:
        return False
    err = _off45(_heading(a, b))
    return err > ANGLE_TOL_DEG and length * math.sin(math.radians(err)) > LATERAL_MM


def _blocked_points(bg, net: str, layer: str):
    """Pads and vias of the net on the layer: a joint on one is not bare."""
    polys = [v.poly for v in bg.vias_of(net=net, layer=layer)]
    polys += [p.poly for p in bg.pads_of(net=net, layer=layer)]
    return polys


def find_jogs(segs: list, blocked: list) -> list[tuple]:
    """segs: [(a, b, width, uuid)] straight segments of one net on one layer.
    Returns [(pos, sidestep, uuid of s2)] one per needless jog."""
    ends: dict[tuple, list[int]] = {}
    for i, (a, b, _w, _u) in enumerate(segs):
        ends.setdefault(_key(a), []).append(i)
        ends.setdefault(_key(b), []).append(i)

    def bare(k) -> bool:
        if len(ends.get(k, ())) != 2:
            return False
        pt = Point(k)
        return not any(p.covers(pt) for p in blocked)

    def oriented(i, start_key):
        a, b = segs[i][:2]
        return (a, b) if _key(a) == start_key else (b, a)

    jogs, seen = [], set()
    for mid in range(len(segs)):
        a, b, w, uuid = segs[mid]
        ka, kb = _key(a), _key(b)
        if not (bare(ka) and bare(kb)):
            continue
        i1 = next(j for j in ends[ka] if j != mid)
        i3 = next(j for j in ends[kb] if j != mid)
        if i1 == i3:
            continue
        p0, p1 = oriented(i1, ka)[::-1]        # s1 runs into joint ka
        q0, q1 = oriented(i3, kb)              # s3 runs out of joint kb
        if min(math.dist(p0, p1), math.dist(q0, q1)) < MIN_LEN_MM:
            continue
        h1, h2, h3 = _heading(p0, p1), _heading(*oriented(mid, ka)), _heading(q0, q1)
        if not _same_heading(h1, h3) or _same_heading(h1, h2):
            continue
        # sidestep = distance of s3's start from s1's (infinite) line
        ux, uy = math.cos(math.radians(h1)), math.sin(math.radians(h1))
        side = abs((q0[0] - p1[0]) * uy - (q0[1] - p1[1]) * ux)
        width = max(w, segs[i1][2], segs[i3][2])
        if side < max(width, JOG_MM) and mid not in seen:
            seen.add(mid)
            jogs.append((((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), side, uuid))
    return jogs


def score_board(bg) -> tuple[dict, list[dict]]:
    groups: dict[tuple, list] = {}   # (net, layer) -> [(a, b, width, uuid)]
    found: dict[tuple, list] = {}    # (net, layer, style) -> [(pos, detail, uuid)]
    segments = arcs = 0
    for t in bg.tracks_of():
        pts = list(t.shape.coords)
        if len(pts) > 2:
            arcs += 1
            found.setdefault((t.net, t.layer, "arc"), []).append(
                (pts[len(pts) // 2], f"arc r~{_chord_radius(pts):.2f} mm", t.uuid))
            continue
        segments += 1
        a, b = pts
        groups.setdefault((t.net, t.layer), []).append((a, b, t.width, t.uuid))
        if off_angle(a, b):
            found.setdefault((t.net, t.layer, "angle"), []).append(
                (((a[0] + b[0]) / 2, (a[1] + b[1]) / 2),
                 f"heading {_heading(a, b):.1f} deg", t.uuid))
    for (net, layer), segs in groups.items():
        for pos, side, uuid in find_jogs(segs, _blocked_points(bg, net, layer)):
            found.setdefault((net, layer, "jog"), []).append(
                (pos, f"sidestep {side:.3f} mm", uuid))

    violations = []
    for (net, layer, style), hits in sorted(found.items()):
        hits.sort(key=lambda h: (h[0], h[1]))
        msg = f"{len(hits)} {MSG[style]} on {net or '<no net>'} {layer}"
        v = violation(SCRIPT, SEVERITY[style], hits[0][0], layer,
                      net or None, [], msg, SCRIPT, kind=KIND, style=style)
        v["items"] = [{"msg": d, "pos": [checklib.rnd(p[0]), checklib.rnd(p[1])],
                       "uuid": u} for p, d, u in hits]
        violations.append(v)
    counts = {s: sum(len(h) for (_n, _l, st), h in found.items() if st == s)
              for s in ("arc", "angle", "jog")}
    total = segments + arcs
    # a segment can be both off-angle and a jog's middle; count it once
    # (by uuid; a synthetic board without uuids falls back to position)
    flagged = len({u or p for hits in found.values() for p, _d, u in hits})
    style = {"segments": segments, "arcs": arcs, "off_angle": counts["angle"],
             "jogs": counts["jog"], "flagged": flagged,
             "score": checklib.rnd(1.0 - flagged / total, 3) if total else 1.0}
    return style, violations


def _chord_radius(pts) -> float:
    r = geom._arc_radius(pts[0], pts[len(pts) // 2], pts[-1])
    return r if r else 0.0


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--out", help="write the report JSON here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.exists():
        raise checklib.CheckError(f"board not found: {pcb}")
    style, violations = score_board(geom.load_board(pcb))
    return checklib.report(SCRIPT, pcb, violations, style=style), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
