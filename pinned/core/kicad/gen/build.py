"""core: the Plus/Ultra core board schematic generator (hifi-receiver-2).

The design is described as data in design.py: one entry per part with its
symbol, footprint, LCSC number and a pin -> net map. This file packs those
parts onto one child sheet per block, stitches every net that crosses
blocks through hierarchical pins on the root sheet, hangs a power symbol on
every rail pin, and writes core.kicad_sch (+ children), decoupling.json and
parts/parts.json.

Rebuild (from the board workspace):
  eval "$(sh $HIVE_MODULES/hive-hwde/bin/hwde env)"
  PYTHONPATH=$HWDE_SKILL/scripts:$HWDE_SKILL/scripts/lib $HWDE_PY kicad/gen/build.py
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
BOARD = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

import schlib  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()), \
        contextlib.redirect_stderr(io.StringIO()):
    schlib.ksa.get_symbol_cache().add_library_path(
        str(BOARD / "lib" / "aiee.kicad_sym"))

import design  # noqa: E402

NAME = "core"
G = schlib.GRID

# Rails: global through power symbols, never through sheet pins.
RAIL_SYM = {"GND": "power:GND"}
for _r in design.RAILS:
    RAIL_SYM.setdefault(_r, "power:VCC")


def snapup(v: float) -> float:
    return math.ceil(v / G - 1e-9) * G


_extent_cache: dict[str, tuple] = {}


def extents(lib_id: str):
    """Pin bounding box relative to the anchor, plus the outward direction
    of each pin, measured on a scratch sheet."""
    if lib_id in _extent_cache:
        return _extent_cache[lib_id]
    sh = schlib.Sheet("_scratch")
    sh.add_component(lib_id, "X1", "x", at=(127.0, 127.0))
    pins = {}
    c = sh.sch.components.get("X1")
    for p in c.pins:
        x, y = sh.pin_pos("X1", p.number)
        pins[p.number] = (x - 127.0, y - 127.0, sh._pin_out_dir("X1", p.number))
    xs = [v[0] for v in pins.values()] or [0]
    ys = [v[1] for v in pins.values()] or [0]
    ext = (min(xs), max(xs), min(ys), max(ys), pins,
           {p.number: p.name for p in c.pins})
    _extent_cache[lib_id] = ext
    return ext


_sym_fp: dict[str, str] = {}


def symbol_footprint(lib_id: str) -> str | None:
    """The Footprint property lib_pull wrote on an aiee symbol."""
    if not _sym_fp:
        import re
        text = (BOARD / "lib" / "aiee.kicad_sym").read_text(encoding="utf-8")
        cur = None
        for line in text.splitlines():
            m = re.match(r'^  \(symbol "([^"]+)"', line)
            if m:
                cur = m.group(1)
            m = re.search(r'"Footprint"\s*$', line) or re.search(
                r'\(property "Footprint" "([^"]*)"', line)
            if m and cur:
                if m.groups():
                    _sym_fp["aiee:" + cur] = m.group(1)
                else:
                    _sym_fp["aiee:" + cur] = "__next__"
            elif cur and _sym_fp.get("aiee:" + cur) == "__next__":
                v = re.search(r'"([^"]*)"', line)
                if v:
                    _sym_fp["aiee:" + cur] = v.group(1)
    return _sym_fp.get(lib_id)


def resolve_pins(part: dict) -> dict:
    """design entries may name pins by number or by NAME (prefix 'n:');
    a name maps to every pin carrying it."""
    _, _, _, _, pins, names = extents(part["sym"])
    out = {}
    for key, net in part["pins"].items():
        if key.startswith("n:"):
            want = key[2:]
            hits = [n for n, nm in names.items() if nm == want]
            if not hits:
                raise ValueError(f"{part['ref']}: no pin named {want} "
                                 f"(has {sorted(set(names.values()))})")
            for h in hits:
                out[h] = net
        else:
            if key not in names:
                raise ValueError(f"{part['ref']}: no pin {key}")
            out[key] = net
    missing = [n for n in names if n not in out]
    if missing and not part.get("nc_rest"):
        raise ValueError(f"{part['ref']}: pins not mapped {missing} "
                         f"({[names[m] for m in missing]})")
    for m in missing:
        out[m] = "NC"
    return out


def build():
    parts = design.parts()
    refs = [p["ref"] for p in parts]
    dup = {r for r in refs if refs.count(r) > 1}
    if dup:
        raise ValueError(f"duplicate refs {sorted(dup)}")
    for p in parts:
        p["resolved"] = resolve_pins(p)
        fp = symbol_footprint(p["sym"])
        if fp:
            p["fp"] = fp if ":" in fp else "aiee:" + fp

    # which sheets use each net
    net_sheets = defaultdict(set)
    for p in parts:
        for net in p["resolved"].values():
            if net != "NC":
                net_sheets[net].add(p["sheet"])
    single = [n for n, s in net_sheets.items()
              if n not in RAIL_SYM and sum(
                  1 for p in parts for v in p["resolved"].values() if v == n) < 2]
    if single:
        raise ValueError(f"nets with one pin only: {sorted(single)}")

    root = schlib.Sheet(NAME, title="core: Plus/Ultra main board",
                        paper="A3", pwr_base=1)
    proj = schlib.Project(root)
    pwr_base = 1000
    decoupling = []
    sheet_names = [s for s, _ in design.SHEETS]
    for si, (sname, (paper, width)) in enumerate(design.SHEETS):
        sp = [p for p in parts if p["sheet"] == sname]
        sh = schlib.Sheet(sname, title=f"core: {design.SHEET_TITLES[sname]}",
                          paper=paper, pwr_base=pwr_base)
        pwr_base += 1000
        cross = sorted(n for n, s in net_sheets.items()
                       if sname in s and len(s) > 1 and n not in RAIL_SYM)
        # hierarchical pins in a column on the left
        y = 25.4
        for net in cross:
            sh.hier_pin(net, shape="passive", at=(12.7, y))
            y += 2 * G
        # pack parts in rows to the right of the hier column
        x0 = 50.8 + max([len(n) for n in cross] or [0]) * 1.27
        x0 = snapup(x0)
        x, y, row_h = x0, 25.4, 0.0
        for p in sp:
            xmin, xmax, ymin, ymax, pins, _ = extents(p["sym"])
            lab = {"l": 0, "r": 0}
            for num, net in p["resolved"].items():
                d = pins[num][2]
                n = len(net) if net != "NC" else 0
                if d[0] < 0:
                    lab["l"] = max(lab["l"], n)
                elif d[0] > 0:
                    lab["r"] = max(lab["r"], n)
            left = snapup(-xmin + 2 * 2.54 + lab["l"] * 1.27 + 2.54)
            right = snapup(xmax + 2 * 2.54 + lab["r"] * 1.27 + 5.08)
            up = snapup(-ymin + 10.16)
            down = snapup(ymax + 10.16)
            w = left + right
            if x + w > width - 12.7 and x > x0:
                x = x0
                y += row_h
                row_h = 0.0
            at = (snapup(x + left), snapup(y + up))
            row_h = max(row_h, up + down)
            x += w
            fields = {"LCSC": p["lcsc"]} if p.get("lcsc") else {}
            if p.get("mpn"):
                fields["MPN"] = p["mpn"]
            sh.add_component(p["sym"], p["ref"], p["value"], at,
                             footprint=p["fp"], fields=fields)
            for num, net in sorted(p["resolved"].items()):
                sh.wire_pin(p["ref"], num, net)
                if net in RAIL_SYM:
                    sh.power_symbol_at_pin(p["ref"], num, RAIL_SYM[net])
            if p.get("dnp"):
                sh.mark_dnp(p["ref"])
        for d in design.DECOUPLING:
            if d["cap"] in [p["ref"] for p in sp]:
                d = dict(d)
                rail = d["rail"]
                if rail not in RAIL_SYM:
                    d["rail"] = (f"/{rail}" if len(net_sheets[rail]) > 1
                                 else f"/{sname}/{rail}")
                decoupling.append(d)
        cols = 4
        proj.add_sheet(sh, at=(25.4 + (si % cols) * 96.52,
                               38.1 + (si // cols) * 127.0),
                       size=(50.8, max(12.7, snapup(2 * G * (len(cross) + 2)))),
                       nets=cross)
    # rails: one PWR_FLAG each on the root
    y = 254.0
    for i, rail in enumerate(sorted(RAIL_SYM)):
        root.power_flag(rail, at=(25.4 + (i % 6) * 60.96,
                                  y + (i // 6) * 12.7),
                        sym=RAIL_SYM[rail], flag=True)
    return proj, parts, decoupling


def write_parts(parts, path: Path, variant: str = "plus"):
    """parts.json for one build. Plus: Ultra-only parts (dnp) ship empty.
    Ultra: those are fitted and the Plus-only front-edge controls are not."""
    lines = defaultdict(lambda: {"refdes": [], "refdes_class": {}})
    for p in parts:
        key = p.get("lcsc") or f"{p['value']}|{p['fp']}"
        ln = lines[key]
        ln["refdes"].append(p["ref"])
        off = p.get("dnp") if variant == "plus" else p.get("plus_only")
        ln["refdes_class"][p["ref"]] = "dnp" if off else "smt_placed"
        ln.setdefault("lcsc", p.get("lcsc", ""))
        ln.setdefault("mpn", p.get("mpn") or p["value"])
        ln.setdefault("value", p["value"])
        ln.setdefault("package", p["fp"].split(":")[-1])
        ln.setdefault("block", p["sheet"])
        ln.setdefault("basic", p.get("basic", False))
        ln.setdefault("role", p.get("role", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"variant": variant, "parts": list(lines.values())},
                               indent=1) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    out_dir = Path(argv[0]) if argv else HERE.parents[1]
    try:
        proj, parts, dec = build()
        sch = proj.save(out_dir)
        (out_dir / "decoupling.json").write_text(
            json.dumps({"associations": dec}, indent=1) + "\n")
        write_parts(parts, BOARD / "parts" / "parts.json", "plus")
        write_parts(parts, BOARD / "parts" / "parts-ultra.json", "ultra")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"script": "gen.build", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps({"script": "gen.build", "status": "pass",
                      "root": str(sch), "parts": len(parts),
                      "dnp": sum(1 for p in parts if p.get("dnp")),
                      "decoupling": len(dec)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
