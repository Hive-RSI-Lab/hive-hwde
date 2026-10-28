#!/usr/bin/env python
"""schem_refdes.py - deterministic refdes/value placement on a schematic sheet.

P4 emits schematics through kicad-sch-api, which positions every instance
property by ADDING the library offset to the symbol origin. Library coordinates
are y-UP and page coordinates are y-DOWN, so each field lands mirrored to the
wrong side of its part: the reference below and the value above, the reverse of
KiCad's own convention (verified against wire endpoints - a Device:C pin at
library y +3.81 lands at page y -3.81, while its Reference lands at +2.54).
On a dense sheet the mirrored fields collide with wires, labels and neighbours.

This places both fields from an offset table keyed by symbol CLASS and
orientation, then walks a deterministic candidate ladder until the text box
clears every other item on the sheet (bodies, pins, wires, labels, junctions,
no-connects, sheet boxes, text, and the fields placed before it). Anything it
cannot resolve is reported as residue with the colliding item named - it is a
placement report, not a silent best effort.

Reading is a direct s-expression parse (the schematic's OWN embedded
lib_symbols, so project symbols need no library resolution); writing goes
through kicad-sch-api with the project symbol library registered first and a
post-save guard - see the lib_symbols warning in LEARNINGS [kicad-sch-api].

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2.
  0 = every field placed clear
  1 = residue (a field no candidate could clear)
  2 = internal error / bad arguments

Examples:
  schem_refdes.py --sch boards/b/kicad/b.kicad_sch
  schem_refdes.py --sch a.kicad_sch b.kicad_sch --dry-run --out place.json
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import re
import sys
from pathlib import Path

import sexpdata
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import env  # noqa: E402

env.export_kicad_symbol_dir()   # before kicad-sch-api's first library scan

# Text metrics. KiCad's stroke font inks a box of the nominal glyph height plus
# the stroke thickness (LEARNINGS 2026-07-29 [parts][silk]); the per-character
# advance is a fraction of the height, taken slightly generous so a "clear"
# verdict stays conservative.
ADVANCE = 0.72
DEFAULT_SIZE = 1.27
DEFAULT_THICK = 0.15
CLEARANCE = 0.254          # gap a field must keep from every other item, mm
STEP = 1.27                # candidate ladder pitch, mm
PIN_TEXT_BAND = 0.7        # half-height of the pin-number text band, mm
GAP = 1.27                 # field-to-body gap for the primary candidate, mm

_FIELDS = ("Reference", "Value")


class RefdesError(RuntimeError):
    pass


# --------------------------------------------------------------- sexp helpers

def _tok(x):
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def _head(n):
    return _tok(n[0]) if isinstance(n, list) and n else None


def _kid(n, name):
    for c in n[1:] if isinstance(n, list) else []:
        if isinstance(c, list) and c and _tok(c[0]) == name:
            return c
    return None


def _kids(n, name):
    return [c for c in (n[1:] if isinstance(n, list) else [])
            if isinstance(c, list) and c and _tok(c[0]) == name]


def _nums(n) -> list[float]:
    return [float(x) for x in n[1:] if isinstance(x, (int, float))] if n else []


def _yes(n, name) -> bool:
    k = _kid(n, name)
    return bool(k) and len(k) > 1 and str(_tok(k[1])).lower() in ("yes", "true")


def _pts(node) -> list[tuple[float, float]]:
    p = _kid(node, "pts") or node
    return [(_nums(c)[0], _nums(c)[1]) for c in _kids(p, "xy") if len(_nums(c)) >= 2]


def _effects(node):
    """(size_x, size_y, thickness, justify_h, hidden) of a text-bearing node."""
    eff = _kid(node, "effects")
    size = (DEFAULT_SIZE, DEFAULT_SIZE)
    thick, just = DEFAULT_THICK, None
    hidden = _yes(node, "hide") or (eff is not None and _yes(eff, "hide"))
    if eff is not None:
        font = _kid(eff, "font")
        if font is not None:
            s = _nums(_kid(font, "size"))
            if len(s) >= 2:
                size = (s[0], s[1])
            t = _nums(_kid(font, "thickness"))
            if t:
                thick = t[0]
        j = _kid(eff, "justify")
        if j is not None:
            names = [str(_tok(v)) for v in j[1:]]
            for v in names:
                if v in ("left", "right"):
                    just = v
        if "hide" in [str(_tok(v)) for v in (eff[1:] if len(eff) > 1 else [])]:
            hidden = True
    return size, thick, just, hidden


def text_box(text: str, at: tuple[float, float], size, thick: float,
             justify: str | None, angle: float = 0.0) -> Polygon:
    """Inked box of a schematic text item, honouring its justification."""
    w = max(len(text), 1) * size[0] * ADVANCE + thick
    h = size[1] + thick
    x, y = at
    if justify == "left":
        x0, x1 = x, x + w
    elif justify == "right":
        x0, x1 = x - w, x
    else:
        x0, x1 = x - w / 2.0, x + w / 2.0
    y0, y1 = y - h / 2.0, y + h / 2.0
    b = box(x0, y0, x1, y1)
    if angle % 360:
        from shapely import affinity
        b = affinity.rotate(b, -angle, origin=(x, y))
    return b


# ------------------------------------------------------------- symbol library

def _lib_graphics(sym) -> tuple[list, list]:
    """(graphic geometries, pin segments) of a lib symbol, in library coords."""
    graphics, pins = [], []
    for sub in [sym] + _kids(sym, "symbol"):
        for g in sub[1:] if isinstance(sub, list) else []:
            if not isinstance(g, list) or not g:
                continue
            h = _tok(g[0])
            if h == "rectangle":
                s, e = _nums(_kid(g, "start")), _nums(_kid(g, "end"))
                if len(s) >= 2 and len(e) >= 2:
                    graphics.append(box(min(s[0], e[0]), min(s[1], e[1]),
                                        max(s[0], e[0]), max(s[1], e[1])))
            elif h == "polyline":
                p = _pts(g)
                if len(p) >= 2:
                    graphics.append(LineString(p))
            elif h == "circle":
                c = _nums(_kid(g, "center"))
                r = _nums(_kid(g, "radius"))
                if len(c) >= 2 and r:
                    graphics.append(Point(c[0], c[1]).buffer(r[0], quad_segs=12))
            elif h == "arc":
                p = [_nums(_kid(g, k))[:2] for k in ("start", "mid", "end")
                     if _kid(g, k) is not None]
                if len(p) == 3:
                    graphics.append(LineString(p))
            elif h == "text":
                a = _nums(_kid(g, "at"))
                if len(a) >= 2 and len(g) > 1:
                    size, thick, just, _hid = _effects(g)
                    graphics.append(text_box(str(_tok(g[1])), (a[0], a[1]),
                                             size, thick, just))
            elif h == "pin":
                a = _nums(_kid(g, "at"))
                ln = _nums(_kid(g, "length"))
                if len(a) >= 3 and ln:
                    ang = math.radians(a[2])
                    pins.append(LineString([(a[0], a[1]),
                                            (a[0] + ln[0] * math.cos(ang),
                                             a[1] + ln[0] * math.sin(ang))]))
    return graphics, pins


def parse_lib_symbols(root) -> dict:
    out = {}
    block = _kid(root, "lib_symbols")
    if block is None:
        return out
    for sym in _kids(block, "symbol"):
        if len(sym) < 2:
            continue
        lib_id = str(_tok(sym[1]))
        graphics, pins = _lib_graphics(sym)
        out[lib_id] = {"graphics": graphics, "pins": pins,
                       "power": _yes(sym, "power") or lib_id.startswith("power:")}
    return out


# ------------------------------------------------------------------ transform

def to_page(lx: float, ly: float, at: tuple[float, float, float],
            mirror: str | None) -> tuple[float, float]:
    """Library coordinates (y up) -> page coordinates (y down).

    KiCad applies the instance ROTATION first, then the mirror across the
    page axis (T6 rotmirror fixture: kicad-cli ERC + netlist oracle over
    rot 0/90/180/270, mirror x/y and rot90+mirror x - the reversed order
    swaps the pins of a rotated+mirrored part; also verified against wire
    endpoints: a Device:C pin at library (0, +3.81) with the symbol at page
    (152.40, 240.03) is wired at page y 236.22 = 240.03-3.81).
    """
    ux, uy = lx, -ly
    a = math.radians(at[2])
    ca, sa = math.cos(a), math.sin(a)
    rx, ry = ux * ca + uy * sa, -ux * sa + uy * ca
    if mirror == "y":
        rx = -rx
    elif mirror == "x":
        ry = -ry
    return (at[0] + rx, at[1] + ry)


def _xform_geom(g, at, mirror):
    """Same composition as to_page: y-flip, rotate, THEN mirror (KiCad
    order - see to_page's fixture note); no-op difference when unmirrored."""
    from shapely import affinity
    g = affinity.scale(g, xfact=1, yfact=-1, origin=(0, 0))   # y up -> y down
    if at[2] % 360:
        g = affinity.rotate(g, -at[2], origin=(0, 0))
    if mirror == "y":
        g = affinity.scale(g, xfact=-1, yfact=1, origin=(0, 0))
    elif mirror == "x":
        g = affinity.scale(g, xfact=1, yfact=-1, origin=(0, 0))
    return affinity.translate(g, at[0], at[1])


# ---------------------------------------------------------------- sheet parse

class Sheet:
    """Everything on one .kicad_sch that a field has to stay clear of."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.text = self.path.read_text(encoding="utf-8")
        try:
            self.root = sexpdata.loads(self.text)
        except Exception as exc:  # noqa: BLE001
            raise RefdesError(f"{self.path.name}: unparsable schematic ({exc})") from exc
        if _head(self.root) != "kicad_sch":
            raise RefdesError(f"{self.path} is not a .kicad_sch")
        self.libs = parse_lib_symbols(self.root)
        self.symbols = self._parse_symbols()
        self.statics = self._parse_statics()

    # -- symbol instances
    def _parse_symbols(self) -> list[dict]:
        out = []
        for s in _kids(self.root, "symbol"):
            lib = _kid(s, "lib_id")
            at = _nums(_kid(s, "at"))
            if lib is None or len(at) < 2:
                continue
            mir = _kid(s, "mirror")
            props = {}
            for p in _kids(s, "property"):
                if len(p) < 3:
                    continue
                name = str(_tok(p[1]))
                pat = _nums(_kid(p, "at"))
                size, thick, just, hidden = _effects(p)
                props[name] = {"value": str(_tok(p[2])),
                               "at": (pat[0], pat[1]) if len(pat) >= 2 else None,
                               "angle": pat[2] if len(pat) >= 3 else 0.0,
                               "size": size, "thick": thick,
                               "justify": just, "hidden": hidden}
            ref = props.get("Reference", {}).get("value", "?")
            out.append({
                "ref": ref,
                "lib_id": str(_tok(lib[1])) if len(lib) > 1 else "",
                "at": (at[0], at[1], at[2] if len(at) >= 3 else 0.0),
                "mirror": str(_tok(mir[1])) if mir is not None and len(mir) > 1 else None,
                "props": props,
            })
        out.sort(key=lambda s: (_ref_key(s["ref"]), s["at"][0], s["at"][1]))
        return out

    # -- everything that is not a symbol field
    def _parse_statics(self) -> list[tuple[str, object, str]]:
        items: list[tuple[str, object, str]] = []
        for w in _kids(self.root, "wire") + _kids(self.root, "bus"):
            p = _pts(w)
            if len(p) >= 2:
                items.append(("wire", LineString(p).buffer(0.05), "wire"))
        for j in _kids(self.root, "junction"):
            a = _nums(_kid(j, "at"))
            if len(a) >= 2:
                items.append(("junction", Point(a[0], a[1]).buffer(0.5), "junction"))
        for nc in _kids(self.root, "no_connect"):
            a = _nums(_kid(nc, "at"))
            if len(a) >= 2:
                items.append(("no_connect", Point(a[0], a[1]).buffer(0.7), "no_connect"))
        for kind in ("label", "global_label", "hierarchical_label", "text"):
            for lb in _kids(self.root, kind):
                a = _nums(_kid(lb, "at"))
                if len(a) < 2 or len(lb) < 2:
                    continue
                txt = str(_tok(lb[1]))
                size, thick, just, hidden = _effects(lb)
                if hidden:
                    continue
                ang = a[2] if len(a) >= 3 else 0.0
                # an unjustified label hangs off its anchor along its own angle
                j = just or ("left" if ang in (0.0, 90.0) else "right")
                items.append((kind, text_box(txt, (a[0], a[1]), size, thick, j, ang),
                              f"{kind} {txt}"))
        for sh in _kids(self.root, "sheet"):
            a = _nums(_kid(sh, "at"))
            sz = _nums(_kid(sh, "size"))
            if len(a) >= 2 and len(sz) >= 2:
                items.append(("sheet", box(a[0], a[1], a[0] + sz[0], a[1] + sz[1]),
                              "sheet body"))
            for p in _kids(sh, "property"):
                pa = _nums(_kid(p, "at"))
                if len(p) >= 3 and len(pa) >= 2:
                    size, thick, just, hidden = _effects(p)
                    if not hidden:
                        items.append(("sheet_field",
                                      text_box(str(_tok(p[2])), (pa[0], pa[1]),
                                               size, thick, just or "left"),
                                      f"sheet field {_tok(p[1])}"))
            for sp in _kids(sh, "pin"):
                pa = _nums(_kid(sp, "at"))
                if len(sp) >= 2 and len(pa) >= 2:
                    size, thick, just, _h = _effects(sp)
                    items.append(("sheet_pin",
                                  text_box(str(_tok(sp[1])), (pa[0], pa[1]),
                                           size, thick, just or "left"),
                                  f"sheet pin {_tok(sp[1])}"))
        return items

    # -- per-symbol geometry
    def body(self, sym: dict):
        """Drawn extent of the symbol: graphics plus pin lines (no text)."""
        lib = self.libs.get(sym["lib_id"])
        if lib is None:
            return None
        geoms = [_xform_geom(g, sym["at"], sym["mirror"])
                 for g in lib["graphics"] + lib["pins"]]
        return unary_union(geoms) if geoms else None

    def body_obstacle(self, sym: dict):
        """What a field must clear: the body plus each pin's number band.

        KiCad prints the pin number alongside its pin line, outside the body.
        It is not in the library graphics, so a field cleared against pin LINES
        alone can still land on printed text - the band models it."""
        b = self.body(sym)
        if b is None:
            return None
        lib = self.libs[sym["lib_id"]]
        bands = [_xform_geom(seg, sym["at"], sym["mirror"]).buffer(PIN_TEXT_BAND)
                 for seg in lib["pins"] if seg.length > 0]
        return unary_union([b] + bands) if bands else b

    def pin_dirs(self, sym: dict) -> list[tuple[float, float]]:
        """Unit vectors, in page coords, of each pin's outward direction."""
        lib = self.libs.get(sym["lib_id"])
        if lib is None:
            return []
        out = []
        for seg in lib["pins"]:
            (x0, y0), (x1, y1) = seg.coords[0], seg.coords[-1]
            p0 = to_page(x0, y0, sym["at"], sym["mirror"])
            p1 = to_page(x1, y1, sym["at"], sym["mirror"])
            dx, dy = p0[0] - p1[0], p0[1] - p1[1]        # body -> connection end
            n = math.hypot(dx, dy)
            if n < 1e-9:                                  # zero-length pin
                continue
            out.append((dx / n, dy / n))
        return out

    def label_dir(self, sym: dict) -> tuple[float, float]:
        """Which way a power symbol's label goes: away from its connection.

        Power symbols carry a ZERO-length pin at the origin (verified on
        power:+5V / GND / PWR_FLAG), so there is no pin vector to read - the
        body itself points away from the wire, and the label follows it.
        """
        lib = self.libs.get(sym["lib_id"])
        b = self.body(sym)
        if lib is None or b is None or b.is_empty:
            return (0.0, -1.0)
        conn = [to_page(seg.coords[0][0], seg.coords[0][1], sym["at"], sym["mirror"])
                for seg in lib["pins"]]
        if not conn:
            return (0.0, -1.0)
        c = b.centroid
        dx, dy = c.x - conn[0][0], c.y - conn[0][1]
        n = math.hypot(dx, dy)
        if n < 1e-9:
            return (0.0, -1.0)
        return (dx / n, dy / n)


def _ref_key(ref: str):
    m = re.match(r"^([^\d]*)(\d*)", ref or "")
    return (m.group(1), int(m.group(2) or 0))


# ------------------------------------------------------------------ placement

def classify(sheet: Sheet, sym: dict) -> str:
    lib = sheet.libs.get(sym["lib_id"])
    if lib is not None and lib["power"]:
        return "power"
    npins = len(lib["pins"]) if lib else 0
    if npins == 2:
        dirs = sheet.pin_dirs(sym)
        if all(abs(d[0]) < 1e-6 for d in dirs):
            return "passive_v"
        if all(abs(d[1]) < 1e-6 for d in dirs):
            return "passive_h"
    return "block"


def candidates(sheet: Sheet, sym: dict, field: str, bbox) -> list[tuple[tuple[float, float], str | None]]:
    """Primary offset first, then a deterministic ladder of alternatives."""
    cx, cy = sym["at"][0], sym["at"][1]
    if bbox is None:
        x0, y0, x1, y1 = cx - 1.27, cy - 1.27, cx + 1.27, cy + 1.27
    else:
        x0, y0, x1, y1 = bbox
    first = field == "Reference"
    out: list[tuple[tuple[float, float], str | None]] = []
    kind = classify(sheet, sym)

    if kind == "power":
        # outboard of the body, i.e. away from the wire the symbol sits on
        dx, dy = sheet.label_dir(sym)
        for k in range(0, 6):
            step = GAP + k * STEP
            if abs(dy) >= abs(dx):
                y = (y1 + step) if dy > 0 else (y0 - step)
                out.append(((cx, y), None))
                for s in (+1, -1):               # rails packed side by side
                    out.append(((cx + s * STEP, y), "left" if s > 0 else "right"))
            else:
                x = (x1 + step) if dx > 0 else (x0 - step)
                out.append(((x, cy), "left" if dx > 0 else "right"))
                for s in (+1, -1):
                    out.append(((x, cy + s * STEP), "left" if dx > 0 else "right"))
        return out + _corners(x0, y0, x1, y1)

    if kind == "passive_h":
        # fields above and below: reference first (KiCad's own convention)
        prim = [((cx, y0 - GAP if first else y1 + GAP), None),
                ((cx, y1 + GAP if first else y0 - GAP), None)]
    else:
        # vertical passive, or a block: stack both fields off the right edge,
        # reference above the centre line
        dy = -STEP if first else STEP
        prim = [((x1 + GAP, cy + dy), "left"), ((x0 - GAP, cy + dy), "right")]
        if kind == "block":
            prim = [((cx, y0 - GAP), None), ((cx, y1 + GAP), None)] if first else \
                   [((cx, y1 + GAP), None), ((cx, y0 - GAP), None)]
    out.extend(prim)

    # ladder: slide the primary side along its free axis, then the alternates
    for (px, py), just in list(prim):
        for k in range(1, 5):
            for s in (+1, -1):
                if just is None:                 # above/below -> slide in x
                    out.append(((px + s * k * STEP, py), just))
                else:                            # side -> slide in y
                    out.append(((px, py + s * k * STEP), just))
    # A dense sheet stubs a wire and a label at EVERY pin, so the bands above
    # and below a block are crossed by wires the whole way along. The free
    # space is diagonally outside the corners, where no stub runs - which is
    # also where a human puts the label.
    out.extend(_corners(x0, y0, x1, y1))
    for k in range(2, 6):
        out.append(((x1 + GAP + k * STEP, cy), "left"))
        out.append(((x0 - GAP - k * STEP, cy), "right"))
        out.append(((cx, y0 - GAP - k * STEP), None))
        out.append(((cx, y1 + GAP + k * STEP), None))
    return out


def _corners(x0: float, y0: float, x1: float, y1: float, rings: int = 3):
    """Diagonally outboard of each corner, nearest ring first."""
    out = []
    for k in range(rings):
        d = GAP + k * STEP
        out.append(((x0 - d, y0 - d), "right"))     # above-left, growing left
        out.append(((x1 + d, y0 - d), "left"))      # above-right
        out.append(((x0 - d, y1 + d), "right"))     # below-left
        out.append(((x1 + d, y1 + d), "left"))      # below-right
    return out


def place_sheet(sheet: Sheet, clearance: float = CLEARANCE) -> dict:
    """Choose a position for every visible Reference/Value on the sheet."""
    obstacles = list(sheet.statics)
    bodies = {}
    for sym in sheet.symbols:
        bodies[id(sym)] = sheet.body(sym)
        ob = sheet.body_obstacle(sym)
        if ob is not None:
            obstacles.append(("symbol", ob, sym["ref"]))

    placements, residue = [], []
    for sym in sheet.symbols:
        b = bodies[id(sym)]
        bbox = b.bounds if b is not None else None
        for field in _FIELDS:
            p = sym["props"].get(field)
            if p is None or p["hidden"] or p["at"] is None or not p["value"]:
                continue
            chosen = None
            # ... and if no table position is free, the field's CURRENT spot is
            # still better than moving it onto something.
            ladder = candidates(sheet, sym, field, bbox) + [(p["at"], p["justify"])]
            for (pos, just) in ladder:
                tb = text_box(p["value"], pos, p["size"], p["thick"], just)
                probe = tb.buffer(clearance)
                hit = next((name for _k, g, name in obstacles if probe.intersects(g)), None)
                if hit is None:
                    chosen = (pos, just, tb)
                    break
            if chosen is None:
                # keep the current position; report what it hits
                tb = text_box(p["value"], p["at"], p["size"], p["thick"], p["justify"])
                hit = next((name for _k, g, name in obstacles
                            if tb.buffer(clearance).intersects(g)), None)
                residue.append({"ref": sym["ref"], "field": field,
                                "value": p["value"], "at": list(p["at"]),
                                "collides_with": hit})
                obstacles.append(("field", tb, f"{sym['ref']}.{field}"))
                continue
            pos, just, tb = chosen
            # Every placement is written with an EXPLICIT justification: a
            # "centered" choice becomes a left-justified anchor at the box's
            # left edge. Leaving justify unset would keep whatever the file
            # already had and shift the text half its width off the plan.
            if just is None:
                w = max(len(p["value"]), 1) * p["size"][0] * ADVANCE + p["thick"]
                pos, just = (pos[0] - w / 2.0, pos[1]), "left"
            obstacles.append(("field", tb, f"{sym['ref']}.{field}"))
            moved = (p["at"] is None
                     or abs(pos[0] - p["at"][0]) > 1e-6
                     or abs(pos[1] - p["at"][1]) > 1e-6
                     or (just or "") != (p["justify"] or ""))
            placements.append({"ref": sym["ref"], "field": field,
                               "value": p["value"], "class": classify(sheet, sym),
                               "from": list(p["at"]) if p["at"] else None,
                               "to": [round(pos[0], 4), round(pos[1], 4)],
                               "justify": just, "moved": bool(moved)})
    return {"placements": placements, "residue": residue}


def audit_sheet(sheet: Sheet, clearance: float = CLEARANCE) -> list[dict]:
    """Overlaps between visible fields and everything else, as the file stands."""
    items = list(sheet.statics)
    fields = []
    for sym in sheet.symbols:
        ob = sheet.body_obstacle(sym)
        if ob is not None:
            items.append(("symbol", ob, sym["ref"]))
        for field in _FIELDS:
            p = sym["props"].get(field)
            if p is None or p["hidden"] or p["at"] is None or not p["value"]:
                continue
            fields.append((f"{sym['ref']}.{field}",
                           text_box(p["value"], p["at"], p["size"], p["thick"],
                                    p["justify"], p["angle"])))
    out = []
    for i, (name, tb) in enumerate(fields):
        probe = tb.buffer(clearance)
        for _k, g, other in items:
            if probe.intersects(g):
                out.append({"field": name, "collides_with": other})
        for other_name, other_tb in fields[i + 1:]:
            if probe.intersects(other_tb):
                out.append({"field": name, "collides_with": other_name})
    return out


# -------------------------------------------------------------------- writing

def _register_project_libs(sch_path: Path) -> list[str]:
    """Point kicad-sch-api's symbol cache at the project's own libraries.

    Without this its save() re-serialises lib_symbols from the global cache and
    SILENTLY DROPS every symbol it cannot resolve - measured on pd-trigger:
    8879 lines in, 4359 out, the whole aiee library gone (LEARNINGS
    [kicad-sch-api]). With the libs registered the round trip is faithful.
    """
    import kicad_sch_api as ksa
    found = []
    roots = [sch_path.parent, sch_path.parent.parent]
    for root in roots:
        for lib in sorted(root.glob("*.kicad_sym")) + sorted(root.glob("lib/*.kicad_sym")):
            ksa.get_symbol_cache().add_library_path(str(lib))
            found.append(str(lib))
    return found


def _lib_symbol_names(text: str) -> set[str]:
    block = re.search(r"\(lib_symbols\b", text)
    if not block:
        return set()
    return set(re.findall(r'\(symbol "([^"]+)"\s*\n\s*\((?:pin_numbers|pin_names|'
                          r'exclude_from_sim|in_bom|power|property)', text))


_PRIVATE_PROP_RE = re.compile(r'\n[ \t]*\(property "private" ')


def strip_private_properties(sch_path: Path) -> int:
    """Drop `private` symbol properties from lib_symbols after a kicad-sch-api save.

    KiCad 10 symbol libraries (format 20251024, e.g. the 10.0.5 stock libs) write
    KLC notes as `(property private "KLC_S3.3" "text" ...)` - a bare `private`
    flag BEFORE the name. kicad-sch-api 0.5.6 reads the flag as the name, the
    name as the value and keeps the text as bare atoms, so its save() emits
    `(property "private" "KLC_S3.3" The rectangle is not ...` - not an
    s-expression, and kicad-cli 10.0.5 refuses the whole file ("Failed to load
    schematic", exit 3; Device:Crystal_GND24 is one of 419 such properties in
    the stock libraries). The notes carry no electrical meaning: remove each
    balanced block. Returns the number removed. LEARNINGS [kicad-sch-api][linux].
    """
    text = sch_path.read_text(encoding="utf-8")
    out: list[str] = []
    pos = 0
    n = 0
    for m in _PRIVATE_PROP_RE.finditer(text):
        if m.start() < pos:
            continue
        i = text.index("(", m.start())
        depth = 0
        j = i
        while j < len(text):
            c = text[j]
            if c == '"':                      # skip a quoted string (escapes included)
                j += 1
                while j < len(text) and text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(text[pos:m.start()])       # drop the newline before the block too
        pos = j + 1
        n += 1
    if not n:
        return 0
    out.append(text[pos:])
    sch_path.write_text("".join(out), encoding="utf-8")
    return n


def write_placements(sch_path: Path, result: dict) -> dict:
    """Apply the chosen positions through kicad-sch-api, then verify the file."""
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        import kicad_sch_api as ksa
        libs = _register_project_libs(sch_path)
        before = _lib_symbol_names(sch_path.read_text(encoding="utf-8"))
        sch = ksa.Schematic.load(str(sch_path))
        by_ref = {c.reference: c for c in sch.components}
        applied = 0
        for p in result["placements"]:
            if not p["moved"]:
                continue
            comp = by_ref.get(p["ref"])
            if comp is None:
                raise RefdesError(f"{p['ref']} not found by kicad-sch-api")
            eff = {"position": (p["to"][0], p["to"][1]), "rotation": 0.0}
            if p["justify"]:
                eff["justify_h"] = p["justify"]
            comp.set_property_effects(p["field"], eff)
            applied += 1
        if applied:
            sch.save()
    stripped = strip_private_properties(sch_path)   # KiCad-10 lib notes ksa mangles
    after = _lib_symbol_names(sch_path.read_text(encoding="utf-8"))
    if before - after:
        raise RefdesError(
            f"kicad-sch-api dropped {len(before - after)} lib_symbols entries "
            f"({sorted(before - after)[:3]}...) - the project symbol library was "
            f"not resolvable. Registered: {libs or 'none'}")
    return {"applied": applied, "libs_registered": libs,
            "private_props_stripped": stripped,
            "lib_symbols": len(after)}


# ------------------------------------------------------------------------ CLI

def run(argv: list[str] | None = None) -> tuple[dict, str | None]:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sch", nargs="+", required=True, help=".kicad_sch file(s)")
    ap.add_argument("--clearance", type=float, default=CLEARANCE,
                    help=f"gap a field must keep, mm (default {CLEARANCE})")
    ap.add_argument("--audit", action="store_true",
                    help="report overlaps as the files stand; change nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="choose positions and report them; write nothing")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    sheets, total_res, total_moved, total_placed = [], 0, 0, 0
    for path in args.sch:
        sheet = Sheet(Path(path))
        row: dict = {"sheet": str(path), "symbols": len(sheet.symbols)}
        row["overlaps_before"] = audit_sheet(sheet, args.clearance)
        if args.audit:
            sheets.append(row)
            total_res += len(row["overlaps_before"])
            continue
        res = place_sheet(sheet, args.clearance)
        row["placements"] = res["placements"]
        row["residue"] = res["residue"]
        row["moved"] = sum(1 for p in res["placements"] if p["moved"])
        total_placed += len(res["placements"])
        total_moved += row["moved"]
        total_res += len(res["residue"])
        if not args.dry_run:
            row["write"] = write_placements(Path(path), res)
            after = Sheet(Path(path))
            row["overlaps_after"] = audit_sheet(after, args.clearance)
            total_res += len(row["overlaps_after"])
        sheets.append(row)

    payload = {
        "script": "schem_refdes",
        "status": "residue" if total_res else "pass",
        "mode": "audit" if args.audit else ("dry-run" if args.dry_run else "apply"),
        "sheets": sheets,
        "counts": {"sheets": len(sheets), "fields": total_placed,
                   "moved": total_moved, "residue": total_res},
    }
    return payload, args.out


def main(argv: list[str] | None = None) -> int:
    try:
        payload, out = run(argv)
    except Exception as exc:  # noqa: BLE001 - contract: any error -> exit 2
        print(json.dumps({"script": "schem_refdes", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}, indent=1))
        return 2
    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if payload["status"] == "residue" else 0


if __name__ == "__main__":
    sys.exit(main())
