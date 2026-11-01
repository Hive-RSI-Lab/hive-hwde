#!/usr/bin/env python
"""schem_layout.py - redraw a generated schematic so a person can read it.

schlib's generators give every pin a short stub and a net label, and put parts
in whatever grid the generator chose, so the sheet says which pin goes where
only through matching text. This pass redraws a finished .kicad_sch:

- each IC or connector is a block, with its passives around it: series parts in
  line with the pin they serve, shunts hanging off that line, decoupling caps
  in banks above it, one bank per rail, on a shared rail and ground wire;
- nets inside a block, and short nets between blocks, are drawn as real wires
  (a maze router, so wires go around parts and cross as little as they can);
- rails end in their power symbols, ground down and supplies up;
- blocks run left to right by signal direction;
- only Reference and Value stay visible.

A net that cannot be wired sensibly, or that spreads over many blocks, keeps
its labels: that is what a person would draw for an SPI bus too.

It changes the drawing and nothing else. Every symbol keeps its UUID (the PCB's
footprint paths use them), every net keeps every name it had, and the run is
gated on the kicad-cli netlist: same net names, same pins on each, same part
stamps. A sheet that fails the gate is left as it was, and if ERC finds more
errors than before, all of them are, so a bad redraw can't reach a board. Sheets
that a person drew (real wires already), and root sheets with sheet boxes, are
skipped.

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2.
  0 = every sheet redrawn or rightly skipped, netlist identical
  1 = at least one sheet failed its gate and was left as it was
  2 = internal error / bad arguments

Examples:
  schem_layout.py --sch boards/b/kicad/b.kicad_sch               # in place
  schem_layout.py --sch b.kicad_sch --out-dir /tmp/view          # a redrawn copy
"""
from __future__ import annotations

import argparse
import heapq
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import sexpdata  # noqa: E402

import env  # noqa: E402
import schem_refdes as sr  # noqa: E402

G = 1.27                 # the schematic grid, mm
STUB = 2.54              # pin stub
PART_GAP = 5.08          # between neighbours on a strip
ROW_GAP = 2.54           # between strips
BLOCK_GAP = 15.24        # between blocks
BANK_GAP = 7.62          # between decoupling banks
TEXT_W = 1.1             # mm per character, as kicad-cli draws 1.27 mm text
NS = uuid.UUID("6f0c2f9e-52a5-4c3e-9d7f-3b1d3c1e7a11")

MAX_LABEL_PINS = 5       # a net on more pins than this between blocks keeps labels
MAX_WIRE_MM = 90.0       # ... and so does one longer than this
MAX_CROSSINGS = 2        # ... or one that would cross more wires than this

_GND = re.compile(r"(^|[/_.])(A|D|P|S|C|E|R|H)?(GND|VSS|VEE|0V)([A-Z0-9_]*)$", re.I)
_RAIL = re.compile(r"^(\+|-)?\d+V\d*|^\+|^V(CC|DD|BUS|IN|BAT|SYS|REF|OUT|S)\b"
                   r"|^V(CC|DD)|^(\+?)\d+V\d*", re.I)
_NOT_RAIL = re.compile(r"(^|[/_])(SW|LX|PH|PHASE|BST|BOOT|HO|LO|FB|COMP|SS|EN)\d*$", re.I)
_SIDES = {(-1, 0): "L", (1, 0): "R", (0, -1): "T", (0, 1): "B"}


class LayoutError(RuntimeError):
    """This sheet can't be redrawn; it stays as it was."""


def snap(v: float, g: float = G) -> float:
    return round(round(v / g) * g, 4)


def cell(p) -> tuple[int, int]:
    return (round(p[0] / G), round(p[1] / G))


def pos(c) -> tuple[float, float]:
    return (round(c[0] * G, 4), round(c[1] * G, 4))


def fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _uuid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def text_w(s: str) -> float:
    return max(len(s), 1) * TEXT_W + 0.3


# ------------------------------------------------------------------ text forms

def forms(text: str, start: int = 0, end: int | None = None):
    """(head, open, close) of each top-level form of text[start:end]."""
    end = len(text) if end is None else end
    depth, i, open_at = 0, start, -1
    while i < end:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < end and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "(":
            if depth == 0:
                open_at = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                m = re.match(r"\((\S+)", text[open_at:open_at + 40])
                yield (m.group(1) if m else "", open_at, i + 1)
        i += 1


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------- parse

class Pin:
    __slots__ = ("num", "name", "type", "lx", "ly", "ang", "unit", "hidden")

    def __init__(self, num, name, typ, lx, ly, ang, unit, hidden):
        self.num, self.name, self.type = num, name, typ
        self.lx, self.ly, self.ang = lx, ly, ang
        self.unit, self.hidden = unit, hidden


def _lib_pins(sym) -> list[Pin]:
    """The pins of an embedded library symbol, with their unit (0 = all)."""
    out = []
    name = str(sr._tok(sym[1]))
    short = name.split(":")[-1]
    for sub in sr._kids(sym, "symbol"):
        sub_name = str(sr._tok(sub[1]))
        m = re.match(re.escape(short) + r"_(\d+)_(\d+)$", sub_name)
        unit = int(m.group(1)) if m else 0
        if m and int(m.group(2)) > 1:   # a De Morgan alternate draws the same pins
            continue
        for p in sr._kids(sub, "pin"):
            at = sr._nums(sr._kid(p, "at"))
            nm = sr._kid(p, "name")
            no = sr._kid(p, "number")
            hidden = sr._yes(p, "hide") or any(
                sr._tok(x) == "hide" for x in p[1:] if not isinstance(x, list))
            out.append(Pin(str(sr._tok(no[1])) if no else "?",
                           str(sr._tok(nm[1])) if nm else "",
                           str(sr._tok(p[1])), at[0], at[1],
                           at[2] if len(at) > 2 else 0.0, unit, hidden))
    return out


class Sym:
    """One placed symbol: where it was, where it goes, what its pins do."""

    def __init__(self, raw, form_text, libs_pins, libs_geo):
        self.text = form_text
        self.ref = "?"
        self.value = ""
        for p in sr._kids(raw, "property"):
            k = str(sr._tok(p[1]))
            if k == "Reference":
                self.ref = str(sr._tok(p[2]))
            elif k == "Value":
                self.value = str(sr._tok(p[2]))
        self.lib_id = str(sr._tok(sr._kid(raw, "lib_id")[1]))
        at = sr._nums(sr._kid(raw, "at"))
        self.at = (at[0], at[1], at[2] if len(at) > 2 else 0.0)
        mir = sr._kid(raw, "mirror")
        self.mirror = str(sr._tok(mir[1])) if mir is not None else None
        u = sr._kid(raw, "unit")
        self.unit = int(sr._nums(u)[0]) if u is not None else 1
        self.uuid = str(sr._tok(sr._kid(raw, "uuid")[1]))
        self.pins = [p for p in libs_pins.get(self.lib_id, [])
                     if p.unit in (0, self.unit)]
        self.geo = libs_geo.get(self.lib_id)
        self.new_at: tuple | None = None
        self.placed = False
        self.is_power = self.lib_id.startswith("power:") or self.ref.startswith(("#PWR", "#FLG"))
        self.is_flag = self.is_power and "PWR_FLAG" in self.lib_id

    def key(self):
        return (self.ref, self.unit)

    def visible_pins(self):
        return [p for p in self.pins if not p.hidden or self.is_power]

    def pin_page(self, pin: Pin, at=None, mirror="keep"):
        at = at or self.new_at or self.at
        mir = self.mirror if mirror == "keep" else mirror
        return sr.to_page(pin.lx, pin.ly, at, mir)

    def pin_out(self, pin: Pin, at=None) -> tuple[int, int]:
        """Page direction a wire leaves this pin."""
        import math
        a = math.radians(pin.ang)
        at = at or self.new_at or self.at
        tip = sr.to_page(pin.lx, pin.ly, at, self.mirror)
        inner = sr.to_page(pin.lx + math.cos(a), pin.ly + math.sin(a), at, self.mirror)
        return (round(tip[0] - inner[0]), round(tip[1] - inner[1]))

    def body_box(self, at=None):
        """(x0, y0, x1, y1) of the graphics and pins at this placement."""
        at = at or self.new_at or self.at
        pts = []
        if self.geo:
            for g in self.geo["graphics"] + self.geo["pins"]:
                x0, y0, x1, y1 = sr._xform_geom(g, at, self.mirror).bounds
                pts += [(x0, y0), (x1, y1)]
        if not pts:
            pts = [(at[0], at[1])]
        return (min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts))


class Net:
    def __init__(self, nid):
        self.id = nid
        self.pins: list[tuple[Sym, Pin]] = []
        self.locals: list[str] = []
        self.globals: list[tuple[str, str]] = []
        self.hiers: list[tuple[str, str]] = []
        self.power: list[Sym] = []
        self.flags: list[Sym] = []
        self.ground = False
        self.rail = False
        self.anchored = False

    def names(self):
        return (self.locals + [g[0] for g in self.globals] + [h[0] for h in self.hiers]
                + [s.value for s in self.power])

    def name(self) -> str:
        n = self.names()
        return n[0] if n else f"#{self.id}"


class Sheet:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.text = self.path.read_text(encoding="utf-8")
        self.root = sexpdata.loads(self.text)
        block = sr._kid(self.root, "lib_symbols")
        libs_pins = {}
        for s in (sr._kids(block, "symbol") if block else []):
            libs_pins[str(sr._tok(s[1]))] = _lib_pins(s)
        self.geo = sr.parse_lib_symbols(self.root)
        top = next(forms(self.text))
        self.body = (top[1] + 1, top[2] - 1)
        self.top_forms = list(forms(self.text, *self.body))
        raw_syms = sr._kids(self.root, "symbol")
        sym_texts = [self.text[a:b] for h, a, b in self.top_forms if h == "symbol"]
        if len(raw_syms) != len(sym_texts):
            raise LayoutError("symbol forms don't line up")
        self.syms = [Sym(r, t, libs_pins, self.geo) for r, t in zip(raw_syms, sym_texts)]
        self.wires = [sr._pts(w) for w in sr._kids(self.root, "wire")]
        self.has_sheets = bool(sr._kids(self.root, "sheet"))
        self.has_bus = bool(sr._kids(self.root, "bus"))
        self.nets: list[Net] = []
        self.pin_net: dict[tuple, Net] = {}
        self.nc_pins: list[tuple[Sym, Pin]] = []
        self._connect()

    def _connect(self):
        parent: dict = {}

        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            parent[find(a)] = find(b)

        segs = []
        for pts in self.wires:
            for a, b in zip(pts, pts[1:]):
                ca, cb = cell(a), cell(b)
                segs.append((ca, cb))
                union(("c", ca), ("c", cb))

        def on_wires(c):
            """A point joins every wire it touches."""
            for a, b in segs:
                if (a[0] == b[0] == c[0] and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])) or \
                   (a[1] == b[1] == c[1] and min(a[0], b[0]) <= c[0] <= max(a[0], b[0])):
                    union(("c", c), ("c", a))

        # a wire's end on another wire's span is a connection (a crossing isn't)
        for a, b in segs:
            on_wires(a)
            on_wires(b)
        for j in sr._kids(self.root, "junction"):
            on_wires(cell(sr._nums(sr._kid(j, "at"))))

        tags = defaultdict(list)
        for kind in ("label", "global_label", "hierarchical_label"):
            for lb in sr._kids(self.root, kind):
                c = cell(sr._nums(sr._kid(lb, "at")))
                on_wires(c)
                name = str(sr._tok(lb[1]))
                shape = sr._kid(lb, "shape")
                shape = str(sr._tok(shape[1])) if shape is not None else ""
                tags[("c", c)].append((kind, name, shape))
                # the same name on one sheet is one net
                union(("c", c), (kind, name))

        attach = []
        for s in self.syms:
            for p in s.visible_pins():
                c = cell(s.pin_page(p))
                on_wires(c)
                attach.append((("c", c), s, p))
                if s.is_power and not s.is_flag:
                    union(("c", c), ("power", s.value))

        by_root: dict = {}

        def net_of(key):
            r = find(key)
            if r not in by_root:
                by_root[r] = Net(len(by_root))
                self.nets.append(by_root[r])
            return by_root[r]

        for key, items in sorted(tags.items(), key=lambda kv: kv[0][1]):
            n = net_of(key)
            for kind, name, shape in items:
                if kind == "label" and name not in n.locals:
                    n.locals.append(name)
                elif kind == "global_label" and (name, shape) not in n.globals:
                    n.globals.append((name, shape))
                elif kind == "hierarchical_label" and (name, shape) not in n.hiers:
                    n.hiers.append((name, shape))

        nc = {cell(sr._nums(sr._kid(x, "at"))) for x in sr._kids(self.root, "no_connect")}
        # a hidden pin isn't drawn on, but its no-connect flag is still wanted
        for s in self.syms:
            if not s.is_power:
                for p in s.pins:
                    if p.hidden and cell(s.pin_page(p)) in nc:
                        self.nc_pins.append((s, p))
        for key, s, p in attach:
            n = net_of(key)
            if s.is_flag:
                n.flags.append(s)
            elif s.is_power:
                n.power.append(s)
            elif key[1] in nc:
                self.nc_pins.append((s, p))
            else:
                n.pins.append((s, p))
                self.pin_net[(s.key(), p.num)] = n

        self.nets = [n for n in self.nets if n.pins or n.power or n.flags or n.names()]
        for n in self.nets:
            # a local label named like the net's port or symbol adds nothing
            stronger = {x[0] for x in n.hiers + n.globals} | {s.value for s in n.power}
            n.locals = [x for x in n.locals if x not in stronger]
        for n in self.nets:
            names = n.names()
            n.ground = any(_GND.search(x) for x in names) or \
                any("GND" in s.lib_id.upper() for s in n.power)
            n.rail = n.ground or bool(n.power) or any(_RAIL.search(x) for x in names)
        # a supply no symbol or name gives away: several parts draw on it, or it
        # is bypassed to ground. One power pin alone may be a switch node or a
        # bootstrap, which a datasheet types as power but a person draws as signal.
        for n in self.nets:
            if n.rail or any(_NOT_RAIL.search(x) for x in n.names()):
                continue
            power = sum(1 for s, p in n.pins
                        if p.type in ("power_in", "power_out") and len(s.pins) > 2)
            if power >= 2:
                n.rail = True
            elif power == 1:
                for s, p in n.pins:
                    if len(s.pins) == 2:
                        o = next((q2 for q2 in s.pins if q2 is not p), None)
                        other = self.pin_net.get((s.key(), o.num)) if o else None
                        if other is not None and other.ground:
                            n.rail = True

    def hand_drawn(self) -> bool:
        """True when wires already join pins: nothing here for this pass."""
        pins = defaultdict(int)
        for s in self.syms:
            if s.is_power:
                continue
            for p in s.visible_pins():
                pins[cell(s.pin_page(p))] += 1
        joined = 0
        for pts in self.wires:
            ends = [cell(pts[0]), cell(pts[-1])]
            if sum(1 for e in ends if e in pins) == 2:
                joined += 1
        # the generators never tee a wire or run one pin to another
        return bool(sr._kids(self.root, "junction")) or joined > 0.05 * len(self.wires)


# --------------------------------------------------------------------- drawing

class Drawing:
    """What a redraw puts on the sheet, in page coordinates."""

    def __init__(self, sheet: Sheet, power_base: int):
        self.sheet = sheet
        self.power_i = power_base
        self.clones: list[str] = []
        self.wires: dict = {}        # net id -> set of (cell, cell) unit steps
        self.labels: list[dict] = []
        self.ncs: list[tuple] = []
        self.blocked: dict = {}      # cell -> kind
        self.pin_cells: dict = {}    # cell -> net id (a pin: others may not touch)
        self.hwires: dict = {}       # cell -> net id, a horizontal wire through it
        self.vwires: dict = {}
        self.nodes: dict = {}        # cell -> net id: wire ends, bends, pins
        self.power_pins: set = set()

    # -- obstacles
    def block_box(self, box, kind="body", pad=0.0):
        x0, y0, x1, y1 = box
        cx0, cy0 = cell((x0 - pad, y0 - pad))
        cx1, cy1 = cell((x1 + pad, y1 + pad))
        for x in range(cx0, cx1 + 1):
            for y in range(cy0, cy1 + 1):
                self.blocked.setdefault((x, y), kind)

    def block_text(self, s: str, at, justify: str | None, vertical=False):
        w, h = text_w(s), 1.27
        x, y = at
        if vertical:
            if justify == "left":     # runs upward
                self.block_box((x - h / 2, y - w, x + h / 2, y), "text")
            elif justify == "right":
                self.block_box((x - h / 2, y, x + h / 2, y + w), "text")
            else:
                self.block_box((x - h / 2, y - w / 2, x + h / 2, y + w / 2), "text")
            return
        if justify == "left":
            self.block_box((x, y - h / 2, x + w, y + h / 2), "text")
        elif justify == "right":
            self.block_box((x - w, y - h / 2, x, y + h / 2), "text")
        else:
            self.block_box((x - w / 2, y - h / 2, x + w / 2, y + h / 2), "text")

    # -- wires
    def add_path(self, net: Net, cells: list[tuple]) -> None:
        steps = self.wires.setdefault(net.id, set())
        for a, b in zip(cells, cells[1:]):
            steps.add((min(a, b), max(a, b)))
            table = self.hwires if a[1] == b[1] else self.vwires
            lo, hi = min(a, b), max(a, b)
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    table[(x, y)] = net.id
        for c in (cells[0], cells[-1]):
            self.nodes[c] = net.id
        for a, b, d in zip(cells, cells[1:], cells[2:]):
            if (a[0] == b[0]) != (b[0] == d[0]):
                self.nodes[b] = net.id

    def wire(self, net: Net, p, q2) -> None:
        """A straight wire; the caller knows this run is clear."""
        a, b = cell(p), cell(q2)
        if a == b:
            return
        path = [a]
        dx = (b[0] > a[0]) - (b[0] < a[0])
        dy = (b[1] > a[1]) - (b[1] < a[1])
        c = a
        while c != b:
            c = (c[0] + dx, c[1] + dy)
            path.append(c)
        self.add_path(net, path)

    # -- net ends
    def label(self, kind: str, name: str, at, d: tuple[int, int], shape: str = "") -> None:
        ang = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[d]
        just = "left" if ang in (0, 90) else "right"
        self.labels.append({"kind": kind, "name": name, "at": at, "ang": ang,
                            "just": just, "shape": shape})
        off = {"label": 0.0, "hierarchical_label": 2.54, "global_label": 2.54}[kind]
        tx = (at[0] + d[0] * off, at[1] + d[1] * off)
        self.block_text(name, tx, just, vertical=ang in (90, 270))

    def power_clone(self, proto: Sym, at, d: tuple[int, int]) -> None:
        """Another copy of this rail's power symbol, its pin at `at`, its body
        running along d."""
        self.power_i += 1
        ref = f"#PWR{self.power_i}"
        lx, ly = 0.0, 0.0
        if proto.pins:
            lx, ly = proto.pins[0].lx, proto.pins[0].ly
        down = _power_points_down(proto)
        # the angle that points the body along d
        if down:
            ang = {(0, 1): 0, (1, 0): 90, (0, -1): 180, (-1, 0): 270}[d]
        else:
            ang = {(0, -1): 0, (-1, 0): 90, (0, 1): 180, (1, 0): 270}[d]
        ox, oy = sr.to_page(lx, ly, (0.0, 0.0, float(ang)), None)
        sym_at = (round(at[0] - ox, 4), round(at[1] - oy, 4), float(ang))
        # the text sits past the end of the body
        reach = 5.08
        tx, ty = at[0] + d[0] * reach, at[1] + d[1] * reach
        just = {(1, 0): "left", (-1, 0): "right"}.get(d)
        if d[1] == -1:
            ty = at[1] - 4.57
        if d[1] == 1:
            ty = at[1] + 5.08
        self.power_pins.add(cell(at))
        self.clones.append(_clone_text(proto, ref, sym_at, (tx, ty), just, self.sheet.path.stem,
                                       self.power_i))
        ext = 3.81
        x0, x1 = sorted((at[0], at[0] + d[0] * ext))
        y0, y1 = sorted((at[1], at[1] + d[1] * ext))
        self.block_box((x0 - 1.27 * abs(d[1]), y0 - 1.27 * abs(d[0]),
                        x1 + 1.27 * abs(d[1]), y1 + 1.27 * abs(d[0])), "power")
        self.block_text(proto.value, (tx, ty), just)

    def drop(self, net: Net, at, d: tuple[int, int]) -> None:
        """End this wire on the net's own symbol or name. The first end on a
        net carries every name it has, so none is lost and all stay one net."""
        extra = []
        self.nodes[cell(at)] = net.id
        if not net.anchored:
            net.anchored = True
            power_vals = {s.value for s in net.power}
            extra = [("label", n, "") for n in net.locals if n not in power_vals]
            extra += [("global_label", n, sh) for n, sh in net.globals]
            extra += [("hierarchical_label", n, sh) for n, sh in net.hiers]
            if net.power:
                # a label sharing the symbol's run carries the local names
                pass
        # further names sit along a run out to the end, so nothing branches off
        # sideways into a neighbouring pin's stub
        p = at
        last = None if net.power else (extra[-1] if extra else None)
        for kind, name, shape in (extra if net.power else extra[:-1]):
            nxt = (round(p[0] + d[0] * STUB * 2, 4), round(p[1] + d[1] * STUB * 2, 4))
            self.wire(net, p, nxt)
            self.nodes[cell(p)] = net.id
            self.label(kind, name, p, (0, -1) if d[0] else (1, 0), shape)
            p = nxt
        if net.power:
            self.power_clone(net.power[0], p, d)
            return
        if last is None:
            if net.locals:
                self.label("label", net.locals[0], p, d)
            elif net.hiers:
                self.label("hierarchical_label", net.hiers[0][0], p, d, net.hiers[0][1])
            elif net.globals:
                self.label("global_label", net.globals[0][0], p, d, net.globals[0][1])
            else:
                raise LayoutError(f"net {net.id} needs a name to end on and has none")
            return
        self.nodes[cell(p)] = net.id
        self.label(last[0], last[1], p, d, last[2])


def _power_points_down(proto: Sym) -> bool:
    """Whether this power symbol's body hangs below its pin at angle 0."""
    if proto.geo and proto.pins:
        p = proto.pins[0]
        boxes = [g.bounds for g in proto.geo["graphics"]]
        if boxes:
            cy = (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2
            return cy < p.ly   # library y is up
    return "GND" in proto.lib_id.upper()


def _clone_text(proto: Sym, ref: str, at, text_at, just, stem: str, n: int) -> str:
    t = proto.text
    t = re.sub(r'\(reference "[^"]*"\)', f'(reference {q(ref)})', t)
    head_end = t.find('(property')
    head = re.sub(r'\(at \S+ \S+(?: \S+)?\)', f'(at {fmt(at[0])} {fmt(at[1])} {fmt(at[2])})',
                  t[:head_end], count=1)
    t = head + t[head_end:]
    t = _rewrite_property(t, "Reference", ref, at[:2], None, True, at[2])
    t = _rewrite_property(t, "Value", proto.value, text_at, just, False, at[2])
    # every uuid in the clone is its own, and stable from run to run
    i = 0

    def fresh(_m):
        nonlocal i
        i += 1
        return f'(uuid "{_uuid(stem, ref, i)}")'
    return re.sub(r'\(uuid "[^"]*"\)', fresh, t)


def _rewrite_property(t: str, field: str, value, at, just, hide: bool,
                      sym_angle: float = 0.0) -> str:
    """Replace one property form of a symbol's text."""
    m = re.search(r'\(property "' + re.escape(field) + r'"', t)
    if m is None:
        return t
    for _h, a, b in forms(t, m.start()):
        repl = _property_form(field, value, at, just, hide, sym_angle)
        return t[:a] + repl + t[b:]
    return t


def _property_form(field, value, at, just, hide, sym_angle: float = 0.0) -> str:
    """KiCad turns a field with its symbol, and its justification with it, so
    a field on a turned symbol takes 90 degrees to read level, and every field
    is written centred on the box the plan wants: then the turn can't move it."""
    x, y = at
    w = text_w(value)
    if just == "left":
        x += w / 2
    elif just == "right":
        x -= w / 2
    ang = 90 if sym_angle % 180 else 0
    h = " (hide yes)" if hide else ""
    return (f'(property {q(field)} {q(value)} (at {fmt(x)} {fmt(y)} {ang}) '
            f'(effects (font (size 1.27 1.27)){h}))')


# ------------------------------------------------------------------- placement

def passive_like(s: Sym) -> bool:
    """Two pins, opposite each other: can be turned and laid in a line."""
    pins = s.visible_pins()
    if len(pins) != 2 or s.mirror:
        return False
    a, b = s.pin_out(pins[0]), s.pin_out(pins[1])
    return a == (-b[0], -b[1])


def _ref_num(ref: str) -> int:
    m = re.search(r"(\d+)", ref)
    return int(m.group(1)) if m else 0


def _turn(s: Sym, pin: Pin, want: tuple[int, int], at_xy) -> tuple:
    """The rotation of a two-pin part that points `pin` along `want`,
    with that pin at at_xy. Returns the new (x, y, angle)."""
    for ang in (0.0, 90.0, 180.0, 270.0):
        if s.pin_out(pin, (0.0, 0.0, ang)) == want:
            ox, oy = sr.to_page(pin.lx, pin.ly, (0.0, 0.0, ang), s.mirror)
            return (round(at_xy[0] - ox, 4), round(at_xy[1] - oy, 4), ang)
    raise LayoutError(f"{s.ref}: no rotation points pin {pin.num} along {want}")


def _span(s: Sym) -> float:
    a, b = s.visible_pins()
    return round(abs(a.lx - b.lx) + abs(a.ly - b.ly), 4)


class Block:
    """A part and what hangs off it, drawn around (0, 0), moved in one piece."""

    def __init__(self, title: str):
        self.title = title
        self.parts: list[tuple[Sym, tuple]] = []  # local placements
        self.ops: list = []                       # deferred drawing, local coords
        self.box = [0.0, 0.0, 0.0, 0.0]
        self.keys: list = []                      # (signal) nets touched
        self.syms: list[Sym] = []

    def grow(self, box):
        self.box = [min(self.box[0], box[0]), min(self.box[1], box[1]),
                    max(self.box[2], box[2]), max(self.box[3], box[3])]

    def place(self, s: Sym, at: tuple) -> None:
        s.new_at = at
        self.parts.append((s, at))
        self.syms.append(s)
        b = s.body_box(at)
        self.grow(b)
        pad = 1.27
        self.grow((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))
        vertical = s_vertical(s, at)
        if passive_like(s):
            if vertical:
                w = max(text_w(s.ref), text_w(s.value)) + 2.54
                self.grow((b[0], b[1], b[2] + w, b[3]))
            else:
                self.grow((b[0], b[1] - 3.81, b[2], b[3] + 3.81))
        else:
            w = text_w(s.value) + 2.54
            self.grow((b[0], b[1] - 5.08, max(b[2], b[0] + w), b[3] + 5.08))


def s_vertical(s: Sym, at) -> bool:
    pins = s.visible_pins()
    return bool(pins) and s.pin_out(pins[0], at)[0] == 0


def _other(sheet: Sheet, s: Sym, pin: Pin):
    pins = s.visible_pins()
    o = pins[1] if pins[0] is pin else pins[0]
    return o, sheet.pin_net.get((s.key(), o.num))


def _passives_on(net: Net, used: set) -> list[tuple[Sym, Pin]]:
    out = [(s, p) for s, p in net.pins
           if s.key() not in used and passive_like(s)]
    out.sort(key=lambda sp: _ref_key(sp[0].ref))
    return out


def _ref_key(ref: str):
    m = re.match(r"([A-Za-z#]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


class Strip:
    """One line of parts leaving a pin (or a block's label)."""

    def __init__(self):
        self.items: list = []   # ("series"|"up"|"down", sym, inner pin, next net)
        self.up = 3.81
        self.down = 3.81


def _build_strips(sheet: Sheet, net: Net, used: set) -> list[Strip]:
    """Everything reachable from a signal net through two-pin parts."""
    strip = Strip()
    extra: list[Strip] = []
    cur = net
    seen = {net.id}
    while cur is not None:
        nxt = None
        for s, p in _passives_on(cur, used):
            o, other = _other(sheet, s, p)
            if other is None or other.rail:
                used.add(s.key())
                # a shunt to a supply goes up, to ground goes down
                up = other is not None and not other.ground
                strip.items.append(("up" if up else "down", s, p, other))
                if up:
                    strip.up = max(strip.up, _span(s) + 10.16)
                else:
                    strip.down = max(strip.down, _span(s) + 8.89)
        for s, p in _passives_on(cur, used):
            o, other = _other(sheet, s, p)
            used.add(s.key())
            if other is not None and other.id not in seen and nxt is None:
                seen.add(other.id)
                strip.items.append(("series", s, p, other))
                nxt = other
            else:
                # a second branch off this net gets its own line below
                b = Strip()
                b.items.append(("series", s, p, other))
                if other is not None and other.id not in seen:
                    seen.add(other.id)
                    extra.extend(_build_strips(sheet, other, used))
                extra.append(b)
        cur = nxt
    return [strip] + extra


def _lay_strip(block: Block, strip: Strip, x0: float, y: float, side: int,
               sheet: Sheet) -> float:
    """Lay a strip outward from x0 along row y. Returns the far x."""
    x = x0
    for kind, s, p, other in strip.items:
        if kind == "series":
            sp = _span(s)
            # the text sits centred over the part: it may be wider than the part
            pad = snap(max(0.0, (max(text_w(s.ref), text_w(s.value)) - sp) / 2 + 1.27), 2.54)
            x = round(x + side * pad, 4)
            at = _turn(s, p, (-side, 0), (x, y))
            block.place(s, at)
            x = round(x + side * sp, 4)
            block.grow((min(x0, x), y - 3.81, max(x0, x), y + 3.81))
            x = round(x + side * (PART_GAP + pad), 4)
        else:
            want = (0, 1) if kind == "up" else (0, -1)  # the signal pin's way out
            at = _turn(s, p, want, (x, y))
            block.place(s, at)
            o = next(q2 for q2 in s.visible_pins() if q2 is not p)
            if other is not None:
                block.ops.append(("drop", other, _local_pin(s, o, at),
                                  (0, -1) if kind == "up" else (0, 1)))
                reach = -13.97 if kind == "up" else 13.97
                ep = _local_pin(s, o, at)
                block.grow((ep[0] - 2.54, min(ep[1], ep[1] + reach), ep[0] + 2.54,
                            max(ep[1], ep[1] + reach)))
            w = max(text_w(s.ref), text_w(s.value)) + 3.81
            x = round(x + side * max(snap(w, 2.54), 7.62), 4)
    return x


def _local_pin(s: Sym, pin: Pin, at) -> tuple:
    x, y = sr.to_page(pin.lx, pin.ly, at, s.mirror)
    return (round(x, 4), round(y, 4))


def _bank_owner(sheet: Sheet, cap: Sym, rail: Net, owners: dict) -> Sym | None:
    """The part this cap decouples: the closest ref on the same rail."""
    best = None
    for s, p in rail.pins:
        if s.key() in owners and p.type in ("power_in", "power_out", "passive"):
            score = (p.type == "passive", abs(_ref_num(s.ref) - _ref_num(cap.ref)),
                     _ref_key(s.ref))
            if best is None or score < best[0]:
                best = (score, s)
    return best[1] if best else None


def _lay_bank(block: Block, caps: list[tuple[Sym, Pin, Pin]], rail: Net, gnd: Net,
              x0: float, y_top: float) -> float:
    """Decoupling caps in a row: rail wire along the top, ground along the bottom."""
    x = x0 + 5.08
    xs = []
    y_bot = y_top
    for s, top, bot in caps:
        at = _turn(s, top, (0, -1), (x, y_top))
        block.place(s, at)
        y_bot = max(y_bot, _local_pin(s, bot, at)[1])
        xs.append((x, s, bot, at))
        w = max(text_w(s.ref), text_w(s.value)) + 3.81
        x = round(x + max(snap(w, 2.54), 7.62), 4)
    last = xs[-1][0]
    block.ops.append(("wire", rail, (x0, y_top), (last, y_top)))
    block.ops.append(("drop", rail, (x0, y_top), (0, -1)))
    for bx, s, bot, at in xs:
        p = _local_pin(s, bot, at)
        if p[1] != y_bot:
            block.ops.append(("wire", gnd, p, (bx, y_bot)))
    block.ops.append(("wire", gnd, (x0, y_bot), (last, y_bot)))
    block.ops.append(("drop", gnd, (x0, y_bot), (0, 1)))
    block.grow((x0 - text_w(rail.name()) / 2 - 1.27, y_top - 7.62, last + 7.62, y_bot + 7.62))
    return last + BANK_GAP


def _layout_part(sheet: Sheet, part: Sym, used: set, banks: dict) -> Block:
    """A multi-pin part with its strips and banks."""
    block = Block(part.ref)
    at = (0.0, 0.0, part.at[2])
    block.place(part, at)
    box0 = part.body_box(at)

    sides: dict = {"L": [], "R": [], "T": [], "B": []}
    for p in part.visible_pins():
        d = part.pin_out(p, at)
        sides.setdefault(_SIDES.get(d, "R"), []).append(p)

    nc = {(s.key(), p.num) for s, p in sheet.nc_pins}
    # the strips, in pin order, so their wires needn't cross
    for side_name, sgn in (("L", -1), ("R", 1)):
        pins = sorted(sides[side_name], key=lambda p: part.pin_page(p, at)[1])
        rows = []
        for p in pins:
            if (part.key(), p.num) in nc:
                continue
            net = sheet.pin_net.get((part.key(), p.num))
            if net is None or net.rail:
                continue
            strips = _build_strips(sheet, net, used)
            if any(s.items for s in strips):
                rows.append((p, net, [s for s in strips if s.items]))
        if not rows:
            continue
        # keep the strips clear of the names on the pins
        names = [len(sheet.pin_net[(part.key(), p.num)].name()) for p in pins
                 if (part.key(), p.num) in sheet.pin_net]
        label_room = (max(names) if names else 0) * TEXT_W + 2 * STUB
        edge = box0[0] if sgn < 0 else box0[2]
        x0 = snap(edge + sgn * (label_room + 5.08 + 1.27 * 2 * len(rows)), 2.54)
        lines = [(p, s) for p, _n, ss in rows for s in ss]
        total = sum(s.up + s.down + ROW_GAP for _p, s in lines)
        first_pin_y = part.pin_page(lines[0][0], at)[1]
        last_pin_y = part.pin_page(lines[-1][0], at)[1]
        y = (first_pin_y + last_pin_y) / 2 - total / 2
        for p, s in lines:
            row = snap(max(y + s.up, -1e9), 2.54)
            far = _lay_strip(block, s, x0, row, sgn, sheet)
            block.grow((min(x0, far), row - s.up, max(x0, far), row + s.down))
            y = row + s.down + ROW_GAP

    # banks over the part
    own = banks.get(part.key(), [])
    if own:
        by_rail: dict = defaultdict(list)
        for s, top, bot, rail, gnd in own:
            by_rail[rail.id].append((s, top, bot, rail, gnd))
        bank_h = 0.0
        for items in by_rail.values():
            bank_h = max(bank_h, max(_span(s) for s, *_ in items))
        ytop = snap(block.box[1] - bank_h - 12.7, 2.54)
        x = snap(block.box[0], 2.54)
        wrap = max(block.box[2] - block.box[0], 120.0) + x
        for rid in sorted(by_rail, key=lambda r: min(_ref_key(i[0].ref) for i in by_rail[r])):
            items = by_rail[rid]
            items.sort(key=lambda i: _ref_key(i[0].ref))
            rail, gnd = items[0][3], items[0][4]
            if x > wrap:
                x = snap(block.box[0], 2.54)
                ytop = snap(ytop - bank_h - 22.86, 2.54)
            x = snap(_lay_bank(block, [(s, t, b) for s, t, b, *_ in items], rail, gnd,
                               x, ytop), 2.54)
    return block


def _layout_links(sheet: Sheet, used: set) -> Block | None:
    """Parts between two rails (ferrites, sense resistors) in a row of their own."""
    block = Block("links")
    x, y = 0.0, 0.0
    any_ = False
    for s in sorted((s for s in sheet.syms if passive_like(s) and not s.is_power
                     and s.key() not in used), key=lambda s: _ref_key(s.ref)):
        a, b = s.visible_pins()
        na, nb = sheet.pin_net.get((s.key(), a.num)), sheet.pin_net.get((s.key(), b.num))
        if not (na and nb and na.rail and nb.rail) or na.ground or nb.ground:
            continue
        used.add(s.key())
        any_ = True
        at = _turn(s, a, (-1, 0), (x + 7.62, y))
        block.place(s, at)
        pa, pb = _local_pin(s, a, at), _local_pin(s, b, at)
        block.ops.append(("wire", na, (x, y), pa))
        block.ops.append(("drop", na, (x, y), (0, -1)))
        block.ops.append(("wire", nb, pb, (pb[0] + 5.08, y)))
        block.ops.append(("drop", nb, (pb[0] + 5.08, y), (0, -1)))
        block.grow((x - 5.08, y - 12.7, pb[0] + 15.24, y + 5.08))
        x = snap(pb[0] + 22.86, 2.54)
    return block if any_ else None


def _layout_leftovers(sheet: Sheet, used: set, banks_left: list) -> list[Block]:
    """Rail caps nobody owns, chains that reach no part, one-pin parts."""
    out = []
    if banks_left:
        block = Block("rails")
        by_rail: dict = defaultdict(list)
        for it in banks_left:
            by_rail[it[3].id].append(it)
        x = 0.0
        for rid in sorted(by_rail):
            items = sorted(by_rail[rid], key=lambda i: _ref_key(i[0].ref))
            x = snap(_lay_bank(block, [(s, t, b) for s, t, b, *_ in items],
                               items[0][3], items[0][4], x, 0.0), 2.54)
        out.append(block)
    chains = Block("chains")
    y = 0.0
    n = 0
    for s in sorted((s for s in sheet.syms if passive_like(s) and not s.is_power
                     and s.key() not in used), key=lambda s: _ref_key(s.ref)):
        if s.key() in used:
            continue
        a, b = s.visible_pins()
        net = sheet.pin_net.get((s.key(), a.num)) or sheet.pin_net.get((s.key(), b.num))
        if net is None:
            continue
        if net.rail:
            other = sheet.pin_net.get((s.key(), b.num))
            if other is not None and not other.rail:
                net = other
        strips = _build_strips(sheet, net, used)
        for st in strips:
            if not st.items:
                continue
            row = snap(y + st.up, 2.54)
            far = _lay_strip(chains, st, 15.24, row, 1, sheet)
            chains.grow((0.0, row - st.up, far, row + st.down))
            y = row + st.down + ROW_GAP
            n += 1
    if n:
        out.append(chains)
    return out


def _layout_singles(sheet: Sheet, used: set) -> Block | None:
    """Test points, mounting holes, fiducials."""
    block = Block("misc")
    x = 0.0
    any_ = False
    for s in sorted((s for s in sheet.syms if not s.is_power and len(s.visible_pins()) <= 1
                     and s.key() not in used), key=lambda s: _ref_key(s.ref)):
        used.add(s.key())
        any_ = True
        at = (x, 0.0, 0.0)
        s0 = s.body_box((0.0, 0.0, 0.0))
        block.place(s, at)
        x = snap(x + (s0[2] - s0[0]) + max(text_w(s.ref), text_w(s.value)) + 7.62, 2.54)
    return block if any_ else None


# --------------------------------------------------------------- block ordering

def _block_order(blocks: list[Block], sheet: Sheet) -> list[list[Block]]:
    """Blocks into left-to-right columns by which way their signals run."""
    owner = {}
    for b in blocks:
        for s in b.syms:
            owner[s.key()] = b
    edges = defaultdict(set)
    weight = defaultdict(int)
    sources, sinks = set(), set()
    for n in sheet.nets:
        if n.rail:
            continue
        ends = [(owner.get(s.key()), p) for s, p in n.pins if owner.get(s.key())]
        drivers = {id(b) for b, p in ends if p.type in ("output", "power_out", "tri_state")}
        loads = {id(b) for b, p in ends if p.type == "input"}
        for b, _p in ends:
            for c, _q in ends:
                if b is not c:
                    weight[(id(b), id(c))] += 1
        for d in drivers:
            for x in loads:
                if d != x:
                    edges[d].add(x)
    for b in blocks:
        if b.title.startswith("J") or b.title.startswith("P"):
            # a connector with nothing else to say sits at an edge
            sources.add(id(b))
    by_id = {id(b): b for b in blocks}
    # longest path, breaking cycles by visiting order
    layer: dict = {}
    state: dict = {}

    def visit(i):
        if state.get(i) == 2:
            return layer[i]
        if state.get(i) == 1:
            return -1     # a back edge: ignore it
        state[i] = 1
        best = 0
        for pre in sorted(preds[i], key=lambda z: by_id[z].title):
            best = max(best, visit(pre) + 1)
        state[i] = 2
        layer[i] = best
        return best

    preds = defaultdict(set)
    for a, bs in edges.items():
        for b in bs:
            preds[b].add(a)
    for b in blocks:
        visit(id(b))
    # blocks with no direction go beside what they talk to
    for b in sorted(blocks, key=lambda b: _ref_key(b.title)):
        i = id(b)
        if not preds[i] and not edges[i]:
            peers = [(w, layer[j]) for (a, j), w in weight.items() if a == i and j in layer]
            if peers and i not in sources:
                layer[i] = max(peers)[1]
    if layer and any(id(b) in sources for b in blocks):
        for b in blocks:
            if id(b) in sources and not preds[id(b)] and not edges[id(b)]:
                peers = [layer[j] for (a, j), _w in weight.items() if a == id(b) and j in layer]
                # an input-side connector left of its part, the rest right of it
                layer[id(b)] = (min(peers) - 1) if peers else 0
    lo = min(layer.values()) if layer else 0
    cols: dict = defaultdict(list)
    for b in blocks:
        cols[layer[id(b)] - lo].append(b)
    return [sorted(cols[k], key=lambda b: _ref_key(b.title)) for k in sorted(cols)]


# ---------------------------------------------------------------------- router

def _route(dr: Drawing, net: Net, starts: set, goal: tuple, bound, max_cost: float,
           max_cross: int):
    """Cheapest orthogonal path from goal back onto `starts` (a set of cells on
    this net): few bends, fewer crossings. Returns (path, crossings) or None."""
    x0, y0, x1, y1 = bound
    BEND, CROSS = 6.0, 30.0
    nid = net.id
    heap = []
    seen = {}
    for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        heapq.heappush(heap, (0.0, 0.0, goal, d, 0, None))
    came = {}
    tick = 0
    limit = 200000

    def h(c):
        return min(abs(c[0] - s[0]) + abs(c[1] - s[1]) for s in sample)

    sample = list(starts)[:32]
    while heap and tick < limit:
        tick += 1
        f, g0, c, d, cr, prev = heapq.heappop(heap)
        k = (c, d)
        if k in seen and seen[k] <= (g0, cr):
            continue
        seen[k] = (g0, cr)
        came[k] = prev
        if c in starts and prev is not None and \
                dr.hwires.get(c, nid) == nid and dr.vwires.get(c, nid) == nid:
            path = [c]
            st = prev
            while st is not None:
                path.append(st[0])
                st = came.get(st)
            return path, cr
        for nd in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if nd == (-d[0], -d[1]):
                continue
            n = (c[0] + nd[0], c[1] + nd[1])
            if not (x0 <= n[0] <= x1 and y0 <= n[1] <= y1):
                continue
            kind = dr.blocked.get(n)
            if kind is not None and n not in starts:
                continue
            pin = dr.pin_cells.get(n)
            if pin is not None and pin != nid:
                continue
            node = dr.nodes.get(n)
            if node is not None and node != nid:
                continue
            horiz = nd[1] == 0
            same = dr.hwires if horiz else dr.vwires
            cross = dr.vwires if horiz else dr.hwires
            if same.get(n, nid) != nid:
                continue
            if nd != d and prev is not None:
                # may not turn on someone else's wire
                if dr.hwires.get(c, nid) != nid or dr.vwires.get(c, nid) != nid:
                    continue
            ncr = cr
            cost = 1.0
            if cross.get(n, nid) != nid:
                ncr += 1
                cost += CROSS
                if ncr > max_cross:
                    continue
            if nd != d and prev is not None:
                cost += BEND
            ng = g0 + cost
            if ng > max_cost:
                continue
            if (n, nd) in seen and seen[(n, nd)] <= (ng, ncr):
                continue
            heapq.heappush(heap, (ng + h(n), ng, n, nd, ncr, (c, d)))
    return None


def _reserve_pins(dr: Drawing, sheet: Sheet) -> None:
    tips: set = set()
    for s in sheet.syms:
        if s.is_power or not s.placed:
            continue
        for p in s.visible_pins():
            c = cell(s.pin_page(p))
            n = sheet.pin_net.get((s.key(), p.num))
            nid = n.id if n else -1
            if dr.pin_cells.get(c, nid) != nid and c in tips:
                raise LayoutError(f"{s.ref}.{p.num} lands on another net's pin at {pos(c)}")
            tips.add(c)
            dr.pin_cells[c] = nid
            if n is None:
                continue
            d = s.pin_out(p)
            dr.blocked.pop(c, None)
            # room for the pin's own stub and name: other nets keep out
            for i in (1, 2, 3):
                nxt = (c[0] + d[0] * i, c[1] + d[1] * i)
                dr.pin_cells.setdefault(nxt, n.id)
                if dr.blocked.get(nxt) == "body":
                    dr.blocked.pop(nxt, None)


def _clear_pin_way(dr: Drawing, s: Sym, p: Pin) -> None:
    c = cell(s.pin_page(p))
    d = s.pin_out(p)
    for i in range(0, 3):
        dr.blocked.pop((c[0] + d[0] * i, c[1] + d[1] * i), None)


def _pin_groups(dr: Drawing, net: Net) -> list[tuple]:
    """A net's pins, nearest first, as (cell, sym, pin)."""
    out = [(cell(s.pin_page(p)), s, p) for s, p in net.pins]
    out.sort(key=lambda t: (t[0][0], t[0][1]))
    return out


def _route_net(dr: Drawing, net: Net, bound, max_cost: float, max_cross: int) -> list:
    """Wire what can be wired. Returns the pins left over as [(sym, pin)]."""
    pins = _pin_groups(dr, net)
    if len(pins) < 2:
        return [(s, p) for _c, s, p in pins]
    # wires this net already has, from a bank or a drop, count as reached
    tree = set()
    # start at the pin nearest the middle
    mx = sum(c[0] for c, *_ in pins) / len(pins)
    my = sum(c[1] for c, *_ in pins) / len(pins)
    pins.sort(key=lambda t: abs(t[0][0] - mx) + abs(t[0][1] - my))
    tree.add(pins[0][0])
    left = pins[1:]
    missed = []
    while left:
        left.sort(key=lambda t: min(abs(t[0][0] - c[0]) + abs(t[0][1] - c[1]) for c in tree))
        c, s, p = left.pop(0)
        if c in tree:
            continue
        res = _route(dr, net, tree, c, bound, max_cost, max_cross)
        if res is None:
            missed.append((s, p))
            continue
        path, _cr = res
        dr.add_path(net, path)
        tree.update(path)
    return missed


def _clear_for(dr: Drawing, net: Net, c) -> bool:
    """May this net put a wire end on this cell?"""
    for table in (dr.hwires, dr.vwires, dr.nodes, dr.pin_cells):
        if table.get(c, net.id) != net.id:
            return False
    return True


def _stub_label(dr: Drawing, net: Net, s: Sym, p: Pin) -> None:
    """A pin that keeps a name instead of a wire. The stub stretches past
    anything of another net; a label on someone else's wire would join them."""
    d = s.pin_out(p)
    a = s.pin_page(p)
    c = cell(a)
    run = 0
    for n in range(1, 25):
        cur = (c[0] + d[0] * n, c[1] + d[1] * n)
        if not _clear_for(dr, net, cur) or dr.blocked.get(cur) == "body":
            if n > 2 and run >= 2:
                break
            what = dr.pin_cells.get(cur), dr.hwires.get(cur), dr.vwires.get(cur), \
                dr.nodes.get(cur), dr.blocked.get(cur), net.id
            raise LayoutError(f"{s.ref}.{p.num}: no room for a stub to name {net.name()} "
                              f"at {pos(cur)}: {what}")
        run = n
        if n >= 2 and dr.blocked.get((cur[0] + d[0], cur[1] + d[1])) is None:
            break
    e = pos((c[0] + d[0] * run, c[1] + d[1] * run))
    dr.wire(net, a, e)
    dr.drop(net, e, d)


def _label_spot(dr: Drawing, net: Net, name: str):
    """The cell on this net's level wires whose label text has the most clear
    room above it: text over a part is what makes a redraw hard to read."""
    need = int(text_w(name) / G) + 1
    best = None
    for c in sorted({x for st in dr.wires.get(net.id, ()) for x in st}):
        if c in dr.pin_cells or not _clear_for(dr, net, c):
            continue
        right = (c[0] + 1, c[1]) in {x for st in dr.wires[net.id] for x in st} or \
            ((c, (c[0] + 1, c[1])) in dr.wires[net.id])
        if not right:
            continue
        hits = sum(1 for i in range(need) for j in (1, 2)
                   if (c[0] + i, c[1] - j) in dr.blocked
                   or (c[0] + i, c[1] - j) in dr.hwires)
        score = (hits, c[1], c[0])
        if best is None or score < best[0]:
            best = (score, c)
    return best[1] if best else None


def _name_tree(dr: Drawing, net: Net, tree_pins: list) -> None:
    """A wired net keeps every name it had: the first sits on one of its own
    level wires, any others on stubs off its pins."""
    spot = None
    names = [("label", x, "") for x in net.locals]
    names += [("global_label", x, sh) for x, sh in net.globals]
    names += [("hierarchical_label", x, sh) for x, sh in net.hiers]
    if not names:
        net.anchored = True
        return
    net.anchored = True
    if names[0][0] == "label":
        spot = _label_spot(dr, net, names[0][1])
    if spot is not None:
        kind, name, shape = names[0]
        if kind == "label":
            names = names[1:]
            at = pos(spot)
            dr.nodes[spot] = net.id
            dr.labels.append({"kind": "label", "name": name, "at": at, "ang": 0,
                              "just": "left", "shape": ""})
            dr.block_text(name, (at[0], at[1] - 1.27), "left")
    stubs = iter(sorted(tree_pins, key=lambda sp: _ref_key(sp[0].ref)))
    for kind, name, shape in names:
        s, p = next(stubs)
        d = s.pin_out(p)
        c = cell(s.pin_page(p))
        # off the pin's own run if it has one
        e = (c[0] + d[0] * 2, c[1] + d[1] * 2)
        if not _clear_for(dr, net, e) or not _clear_for(dr, net, (c[0] + d[0], c[1] + d[1])):
            raise LayoutError(f"{s.ref}.{p.num}: no room to name {name}")
        dr.wire(net, pos(c), pos(e))
        dr.label(kind, name, pos(e), d, shape)


def _merge_rail_pins(part: Sym, pins: list[Pin], sheet: Sheet) -> list[list[Pin]]:
    """Rail pins side by side share one stub."""
    groups: list[list[Pin]] = []
    for p in sorted(pins, key=lambda p: part.pin_page(p)):
        n = sheet.pin_net.get((part.key(), p.num))
        if groups:
            last = groups[-1][-1]
            la, a = part.pin_page(last), part.pin_page(p)
            if sheet.pin_net.get((part.key(), last.num)) is n and \
                    abs(la[0] - a[0]) + abs(la[1] - a[1]) <= 2.54 + 1e-6 and n is not None:
                groups[-1].append(p)
                continue
        groups.append([p])
    return groups


# ---------------------------------------------------------------------- redraw

def redraw(sheet: Sheet, power_base: int = 9000) -> tuple[str, dict]:
    """The sheet's new text and a report. Raises LayoutError to leave it alone."""
    used: set = set()
    dr = Drawing(sheet, power_base)
    parts = [s for s in sheet.syms if not s.is_power and not passive_like(s)
             and len(s.visible_pins()) > 1]
    parts.sort(key=lambda s: _ref_key(s.ref))
    owners = {s.key(): s for s in parts}

    # who decouples what: passives between a supply and ground
    banks: dict = defaultdict(list)
    banks_left = []
    for s in sorted((s for s in sheet.syms if passive_like(s) and not s.is_power),
                    key=lambda s: _ref_key(s.ref)):
        a, b = s.visible_pins()
        na, nb = sheet.pin_net.get((s.key(), a.num)), sheet.pin_net.get((s.key(), b.num))
        if na is None or nb is None or not (na.rail and nb.rail):
            continue
        if na.ground == nb.ground:
            continue
        top, bot, rail, gnd = (a, b, na, nb) if nb.ground else (b, a, nb, na)
        used.add(s.key())
        own = _bank_owner(sheet, s, rail, owners)
        item = (s, top, bot, rail, gnd)
        if own is not None:
            banks[own.key()].append(item)
        else:
            banks_left.append(item)

    links = _layout_links(sheet, used)
    blocks = [_layout_part(sheet, p, used, banks) for p in parts]
    blocks += _layout_leftovers(sheet, used, banks_left)
    if links is not None:
        blocks.append(links)
    singles = _layout_singles(sheet, used)

    # columns, wrapped so the page stays landscape
    cols = _block_order(blocks, sheet)
    area = sum((b.box[2] - b.box[0] + BLOCK_GAP) * (b.box[3] - b.box[1] + BLOCK_GAP)
               for b in blocks) or 1.0
    max_h = max(area ** 0.5 / 1.2, max((b.box[3] - b.box[1] for b in blocks), default=0.0))
    x = 0.0
    top = 0.0
    bottom = 0.0
    moves = []
    for col in cols:
        y = top
        col_w = 0.0
        for b in col:
            h = b.box[3] - b.box[1]
            w = b.box[2] - b.box[0]
            if y > top and y + h > top + max_h:
                x = snap(x + col_w + BLOCK_GAP, 2.54)
                y = top
                col_w = 0.0
            moves.append((b, snap(x - b.box[0], 2.54), snap(y - b.box[1], 2.54)))
            col_w = max(col_w, w)
            y += h + BLOCK_GAP
            bottom = max(bottom, y)
        x = snap(x + col_w + BLOCK_GAP * 1.5, 2.54)

    placed_all = []
    for b, dx, dy in moves:
        for s, at in b.parts:
            s.new_at = (round(at[0] + dx, 4), round(at[1] + dy, 4), at[2])
            s.placed = True
            placed_all.append(s)
    if singles is not None:
        for s, at in singles.parts:
            s.new_at = (round(at[0], 4), round(at[1] + snap(bottom, 2.54) + 12.7, 4), at[2])
            s.placed = True
            placed_all.append(s)
        bottom = snap(bottom, 2.54) + 30.48

    for s in sheet.syms:
        if not s.is_power and not s.placed:
            raise LayoutError(f"{s.ref} wasn't placed")

    # fields and obstacles
    fields: dict = {}
    for s in placed_all:
        dr.block_box(s.body_box(), "body", pad=0.0)
    for s in placed_all:
        fields[s.uuid] = _field_spots(s)
        for name, (fx, fy, just) in fields[s.uuid].items():
            txt = s.ref if name == "Reference" else s.value
            dr.block_text(txt, (fx, fy), just)
    for s in placed_all:
        for p in s.visible_pins():
            _clear_pin_way(dr, s, p)
    _reserve_pins(dr, sheet)

    # block-owned wires and drops, then rail pins on parts
    for b, dx, dy in moves:
        for op in b.ops:
            if op[0] == "wire":
                _, net, p, q2 = op
                dr.wire(net, (p[0] + dx, p[1] + dy), (q2[0] + dx, q2[1] + dy))
    for b, dx, dy in moves:
        for op in b.ops:
            if op[0] == "drop":
                _, net, p, d = op
                dr.drop(net, (round(p[0] + dx, 4), round(p[1] + dy, 4)), d)

    wired = {"labelled": [], "wired": 0}
    # supply pins on parts: a stub and the rail's symbol; side by side they share one
    rail_pins_done = set()
    for s in parts:
        rail_pins = [p for p in s.visible_pins()
                     if (s.key(), p.num) in sheet.pin_net
                     and sheet.pin_net[(s.key(), p.num)].rail]
        for grp in _merge_rail_pins(s, rail_pins, sheet):
            net = sheet.pin_net[(s.key(), grp[0].num)]
            d = s.pin_out(grp[0])
            ends = []
            for p in grp:
                a = s.pin_page(p)
                e = (round(a[0] + d[0] * STUB, 4), round(a[1] + d[1] * STUB, 4))
                dr.wire(net, a, e)
                ends.append(e)
                rail_pins_done.add((s.key(), p.num))
            if len(ends) > 1:
                dr.wire(net, ends[0], ends[-1])
            dr.drop(net, ends[len(ends) // 2], d)

    # passives' rail pins that the strips didn't take (series parts that end on a rail)
    for s in sheet.syms:
        if s.is_power:
            continue
        for p in s.visible_pins():
            n = sheet.pin_net.get((s.key(), p.num))
            if n is None or not n.rail or (s.key(), p.num) in rail_pins_done:
                continue
            c = cell(s.pin_page(p))
            if c in dr.nodes or c in dr.hwires or c in dr.vwires:
                continue
            a = s.pin_page(p)
            d = s.pin_out(p)
            e = (round(a[0] + d[0] * STUB, 4), round(a[1] + d[1] * STUB, 4))
            dr.wire(n, a, e)
            dr.drop(n, e, d)

    # signal nets: shortest first, inside blocks before between them
    owner = {}
    for b, _dx, _dy in moves:
        for s in b.syms:
            owner[s.key()] = id(b)
    signals = [n for n in sheet.nets if not n.rail]

    def spread(n):
        cs = [cell(s.pin_page(p)) for s, p in n.pins] or [(0, 0)]
        return (max(c[0] for c in cs) - min(c[0] for c in cs)) + \
               (max(c[1] for c in cs) - min(c[1] for c in cs))

    signals.sort(key=lambda n: (len({owner.get(s.key()) for s, _p in n.pins}) > 1,
                                spread(n), n.name()))

    all_cells = list(dr.blocked) + list(dr.pin_cells) + list(dr.hwires) + list(dr.vwires)
    if all_cells:
        bx0 = min(c[0] for c in all_cells) - 12
        by0 = min(c[1] for c in all_cells) - 12
        bx1 = max(c[0] for c in all_cells) + 12
        by1 = max(c[1] for c in all_cells) + 12
    else:
        bx0, by0, bx1, by1 = 0, 0, 10, 10
    bound = (bx0, by0, bx1, by1)
    # nets that keep labels get their stubs first, so wires go round the text
    to_route = []
    for n in signals:
        if not n.pins:
            continue
        blocks_on = {owner.get(s.key()) for s, _p in n.pins}
        if len(n.pins) >= 2 and not (len(n.pins) > MAX_LABEL_PINS and len(blocks_on) > 1):
            to_route.append((n, len(blocks_on) > 1))
            continue
        if not n.names():
            raise LayoutError(f"{n.pins[0][0].ref}.{n.pins[0][1].num}: unnamed lone pin")
        wired["labelled"].append(n.name())
        for s, p in n.pins:
            _stub_label(dr, n, s, p)
    for n, between in to_route:
        max_cost = (MAX_WIRE_MM if between else 160.0) / G + 60
        missed = _route_net(dr, n, bound, max_cost, MAX_CROSSINGS if between else 4)
        wired["wired"] += 1
        got = [(s, p) for s, p in n.pins if (s, p) not in missed]
        if missed and not n.names():
            raise LayoutError(f"net of {n.pins[0][0].ref}.{n.pins[0][1].num} has no "
                              "name and couldn't be wired")
        if len(got) >= 2:
            _name_tree(dr, n, got)
        elif got:
            missed = missed + got
        if missed:
            wired["labelled"].append(n.name())
        for s, p in missed:
            _stub_label(dr, n, s, p)

    # names with nothing to hang on (a net with only labels and flags)
    for n in sheet.nets:
        if not n.anchored and n.names() and not n.pins and not n.flags:
            raise LayoutError(f"net {n.name()} has a name but no pins")

    # power flags in a row: a rail's symbol and its flag on one wire
    flag_y = snap(bottom, 2.54) + 12.7
    fx = 0.0
    for n in sheet.nets:
        if not n.flags:
            continue
        for fl in n.flags:
            a = (fx, flag_y)
            b = (fx + 7.62, flag_y)
            dr.wire(n, a, b)
            dr.drop(n, a, (0, 1) if n.ground else (-1, 0) if n.power else (-1, 0))
            pin = fl.pins[0] if fl.pins else None
            lx, ly = (pin.lx, pin.ly) if pin else (0.0, 0.0)
            ox, oy = sr.to_page(lx, ly, (0.0, 0.0, 0.0), None)
            fl.new_at = (round(b[0] - ox, 4), round(b[1] - oy, 4), 0.0)
            fl.placed = True
            fields[fl.uuid] = {"Value": (b[0] + 1.27, b[1] - 5.08, "left")}
            fx = snap(fx + 25.4 + text_w(n.name()), 2.54)

    # power symbols with nothing left on them keep their net alive
    for n in sheet.nets:
        if n.power and not n.anchored:
            if not n.pins and not n.flags:
                raise LayoutError(f"rail {n.name()}: power symbol on nothing")

    text = _write(sheet, dr, fields, placed_all)
    _self_check(sheet, text)
    report = {"parts": len(placed_all), "blocks": len(blocks),
              "wired_nets": wired["wired"], "labelled_nets": sorted(set(wired["labelled"])),
              "power_symbols_added": dr.power_i - power_base}
    return text, report, dr.power_i


def _partition(sheet: Sheet) -> dict:
    """frozenset of (ref, unit, pin) per net, with the net's label names."""
    out = {}
    for n in sheet.nets:
        pins = frozenset((s.ref, s.unit, p.num) for s, p in n.pins)
        if pins:
            out[pins] = set(n.names())
    return out


def _self_check(old: Sheet, text: str) -> None:
    """The sheet's own connectivity, before the slower kicad-cli gate."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / old.path.name
        p.write_text(text, encoding="utf-8")
        new = Sheet(p)
    a, b = _partition(old), _partition(new)
    if a.keys() != b.keys():
        gone = [sorted(x)[:6] for x in a.keys() - b.keys()][:4]
        made = [sorted(x)[:6] for x in b.keys() - a.keys()][:4]
        raise LayoutError(f"self-check: nets differ; were {gone}; now {made}")
    for k, names in a.items():
        if not names <= b[k]:
            raise LayoutError(f"self-check: {sorted(k)[:3]} lost names {names - b[k]}")


def _field_spots(s: Sym) -> dict:
    """Where Reference and Value go, by how the part sits."""
    b = s.body_box()
    if passive_like(s):
        pins = s.visible_pins()
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if s.pin_out(pins[0])[0] == 0:   # vertical: text to the right
            x = snap(cx + 1.27 + 1.27)
            return {"Reference": (x, round(cy - 1.27, 4), "left"),
                    "Value": (x, round(cy + 1.27, 4), "left")}
        return {"Reference": (round(cx, 4), round(cy - 3.05, 4), None),
                "Value": (round(cx, 4), round(cy + 3.05, 4), None)}
    return {"Reference": (round(b[0], 4), round(b[1] - 1.91, 4), "left"),
            "Value": (round(b[0], 4), round(b[3] + 1.91, 4), "left")}


# ---------------------------------------------------------------------- output

def _rewrite_symbol(s: Sym, spots: dict | None) -> str:
    """The symbol's text at its new place: only Reference and Value show."""
    t = s.text
    head_end = t.find("(property")
    head = t[:head_end]
    at = s.new_at
    head = re.sub(r'\(at \S+ \S+(?: \S+)?\)',
                  f'(at {fmt(at[0])} {fmt(at[1])} {fmt(at[2])})', head, count=1)
    head = re.sub(r'\s*\(mirror \S+\)', "", head) if passive_like(s) else head
    head = re.sub(r'\(fields_autoplaced \S+\)', "(fields_autoplaced no)", head)
    body = t[head_end:]
    out, last = [], 0
    for h, a, b in forms(body):
        if h != "property":
            continue
        out.append(body[last:a])
        m = re.match(r'\(property\s+"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)"', body[a:b])
        name, val = m.group(1), m.group(2)
        val = val.replace('\\"', '"').replace("\\\\", "\\")
        spot = (spots or {}).get(name)
        hide = name not in ("Reference", "Value") or spot is None
        if s.is_flag and name == "Value":
            hide = True
        if s.is_power and name == "Reference":
            hide = True
        if spot is None:
            spot = (at[0], at[1], None)
        out.append(_property_form(name, val, spot[:2], spot[2], hide, at[2]))
        last = b
    out.append(body[last:])
    return head + "".join(out)


def _wire_runs(dr: Drawing) -> tuple[list, list]:
    """Unit steps -> straight runs broken at every pin, bend and tee, and the
    junctions a tee needs. (Pins only connect at a wire's end.)"""
    runs, junctions = [], []
    for nid, steps in sorted(dr.wires.items()):
        adj = defaultdict(set)
        for a, b in steps:
            lo, hi = a, b
            dx = (hi[0] > lo[0]) - (hi[0] < lo[0])
            dy = (hi[1] > lo[1]) - (hi[1] < lo[1])
            c = lo
            while c != hi:
                n = (c[0] + dx, c[1] + dy)
                adj[c].add(n)
                adj[n].add(c)
                c = n
        stops = set()
        for c, ns in adj.items():
            if len(ns) != 2:
                stops.add(c)
            else:
                u, v = sorted(ns)
                if (u[0] == v[0]) != (c[0] == u[0]) or (u[1] == v[1]) != (c[1] == u[1]):
                    stops.add(c)
            if c in dr.pin_only:
                stops.add(c)
        for c, ns in adj.items():
            if len(ns) >= 3:
                junctions.append(c)
            elif c in dr.pin_only and len(ns) == 2:
                junctions.append(c)
        done = set()
        for c in sorted(stops):
            for n in sorted(adj[c]):
                if (c, n) in done:
                    continue
                d = (n[0] - c[0], n[1] - c[1])
                cur = n
                while cur not in stops:
                    cur = (cur[0] + d[0], cur[1] + d[1])
                done.add((c, n))
                back = (cur[0] - d[0], cur[1] - d[1])
                done.add((cur, back))
                runs.append((c, cur))
        # a closed loop has no stops: nothing we draw makes one
    return runs, junctions


def _write(sheet: Sheet, dr: Drawing, fields: dict, placed: list[Sym]) -> str:
    dr.pin_only = set(dr.power_pins)
    for fl in (s for s in sheet.syms if s.is_flag and s.new_at):
        for p in fl.pins:
            dr.pin_only.add(cell(fl.pin_page(p)))
    for s in placed:
        for p in s.visible_pins():
            dr.pin_only.add(cell(s.pin_page(p)))
    # labels and power pins sit on wires too: they don't need a wire end, but a
    # power symbol's pin does
    for s, p in sheet.nc_pins:
        dr.ncs.append(s.pin_page(p))
    runs, junctions = _wire_runs(dr)

    keep = []
    last = sheet.body[0]
    sym_i = 0
    stem = sheet.path.stem
    for h, a, b in sheet.top_forms:
        keep.append(sheet.text[last:a])
        last = b
        if h == "symbol":
            s = sheet.syms[sym_i]
            sym_i += 1
            if s.is_power and not s.is_flag:
                continue    # its clones stand in for it
            keep.append(_rewrite_symbol(s, fields.get(s.uuid)))
        elif h in ("wire", "junction", "label", "global_label", "hierarchical_label",
                   "no_connect"):
            continue
        else:
            keep.append(sheet.text[a:b])
    tail = sheet.text[last:]

    new = list(dr.clones)
    for i, (a, b) in enumerate(runs):
        pa, pb = pos(a), pos(b)
        new.append(f'(wire (pts (xy {fmt(pa[0])} {fmt(pa[1])}) (xy {fmt(pb[0])} {fmt(pb[1])})) '
                   f'(stroke (width 0) (type default)) (uuid "{_uuid(stem, "w", a, b)}"))')
    for c in sorted(set(junctions)):
        p = pos(c)
        new.append(f'(junction (at {fmt(p[0])} {fmt(p[1])}) (diameter 0) (color 0 0 0 0) '
                   f'(uuid "{_uuid(stem, "j", c)}"))')
    for lb in dr.labels:
        at = lb["at"]
        name = q(lb["name"])
        sh = f'(shape {lb["shape"] or "passive"}) ' if lb["kind"] != "label" else ""
        just = "left bottom" if lb["kind"] == "label" and lb["just"] == "left" else \
            "right bottom" if lb["kind"] == "label" else lb["just"]
        new.append(f'({lb["kind"]} {name} {sh}(at {fmt(at[0])} {fmt(at[1])} {lb["ang"]}) '
                   f'(effects (font (size 1.27 1.27)) (justify {just})) '
                   f'(uuid "{_uuid(stem, "l", lb["kind"], lb["name"], at)}"))')
    for p in dr.ncs:
        new.append(f'(no_connect (at {fmt(p[0])} {fmt(p[1])}) '
                   f'(uuid "{_uuid(stem, "nc", p)}"))')
    # new items go before sheet_instances, where KiCad writes its drawing items
    joined = "".join(keep).rstrip()
    m = re.search(r'\(sheet_instances', joined)
    block = "\n\t".join(new)
    if m:
        joined = joined[:m.start()] + block + "\n\t" + joined[m.start():]
    else:
        tail = "\n\t" + block + "\n" + tail
    return _hide_two_pin_numbers(sheet.text[:sheet.body[0]] + joined + tail, sheet)


def _hide_two_pin_numbers(text: str, sheet: Sheet) -> str:
    """A resistor doesn't need its pin numbers printed; the embedded symbol
    says so. Polarity marks are in the graphics, so diodes lose nothing."""
    ids = {s.lib_id for s in sheet.syms if not s.is_power and passive_like(s)}
    m = re.search(r"\(lib_symbols", text)
    if not m or not ids:
        return text
    for _h, a, b in forms(text, m.start()):
        block = text[a:b]
        out, last = [], 0
        inner_start = block.find("(symbol")
        for h2, c, d in forms(block, inner_start, len(block) - 1):
            mm = re.match(r'\(symbol\s+"([^"]+)"', block[c:d])
            if h2 == "symbol" and mm and mm.group(1) in ids:
                f = block[c:d]
                f2 = re.sub(r'\(pin_numbers[^()]*(\(hide [^()]*\))?[^()]*\)\s*', "", f, count=1)
                head = re.match(r'\(symbol\s+"[^"]+"', f2)
                f2 = f2[:head.end()] + " (pin_numbers (hide yes))" + f2[head.end():]
                out.append(block[last:c])
                out.append(f2)
                last = d
        out.append(block[last:])
        return text[:a] + "".join(out) + text[b:]
    return text


# ---------------------------------------------------------------------- netlist

def _cli() -> str:
    cli = env.find_kicad_cli()
    if cli is None:
        raise LayoutError("no kicad-cli")
    return str(cli)


def netlist(sch: Path) -> dict:
    """{net name: sorted (ref, pin)} from kicad-cli."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.net"
        cp = subprocess.run([_cli(), "sch", "export", "netlist", "--format", "kicadsexpr",
                             "-o", str(out), str(sch)], capture_output=True, text=True,
                            timeout=300)
        if cp.returncode != 0 or not out.exists():
            raise LayoutError(f"netlist export failed: {cp.stderr[-300:]}")
        root = sexpdata.loads(out.read_text(encoding="utf-8"))
    nets = {}
    for n in sr._kids(sr._kid(root, "nets"), "net"):
        name = str(sr._tok(sr._kid(n, "name")[1]))
        nodes = sorted((str(sr._tok(sr._kid(x, "ref")[1])), str(sr._tok(sr._kid(x, "pin")[1])))
                       for x in sr._kids(n, "node"))
        nets[name] = nodes
    # the PCB finds its parts by these: a redraw must not change one
    for c in sr._kids(sr._kid(root, "components"), "comp"):
        ref = str(sr._tok(sr._kid(c, "ref")[1]))
        stamps = sr._kid(c, "tstamps")
        path = sr._kid(c, "sheetpath")
        nets[f"#comp {ref}"] = [str(sr._tok(x)) for x in (stamps or [])[1:]] + \
            [str(sr._tok(sr._kid(path, "tstamps")[1]))] if path is not None else []
    return nets


def diff_netlists(a: dict, b: dict) -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            out.append(f"{k}: {a.get(k)} -> {b.get(k)}")
    return out


def erc_errors(sch: Path) -> int:
    """How many errors kicad-cli's ERC finds."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "erc.json"
        cp = subprocess.run([_cli(), "sch", "erc", "--format", "json", "--severity-error",
                             "-o", str(out), str(sch)], capture_output=True, text=True,
                            timeout=300)
        if not out.exists():
            raise LayoutError(f"erc failed: {cp.stderr[-300:]}")
        data = json.loads(out.read_text(encoding="utf-8"))
    return sum(len(s.get("violations", [])) for s in data.get("sheets", []))


def sheet_files(top: Path) -> list[Path]:
    """The top file and every sheet under it."""
    seen, todo = [], [Path(top)]
    while todo:
        p = todo.pop(0)
        if p in seen or not p.exists():
            continue
        seen.append(p)
        for f in re.findall(r'\(property "Sheetfile" "([^"]+)"', p.read_text(encoding="utf-8")):
            todo.append(p.parent / f)
    return seen


def _fit(path: Path) -> None:
    import schlib
    schlib.fit_sheet(path, "auto")


def run(top: Path, out_dir: Path | None = None) -> tuple[dict, int]:
    top = Path(top).resolve()
    if out_dir is not None:
        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in top.parent.iterdir():
            if f.is_file() and f.suffix in (".kicad_sch", ".kicad_pro", ".kicad_sym", ".kicad_prl") \
                    or f.name in ("sym-lib-table", "fp-lib-table"):
                shutil.copy2(f, out_dir / f.name)
        top = out_dir / top.name
    before = netlist(top)
    errors = erc_errors(top)
    originals = {p: p.read_text(encoding="utf-8") for p in sheet_files(top)}
    report = {"top": str(top), "sheets": {}}
    code = 0
    base = 9000
    for path in sheet_files(top):
        name = path.name
        original = path.read_text(encoding="utf-8")
        try:
            sheet = Sheet(path)
            if sheet.has_sheets or sheet.has_bus:
                # its wiring stays, but the boxes should at least fit the page
                if not sheet.hand_drawn():
                    _fit(path)
                    if diff_netlists(before, netlist(top)):
                        path.write_text(original, encoding="utf-8")
                report["sheets"][name] = {"status": "skipped", "why": "has sheet boxes or buses"}
                continue
            if not [s for s in sheet.syms if not s.is_power]:
                report["sheets"][name] = {"status": "skipped", "why": "no parts"}
                continue
            if sheet.hand_drawn():
                report["sheets"][name] = {"status": "skipped", "why": "already wired"}
                continue
            text, rep, base = redraw(sheet, base)
            path.write_text(text, encoding="utf-8")
            _fit(path)
            diff = diff_netlists(before, netlist(top))
            if diff:
                raise LayoutError(f"netlist changed: {len(diff)} nets, first: {diff[:3]}")
            report["sheets"][name] = {"status": "redrawn", **rep}
        except Exception as exc:  # noqa: BLE001 - the original goes back, whatever broke
            path.write_text(original, encoding="utf-8")
            report["sheets"][name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            code = 1
    now = erc_errors(top)
    if now > errors:
        # netlist the same but ERC worse (a lost no-connect, say): take none of it
        for p, text in originals.items():
            p.write_text(text, encoding="utf-8")
        report["erc"] = f"errors {errors} -> {now}: every sheet restored"
        for v in report["sheets"].values():
            if v["status"] == "redrawn":
                v["status"] = "failed"
        code = 1
    return report, code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sch", required=True, help="top .kicad_sch")
    ap.add_argument("--out-dir", help="redraw a copy there; default is in place")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    try:
        report, code = run(Path(args.sch), Path(args.out_dir) if args.out_dir else None)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
