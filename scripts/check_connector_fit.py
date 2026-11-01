"""check_connector_fit.py - does each part's metal go where its drawing says?
(P8 verify suite)

The hits (hifi-receiver-2 first-spin review, 2026-10-29): core's RCA jacks
sat on a footprint with no holes for their locating posts, so the posts
stood on the board top and the signal pins never reached the solder side
(DFM-1); Ultra's TOSLINK was a T8BK on a T10BK footprint (DFM-12). Every
gate passed: nothing compared the footprint with the part's mechanical
drawing. A reviewer did it by hand; this check does it on every board.

The drawing comes from the part's datasheet extraction, <parts>/<LCSC>.json,
its `mechanical` block (datasheet_extract.py; `--render` puts the drawing
pages beside the text, OCR'd when they are scans):

  pins   [{pads?, dia_mm | w_mm + t_mm, hole_mm?}]  the leads' max
         cross-section; `pads` defaults to every numbered plated hole
  posts  [{count, dia_mm | w_mm + t_mm, hole_mm?}]  locating posts or pegs,
         each needing a hole of its own
  edge   {overhang_mm, tol_mm?}  how far the body's front stands past the
         PCB edge line on the drawing (negative: set back behind it)

Findings:
  tht_pin_tight       error: a lead's cross-section (a rectangular pin's
                      diagonal, or per axis in a slot) leaves less than
                      PIN_CLEAR_MM in its hole - at JLC's -0.08 mm
                      plated-hole tolerance it may not go in at all
  tht_hole_ipc2222    warning (one per part): a round hole that takes the
                      lead but is under IPC-2222 9.2.1's minimum, the lead's
                      max diameter (a square/rect lead's diagonal) + 0.15 mm
                      (level C; B 0.20, A 0.25). A 0.64 mm square header pin
                      wants 1.055 mm; KiCad's stock 1.0 mm drill fits in
                      practice but leaves no room for solder to wet through
  tht_hole_loose      warning: a round hole more than LOOSE_MM over the
                      lead (poor wetting, the part leans)
  tht_hole_under_spec warning: the hole is under the drawing's hole_mm by
                      more than SPEC_TOL_MM
  tht_pad_missing     error: a pad the drawing's pins name is not a plated
                      hole on the footprint (SMD, or no such pad)
  post_holes_missing  error: fewer holes that fit a post than the drawing
                      has posts (holes the pins do not use count; a hole too
                      small for the post is named)
  edge_overhang_off   error: the body's front stands past the board edge
                      by a different amount than the drawing says, beyond
                      tol_mm (default EDGE_TOL_MM); a warning when the body
                      outline is only the courtyard (no F.Fab, no 3D model)

Every mating connector's front vs Edge.Cuts is reported in the facts
(`edges`: overhang_mm and where the body outline came from: F.Fab, the 3D
model's box or the courtyard), drawing or not; connectors whose extraction
has no `mechanical` block are listed under `unverified`, so a review sees
what this check could not hold to a drawing. A part with no extraction is
never failed for it. A part with no `mechanical.pins` whose footprint is a
stock pin header is held to reference/tht_leads.yaml's lead instead (the
part's fact says `lead_source: stock`).

CLI: --pcb board.kicad_pcb [--parts DIR] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import sexpdata
import yaml
from shapely import affinity
from shapely.geometry import LineString, MultiPoint

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import fplib  # noqa: E402
import matinglib  # noqa: E402
import placelib  # noqa: E402
from geom import _kids, _strs  # noqa: E402

SCRIPT = "check_connector_fit"
SOURCE = SCRIPT  # verify_all, gate and the scorecard match a finding to its check by source
# A lead needs this much more than its cross-section: JLC's plated holes
# finish -0.08/+0.13 mm of the drill size, so less may not take the lead.
PIN_CLEAR_MM = 0.08
# IPC-2222A 9.2.1: plated hole minus the lead's max diameter, per level
IPC2222_MM = {"A": 0.25, "B": 0.20, "C": 0.15}
IPC2222_LEVEL = "C"
LEADS = HERE.parent / "reference" / "tht_leads.yaml"
# A round hole this much over the lead leaves it loose (IPC-2222 level B is
# lead + 0.25 mm; 0.7 mm is well past any class).
LOOSE_MM = 0.70
SPEC_TOL_MM = 0.10
# Unplated holes finish +-0.05 mm of the drill.
POST_TOL_MM = 0.05
EDGE_TOL_MM = 0.30
CONNECTOR_REF = re.compile(r"^(J|CN|P|USB|X)\d", re.I)


def _cross(spec: dict) -> tuple[float, float] | None:
    """(short, long) side of a pin or post's cross-section, or None."""
    if spec.get("dia_mm"):
        return float(spec["dia_mm"]), float(spec["dia_mm"])
    if spec.get("w_mm") and spec.get("t_mm"):
        return tuple(sorted((float(spec["w_mm"]), float(spec["t_mm"]))))
    return None


def clearance(drill: tuple[float, float], cross: tuple[float, float],
              rect: bool | None = None) -> float:
    """The least gap (hole minus lead) when the lead goes through the hole:
    a round hole against the lead's diagonal, a slot per axis. `rect` says
    the lead is a square/rectangular pin (w_mm + t_mm), so a square one is
    measured across its diagonal too; None reads it from the sides."""
    dmin, dmax = sorted(drill)
    pmin, pmax = cross
    if rect is None:
        rect = abs(pmax - pmin) >= 1e-6
    if abs(dmax - dmin) < 1e-6:
        return dmin - (math.hypot(pmin, pmax) if rect else pmin)
    return min(dmin - pmin, dmax - pmax)


def _fmt_cross(c: tuple[float, float], rect: bool = True) -> str:
    return (f"{c[0]:.2f} mm round" if not rect
            else f"{c[0]:.2f} mm square" if abs(c[0] - c[1]) < 1e-6
            else f"{c[0]:.2f} x {c[1]:.2f} mm")


def _fmt_drill(d: tuple[float, float]) -> str:
    return (f"{d[0]:.2f} mm" if abs(d[0] - d[1]) < 1e-6
            else f"{min(d):.2f} x {max(d):.2f} mm slot")


class LocalFrame:
    """A library footprint standing in for a placed one (fp_verify): its own
    frame is the board's."""

    def __init__(self, fpid: str):
        self.fpid, self.pos = fpid, (0.0, 0.0)

    def to_abs(self, xy):
        return (xy[0], xy[1])


def load_board(pcb: Path) -> dict[str, dict]:
    """ref -> {lcsc, node, pads: [fplib.Pad]} from the board file (fplib's
    pad parse keeps unplated holes, which the copper model drops)."""
    try:
        root = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise checklib.CheckError(f"cannot parse {pcb}: {exc}") from exc
    out = {}
    for fp in _kids(root, "footprint"):
        props = {}
        for p in _kids(fp, "property"):
            s = _strs(p)
            if len(s) >= 2:
                props[s[0]] = s[1]
        ref = props.get("Reference")
        if not ref:
            continue
        pads = [p for p in (fplib._parse_pad(n) for n in _kids(fp, "pad"))
                if p is not None]
        out[ref] = {"lcsc": (props.get("LCSC") or props.get("LCSC Part")
                             or "").strip().upper(),
                    "node": fp, "pads": pads}
    return out


def load_extractions(parts: Path | None, lcscs: set[str]) -> dict[str, dict]:
    out = {}
    if parts is None:
        return out
    for lcsc in sorted(x for x in lcscs if x):
        f = parts / f"{lcsc}.json"
        if f.is_file():
            out[lcsc] = checklib.load_json(f, f"extraction {f.name}")
    return out


def _holes(pads) -> list:
    return [p for p in pads if p.ptype in ("thru_hole", "np_thru_hole")
            and p.drill_xy and min(p.drill_xy) > 0]


def check_pins(ref, f, pads, mech, viols, fact):
    holes = _holes(pads)
    used: set[int] = set()
    by_num: dict[str, list] = {}
    for p in holes:
        if p.ptype == "thru_hole" and p.number:
            by_num.setdefault(p.number, []).append(p)
    fits, ipc = [], []
    for spec in mech.get("pins") or []:
        cross = _cross(spec)
        rect = not spec.get("dia_mm")
        names = [str(n) for n in spec.get("pads") or []] or sorted(by_num)
        for num in names:
            got = by_num.get(num)
            if not got:
                viols.append(checklib.violation(
                    SCRIPT, "error", f.pos, None, None, [ref],
                    f"{ref}: the drawing has a lead through pad {num}, but "
                    f"the footprint ({f.fpid}) has no plated hole {num}",
                    SOURCE, kind="tht_pad_missing", pad=num,
                    footprint=f.fpid))
                continue
            for p in got:
                used.add(id(p))
                if cross is None:
                    continue
                gap = clearance(p.drill_xy, cross, rect)
                pos = f.to_abs(p.at[:2])
                fits.append({"pad": num, "drill": [checklib.rnd(v, 3) for v in p.drill_xy],
                             "lead": list(cross), "gap_mm": checklib.rnd(gap, 3)})
                what = (f"{ref} pad {num}: the lead ({_fmt_cross(cross, rect)}) "
                        f"in a {_fmt_drill(p.drill_xy)} hole")
                if gap < PIN_CLEAR_MM:
                    viols.append(checklib.violation(
                        SCRIPT, "error", pos, None, None, [ref],
                        f"{what} leaves {gap:.3f} mm (the plated hole "
                        f"finishes up to {PIN_CLEAR_MM} mm under its drill): it "
                        "may not go in. Use the drawing's hole, or the "
                        "footprint of the part actually ordered", SOURCE,
                        kind="tht_pin_tight", pad=num, gap_mm=checklib.rnd(gap, 3),
                        footprint=f.fpid))
                elif abs(p.drill_xy[0] - p.drill_xy[1]) < 1e-6 \
                        and gap > LOOSE_MM:
                    viols.append(checklib.violation(
                        SCRIPT, "warning", pos, None, None, [ref],
                        f"{what} leaves {gap:.2f} mm: loose (over "
                        f"{LOOSE_MM} mm), the joint may not fill and the "
                        "part may lean - is this the right footprint?",
                        SOURCE, kind="tht_hole_loose", pad=num,
                        gap_mm=checklib.rnd(gap, 3), footprint=f.fpid))
                elif abs(p.drill_xy[0] - p.drill_xy[1]) < 1e-6 \
                        and gap < IPC2222_MM[IPC2222_LEVEL] - 1e-6:
                    ipc.append((num, p, cross, gap, pos, rect))
                if spec.get("hole_mm") and \
                        min(p.drill_xy) < float(spec["hole_mm"]) - SPEC_TOL_MM:
                    viols.append(checklib.violation(
                        SCRIPT, "warning", pos, None, None, [ref],
                        f"{what}: the drawing asks for a "
                        f"{float(spec['hole_mm']):.2f} mm hole", SOURCE,
                        kind="tht_hole_under_spec", pad=num,
                        footprint=f.fpid))
    if ipc:
        num, p, cross, gap, pos, rect = min(ipc, key=lambda t: t[3])
        need = p.drill_xy[0] - gap + IPC2222_MM[IPC2222_LEVEL]
        pads_ = sorted({t[0] for t in ipc}, key=_padkey)
        viols.append(checklib.violation(
            SCRIPT, "warning", pos, None, None, [ref],
            f"{ref}: {len(ipc)} plated hole(s) (pads {', '.join(pads_)}) of "
            f"{_fmt_drill(p.drill_xy)} leave {gap:.3f} mm over the lead "
            f"({_fmt_cross(cross, rect)}, {p.drill_xy[0] - gap:.3f} mm max "
            f"diameter); IPC-2222 level {IPC2222_LEVEL} wants lead + "
            f"{IPC2222_MM[IPC2222_LEVEL]} mm = {need:.3f} mm (B "
            f"{IPC2222_MM['B']}, A {IPC2222_MM['A']}). It goes in, but tight: "
            f"drill {math.ceil(need * 20 - 1e-6) / 20:.2f} mm for solder to "
            f"wet through", SOURCE, kind="tht_hole_ipc2222", pads=pads_,
            gap_mm=checklib.rnd(gap, 3), min_hole_mm=checklib.rnd(need, 3),
            level=IPC2222_LEVEL, footprint=f.fpid))
    fact["pins"] = fits
    return used


def _padkey(n: str):
    return (0, int(n), "") if n.isdigit() else (1, 0, n)


def load_leads(path: Path = LEADS) -> list[dict]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [{**e, "re": re.compile(e["match"])} for e in data.get("leads") or []]


def stock_lead(fpid: str, leads: list[dict]) -> dict | None:
    """The stock-table `mechanical` for a footprint, or None."""
    name = fpid.split(":")[-1]
    for e in leads:
        if e["re"].search(name):
            return {"pins": e["pins"], "source": e.get("source", "")}
    return None


def check_posts(ref, f, pads, mech, used, viols, fact):
    # the holes the pins took are not post holes; with no pins in the
    # drawing, a numbered plated hole is taken as a lead's
    spare = [p for p in _holes(pads) if id(p) not in used]
    if not mech.get("pins"):
        spare = [p for p in spare
                 if p.ptype == "np_thru_hole" or not p.number]
    rows = []
    for spec in mech.get("posts") or []:
        cross = _cross(spec)
        rect = not spec.get("dia_mm")
        need = int(spec["count"])
        if spec.get("hole_mm"):
            ok = [p for p in spare
                  if min(p.drill_xy) >= float(spec["hole_mm"]) - POST_TOL_MM]
        elif cross is not None:
            ok = [p for p in spare
                  if clearance(p.drill_xy, cross, rect) >= POST_TOL_MM]
        else:
            ok = list(spare)
        small = [p for p in spare if p not in ok]
        rows.append({"count": need, "holes": len(ok)})
        if len(ok) >= need:
            for p in ok[:need]:
                spare.remove(p)
            continue
        size = (f"{float(spec['hole_mm']):.2f} mm hole" if spec.get("hole_mm")
                else f"{_fmt_cross(cross, rect)} post" if cross else "post")
        also = (" (" + ", ".join(_fmt_drill(p.drill_xy) for p in small)
                + " too small)") if small else ""
        viols.append(checklib.violation(
            SCRIPT, "error", f.pos, None, None, [ref],
            f"{ref}: the drawing has {need} locating post(s) ({size}) but "
            f"the footprint ({f.fpid}) has {len(ok)} hole(s) that take one"
            f"{also}: the posts stand on the board and the leads may not "
            "reach the solder side. Add the post holes to the footprint",
            SOURCE, kind="post_holes_missing", posts=need, holes=len(ok),
            footprint=f.fpid))
    fact["posts"] = rows


def body_local(f, node, meta, pcb) -> tuple:
    """(local body polygon, where it came from): F.Fab (B.Fab on the back),
    else the 3D model's box, else the courtyard."""
    fab = placelib._courtyard_poly(node, "B.Fab" if f.side == "back"
                                   else "F.Fab")
    if fab is not None and not fab.is_empty and fab.area > 0.01:
        return fab.convex_hull, "fab"
    corners = matinglib.model_corners_local(f, meta, pcb)
    if corners:
        hull = MultiPoint(corners).convex_hull
        if hull.area > 0.01:
            return hull, "model"
    return f.extents_local(), "courtyard"


def _front_gap(f, outline, body, u) -> float:
    """Board edge minus the body's front, along u: the edge is found on a ray
    from the pads (on the board, unlike the centroid of an edge-launch
    part's body), the front is the body's furthest point along u."""
    pads = [f.to_abs(p.local) for p in f.pads] or [f.center_abs()]
    ox = sum(x for x, _ in pads) / len(pads)
    oy = sum(y for _, y in pads) / len(pads)
    t0 = ox * u[0] + oy * u[1]
    ray = LineString([(ox, oy), (ox + u[0] * 1e4, oy + u[1] * 1e4)])
    hit = ray.intersection(outline.exterior)
    ts = [g.x * u[0] + g.y * u[1] for g in
          (getattr(hit, "geoms", None) or [hit]) if not g.is_empty
          and g.geom_type == "Point"]
    ts = [t for t in ts if t >= t0 - 1e-9]
    front = max(x * u[0] + y * u[1] for x, y in body.exterior.coords)
    return (min(ts) if ts else t0) - front


def edge_fact(ref, f, node, meta, pcb, outline, table):
    """{overhang_mm, mouth, body} for a connector with a readable front, or
    None. The front faces its mouth (matinglib) or, failing that, its
    nearest edge."""
    local, src = body_local(f, node, meta, pcb)
    body = affinity.translate(affinity.rotate(local, -f.angle, origin=(0, 0)),
                              f.pos[0], f.pos[1])
    d = matinglib.mouth_local(f, table)
    if d is not None:
        u = matinglib._to_abs_dir(f, d)
        facing = matinglib._snap(*u)
    else:
        gaps = {k: matinglib._edge_gap(outline, body, a)
                for k, a in matinglib.AXES.items()}
        facing = min(gaps, key=gaps.get)
        u = matinglib.AXES[facing]
    gap = _front_gap(f, outline, body, u)
    return {"ref": ref, "facing": facing, "body": src,
            "mouth_from": "pads" if d is not None else "nearest edge",
            "overhang_mm": checklib.rnd(-gap, 2)}


def check(pcb: Path, parts: Path | None) -> tuple[list[dict], dict]:
    model = placelib.PlaceModel(pcb)
    board = load_board(pcb)
    exts = load_extractions(parts, {b["lcsc"] for b in board.values()})
    metas = matinglib.board_meta(pcb)
    table = matinglib.load_table()
    leads = load_leads()
    viols: list[dict] = []
    facts = {"parts": [], "edges": [], "unverified": []}
    for ref in sorted(model.footprints):
        f, b = model.footprints[ref], board.get(ref)
        if b is None:
            continue
        ext = exts.get(b["lcsc"])
        mech = (ext or {}).get("mechanical") or {}
        fam = matinglib.family_of(f.fpid, table)
        connector = fam is not None or bool(CONNECTOR_REF.match(ref))
        if connector and not mech:
            facts["unverified"].append(
                {"ref": ref, "lcsc": b["lcsc"] or None,
                 "why": "no extraction" if ext is None
                 else "extraction has no mechanical block"})
        fact = {"ref": ref, "lcsc": b["lcsc"]}
        if mech:
            used = check_pins(ref, f, b["pads"], mech, viols, fact)
            check_posts(ref, f, b["pads"], mech, used, viols, fact)
        elif (stock := stock_lead(f.fpid, leads)) is not None:
            fact["lead_source"] = "stock"
            check_pins(ref, f, b["pads"], stock, viols, fact)
            facts["parts"].append(fact)
        edge = None
        if (fam is not None and fam.get("entry") == "horizontal") \
                or mech.get("edge"):
            edge = edge_fact(ref, f, b["node"], metas.get(ref), pcb,
                             model.outline, table)
            facts["edges"].append(edge)
        want = mech.get("edge")
        if want and edge is not None:
            tol = float(want.get("tol_mm", EDGE_TOL_MM))
            off = edge["overhang_mm"] - float(want["overhang_mm"])
            edge["drawing_overhang_mm"] = float(want["overhang_mm"])
            if abs(off) > tol:
                rough = edge["body"] == "courtyard"
                viols.append(checklib.violation(
                    SCRIPT, "warning" if rough else "error", f.center_abs(),
                    "Edge.Cuts", None, [ref],
                    f"{ref}: its front stands {edge['overhang_mm']:+.2f} mm "
                    f"past the {edge['facing']} board edge (body from the "
                    f"{edge['body']}), the drawing says "
                    f"{float(want['overhang_mm']):+.2f} mm (tol {tol} mm): "
                    + ("move it to the drawing's PCB-edge line"
                       if not rough else "the footprint has no F.Fab body or "
                       "3D model, so this is the courtyard's front - add one "
                       "or check by hand"),
                    SOURCE, kind="edge_overhang_off",
                    overhang_mm=edge["overhang_mm"],
                    drawing_overhang_mm=float(want["overhang_mm"]),
                    body=edge["body"]))
        if mech:
            facts["parts"].append(fact)
    return viols, facts


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--parts", help="the workspace parts dir (extractions)")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    parts = Path(args.parts) if args.parts else None
    viols, facts = check(pcb, parts if parts and parts.is_dir() else None)
    return checklib.report(SCRIPT, pcb, viols, **facts), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
