"""pullfix - text surgery lib_pull runs on what one EasyEDA pull wrote.

Two defects easyeda2kicad hands over as-is:

shared footprint names (#412)
    EasyEDA names footprints by package (SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL),
    so two different parts can write the same <lib>.pretty/<name>.kicad_mod:
    hw-lab's vna pulled USBLC6-2SC6 (C7519) and SN74LVC1G3157 (C10426) onto
    one file, the second write replacing the first - and with it the first
    pull's fpfix repairs (a courtyard rebuilt round the pads came back as the
    body outline, #438). `same_pads` compares the copper; lib_pull then puts
    the old file back when the pads agree, or, when they differ and another
    part's symbol names the file, keeps the old one and writes the new one as
    <name>_<LCSC>, pointing this part's symbol at it (`rename_footprint`,
    `set_symbol_footprint`).

duplicate pin numbers (#393)
    EasyEDA numbers a USB-A receptacle's two shell legs both "0" (C2346:
    symbol pins "0","0", pads 0,0). A generator that wires by number reaches
    one of them; the other symbol pin floats. `renumber_duplicate_pins` gives
    each such pin, and the pad it pairs with, its own number past the
    highest one in use (5, 6), when the symbol and the footprint carry the
    same count of that number; otherwise it only reports.

Substring edits on paren-scanned blocks (fpfix.top_level_nodes), never a
reformatting parse: the files stay diffable.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import fpfix
import fplib

SYM_NAME = re.compile(r'\(\s*symbol\s+"((?:[^"\\]|\\.)*)"')
_FP_PROP = re.compile(r'(\(\s*property\s+"Footprint"\s+")((?:[^"\\]|\\.)*)(")')
_PIN_NUM = re.compile(r'\(\s*number\s+"((?:[^"\\]|\\.)*)"')
_PIN_NAME = re.compile(r'\(\s*name\s+"((?:[^"\\]|\\.)*)"')
_PIN = re.compile(r'\(\s*pin\s')
_PAD = re.compile(r'\(\s*pad\s+("(?:[^"\\]|\\.)*"|[^\s()]+)')
_FP_HEAD = re.compile(r'\(\s*(?:module|footprint)\s+("(?:[^"\\]|\\.)*"|[^\s()]+)')


# ------------------------------------------------------------ symbol blocks

def symbol_block(text: str, name: str) -> tuple[int, int] | None:
    """(start, end) of the top-level (symbol "name" ...) block, or None."""
    for head, s, e in fpfix.top_level_nodes(text):
        if head == "symbol":
            m = SYM_NAME.match(text[s:e])
            if m and m.group(1) == name:
                return s, e
    return None


def set_symbol_footprint(text: str, name: str, footprint: str) -> str | None:
    """The symbol lib text with symbol `name`'s Footprint property set to
    `footprint`; None when the symbol or the property is missing."""
    blk = symbol_block(text, name)
    if blk is None:
        return None
    s, e = blk
    body = text[s:e]
    if not _FP_PROP.search(body):
        return None
    body = _FP_PROP.sub(lambda m: m.group(1) + footprint + m.group(3), body,
                        count=1)
    return text[:s] + body + text[e:]


# ------------------------------------------------------------ footprints

def _pad_map(fp_text: str) -> dict:
    """{(x, y, w, h, drilled): sorted pad numbers there} for the copper pads."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.kicad_mod"
        p.write_text(fp_text, encoding="utf-8")
        fp = fplib.parse_footprint(p)
    out: dict = {}
    for pad in fp.copper_pads:
        key = (round(pad.at[0], 3), round(pad.at[1], 3),
               round(pad.size[0], 3), round(pad.size[1], 3),
               pad.ptype == "thru_hole")
        out.setdefault(key, []).append(pad.number or "")
    return {k: sorted(v) for k, v in out.items()}


def same_pads(old_text: str, new_text: str) -> bool:
    """Same copper at the same places with the same numbers. A pad fpfix
    numbered (an exposed pad's thermal vias, blank as pulled) matches a
    blank one."""
    a, b = _pad_map(old_text), _pad_map(new_text)
    if a.keys() != b.keys():
        return False
    for k in a:
        if len(a[k]) != len(b[k]):
            return False
        na = [n for n in a[k] if n]
        nb = [n for n in b[k] if n]
        if na and nb and set(na) != set(nb):
            return False
    return True


def rename_footprint(fp_text: str, new_name: str) -> str:
    """The footprint text with its own name (module/footprint head) set to
    new_name, keeping any lib prefix the head carried."""
    m = _FP_HEAD.search(fp_text)
    if not m:
        return fp_text
    tok = m.group(1)
    quoted = tok.startswith('"')
    raw = tok[1:-1] if quoted else tok
    lib = raw.split(":")[0] + ":" if ":" in raw else ""
    new = f'"{lib}{new_name}"' if quoted else f"{lib}{new_name}"
    return fp_text[:m.start(1)] + new + fp_text[m.end(1):]


# ------------------------------------------------------------ duplicate pins

def _num_key(n: str) -> int | None:
    return int(n) if n.isdigit() else None


def _pins(block: str) -> list[tuple[int, int, str, tuple | None]]:
    """[(pin start, pin end, number, (name start, name end, name) | None)]
    for every (pin ...) node inside a symbol block."""
    out = []
    for m in _PIN.finditer(block):
        s = m.start()
        depth, i, in_str, esc = 0, s, False, False
        while i < len(block):
            ch = block[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        e = i + 1
        pin = block[s:e]
        nm = _PIN_NUM.search(pin)
        if not nm:
            continue
        na = _PIN_NAME.search(pin)
        out.append((s, e, nm.group(1),
                    (s + na.start(1), s + na.end(1), na.group(1)) if na
                    else None))
    return out


def _pads(fp_text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _PAD.finditer(fp_text):
        tok = m.group(1)
        num = tok[1:-1] if tok.startswith('"') else tok
        out.append((m.start(1), m.end(1), num))
    return out


def renumber_duplicate_pins(sym_text: str, name: str, fp_text: str
                            ) -> tuple[str, str, list[dict], list[str]]:
    """-> (sym_text, fp_text, changes, warnings). For every pin number the
    symbol `name` carries more than once: when the footprint has exactly as
    many pads with that number, the k-th pin and the k-th pad (file order)
    both get a fresh number past the highest numeric one in use; a pin name
    equal to its number follows it. A count mismatch is only warned."""
    blk = symbol_block(sym_text, name)
    if blk is None:
        return sym_text, fp_text, [], []
    s0, e0 = blk
    block = sym_text[s0:e0]
    pins = _pins(block)
    pads = _pads(fp_text)
    counts: dict[str, int] = {}
    for p in pins:
        counts[p[2]] = counts.get(p[2], 0) + 1
    dups = sorted(n for n, c in counts.items() if c > 1)
    if not dups:
        return sym_text, fp_text, [], []
    used = [k for k in (_num_key(n) for n in
                        [p[2] for p in pins] + [p[2] for p in pads])
            if k is not None]
    nxt = max(used, default=0) + 1
    changes, warnings = [], []
    sym_edits: list[tuple[int, int, str]] = []
    fp_edits: list[tuple[int, int, str]] = []
    for n in dups:
        dpins = [p for p in pins if p[2] == n]
        dpads = [p for p in pads if p[2] == n]
        if len(dpads) != len(dpins):
            warnings.append(f"{name}: pin number {n!r} on {len(dpins)} pins "
                            f"but {len(dpads)} pads - left as pulled")
            continue
        for pin, pad in zip(dpins, dpads):
            new = str(nxt)
            nxt += 1
            ps, pe, _num, nm = pin
            seg = block[ps:pe]
            m = _PIN_NUM.search(seg)
            sym_edits.append((ps + m.start(1), ps + m.end(1), new))
            if nm is not None and nm[2] == n:
                sym_edits.append((nm[0], nm[1], new))
            tok = fp_text[pad[0]:pad[1]]
            fp_edits.append((pad[0], pad[1],
                             f'"{new}"' if tok.startswith('"') else new))
            changes.append({"symbol": name, "from": n, "to": new})
    for s, e, rep in sorted(sym_edits, key=lambda t: -t[0]):
        block = block[:s] + rep + block[e:]
    for s, e, rep in sorted(fp_edits, key=lambda t: -t[0]):
        fp_text = fp_text[:s] + rep + fp_text[e:]
    return sym_text[:s0] + block + sym_text[e0:], fp_text, changes, warnings
