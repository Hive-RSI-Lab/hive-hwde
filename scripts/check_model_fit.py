"""check_model_fit.py - every footprint's 3D model sits on its own pads
(P8 verify suite).

The hit: EasyEDA's model of the HRO TYPE-C-31-M-12 USB-C (lite-main J1, as
lib_pull fetches it) sits 2.2 mm off its footprint: its shell legs and
locating pegs land beside the holes, its body hangs over the board edge the
wrong way, and the render, the STEP export and the enclosure fitted to that
STEP (hive-mech reads part heights and port positions from it) all show the
connector where it isn't. No gate looked: the model is not copper. fpfix's
body_flags read a pulled library footprint's VRML once, THT pins only, and
never the board.

The model is read as geometry (lib/modelgeom.py: VRML faces, STEP B-rep
faces) and placed by the footprint's own (model (offset) (scale) (rotate)),
in the footprint's frame. Its contact features are then laid against the
pads:
  leads  geometry in the seating band (z -0.1..+0.2 mm) that isn't the
         body: flat faces merged level by level into regions, and a region
         (or any other face) bigger than LEAD_AREA_X pads or longer than
         LEAD_SPAN_X pads is the body's underside, not a lead;
  pins   geometry wholly below the board (z < -0.5 mm): pins, shell legs and
         locating pegs, scored only at each hole's centre (HOLE_CORE), so a
         fat peg in a pin's hole is no better fit than the pin.
The fit is the pads' worth the features cover (each SMD pad's covered share,
each hole's centre), worked out for every slide of the model within
+-SEARCH_MM on a RES_MM raster at once, by cross-correlating the feature
raster with the pad weights; at the model's own turn and at +90/180/270 deg.
When some placement covers more than the model as placed, by GAIN_MIN pads
and GAIN_REL of its own score, and meets (MET_FRAC) no fewer pads, the model
is off, and the finding gives the (offset)/(rotate) of that placement (the
middle of its plateau nearest the model as placed). Symmetric parts can't
show a 180 deg turn (or a 90 deg one if square): only their marking differs.
Findings, one per footprint per kind, kind:
  model_offset       error: a slide of >= MIN_SHIFT_MM fits better
  model_rotation     error: a turn (and slide) fits better
  model_scale        error: (scale) is not 1 1 1 (a model is drawn to size)
  model_floats       error: the model's lowest point is above the board
                     (z > FLOAT_MM): its leads don't touch their pads
  model_in_board     error: the model reaches into the board's material
                     (z < -SINK_MM, above its bottom face) inside the
                     outline where the footprint has no hole: a Z offset
                     gone wrong, or a peg or mid-mount body with nowhere to
                     go. An edge-mount part's legs straddling the board
                     edge are outside the outline, so they pass.
  model_unmatched    warning: under half its pads are met and no slide or
                     turn fits better: a wrong model, or one tilted on X/Y
A model with no file on disk is check_model_paths' / render's concern, and a
hidden one is never drawn: both are skipped (counted in the report's facts).

The repair belongs in the footprint library (fix the (model) there and
update the board from it, so the next update doesn't bring the old one
back); `fit` in each finding is the (offset)/(rotate) to write.

CLI: --pcb board.kicad_pcb [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import sexpdata
from shapely import affinity
from shapely.geometry import box

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import kc  # noqa: E402
import modelgeom  # noqa: E402
from geom import _kid, _kids, _nums, _strs, _tok  # noqa: E402

SCRIPT = "check_model_fit"
SOURCE = SCRIPT
SEAT_Z = (-0.1, 0.2)     # mm: the seating band a lead's foot lies in
BELOW_Z = -0.5           # mm: wholly below this is a pin or peg in a hole
FEATURE_PAD = 0.02       # mm: features grow this much (a face edge-on to a pad)
LEAD_AREA_X = 2.0        # a seating face bigger than this x the largest pad is body
LEAD_SPAN_X = 1.5        # ... or longer than this x the longest pad side
SEARCH_MM = 5.0          # mm: the farthest slide tried
RES_MM = 0.05            # the raster's pixel
MET_FRAC = 0.05          # a pad this much covered by a feature is met
FLAT_MM = 0.01           # a face this flat in z is horizontal
HOLE_CORE = 0.25         # a hole scores over this share of its width
PLATEAU = 0.02           # pads' worth: scores this close tie
GAIN_MIN = 0.3           # pads' worth a slide or turn must win by ...
GAIN_REL = 0.15          # ... and this share of its own score
TURN_PENALTY_MM = 0.2    # mm: a turn must beat a plain slide by this much
MIN_SHIFT_MM = 0.2       # mm: a shorter slide is no finding
FLOAT_MM = 0.15          # mm: a model this far above the board floats
SINK_MM = 0.3            # mm: a model detail this deep in the board is noise
HOLE_SLACK_MM = 0.3      # a pin's corner may stand this far past its drill
UNIT_EPS = 1e-3


def _sval(x) -> str:
    return x.value() if isinstance(x, sexpdata.Symbol) else str(x)


def stock_dirs() -> list[Path]:
    """Where ${KICAD<n>_3DMODEL_DIR} points when the environment doesn't say:
    beside the pinned kicad-cli (HWDE_KICAD_CLI), the system's KiCad, then
    each KiCad install found (env.kicad_installs, the Hive's AppDir under
    the shared tools folder among them)."""
    out = []
    try:
        import env
        pin, _ = env.skill_env("KICAD_CLI")
        if pin:
            out += [r / "share" / "kicad" / "3dmodels"
                    for r in Path(pin).resolve().parents]
        out.append(Path("/usr/share/kicad/3dmodels"))
        out += [r / "share" / "kicad" / "3dmodels" for r in env.kicad_installs()]
    except Exception:  # noqa: BLE001 - a lookup that fails finds nothing
        pass
    return [d for d in out if d.is_dir()]


def resolver(pcb: Path):
    envv = dict(os.environ)
    stock = stock_dirs()
    if stock:
        for n in ("", "6", "7", "8", "9", "10", "11"):
            envv.setdefault(f"KICAD{n}_3DMODEL_DIR", str(stock[0]))
    return kc._model_resolver(pcb, envv)


# ------------------------------------------------------------ the board

def _xyz(node, key, dflt):
    k = _kid(node, key)
    x = _kid(k, "xyz") if k is not None else None
    v = _nums(x) if x is not None else []
    return tuple(v[:3]) if len(v) >= 3 else dflt


def _hidden(node) -> bool:
    h = _kid(node, "hide")
    if h is not None:
        return len(h) < 2 or _tok(h[1]) != "no"
    return any(not isinstance(t, list) and _tok(t) == "hide" for t in node[2:])


def _pads(fp, angle: float) -> list[tuple[str, bool, object]]:
    """(number, through, copper or hole polygon in the footprint's file
    frame) of every pad with copper or a hole."""
    out = []
    for pad in _kids(fp, "pad"):
        ps = _strs(pad)
        number = ps[0] if ps else ""
        toks = [_tok(t) for t in pad[1:4] if not isinstance(t, list)]
        through = any(t in ("thru_hole", "np_thru_hole") for t in toks)
        layers = " ".join(_strs(_kid(pad, "layers")) if _kid(pad, "layers")
                          is not None else [])
        if not through and ".Cu" not in layers:
            continue                       # a paste or mask aperture
        at = _nums(_kid(pad, "at")) if _kid(pad, "at") is not None else [0, 0]
        if through and _kid(pad, "drill") is not None:
            d = _nums(_kid(pad, "drill"))
            w, h = (d[0], d[1]) if len(d) >= 2 else ((d or [0])[0],) * 2
        else:
            sz = _nums(_kid(pad, "size")) if _kid(pad, "size") is not None else []
            w, h = (sz[0], sz[1]) if len(sz) >= 2 else (0.0, 0.0)
        if w <= 0 or h <= 0:
            continue
        rel = (at[2] if len(at) > 2 else 0.0) - angle
        poly = affinity.rotate(box(-w / 2, -h / 2, w / 2, h / 2), -rel,
                               origin=(0, 0))
        out.append((number, through, affinity.translate(poly, at[0], at[1])))
    return out


def footprints(pcb: Path):
    """(ref, (x, y), angle, back, pads, [(path, offset, scale, rotate)])
    for every footprint with a shown model."""
    tree = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    for fp in _kids(tree, "footprint"):
        ref = next((_strs(p)[1] for p in _kids(fp, "property")
                    if len(_strs(p)) >= 2 and _strs(p)[0] == "Reference"), "?")
        at = _nums(_kid(fp, "at")) if _kid(fp, "at") is not None else [0, 0]
        angle = at[2] if len(at) > 2 else 0.0
        layer = _kid(fp, "layer")
        back = bool(layer is not None and _strs(layer)
                    and _strs(layer)[0].startswith("B."))
        models = []
        for m in _kids(fp, "model"):
            if len(m) < 2:
                continue
            path = _sval(m[1])
            hidden = _hidden(m)
            models.append((path, _xyz(m, "offset", (0.0, 0.0, 0.0)),
                           _xyz(m, "scale", (1.0, 1.0, 1.0)),
                           _xyz(m, "rotate", (0.0, 0.0, 0.0)), hidden))
        if models:
            yield ref, (at[0], at[1]), angle, back, _pads(fp, angle), models


# ------------------------------------------------------------ the fit

def _features(faces, pads):
    """(lead boxes, pin boxes) as (x0, y0, x1, y1): the model's contact
    features (module doc). Flat faces in the seating band are merged level
    by level into regions first: EasyEDA cuts a body's underside into
    triangles, each smaller than a pad, and only the whole region shows it
    is the body: its box bigger than LEAD_AREA_X pads or longer than
    LEAD_SPAN_X pads (the thin ring of a body's rounded bottom edge)."""
    from shapely.geometry import MultiPoint
    from shapely.ops import unary_union
    smd = [p for _, t, p in pads if not t]
    big = max((p.area for p in smd), default=1.0) * LEAD_AREA_X
    span = max((max(p.bounds[2] - p.bounds[0], p.bounds[3] - p.bounds[1])
                for p in smd), default=1.0) * LEAD_SPAN_X

    def lead(b) -> bool:
        w, h = b[2] - b[0], b[3] - b[1]
        return w * h <= big and max(w, h) <= span

    leads, pins = [], []
    flat: dict[float, list] = {}
    for face in faces:
        zs = [p[2] for p in face]
        xs = [p[0] for p in face]
        ys = [p[1] for p in face]
        b = (min(xs) - FEATURE_PAD, min(ys) - FEATURE_PAD,
             max(xs) + FEATURE_PAD, max(ys) + FEATURE_PAD)
        if max(zs) < BELOW_Z:
            pins.append(b)
        elif SEAT_Z[0] <= min(zs) and max(zs) <= SEAT_Z[1]:
            if len(face) >= 3 and max(zs) - min(zs) < FLAT_MM:
                flat.setdefault(round(min(zs) / FLAT_MM), []).append(
                    MultiPoint([(x, y) for x, y, _ in face]).convex_hull
                    .buffer(FEATURE_PAD, join_style=2))
            elif lead(b):
                leads.append(b)
    for polys in flat.values():
        merged = unary_union(polys)
        for g in getattr(merged, "geoms", [merged]):
            if g.area > 0 and lead(g.bounds):
                leads.append(g.bounds)
    return leads, pins


class _Frame:
    """The raster the pads are drawn on: RES_MM pixels over the pads' box,
    and the same box grown by the search radius for the model's features.
    Pad p weighs 1/(its pixel count), so a pad the features cover whole
    scores 1 and the score of a placement is in pads' worth."""

    def __init__(self, pads):
        import numpy as np
        import shapely
        self.n = int(round(SEARCH_MM / RES_MM))
        x0 = min(p.bounds[0] for _, _, p in pads)
        y0 = min(p.bounds[1] for _, _, p in pads)
        x1 = max(p.bounds[2] for _, _, p in pads)
        y1 = max(p.bounds[3] for _, _, p in pads)
        self.ox = math.floor(x0 / RES_MM) * RES_MM
        self.oy = math.floor(y0 / RES_MM) * RES_MM
        self.w = int(math.ceil((x1 - self.ox) / RES_MM)) + 1
        self.h = int(math.ceil((y1 - self.oy) / RES_MM)) + 1
        xs = self.ox + (np.arange(self.w) + 0.5) * RES_MM
        ys = self.oy + (np.arange(self.h) + 0.5) * RES_MM
        gx, gy = np.meshgrid(xs, ys)
        self.weights = {False: np.zeros((self.h, self.w)),
                        True: np.zeros((self.h, self.w))}
        self.masks = []          # (number, through, row idx, col idx)
        for number, through, poly in pads:
            if through:      # a pin fits its hole or not: score its centre
                c = poly.centroid
                minx, miny, maxx, maxy = poly.bounds
                poly = c.buffer(max(RES_MM, HOLE_CORE * min(maxx - minx,
                                                            maxy - miny) / 2))
            m = shapely.contains_xy(poly, gx, gy)
            if not m.any():      # a pad under one pixel: its centre pixel
                c = poly.centroid
                m[min(self.h - 1, int((c.y - self.oy) / RES_MM)),
                  min(self.w - 1, int((c.x - self.ox) / RES_MM))] = True
            rows, cols = np.nonzero(m)
            self.weights[through][rows, cols] += 1.0 / len(rows)
            self.masks.append((number or "(unnumbered)", through, rows, cols))

    def raster(self, boxes):
        """The boxes drawn on the grown raster (n pixels more each side)."""
        import numpy as np
        n = self.n
        u = np.zeros((self.h + 2 * n, self.w + 2 * n), dtype=float)
        gx0 = self.ox - n * RES_MM
        gy0 = self.oy - n * RES_MM
        for bx0, by0, bx1, by1 in boxes:
            j0 = max(0, int(math.floor((bx0 - gx0) / RES_MM)))
            j1 = min(u.shape[1], max(j0 + 1, int(math.ceil((bx1 - gx0) / RES_MM))))
            i0 = max(0, int(math.floor((by0 - gy0) / RES_MM)))
            i1 = min(u.shape[0], max(i0 + 1, int(math.ceil((by1 - gy0) / RES_MM))))
            if j0 < u.shape[1] and i0 < u.shape[0] and j1 > 0 and i1 > 0:
                u[i0:i1, j0:j1] = 1.0
        return u


class _Fit:
    """How well a placed model's features cover its pads, for every slide
    of the model on the RES_MM grid within +-SEARCH_MM, by one
    cross-correlation of the feature raster with the pad weights."""

    def __init__(self, faces, pads, frame: _Frame):
        from scipy.signal import correlate
        leads, pins = _features(faces, pads)
        self.frame = frame
        self.u = {False: frame.raster(leads), True: frame.raster(pins)}
        self.usable = any((leads if not t else pins) for _, t, _ in pads)
        score = None
        for kind in (False, True):
            if not frame.weights[kind].any() or not self.u[kind].any():
                continue
            c = correlate(self.u[kind], frame.weights[kind], mode="valid",
                          method="fft")
            score = c if score is None else score + c
        n = frame.n
        self.score = score if score is not None else \
            __import__("numpy").zeros((2 * n + 1, 2 * n + 1))

    def at(self, i: int, j: int) -> float:
        """The score with the model slid (j, i) pixels (x, y)."""
        n = self.frame.n
        return float(self.score[n - i, n - j])

    def met(self, i: int = 0, j: int = 0) -> list[str]:
        """The pads a feature covers at least MET_FRAC of, slid (j, i)."""
        n = self.frame.n
        hit = []
        for number, through, rows, cols in self.frame.masks:
            cov = self.u[through][rows + n - i, cols + n - j].mean()
            if cov >= MET_FRAC:
                hit.append(number)
        return hit

    def best(self):
        """(score, i, j): the best slide, in pixels; on a plateau (within
        PLATEAU of the top) the middle of the part of it nearest no slide."""
        import numpy as np
        n = self.frame.n
        top = float(self.score.max())
        on = self.score >= top - PLATEAU - 1e-9
        cells = {(n - a, n - b) for a, b in zip(*np.nonzero(on))}
        start = min(cells, key=lambda c: (c[0] ** 2 + c[1] ** 2, c))
        comp, todo = {start}, [start]
        while todo:
            i, j = todo.pop()
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    c = (i + di, j + dj)
                    if c not in comp and c in cells:
                        comp.add(c)
                        todo.append(c)
        mi = sum(c[0] for c in comp) / len(comp)
        mj = sum(c[1] for c in comp) / len(comp)
        # the middle of a bent plateau may be off it: then its nearest cell
        mid = min(comp, key=lambda c: ((c[0] - mi) ** 2 + (c[1] - mj) ** 2, c))
        if abs(mid[0] - mi) <= 1 and abs(mid[1] - mj) <= 1:
            return self.at(*mid), mi, mj
        return self.at(*mid), mid[0], mid[1]


def _r(v: float, nd: int = 3) -> float:
    return round(v, nd) + 0.0   # never -0.0


def _better(a, b) -> bool:
    """Placement a = (score, turn, i, j, ...) beats b: a higher score by
    more than PLATEAU, else the shorter slide, a turn costing
    TURN_PENALTY_MM."""
    def cost(c):
        return math.hypot(c[2], c[3]) * RES_MM + (TURN_PENALTY_MM if c[1] else 0.0)
    if abs(a[0] - b[0]) > PLATEAU:
        return a[0] > b[0]
    return cost(a) < cost(b) - 1e-9


def fit_model(mesh, offset, scale, rotate, back, pads) -> list[dict]:
    """The findings (kind, severity, detail facts) for one placed model."""
    out = []
    if any(abs(s - 1.0) > UNIT_EPS for s in scale):
        out.append({"kind": "model_scale", "severity": "error",
                    "scale": list(scale),
                    "msg": f"(scale {' '.join(f'{s:g}' for s in scale)}) "
                           "stretches the model; a part's model is drawn to "
                           "size, so its scale is 1 1 1",
                    "fit": {"scale": [1, 1, 1]}})
    bx = modelgeom.placed_box(mesh, offset, scale, rotate, back)
    if bx[2] > FLOAT_MM:
        out.append({"kind": "model_floats", "severity": "error",
                    "z_min": _r(bx[2]),
                    "msg": f"the model's lowest point is {bx[2]:.2f} mm above "
                           "the board: it floats off its pads",
                    "fit": {"offset": [_r(offset[0]), _r(offset[1]),
                                       _r(offset[2] - bx[2])]}})
    if not pads:
        return out
    frame = _Frame(pads)
    fit = _Fit(modelgeom.place(mesh, offset, scale, rotate, back), pads, frame)
    if not fit.usable:
        return out
    s0, met0 = max(0.0, fit.at(0, 0)), fit.met()
    best = None   # (score, turn, i, j, fit)
    for turn in (0, 90, 180, 270):
        rot = (rotate[0], rotate[1], (rotate[2] + turn) % 360)
        f = fit if not turn else _Fit(
            modelgeom.place(mesh, offset, scale, rot, back), pads, frame)
        sc, i, j = f.best()
        cand = (sc, turn, i, j, f)
        if best is None or _better(cand, best):
            best = cand
    gain = best[0] - s0
    if gain < max(GAIN_MIN, GAIN_REL * best[0]) or (
            not best[1] and math.hypot(best[2], best[3]) * RES_MM
            < MIN_SHIFT_MM - 1e-9):
        best = None
    unmet = [n or "(unnumbered)" for n, _, _ in pads]
    for n in met0:
        unmet.remove(n)
    if best is None:
        if 2 * len(met0) < len(pads):
            out.append({"kind": "model_unmatched", "severity": "warning",
                        "met": len(met0), "pads": len(pads),
                        "unmet_pads": sorted(set(unmet)),
                        "msg": f"the model's leads and pins meet {len(met0)} of "
                               f"{len(pads)} pads and no shift or turn does "
                               "better: a wrong model, or one tilted on X/Y"})
        return out
    score, turn, i, j, f = best
    dx, dy = j * RES_MM, i * RES_MM
    count = len(f.met(round(i), round(j)))
    if count < len(met0):
        return out   # it covers some pads better by leaving others
    # the slide is in the file frame (Y down); the offset is in the model's
    # Y-up frame, which a back-side part's mirrored locals flip again
    new_off = [_r(offset[0] + dx, 2), _r(offset[1] + (dy if back else -dy), 2),
               _r(offset[2])]
    new_rot = [_r(rotate[0]), _r(rotate[1]), _r((rotate[2] + turn) % 360)]
    where = f"slid {math.hypot(dx, dy):.2f} mm (dx {dx:+.2f}, dy {dy:+.2f})"
    if turn:
        kind = "model_rotation"
        how = f"turned {turn} deg" + (f" and {where}" if (dx or dy) else "")
    else:
        kind, how = "model_offset", where
    out.append({"kind": kind, "severity": "error",
                "met": len(met0), "met_fit": count, "pads": len(pads),
                "cover": _r(s0), "cover_fit": _r(score),
                "unmet_pads": sorted(set(unmet)),
                "shift_mm": [_r(dx, 2), _r(dy, 2)], "turn_deg": turn,
                "msg": f"the model's leads and pins cover {s0:.2f} pads' worth "
                       f"of its {len(pads)} pads ({len(met0)} met) as placed; "
                       f"{how} they cover {score:.2f} ({count} met)",
                "fit": {"offset": new_off, "rotate": new_rot}})
    return out


def in_board(mesh, offset, scale, rotate, back, pads, region,
             thickness: float) -> dict | None:
    """The finding when the placed model reaches into the board's material
    (z between -SINK_MM and SINK_MM - thickness) inside `region` (the
    board, in the footprint's file frame) and outside every hole of the
    footprint, else None."""
    import numpy as np
    import shapely
    from shapely.ops import unary_union
    holes = unary_union([p for _, t, p in pads if t]).buffer(HOLE_SLACK_MM) \
        if any(t for _, t, _ in pads) else None
    f = modelgeom.transform(offset, scale, rotate, back)
    pts = np.array([q for face in mesh.faces for q in map(f, face)
                    if SINK_MM - thickness < q[2] < -SINK_MM])
    if not len(pts):
        return None
    inside = shapely.contains_xy(region, pts[:, 0], pts[:, 1])
    if holes is not None:
        inside &= ~shapely.contains_xy(holes, pts[:, 0], pts[:, 1])
    if not inside.any():
        return None
    hit = pts[inside]
    depth = float(-hit[:, 2].min())
    x, y = float(hit[:, 0].mean()), float(hit[:, 1].mean())
    return {"kind": "model_in_board", "severity": "error",
            "depth_mm": _r(depth), "at_local": [_r(x), _r(y)],
            "msg": f"the model reaches {depth:.2f} mm into the board around "
                   f"({x:.2f}, {y:.2f}) in the footprint, where the footprint "
                   "has no hole: its Z offset is wrong, or the part needs a "
                   "hole or cut-out there (a peg, a mid-mount body)"}


class _Board:
    """The board's outline (inner windows cut out) and thickness, read only
    when a model reaches below the board."""

    def __init__(self, pcb: Path):
        self.pcb = pcb
        self._geo = None

    def _load(self):
        if self._geo is None:
            import geom
            from shapely.ops import unary_union
            bg = geom.load_board(self.pcb)
            outline = bg.outline
            windows = [f for f in getattr(bg, "outline_faces", [])[1:]
                       if outline.contains(f.representative_point())]
            if windows:
                outline = outline.difference(unary_union(windows))
            self._geo = (outline.buffer(-0.05), float(bg.thickness or 1.6))
        return self._geo

    @property
    def thickness(self) -> float:
        return self._load()[1]

    def region(self, pos, angle: float):
        """The board in a footprint's file frame (placelib: abs = pos +
        R(-angle) local, so local = R(angle)(abs - pos))."""
        outline = self._load()[0]
        return affinity.rotate(affinity.translate(outline, -pos[0], -pos[1]),
                               angle, origin=(0, 0))


# ------------------------------------------------------------ the check

def check(pcb: Path) -> tuple[list[dict], dict]:
    resolve = resolver(pcb)
    facts = {"models": 0, "checked": 0, "hidden": 0, "missing": [],
             "unreadable": {}}
    cache: dict = {}
    board = _Board(pcb)
    viols = []
    for ref, pos, angle, back, pads, models in footprints(pcb):
        for path, offset, scale, rotate, hidden in models:
            facts["models"] += 1
            if hidden:
                facts["hidden"] += 1
                continue
            f = resolve(path)
            if not f.is_file():
                if path not in facts["missing"]:
                    facts["missing"].append(path)
                continue
            key = (str(f), offset, scale, rotate, back,
                   tuple((n, t, p.wkt) for n, t, p in pads))
            if key not in cache:
                try:
                    mesh = modelgeom.load(f)
                except (OSError, ValueError) as exc:
                    facts["unreadable"][path] = f"{type(exc).__name__}: {exc}"
                    cache[key] = None
                    continue
                cache[key] = (mesh, fit_model(mesh, offset, scale, rotate,
                                              back, pads)) \
                    if mesh is not None else (None, [])
            mesh, found = cache[key] or (None, None)
            if found is None:
                continue
            if mesh is not None and not any(
                    h["kind"] in ("model_offset", "model_rotation")
                    for h in found) and modelgeom.placed_box(
                    mesh, offset, scale, rotate, back)[2] < -SINK_MM:
                try:
                    region = board.region(pos, angle)
                except Exception as exc:  # noqa: BLE001 - no outline, no test
                    facts["no_outline"] = f"{type(exc).__name__}: {exc}"
                else:
                    h = in_board(mesh, offset, scale, rotate, back, pads,
                                 region, board.thickness)
                    found = found + ([h] if h else [])
            facts["checked"] += 1
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            for h in found:
                fit = " ".join(f"({k} (xyz {' '.join(f'{v:g}' for v in val)}))"
                               for k, val in h["fit"].items()) \
                    if h.get("fit") else ""
                fix = (f"set the footprint's (model ...) to {fit} in its "
                       "library and update the board from it") if fit else \
                    "check the model against the part's drawing"
                extra = {k: v for k, v in h.items()
                         if k not in ("kind", "severity", "msg")}
                viols.append(checklib.violation(
                    SCRIPT, h["severity"], pos, "B.Cu" if back else "F.Cu",
                    None, [ref], f"{ref}: 3D model {name}: {h['msg']}. "
                    f"Fix: {fix}", SOURCE, kind=h["kind"], model_path=path,
                    fix=fix, **extra))
    return viols, facts


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    viols, facts = check(pcb)
    return checklib.report(SCRIPT, pcb, viols, **facts), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
