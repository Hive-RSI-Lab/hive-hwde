"""Mutant: via-stub-long (usbbuck4).

SI fault: /MCO, declared with a 25 ps edge (20 GHz knee), is moved from B.Cu to
In1.Cu at the through via (141, 123), leaving the 1.3+ mm of barrel below In1 as
an open stub; a new via at (146, 138.6) takes it back down to the B.Cu run.
KiCad's DRC doesn't look at stubs. Must be caught by check_via_stubs (kind
"via_stub_resonance" at the via).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

MOVED = ["(start 141 123)\n\t\t(end 140.4 123)",
         "(start 140.4 123)\n\t\t(end 140.4 138.6)",
         "(start 140.4 138.6)\n\t\t(end 146 138.6)"]
NEW_VIA = ('\t(via\n\t\t(at 146 138.6)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
           '\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "/MCO")\n'
           '\t\t(uuid "78000000-0000-4000-8000-000000000001")\n\t)\n')


def surgery(text):
    for head in MOVED:
        i = text.index(head)
        j = text.index('(layer "B.Cu")', i)
        if j - i > 120:
            raise ValueError(f"segment layer not found near {head!r}")
        text = text[:j] + '(layer "In1.Cu")' + text[j + len('(layer "B.Cu")'):]
    at = text.index("\t(via\n")
    text = text[:at] + NEW_VIA + text[at:]
    cons = json.loads((mutlib.GOLDEN / "usbbuck4" / "constraints.json")
                      .read_text(encoding="utf-8"))
    for e in cons["high_speed"]:
        if e["net"] == "/MCO":
            e["t_rise_ns"] = 0.025
    return text, {"via": [141, 123],
                  "sidecars": {"constraints.json": json.dumps(cons, indent=1)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("via-stub-long", "usbbuck4", surgery))
