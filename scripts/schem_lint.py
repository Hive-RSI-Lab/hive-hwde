#!/usr/bin/env python
"""schem_lint.py - what makes a drawn schematic hard to read, as numbers.

Draws each sheet with kicad-cli and reads the text boxes back from the PDF, so
the count is of what a person sees (pin names and numbers included), not of
what the layout code thinks it placed. Counts per sheet:

  text_text   two words overlap
  text_wire   a wire runs through a word
  text_part   a word sits on a two-pin part's body
  close_wires two parallel wires less than MIN_PITCH apart

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2.
  0 = measured, and within --max-* if any were given
  1 = over a limit
  2 = internal error / bad arguments

Example:
  schem_lint.py --sch boards/b/kicad/b.kicad_sch --max-text-text 0
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import schem_layout as sl  # noqa: E402
import schem_refdes as sr  # noqa: E402

PT_PER_MM = 72 / 25.4
MIN_PITCH = 2.54         # mm: the pitch of a symbol's pins
MIN_OVERLAP = 0.25       # mm: both ways, so a kerning touch isn't an overlap
TITLE_W, TITLE_H = 115.0, 45.0   # the title block, up from the page's corner
INK = 0.23               # half the glyphs' height, of the word box's line height
CHAR_ASPECT = 0.42       # a letter's advance, of the word box's line height
FRAME = 13.0             # the page frame and its tick labels

_WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">'
                   r'(.*?)</word>')
_PAGE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)"')


def words(pdf_html: str) -> list[tuple]:
    """(x0, y0, x1, y1, text) in mm, trimmed to the glyphs: a PDF word's box
    carries the font's whole line height, not the ink."""
    m = _PAGE.search(pdf_html)
    pw, ph = float(m.group(1)) / PT_PER_MM, float(m.group(2)) / PT_PER_MM
    raw = _WORD.findall(pdf_html)
    # level text has a word box as tall as its font's line, whatever the letters
    tall = Counter(round((float(w[3]) - float(w[1])) / PT_PER_MM, 2) for w in raw)
    lines = {h for h, n in tall.items() if n >= max(0.05 * len(raw), 2)}
    out = []
    for w in _WORD.finditer(pdf_html):
        x0, y0, x1, y1 = (float(w.group(i)) / PT_PER_MM for i in range(1, 5))
        if x0 < FRAME or y0 < FRAME or x1 > pw - FRAME or y1 > ph - FRAME:
            continue
        if x1 > pw - TITLE_W and y1 > ph - TITLE_H:
            continue
        # level text is about CHAR_ASPECT line heights wide per letter, whatever
        # the font size; on its side the box's ratio is the inverse
        expect = max(len(html.unescape(w.group(5))), 1) * CHAR_ASPECT
        level = round(y1 - y0, 2) in lines or (
            round(x1 - x0, 2) not in lines
            and math.log((x1 - x0) / (y1 - y0)) * math.log(expect) >= 0)
        if level:
            mid, half = (y0 + y1) / 2, (y1 - y0) * INK
            y0, y1 = mid - half, mid + half
        else:
            mid, half = (x0 + x1) / 2, (x1 - x0) * INK
            x0, x1 = mid - half, mid + half
        out.append((x0, y0, x1, y1, html.unescape(w.group(5))))
    return out


def _hit(a, b, m=MIN_OVERLAP) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > m and min(a[3], b[3]) - max(a[1], b[1]) > m


def _segs(sheet: sl.Sheet) -> list[tuple]:
    out = []
    for pts in sheet.wires:
        for a, b in zip(pts, pts[1:]):
            out.append((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])))
    return out


def close_wires(segs: list[tuple]) -> list[tuple]:
    """Parallel pairs nearer than MIN_PITCH, running side by side for at least
    a pin pitch: a wire stepping past another's end isn't a pair."""
    bad = []
    hs = [s for s in segs if s[1] == s[3] and s[0] != s[2]]
    vs = [s for s in segs if s[0] == s[2] and s[1] != s[3]]
    for group, along, across in ((hs, 0, 1), (vs, 1, 0)):
        group = sorted(group, key=lambda s: s[across])
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                gap = b[across] - a[across]
                if gap >= MIN_PITCH - 1e-3:
                    break
                if gap < 1e-3:
                    continue
                run = min(a[along + 2], b[along + 2]) - max(a[along], b[along])
                if run >= MIN_PITCH - 1e-3:
                    bad.append((a, b, round(gap, 2), round(run, 2)))
    return bad


def text_wire(ws: list[tuple], segs: list[tuple]) -> list[tuple]:
    bad = []
    for w in ws:
        for s in segs:
            # a wire is a line: pad it so the box test sees it
            if _hit(w, (s[0] - 0.13, s[1] - 0.13, s[2] + 0.13, s[3] + 0.13), 0.2):
                bad.append((w[4], tuple(round(v, 2) for v in s)))
                break
    return bad


def measure(sch: Path, cli: str) -> dict:
    sheet = sl.Sheet(sch)
    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "s.pdf"
        subprocess.run([cli, "sch", "export", "pdf", "-o", str(pdf), str(sch)],
                       capture_output=True, text=True, check=True, timeout=300)
        box = Path(td) / "b.html"
        subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-bbox", str(pdf), str(box)],
                       capture_output=True, text=True, check=True, timeout=60)
        ws = words(box.read_text(encoding="utf-8"))
    # what a library symbol prints inside its own body is the library's, not the layout's
    ics = [s.body_box() for s in sheet.syms if not s.is_power and not sl.passive_like(s)]
    # ... and a note's own lines are its author's
    for h, s, e in sl.forms(sheet.text, *sheet.body):
        if h == "text_box":
            m = re.search(r'\(at (\S+) (\S+)[^()]*\)\s*\(size (\S+) (\S+)\)', sheet.text[s:e])
            if m:
                x, y, bw, bh = map(float, m.groups())
                ics.append((x, y, x + bw, y + bh))
    inside = [any(b[0] <= (w[0] + w[2]) / 2 <= b[2] and b[1] <= (w[1] + w[3]) / 2 <= b[3]
                  for b in ics) for w in ws]
    tt = []
    for i, a in enumerate(ws):
        for j, b in enumerate(ws[i + 1:], i + 1):
            if not (inside[i] and inside[j]) and _hit(a, b):
                tt.append((a[4], b[4], round(a[0], 1), round(a[1], 1)))
    segs = _segs(sheet)
    bodies = []
    for s in sheet.syms:
        if not s.is_power and sl.passive_like(s) and s.geo:
            # the drawn body only: a pin's own line may carry text beside it
            pts = [sr._xform_geom(g, s.at, s.mirror).bounds for g in s.geo["graphics"]]
            if pts:
                bodies.append((min(p[0] for p in pts), min(p[1] for p in pts),
                               max(p[2] for p in pts), max(p[3] for p in pts)))
    tp = [w[4] for w in ws if any(_hit(w, b) for b in bodies)]
    tw = text_wire(ws, segs)
    cw = close_wires(segs)
    return {"words": len(ws), "text_text": len(tt), "text_wire": len(tw), "text_part": len(tp),
            "close_wires": len(cw), "examples": {"text_text": tt[:8], "text_wire": tw[:8],
                                                 "text_part": tp[:8], "close_wires": cw[:6]}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sch", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    for k in ("text-text", "text-wire", "text-part", "close-wires"):
        ap.add_argument(f"--max-{k}", type=int)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    try:
        cli = sl._cli()
        res = {"sheets": {}}
        for f in sl.sheet_files(a.sch):
            res["sheets"][f.name] = measure(f, cli)
        tot = {k: sum(s[k] for s in res["sheets"].values())
               for k in ("words", "text_text", "text_wire", "text_part", "close_wires")}
        res["total"] = tot
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)}))
        return 2
    text = json.dumps(res, indent=1)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    over = [k for k, lim in (("text_text", a.max_text_text), ("text_wire", a.max_text_wire),
                             ("text_part", a.max_text_part), ("close_wires", a.max_close_wires))
            if lim is not None and tot[k] > lim]
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
