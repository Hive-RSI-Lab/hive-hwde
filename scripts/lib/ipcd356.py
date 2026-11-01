"""ipcd356.py - IPC-D-356 netlist and the Gerber netlist compare.

A bare-board tester (flying probe, bed of nails) checks every board against
an IPC-D-356 netlist: continuity inside each net, isolation between nets.
When none is supplied the fab extracts one from the Gerbers, so a Gerber
export defect is "tested good". CAM tools (Valor NPI, CAM350) do the same
compare before release: build the connectivity of the copper the fab will
actually etch and drill, and compare it with the design's netlist.

    short  one copper island (joined through plated holes) holds the test
           points of more than one D-356 net
    open   one D-356 net's test points fall in more than one island, or a
           test point lands on no copper at all

The netlist is kicad-cli's (`pcb export ipcd356`, IPC-D-356A fixed columns):
317 = plated through point (pads and vias), 327 = SMD point, 367 = unplated
hole (never a conductor), 999 = end. KiCad writes UNITS CUST 0 (inch,
0.0001 in) and Y up; this module returns board space (mm, Y down), the same
frame gerblib uses. Access A00 = both sides, A01 = top, A<n> = copper layer n
(KiCad puts a bottom SMD pad at A<layer count>).

kicad-cli keeps the LAST 14 characters of a long net name, so two
"unconnected-(...-PAD5)" nets can share one D-356 name. A tester would then
expect continuity between them and fail a good board; `resolve()` maps each
point back to the board's own net (pad ref+number, via position) and
reports the clash.

Public API:
    parse(text) -> D356
    resolve(d356, bg) -> list[name clash dict]   (sets point.board_net)
    net_tie_classes(pcb_text, bg) -> {net: class id}
    compare(fab, d356, ties=None) -> {"shorts": [...], "opens": [...], ...}
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from shapely.geometry import Point
from shapely.strtree import STRtree

NO_NET = "N/C"

# Mils of the tester's world. CUST 0 / CUST 2: inch, 0.0001 in per count;
# CUST 1: mm, 0.001 mm per count; SI: mm, 0.001 mm.
_UNIT_MM = {"CUST 0": 0.00254, "CUST 2": 0.00254, "CUST 1": 0.001,
            "SI": 0.001, "ENGLISH": 0.00254}

_TAIL = re.compile(
    r"(?:D(?P<hole>\d{4})(?P<plated>[PU]))?\s*A(?P<access>\d{2})"
    r"X(?P<x>[+-]\s*\d+)Y(?P<y>[+-]\s*\d+)"
    r"(?:X(?P<sx>\d{4}))?(?:Y(?P<sy>\d{4}))?(?:R(?P<rot>\d{3}))?")
_ALIAS = re.compile(r"^P\s+(NNAME\d+)\s+(\S.*?)\s*$")
_UNITS = re.compile(r"^P\s+UNITS\s+(.+?)\s*$")

# Test point to copper: KiCad writes the pad's position, which is inside its
# own flash for every pad shape. 1 um covers coordinate round-off (the
# format resolves 2.54 um); a point further out falls back to the nearest
# island within half the pad's smaller side.
_HIT_MM = 0.001


@dataclass
class TestPoint:
    net: str
    ref: str
    pin: str
    x: float
    y: float
    code: str                   # "317" | "327" | "367"
    access: int                 # 0 both, 1 top, n = copper layer n
    hole_mm: float | None = None
    plated: bool | None = None
    size: tuple[float, float] = (0.0, 0.0)
    board_net: str | None = None

    @property
    def key(self) -> str:
        """The net the compare groups by: the board's own net when resolve()
        found one, else the D-356 name."""
        return self.board_net or self.net

    @property
    def label(self) -> str:
        return f"{self.ref}-{self.pin}" if self.pin else self.ref

    def as_dict(self) -> dict:
        return {"ref": self.ref, "pin": self.pin, "x": round(self.x, 4),
                "y": round(self.y, 4), "net": self.key}


@dataclass
class D356:
    units: str = "CUST 0"
    points: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)

    def nets(self) -> set[str]:
        return {p.key for p in self.points if p.key != NO_NET}


class D356Error(ValueError):
    pass


def parse(text: str) -> D356:
    """IPC-D-356(A) text -> D356 (board space, mm)."""
    d = D356()
    for raw in text.splitlines():
        if (m := _UNITS.match(raw)):
            d.units = m.group(1).upper()
        elif (m := _ALIAS.match(raw)):
            d.aliases[m.group(1)] = m.group(2)
    scale = _UNIT_MM.get(d.units)
    if scale is None:
        raise D356Error(f"IPC-D-356 units {d.units!r} not understood")
    for raw in text.splitlines():
        code = raw[:3]
        if code not in ("317", "327", "367"):
            continue
        line = raw.ljust(80)
        net = line[3:17].strip()
        net = d.aliases.get(net, net)
        ref = line[20:26].strip()
        pin = line[27:31].strip() if line[26] == "-" else ""
        m = _TAIL.search(line, 31)
        if m is None:
            raise D356Error(f"IPC-D-356 record not understood: {raw!r}")
        hole = m.group("hole")
        d.points.append(TestPoint(
            net=net or NO_NET, ref=ref, pin=pin,
            x=int(m.group("x").replace(" ", "")) * scale,
            y=-int(m.group("y").replace(" ", "")) * scale,
            code=code, access=int(m.group("access")),
            hole_mm=int(hole) * scale if hole else None,
            plated=(m.group("plated") == "P") if hole else None,
            size=(int(m.group("sx") or 0) * scale,
                  int(m.group("sy") or 0) * scale)))
    if not d.points:
        raise D356Error("IPC-D-356 file holds no test points")
    return d


# ------------------------------------------------------------ board side

def resolve(d: D356, bg) -> list[dict]:
    """Point each test point at the board's own net: a pad by ref+number, a
    VIA by position. Returns the D-356 names that stand for more than one
    board net (kicad-cli's 14-character truncation)."""
    pads = {}
    by_ref = defaultdict(list)
    for p in bg.pads_of():
        pads[(p.ref, str(p.number))] = p.net
        by_ref[p.ref].append(p)
    vias = [(v.at, v.net) for v in bg.vias_of()]
    via_tree = STRtree([Point(a) for a, _ in vias]) if vias else None
    by_name: dict[str, set[str]] = defaultdict(set)
    for tp in d.points:
        net = None
        if tp.ref == "VIA" and via_tree is not None:
            i = via_tree.nearest(Point(tp.x, tp.y))
            (vx, vy), vnet = vias[int(i)]
            if abs(vx - tp.x) < 0.01 and abs(vy - tp.y) < 0.01:
                net = vnet
        elif (tp.ref, tp.pin) in pads:
            net = pads[(tp.ref, tp.pin)]
        else:
            # the pin field holds 4 characters ("A1-B12" -> "A1-B"): the
            # ref's pad under the point whose number starts with it
            for p in by_ref.get(tp.ref, ()):
                if str(p.number).startswith(tp.pin) \
                        and abs(p.center[0] - tp.x) < 0.01 \
                        and abs(p.center[1] - tp.y) < 0.01:
                    net = p.net
                    break
        if net:
            tp.board_net = net
            if tp.net != NO_NET:
                by_name[tp.net].add(net)
    return [{"d356_net": name, "board_nets": sorted(nets)}
            for name, nets in sorted(by_name.items()) if len(nets) > 1]


def _sexp_end(text: str, start: int) -> int:
    depth = 0
    in_str = False
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


_NET_TIE = re.compile(r"\(net_tie_pad_groups((?:\s+\"[^\"]*\")+)\s*\)")
_REF = re.compile(r'\(property\s+"Reference"\s+"([^"]+)"')


def net_tie_classes(pcb_text: str, bg) -> dict[str, int]:
    """Nets a net-tie footprint joins on purpose -> one class id per joined
    group. A short whose nets all share a class is the design, not a defect."""
    parent: dict[str, str] = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for m in re.finditer(r"\(footprint\s", pcb_text):
        block = pcb_text[m.start():_sexp_end(pcb_text, m.start())]
        tie = _NET_TIE.search(block)
        ref = _REF.search(block)
        if not tie or not ref:
            continue
        nets = {str(p.number): p.net for p in bg.pads_of(ref=ref.group(1))}
        for group in re.findall(r'"([^"]*)"', tie.group(1)):
            members = {nets.get(n.strip()) for n in group.split(",")}
            members.discard(None)
            members = sorted(members)
            for other in members[1:]:
                parent[find(other)] = find(members[0])
    roots = sorted({find(n) for n in parent})
    return {n: roots.index(find(n)) for n in parent}


# ------------------------------------------------------------ copper side

class _DSU:
    def __init__(self):
        self.p: dict = {}

    def find(self, a):
        p = self.p
        while p.setdefault(a, a) != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


_FILE_FN = re.compile(r"TF\.FileFunction,(?:Plated|NonPlated),(\d+),(\d+)", re.I)


def drill_span(path, n_layers: int) -> tuple[int, int]:
    """The copper layers (1-based, physical order) a drill file's holes
    join: KiCad writes `TF.FileFunction,Plated,<from>,<to>,<PTH|Blind|
    Buried>` in each file's header. No header = the full stack."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        head = ""
    m = _FILE_FN.search(head)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return max(a, 1), min(b, n_layers)
    return 1, n_layers


def compare(fab, d: D356, ties: dict[str, int] | None = None) -> dict:
    """Connectivity of the shipped copper vs the D-356 netlist."""
    import gerblib

    ties = ties or {}
    layers = fab.copper_layer_names()
    n = len(layers)
    islands: dict[str, list] = {}
    trees: dict[str, STRtree] = {}
    for name in layers:
        lg = fab.copper(name)
        comps = lg.components() if lg is not None else []
        islands[name] = comps
        trees[name] = STRtree(comps) if comps else None
    dsu = _DSU()
    for name in layers:
        for i in range(len(islands[name])):
            dsu.find((name, i))

    def hits(layer: str, geom) -> list[int]:
        tree = trees.get(layer)
        if tree is None:
            return []
        return [int(i) for i in tree.query(geom, predicate="intersects")]

    # Plated holes join every layer of their span they touch copper on.
    n_holes = 0
    for path in fab.drill_files:
        lo, hi = drill_span(path, n)
        try:
            shapes = [Point(h.x, h.y).buffer(max(h.diameter, 0.01) / 2.0,
                                             quad_segs=8)
                      for h in gerblib.read_drill(path) if h.plated]
            shapes += [s.poly for s in gerblib.read_slots(path) if s.plated]
        except Exception:
            continue
        for disc in shapes:
            n_holes += 1
            found = [(layers[k], i) for k in range(lo - 1, hi)
                     for i in hits(layers[k], disc)]
            for node in found[1:]:
                dsu.union(found[0], node)

    def locate(tp: TestPoint):
        if tp.access == 0:
            order = [layers[0], layers[-1]] + layers[1:-1]
        elif 1 <= tp.access <= n:
            order = [layers[tp.access - 1]]
        else:
            order = [layers[-1]]
        pt = Point(tp.x, tp.y)
        for layer in order:
            got = hits(layer, pt.buffer(_HIT_MM))
            if got:
                return (layer, got[0])
        reach = min((s for s in tp.size if s > 0), default=0.0) / 2.0
        for layer in order:
            tree = trees.get(layer)
            if tree is None or reach <= 0:
                continue
            i = int(tree.nearest(pt))
            if islands[layer][i].distance(pt) <= reach:
                return (layer, i)
        return None

    nets_in: dict = defaultdict(lambda: defaultdict(list))   # root -> net -> tps
    roots_of: dict = defaultdict(lambda: defaultdict(list))  # net -> root -> tps
    no_copper = []
    n_points = 0
    for tp in d.points:
        if tp.code == "367" or tp.key == NO_NET:
            continue
        n_points += 1
        node = locate(tp)
        if node is None:
            no_copper.append(tp)
            continue
        root = dsu.find(node)
        nets_in[root][tp.key].append(tp)
        roots_of[tp.key][root].append(tp)

    def where(root) -> dict:
        layer, i = root
        x0, y0, x1, y1 = islands[layer][i].bounds
        return {"layer": layer, "bbox": [round(v, 3) for v in (x0, y0, x1, y1)]}

    shorts = []
    for root, by_net in nets_in.items():
        if len(by_net) < 2:
            continue
        nets = sorted(by_net)
        if len({ties.get(nm, ("solo", nm)) for nm in nets}) == 1:
            continue          # a net tie joins exactly these nets
        # the nearest pair of points from different nets: where to look
        best = None
        for a in range(len(nets)):
            for b in range(a + 1, len(nets)):
                for pa in by_net[nets[a]]:
                    for pb in by_net[nets[b]]:
                        dd = (pa.x - pb.x) ** 2 + (pa.y - pb.y) ** 2
                        if best is None or dd < best[0]:
                            best = (dd, pa, pb)
        _, pa, pb = best
        shorts.append({
            "nets": nets,
            "near": [pa.as_dict(), pb.as_dict()],
            "at": [round((pa.x + pb.x) / 2, 3), round((pa.y + pb.y) / 2, 3)],
            "points": {nm: [p.label for p in by_net[nm]][:6] for nm in nets},
            "island": where(root)})
    opens = []
    for net, by_root in roots_of.items():
        if len(by_root) < 2:
            continue
        frags = sorted(by_root.values(), key=len, reverse=True)
        opens.append({
            "net": net,
            "fragments": [[p.label for p in f][:8] for f in frags],
            "n_fragments": len(frags),
            "at": [round(frags[-1][0].x, 3), round(frags[-1][0].y, 3)]})
    for tp in no_copper:
        opens.append({"net": tp.key, "no_copper": tp.as_dict(),
                      "fragments": [[tp.label]], "n_fragments": 1,
                      "at": [round(tp.x, 3), round(tp.y, 3)]})
    shorts.sort(key=lambda s: s["nets"])
    opens.sort(key=lambda o: (o["net"], "no_copper" in o))
    return {"shorts": shorts, "opens": opens, "n_points": n_points,
            "n_nets": len(roots_of), "n_plated_holes": n_holes,
            "n_islands": {k: len(v) for k, v in islands.items()},
            "fragments_per_net": {net: len(r) for net, r in roots_of.items()}}
