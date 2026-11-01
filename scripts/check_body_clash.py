"""check_body_clash.py - the parts' 3D bodies fit together on the board
(P8 verify suite, #608).

The hit: hifi core's RCA jack has a side ear 11-16 mm above the board reaching
12 mm from its centre. Its hand-drawn courtyard stopped at 8 mm, so the
courtyard check passed while J4's ear ran 2.0 mm into banana socket J27 and
J5's ran 0.4 mm into J4. Every gate passed; the person saw it in a render.
Courtyards are 2D and only as good as whoever drew them; this reads the bodies.

Each footprint's shown models (VRML first, a STEP's .wrl twin before the STEP:
lib/modelgeom.py reads a STEP's curved faces only roughly) are placed in board
coordinates, with z = 0 at the top face and the back side below -thickness, so
a through-hole part's pin tails meet the parts on the far side. The surfaces
are rasterised on a shared lattice of VOX_MM (coarser for a very large part,
VOX_MAX_CELLS) and closed shells are filled, so a part wholly inside another
still overlaps it. A model that isn't closed (`open_shells` in the report; a
STEP's curved faces, read roughly) is filled by scanning along each axis instead.

Findings, one per pair or part, kind:
  body_clash        error: two bodies share material at least CLASH_MM thick.
  body_contact      warning: open shells cross but share under CLASH_MM, too thin
                    to measure, over at least 1 mm.
  body_tight        warning: two bodies both taller than TALL_MM stand under
                    TIGHT_MM apart. Through-hole parts lean as they're soldered
                    and a jack that touches its neighbour takes plug force.
  body_past_courtyard  warning: the model's silhouette above its seat stands
                    more than COURTYARD_SLACK_MM outside its own courtyard. This
                    is the cause, and placement can't see it. A connector's
                    overhang past the board edge doesn't count: that's its mouth.
  body_over_hole    error: a body stands in a mounting hole's screw column
                    (the drill's width at any height, a screw head's width up
                    to HEAD_HEIGHT_MM).
  body_off_board    warning: a part that isn't a connector has a body past the
                    outline by more than OFF_BOARD_MM.
  body_unjudged     warning, one per board: connectors (J, CN, X, P) with no
                    readable model, so nothing here judged them.

A part with no model can't be judged, and the report's not_judged says which.
Deliberate nesting (a battery in its holder, a part under a shield) goes in
constraints `mechanical.allow_body_overlap`: [{refs: [A, B], why: "..."}].
This check doesn't judge whether the model is the real part: that's
check_model_fit (model on its pads) and the drawing.

CLI: --pcb board.kicad_pcb [--constraints constraints.json]
     [--model-dir DIR ...] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sexpdata
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import check_courtyard  # noqa: E402
import check_model_fit as cmf  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402
import kc  # noqa: E402
import modelgeom  # noqa: E402
import placelib  # noqa: E402
from geom import _kid, _kids, _nums, _strs  # noqa: E402

SCRIPT = "check_body_clash"
VOX_MM = 0.1               # the lattice; finer sees nothing a model is good to
VOX_MAX_CELLS = 30_000_000  # a part bigger than this drops to 2x, 4x the pitch
CLASH_MM = 0.25            # thinner shared material is two faces touching
TALL_MM = 5.0              # both bodies this tall: body_tight applies
TIGHT_MM = 0.3             # hand-placed tall parts closer than this
COURTYARD_SLACK_MM = 0.25  # a lead's toe or a model's rounding
SEAT_MM = 0.3              # z above the part's own side that is leads, not body
OFF_BOARD_MM = 0.5         # a body this far past the outline
HEAD_RADIUS_X = 1.8        # screw head width, as a share of the drill
HEAD_HEIGHT_MM = 3.0       # how high a screw head and driver bit need it clear
BOARD_BAND_MM = 0.05       # inside the board's material nothing is a clash
EDGE_REFS = re.compile(r"^(J|CN|X|P|USB|ANT|SW|S)\d", re.I)
CONNECTOR_REFS = re.compile(r"^(J|CN|X|P)\d", re.I)
SKIP_UNJUDGED = re.compile(r"^(TP|FID|H|MH|LOGO|NT|REF|G)\d*$", re.I)


def _triangles(faces) -> np.ndarray:
    """Nx3x3 float32: each face (a vertex loop) as a fan."""
    tris = []
    for face in faces:
        for k in range(1, len(face) - 1):
            tris.append((face[0], face[k], face[k + 1]))
    return np.asarray(tris, dtype=np.float32).reshape(-1, 3, 3)


@dataclass
class Body:
    """A part's bodies in board coordinates (mm, Y down, Z up from the top
    face). `tris` is dropped after rasterising; `cells` holds the filled solid
    from lattice cell `org`, at pitch `vox`."""
    ref: str
    back: bool
    pos: tuple
    tris: np.ndarray
    box: tuple
    sealed: bool = True
    vox: float = VOX_MM
    org: tuple = (0, 0, 0)
    cells: np.ndarray | None = None

    def height(self, thickness: float) -> float:
        """Standing height from the part's own side."""
        return (-thickness - self.box[2]) if self.back else self.box[5]


def _pitch(box) -> float:
    """VOX_MM, doubled until this part's lattice fits VOX_MAX_CELLS."""
    vox = VOX_MM
    size = [box[i + 3] - box[i] for i in range(3)]
    while math.prod(s / vox + 5 for s in size) > VOX_MAX_CELLS:
        vox *= 2
    return vox


def _mark(tris: np.ndarray, grid: np.ndarray, org, vox: float) -> None:
    """Set every cell the triangles cross: scan each along its dominant
    normal, so one column holds one depth and neighbours differ by at most a
    cell. A column counts when its centre is within half a cell of the
    triangle, so meeting triangles leave no leak; a face reads a cell fat at
    most, so an overlap's extent is right to about 0.1 mm and two faces touching
    share less than CLASH_MM."""
    p = tris.astype(np.float64) - np.array(org, dtype=np.float64) * vox
    p /= vox
    # corners always count: a degenerate triangle has no normal to scan along
    top = np.array(grid.shape) - 1
    c = np.clip(np.floor(p.reshape(-1, 3)).astype(np.int32), 0, top)
    grid[c[:, 0], c[:, 1], c[:, 2]] = True
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    keep = (n * n).sum(axis=1) > 1e-12
    p, n = p[keep], n[keep]
    axis = np.abs(n).argmax(axis=1)
    for w in range(3):
        sel = axis == w
        if sel.any():
            u, v = [k for k in range(3) if k != w]
            _scan(p[sel], n[sel], (u, v, w), grid, top)


def _scan(p, n, axes, grid, top, chunk=4_000_000) -> None:
    """_mark's columns for triangles whose normal is mostly along axes[2]."""
    u, v, w = axes
    margin = 0.5
    u0 = np.floor(p[:, :, u].min(axis=1) - margin).astype(np.int64)
    v0 = np.floor(p[:, :, v].min(axis=1) - margin).astype(np.int64)
    nu = np.floor(p[:, :, u].max(axis=1) + margin).astype(np.int64) - u0 + 1
    nv = np.floor(p[:, :, v].max(axis=1) + margin).astype(np.int64) - v0 + 1
    cost = np.cumsum(nu * nv)
    start = 0
    while start < len(p):
        base = cost[start - 1] if start else 0
        end = int(np.searchsorted(cost, base + chunk, side="right"))
        end = max(end, start + 1)
        _scan_chunk(p[start:end], n[start:end], axes, grid, top,
                    u0[start:end], v0[start:end], nu[start:end], nv[start:end],
                    margin)
        start = end


def _scan_chunk(p, n, axes, grid, top, u0, v0, nu, nv, margin) -> None:
    u, v, w = axes
    counts = nu * nv
    t = np.repeat(np.arange(len(p)), counts)
    first = np.repeat(np.cumsum(counts) - counts, counts)
    k = np.arange(len(t)) - first
    iu = u0[t] + k % nu[t]
    iv = v0[t] + k // nu[t]
    cu, cv = iu + 0.5, iv + 0.5
    pu, pv = p[:, :, u], p[:, :, v]
    area = (pu[:, 1] - pu[:, 0]) * (pv[:, 2] - pv[:, 0]) \
        - (pu[:, 2] - pu[:, 0]) * (pv[:, 1] - pv[:, 0])
    sign = np.where(area >= 0, 1.0, -1.0)[t]
    inside = np.ones(len(t), dtype=bool)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        eu, ev = pu[:, b] - pu[:, a], pv[:, b] - pv[:, a]
        ln = np.hypot(eu, ev)
        ln[ln == 0] = 1.0
        d = ((eu / ln)[t] * (cv - pv[:, a][t])
             - (ev / ln)[t] * (cu - pu[:, a][t])) * sign
        inside &= d >= -margin
    t, iu, iv, cu, cv = t[inside], iu[inside], iv[inside], cu[inside], cv[inside]
    pw = p[:, :, w]
    depth = pw[:, 0][t] - (n[:, u][t] * (cu - pu[:, 0][t])
                           + n[:, v][t] * (cv - pv[:, 0][t])) / n[:, w][t]
    depth = np.clip(depth, pw.min(axis=1)[t], pw.max(axis=1)[t])
    iw = np.floor(depth).astype(np.int64)
    ok = ((iu >= 0) & (iu <= top[u]) & (iv >= 0) & (iv <= top[v])
          & (iw >= 0) & (iw <= top[w]))
    idx = [None, None, None]
    idx[u], idx[v], idx[w] = iu[ok], iv[ok], iw[ok]
    grid[idx[0], idx[1], idx[2]] = True


def rasterise(body: Body, vox: float | None = None) -> None:
    """Fill body.cells at `vox` (or its own pitch): whatever the outside can't
    reach through face-joined empty cells is inside. The grid has an empty
    rim, so its corner is outside."""
    vox = vox or _pitch(body.box)
    if body.cells is not None and body.vox == vox:
        return
    lo = [math.floor(body.box[i] / vox) - 2 for i in range(3)]
    hi = [math.floor(body.box[i + 3] / vox) + 3 for i in range(3)]
    grid = np.zeros([h - l for l, h in zip(lo, hi)], dtype=bool)
    _mark(body.tris, grid, lo, vox)
    surface = int(grid.sum())
    labels, _ = ndimage.label(~grid)
    grid = labels != labels[0, 0, 0]
    # a thin part has no inside at this pitch: only a real volume says "open"
    size = sorted(body.box[i + 3] - body.box[i] for i in range(3))
    body.sealed = int(grid.sum()) > surface or size[0] < 4 * vox
    if not body.sealed:
        grid = _scan_fill(grid)
    body.cells, body.org, body.vox = grid, tuple(lo), vox


def _scan_fill(grid: np.ndarray) -> np.ndarray:
    """A shell with gaps (lib/modelgeom reads a STEP's curved faces as their
    vertex loops): a cell is inside when surface lies both ways along all three
    axes. A notch or the space under an ear has an open side, so it stays out."""
    out = np.ones_like(grid)
    for axis in range(3):
        out &= np.logical_or.accumulate(grid, axis=axis)
        out &= np.flip(np.logical_or.accumulate(np.flip(grid, axis), axis=axis), axis)
    return out | grid


def overlap(a: Body, b: Body):
    """(shared cells, lattice cell of their first corner), or None."""
    lo = [max(a.org[i], b.org[i]) for i in range(3)]
    hi = [min(a.org[i] + a.cells.shape[i], b.org[i] + b.cells.shape[i])
          for i in range(3)]
    if any(h <= l for l, h in zip(lo, hi)):
        return None
    sa = tuple(slice(l - a.org[i], h - a.org[i])
               for i, (l, h) in enumerate(zip(lo, hi)))
    sb = tuple(slice(l - b.org[i], h - b.org[i])
               for i, (l, h) in enumerate(zip(lo, hi)))
    return a.cells[sa] & b.cells[sb], lo


def thickness_mm(shared: np.ndarray, vox: float) -> float:
    """The thickest ball of shared material, across (its inscribed width)."""
    pad = np.pad(shared, 1)
    return round(float(2 * ndimage.distance_transform_edt(pad).max() - 1) * vox, 2)


def gap_mm(a: Body, b: Body, reach: float) -> float | None:
    """The surfaces' nearest approach within `reach` mm, None if farther."""
    vox = a.vox
    n = int(reach / vox) + 2
    lo = [max(a.org[i], b.org[i]) - n for i in range(3)]
    hi = [min(a.org[i] + a.cells.shape[i], b.org[i] + b.cells.shape[i]) + n
          for i in range(3)]
    if any(h <= l for l, h in zip(lo, hi)):
        return None

    def crop(body: Body):
        out = np.zeros([h - l for l, h in zip(lo, hi)], dtype=bool)
        src, dst = [], []
        for i in range(3):
            s0 = max(lo[i], body.org[i])
            s1 = min(hi[i], body.org[i] + body.cells.shape[i])
            if s1 <= s0:
                return out
            src.append(slice(s0 - body.org[i], s1 - body.org[i]))
            dst.append(slice(s0 - lo[i], s1 - lo[i]))
        out[tuple(dst)] = body.cells[tuple(src)]
        return out

    ca, cb = crop(a), crop(b)
    if not ca.any() or not cb.any():
        return None
    d = ndimage.distance_transform_edt(~ca)[cb].min()
    gap = float(d) * vox     # cell centres: right to about one cell
    return round(gap, 2) if gap <= reach else None


def silhouette(body: Body, z_lo: float, z_hi: float) -> np.ndarray:
    """Mx2 cell centres of the body between two world heights."""
    lo = int(math.floor(z_lo / body.vox)) - body.org[2]
    hi = int(math.ceil(z_hi / body.vox)) - body.org[2]
    lo, hi = max(lo, 0), min(hi, body.cells.shape[2])
    if hi <= lo:
        return np.empty((0, 2))
    cols = body.cells[:, :, lo:hi].any(axis=2)
    ix, iy = np.nonzero(cols)
    return np.column_stack(((ix + body.org[0] + 0.5) * body.vox,
                            (iy + body.org[1] + 0.5) * body.vox))


def _past(points: np.ndarray, region):
    """(farthest distance past region, that point), or (0, None)."""
    import shapely
    if not len(points):
        return 0.0, None
    out = points[~shapely.contains_xy(region, points[:, 0], points[:, 1])]
    if not len(out):
        return 0.0, None
    d = shapely.distance(region, shapely.points(out))
    k = int(np.argmax(d))
    return float(d[k]), out[k]


# ------------------------------------------------------------ the board

def bodies(pcb: Path, thickness: float, model_dirs=()):
    """(Body per footprint with a readable shown model, facts). A path that
    doesn't resolve is looked up by name in the workspace's lib/*.3dshapes, as
    render does, then in `model_dirs`."""
    resolve = cmf.resolver(pcb)
    libs = [*kc.model_libs(pcb.resolve().parent), *map(Path, model_dirs)]
    facts = {"models_missing": {}, "unreadable": {}, "hidden_only": []}
    out, mesh_tris = [], {}
    for ref, pos, angle, back, _pads, models in cmf.footprints(pcb):
        parts, shown = [], False
        for path, offset, scale, rotate, hidden in models:
            if hidden:
                continue
            shown = True
            f = resolve(path)
            twin = f.with_suffix(".wrl")
            if f.suffix.lower() in (".step", ".stp") and twin.is_file():
                f = twin
            if not f.is_file():
                f = kc.model_lib_file(path, libs) or \
                    kc.model_lib_file(str(Path(path).with_suffix(".wrl")), libs) \
                    or f
            if not f.is_file():
                facts["models_missing"][ref] = path
                continue
            key = (str(f), offset, scale, rotate, back)
            if key not in mesh_tris:
                try:
                    mesh = modelgeom.load(f)
                except (OSError, ValueError) as exc:
                    facts["unreadable"][ref] = f"{type(exc).__name__}: {exc}"
                    mesh = None
                mesh_tris[key] = None if mesh is None else _triangles(
                    modelgeom.place(mesh, offset, scale, rotate, back))
            if mesh_tris[key] is not None and len(mesh_tris[key]):
                parts.append(mesh_tris[key])
        if not parts:
            if models and not shown:
                facts["hidden_only"].append(ref)
            continue
        t = np.concatenate(parts).astype(np.float64)
        a = math.radians(angle)
        c, s = math.cos(a), math.sin(a)
        x = pos[0] + t[..., 0] * c + t[..., 1] * s
        y = pos[1] - t[..., 0] * s + t[..., 1] * c
        z = (-thickness - t[..., 2]) if back else t[..., 2]
        w = np.stack([x, y, z], axis=-1).astype(np.float32)
        box = tuple(float(v) for v in (*w.reshape(-1, 3).min(0),
                                       *w.reshape(-1, 3).max(0)))
        out.append(Body(ref, back, pos, w, box))
    return out, facts


def holes(pcb: Path) -> list[tuple]:
    """(ref, x, y, drill) of each footprint that is only a mounting hole: all
    its holes unconnected, a footprint name or reference that says so."""
    tree = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    found = []
    for fp in _kids(tree, "footprint"):
        name = _strs(fp)[0] if _strs(fp) else ""
        ref = next((_strs(p)[1] for p in _kids(fp, "property")
                    if len(_strs(p)) >= 2 and _strs(p)[0] == "Reference"), "?")
        if not (re.search(r"mount|standoff|screw.?hole", name, re.I)
                or re.match(r"^(H|MH)\d+$", ref)):
            continue
        at = _nums(_kid(fp, "at"))
        best = 0.0
        for pad in _kids(fp, "pad"):
            d = _kid(pad, "drill")
            if d is not None and _nums(d):
                best = max(best, _nums(d)[0])
        if best > 0:
            found.append((ref, at[0], at[1], best))
    return found


def allowed_pairs(constraints: dict | None) -> dict[frozenset, str]:
    out = {}
    mech = (constraints or {}).get("mechanical") or {}
    for e in mech.get("allow_body_overlap") or []:
        refs, why = e.get("refs") or [], e.get("why") or ""
        if len(refs) != 2 or not why:
            raise checklib.CheckError(
                "mechanical.allow_body_overlap entries need two refs and a why:"
                f" {e}")
        out[frozenset(refs)] = why
    return out


def check(pcb: Path, constraints: dict | None = None, model_dirs=()):
    bg = geom.load_board(pcb)
    thickness = float(bg.thickness or 1.6)
    allow = allowed_pairs(constraints)
    parts, facts = bodies(pcb, thickness, model_dirs)
    viols: list[dict] = []
    place = placelib.PlaceModel(pcb)

    def tall(b: Body) -> bool:
        return b.height(thickness) >= TALL_MM

    used_allow = {}

    # pairs whose boxes come within reach
    order = sorted(parts, key=lambda b: b.box[0])
    pairs = []
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if b.box[0] > a.box[3] + TIGHT_MM:
                break
            if all(a.box[k] <= b.box[k + 3] + TIGHT_MM
                   and b.box[k] <= a.box[k + 3] + TIGHT_MM for k in (1, 2)):
                pairs.append((a, b))
    for b in parts:
        rasterise(b)
    for a, b in pairs:
        if a.vox != b.vox:             # the finer part follows the coarser
            fine, coarse = (a, b) if a.vox < b.vox else (b, a)
            keep = (fine.cells, fine.org, fine.vox, fine.sealed)
            rasterise(fine, coarse.vox)
            _pair(a, b, thickness, allow, used_allow, tall, viols)
            fine.cells, fine.org, fine.vox, fine.sealed = keep
        else:
            _pair(a, b, thickness, allow, used_allow, tall, viols)

    # own courtyard, the outline, the holes
    import shapely
    outline = bg.outline
    screw = holes(pcb)
    for b in parts:
        lo, hi = (b.box[2], -thickness - SEAT_MM) if b.back \
            else (SEAT_MM, b.box[5])
        pts = silhouette(b, lo, hi)
        fp = place.footprints.get(b.ref)
        cy = check_courtyard.courtyard_abs(fp) if fp is not None else None
        if cy is not None:
            own = pts
            if outline is not None and EDGE_REFS.match(b.ref) and len(pts):
                on = shapely.contains_xy(outline, pts[:, 0], pts[:, 1])
                own = pts[on]
            d, at = _past(own, cy)
            if d > COURTYARD_SLACK_MM + b.vox:
                viols.append(checklib.violation(
                    SCRIPT, "warning", (float(at[0]), float(at[1])),
                    "B.Cu" if b.back else "F.Cu", None, [b.ref],
                    f"{b.ref}: the 3D body stands {d:.2f} mm outside its own "
                    "courtyard, so nothing placing a part beside it can see "
                    "that much of it. Fix: draw the courtyard to the part's "
                    "real outline in the library, or fix the model if the "
                    "drawing disagrees",
                    SCRIPT, kind="body_past_courtyard", past_mm=round(d, 2)))
        if outline is not None and not EDGE_REFS.match(b.ref):
            d, at = _past(pts, outline)
            if d > OFF_BOARD_MM:
                viols.append(checklib.violation(
                    SCRIPT, "warning", (float(at[0]), float(at[1])),
                    "B.Cu" if b.back else "F.Cu", None, [b.ref],
                    f"{b.ref}: the 3D body overhangs the board edge by "
                    f"{d:.2f} mm and isn't a connector",
                    SCRIPT, kind="body_off_board", past_mm=round(d, 2)))
        for ref, x, y, drill in screw:
            if ref == b.ref:
                continue
            r = drill * HEAD_RADIUS_X / 2
            if (x + r < b.box[0] or x - r > b.box[3]
                    or y + r < b.box[1] or y - r > b.box[4]):
                continue
            c0 = silhouette(b, -1e3, 1e3)          # the drill, any height
            d0 = np.hypot(c0[:, 0] - x, c0[:, 1] - y) if len(c0) else np.empty(0)
            near = (-thickness - HEAD_HEIGHT_MM, -thickness) if b.back \
                else (0.0, HEAD_HEIGHT_MM)
            c1 = silhouette(b, *near)              # the head, up to its height
            d1 = np.hypot(c1[:, 0] - x, c1[:, 1] - y) if len(c1) else np.empty(0)
            if (len(d0) and d0.min() < drill / 2) or (len(d1) and d1.min() < r):
                viols.append(checklib.violation(
                    SCRIPT, "error", (x, y), "B.Cu" if b.back else "F.Cu",
                    None, [ref, b.ref],
                    f"{b.ref}'s 3D body stands in mounting hole {ref}'s screw "
                    f"column (a {drill:g} mm hole wants {r * 2:.1f} mm clear "
                    f"up to {HEAD_HEIGHT_MM:g} mm). Fix: move the part or the "
                    "hole", SCRIPT, kind="body_over_hole"))

    judged = {b.ref for b in parts}
    no_model = [r for r, f in sorted(place.footprints.items())
                if r not in judged and not SKIP_UNJUDGED.match(r)
                and "board_only" not in f.attrs and f.pads]
    connectors = [r for r in no_model if CONNECTOR_REFS.match(r)]
    if connectors:
        viols.append(checklib.violation(
            SCRIPT, "warning", None, None, None, connectors,
            f"{' '.join(connectors)}: no readable 3D model, so these connectors'"
            " bodies were checked against nothing, and a connector is where a "
            "body clash costs most. Fix: add the models",
            SCRIPT, kind="body_unjudged"))
    facts = {k: v for k, v in facts.items() if v}
    return viols, {
        **facts, "bodies": len(parts), "pairs_compared": len(pairs),
        "open_shells": sorted(b.ref for b in parts if not b.sealed),
        "allowed_overlaps": used_allow,
        "tallest_mm": round(max((b.height(thickness) for b in parts),
                                default=0.0), 2),
        "not_judged": no_model,
    }


def _pair(a, b, thickness, allow, used_allow, tall, viols) -> None:
    got = overlap(a, b)
    key = frozenset((a.ref, b.ref))
    layer = "B.Cu" if a.back and b.back else "F.Cu"
    if got is not None and got[0].any():
        shared, lo = got
        vox = a.vox
        # the board's own material is no clash: pins pass through holes there
        k0 = int(math.ceil((-thickness + BOARD_BAND_MM) / vox)) - lo[2]
        k1 = int(math.floor(-BOARD_BAND_MM / vox)) - lo[2]
        if k1 > k0:
            shared = shared.copy()
            shared[:, :, max(k0, 0):max(k1, 0)] = False
        if shared.any():
            depth = thickness_mm(shared, vox)
            cells = np.argwhere(shared)
            c0 = (cells.min(0) + lo) * vox
            c1 = (cells.max(0) + lo + 1) * vox
            span = (c1 - c0).round(2).tolist()
            where = {"x": [round(float(c0[0]), 2), round(float(c1[0]), 2)],
                     "y": [round(float(c0[1]), 2), round(float(c1[1]), 2)],
                     "z": [round(float(c0[2]), 2), round(float(c1[2]), 2)]}
            pos = (float(c0[0] + c1[0]) / 2, float(c0[1] + c1[1]) / 2)
            if depth >= max(CLASH_MM, 2.5 * vox):
                if key in allow:
                    used_allow[f"{a.ref}+{b.ref}"] = allow[key]
                    return
                viols.append(checklib.violation(
                    SCRIPT, "error", pos, layer, None, [a.ref, b.ref],
                    f"{a.ref} and {b.ref}: the 3D bodies share material "
                    f"{depth:.2f} mm thick at x {where['x']}, y {where['y']}, "
                    f"z {where['z']} above the board. Check the drawings; if "
                    "the models are right the parts don't fit. Fix: move a "
                    "part. Deliberate nesting goes in constraints "
                    "mechanical.allow_body_overlap with a why",
                    SCRIPT, kind="body_clash", depth_mm=depth, overlap=where))
                return
            if (not a.sealed or not b.sealed) and sorted(span)[1] >= 1.0 \
                    and key not in allow:
                viols.append(checklib.violation(
                    SCRIPT, "warning", pos, layer, None, [a.ref, b.ref],
                    f"{a.ref} and {b.ref}: surfaces of a model that isn't a "
                    f"closed solid cross at x {where['x']}, y {where['y']}, "
                    f"z {where['z']}; the depth can't be measured. Look at it",
                    SCRIPT, kind="body_contact", overlap=where))
                return
    if tall(a) and tall(b) and a.back == b.back and key not in allow:
        gap = gap_mm(a, b, TIGHT_MM)
        if gap is not None and gap < TIGHT_MM:
            pos = ((a.pos[0] + b.pos[0]) / 2, (a.pos[1] + b.pos[1]) / 2)
            viols.append(checklib.violation(
                SCRIPT, "warning", pos, layer, None, [a.ref, b.ref],
                f"{a.ref} and {b.ref}: two tall bodies stand {gap:.2f} mm "
                f"apart (under {TIGHT_MM} mm). Through-hole parts lean as "
                "they're soldered, and a jack touching its neighbour takes "
                "plug force", SCRIPT, kind="body_tight", gap_mm=gap))


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints")
    ap.add_argument("--model-dir", action="append", default=[],
                    help="more *.3dshapes dirs to look a missing model up in")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    cons = checklib.load_json(args.constraints, "constraints") \
        if args.constraints else None
    viols, facts = check(pcb, cons, args.model_dir)
    return checklib.report(SCRIPT, pcb, viols, **facts,
                           nothing_to_judge=None if facts.get("bodies") else
                           "no 3D model could be read"), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
