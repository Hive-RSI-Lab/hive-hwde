#!/usr/bin/env python
"""check_wicking.py - open vias that steal solder from SMD pads (P8 verify;
board only; hive-hwde #475).

An open via (not filled, not capped) whose barrel is exposed to a pad's
solder pulls the molten solder down the hole: the joint starves, a QFN's
exposed pad voids and the other side grows a solder ball. Valor NPI and
CAM350 flag it; Eurocircuits asks for a mask dam between any via within
0.25 mm of a pad; NXP AN-HVQFN and TI SLUA271 ask for exposed-pad thermal
vias to be plugged/tented from the far side, kept out of the paste
(window-pane) or filled (VIPPO). An exposed pad's own vias are
check_ep_paste's (paste_on_via); here they are only counted
(`ep_vias_skipped`). Findings (all warnings):

  wick_via_in_pad   a via's drill reaches 0.05 mm or more (past the mask's
                    registration) into an SMD pad's mask opening
                    (via in pad) and the via is neither filled nor capped:
                    the joint drains down the barrel. Fix: move it off the
                    pad behind a mask dam, or order the via filled and
                    capped (IPC-4761 type VII; #338) and set the board's
                    via filling/capping.
  wick_via_mask_web an untented via within 0.25 mm of an SMD pad (copper to
                    copper) whose mask opening is less than the dam minimum
                    from the pad's: the openings merge (or the fab strips
                    the sliver), so nothing dams the solder. Fix: tent the
                    via on that side, or move it out to a full dam.

Mask openings are modelled the way KiCad plots them: a pad on X.Mask grows
by its `solder_mask_margin` (pad, else footprint, else the board's
`pad_to_mask_clearance`); an untented via opens at its diameter plus the
board margin. Tenting, plugging, filling and capping are the via's own
`(tenting|plugging (front ..) (back ..))`, `(filling ..)`, `(capping ..)`,
else the board setup's (KiCad 9+), else KiCad's default (tented, open).
The dam minimum is the larger of the board's `solder_mask_min_width` and
`--dam-mm` (default 0.1, JLC's `min_solder_mask_dam_mm` in
reference/jlc_capabilities.yaml).

Cross-check: with `--gerbers DIR` (or the board's own fab/gerbers next to
kicad/, when its <board>-F_Mask file is newer than the .kicad_pcb), each
via-beside-pad web is measured again on the shipped mask gerber (gerblib)
and the gerber's web is used; `mask_crosscheck` in the facts counts the
pairs that agree with the board model (within 0.01 mm) and lists the ones
that don't.

CLI: --pcb board.kicad_pcb [--gerbers DIR] [--dam-mm D] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sexpdata
from shapely import affinity
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import check_paste  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402
from geom import _kid, _kids, _nums, _strs, _tok  # noqa: E402

SCRIPT = "check_wicking"
SOURCE = SCRIPT

NEAR_MM = 0.25      # Eurocircuits: a via this close to a pad needs a dam
DAM_MM = 0.1        # JLC min_solder_mask_dam_mm (every class)
AGREE_MM = 0.01     # board model vs gerber web
# a drill reaching less than this into a pad's mask opening is a graze
# inside the mask's registration (JLC: +-0.05 mm), not a via in the pad
INTRUDE_MM = 0.05
SIDES = ("F", "B")


def _num(node, name: str, default=None):
    n = _kid(node, name) if node is not None else None
    v = _nums(n) if n is not None else []
    return v[0] if v else default


def _yn(node, name: str) -> dict:
    """`(name (front yes) (back no))` -> {"F": True, "B": False}; KiCad 8's
    `(name front back)` lists the sides that are set. Sides not stated are
    absent (fall back to the board default)."""
    out: dict[str, bool] = {}
    n = _kid(node, name) if node is not None else None
    if n is None:
        return out
    for item in n[1:]:
        if geom._is_node(item):
            side = {"front": "F", "back": "B"}.get(str(_tok(item[0])))
            val = str(_tok(item[1])) if len(item) > 1 else ""
            if side and val in ("yes", "no"):
                out[side] = val == "yes"
        else:
            side = {"front": "F", "back": "B"}.get(str(_tok(item)))
            if side:
                out[side] = True
    if out and all(geom._is_node(i) is False for i in n[1:]):
        for s in SIDES:      # KiCad 8 list form: unlisted = not set
            out.setdefault(s, False)
    return out


def _flag(node, name: str):
    n = _kid(node, name) if node is not None else None
    if n is None or len(n) < 2:
        return None
    v = str(_tok(n[1]))
    return v == "yes" if v in ("yes", "no") else None


TENTING_ASSUMED = ("vias: the board's setup has no tenting setting, so every "
                   "via without its own was taken as tented")


def board_defaults(root) -> dict:
    setup = _kid(root, "setup")
    tent = {"F": True, "B": True}
    tent.update(_yn(setup, "tenting"))
    plug = {"F": False, "B": False}
    plug.update(_yn(setup, "plugging"))
    return {
        "tenting": tent, "plugging": plug,
        "tenting_assumed": not _yn(setup, "tenting"),
        "filling": bool(_flag(setup, "filling")),
        "capping": bool(_flag(setup, "capping")),
        "mask_margin": _num(setup, "pad_to_mask_clearance", 0.0) or 0.0,
        "mask_min_width": _num(setup, "solder_mask_min_width", 0.0) or 0.0,
    }


def read_vias(root, dflt: dict, cu: list[str]) -> list[dict]:
    out = []
    for v in _kids(root, "via"):
        at, size, drill = _kid(v, "at"), _kid(v, "size"), _kid(v, "drill")
        if at is None or size is None or drill is None:
            continue
        x, y = _nums(at)[:2]
        lay = _strs(_kid(v, "layers")) if _kid(v, "layers") is not None else []
        kind = str(_tok(v[1])) if len(v) > 1 and not geom._is_node(v[1]) else "through"
        if kind in ("blind", "buried", "micro") and len(lay) == 2 \
                and lay[0] in cu and lay[1] in cu:
            i, j = sorted((cu.index(lay[0]), cu.index(lay[1])))
            spans = set(cu[i:j + 1])
        else:
            spans = set(cu)
        tent = dict(dflt["tenting"])
        tent.update(_yn(v, "tenting"))
        plug = dict(dflt["plugging"])
        plug.update(_yn(v, "plugging"))
        fill = _flag(v, "filling")
        cap = _flag(v, "capping")
        net = _kid(v, "net")
        out.append({
            "at": (x, y), "diameter": _nums(size)[0], "drill": _nums(drill)[0],
            "sides": {s for s in SIDES if f"{s}.Cu" in spans},
            "tent": tent, "plug": plug,
            "filled": dflt["filling"] if fill is None else fill,
            "capped": dflt["capping"] if cap is None else cap,
            "net": (str(_strs(net)[-1]) if net is not None and _strs(net)
                    else None),
            "uuid": geom._uuid_of(v)})
    return out


def _intrusion(v: dict, opening) -> float:
    """How far the via's hole reaches into a mask opening (mm): its radius
    less the centre's distance to the opening, the full radius (or more)
    when the centre is inside."""
    c = Point(v["at"])
    r = v["drill"] / 2.0
    if opening.contains(c):
        return r + opening.exterior.distance(c) if hasattr(opening, "exterior") \
            else r
    return r - opening.distance(c)


def _margin(node):
    return _num(node, "solder_mask_margin")


def read_pads(root, dflt: dict) -> dict[str, list[dict]]:
    """{ref: [pad]} with its copper and its mask opening per side."""
    out: dict[str, list[dict]] = {}
    for fp in _kids(root, "footprint"):
        at = _kid(fp, "at")
        fn = _nums(at) if at is not None else [0, 0, 0]
        fx, fy, fa = fn[0], fn[1], (fn[2] if len(fn) > 2 else 0.0)
        ref = next((pv[1] for prop in _kids(fp, "property")
                    if len(pv := _strs(prop)) >= 2 and pv[0] == "Reference"),
                   "?")
        f_mm = _margin(fp)
        for pad in _kids(fp, "pad"):
            if len(pad) < 4:
                continue
            kind, shape = str(_tok(pad[2])), str(_tok(pad[3]))
            pat, size = _kid(pad, "at"), _kid(pad, "size")
            if pat is None or size is None:
                continue
            layers = check_paste._layers(pad)
            lx, ly, *rest = _nums(pat)
            angle = rest[0] if rest else 0.0
            dx, dy = geom._rot(lx, ly, -fa)
            c = (fx + dx, fy + dy)
            sz = _nums(size)
            w, h = sz[0], (sz[1] if len(sz) > 1 else sz[0])
            rr = _kid(pad, "roundrect_rratio")
            rratio = _nums(rr)[0] if rr is not None and _nums(rr) else 0.0
            if shape == "custom" and (cg := geom._custom_pad(pad, w, h)) is not None:
                cg = affinity.rotate(cg, -angle, origin=(0, 0), use_radians=False)
                copper = affinity.translate(cg, c[0], c[1])
            else:
                copper = geom._pad_polygon("rect" if shape == "custom" else shape,
                                           w, h, rratio, c, angle)
            cu_side = check_paste._cu_side(layers)
            if cu_side is None and "*.Cu" in layers:
                cu_side = "F"
            mm = next(v for v in (_margin(pad), f_mm, dflt["mask_margin"])
                      if v is not None)
            mask = {}
            for s in SIDES:
                if f"{s}.Mask" in layers or "*.Mask" in layers:
                    mask[s] = (copper.buffer(mm, join_style="mitre")
                               if mm else copper)
            out.setdefault(ref, []).append({
                "ref": ref, "number": str(pad[1]), "kind": kind,
                "cu_side": cu_side, "copper": copper, "mask": mask,
                "center": c})
    return out


def _gerber_dir(pcb: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    d = pcb.resolve().parent.parent / "fab" / "gerbers"
    f = d / f"{pcb.stem}-F_Mask.gts"
    if f.is_file() and f.stat().st_mtime >= pcb.stat().st_mtime:
        return d
    return None


class GerberMask:
    """The shipped mask openings of one side, for web measurements."""

    def __init__(self, lg):
        polys = lg.all_polys if lg is not None else []
        self.polys = [p for p in polys if not p.is_empty]
        self.tree = STRtree(self.polys) if self.polys else None

    def opening_at(self, pt):
        if self.tree is None:
            return None
        hits = [self.polys[i] for i in self.tree.query(Point(pt).buffer(1e-3))]
        hits = [g for g in hits if g.buffer(1e-4).contains(Point(pt))]
        return unary_union(hits) if hits else None

    def web(self, a, b):
        """Gap between the opening covering point a and the one covering b
        (0 when one opening covers both), or None if either has none."""
        ga, gb = self.opening_at(a), self.opening_at(b)
        if ga is None or gb is None:
            return None
        u = unary_union(self.polys)
        comps = list(u.geoms) if u.geom_type.startswith("Multi") else [u]
        ca = next((g for g in comps if g.buffer(1e-4).contains(Point(a))), ga)
        if ca.buffer(1e-4).contains(Point(b)):
            return 0.0
        cb = next((g for g in comps if g.buffer(1e-4).contains(Point(b))), gb)
        return ca.distance(cb)


def check(pcb: Path, gerbers: Path | None = None, dam_mm: float = DAM_MM
          ) -> tuple[list[dict], dict]:
    root = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    dflt = board_defaults(root)
    lyr = _kid(root, "layers")
    cu = [str(_tok(n[1])) for n in (lyr[1:] if lyr is not None else [])
          if geom._is_node(n) and len(n) > 1 and str(_tok(n[1])).endswith(".Cu")]
    cu = sorted(set(cu) | {"F.Cu", "B.Cu"},
                key=lambda n: (0 if n == "F.Cu" else 2 if n == "B.Cu" else 1,
                               int(n[2:-3]) if n.startswith("In") else 0))
    vias = read_vias(root, dflt, cu)
    fps = read_pads(root, dflt)
    dam = max(dam_mm, dflt["mask_min_width"])
    eps = {ref: check_paste.exposed_pad(p) for ref, p in fps.items()}
    gm = {}
    if gerbers is not None and gerbers.is_dir():
        import gerblib
        fab = gerblib.open_fab(gerbers)
        gm = {s: GerberMask(fab.mask(s)) for s in SIDES if fab.mask(s)}
    seen = {"vias": len(vias), "tenting_assumed": dflt["tenting_assumed"],
            "vias_filled_or_capped": 0, "smd_pads": 0,
            "pairs_near": 0, "ep_vias_skipped": 0, "dam_mm": dam,
            "gerbers": str(gerbers) if gm else None}
    cross = {"agree": 0, "disagree": []}
    vios: list[dict] = []
    pad_hits: dict[tuple, list] = {}
    for s in SIDES:
        pads = [p for ps in fps.values() for p in ps
                if p["kind"] == "smd" and p["cu_side"] == s and s in p["mask"]]
        seen["smd_pads"] += len(pads)
        if not pads:
            continue
        tree = STRtree([p["mask"][s] for p in pads])
        for v in vias:
            if s not in v["sides"]:
                continue
            if v["filled"] or v["capped"]:
                if s == "F":
                    seen["vias_filled_or_capped"] += 1
                continue
            drill = Point(v["at"]).buffer(v["drill"] / 2.0)
            vcu = Point(v["at"]).buffer(v["diameter"] / 2.0)
            reach = vcu.buffer(NEAR_MM + 1.0)
            near = [pads[i] for i in tree.query(reach)]
            inside = [p for p in near
                      if _intrusion(v, p["mask"][s]) >= INTRUDE_MM - 1e-9]
            if inside:
                p = inside[0]
                if eps.get(p["ref"]) is p:
                    seen["ep_vias_skipped"] += 1
                    continue
                pad_hits.setdefault((p["ref"], p["number"], s), []).append(v)
                continue
            if v["tent"][s]:
                continue
            vmask = Point(v["at"]).buffer(v["diameter"] / 2.0
                                          + dflt["mask_margin"])
            best = None
            for p in near:
                if vcu.distance(p["copper"]) > NEAR_MM + 1e-9:
                    continue
                seen["pairs_near"] += 1
                web = vmask.distance(p["mask"][s])
                if s in gm:
                    gw = gm[s].web(v["at"], p["center"])
                    if gw is not None:
                        if abs(gw - web) <= AGREE_MM:
                            cross["agree"] += 1
                        else:
                            cross["disagree"].append({
                                "ref": p["ref"], "pad": p["number"],
                                "at": [checklib.rnd(c) for c in v["at"]],
                                "model_web_mm": checklib.rnd(web),
                                "gerber_web_mm": checklib.rnd(gw)})
                        web = gw
                if web < dam - 1e-6 and (best is None or web < best[0]):
                    best = (web, p)
            if best is not None:
                web, p = best
                merged = web <= 1e-6
                vios.append(checklib.violation(
                    SCRIPT, "warning", v["at"], f"{s}.Mask", v["net"],
                    [p["ref"]],
                    f"{p['ref']} pad {p['number']}: untented via "
                    f"{vcu.distance(p['copper']):.2f} mm from the pad with "
                    + ("its mask opening merged into the pad's"
                       if merged else
                       f"a {web:.3f} mm mask web (dam minimum {dam} mm)")
                    + "; solder wicks into the via. Tent the via on "
                    f"{'top' if s == 'F' else 'bottom'} or move it out to a "
                    f"full mask dam", SOURCE, kind="wick_via_mask_web",
                    pad=p["number"], web_mm=checklib.rnd(web), dam_mm=dam,
                    merged=merged, via=v["uuid"]))
    for (ref, num, s), vs in sorted(pad_hits.items()):
        vios.append(checklib.violation(
            SCRIPT, "warning", vs[0]["at"], f"{s}.Cu", vs[0]["net"], [ref],
            f"{ref} pad {num}: {len(vs)} open via(s) (drill "
            f"{min(v['drill'] for v in vs)} mm) inside the pad's mask "
            f"opening; the joint's solder drains down the barrel. Move the "
            f"via off the pad behind a mask dam, or order it filled and "
            f"capped (IPC-4761 VII) and set the board's via filling/capping",
            SOURCE, kind="wick_via_in_pad", pad=num, vias=len(vs),
            drill_mm=min(v["drill"] for v in vs),
            at=[[checklib.rnd(c) for c in v["at"]] for v in vs]))
    if gm:
        seen["mask_crosscheck"] = cross
    return vios, seen


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--gerbers", help="shipped gerber dir to cross-check")
    ap.add_argument("--dam-mm", type=float, default=DAM_MM)
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    vios, seen = check(pcb, _gerber_dir(pcb, args.gerbers), args.dam_mm)
    return checklib.report(
        SCRIPT, pcb, vios, checked=[seen], not_judged=[TENTING_ASSUMED]
        if seen.get("tenting_assumed") and seen.get("vias") else None), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
