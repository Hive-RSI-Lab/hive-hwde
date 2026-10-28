#!/usr/bin/env python
"""lib_refdes_norm.py - normalize refdes text offsets in a footprint library.

easyeda2kicad emits EVERY footprint with its reference text at a blanket
(0, -4.0) mm, regardless of part size. On an 0603 (pad extent ~1.2 mm) that
puts the label ~3 mm clear of its own part and often nearer a neighbour, which
is unreadable on a populated board - and it is inherited by every board built
from the library, so it is a library defect rather than a per-board one.

This sets each footprint's reference offset from the footprint's OWN geometry:
centred in x, and just above the pad bounding box by half the text height plus
a margin. Idempotent - re-run after any lib_pull refresh.

Text surgery, not a sexp round-trip: only the reference block's `(at ...)`
numbers are rewritten, so nothing else in the file can be reformatted.

Usage:
  lib_refdes_norm.py --lib boards/<b>/lib/aiee.pretty [--margin 0.25]
                     [--min-offset 1.0] [--dry-run] [--out report.json]

Exit 0 ok (report lists every change), 2 error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

# (fp_text reference "R5" (at 0 -4 0)   |   (property "Reference" "R5" ... (at 0 -4 0)
_REF_HEAD = re.compile(r'\((?:fp_text\s+reference|property\s+"Reference")', re.S)
_AT = re.compile(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)((?:\s+-?[\d.]+)?)\s*\)')
_PAD = re.compile(
    r'\(pad\s+[^\s]+\s+\S+\s+\S+.*?\(at\s+(-?[\d.]+)\s+(-?[\d.]+)'
    r'(?:\s+-?[\d.]+)?\s*\).*?\(size\s+([\d.]+)\s+([\d.]+)\s*\)', re.S)
_TEXT_H = re.compile(r'\(size\s+([\d.]+)\s+([\d.]+)\s*\)')
_THICK = re.compile(r'\(thickness\s+([\d.]+)\s*\)')
# any silk graphic primitive's coordinate pairs
# NB layer names are QUOTED in KiCad-10 stock footprints but UNQUOTED in the
# older format easyeda2kicad emits - `(layer F.SilkS)`. Requiring quotes made
# silk detection silently return nothing and produce a plausible wrong answer.
_SILK_ITEM = re.compile(
    r'\((?:fp_line|fp_rect|fp_poly|fp_circle|fp_arc)\b(.*?)'
    r'\(layer\s+"?([^"\s)]+)"?\s*\)', re.S)
_XY = re.compile(r'\((?:start|end|center|mid|xy)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)')


def pad_top(text: str) -> float | None:
    """Smallest pad y minus half its height (KiCad y grows downward)."""
    tops = []
    for m in _PAD.finditer(text):
        py, sh = float(m.group(2)), float(m.group(4))
        tops.append(py - sh / 2.0)
    return min(tops) if tops else None


def silk_top(text: str) -> float | None:
    """Topmost y of any F./B.SilkS graphic. The footprint's own outline often
    extends past its pads, and a label placed only clear of PADS then collides
    with that outline - which is how the first version of this script produced
    283 DRC silk warnings across 85 of 111 refdes."""
    tops = []
    for m in _SILK_ITEM.finditer(text):
        if "SilkS" not in m.group(2):
            continue
        for xy in _XY.finditer(m.group(1)):
            tops.append(float(xy.group(2)))
    return min(tops) if tops else None


def inked_half_height(text: str, start: int) -> float:
    """Half the INKED height of the reference text, not its nominal size.

    KiCad's DRC measures the inked box: glyph height plus stroke thickness
    (measured: nominal size 1.0 + thickness 0.15 -> 1.162 mm inked, whereas
    GetTextBox reports 1.6965 mm). Using size/2 under-reserves by ~0.08 mm per
    side and lands the label on its own silk."""
    h, t = 1.0, 0.15
    tm = _TEXT_H.search(text, start, start + 400)
    if tm:
        h = float(tm.group(2))
    th = _THICK.search(text, start, start + 400)
    if th:
        t = float(th.group(1))
    return (h + t) / 2.0


def normalize(path: Path, margin: float, min_offset: float,
              dry_run: bool) -> dict:
    text = path.read_text(encoding="utf-8")
    head = _REF_HEAD.search(text)
    if not head:
        return {"footprint": path.stem, "status": "skipped",
                "detail": "no reference text block"}
    at = _AT.search(text, head.end())
    if not at:
        return {"footprint": path.stem, "status": "skipped",
                "detail": "reference block has no (at ...)"}

    ptop = pad_top(text)
    stop = silk_top(text)
    tops = [t for t in (ptop, stop) if t is not None]
    if not tops:
        return {"footprint": path.stem, "status": "skipped",
                "detail": "no pads or silk to measure against"}
    top = min(tops)          # clear the footprint's OWN silk, not just its pads

    half = inked_half_height(text, at.end())
    old = (float(at.group(1)), float(at.group(2)))
    new_y = top - (half + margin)
    if abs(new_y) < min_offset:
        new_y = -min_offset
    new = (0.0, round(new_y, 3))

    if abs(old[0] - new[0]) < 1e-6 and abs(old[1] - new[1]) < 1e-6:
        return {"footprint": path.stem, "status": "unchanged",
                "offset": list(old)}

    rot = at.group(3) or ""
    repl = f"(at {new[0]:g} {new[1]:g}{rot})"
    if not dry_run:
        path.write_text(text[:at.start()] + repl + text[at.end():],
                        encoding="utf-8")
    return {"footprint": path.stem, "status": "changed",
            "from": list(old), "to": list(new),
            "pad_top": round(ptop, 3) if ptop is not None else None,
            "silk_top": round(stop, 3) if stop is not None else None,
            "clear_of": round(top, 3), "inked_half_h": round(half, 3)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", required=True, help="a .pretty directory")
    ap.add_argument("--margin", type=float, default=0.25,
                    help="gap between pad edge and text edge, mm (default 0.25)")
    ap.add_argument("--min-offset", type=float, default=1.0,
                    help="never place the label closer than this, mm")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        lib = Path(args.lib)
        if not lib.is_dir():
            raise RuntimeError(f"not a directory: {lib}")
        rows = [normalize(f, args.margin, args.min_offset, args.dry_run)
                for f in sorted(lib.glob("*.kicad_mod"))]
        changed = [r for r in rows if r["status"] == "changed"]
        payload = {
            "script": "lib_refdes_norm", "status": "pass",
            "lib": str(lib), "dry_run": bool(args.dry_run),
            "footprints": len(rows), "changed": len(changed),
            "unchanged": sum(1 for r in rows if r["status"] == "unchanged"),
            "skipped": sum(1 for r in rows if r["status"] == "skipped"),
            "results": rows,
        }
    except Exception as exc:  # noqa: BLE001
        out = {"script": "lib_refdes_norm", "status": "error", "error": str(exc)}
        print(json.dumps(out, indent=1))
        return 2

    text = json.dumps(payload, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
