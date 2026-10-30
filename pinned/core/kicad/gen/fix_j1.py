"""core: USB-C J1 fixes on the routed board (KiCad python).
- Moves J1 0.5 mm in from the left edge: its shell legs sat inside the
  0.3 mm copper-to-edge floor.
- Shortens the four GND/VBUS pads by 0.1 mm on the peg side: the pulled
  footprint's NPTH pegs sit 0.18 mm from them (hole clearance floor 0.25).
- Rips the copper that touched J1's pads, for miniroute to re-route.
"""
from pathlib import Path
import pcbnew
pcb = str(Path(__file__).resolve().parents[2] / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
j1 = b.FindFootprintByReference("J1")
pegs = [p for p in j1.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]
for p in j1.Pads():
    if p.GetNumber() in ("A1-B12", "B1-A12", "A4-B9", "B4-A9"):
        # J1 sits rotated with its pad rows along the board's x axis: the
        # pad's long side (size.y) runs along x, and the pegs are on the
        # -x side
        sz = p.GetSize()
        p.SetSize(pcbnew.VECTOR2I(sz.x, sz.y - 100000))
        pos = p.GetPosition()
        p.SetPosition(pcbnew.VECTOR2I(pos.x + 50000, pos.y))
bb = j1.GetBoundingBox(False, False)
bb.Inflate(1000000)
nets = {p.GetNetname() for p in j1.Pads() if p.GetNetname()}
gone = [t for t in b.GetTracks() if t.GetNetname() in nets and bb.Intersects(t.GetBoundingBox())]
for t in gone:
    b.Remove(t)
j1.Move(pcbnew.VECTOR2I(500000, 0))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print("ripped", len(gone), "nets", sorted(nets))
