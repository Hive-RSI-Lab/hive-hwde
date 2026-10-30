"""Grow EasyEDA-pulled courtyards to cover their pads (+0.25 mm).

lib_pull's EasyEDA footprints draw the courtyard on the body outline, so pads
stick out of it (hwde-led-test LEARNINGS, P3 library; still true here for
R0603/C0603 and others). For every footprint in lib/aiee.pretty whose pads
pass the F.CrtYd box, the F.CrtYd lines are replaced by one rectangle: the
union of the old courtyard and the pad box grown by 0.25 mm. Idempotent.

  python3 kicad/gen/fix_courtyards.py      (from the board workspace)
"""
import json
import math
import re
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "lib" / "aiee.pretty"
M = 0.25
PAD = re.compile(r"\(pad \S+ \S+ \S+ \(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\) \(size ([-\d.]+) ([-\d.]+)\)")
CY = re.compile(r"^\s*\(fp_line \(start ([-\d.]+) ([-\d.]+)\) \(end ([-\d.]+) ([-\d.]+)\) \(layer F\.CrtYd\)[^\n]*\n", re.M)


def main():
    changed = []
    for f in sorted(LIB.glob("*.kicad_mod")):
        t = f.read_text()
        xs, ys = [], []
        for m in PAD.finditer(t):
            x, y = float(m[1]), float(m[2])
            a = math.radians(float(m[3] or 0))
            w, h = float(m[4]), float(m[5])
            hw = abs(w / 2 * math.cos(a)) + abs(h / 2 * math.sin(a))
            hh = abs(w / 2 * math.sin(a)) + abs(h / 2 * math.cos(a))
            xs += [x - hw - M, x + hw + M]
            ys += [y - hh - M, y + hh + M]
        cy = CY.findall(t)
        if not xs or not cy:
            continue
        cxs = [float(v) for c in cy for v in (c[0], c[2])]
        cys = [float(v) for c in cy for v in (c[1], c[3])]
        if min(xs) >= min(cxs) - 1e-6 and max(xs) <= max(cxs) + 1e-6 and \
                min(ys) >= min(cys) - 1e-6 and max(ys) <= max(cys) + 1e-6:
            continue
        x0, x1 = round(min(xs + cxs), 2), round(max(xs + cxs), 2)
        y0, y1 = round(min(ys + cys), 2), round(max(ys + cys), 2)
        t = CY.sub("", t)
        rect = "".join(
            f"\t(fp_line (start {a} {b}) (end {c} {d}) (layer F.CrtYd) (width 0.05))\n"
            for a, b, c, d in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)))
        i = t.rindex(")")
        t = t[:i] + rect + t[i:]
        f.write_text(t)
        changed.append({"footprint": f.stem, "courtyard": [x0, y0, x1, y1]})
    print(json.dumps({"script": "fix_courtyards", "status": "pass", "changed": changed}, indent=1))


if __name__ == "__main__":
    main()
