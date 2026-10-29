"""courtyard-pads: hwde-led-test's 0603 parts with the courtyards EasyEDA gave
them (the body outline, +-0.80 x +-0.40 mm, inside the pads): does a gate the
board passes catch it, and does a pull repair it?

The cause was two-sided: no gate compared a courtyard with its own pads, and
lib_pull kept EasyEDA's. It is fixed when the verify gate fails on the narrow
courtyards (check_courtyard) AND fpfix, which lib_pull runs on every pull,
rebuilds a narrow pulled footprint into one whose courtyard covers its pads.
"""
import re

import _probe

import fpfix

B = "hwde-led-test"
ws = _probe.board_copy(B)
# any F.CrtYd fp_line, KiCad 10 (board) or legacy (library) form
line = re.compile(r'(\(fp_line\s*\(start )([-\d.]+) ([-\d.]+)(\)\s*\(end )([-\d.]+) ([-\d.]+)'
                  r'(\)(?:(?!\(fp_line).)*?\(layer "?F\.CrtYd"?\))', re.S)


def body(v: str, half: float) -> str:
    return f"{half if float(v) > 0 else -half:.2f}"


def narrow(text: str) -> tuple[str, int]:
    n = 0

    def sub(m):
        nonlocal n
        n += 1
        x1, y1, x2, y2 = (m.group(i) for i in (2, 3, 5, 6))
        return (f"{m.group(1)}{body(x1, .8)} {body(y1, .4)}{m.group(4)}"
                f"{body(x2, .8)} {body(y2, .4)}{m.group(7)}")
    return line.sub(sub, text), n


def is_0603(block: str) -> bool:
    return "0603" in block.split("\n", 1)[0]


# the board: narrow the courtyard of every placed 0603
pcb = ws / "kicad" / f"{B}.kicad_pcb"
text = pcb.read_text()
out, n, parts = [], 0, 0
for head, s, e in fpfix.top_level_nodes(text):
    if head == "footprint" and is_0603(text[s:e]):
        new, k = narrow(text[s:e])
        out.append((s, e, new))
        n, parts = n + k, parts + 1
for s, e, new in sorted(out, key=lambda t: -t[0]):
    text = text[:s] + new + text[e:]
pcb.write_text(text)
if n == 0:
    raise SystemExit("found no 0603 courtyard lines to narrow")
caught = [g for g in ("drc_routed", "verify") if not _probe.gate(g, ws, B)]

# the library: a narrow pull, through fpfix's sanitiser
repaired = []
for f in sorted((ws / "lib" / "aiee.pretty").glob("*0603*.kicad_mod")):
    raw, k = narrow(f.read_text())
    if not k:
        continue
    new, acts = fpfix.sanitize(raw)
    if any(a["rule"] == "E" for a in acts):
        repaired.append(f.stem)

if caught and repaired:
    _probe.fixed(f"{', '.join(caught)} fails on {parts} narrowed courtyards, "
                 f"and fpfix rebuilds {', '.join(repaired)}")
_probe.stands(f"narrow 0603 courtyards: {'caught by ' + ', '.join(caught) if caught else 'no gate fails'}; "
              f"{'repaired on pull: ' + ', '.join(repaired) if repaired else 'not repaired on pull'}")
