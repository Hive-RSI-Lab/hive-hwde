"""core: clear the silk findings silk_place couldn't (KiCad python).
For each silk item a silk_overlap / silk_over_copper / silk_edge_clearance
finding names, matched by position (this KiCad's SWIG proxies of footprint
graphics have no usable UUID): a reference text gets hidden (the assembly
drawing still has it), and a footprint silk graphic gets deleted (EasyEDA
outlines that run over pads, the board edge or a neighbour's outline).
  ~/.cache/hwde/bin/python3 kicad/gen/silk_clean.py reports/drc_routed.json"""
import json, sys
from pathlib import Path
import pcbnew
pcb = str(Path(__file__).resolve().parents[2] / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
rep = json.loads(Path(sys.argv[1]).read_text())
want = []   # (ref, pos)
for v in rep["failing"]:
    if v["check"] in ("silk_overlap", "silk_over_copper", "silk_edge_clearance"):
        for it in v["items"]:
            m = it["msg"]
            if m.startswith("Reference field of "):
                want.append((m.split(" of ")[1].split(" ")[0], None, "Reference"))
            elif "Silk" in m and " of " in m:
                ref = m.split(" of ")[1].split(" ")[0]
                want.append((ref, pcbnew.VECTOR2I(int(it["pos"][0] * 1e6), int(it["pos"][1] * 1e6)), m))
SILK = {b.GetLayerID("F.Silkscreen"), b.GetLayerID("B.Silkscreen")}
hidden = deleted = 0
for ref, pos, msg in want:
    fp = b.FindFootprintByReference(ref)
    if fp is None:
        continue
    if msg == "Reference" or "Reference" in msg.split(" of ")[0]:
        fp.Reference().SetVisible(False); hidden += 1
        continue
    best = None
    for g in fp.GraphicalItems():
        if g.GetLayer() not in SILK:
            continue
        bb = g.GetBoundingBox(); bb.Inflate(50000)
        if bb.Contains(pos):
            best = g
            break
    if best is not None:
        fp.Remove(best); deleted += 1
    elif fp.Reference().GetBoundingBox().Contains(pos):
        fp.Reference().SetVisible(False); hidden += 1
b.Save(pcb)
print(json.dumps({"hidden": hidden, "deleted": deleted, "items": len(want)}))
