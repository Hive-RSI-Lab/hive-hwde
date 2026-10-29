"""Mutant: usb-faces-inward (usbbuck4).

PLACEMENT fault. The micro-USB receptacle J1 sits on the left board edge
with its mouth facing out (-x). It is turned 180 degrees and slid 2.8 mm
into the board, so its contact row lands back on the same five pad sites
and the mouth now faces into the board (+x): no cable can reach it. As in
cpl-rotation, the pad nets are swapped to follow the copper (pads 1/5 and
2/4 trade places, pad 3 stays put), so every contact track still ends on
a pad of its own net; the GND shield tabs at the front move with the body. Must be caught by check_mating (kind
"mating_faces_inward", ref J1).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    text = mutlib.edit_footprint(text, "J1", "(at 103.4 131.5 -90)",
                                 "(at 106.2 131.5 90)", "turn J1")
    s, e = mutlib.footprint_block(text, "J1")
    block, n = re.subn(r"(\(at -?[\d.]+ -?[\d.]+) 270\)", r"\1 90)",
                       text[s:e])
    if n == 0:
        raise mutlib.SurgeryError("turn J1 pads: no 270-degree item found")
    text = text[:s] + block + text[e:]
    # the contact row now runs the other way: swap nets 1<->5 and 2<->4
    nets = {"1": "GND", "2": "unconnected-(J1-ID-Pad4)", "3": "/USB_DP",
            "4": "/USB_DM", "5": "VBUS"}
    for num, net in nets.items():
        s, e = mutlib.footprint_block(text, "J1")
        block = text[s:e]
        head = f'(pad "{num}" smd roundrect'
        i = block.find(head)
        if i < 0 or block.find(head, i + 1) >= 0:
            raise mutlib.SurgeryError(f"J1 pad {num}: not unique")
        j = block.find('(net "', i)
        k = block.find('")', j)   # a net name may hold ")" itself
        block = block[:j] + f'(net "{net}' + block[k:]
        text = text[:s] + block + text[e:]
    # decoupling.json names the VBUS input by J1's pin: that is pin 5 now
    dec = mutlib.golden_json("usbbuck4", "decoupling.json")
    hits = [r for r in dec["associations"] if r.get("ic") == "J1"]
    if [(r["pin"], r["rail"]) for r in hits] != [("1", "VBUS")]:
        raise mutlib.SurgeryError(f"decoupling.json J1 rows changed: {hits}")
    hits[0]["pin"] = "5"
    return text, {"ref": "J1", "rotation_delta_deg": 180,
                  "new_at": [106.2, 131.5], "mouth": "+x (was -x)",
                  "pad_nets": nets,
                  "sidecars": {"decoupling.json": mutlib.dump_json(dec)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("usb-faces-inward", "usbbuck4", surgery))
