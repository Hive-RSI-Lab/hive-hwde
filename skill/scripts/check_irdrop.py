"""check_irdrop.py - DC IR-drop + sheet current density per power net (layout sim).

2.5D resistor-grid FDM (the KiPIDA/OpenROAD-psm formulation class - approach
only, no code shared; KiPIDA is AGPL and was used strictly as approach
validation). Per constrained power net:

 - each layer's net copper (geom.net_copper: tracks+pads+vias+zone fill) is
   rasterized onto ONE common grid frame (all layers share origin/pitch so via
   cells align); every in-copper cell is a node, adjacent cells couple with the
   layer's sheet conductance (1 square per cell edge, so g = 1/Rs independent
   of cell size), Rs = rho_cu / t_cu with rho_cu = 1.72e-8 Ohm-m
   (1 oz / 0.035 mm -> ~0.49 mOhm/sq);
 - layers couple through via resistors: cylinder R = rho*L/A on the drill
   diameter with an ASSUMED 0.025 mm plating wall (annulus area), L = stackup
   height between the coupled coppers; the barrel conductance is split across
   all cell pairs inside the via radius (kills the single-cell injection
   spike). Multi-layer (THT) pads are tied the same way with an assumed drill
   of 0.6 x min(pad size) - geom carries no pad drill;
 - sparse SPD system (graph Laplacian, Dirichlet nodes eliminated) solved with
   scipy.sparse.linalg.spsolve.

INJECTION MODEL (v1, worst-case by default - document to consumers):
 - If the constraints entry carries "source_ref", the source is that
   component's pads (Dirichlet V=0 across all their cells; a THT source pad is
   clamped on every layer it touches). Optional "sinks": [{ref, current_a}]
   attributes return currents per component (split equally over that ref's
   pads, then equally over each pad's cells); overrides are IGNORED in this
   explicit mode (explicit attribution wins).
 - Otherwise WORST-CASE UNIFORM: the source is the component with the largest
   net pad copper area (the "largest pad cluster" - normally the regulator or
   connector), the full current_a returns uniformly across all other
   components' net pads (uniform PER PAD), honoring check_current's per-region
   "overrides" semantics: sink pads whose center falls in an override region
   collectively draw that region's current_a (split equally inside the
   region), the remainder spreads uniformly over the rest. Per-branch
   attribution beyond this is a declared v1 limit.

GRID SIZING (documented floor/ceiling):
 - target cell = min_feature / 8 (>= 8 cells across the narrowest neck, per
   the negative-result guidance: no published rule, 8-10 cells ~5-10% local
   accuracy); min_feature = the net's narrowest track width (fallback:
   smallest pad dimension, then 0.5 mm);
 - floor CELL_MIN_MM = 0.015, ceiling CELL_MAX_MM = 0.4;
 - cell-count ceiling: if the estimated in-copper cell count exceeds
   BASE_CELL_CAP the cell grows to fit (the reported cells_across_min_feature
   fact then exposes the shortfall and the Richardson gate judges it);
 - an entry may pin "cell_mm" explicitly (floor/ceiling still clamp).

RICHARDSON GATE: solve, then double the grid once (cell/2) and report both
resistances + the relative delta (fact richardson_delta); when the doubled
grid would exceed REFINE_CELL_CAP cells the pair is taken against a 2x
COARSER grid instead (fact refine: "coarsened") so runtime stays bounded.
delta > 10% -> warning kind grid_unconverged. Reported numbers always come
from the finer grid of the pair.

Outputs (checklib report): per net - total resistance source->worst sink
(worst sink mean drop / injected current), worst-node IR drop mV, sheet
current density maxima (A/mm) with [x, y] + layer, neck locations. Violations
only when the entry carries optional "irdrop_mv_max" (kind irdrop_excess) or
"jmax_a_per_mm" (kind current_density_excess); otherwise facts-only advisory.
Entries with "pdn": false are skipped (same opt-out as check_pdn).

CLI: --pcb board.kicad_pcb --constraints constraints.json [--out report.json]
     exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import shapely
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import spsolve
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "check_irdrop"

RHO_CU_OHM_M = 1.72e-8     # contract C1 (Bogatin/EDN band, do not use 0.7 mOhm/sq)
PLATING_MM = 0.025         # assumed via barrel plating wall (JLC-class 20-25 um)
THT_DRILL_FRACTION = 0.6   # assumed THT drill = 0.6 x min(pad size) (no drill in geom)
NECK_CELLS = 8.0           # target cells across the narrowest feature
CELL_MIN_MM = 0.015        # documented floor
CELL_MAX_MM = 0.4          # documented ceiling
BASE_CELL_CAP = 250_000    # in-copper cell budget for the base grid
REFINE_CELL_CAP = 800_000  # doubling allowed only below this (else coarsen)
RICHARDSON_WARN = 0.10     # grid_unconverged threshold
NECK_MIN_SEP_MM = 1.5      # reported neck maxima are at least this far apart
NECK_KEEP = 5              # top-N reported necks


def sheet_res_ohm_sq(cu_mm: float) -> float:
    """Sheet resistance (Ohm/square) of copper cu_mm thick."""
    return RHO_CU_OHM_M / (cu_mm * 1e-3)


def via_res_ohm(drill_mm: float, span_mm: float,
                plating_mm: float = PLATING_MM) -> float:
    """Plated-barrel resistance: rho * L / A on the annulus drill->drill+wall."""
    r = max(drill_mm, 0.05) / 2.0
    area_mm2 = math.pi * ((r + plating_mm) ** 2 - r ** 2)
    return RHO_CU_OHM_M * (span_mm * 1e-3) / (area_mm2 * 1e-6)


# ============================================================ raster grid

class NetGrid:
    """All layers of one net rasterized on a single common frame."""

    def __init__(self, bg: geom.BoardGeom, net: str, layers: list[str],
                 cell: float):
        self.bg = bg
        self.net = net
        self.layers = layers
        self.cell = cell
        polys = {l: bg.net_copper(net, l) for l in layers}
        bounds = [polys[l].bounds for l in layers if not polys[l].is_empty]
        if not bounds:
            raise CheckError(f"net {net!r} has no copper to solve")
        minx = min(b[0] for b in bounds) - cell
        miny = min(b[1] for b in bounds) - cell
        maxx = max(b[2] for b in bounds) + cell
        maxy = max(b[3] for b in bounds) + cell
        self.x0, self.y0 = minx, miny
        self.nx = max(int(math.ceil((maxx - minx) / cell)), 1)
        self.ny = max(int(math.ceil((maxy - miny) / cell)), 1)
        self.xs = minx + (np.arange(self.nx) + 0.5) * cell
        self.ys = miny + (np.arange(self.ny) + 0.5) * cell
        self.mask: dict[str, np.ndarray] = {
            l: self._raster(polys[l]) for l in layers}
        self.ids: dict[str, np.ndarray] = {}
        n = 0
        for l in layers:
            ids = np.full((self.ny, self.nx), -1, dtype=np.int64)
            m = self.mask[l]
            ids[m] = n + np.arange(int(m.sum()))
            n += int(m.sum())
            self.ids[l] = ids
        self.n_nodes = n
        self._trees: dict[str, tuple[cKDTree, np.ndarray]] = {}

    def _raster(self, poly) -> np.ndarray:
        m = np.zeros((self.ny, self.nx), dtype=bool)
        if poly.is_empty:
            return m
        minx, miny, maxx, maxy = poly.bounds
        ix = np.nonzero((self.xs >= minx) & (self.xs <= maxx))[0]
        iy = np.nonzero((self.ys >= miny) & (self.ys <= maxy))[0]
        if not len(ix) or not len(iy):
            return m
        shapely.prepare(poly)
        xx, yy = np.meshgrid(self.xs[ix], self.ys[iy])
        hits = shapely.contains_xy(poly, xx.ravel(), yy.ravel())
        m[np.ix_(iy, ix)] = hits.reshape(len(iy), len(ix))
        return m

    def cell_center(self, i: int, j: int) -> tuple[float, float]:
        return (float(self.xs[j]), float(self.ys[i]))

    def disk_cells(self, layer: str, cx: float, cy: float, r: float):
        """(i, j) index arrays of in-copper cells with center within r."""
        h = self.cell
        j0 = max(int((cx - r - self.x0) / h) - 1, 0)
        j1 = min(int((cx + r - self.x0) / h) + 2, self.nx)
        i0 = max(int((cy - r - self.y0) / h) - 1, 0)
        i1 = min(int((cy + r - self.y0) / h) + 2, self.ny)
        if j0 >= j1 or i0 >= i1:
            return np.empty(0, int), np.empty(0, int)
        sub = self.mask[layer][i0:i1, j0:j1]
        xx, yy = np.meshgrid(self.xs[j0:j1], self.ys[i0:i1])
        near = ((xx - cx) ** 2 + (yy - cy) ** 2 <= r * r) & sub
        ii, jj = np.nonzero(near)
        return ii + i0, jj + j0

    def poly_cells(self, layer: str, poly):
        """Nodes of in-copper cells whose center lies inside poly."""
        minx, miny, maxx, maxy = poly.bounds
        ix = np.nonzero((self.xs >= minx) & (self.xs <= maxx))[0]
        iy = np.nonzero((self.ys >= miny) & (self.ys <= maxy))[0]
        if not len(ix) or not len(iy):
            return np.empty(0, dtype=np.int64)
        shapely.prepare(poly)
        xx, yy = np.meshgrid(self.xs[ix], self.ys[iy])
        hits = shapely.contains_xy(poly, xx.ravel(), yy.ravel())
        hits = hits.reshape(len(iy), len(ix))
        sub = self.mask[layer][np.ix_(iy, ix)] & hits
        ids = self.ids[layer][np.ix_(iy, ix)][sub]
        return ids

    def nearest_node(self, layers: list[str], cx: float, cy: float,
                     max_dist: float):
        """Nearest in-copper node to (cx, cy) on any given layer, or None."""
        best = None
        for l in layers:
            if l not in self.mask:
                continue
            if l not in self._trees:
                ii, jj = np.nonzero(self.mask[l])
                if not len(ii):
                    continue
                pts = np.column_stack([self.xs[jj], self.ys[ii]])
                self._trees[l] = (cKDTree(pts), self.ids[l][ii, jj])
            tree, ids = self._trees[l]
            d, k = tree.query([cx, cy])
            if d <= max_dist and (best is None or d < best[0]):
                best = (float(d), int(ids[k]))
        return None if best is None else best[1]


# ============================================================ injection plan

def pad_groups(bg: geom.BoardGeom, net: str) -> dict[str, list]:
    groups: dict[str, list] = {}
    for p in bg.pads_of(net):
        groups.setdefault(p.ref, []).append(p)
    return groups


def pick_source_ref(groups: dict[str, list]) -> str:
    """Worst-case source proxy: component with the largest net pad area."""
    return max(sorted(groups),
               key=lambda r: sum(p.poly.area for p in groups[r]))


def sink_currents(entry: dict, groups: dict[str, list], source_ref: str):
    """[(ref, pad, amps)] per sink pad (uniform-per-pad + overrides, or the
    explicit sinks list when present)."""
    out: list[tuple[str, object, float]] = []
    if entry.get("sinks"):
        for s in entry["sinks"]:
            ref = s.get("ref")
            if ref not in groups:
                raise CheckError(f"sink ref {ref!r} has no pads on the net")
            amps = float(s["current_a"]) / len(groups[ref])
            out.extend((ref, p, amps) for p in groups[ref])
        return out
    pads = [(r, p) for r in sorted(groups) if r != source_ref
            for p in groups[r]]
    if not pads:
        raise CheckError("no sink pads (single-component net)")
    total = float(entry["current_a"])
    assigned: dict[int, float] = {}
    remaining = list(range(len(pads)))
    for ov in entry.get("overrides", []):
        near = ov.get("near")
        if not near:
            continue
        r = float(ov.get("radius_mm", 2.0))
        idxs = [i for i in remaining
                if math.hypot(pads[i][1].center[0] - near[0],
                              pads[i][1].center[1] - near[1]) <= r]
        if not idxs:
            continue
        share = float(ov["current_a"]) / len(idxs)
        for i in idxs:
            assigned[i] = share
        remaining = [i for i in remaining if i not in idxs]
    rest = max(0.0, total - sum(assigned.values()))
    for i in remaining:
        assigned[i] = rest / len(remaining)
    return [(pads[i][0], pads[i][1], assigned[i]) for i in range(len(pads))]


# ============================================================ system assembly

def _grid_edges(grid: NetGrid, rows, cols, vals):
    for l in grid.layers:
        g = 1.0 / sheet_res_ohm_sq(grid.bg.stackup.copper_thickness[l])
        m = grid.mask[l]
        ids = grid.ids[l]
        h = m[:, :-1] & m[:, 1:]
        rows.append(ids[:, :-1][h])
        cols.append(ids[:, 1:][h])
        vals.append(np.full(int(h.sum()), g))
        v = m[:-1, :] & m[1:, :]
        rows.append(ids[:-1, :][v])
        cols.append(ids[1:, :][v])
        vals.append(np.full(int(v.sum()), g))


def _barrel_edges(grid: NetGrid, cx, cy, radius, drill, span_layers,
                  rows, cols, vals) -> bool:
    """Couple consecutive layer pairs of one barrel; True if fully linked."""
    bg = grid.bg
    ok = True
    for la, lb in zip(span_layers, span_layers[1:]):
        span = bg.stackup.height_between(la, lb)
        g_total = 1.0 / via_res_ohm(drill, max(span, 1e-3))
        ia, ja = grid.disk_cells(la, cx, cy, radius)
        ib, jb = grid.disk_cells(lb, cx, cy, radius)
        pa = set(zip(ia.tolist(), ja.tolist()))
        pb = set(zip(ib.tolist(), jb.tolist()))
        common = sorted(pa & pb)
        if common:
            ii = np.array([c[0] for c in common], dtype=int)
            jj = np.array([c[1] for c in common], dtype=int)
            rows.append(grid.ids[la][ii, jj])
            cols.append(grid.ids[lb][ii, jj])
            vals.append(np.full(len(common), g_total / len(common)))
            continue
        reach = radius + 2.0 * grid.cell
        na = grid.nearest_node([la], cx, cy, reach)
        nb = grid.nearest_node([lb], cx, cy, reach)
        if na is not None and nb is not None and na != nb:
            rows.append(np.array([na]))
            cols.append(np.array([nb]))
            vals.append(np.array([g_total]))
        else:
            ok = False
    return ok


def assemble(grid: NetGrid):
    """Adjacency matrix (conductances) + via bookkeeping."""
    bg, net = grid.bg, grid.net
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    vals: list[np.ndarray] = []
    _grid_edges(grid, rows, cols, vals)
    used = skipped = 0
    for v in bg.vias_of(net):
        span = [l for l in bg.copper_layers
                if l in v.layers and l in grid.mask]
        if len(span) < 2:
            continue
        if _barrel_edges(grid, v.at[0], v.at[1], v.diameter / 2.0,
                         v.drill or v.diameter / 2.0, span,
                         rows, cols, vals):
            used += 1
        else:
            skipped += 1
    for p in bg.pads_of(net):
        span = [l for l in bg.copper_layers
                if l in p.layers and l in grid.mask]
        if len(span) < 2:
            continue
        drill = THT_DRILL_FRACTION * min(p.size)
        if _barrel_edges(grid, p.center[0], p.center[1],
                         max(p.size) / 2.0, drill, span, rows, cols, vals):
            used += 1
        else:
            skipped += 1
    if rows:
        r = np.concatenate(rows)
        c = np.concatenate(cols)
        g = np.concatenate(vals)
    else:
        r = c = np.empty(0, int)
        g = np.empty(0)
    n = grid.n_nodes
    adj = sparse.coo_matrix((np.concatenate([g, g]),
                             (np.concatenate([r, c]),
                              np.concatenate([c, r]))), shape=(n, n)).tocsr()
    return adj, used, skipped


# ============================================================ solve one grid

def solve_grid(bg: geom.BoardGeom, entry: dict, net: str, layers: list[str],
               cell: float):
    """Build + solve the resistor grid at one cell size. Returns a dict of
    raw results (voltages folded into summary numbers, no arrays)."""
    grid = NetGrid(bg, net, layers, cell)
    adj, vias_used, vias_skipped = assemble(grid)

    groups = pad_groups(bg, net)
    if not groups:
        raise CheckError(f"net {net!r} has no component pads")
    source_ref = entry.get("source_ref")
    source_mode = "constraints"
    if source_ref:
        if source_ref not in groups:
            raise CheckError(f"source_ref {source_ref!r} has no pads on {net!r}")
    else:
        source_ref = pick_source_ref(groups)
        source_mode = "largest_pad_area"

    def pad_nodes(p) -> np.ndarray:
        pools = [grid.poly_cells(l, p.poly) for l in p.layers
                 if l in grid.mask]
        pool = (np.unique(np.concatenate(pools))
                if pools and sum(len(x) for x in pools) else
                np.empty(0, dtype=np.int64))
        if len(pool):
            return pool
        reach = max(p.size) / 2.0 + 3.0 * cell
        near = grid.nearest_node([l for l in p.layers if l in grid.mask],
                                 p.center[0], p.center[1], reach)
        return (np.array([near], dtype=np.int64) if near is not None
                else np.empty(0, dtype=np.int64))

    src_nodes = (np.unique(np.concatenate(
        [pad_nodes(p) for p in groups[source_ref]] or
        [np.empty(0, dtype=np.int64)])))
    if not len(src_nodes):
        raise CheckError(f"source {source_ref!r} pads land on no copper cells")

    # connected components: only what the source can reach is solvable
    n_comp, labels = connected_components(adj, directed=False)
    keep_labels = set(labels[src_nodes].tolist())
    kept = np.isin(labels, sorted(keep_labels))

    b = np.zeros(grid.n_nodes)
    warnings: list[dict] = []
    injected = 0.0
    sink_pad_count = 0
    sink_drop_nodes: dict[str, list[np.ndarray]] = {}
    for ref, pad, amps in sink_currents(entry, groups, source_ref):
        if amps <= 0:
            continue
        nodes = pad_nodes(pad)
        nodes = nodes[kept[nodes]] if len(nodes) else nodes
        if not len(nodes):
            warnings.append(violation(
                SCRIPT, "warning", pad.center, pad.layers[0], net, [ref],
                f"{net} sink pad {ref} pad {pad.number} at "
                f"({pad.center[0]:.2f}, {pad.center[1]:.2f}) is not connected "
                f"to the source copper on the solver grid (open or "
                f"rasterization artifact); its {amps:.3f} A was dropped",
                SCRIPT, kind="sink_disconnected"))
            continue
        b[nodes] -= amps / len(nodes)
        injected += amps
        sink_pad_count += 1
        sink_drop_nodes.setdefault(ref, []).append(nodes)
    if injected <= 0:
        raise CheckError(
            f"all sink pads on {net!r} are disconnected from the source "
            "copper on the solver grid; nothing to solve")

    is_src = np.zeros(grid.n_nodes, dtype=bool)
    is_src[src_nodes] = True
    unknown = np.nonzero(kept & ~is_src)[0]
    deg = np.asarray(adj.sum(axis=1)).ravel()
    lap = (sparse.diags(deg) - adj).tocsr()
    lap_uu = lap[unknown][:, unknown].tocsc()
    v_u = spsolve(lap_uu, b[unknown])
    v = np.zeros(grid.n_nodes)
    v[unknown] = v_u
    drop = -v  # volts below the source

    sinks = []
    for ref in sorted(sink_drop_nodes):
        nodes = np.concatenate(sink_drop_nodes[ref])
        sinks.append((ref, float(drop[nodes].mean())))
    worst_ref, worst_sink_v = max(sinks, key=lambda t: t[1])
    worst_node = int(np.argmax(np.where(kept, drop, -np.inf)))

    jmax, jmax_pos, jmax_layer, necks = current_density(grid, v)

    return {
        "cell": cell,
        "cells": grid.n_nodes,
        "kept_cells": int(kept.sum()),
        "source_ref": source_ref,
        "source_mode": source_mode,
        "injected_a": injected,
        "sink_pads": sink_pad_count,
        "resistance_ohm": worst_sink_v / injected,
        "worst_sink": {"ref": worst_ref, "drop_v": worst_sink_v},
        "worst_drop_v": float(drop[worst_node]),
        "jmax": jmax, "jmax_pos": jmax_pos, "jmax_layer": jmax_layer,
        "necks": necks,
        "vias_used": vias_used, "vias_skipped": vias_skipped,
        "warnings": warnings,
    }


def current_density(grid: NetGrid, v: np.ndarray):
    """Sheet current density |K| (A/mm) per cell from the solved voltages.
    Returns (jmax, [x, y], layer, top-neck list). K on the edge between two
    cells is the edge current / cell size; a cell's K averages its incident
    edge currents per axis, so interior necks are exact and boundary cells
    read slightly low (documented; necks are interior by construction)."""
    h = grid.cell
    jmax, jmax_pos, jmax_layer = 0.0, None, None
    cands: list[tuple[float, tuple[float, float], str]] = []
    for l in grid.layers:
        m = grid.mask[l]
        if not m.any():
            continue
        g = 1.0 / sheet_res_ohm_sq(grid.bg.stackup.copper_thickness[l])
        vg = np.zeros(m.shape)
        vg[m] = v[grid.ids[l][m]]
        ex = (m[:, :-1] & m[:, 1:])
        ix = np.where(ex, g * (vg[:, :-1] - vg[:, 1:]), 0.0)
        ey = (m[:-1, :] & m[1:, :])
        iy = np.where(ey, g * (vg[:-1, :] - vg[1:, :]), 0.0)
        jx = np.zeros(m.shape)
        jx[:, :-1] += 0.5 * ix
        jx[:, 1:] += 0.5 * ix
        jy = np.zeros(m.shape)
        jy[:-1, :] += 0.5 * iy
        jy[1:, :] += 0.5 * iy
        jj = np.hypot(jx, jy) / h
        jj[~m] = 0.0
        peak = float(jj.max())
        if peak > jmax:
            k = int(np.argmax(jj))
            i, j = divmod(k, grid.nx)
            jmax = peak
            jmax_pos = grid.cell_center(i, j)
            jmax_layer = l
        ci, cj = np.nonzero(jj >= 0.3 * peak) if peak > 0 else \
            (np.empty(0, int), np.empty(0, int))
        vals = jj[ci, cj]
        order = np.argsort(-vals, kind="stable")[:2000]
        cands.extend((float(vals[o]),
                      grid.cell_center(int(ci[o]), int(cj[o])), l)
                     for o in order)
    necks: list[dict] = []
    cands.sort(key=lambda t: (-t[0], t[1], t[2]))
    for val, pos, layer in cands:
        if val < 0.3 * jmax or len(necks) >= NECK_KEEP:
            break
        if all(n["layer"] != layer or
               math.hypot(pos[0] - n["pos"][0], pos[1] - n["pos"][1])
               >= NECK_MIN_SEP_MM for n in necks):
            necks.append({"a_per_mm": checklib.rnd(val),
                          "pos": [checklib.rnd(pos[0]), checklib.rnd(pos[1])],
                          "layer": layer})
    return jmax, jmax_pos, jmax_layer, necks


# ============================================================ per-net check

def pick_cell_mm(bg: geom.BoardGeom, net: str, layers: list[str],
                 entry: dict):
    """(cell_mm, min_feature_mm) per the documented sizing rules."""
    tracks = bg.tracks_of(net)
    if tracks:
        feat = min(t.width for t in tracks)
    else:
        pads = bg.pads_of(net)
        feat = min(min(p.size) for p in pads) if pads else 0.5
    if entry.get("cell_mm"):
        h = min(max(float(entry["cell_mm"]), CELL_MIN_MM), CELL_MAX_MM)
        return h, feat
    h = min(max(feat / NECK_CELLS, CELL_MIN_MM), CELL_MAX_MM)
    area = sum(bg.net_area(net, l) for l in layers)
    if area / (h * h) > BASE_CELL_CAP:
        h = math.sqrt(area / BASE_CELL_CAP)
    return h, feat


def check_net(bg: geom.BoardGeom, entry: dict):
    net = entry.get("net")
    if not net:
        raise CheckError('power entry without "net"')
    if net not in bg.nets:
        raise CheckError(f"power net {net!r} not on board "
                         f"(nets: {sorted(n for n in bg.nets if n)})")
    if "current_a" not in entry:
        raise CheckError(f'power entry {net!r} without "current_a"')
    layers = [l for l in bg.copper_layers if bg.net_area(net, l) > 0]
    if not layers:
        raise CheckError(f"net {net!r} has no copper on any layer")

    cell, feat = pick_cell_mm(bg, net, layers, entry)
    base = solve_grid(bg, entry, net, layers, cell)
    if 4 * base["cells"] <= REFINE_CELL_CAP:
        other = solve_grid(bg, entry, net, layers, cell / 2.0)
        fine, refine = other, "doubled"
    else:
        other = solve_grid(bg, entry, net, layers, cell * 2.0)
        fine, refine = base, "coarsened"
    coarse = base if fine is not base else other
    r_fine = fine["resistance_ohm"]
    r_coarse = coarse["resistance_ohm"]
    delta = abs(r_fine - r_coarse) / max(abs(r_fine), 1e-12)

    violations = list(fine["warnings"])
    if delta > RICHARDSON_WARN:
        violations.append(violation(
            SCRIPT, "warning", None, None, net, [],
            f"{net} IR-drop grid not converged: source->worst-sink resistance "
            f"moved {delta:.1%} between {coarse['cell']:.4f} and "
            f"{fine['cell']:.4f} mm grids "
            f"({r_coarse * 1e3:.3f} -> {r_fine * 1e3:.3f} mOhm)", SCRIPT,
            kind="grid_unconverged", richardson_delta=checklib.rnd(delta)))

    worst_mv = fine["worst_drop_v"] * 1e3
    limit_mv = entry.get("irdrop_mv_max")
    if limit_mv is not None and worst_mv > float(limit_mv):
        violations.append(violation(
            SCRIPT, "error", fine["jmax_pos"], fine["jmax_layer"], net,
            [fine["worst_sink"]["ref"]],
            f"{net} worst IR drop {worst_mv:.1f} mV at "
            f"{fine['injected_a']:.2f} A exceeds irdrop_mv_max "
            f"{float(limit_mv):.1f} mV (worst sink "
            f"{fine['worst_sink']['ref']})", SCRIPT, kind="irdrop_excess",
            drop_mv=checklib.rnd(worst_mv), limit_mv=float(limit_mv)))
    limit_j = entry.get("jmax_a_per_mm")
    if limit_j is not None and fine["jmax"] > float(limit_j):
        violations.append(violation(
            SCRIPT, "error", fine["jmax_pos"], fine["jmax_layer"], net, [],
            f"{net} sheet current density {fine['jmax']:.2f} A/mm at "
            f"({fine['jmax_pos'][0]:.2f}, {fine['jmax_pos'][1]:.2f}) on "
            f"{fine['jmax_layer']} exceeds jmax_a_per_mm "
            f"{float(limit_j):.2f}", SCRIPT, kind="current_density_excess",
            a_per_mm=checklib.rnd(fine["jmax"]),
            limit_a_per_mm=float(limit_j)))

    facts = {
        "net": net,
        "requested_a": float(entry["current_a"]),
        "injected_a": checklib.rnd(fine["injected_a"]),
        "source_ref": fine["source_ref"],
        "source_mode": fine["source_mode"],
        "sink_pads": fine["sink_pads"],
        "layers": layers,
        "min_feature_mm": checklib.rnd(feat),
        "grid": {
            "cell_mm": checklib.rnd(fine["cell"], 5),
            "cells": fine["cells"],
            "cells_across_min_feature": checklib.rnd(feat / fine["cell"], 2),
            "refine": refine,
            "cell_mm_pair": checklib.rnd(coarse["cell"], 5),
            "cells_pair": coarse["cells"],
        },
        "resistance_mohm": checklib.rnd(r_fine * 1e3),
        "resistance_pair_mohm": [checklib.rnd(r_fine * 1e3),
                                 checklib.rnd(r_coarse * 1e3)],
        "richardson_delta": checklib.rnd(delta),
        "worst_drop_mv": checklib.rnd(worst_mv),
        "worst_sink": {"ref": fine["worst_sink"]["ref"],
                       "drop_mv": checklib.rnd(
                           fine["worst_sink"]["drop_v"] * 1e3)},
        "jmax": {"a_per_mm": checklib.rnd(fine["jmax"]),
                 "pos": [checklib.rnd(fine["jmax_pos"][0]),
                         checklib.rnd(fine["jmax_pos"][1])]
                 if fine["jmax_pos"] else None,
                 "layer": fine["jmax_layer"]},
        "necks": fine["necks"],
        "vias": {"used": fine["vias_used"], "skipped": fine["vias_skipped"]},
    }
    return violations, facts


# ============================================================ CLI

def run(argv=None):
    ap = argparse.ArgumentParser(
        description="DC IR-drop + sheet current density per power net "
                    "(2.5D resistor-grid FDM).")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--constraints", required=True,
                    help="constraints.json with a power list")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    cons = checklib.load_json(args.constraints, "constraints")
    entries = cons.get("power", [])
    bg = geom.load_board(Path(args.pcb))
    bg.assert_fresh()

    violations: list[dict] = []
    checked: list[dict] = []
    for entry in entries:
        if entry.get("pdn") is False:
            checked.append({"net": entry.get("net"),
                            "skipped": "pdn:false (width-only power entry)"})
            continue
        if "current_a" in entry and float(entry["current_a"]) <= 0:
            checked.append({"net": entry.get("net"),
                            "skipped": "current_a <= 0 (placeholder entry; "
                                       "nothing to solve)"})
            continue
        vs, facts = check_net(bg, entry)
        violations.extend(vs)
        checked.append(facts)

    payload = checklib.report(SCRIPT, args.pcb, violations, checked=checked,
                              stackup_assumed=bg.stackup.assumed,
                              model={
                                  "kind": "2.5D resistor-grid FDM",
                                  "rho_cu_ohm_m": RHO_CU_OHM_M,
                                  "via_plating_mm": PLATING_MM,
                                  "injection": "worst-case uniform per pad "
                                               "unless source_ref/sinks given",
                              })
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
