"""core: rip every track/via whose end sits on a pad of the given parts
(before board_update swaps their footprints; the fix loop reroutes).
  KiCad's python: kicad/gen/rip_pads.py PCB REF[.PAD] [REF[.PAD] ...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
want = [a.split(".") for a in sys.argv[2:]]
pads = [p for fp in b.GetFootprints() for p in fp.Pads()
        if any(fp.GetReference() == w[0] and (len(w) == 1 or p.GetNumber() == w[1]) for w in want)]
tracks = list(b.GetTracks())
gone = []
for t in tracks:
    pts = [t.GetPosition()] if t.GetClass() == "PCB_VIA" else [t.GetStart(), t.GetEnd()]
    for p in pads:
        if p.GetNetCode() and t.GetNetCode() == p.GetNetCode() and any(p.HitTest(q) for q in pts):
            gone.append(t)
            break
for t in gone:
    b.Remove(t)
b.Save(sys.argv[1])
print("removed", len(gone))
