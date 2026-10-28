"""geom.py - net-indexed, per-layer shapely geometry from a .kicad_pcb.

The single geometry source for the verification suite (S4/S5). Parses a KiCad
board's s-expressions (no SWIG, no IPC - pure venv) into shapely primitives so
that every check reasons about the SAME copper the fabricator will see.

All coordinates are millimetres (KiCad 6+ .kicad_pcb stores mm natively); areas
are therefore mm^2 and lengths mm. No unit conversion happens anywhere except
the pcbnew round-trip oracle used only by the S3 tests.

--------------------------------------------------------------------- public API
    load_board(path)               -> BoardGeom          (cached by path+mtime)
    BoardGeom.from_file(path)       -> BoardGeom          (uncached)

  BoardGeom:
    .copper_layers                  ordered [F.Cu, In1.Cu, ..., B.Cu] top->bottom
    .stackup                        Stackup (layer order, dielectric h, epsilon_r)
    .outline                        shapely Polygon of Edge.Cuts (largest face)
    .outline_faces                  every closed Edge.Cuts face, largest first
                                    (>1 = interior window / second board)
    .outline_items                  {gr_rect|gr_line|gr_arc|...: count} on
                                    Edge.Cuts, counted before the first-match
                                    return - the only view of what else is there
    .outline_arc_radii              exact radius of each Edge.Cuts arc
    .nets                           set of net names carrying copper

    .tracks_of(net=None, layer=None)    -> list[Track]
    .vias_of(net=None, layer=None)      -> list[Via]     (layer = spans that layer)
    .pads_of(net=None, layer=None, ref=None) -> list[Pad]
    .zones_of(net=None, layer=None)     -> list[Zone]    (copper zones only)
    .rule_areas                         keepout areas [{name, layers, outline}]
                                        (never copper, never "unfilled")

    .net_copper(net, layer)         -> (Multi)Polygon: union of ALL copper
                                       (tracks+pads+vias+zone fill) of net on layer
    .zone_fill(net, layer)          -> (Multi)Polygon: union of net's zone fill
    .layer_copper(layer, net=None, exclude=None) -> union of copper on a layer
    .net_area(net, layer)           -> float mm^2   (== net_copper(...).area)
    .net_area_by_layer(net)         -> {layer: mm^2}

    .adjacent_copper(layer)         -> (above|None, below|None)  copper neighbours
    .layers_with_zone(net)          -> [layers where net has a filled zone]

    .fill_status()                  -> {zone_id: bool filled}
    .unfilled_zones()               -> [Zone] with an outline but no fill
    .assert_fresh(refill=False)     raise StaleFillError on unfilled (or, with
                                    refill=True, on fills that differ from a fresh
                                    `kicad-cli drc --refill-zones` via the S0 path)

Flipped (back-side) footprints need no special handling anywhere: pcbnew bakes
the mirror into the stored file (pad locals mirrored, angles negated, layers
renamed to B.*), so the front-side transform applies to every footprint
(S3-verified against a SWIG-flipped board; LEARNINGS [geometry]).

Design notes are in PROGRESS.md (S3) and LEARNINGS.md [geometry].
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Iterable, Optional

import sexpdata
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, unary_union

UNITS = "mm"
# shapely buffer smoothness (quarter-circle segments). 24 keeps via/round-pad
# area error under ~0.1% while staying fast on the ~130-via RF board.
_QUAD_SEGS = 24
# FR4 relative permittivity used when the board carries no (stackup) block.
_FR4_ER = 4.5
# Typical finished-copper thicknesses (mm): 1 oz outer, 0.5 oz inner.
_CU_OUTER = 0.035
_CU_INNER = 0.0152


class GeomError(RuntimeError):
    """A board could not be parsed into usable geometry."""


class StaleFillError(GeomError):
    """Zones are unfilled or their fill is stale (see assert_fresh)."""


# ============================================================ s-expr helpers
# sexpdata yields nested lists; bare tokens are sexpdata.Symbol, quoted tokens
# are str, numbers are int/float. These helpers walk that tree by node head.

def _tok(x):
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def _is_node(n) -> bool:
    return isinstance(n, list) and bool(n)


def _head(n):
    return _tok(n[0]) if _is_node(n) else None


def _kids(n, name: str) -> list:
    return [c for c in n[1:] if _is_node(c) and _head(c) == name]


def _kid(n, name: str):
    for c in n[1:]:
        if _is_node(c) and _head(c) == name:
            return c
    return None


def _nums(n) -> list[float]:
    """Positional numeric args of a node, e.g. (at 1 2 90) -> [1.0, 2.0, 90.0]."""
    return [float(x) for x in n[1:] if isinstance(x, (int, float))]


def _strs(n) -> list[str]:
    return [x for x in n[1:] if isinstance(x, str)]


def _uuid_of(node) -> "Optional[str]":
    """(uuid "...") / legacy (tstamp ...) child of a board item, else None."""
    for key in ("uuid", "tstamp"):
        u = _kid(node, key)
        if u is not None and len(u) > 1:
            return str(_tok(u[1]))
    return None


def _pts(node) -> list[tuple[float, float]]:
    """Coordinates from a (pts (xy x y) ...) node."""
    out = []
    for xy in _kids(node, "xy"):
        v = _nums(xy)
        if len(v) >= 2:
            out.append((v[0], v[1]))
    return out


def _rot(x: float, y: float, deg: float) -> tuple[float, float]:
    """Rotate (x,y) about the origin by `deg` using the standard CCW matrix."""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (c * x - s * y, s * x + c * y)


# ============================================================ data model

@dataclass(frozen=True)
class Track:
    net: str
    layer: str
    width: float
    shape: LineString  # centerline
    # KiCad item uuid (T8): lets board_update/route_edit name this exact item
    # for removal. None on synthetic boards without (uuid ...) tokens.
    uuid: Optional[str] = None

    @cached_property
    def poly(self) -> Polygon:
        return self.shape.buffer(self.width / 2.0, quad_segs=_QUAD_SEGS,
                                 cap_style="round", join_style="round")

    @property
    def length(self) -> float:
        return self.shape.length


@dataclass(frozen=True)
class Via:
    net: str
    at: tuple[float, float]
    diameter: float
    drill: float
    layers: tuple[str, ...]  # copper layers spanned (inclusive)
    uuid: Optional[str] = None  # see Track.uuid

    @cached_property
    def poly(self) -> Polygon:
        return Point(self.at).buffer(self.diameter / 2.0, quad_segs=_QUAD_SEGS)

    def spans(self, layer: str) -> bool:
        return layer in self.layers


@dataclass(frozen=True)
class Pad:
    ref: str
    number: str
    net: Optional[str]
    shape: str            # rect | roundrect | circle | oval | (trapezoid/custom)
    size: tuple[float, float]
    center: tuple[float, float]
    angle: float          # absolute board-frame degrees
    rratio: float
    layers: tuple[str, ...]  # copper layers occupied
    # Drill geometry (T6, P7A-4): (w, h) mm - circle drills store (d, d),
    # `(drill oval w h)` stores (w, h); None for SMD pads. drill_offset is
    # the pad-local `(drill ... (offset x y))`, rotated with the pad. KiCad
    # keeps the HOLE at the pad's (at), `center`, and moves the COPPER by the
    # offset (pcbnew PAD::GetOffset, checked in SWIG 2026-10-04), whatever
    # the token's name suggests: poly is offset, drill_poly is not.
    drill: Optional[tuple[float, float]] = None
    drill_offset: tuple[float, float] = (0.0, 0.0)
    # KiCad's "Castellated pad" fabrication property, `(property
    # pad_prop_castellated)` - a plated half-hole on the board edge
    # (lib/castellation.py).
    castellated: bool = False

    @cached_property
    def copper_center(self) -> tuple[float, float]:
        """Centre of the pad's copper: `center` moved by the drill offset,
        turned with the pad the same -angle way drill_poly's shape is."""
        ox, oy = self.drill_offset
        if not ox and not oy:
            return self.center
        o = affinity.rotate(Point(ox, oy), -self.angle, origin=(0, 0),
                            use_radians=False)
        return (self.center[0] + o.x, self.center[1] + o.y)

    @cached_property
    def poly(self) -> Polygon:
        return _pad_polygon(self.shape, self.size[0], self.size[1],
                            self.rratio, self.copper_center, self.angle)

    @cached_property
    def drill_poly(self) -> Polygon:
        """Hole extents as board-frame copper-free area: a disc for round
        drills, a stadium for `(drill oval w h)` slots. The SHAPE rotates by
        -self.angle - the pad's stored angle is the ABSOLUTE board angle
        (LEARNINGS [routing][stitch][drc] 2026-07-28: using prot - frot
        silently yields a drill turned 90 deg). Empty polygon for SMD pads.
        """
        if self.drill is None:
            return Polygon()
        w, h = self.drill
        if w <= 0 or h <= 0:
            return Polygon()
        if abs(w - h) < 1e-9:
            g = Point(0, 0).buffer(w / 2.0, quad_segs=_QUAD_SEGS)
        elif w > h:
            half = (w - h) / 2.0
            g = LineString([(-half, 0), (half, 0)]).buffer(
                h / 2.0, quad_segs=_QUAD_SEGS)
        else:
            half = (h - w) / 2.0
            g = LineString([(0, -half), (0, half)]).buffer(
                w / 2.0, quad_segs=_QUAD_SEGS)
        g = affinity.rotate(g, -self.angle, origin=(0, 0), use_radians=False)
        return affinity.translate(g, self.center[0], self.center[1])

    def on(self, layer: str) -> bool:
        return layer in self.layers


@dataclass
class Zone:
    net: str
    layers: tuple[str, ...]           # declared layers
    fills: dict[str, list[Polygon]]   # layer -> filled polygons (may be empty)
    zone_id: int

    @property
    def filled(self) -> bool:
        return any(polys for polys in self.fills.values())

    def fill_on(self, layer: str) -> MultiPolygon | Polygon:
        return _union(self.fills.get(layer, []))

    def fill_area(self, layer: str) -> float:
        return self.fill_on(layer).area


@dataclass
class Stackup:
    """Physical layer stack. Copper order is authoritative (from the board's
    (layers) list, top->bottom); dielectric heights + epsilon_r come from a
    (stackup) block when present, else documented FR4 defaults (assumed=True)."""
    copper_layers: list[str]
    total_thickness: float
    dielectrics: list[dict]  # per gap: {above, below, height, epsilon_r}
    copper_thickness: dict[str, float]
    assumed: bool
    source: str

    def index(self, layer: str) -> int:
        return self.copper_layers.index(layer)

    def is_outer(self, layer: str) -> bool:
        return layer in (self.copper_layers[0], self.copper_layers[-1])

    def adjacent(self, layer: str) -> tuple[Optional[str], Optional[str]]:
        """(above, below) copper neighbours in the stack, or None at an edge."""
        i = self.index(layer)
        above = self.copper_layers[i - 1] if i > 0 else None
        below = self.copper_layers[i + 1] if i < len(self.copper_layers) - 1 else None
        return above, below

    def height_between(self, a: str, b: str) -> float:
        i, j = sorted((self.index(a), self.index(b)))
        return sum(d["height"] for d in self.dielectrics[i:j])

    def epsilon_between(self, a: str, b: str) -> float:
        i, j = sorted((self.index(a), self.index(b)))
        gaps = self.dielectrics[i:j] or self.dielectrics
        return sum(d["epsilon_r"] for d in gaps) / len(gaps)

    def as_dict(self) -> dict:
        return {
            "copper_layers": self.copper_layers,
            "total_thickness": self.total_thickness,
            "dielectrics": self.dielectrics,
            "copper_thickness": self.copper_thickness,
            "assumed": self.assumed,
            "source": self.source,
        }


# ============================================================ geometry builders

def _pad_polygon(shape: str, w: float, h: float, rratio: float,
                 center: tuple[float, float], angle: float) -> Polygon:
    """Copper polygon for a pad, centred at `center`, oriented `angle` deg.

    Built at the origin then rotated by -angle (KiCad CW-positive with Y down)
    and translated. All corpus shapes are axis-symmetric, so the rotation sign
    does not change the covered region; only `center` (fixed by the footprint
    transform in the caller) matters. trapezoid/custom are absent from the
    corpus and fall back to the size bounding box.
    """
    w = max(w, 0.0)
    h = max(h, 0.0)
    if shape == "circle":
        g = Point(0, 0).buffer(w / 2.0, quad_segs=_QUAD_SEGS)
    elif shape == "oval":
        if w >= h:
            r, half = h / 2.0, (w - h) / 2.0
            line = LineString([(-half, 0), (half, 0)]) if half > 0 else None
        else:
            r, half = w / 2.0, (h - w) / 2.0
            line = LineString([(0, -half), (0, half)])
        g = (line.buffer(r, quad_segs=_QUAD_SEGS) if half > 0
             else Point(0, 0).buffer(r, quad_segs=_QUAD_SEGS))
    elif shape == "roundrect":
        # KiCad clamps rratio to [0, 0.5]; malformed footprints (easyeda2kicad)
        # can exceed it, and an unclamped r inverts the inner box -> garbage
        # polygon LARGER than the pad. Clamp; r == min/2 is a stadium (valid).
        r = min(max(0.0, rratio) * min(w, h), min(w, h) / 2.0)
        if r <= 0:
            g = box(-w / 2, -h / 2, w / 2, h / 2)
        else:
            inner = box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r)
            g = inner.buffer(r, quad_segs=_QUAD_SEGS, join_style="round")
    else:  # rect, trapezoid, custom -> bounding box
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    g = affinity.rotate(g, -angle, origin=(0, 0), use_radians=False)
    return affinity.translate(g, center[0], center[1])


def _union(geoms: Iterable) -> MultiPolygon | Polygon:
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return Polygon()
    if len(geoms) == 1:
        return geoms[0]
    return unary_union(geoms)


def _arc_radius(start, mid, end) -> Optional[float]:
    """Exact radius of the circle through three points (None if colinear)."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return None
    cx = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1)
          + (x3**2 + y3**2) * (y1 - y2)) / d
    cy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3)
          + (x3**2 + y3**2) * (x2 - x1)) / d
    return math.hypot(x1 - cx, y1 - cy)


def _arc_points(start, mid, end, n: int = 16) -> list[tuple[float, float]]:
    """Sample a circular arc through three points (used for track/edge arcs)."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [start, mid, end]  # colinear -> polyline
    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1)
          + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3)
          + (x3**2 + y3**2) * (x2 - x1)) / d
    cx, cy = ux, uy
    a1 = math.atan2(y1 - cy, x1 - cx)
    a2 = math.atan2(y2 - cy, x2 - cx)
    a3 = math.atan2(y3 - cy, x3 - cx)
    # ensure the sampled sweep passes through mid
    def norm(a, ref):
        while a < ref:
            a += 2 * math.pi
        return a
    a3u = norm(a3, a1)
    a2u = norm(a2, a1)
    if not (a1 <= a2u <= a3u):  # sweep the other way
        a3u = a3 - 2 * math.pi if a3 > a1 else a3
        pts = [(cx + math.cos(a1 + (a3u - a1) * t / n) * math.hypot(x1 - cx, y1 - cy),
                cy + math.sin(a1 + (a3u - a1) * t / n) * math.hypot(x1 - cx, y1 - cy))
               for t in range(n + 1)]
        pts[0], pts[-1] = start, end
        return pts
    r = math.hypot(x1 - cx, y1 - cy)
    pts = [(cx + r * math.cos(a1 + (a3u - a1) * t / n),
            cy + r * math.sin(a1 + (a3u - a1) * t / n)) for t in range(n + 1)]
    # Pin the sampled ends to the DECLARED endpoints. Recomputing them from the
    # centre+angle lands ~1e-14 off, which is enough that an arc and the line
    # meeting it never node in unary_union - polygonize then finds no closed
    # face and a rounded board outline silently parses as POLYGON EMPTY.
    pts[0], pts[-1] = start, end
    return pts


# ============================================================ stackup model

def _build_stackup(root, copper_layers: list[str], total_thickness: float) -> Stackup:
    setup = _kid(root, "setup")
    stk = _kid(setup, "stackup") if setup is not None else None
    n = len(copper_layers)
    cu_th = {l: (_CU_OUTER if l in (copper_layers[0], copper_layers[-1]) else _CU_INNER)
             for l in copper_layers}

    if stk is not None:
        # Parse dielectric sublayers between coppers from an explicit stackup.
        dielectrics: list[dict] = []
        gap: list[dict] = []
        order: list[tuple[str, dict]] = []
        for layer in _kids(stk, "layer"):
            name = _strs(layer)[0] if _strs(layer) else ""
            th = _kid(layer, "thickness")
            er = _kid(layer, "epsilon_r")
            entry = {
                "height": _nums(th)[0] if th is not None and _nums(th) else 0.0,
                "epsilon_r": _nums(er)[0] if er is not None and _nums(er) else _FR4_ER,
            }
            order.append((name, entry))
        # Walk copper-to-copper, summing dielectric sublayers between them.
        cu_names = set(copper_layers)
        cur: Optional[str] = None
        acc_h = 0.0
        acc_er: list[float] = []
        for name, entry in order:
            if name in cu_names:
                if cur is not None:
                    dielectrics.append({
                        "above": cur, "below": name, "height": acc_h,
                        "epsilon_r": (sum(acc_er) / len(acc_er)) if acc_er else _FR4_ER,
                    })
                cur = name
                acc_h, acc_er = 0.0, []
                if entry["height"] > 0:  # copper thickness from the block
                    cu_th[name] = entry["height"]
            else:
                acc_h += entry["height"]
                if entry["epsilon_r"]:
                    acc_er.append(entry["epsilon_r"])
        if len(dielectrics) == n - 1:
            return Stackup(copper_layers, total_thickness, dielectrics, cu_th,
                           assumed=False, source="board (stackup)")

    # No usable stackup block -> documented FR4 defaults.
    cu_total = sum(cu_th.values())
    dielec_total = max(total_thickness - cu_total, 0.1)
    if n == 2:
        heights = [dielec_total]
    elif n == 4:
        # JLC-typical 1.6 mm ratio: prepreg / core / prepreg ~= 0.165/0.67/0.165.
        heights = [dielec_total * 0.165, dielec_total * 0.67, dielec_total * 0.165]
    else:
        heights = [dielec_total / (n - 1)] * (n - 1) if n > 1 else []
    dielectrics = [{"above": copper_layers[i], "below": copper_layers[i + 1],
                    "height": heights[i], "epsilon_r": _FR4_ER}
                   for i in range(n - 1)]
    return Stackup(copper_layers, total_thickness, dielectrics, cu_th,
                   assumed=True, source="default FR4 (no stackup block)")


# ============================================================ BoardGeom

class BoardGeom:
    """Parsed, net-indexed geometry for one .kicad_pcb. Build via
    `BoardGeom.from_file(path)` or the cached `load_board(path)`."""

    def __init__(self, path: Path, root: list):
        self.path = Path(path)
        self._union_cache: dict[tuple, object] = {}

        # ---- layers (copper order = file order, top->bottom) ----
        self._layer_by_ord: dict[int, str] = {}
        copper: list[str] = []
        layers_node = _kid(root, "layers")
        if layers_node is None:
            raise GeomError(f"no (layers) block in {path}")
        for entry in layers_node[1:]:
            if not _is_node(entry):
                continue
            ordn = int(entry[0]) if isinstance(entry[0], (int, float)) else None
            name = _strs(entry)[0] if _strs(entry) else None
            if name is None:
                continue
            if ordn is not None:
                self._layer_by_ord[ordn] = name
            if name.endswith(".Cu"):
                copper.append(name)
        if not copper:
            raise GeomError(f"no copper layers in {path}")
        self.copper_layers = copper
        self._copper_set = set(copper)

        gen = _kid(root, "general")
        thick_node = _kid(gen, "thickness") if gen is not None else None
        self.thickness = _nums(thick_node)[0] if thick_node and _nums(thick_node) else 1.6

        # ---- optional numeric net table (num -> name); items usually name-only ----
        self._net_table: dict[int, str] = {}
        for netn in _kids(root, "net"):
            nums = [a for a in netn[1:] if isinstance(a, (int, float))]
            strs = _strs(netn)
            if nums:
                self._net_table[int(nums[0])] = strs[0] if strs else ""

        self.stackup = _build_stackup(root, self.copper_layers, self.thickness)

        # ---- primitives ----
        self._tracks: list[Track] = []
        self._vias: list[Via] = []
        self._pads: list[Pad] = []
        self._zones: list[Zone] = []
        self.rule_areas: list[dict] = []  # keepout areas: {name, layers, outline}
        self.footprints: dict[str, dict] = {}  # ref -> {"lib", "value"}
        self._parse_tracks(root)
        self._parse_vias(root)
        self._parse_footprints(root)
        self._parse_zones(root)
        self.outline = self._parse_outline(root)

        self.nets: set[str] = set()
        for coll in (self._tracks, self._vias, self._zones):
            self.nets.update(x.net for x in coll if x.net)
        self.nets.update(p.net for p in self._pads if p.net)

    # -------------------------------------------------- construction
    @classmethod
    def from_file(cls, path) -> "BoardGeom":
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GeomError(f"cannot read {path}: {exc}") from exc
        try:
            root = sexpdata.loads(text)
        except Exception as exc:  # sexpdata raises various parse errors
            raise GeomError(f"s-expr parse failed for {path}: {exc}") from exc
        if _head(root) != "kicad_pcb":
            raise GeomError(f"not a kicad_pcb file: {path}")
        return cls(path, root)

    def _resolve_net(self, item) -> Optional[str]:
        node = _kid(item, "net")
        if node is None:
            return None
        strs = _strs(node)
        if strs:
            return strs[-1]  # (net "NAME") or (net N "NAME")
        nums = [a for a in node[1:] if isinstance(a, (int, float))]
        if nums:
            return self._net_table.get(int(nums[0]))
        return None

    def _copper_of(self, layers_node) -> tuple[str, ...]:
        """Copper layers named by a (layers ...) node, expanding *.Cu wildcards
        and the F&B.Cu front-and-back shorthand (zones/rule areas use it)."""
        out: list[str] = []
        for tok in _strs(layers_node) if layers_node is not None else []:
            if "*" in tok and tok.endswith(".Cu"):
                out = list(self.copper_layers)
                break
            if tok == "F&B.Cu":
                out += [n for n in ("F.Cu", "B.Cu")
                        if n in self._copper_set and n not in out]
                continue
            if tok in self._copper_set and tok not in out:
                out.append(tok)
        return tuple(out)

    def _zone_layers(self, zone) -> tuple[str, ...]:
        """Declared copper layers of a zone/rule-area node ((layers) or (layer))."""
        layers_node = _kid(zone, "layers")
        if layers_node is not None:
            return self._copper_of(layers_node)
        layer = _kid(zone, "layer")
        if layer is not None and _strs(layer):
            ln = _strs(layer)[0]
            if ln in self._copper_set:
                return (ln,)
        return ()

    def _parse_tracks(self, root):
        for seg in _kids(root, "segment"):
            s, e = _kid(seg, "start"), _kid(seg, "end")
            w, layer = _kid(seg, "width"), _kid(seg, "layer")
            if not (s and e and w and layer):
                continue
            ln = _strs(layer)[0] if _strs(layer) else None
            if ln not in self._copper_set:
                continue
            a, b = _nums(s), _nums(e)
            self._tracks.append(Track(
                net=self._resolve_net(seg) or "", layer=ln,
                width=_nums(w)[0], shape=LineString([tuple(a[:2]), tuple(b[:2])]),
                uuid=_uuid_of(seg)))
        for arc in _kids(root, "arc"):
            s, m, e = _kid(arc, "start"), _kid(arc, "mid"), _kid(arc, "end")
            w, layer = _kid(arc, "width"), _kid(arc, "layer")
            if not (s and m and e and w and layer):
                continue
            ln = _strs(layer)[0] if _strs(layer) else None
            if ln not in self._copper_set:
                continue
            pts = _arc_points(tuple(_nums(s)[:2]), tuple(_nums(m)[:2]), tuple(_nums(e)[:2]))
            self._tracks.append(Track(
                net=self._resolve_net(arc) or "", layer=ln,
                width=_nums(w)[0], shape=LineString(pts), uuid=_uuid_of(arc)))

    def _parse_vias(self, root):
        for via in _kids(root, "via"):
            at = _kid(via, "at")
            size = _kid(via, "size")
            drill = _kid(via, "drill")
            layers = _kid(via, "layers")
            if not (at and size):
                continue
            # A via's (layers A B) is a from/to SPAN, not a literal set: a
            # through via has copper on every copper layer between A and B.
            names = [t for t in _strs(layers) if t in self._copper_set] if layers else []
            if len(names) >= 2:
                i, j = sorted((self.copper_layers.index(names[0]),
                               self.copper_layers.index(names[-1])))
                spanned = tuple(self.copper_layers[i:j + 1])
            elif len(names) == 1:
                spanned = (names[0],)
            else:
                spanned = tuple(self.copper_layers)  # default through-via
            a = _nums(at)
            self._vias.append(Via(
                net=self._resolve_net(via) or "", at=(a[0], a[1]),
                diameter=_nums(size)[0],
                drill=_nums(drill)[0] if drill and _nums(drill) else 0.0,
                layers=spanned, uuid=_uuid_of(via)))

    def _parse_footprints(self, root):
        for fp in _kids(root, "footprint"):
            at = _kid(fp, "at")
            fnums = _nums(at) if at is not None else [0, 0, 0]
            fx, fy = fnums[0], fnums[1]
            fangle = fnums[2] if len(fnums) > 2 else 0.0
            ref, value = "?", None
            for prop in _kids(fp, "property"):
                pv = _strs(prop)
                if len(pv) >= 2 and pv[0] == "Reference":
                    ref = pv[1]
                elif len(pv) >= 2 and pv[0] == "Value":
                    value = pv[1]
            lib = _strs(fp)[0] if _strs(fp) else ""
            # ref -> lib id + Value field (check_current bounds a resistor
            # branch's current from these); the last duplicate ref wins
            self.footprints[ref] = {"lib": lib, "value": value}
            for pad in _kids(fp, "pad"):
                self._add_pad(pad, fx, fy, fangle, ref)

    def _add_pad(self, pad, fx, fy, fangle, ref):
        """Back-side (flipped) footprints need NO special handling: pcbnew
        Flip() bakes the mirror into the stored values - pad locals are
        already mirrored, angles negated, and the pad (layers) list renamed
        to B.* in the file - so the same fp + R(-fp_angle).local transform
        reproduces pcbnew's positions for front AND back parts
        (S3-verified against a SWIG-flipped board; V10, LEARNINGS [geometry]).
        """
        # positional: (pad "num" <type> <shape> ...)
        number = str(pad[1]) if len(pad) > 1 else "?"
        pshape = _tok(pad[3]) if len(pad) > 3 else "rect"
        at = _kid(pad, "at")
        size = _kid(pad, "size")
        if at is None or size is None:
            return
        lx, ly, *rest = _nums(at)
        pad_angle = rest[0] if rest else 0.0
        # absolute center = fp + R(-fangle) . (lx, ly)
        dx, dy = _rot(lx, ly, -fangle)
        center = (fx + dx, fy + dy)
        sz = _nums(size)
        w, h = sz[0], (sz[1] if len(sz) > 1 else sz[0])
        rr = _kid(pad, "roundrect_rratio")
        rratio = _nums(rr)[0] if rr and _nums(rr) else 0.0
        layers = self._copper_of(_kid(pad, "layers"))
        if not layers:
            return  # no copper (e.g. NPTH mechanical or paste-only pad)
        drill = None
        drill_off = (0.0, 0.0)
        dnode = _kid(pad, "drill")
        if dnode is not None:
            dnums = _nums(dnode)
            oval = any(_tok(x) == "oval" for x in dnode[1:]
                       if isinstance(x, (sexpdata.Symbol, str)))
            if oval and len(dnums) >= 2:
                drill = (dnums[0], dnums[1])
            elif dnums:
                drill = (dnums[0], dnums[0])
            onode = _kid(dnode, "offset")
            if onode is not None:
                on = _nums(onode)
                if len(on) >= 2:
                    drill_off = (on[0], on[1])
        self._pads.append(Pad(
            ref=ref, number=number, net=self._resolve_net(pad),
            shape=pshape, size=(w, h), center=center, angle=pad_angle,
            rratio=rratio, layers=layers, drill=drill,
            drill_offset=drill_off,
            castellated=any(_tok(x) == "pad_prop_castellated"
                            for prop in _kids(pad, "property")
                            for x in prop[1:])))

    def _parse_zones(self, root):
        zid = 0
        for zone in _kids(root, "zone"):
            declared = self._zone_layers(zone)
            if _kid(zone, "keepout") is not None:
                # Rule area: never fills, carries no copper. It must NOT enter
                # the zone list or the freshness gate would flag it as an
                # eternally-unfilled zone (the plane-split mutant carries one).
                # Outline kept as metadata (S9 placement keepouts).
                name_node = _kid(zone, "name")
                poly_node = _kid(zone, "polygon")
                pts = (_pts(_kid(poly_node, "pts"))
                       if poly_node is not None and _kid(poly_node, "pts") else [])
                self.rule_areas.append({
                    "name": _strs(name_node)[0] if name_node and _strs(name_node) else "",
                    "layers": declared,
                    "outline": Polygon(pts) if len(pts) >= 3 else Polygon(),
                    # Keepout flags ({"footprints": "not_allowed", ...}); not
                    # serialised. A rule area that only scopes a rule has all
                    # of them "allowed".
                    "flags": {_tok(k[0]): _tok(k[1]) for k in _kid(zone, "keepout")[1:]
                              if _is_node(k) and len(k) > 1},
                })
                continue
            net = self._resolve_net(zone) or ""
            fills: dict[str, list[Polygon]] = {}
            for fp in _kids(zone, "filled_polygon"):
                fl = _kid(fp, "layer")
                fln = _strs(fl)[0] if fl is not None and _strs(fl) else (
                    declared[0] if declared else None)
                if fln is None:
                    continue
                pts = _pts(_kid(fp, "pts")) if _kid(fp, "pts") is not None else _pts(fp)
                if len(pts) >= 3:
                    poly = Polygon(pts)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if not poly.is_empty:
                        fills.setdefault(fln, []).append(poly)
            self._zones.append(Zone(net=net, layers=declared or tuple(fills.keys()),
                                    fills=fills, zone_id=zid))
            zid += 1

    def _parse_outline(self, root) -> Polygon:
        """The board outline: the LARGEST closed face Edge.Cuts forms.

        `self.outline_faces` keeps every face, largest first - one face is a
        plain outline, more than one means interior windows or a second board
        in the file. Anything REWRITING Edge.Cuts (board_edit) must look at the
        faces, not just the winner, or it silently drops the others.

        `self.outline_arc_radii` holds every Edge.Cuts arc's EXACT radius (from
        its three declared points). A corner radius read back from the sampled
        polygon's AREA carries the sampling bias, so a board re-edited through
        board_edit would inflate its own radius a little each time.

        `self.outline_items` counts the Edge.Cuts primitives BY KIND, before
        any of the early returns below. That matters: this parser returns on
        the FIRST gr_rect it finds, so a second one (an interior window) never
        reaches `.outline` - the counts are the only place it shows up."""
        self.outline_faces: list[Polygon] = []
        self.outline_arc_radii: list[float] = []
        self.outline_items: dict[str, int] = {}
        for kind in ("gr_rect", "gr_poly", "gr_circle", "gr_line", "gr_arc"):
            n = sum(1 for g in _kids(root, kind)
                    if _layer_name(g) == "Edge.Cuts")
            if n:
                self.outline_items[kind] = n
        edges = []
        for rect in _kids(root, "gr_rect"):
            if _layer_name(rect) != "Edge.Cuts":
                continue
            s, e = _nums(_kid(rect, "start")), _nums(_kid(rect, "end"))
            if len(s) >= 2 and len(e) >= 2:
                got = box(min(s[0], e[0]), min(s[1], e[1]),
                          max(s[0], e[0]), max(s[1], e[1]))
                self.outline_faces = [got]
                return got
        for poly in _kids(root, "gr_poly"):
            if _layer_name(poly) != "Edge.Cuts":
                continue
            pts = _pts(_kid(poly, "pts")) if _kid(poly, "pts") else _pts(poly)
            if len(pts) >= 3:
                got = Polygon(pts)
                self.outline_faces = [got]
                return got
        for circ in _kids(root, "gr_circle"):
            if _layer_name(circ) != "Edge.Cuts":
                continue
            c, e = _nums(_kid(circ, "center")), _nums(_kid(circ, "end"))
            if len(c) >= 2 and len(e) >= 2:
                r = math.hypot(e[0] - c[0], e[1] - c[1])
                got = Point(c[0], c[1]).buffer(r, quad_segs=_QUAD_SEGS * 2)
                self.outline_faces = [got]
                return got
        for gl in _kids(root, "gr_line"):
            if _layer_name(gl) != "Edge.Cuts":
                continue
            s, e = _nums(_kid(gl, "start")), _nums(_kid(gl, "end"))
            if len(s) >= 2 and len(e) >= 2:
                edges.append(LineString([(s[0], s[1]), (e[0], e[1])]))
        for ga in _kids(root, "gr_arc"):
            if _layer_name(ga) != "Edge.Cuts":
                continue
            s, m, e = _kid(ga, "start"), _kid(ga, "mid"), _kid(ga, "end")
            if s and m and e:
                sp, mp, ep = (tuple(_nums(s)[:2]), tuple(_nums(m)[:2]),
                              tuple(_nums(e)[:2]))
                edges.append(LineString(_arc_points(sp, mp, ep)))
                r = _arc_radius(sp, mp, ep)
                if r is not None:
                    self.outline_arc_radii.append(r)
        if edges:
            faces = sorted(polygonize(unary_union(edges)),
                           key=lambda g: g.area, reverse=True)
            if faces:
                self.outline_faces = faces
                return faces[0]
        return Polygon()

    # -------------------------------------------------- accessors
    def tracks_of(self, net: str = None, layer: str = None) -> list[Track]:
        return [t for t in self._tracks
                if (net is None or t.net == net) and (layer is None or t.layer == layer)]

    def vias_of(self, net: str = None, layer: str = None) -> list[Via]:
        return [v for v in self._vias
                if (net is None or v.net == net) and (layer is None or v.spans(layer))]

    def pads_of(self, net: str = None, layer: str = None, ref: str = None) -> list[Pad]:
        return [p for p in self._pads
                if (net is None or p.net == net)
                and (layer is None or p.on(layer))
                and (ref is None or p.ref == ref)]

    def zones_of(self, net: str = None, layer: str = None) -> list[Zone]:
        return [z for z in self._zones
                if (net is None or z.net == net)
                and (layer is None or layer in z.layers or layer in z.fills)]

    def zone_fill(self, net: str, layer: str) -> MultiPolygon | Polygon:
        return _union([z.fill_on(layer) for z in self.zones_of(net, layer)])

    def net_copper(self, net: str, layer: str) -> MultiPolygon | Polygon:
        """Union of ALL copper (tracks+pads+vias+zone fill) of `net` on `layer`."""
        key = ("net_copper", net, layer)
        if key not in self._union_cache:
            parts = [t.poly for t in self.tracks_of(net, layer)]
            parts += [p.poly for p in self.pads_of(net, layer)]
            parts += [v.poly for v in self.vias_of(net, layer)]
            parts += [z.fill_on(layer) for z in self.zones_of(net, layer)]
            self._union_cache[key] = _union(parts)
        return self._union_cache[key]

    def layer_copper(self, layer: str, net: str = None,
                     exclude: str = None) -> MultiPolygon | Polygon:
        """Union of copper on a layer. `net` restricts, `exclude` omits a net."""
        key = ("layer_copper", layer, net, exclude)
        if key not in self._union_cache:
            nets = ([net] if net else sorted(self.nets))
            self._union_cache[key] = _union(
                [self.net_copper(n, layer) for n in nets if n != exclude])
        return self._union_cache[key]

    def net_area(self, net: str, layer: str) -> float:
        return self.net_copper(net, layer).area

    def net_area_by_layer(self, net: str) -> dict[str, float]:
        return {l: self.net_area(net, l) for l in self.copper_layers
                if self.net_area(net, l) > 0}

    def adjacent_copper(self, layer: str) -> tuple[Optional[str], Optional[str]]:
        return self.stackup.adjacent(layer)

    def layers_with_zone(self, net: str) -> list[str]:
        out = []
        for l in self.copper_layers:
            if any(z.fills.get(l) for z in self.zones_of(net, l)):
                out.append(l)
        return out

    # -------------------------------------------------- zone-fill freshness
    def fill_status(self) -> dict[int, bool]:
        return {z.zone_id: z.filled for z in self._zones}

    def unfilled_zones(self) -> list[Zone]:
        return [z for z in self._zones if not z.filled]

    def assert_fresh(self, *, refill: bool = False, area_tol: float = 0.01) -> None:
        """Refuse to run on stale/unfilled zones.

        Fast mode (default): raise StaleFillError if any zone has an outline but
        no fill. Thorough mode (refill=True): additionally run the S0-verified
        `kicad-cli pcb drc --refill-zones --save-board` on a temp copy and raise
        if any zone's fill area drifts by more than `area_tol` (relative) - i.e.
        the committed fill is stale versus a fresh refill.
        """
        unfilled = self.unfilled_zones()
        if unfilled:
            raise StaleFillError(
                f"{len(unfilled)} zone(s) unfilled in {self.path.name}: "
                f"ids {[z.zone_id for z in unfilled]}")
        if not refill:
            return
        fresh = _refill_copy(self.path)
        try:
            other = BoardGeom.from_file(fresh)
            for z in self._zones:
                for l in z.layers:
                    a0 = z.fill_area(l)
                    a1 = next((oz.fill_area(l) for oz in other._zones
                               if oz.zone_id == z.zone_id), 0.0)
                    denom = max(a0, a1, 1e-6)
                    if abs(a0 - a1) / denom > area_tol:
                        raise StaleFillError(
                            f"zone {z.zone_id} on {l}: committed fill {a0:.3f} mm^2 "
                            f"differs from fresh {a1:.3f} mm^2 (> {area_tol:.0%})")
        finally:
            _cleanup(fresh)

    # -------------------------------------------------- summary
    def summary(self) -> dict:
        nets = {}
        for n in sorted(self.nets):
            by = self.net_area_by_layer(n)
            if by:
                nets[n] = {layer: round(v, 6) for layer, v in by.items()}
        return {
            "board": self.path.name,
            "copper_layers": self.copper_layers,
            "thickness_mm": self.thickness,
            "stackup": self.stackup.as_dict(),
            "counts": {"tracks": len(self._tracks), "vias": len(self._vias),
                       "pads": len(self._pads), "zones": len(self._zones),
                       "nets": len(self.nets)},
            "outline_area_mm2": round(self.outline.area, 6),
            "unfilled_zones": [z.zone_id for z in self.unfilled_zones()],
            "rule_areas": [{"name": ra["name"], "layers": list(ra["layers"]),
                            "area_mm2": round(ra["outline"].area, 6)}
                           for ra in self.rule_areas],
            "net_area_mm2": nets,
        }


# ============================================================ layer helpers

def _layer_name(node) -> Optional[str]:
    l = _kid(node, "layer")
    return _strs(l)[0] if l is not None and _strs(l) else None


# ============================================================ refill (S0 path)

def _refill_copy(pcb: Path) -> Path:
    """Copy the board to a temp dir and refill zones via kicad-cli. Returns the
    refilled copy's path. Reuses env.py (pin) + kc.run_drc (S0-verified flags)."""
    import shutil
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))          # lib/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))      # scripts/
    import env  # type: ignore
    import kc   # type: ignore
    cli = env.find_kicad_cli()
    if cli is None:
        raise StaleFillError("no kicad-cli for refill (env.find_kicad_cli None)")
    tmp = Path(tempfile.mkdtemp(prefix="geom_refill_"))
    dst = tmp / pcb.name
    shutil.copy2(pcb, dst)
    # copy the sibling .kicad_pro if present (keeps DRC rules identical)
    pro = pcb.with_suffix(".kicad_pro")
    if pro.exists():
        shutil.copy2(pro, dst.with_suffix(".kicad_pro"))
    kc.run_drc(cli, dst, refill=True, save_board=True)
    return dst


def _cleanup(pcb: Path) -> None:
    import shutil
    try:
        shutil.rmtree(pcb.parent, ignore_errors=True)
    except OSError:
        pass


# ============================================================ module cache

_CACHE: dict[str, tuple[tuple, BoardGeom]] = {}


def load_board(path, *, refresh: bool = False) -> BoardGeom:
    """Parse a board, caching by resolved path + (mtime, size). Repeated calls in
    one process return the same BoardGeom (checks reuse the built geometry)."""
    path = Path(path)
    key = str(path.resolve())
    st = path.stat()
    sig = (st.st_mtime_ns, st.st_size)
    cached = _CACHE.get(key)
    if cached and cached[0] == sig and not refresh:
        return cached[1]
    bg = BoardGeom.from_file(path)
    _CACHE[key] = (sig, bg)
    return bg


# ============================================================ CLI

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Dump net-indexed geometry summary "
                                 "of a .kicad_pcb as JSON.")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--net", help="restrict net_area output to this net")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--check-fill", action="store_true",
                    help="exit 1 if any zone is unfilled")
    args = ap.parse_args(argv)
    try:
        bg = BoardGeom.from_file(Path(args.pcb))
        out = bg.summary()
        if args.net:
            out["net_area_mm2"] = {args.net: out["net_area_mm2"].get(args.net, {})}
    except Exception as exc:  # noqa: BLE001  (contract: any error -> exit 2)
        print(json.dumps({"script": "geom", "status": "error", "error": str(exc)}))
        return 2
    stale = bool(args.check_fill and out["unfilled_zones"])
    payload = {"script": "geom",
               "status": "violations" if stale else "pass", **out}
    text = json.dumps(payload, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
