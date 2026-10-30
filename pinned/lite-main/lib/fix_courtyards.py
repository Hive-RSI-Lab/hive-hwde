"""Grow each pulled footprint's courtyard to cover its pads (+0.25 mm).

EasyEDA courtyards often stop short of the pads (HTSSOP-32, USB-C, 0603):
hwde's place gate and DRC then miss real overlaps. Board-local stopgap for
hive-hwde brief 261028-141932-4f01e64893; idempotent.

    python3 lib/fix_courtyards.py lib/aiee.pretty
"""
import json
import re
import sys
from pathlib import Path

M = 0.25
out = []
for f in sorted(Path(sys.argv[1]).glob("*.kicad_mod")):
    s = f.read_text()
    pads = re.findall(r'\(pad \S+ \S+ \S+ \(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\) \(size ([\d.]+) ([\d.]+)\)', s)
    if not pads:
        continue
    px = []
    py = []
    for x, y, rot, w, h in pads:
        x, y, w, h = float(x), float(y), float(w), float(h)
        if rot and abs(float(rot)) % 180 == 90:
            w, h = h, w
        px += [x - w / 2, x + w / 2]
        py += [y - h / 2, y + h / 2]
    cy = re.findall(r'\(fp_line \(start ([-\d.]+) ([-\d.]+)\) \(end ([-\d.]+) ([-\d.]+)\) \(layer F\.CrtYd\)', s)
    cx = [float(v) for c in cy for v in (c[0], c[2])]
    cyy = [float(v) for c in cy for v in (c[1], c[3])]
    if cx and min(cx) <= min(px) - M + 1e-6 and max(cx) >= max(px) + M - 1e-6 \
            and min(cyy) <= min(py) - M + 1e-6 and max(cyy) >= max(py) + M - 1e-6:
        continue
    x1 = min(cx + [min(px) - M]); x2 = max(cx + [max(px) + M])
    y1 = min(cyy + [min(py) - M]); y2 = max(cyy + [max(py) + M])
    s = re.sub(r'\t\(fp_line [^\n]*F\.CrtYd[^\n]*\n', '', s)
    s = re.sub(r'\t\(fp_(?:circle|arc|poly) [^\n]*F\.CrtYd[^\n]*\n', '', s)
    rect = [((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))]
    add = "".join(f"\t(fp_line (start {a[0]:.2f} {a[1]:.2f}) (end {b[0]:.2f} {b[1]:.2f}) (layer F.CrtYd) (width 0.05))\n" for a, b in rect)
    i = s.index("\t(model") if "\t(model" in s else s.rindex(")")
    s = s[:i] + add + s[i:]
    f.write_text(s)
    out.append({"footprint": f.stem, "courtyard": [round(v, 2) for v in (x1, y1, x2, y2)],
                "was": cx and [min(cx), min(cyy), max(cx), max(cyy)]})
print(json.dumps({"fixed": out}, indent=1))
