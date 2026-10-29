"""power-neckdown: can a high-current net neck down into a fine-pitch pin?

rules_gen gives each power net one track width, everywhere: a 5 A rail gets
a 2.38 mm minimum on every track, including the stub into a 0.22 mm DFN pin
or a 0.34 mm HTSSOP pin, which no track can meet. The hi-fi boards in
hifi-receiver-2 (lite-main, core) got through drc_routed by hand: their
constraints.json declares the plane-fed rails and the amp outputs at the
current of the taps that remain (VBUS 0.4 A, amp OUT 1.0 A rms), not of the
rail. Fixed when the .kicad_dru lets a 5 A net's track narrow somewhere
(a neck-down rule at pads), not one width for every track.
"""
import re

import _probe

_, dru = _probe.rules_gen([{"net": "PVDD", "current_a": 5.0, "dt_c": 20}])
mins = []
for block in re.split(r"(?=\(rule )", dru):
    if "track_width" in block and "PVDD" in block:
        m = re.search(r"track_width \(min ([\d.]+)mm\)", block)
        if m:
            mins.append(float(m.group(1)))
if not mins:
    raise SystemExit("rules_gen wrote no track_width rule for PVDD")
if min(mins) >= 1.0:
    _probe.stands(f"PVDD at 5 A: every track at least {min(mins)} mm")
_probe.fixed(f"PVDD at 5 A may neck down to {min(mins)} mm")
