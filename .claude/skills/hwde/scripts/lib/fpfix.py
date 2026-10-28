#!/usr/bin/env python
"""fpfix.py - sanitise pulled (easyeda2kicad) footprints against the fab floor.

Every easyeda2kicad pull ships the same four library-inherent defects, all of
them measured against real DRC on the shipped boards (LEARNINGS 2026-07-28
[easyeda2kicad][drc], boards/*/lib/EDITS.md):

  A  sub-printable silk: `fp_circle` artifacts at 0.06-0.10 mm stroke, drawn at
     the first courtyard vertex, frequently ON or INSIDE pad 1. Below JLC's
     0.15 mm minimum line width they cannot print at all, so the ones clear of
     copper are PROMOTED to the printable floor (this is how a pin-1 mark gets
     kept) and the ones over copper are DELETED.
  B  silk under the silk-to-copper bar: outlines that clear pad copper by less
     than the bar. Fixed by NARROWING the stroke, never by moving coordinates
     (0.25 -> 0.20 buys 0.025 mm per edge and keeps the geometry byte-identical).
     Violating items sharing an original width narrow together, so a footprint
     outline keeps ONE uniform stroke.
  C  plated locating pegs: `(pad "" thru_hole circle (size D D) (drill D))` -
     copper diameter == drill diameter, i.e. zero annular ring. Two DRC ERRORS
     each plus a clearance error against the neighbouring pad. They are
     mechanical holes; the fix is the `np_thru_hole` padstack (position, size
     and drill unchanged).
  D  body legend text: `fp_text user` marks that sit INSIDE the part body and
     collide with the footprint's own silk outline (the 3-position DIP switch
     ships 8 `silk_overlap` warnings this way). They are hidden under the part
     once assembled, so they are deleted.

Text surgery on the raw file: only the nodes that change are rewritten, so
nothing else can be reformatted, and re-running is a no-op (idempotent).
Handles BOTH the legacy `(module ...)`/`(width W)` form easyeda2kicad emits and
KiCad-10's `(footprint ...)`/`(stroke (width W))` form.

Library API:
  sanitize(text, **opts) -> (new_text, [action, ...])
  analyze(text, **opts)  -> {"items": [...], "worst_gap_mm": float|None, ...}
  fix_file(path, **opts) -> report dict

CLI (SPEC section 6): fpfix.py --lib <dir>.pretty [--dry-run] [--out r.json]
  exit 0 = clean or fixed, 1 = residue left (a defect no rule can repair), 2 = error
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import sexpdata
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geom  # noqa: E402  (pad polygons + arc sampling: one geometry source)

# JLC's minimum silkscreen line width; anything thinner cannot print.
MIN_LINE_WIDTH = 0.15
# Required silk-stroke-edge to pad-copper-edge gap (the EDITS.md acceptance bar).
MIN_GAP = 0.15
# Stroke widths are chosen on this grid so a narrowed outline stays a round number.
WIDTH_GRID = 0.05
# Fraction of the nominal glyph height used as per-character advance (stroke font).
TEXT_ADVANCE = 0.75

_SILK = "SilkS"
_GRAPHIC_HEADS = ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc")
_PLACEHOLDER = re.compile(r"%[A-Z]")


class FpFixError(RuntimeError):
    pass


# --------------------------------------------------------------- text scanning

def top_level_nodes(text: str) -> list[tuple[str, int, int]]:
    """(head, start, end) for every direct child node of the root s-expression.

    A paren scanner rather than a full parse, because the edit has to be a
    substring replacement: sexpdata round-tripping the whole file would
    reformat every node in it.
    """
    depth = 0
    in_str = False
    esc = False
    start = -1
    out: list[tuple[str, int, int]] = []
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
            if depth == 2:
                start = i
        elif ch == ")":
            if depth == 2 and start >= 0:
                sub = text[start:i + 1]
                m = re.match(r"\(\s*([A-Za-z_][\w]*)", sub)
                out.append(((m.group(1) if m else ""), start, i + 1))
                start = -1
            depth -= 1
    return out


def _parse(sub: str):
    try:
        return sexpdata.loads(sub)
    except Exception as exc:  # noqa: BLE001
        raise FpFixError(f"unparsable node: {sub[:60]}... ({exc})") from exc


def _tok(x):
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def _kid(node, name):
    for c in node[1:] if isinstance(node, list) else []:
        if isinstance(c, list) and c and _tok(c[0]) == name:
            return c
    return None


def _nums(node) -> list[float]:
    return [float(x) for x in node[1:] if isinstance(x, (int, float))] if node else []


def _layers(node) -> list[str]:
    out = []
    for key in ("layer", "layers"):
        k = _kid(node, key)
        if k is not None:
            out += [str(_tok(t)) for t in k[1:]]
    return out


def _stroke_width(node) -> float | None:
    """(width W) [legacy] or (stroke (width W) ...) [KiCad 10]."""
    w = _kid(node, "width")
    if w is not None and _nums(w):
        return _nums(w)[0]
    st = _kid(node, "stroke")
    if st is not None:
        sw = _kid(st, "width")
        if sw is not None and _nums(sw):
            return _nums(sw)[0]
    return None


_W_LEGACY = re.compile(r"\(width\s+(-?[\d.]+)\s*\)")
_W_MODERN = re.compile(r"\(stroke\b((?:[^()]|\([^()]*\))*?)\(width\s+(-?[\d.]+)\s*\)")


def set_width(sub: str, new_w: float) -> str:
    """Rewrite a graphic node's stroke width, both file formats."""
    txt = f"{new_w:g}"
    if _W_LEGACY.search(sub):
        return _W_LEGACY.sub(lambda m: f"(width {txt})", sub, count=1)
    m = _W_MODERN.search(sub)
    if m:
        return sub[:m.start(2)] + txt + sub[m.end(2):]
    raise FpFixError("graphic has no stroke width to rewrite")


def _is_filled(node) -> bool:
    f = _kid(node, "fill")
    if f is None:
        return False
    if len(f) > 1:
        v = str(_tok(f[1])).lower()
        if v in ("solid", "yes", "true"):
            return True
        if v in ("none", "no", "false"):
            return False
        t = _kid(f, "type")
        if t is not None and len(t) > 1:
            return str(_tok(t[1])).lower() == "solid"
    return False


# ------------------------------------------------------------------- geometry

def _pts(node) -> list[tuple[float, float]]:
    out = []
    for c in node[1:] if isinstance(node, list) else []:
        if isinstance(c, list) and c and _tok(c[0]) == "xy":
            n = _nums(c)
            if len(n) >= 2:
                out.append((n[0], n[1]))
    return out


def graphic_shape(node):
    """Centerline geometry of a graphic node (unbuffered), or None."""
    head = _tok(node[0])
    if head == "fp_line":
        s, e = _kid(node, "start"), _kid(node, "end")
        if s is None or e is None:
            return None
        (sx, sy), (ex, ey) = _nums(s)[:2], _nums(e)[:2]
        if (sx, sy) == (ex, ey):
            return Point(sx, sy)
        return LineString([(sx, sy), (ex, ey)])
    if head == "fp_rect":
        s, e = _kid(node, "start"), _kid(node, "end")
        if s is None or e is None:
            return None
        (x1, y1), (x2, y2) = _nums(s)[:2], _nums(e)[:2]
        ring = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        return Polygon(ring) if _is_filled(node) else LineString(ring)
    if head == "fp_circle":
        c, e = _kid(node, "center"), _kid(node, "end")
        if c is None or e is None:
            return None
        (cx, cy), (ex, ey) = _nums(c)[:2], _nums(e)[:2]
        r = math.hypot(ex - cx, ey - cy)
        disk = Point(cx, cy).buffer(max(r, 1e-6), quad_segs=24)
        return disk if _is_filled(node) else disk.exterior
    if head == "fp_arc":
        s, m, e = _kid(node, "start"), _kid(node, "mid"), _kid(node, "end")
        if s is not None and m is not None and e is not None:
            pts = geom._arc_points(tuple(_nums(s)[:2]), tuple(_nums(m)[:2]),
                                   tuple(_nums(e)[:2]))
            return LineString(pts) if len(pts) > 1 else None
        # legacy (fp_arc (start CENTER) (end START) (angle DEG))
        a = _kid(node, "angle")
        if s is None or e is None or a is None:
            return None
        (cx, cy), (sx, sy) = _nums(s)[:2], _nums(e)[:2]
        ang = _nums(a)[0]
        r = math.hypot(sx - cx, sy - cy)
        a0 = math.atan2(sy - cy, sx - cx)
        n = max(8, int(abs(ang) / 10) + 2)
        pts = [(cx + r * math.cos(a0 + math.radians(ang) * i / n),
                cy + r * math.sin(a0 + math.radians(ang) * i / n))
               for i in range(n + 1)]
        return LineString(pts)
    if head == "fp_poly":
        pts = _pts(_kid(node, "pts") or node)
        if len(pts) < 2:
            return None
        if _is_filled(node) and len(pts) >= 3:
            p = Polygon(pts)
            return p if p.is_valid and p.area > 0 else LineString(pts + [pts[0]])
        return LineString(pts + [pts[0]] if len(pts) > 2 else pts)
    return None


def _pad_poly(node):
    """Copper polygon of a pad node in footprint-local coordinates."""
    if len(node) < 4:
        return None
    ptype, shape = str(_tok(node[2])), str(_tok(node[3]))
    if ptype == "np_thru_hole":
        return None
    layers = _layers(node)
    if not any(ly.endswith(".Cu") or ly == "*.Cu" for ly in layers):
        return None
    at, size = _kid(node, "at"), _kid(node, "size")
    if at is None or size is None:
        return None
    a, s = _nums(at), _nums(size)
    if len(a) < 2 or len(s) < 2:
        return None
    rr = _kid(node, "roundrect_rratio")
    rratio = _nums(rr)[0] if rr is not None and _nums(rr) else 0.25
    rot = a[2] if len(a) >= 3 else 0.0
    return geom._pad_polygon(shape, s[0], s[1], rratio, (a[0], a[1]), rot)


def _text_box(node):
    """Inked box of an fp_text node (approximate, rotation-correct).

    Height is the nominal glyph height plus stroke thickness (KiCad's DRC
    measures the inked box, LEARNINGS 2026-07-29 [parts][silk]); width uses a
    per-character advance of TEXT_ADVANCE x size.
    """
    at = _kid(node, "at")
    if at is None or len(node) < 3:
        return None, ""
    a = _nums(at)
    if len(a) < 2:
        return None, ""
    txt = str(_tok(node[2]))
    eff = _kid(node, "effects")
    font = _kid(eff, "font") if eff is not None else None
    size = _nums(_kid(font, "size")) if font is not None and _kid(font, "size") else []
    h = size[1] if len(size) >= 2 else 1.0
    wch = size[0] if size else 1.0
    th = _nums(_kid(font, "thickness")) if font is not None and _kid(font, "thickness") else []
    t = th[0] if th else 0.15
    hw = (len(txt) * wch * TEXT_ADVANCE + t) / 2.0
    hh = (h + t) / 2.0
    ang = math.radians(a[2] if len(a) >= 3 else 0.0)
    ca, sa = math.cos(ang), math.sin(ang)
    corners = [(dx * ca - dy * sa + a[0], dx * sa + dy * ca + a[1])
               for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
    return Polygon(corners), txt


def _gap(shape, copper, width: float) -> float:
    """Signed stroke-edge to copper-edge gap: d(centerline) - width/2."""
    if copper is None or copper.is_empty or shape is None:
        return float("inf")
    return shape.distance(copper) - width / 2.0


def _floor_grid(w: float) -> float:
    return math.floor(w / WIDTH_GRID + 1e-9) * WIDTH_GRID


# ------------------------------------------------------------------ the rules

def _collect(text: str):
    """Parsed top-level nodes plus the copper union and the silk item list."""
    nodes = []
    for head, s, e in top_level_nodes(text):
        if head in _GRAPHIC_HEADS + ("pad", "fp_text"):
            nodes.append((head, s, e, _parse(text[s:e])))
    copper = unary_union([p for p in (_pad_poly(n) for h, _, _, n in nodes
                                      if h == "pad") if p is not None])
    silk = []
    for head, s, e, node in nodes:
        if head not in _GRAPHIC_HEADS:
            continue
        if not any(_SILK in ly for ly in _layers(node)):
            continue
        w = _stroke_width(node)
        sh = graphic_shape(node)
        if sh is None:
            continue
        silk.append({"head": head, "start": s, "end": e, "node": node,
                     "width": 0.0 if w is None else w, "shape": sh,
                     "has_width": w is not None, "filled": _is_filled(node)})
    return nodes, copper, silk


def analyze(text: str, min_gap: float = MIN_GAP,
            min_line_width: float = MIN_LINE_WIDTH) -> dict:
    """Measure a footprint without changing it (the report lib_pull emits)."""
    nodes, copper, silk = _collect(text)
    items, worst = [], None
    for it in silk:
        g = _gap(it["shape"], copper, it["width"])
        if g != float("inf") and (worst is None or g < worst):
            worst = g
        items.append({"kind": it["head"], "width_mm": round(it["width"], 4),
                      "gap_mm": None if g == float("inf") else round(g, 4),
                      # a filled graphic prints from its fill whatever its stroke
                      "printable": it["filled"] or it["width"] >= min_line_width - 1e-9})
    pegs = sum(1 for h, _, _, n in nodes if h == "pad" and _peg(n))
    return {"silk_items": len(silk), "items": items,
            "worst_gap_mm": None if worst is None else round(worst, 4),
            "unprintable_silk": sum(1 for i in items if not i["printable"]),
            "under_gap_silk": sum(1 for i in items
                                  if i["gap_mm"] is not None and i["gap_mm"] < min_gap - 1e-9),
            "plated_pegs": pegs,
            "copper_pads": sum(1 for h, _, _, n in nodes
                               if h == "pad" and _pad_poly(n) is not None)}


def _peg(node) -> bool:
    """A plated locating peg: unnamed thru_hole whose copper == its drill."""
    if len(node) < 4 or str(_tok(node[2])) != "thru_hole":
        return False
    name = str(_tok(node[1]))
    if name not in ("", '""'):
        return False
    size, drill = _kid(node, "size"), _kid(node, "drill")
    if size is None or drill is None:
        return False
    s, d = _nums(size), _nums(drill)
    if len(s) < 2 or not d:
        return False
    # circular hole, no annular ring anywhere
    return max(s[0], s[1]) <= max(d) + 1e-6


_PAD_TYPE = re.compile(r"(\(pad\s+(?:\"[^\"]*\"|\S+)\s+)thru_hole\b")


def sanitize(text: str, *, min_gap: float = MIN_GAP,
             min_line_width: float = MIN_LINE_WIDTH,
             promote_silk: bool = True, fix_pegs: bool = True,
             fix_body_text: bool = True) -> tuple[str, list[dict]]:
    """Apply rules A-D. Returns (new_text, actions); idempotent."""
    nodes, copper, silk = _collect(text)
    actions: list[dict] = []
    edits: list[tuple[int, int, str | None, str]] = []  # start, end, replacement|None

    # ---- A: silk below the printable floor -> promote if clear, else delete
    survivors = []
    for it in silk:
        # A FILLED graphic prints from its fill, not its stroke: widening it
        # would grow the printed shape (the DIP switch's slider indicators are
        # solid fp_polys at stroke 0). Only unfilled strokes are promotable,
        # and a stroke of exactly 0 has no width to scale from.
        thin = (it["has_width"] and 0 < it["width"] < min_line_width - 1e-9
                and not it["filled"])
        if thin:
            if promote_silk and _gap(it["shape"], copper, min_line_width) >= min_gap - 1e-9:
                edits.append((it["start"], it["end"],
                              set_width(text[it["start"]:it["end"]], min_line_width),
                              "promote"))
                actions.append({"rule": "A", "action": "promote_silk_width",
                                "kind": it["head"], "from_mm": round(it["width"], 4),
                                "to_mm": min_line_width,
                                "gap_mm": round(_gap(it["shape"], copper, min_line_width), 4)})
                it = dict(it, width=min_line_width)
                survivors.append(it)
            else:
                edits.append((it["start"], it["end"], None, "delete"))
                actions.append({"rule": "A", "action": "delete_unprintable_silk",
                                "kind": it["head"], "width_mm": round(it["width"], 4),
                                "gap_mm": round(_gap(it["shape"], copper, it["width"]), 4)})
            continue
        survivors.append(it)

    # ---- B: narrow (never move) silk that sits under the silk-to-copper bar
    violators = []
    for it in survivors:
        g = _gap(it["shape"], copper, it["width"])
        if g == float("inf") or g >= min_gap - 1e-9:
            continue
        if not it["has_width"] or it["width"] <= 0 or it["filled"]:
            # no stroke to give back - the ink IS the geometry, and moving
            # coordinates is out of scope for an automatic library fix
            actions.append({"rule": "B", "action": "residue_silk_gap",
                            "kind": it["head"], "width_mm": round(it["width"], 4),
                            "gap_mm": round(g, 4),
                            "detail": "filled or stroke-less graphic: only a "
                                      "coordinate change could open this gap"})
            continue
        d = it["shape"].distance(copper)          # centerline distance to copper
        w_max = 2.0 * (d - min_gap)
        violators.append((it, g, w_max))
    # Items that share an original width narrow together: a footprint outline
    # keeps ONE uniform stroke (this is what the approved EDITS.md recipe did).
    by_width: dict[float, float] = {}
    for it, _g, w_max in violators:
        k = round(it["width"], 4)
        by_width[k] = min(by_width.get(k, float("inf")), w_max)
    for it, g, _w_max in violators:
        w_new = _floor_grid(by_width[round(it["width"], 4)])
        if w_new < min_line_width - 1e-9 or w_new >= it["width"] - 1e-9:
            actions.append({"rule": "B", "action": "residue_silk_gap",
                            "kind": it["head"], "width_mm": round(it["width"], 4),
                            "gap_mm": round(g, 4),
                            "detail": "cannot reach the gap by narrowing to the "
                                      f"{min_line_width:g} mm line-width floor"})
            continue
        edits.append((it["start"], it["end"],
                      set_width(text[it["start"]:it["end"]], w_new), "narrow"))
        actions.append({"rule": "B", "action": "narrow_silk_stroke",
                        "kind": it["head"], "from_mm": round(it["width"], 4),
                        "to_mm": round(w_new, 4), "gap_mm": round(g, 4),
                        "new_gap_mm": round(_gap(it["shape"], copper, w_new), 4)})

    # ---- C: plated locating pegs -> np_thru_hole
    if fix_pegs:
        for head, s, e, node in nodes:
            if head != "pad" or not _peg(node):
                continue
            sub = text[s:e]
            new = _PAD_TYPE.sub(lambda m: m.group(1) + "np_thru_hole", sub, count=1)
            if new == sub:
                continue
            at = _nums(_kid(node, "at"))
            edits.append((s, e, new, "peg"))
            actions.append({"rule": "C", "action": "peg_to_npth",
                            "at": [round(v, 3) for v in at[:2]],
                            "drill_mm": round(max(_nums(_kid(node, "drill"))), 3)})

    # ---- D: legend text hidden under the body that collides with own silk
    if fix_body_text:
        silk_geoms = [it["shape"].buffer(max(it["width"], 0.01) / 2.0)
                      for it in survivors]
        body = unary_union(silk_geoms).envelope if silk_geoms else None
        for head, s, e, node in nodes:
            if head != "fp_text" or len(node) < 2:
                continue
            if str(_tok(node[1])) != "user":
                continue
            if not any(_SILK in ly for ly in _layers(node)):
                continue
            box, txt = _text_box(node)
            if box is None or _PLACEHOLDER.fullmatch(txt.strip()):
                continue
            if body is None or not body.contains(box.centroid):
                continue
            if not any(box.intersects(g) for g in silk_geoms):
                continue
            edits.append((s, e, None, "body_text"))
            actions.append({"rule": "D", "action": "delete_body_legend_text",
                            "text": txt, "at": [round(v, 3) for v in _nums(_kid(node, "at"))[:2]]})

    if not edits:
        return text, actions
    out = text
    for start, end, repl, _why in sorted(edits, key=lambda t: -t[0]):
        if repl is None:
            # drop the node and the whitespace-only line it sat on
            ls = out.rfind("\n", 0, start) + 1
            le = end
            if out[le:le + 1] == "\n":
                le += 1
            if out[ls:start].strip():
                ls = start          # something else shares the line - keep it
                le = end
            out = out[:ls] + out[le:]
        else:
            out = out[:start] + repl + out[end:]
    return out, actions


def _fp_stem(name: str) -> str:
    """Footprint name without its .kicad_mod suffix (dots are legal in names)."""
    n = Path(name).name
    return n[:-len(".kicad_mod")] if n.endswith(".kicad_mod") else n


def fix_file(path: str | Path, *, dry_run: bool = False, **opts) -> dict:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    before = analyze(text, opts.get("min_gap", MIN_GAP),
                     opts.get("min_line_width", MIN_LINE_WIDTH))
    new, actions = sanitize(text, **opts)
    after = analyze(new, opts.get("min_gap", MIN_GAP),
                    opts.get("min_line_width", MIN_LINE_WIDTH))
    if new != text and not dry_run:
        p.write_text(new, encoding="utf-8")
    residue = [a for a in actions if a["action"].startswith("residue")]
    return {"footprint": p.stem, "file": str(p), "changed": new != text,
            "actions": actions, "residue": len(residue),
            "before": {"worst_gap_mm": before["worst_gap_mm"],
                       "unprintable_silk": before["unprintable_silk"],
                       "under_gap_silk": before["under_gap_silk"],
                       "plated_pegs": before["plated_pegs"]},
            "after": {"worst_gap_mm": after["worst_gap_mm"],
                      "unprintable_silk": after["unprintable_silk"],
                      "under_gap_silk": after["under_gap_silk"],
                      "plated_pegs": after["plated_pegs"]}}


def fix_lib(pretty: str | Path, *, dry_run: bool = False,
            names: list[str] | None = None, **opts) -> dict:
    d = Path(pretty)
    if not d.is_dir():
        raise FpFixError(f"not a .pretty directory: {d}")
    files = sorted(d.glob("*.kicad_mod"))
    if names is not None:
        # NOT Path().stem: footprint names carry dots ("...-LS9.3-BL") and stem
        # would strip ".3-BL" as an extension, silently skipping the footprint.
        want = {_fp_stem(n) for n in names}
        files = [f for f in files if _fp_stem(f.name) in want]
    rows = [fix_file(f, dry_run=dry_run, **opts) for f in files]
    return {"script": "fpfix", "lib": str(d), "dry_run": bool(dry_run),
            "footprints": len(rows),
            "changed": sum(1 for r in rows if r["changed"]),
            "actions": sum(len(r["actions"]) for r in rows),
            "residue": sum(r["residue"] for r in rows),
            "status": "residue" if any(r["residue"] for r in rows) else "pass",
            "results": rows}


# ------------------------------------------------------- real-DRC measurement

def scratch_drc(pretty: str | Path, names: list[str] | None = None,
                spacing: float = 30.0, timeout: int = 300) -> dict:
    """DRC one instance of each footprint on a bare board, alone.

    The only honest way to make a silk claim: geometry checkers and KiCad's DRC
    disagree (LEARNINGS 2026-07-28 CORRECTION - checkers saw 4 overlaps where
    DRC fired once). Returns {status, violations, counts, by_check, board}.
    """
    import subprocess
    import tempfile
    scripts = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(scripts))
    import kc  # noqa: PLC0415  (venv-side; the worker below is bundled python)
    from lib import env  # noqa: PLC0415

    cli = env.find_kicad_cli()
    if cli is None:
        raise FpFixError("no kicad-cli found (env.py)")
    bp = env.find_kicad_python(cli)
    if bp is None:
        raise FpFixError("KiCad bundled python not found (env.py)")

    with tempfile.TemporaryDirectory() as td:
        pcb = Path(td) / "fp_scratch.kicad_pcb"
        job = Path(td) / "job.json"
        job.write_text(json.dumps({"pretty": str(Path(pretty).resolve()),
                                   "names": names, "out": str(pcb),
                                   "spacing": spacing}), encoding="utf-8")
        cp = subprocess.run([str(bp), str(Path(__file__).with_name("fp_scratch.py")),
                             str(job)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
        try:
            res = json.loads((cp.stdout or "").strip().splitlines()[-1])
        except Exception as exc:  # noqa: BLE001
            raise FpFixError(f"scratch board worker failed: "
                             f"{(cp.stdout + cp.stderr)[-300:]}") from exc
        if res.get("status") != "ok":
            raise FpFixError(f"scratch board worker: {res.get('error')}")
        rep = kc.run_drc(cli, pcb, parity=False, all_track_errors=True)
    by_check: dict[str, int] = {}
    for v in rep["violations"]:
        by_check[v["check"]] = by_check.get(v["check"], 0) + 1
    return {"status": rep["status"], "counts": rep["counts"],
            "by_check": by_check, "violations": rep["violations"],
            "placed": res["placed"], "missing": res["missing"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", required=True, help="a .pretty directory")
    ap.add_argument("--only", nargs="+", help="limit to these footprint names")
    ap.add_argument("--min-gap", type=float, default=MIN_GAP,
                    help=f"silk-to-copper bar, mm (default {MIN_GAP})")
    ap.add_argument("--min-line-width", type=float, default=MIN_LINE_WIDTH,
                    help=f"printable silk floor, mm (default {MIN_LINE_WIDTH})")
    ap.add_argument("--no-promote", action="store_true",
                    help="delete unprintable silk instead of promoting the clear ones")
    ap.add_argument("--no-pegs", action="store_true", help="leave plated peg holes alone")
    ap.add_argument("--no-body-text", action="store_true",
                    help="leave hidden legend text alone")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-drc", action="store_true",
                    help="measure the result with real DRC on a scratch board")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        payload = fix_lib(args.lib, dry_run=args.dry_run, names=args.only,
                          min_gap=args.min_gap, min_line_width=args.min_line_width,
                          promote_silk=not args.no_promote,
                          fix_pegs=not args.no_pegs,
                          fix_body_text=not args.no_body_text)
        if args.verify_drc:
            drc = scratch_drc(args.lib, args.only)
            payload["drc"] = {"status": drc["status"], "counts": drc["counts"],
                              "by_check": drc["by_check"],
                              "placed": len(drc["placed"]), "missing": drc["missing"]}
            if drc["counts"]["total"]:
                payload["status"] = "residue"
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": "fpfix", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}, indent=1))
        return 2
    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if payload["status"] == "residue" else 0


if __name__ == "__main__":
    sys.exit(main())
