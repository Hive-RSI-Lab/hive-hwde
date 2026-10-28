"""courtyard-pads: hwde-led-test's 0603 parts with the courtyards EasyEDA gave
them (the body outline, +-0.80 x +-0.40 mm, inside the pads): does any gate
the board passed catch it?

The by-hand fix widened them to +-1.40 x +-0.70. The cause is fixed when a
gate fails on the narrow courtyards; a fix in lib_pull itself (courtyards
that cover the pads when pulled) moves this probe to the pulled footprint.
"""
import re

import _probe

B = "hwde-led-test"
ws = _probe.board_copy(B)
pcb = ws / "kicad" / f"{B}.kicad_pcb"
text = pcb.read_text()
line = re.compile(r'(\(fp_line\s*\(start )([-\d.]+) ([-\d.]+)(\)\s*\(end )([-\d.]+) ([-\d.]+)'
                  r'(\)(?:(?!\(fp_line).)*?\(layer "F\.CrtYd"\))', re.S)


def narrow(v: str) -> str:
    f = float(v)
    return {1.4: "0.8", -1.4: "-0.8", 0.7: "0.4", -0.7: "-0.4"}.get(f, v)


n = 0


def sub(m):
    global n
    vals = [m.group(i) for i in (2, 3, 5, 6)]
    if {abs(float(v)) for v in vals} != {1.4, 0.7}:
        return m.group(0)
    n += 1
    a, b, c, d = (narrow(v) for v in vals)
    return f"{m.group(1)}{a} {b}{m.group(4)}{c} {d}{m.group(7)}"


pcb.write_text(line.sub(sub, text))
if n == 0:
    raise SystemExit("found no widened 0603 courtyard lines to narrow")
caught = [g for g in ("drc_routed", "verify") if not _probe.gate(g, ws, B)]
if caught:
    _probe.fixed(f"{', '.join(caught)} fails on {n // 4} narrowed courtyards")
_probe.stands(f"drc_routed and verify pass with {n // 4} 0603 courtyards inside their pads")
