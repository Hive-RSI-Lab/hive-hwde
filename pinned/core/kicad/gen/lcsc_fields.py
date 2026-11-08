"""core: set every footprint's LCSC fields from parts/parts.json (review-5 R5-DAL-8) (KiCad python).

The EasyEDA footprints arrive with an "LCSC Part" field naming whatever part
the footprint was pulled for, and board_update keeps it, so on ~170 parts it
disagreed with the BOM. parts.json (Plus; Ultra uses the same numbers) is the
source of truth: both "LCSC Part" and "LCSC" are rewritten from it, and a
footprint with no parts.json line gets them blanked (the mounting holes).

    python3.11 kicad/gen/lcsc_fields.py kicad/core.kicad_pcb parts/parts.json
"""
import json
import sys

import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
lcsc = {r: p["lcsc"] for p in json.load(open(sys.argv[2]))["parts"] for r in p["refdes"]}
changed = 0
for fp in b.GetFootprints():
    want = lcsc.get(fp.GetReference())
    for name in ("LCSC Part", "LCSC"):
        if not fp.HasField(name):
            continue
        if want is None:
            want_text = ""          # no parts.json line (mounting holes): no part number
        else:
            want_text = want
        if fp.GetFieldText(name) != want_text:
            fp.SetField(name, want_text)
            changed += 1
b.Save(sys.argv[1])
print(changed, "fields changed")
