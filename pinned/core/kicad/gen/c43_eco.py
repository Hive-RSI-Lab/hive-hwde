"""C43 to U9.19 (LAN8720A VDD1A): hwde's decoupler_copper walk was 20.3 mm, out
through J2.4 and back round the +3V3E via, for a cap 3.8 mm from the pin.
ETH_TXN lies between C43's stub and the track into pin 19, so cross under it on
B.Cu: two vias and 1.8 mm of track. The diagonal into pin 19 is split at the
join, since hwde's copperpath joins tracks only at their ends.
KiCad's python (kicad-10.0.6/bin/python3.11), from boards/core: kicad/gen/c43_eco.py"""
import pcbnew

PCB = "kicad/core.kicad_pcb"
NET = "+3V3E"
b = pcbnew.LoadBoard(PCB)
ni = b.FindNet(NET)
mm = pcbnew.FromMM


def xy(p):
    return pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))


def track(layer, a, c):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(xy(a))
    t.SetEnd(xy(c))
    t.SetWidth(mm(0.2))
    t.SetLayer(b.GetLayerID(layer))
    t.SetNet(ni)
    b.Add(t)


STUB, DIAG_A, DIAG_B = (59.8206, 201.7193), (59.7817, 200.1109), (61.1178, 201.447)
VIA_CAP, VIA_PIN, JOIN = (59.8206, 201.45), (60.55, 200.0), (60.1104, 200.4396)

diag = [t for t in b.GetTracks()
        if t.GetNetname() == NET and t.GetClass() == "PCB_TRACK"
        and {tuple(t.GetStart()), tuple(t.GetEnd())} == {tuple(xy(DIAG_A)), tuple(xy(DIAG_B))}]
assert len(diag) == 1, diag
diag[0].SetStart(xy(DIAG_A))
diag[0].SetEnd(xy(JOIN))
track("F.Cu", JOIN, DIAG_B)

for at in (VIA_CAP, VIA_PIN):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(xy(at))
    v.SetWidth(mm(0.6))
    v.SetDrill(mm(0.3))
    v.SetNet(ni)
    b.Add(v)
track("F.Cu", STUB, VIA_CAP)
track("B.Cu", VIA_CAP, (59.8206, 200.7294))
track("B.Cu", (59.8206, 200.7294), VIA_PIN)
track("F.Cu", VIA_PIN, JOIN)

pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(PCB)
