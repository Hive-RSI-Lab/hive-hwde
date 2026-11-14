"""KiCad 4/5 legacy schematics -> a netlist the checks can read (#1023).

kicad-cli (KiCad 6 and later) opens a legacy `.sch`, exits 0 and writes a
netlist with no components and no nets, so every netlist check passes on a
KiCad 5 board without having read anything. This module builds a
kicadsexpr netlist in the shape kicad-cli 10 writes (each node carrying its
pintype and pinfunction), so the existing parsers read it unchanged:

1. the eeschema netlist `<stem>.net` shipped beside the schematic (export
   version "D"): its nodes carry only ref and pin number, so each node's
   name and electrical type are filled from the file's own libparts;
2. failing that, the board the schematic belongs to: nets from the
   `.kicad_pcb` pads (the copper that shipped), each footprint's symbol
   from the `$Comp` blocks of the `.sch` hierarchy, and the pins' names and
   types from the project's `.lib` files (`-cache.lib` first).

Either way the result says where it came from (`source`). Route 2 is the
board's connectivity, not the schematic's, so a schematic that disagrees
with its own board isn't seen here; a pad whose symbol or pin isn't found
keeps its number with no name.

`empty_export(sch, net)` is the loud half: True when a netlist has no
components though its schematic has some, which is what kicad-cli's export
of a legacy file looks like.
"""
from __future__ import annotations

import re
from pathlib import Path

import sexpdata

LEGACY_MAGIC = "EESchema Schematic File Version"

# legacy .lib electrical type letter -> the name kicad-cli writes
PIN_TYPES = {"I": "input", "O": "output", "B": "bidirectional",
             "T": "tri_state", "P": "passive", "U": "unspecified",
             "W": "power_in", "w": "power_out", "C": "open_collector",
             "E": "open_emitter", "N": "no_connect"}
# eeschema 5 netlist libpart pin types -> the KiCad 6+ names the checks read
NET_PIN_TYPES = {"BiDi": "bidirectional", "3state": "tri_state",
                 "unspc": "unspecified", "openCol": "open_collector",
                 "openEm": "open_emitter", "NotConnected": "no_connect"}


class LegacyError(Exception):
    pass


def is_legacy(sch: Path) -> bool:
    sch = Path(sch)
    if sch.suffix != ".sch" or not sch.is_file():
        return False
    with open(sch, encoding="utf-8", errors="replace") as fh:
        return fh.readline().startswith(LEGACY_MAGIC)


def is_legacy_net(net: Path) -> bool:
    """An eeschema 4/5 netlist (export version C or D): its nodes carry no
    pin names, which the checks read."""
    try:
        with open(net, encoding="utf-8", errors="replace") as fh:
            head = fh.read(200)
    except OSError:
        return False
    return bool(re.match(r'\s*\(export\s+\(version\s+"?[A-D]"?\)', head))


def _s(x) -> str:
    return x.value() if isinstance(x, sexpdata.Symbol) else str(x)


def _kids(node, key):
    return [x for x in node if isinstance(x, list) and x and _s(x[0]) == key]


def _atom(node, key, default=None):
    for x in node:
        if isinstance(x, list) and x and _s(x[0]) == key:
            return _s(x[1]) if len(x) > 1 else default
    return default


def _load(path: Path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sexpdata.load(fh)


# ------------------------------------------------------------ the schematic

def _sheet_files(sch: Path, seen: set | None = None) -> list[Path]:
    """The root sheet and every sub-sheet file it names, each once."""
    seen = set() if seen is None else seen
    sch = sch.resolve()
    if sch in seen or not sch.is_file():
        return []
    seen.add(sch)
    out = [sch]
    text = sch.read_text(encoding="utf-8", errors="replace")
    for blk in re.findall(r"\$Sheet\n(.*?)\$EndSheet", text, re.S):
        m = re.search(r'^F1 "([^"]+)"', blk, re.M)
        if m:
            out += _sheet_files(sch.parent / m.group(1), seen)
    return out


def sch_symbols(sch: Path) -> dict[str, str]:
    """ref -> library symbol name ("Device:R", or "R" on KiCad 4), over the
    whole hierarchy. A sheet used twice annotates its parts on AR lines."""
    refs: dict[str, str] = {}
    for f in _sheet_files(Path(sch)):
        text = f.read_text(encoding="utf-8", errors="replace")
        for blk in re.findall(r"\$Comp\n(.*?)\$EndComp", text, re.S):
            m = re.search(r"^L (\S+) (\S+)", blk, re.M)
            if not m:
                continue
            part, ref = m.groups()
            for r in [ref, *re.findall(r'^AR .*?Ref="([^"]+)"', blk, re.M)]:
                if not r.startswith("#"):
                    refs.setdefault(r, part)
    return refs


def count_sch_components(sch: Path) -> int:
    """Placed non-power symbols in a schematic, legacy or s-expression
    (root sheet and the sheets it names)."""
    sch = Path(sch)
    if is_legacy(sch):
        return len(sch_symbols(sch))
    if not sch.is_file():
        return 0
    text = sch.read_text(encoding="utf-8", errors="replace")
    n = len(re.findall(r'\(symbol\s+\(lib_id\s+"(?!power:)', text))
    for name in re.findall(r'\(property\s+"Sheet ?[Ff]ile(?:name)?"\s+"([^"]+)"',
                           text):
        sub = sch.parent / name
        if sub.resolve() != sch.resolve():
            n += count_sch_components(sub)
    return n


def lib_pins(libdir: Path, stem: str) -> dict[str, dict[str, tuple]]:
    """symbol name (and alias) -> {pin number: (name, kicad pin type)} from
    the legacy .lib files beside the schematic, <stem>-cache.lib winning."""
    files = sorted(Path(libdir).glob("*.lib"),
                   key=lambda p: (p.name != f"{stem}-cache.lib",
                                  "-rescue" not in p.name, p.name))
    out: dict[str, dict[str, tuple]] = {}
    for f in files:
        name, pins, aliases = None, {}, []
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            t = line.split()
            if not t:
                continue
            if t[0] == "DEF" and len(t) > 1:
                name, pins, aliases = t[1].lstrip("~"), {}, []
            elif t[0] == "ALIAS":
                aliases = t[1:]
            elif t[0] == "X" and name and len(t) >= 12:
                pname = "" if t[1] == "~" else t[1]
                pins.setdefault(t[2], (pname, PIN_TYPES.get(t[11], "unspecified")))
            elif t[0] == "ENDDEF" and name:
                for n in [name, *aliases]:
                    out.setdefault(n, pins)
                name = None
    return out


def _symbol_pins(lib: dict, part: str) -> dict | None:
    for key in (part.replace(":", "_"), part.split(":")[-1], part):
        if key in lib:
            return lib[key]
    return None


# ------------------------------------------------------------ the board

def board_pads(pcb: Path) -> tuple[dict, dict]:
    """KiCad 5 board -> ({ref: {value, footprint}}, {net: [(ref, pad)]})."""
    data = _load(Path(pcb))
    comps, nets = {}, {}
    for m in _kids(data, "module") + _kids(data, "footprint"):
        ref = value = None
        for t in _kids(m, "fp_text"):
            if len(t) > 2 and _s(t[1]) == "reference":
                ref = _s(t[2])
            elif len(t) > 2 and _s(t[1]) == "value":
                value = _s(t[2])
        for p in _kids(m, "property"):
            if len(p) > 2 and _s(p[1]) == "Reference":
                ref = _s(p[2])
            elif len(p) > 2 and _s(p[1]) == "Value":
                value = _s(p[2])
        if not ref or ref.startswith("#"):
            continue
        comps.setdefault(ref, {"value": value or "", "footprint": _s(m[1])})
        for pad in _kids(m, "pad"):
            num = _s(pad[1]) if len(pad) > 1 else ""
            net = _kids(pad, "net")
            if not num or not net or len(net[0]) < 3:
                continue
            name = _s(net[0][2])
            if name:
                nets.setdefault(name, [])
                if (ref, num) not in nets[name]:
                    nets[name].append((ref, num))
    return comps, nets


# ------------------------------------------------------------ writing

def _q(v) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unconnected(nets: dict) -> dict:
    """KiCad 4/5 gives every unconnected pin its own auto-named net
    'Net-(U4-Pad32)'; KiCad 6+ names those 'unconnected-(U4-Pad32)', which is
    what the checks skip. Rename single-node auto-named nets to match."""
    out = {}
    for name, nodes in nets.items():
        if len(nodes) == 1 and name and name.startswith("Net-("):
            name = "unconnected-" + name[len("Net-"):]
        out[name] = nodes
    return out


def _write(out: Path, source: str, comps: dict, nets: dict) -> None:
    """comps: ref -> {value, footprint, lib, part}; nets: name -> [(ref, pin,
    pintype, pinfunction)]."""
    lines = ["(export (version \"E\")",
             f"  (design (source {_q(source)}) (tool \"hwde legacy_net\"))",
             "  (components"]
    for ref in sorted(comps):
        c = comps[ref]
        lines.append(f"    (comp (ref {_q(ref)}) (value {_q(c.get('value') or '')})"
                     f" (footprint {_q(c.get('footprint') or '')})"
                     f" (libsource (lib {_q(c.get('lib') or '')})"
                     f" (part {_q(c.get('part') or '')})))")
    lines += ["  )", "  (nets"]
    nets = _unconnected(nets)
    for code, name in enumerate(sorted(nets), 1):
        lines.append(f"    (net (code {_q(code)}) (name {_q(name)})")
        for ref, pin, ptype, func in nets[name]:
            lines.append(f"      (node (ref {_q(ref)}) (pin {_q(pin)})"
                         f" (pintype {_q(ptype)}) (pinfunction {_q(func)}))")
        lines[-1] += ")"
    lines += ["  )", ")"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pin_type(t) -> str:
    return NET_PIN_TYPES.get(t or "", t or "unspecified")


def from_shipped(net: Path, out: Path) -> dict:
    """A version-D eeschema netlist, with each node's name and type filled
    from the file's own libparts (by the comp's libsource part or alias)."""
    data = _load(Path(net))
    if not data or _s(data[0]) != "export":
        raise LegacyError(f"{net.name}: not an eeschema netlist")
    parts: dict[str, dict] = {}
    for blk in _kids(data, "libparts"):
        for lp in _kids(blk, "libpart"):
            pins = {}
            for pb in _kids(lp, "pins"):
                for p in _kids(pb, "pin"):
                    nm = _atom(p, "name", "") or ""
                    pins[_atom(p, "num")] = ("" if nm == "~" else nm,
                                             _pin_type(_atom(p, "type")))
            names = [_atom(lp, "part")]
            for ab in _kids(lp, "aliases"):
                names += [_s(a[1]) for a in _kids(ab, "alias") if len(a) > 1]
            for n in names:
                if n:
                    parts.setdefault(n, pins)
    comps = {}
    for blk in _kids(data, "components"):
        for c in _kids(blk, "comp"):
            ref = _atom(c, "ref")
            src = (_kids(c, "libsource") or [[]])[0]
            comps[ref] = {"value": _atom(c, "value"),
                          "footprint": _atom(c, "footprint"),
                          "lib": _atom(src, "lib"), "part": _atom(src, "part")}
    nets = {}
    for blk in _kids(data, "nets"):
        for n in _kids(blk, "net"):
            nodes = []
            for nd in _kids(n, "node"):
                ref, pin = _atom(nd, "ref"), _atom(nd, "pin")
                pins = parts.get((comps.get(ref) or {}).get("part") or "") or {}
                name, ptype = pins.get(pin, ("", "unspecified"))
                nodes.append((ref, pin, _pin_type(_atom(nd, "pintype")) if
                              _atom(nd, "pintype") else ptype,
                              _atom(nd, "pinfunction") or name))
            nets[_atom(n, "name")] = nodes
    _write(out, f"{net.name} (shipped eeschema netlist)", comps, nets)
    return {"source": "shipped", "from": net.name, "components": len(comps),
            "nets": len(nets)}


def from_board(sch: Path, pcb: Path, out: Path) -> dict:
    sch, pcb = Path(sch), Path(pcb)
    bcomps, bnets = board_pads(pcb)
    syms = sch_symbols(sch)
    lib = lib_pins(sch.parent, sch.stem)
    comps, unnamed = {}, set()
    for ref, c in bcomps.items():
        part = syms.get(ref, "")
        lib_nick, _, pname = part.rpartition(":")
        comps[ref] = {**c, "lib": lib_nick, "part": pname or part}
    nets = {}
    for name, nodes in bnets.items():
        rows = []
        for ref, pad in nodes:
            pins = _symbol_pins(lib, syms.get(ref, "")) or {}
            if pad not in pins:
                unnamed.add(ref)
            pn, pt = pins.get(pad, ("", "unspecified"))
            rows.append((ref, pad, pt, pn))
        nets[name] = rows
    _write(out, f"{pcb.name} pads + {sch.name} symbols + .lib pins", comps, nets)
    return {"source": "board", "from": pcb.name, "components": len(comps),
            "nets": len(nets), "unnamed_pins_on": sorted(unnamed)}


def netlist(sch: Path, out: Path, pcb: Path | None = None) -> dict:
    """Write a checkable netlist for a legacy schematic to `out`. Returns
    {"status": "pass", "source", "from", "path", ...} or {"status": "fail",
    "error"}."""
    sch = Path(sch)
    try:
        shipped = sch.with_suffix(".net")
        if shipped.is_file():
            r = from_shipped(shipped, Path(out))
        else:
            pcb = Path(pcb) if pcb else sch.with_suffix(".kicad_pcb")
            if not pcb.is_file():
                return {"status": "fail",
                        "error": f"{sch.name} is a KiCad 4/5 schematic that "
                                 "kicad-cli exports as an empty netlist, and "
                                 f"there is no {shipped.name} or {pcb.name} "
                                 "beside it to read the nets from"}
            r = from_board(sch, pcb, Path(out))
    except (OSError, LegacyError, ValueError, IndexError) as exc:
        return {"status": "fail", "error": f"{sch.name}: {exc}"}
    if not r["components"]:
        return {"status": "fail",
                "error": f"{sch.name}: the legacy netlist has 0 components"}
    return {"status": "pass", "path": str(out), **r}


def empty_export(sch: Path, net: Path) -> int:
    """How many components the schematic has when the exported netlist has
    none (kicad-cli on a legacy file); 0 when the netlist is fine."""
    try:
        text = Path(net).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if re.search(r"\(comp\s", text):
        return 0
    return count_sch_components(Path(sch))
